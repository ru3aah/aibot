import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Keyword, NewsItem, Post, Source, FilterSettings
from app.database.data_types import PostStatus, SourceType
from app.ai.generator import generate_chain_post
from app.utils import parse_site_source, parse_telegram_source

logger = logging.getLogger(__name__)

# Маркер "захвата" поста на публикацию (чтобы не было дублей при параллельных publish)
CLAIM_PREFIX = "CLAIM:"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def chain_len() -> int:
    return settings.PARSE_THREADS


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _dedupe_news(items: Sequence[NewsItem]) -> List[NewsItem]:
    """
    Дедупликация:
    1) по url (если есть)
    2) иначе по title
    Сохраняем порядок.
    """
    seen_url = set()
    seen_title = set()
    out: List[NewsItem] = []

    for n in items:
        url = _normalize(getattr(n, "url", "") or "")
        title = _normalize(getattr(n, "title", "") or "")

        if url:
            if url in seen_url:
                continue
            seen_url.add(url)
            out.append(n)
            continue

        if title:
            if title in seen_title:
                continue
            seen_title.add(title)
            out.append(n)
            continue

        out.append(n)

    return out


def _get_filter_settings(session) -> FilterSettings:
    """
    Возвращает единственную запись FilterSettings.
    Если её нет — создаёт (id='1').
    """
    fs = session.query(FilterSettings).order_by(FilterSettings.updated_at.desc()).first()
    if fs:
        return fs

    fs = FilterSettings(
        id="1",
        language="ru",
        active_keywords_json="[]",
        updated_at=now_utc(),
    )
    session.add(fs)
    session.commit()
    return fs


def _load_active_keywords(session) -> List[str]:
    """
    Возвращает список АКТИВНЫХ ключевых слов (lowercase) из filter_settings.active_keywords_json.
    Если список пуст — значит фильтрацию не применяем.
    """
    fs = _get_filter_settings(session)

    raw = (fs.active_keywords_json or "").strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    kws: List[str] = []
    for w in data:
        if not isinstance(w, str):
            continue
        w = w.strip()
        if len(w) >= 2:
            kws.append(w.lower())

    # На всякий случай ограничим, даже если в БД больше
    return kws[:5]


def _matches_keywords(n: NewsItem, keywords_lc: Sequence[str]) -> bool:
    """
    Проверяем вхождение любого ключевого слова в текстовые поля.
    """
    if not keywords_lc:
        return True

    hay = " ".join(
        [
            _normalize(getattr(n, "title", "") or ""),
            _normalize(getattr(n, "summary", "") or ""),
            _normalize(getattr(n, "raw_text", "") or ""),
            _normalize(getattr(n, "text100", "") or ""),
        ]
    )

    if not hay.strip():
        return False

    return any(kw in hay for kw in keywords_lc)


def _filter_news(session, items: Sequence[NewsItem]) -> List[NewsItem]:
    """
    1) дедуп
    2) фильтрация по активным keywords из filter_settings (если они есть)
    """
    deduped = _dedupe_news(items)

    keywords_lc = _load_active_keywords(session)
    if not keywords_lc:
        logger.info("active_keywords: пусто -> фильтрацию не применяем")
        return list(deduped)

    filtered = [n for n in deduped if _matches_keywords(n, keywords_lc)]
    logger.info(
        "active_keywords: применили фильтр (%d слов) -> %d/%d новостей прошло",
        len(keywords_lc),
        len(filtered),
        len(deduped),
    )
    return filtered


def _is_claim(mid: str | None) -> bool:
    return bool(mid) and str(mid).startswith(CLAIM_PREFIX)


def _claim_post_for_publish(session, post_id: str) -> str | None:
    """
    Атомарно "захватываем" пост на публикацию.
    Перед отправкой в TG пишем telegram_message_id="CLAIM:<uuid>".
    """
    claim_id = f"{CLAIM_PREFIX}{uuid.uuid4()}"
    updated = (
        session.query(Post)
        .filter(
            Post.id == post_id,
            Post.status == PostStatus.GENERATED,
            Post.telegram_message_id.is_(None),
        )
        .update(
            {Post.telegram_message_id: claim_id},
            synchronize_session=False,
        )
    )
    if updated:
        return claim_id
    return None


def _cleanup_stuck_claims(session) -> int:
    """
    Снимает "залипшие" CLAIM у GENERATED постов по TTL.
    Основание: created_at слишком старый + telegram_message_id LIKE 'CLAIM:%'
    """
    ttl = getattr(settings, "CLAIM_TTL_MINUTES", 25)
    deadline = now_utc() - timedelta(minutes=int(ttl))

    updated = (
        session.query(Post)
        .filter(
            Post.status == PostStatus.GENERATED,
            Post.telegram_message_id.like(f"{CLAIM_PREFIX}%"),
            Post.created_at < deadline,
        )
        .update(
            {Post.telegram_message_id: None},
            synchronize_session=False,
        )
    )
    return int(updated or 0)


@celery_app.task(name="app.tasks.parse_news")
def parse_news():
    with get_db_sync() as session:
        sources = session.query(Source).filter(Source.enabled.is_(True)).all()

        for source in sources:
            if source.type == SourceType.SITE:
                parse_site_source(session, source)
            elif source.type == SourceType.TG:
                parse_telegram_source(session, source)

        session.commit()

    # основная цепочка: parse -> generate -> publish
    celery_app.send_task("app.tasks.generate_chain_post", queue="aibot")


@celery_app.task(name="app.tasks.generate_chain_post")
def generate_chain_post_task():
    with get_db_sync() as session:
        # берём окно побольше, чтобы после фильтрации осталось что генерировать
        raw_limit = max(chain_len() * 3, chain_len())
        raw_news = (
            session.query(NewsItem)
            .order_by(NewsItem.created_at.desc())
            .limit(raw_limit)
            .all()
        )

        # порядок: старые -> новые
        raw_news = list(reversed(raw_news))
        if not raw_news:
            logger.info("Нет новостей для генерации")
            return {"status": "empty", "generated": 0}

        filtered = _filter_news(session, raw_news)

        # если фильтр выкинул всё — не блокируем пайплайн
        if not filtered:
            logger.warning("Фильтрация выкинула все новости -> берём без фильтра")
            filtered = raw_news

        # ограничиваем итоговую длину цепочки (старые -> новые)
        news = filtered[-chain_len():]

        text, status, error, ids_json, key = generate_chain_post(news)

        post = Post(
            generated_text=text,
            status=status if status else PostStatus.FAILED,
            created_at=now_utc(),
            error=error,
            input_news_ids=ids_json,
            input_key=key,
        )

        session.add(post)
        session.commit()

        # основная логика: сразу после генерации пытаемся публиковать
        celery_app.send_task("app.tasks.publish_latest_post", queue="aibot")

        return {
            "status": "success",
            "generated": 1,
            "post_status": str(post.status),
            "has_text": bool(post.generated_text),
            "post_id": str(post.id),
        }


@celery_app.task(name="app.tasks.publish_latest_post")
def publish_latest_post_task():
    """
    Публикует пачку постов GENERATED (FIFO), максимум settings.PUBLISH_BATCH_LIMIT за прогон.

    Защита от дублей:
    - claim в telegram_message_id="CLAIM:<uuid>"

    Дополнительно:
    - авто-снятие "залипших" CLAIM по TTL (settings.CLAIM_TTL_MINUTES)
    """
    from app.telegram.publisher import TelegramPublisher  # локальный импорт, чтобы избежать циклов

    published = 0
    skipped = 0
    retryable = 0
    failed_empty = 0
    claimed_elsewhere = 0

    batch_limit = getattr(settings, "PUBLISH_BATCH_LIMIT", 5)

    with get_db_sync() as session:
        # 0) чистим залипшие claim
        cleared = _cleanup_stuck_claims(session)
        if cleared:
            logger.warning("publish: cleared stuck CLAIM=%d", cleared)
            session.commit()

        # 1) берём только те, у кого telegram_message_id IS NULL
        posts = (
            session.query(Post)
            .filter(
                Post.status == PostStatus.GENERATED,
                Post.telegram_message_id.is_(None),
            )
            .order_by(Post.created_at.asc())  # FIFO
            .limit(batch_limit)
            .all()
        )

        if not posts:
            logger.info("publish: нет постов GENERATED для публикации")
            return {"status": "done", "published": 0, "skipped": 0, "claimed_elsewhere": 0, "retryable": 0, "failed_empty": 0, "batch_limit": batch_limit}

        publisher: TelegramPublisher | None = None

        for post in posts:
            text = (post.generated_text or "").strip()
            if not text:
                post.status = PostStatus.FAILED
                post.error = "generated_text is empty"
                failed_empty += 1
                continue

            # 2) claim (атомарно)
            claim_id = _claim_post_for_publish(session, str(post.id))
            session.commit()  # фиксируем claim сразу

            if not claim_id:
                claimed_elsewhere += 1
                continue

            # 3) отправка в TG
            try:
                if publisher is None:
                    publisher = TelegramPublisher()

                mid = publisher.send(text)

                # 4) фиксируем успех (заменяем claim на реальный message_id)
                post_db = session.get(Post, str(post.id))
                if not post_db:
                    continue

                if post_db.telegram_message_id != claim_id:
                    claimed_elsewhere += 1
                    continue

                post_db.status = PostStatus.PUBLISHED
                post_db.telegram_message_id = str(mid)
                post_db.published_at = now_utc()
                post_db.error = None

                session.commit()
                published += 1

            except Exception as e:
                logger.exception("publish failed: %s", e)

                # снимаем claim, чтобы можно было ретраить
                post_db = session.get(Post, str(post.id))
                if post_db and post_db.telegram_message_id == claim_id:
                    post_db.status = PostStatus.RETRYABLE
                    post_db.error = str(e)
                    post_db.telegram_message_id = None
                    session.commit()

                retryable += 1
                break

    return {
        "status": "done",
        "published": published,
        "skipped": skipped,
        "claimed_elsewhere": claimed_elsewhere,
        "retryable": retryable,
        "failed_empty": failed_empty,
        "batch_limit": batch_limit,
    }