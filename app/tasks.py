import json
import logging
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import NewsItem, Post, PostStatus, Source
from app.telegram.publisher import TelegramPublisher

# ВАЖНО: utils.py лежит в app/utils.py (а НЕ в app/news_parser/utils.py)
from app.utils import parse_site_source, parse_telegram_source

logger = logging.getLogger(__name__)


# ----------------------------
# Time helpers
# ----------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------
# FilterSettings (raw SQL, tolerant to schema drift)
# ----------------------------
def _get_filter_settings_row(session: Session) -> Optional[Dict[str, Any]]:
    """
    Read latest filter_settings row via raw SQL to avoid ORM mismatch.
    Expected columns: id, language, updated_at, active_keywords_json
    """
    row = session.execute(
        text(
            """
            SELECT id, language, updated_at, active_keywords_json
            FROM filter_settings
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    return dict(row) if row else None


def _load_active_keywords(session: Session) -> List[str]:
    fs = _get_filter_settings_row(session)
    if not fs:
        return []

    raw = fs.get("active_keywords_json") or ""
    try:
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            return []
        out: List[str] = []
        seen = set()
        for x in data:
            if not isinstance(x, str):
                continue
            s = x.strip().lower()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    except Exception:
        return []


def _load_selected_language(session: Session, default: str = "ru") -> str:
    fs = _get_filter_settings_row(session)
    if not fs:
        return default
    lang = (fs.get("language") or default).strip().lower()
    return lang or default


def _filter_signature(language: str, keywords_lc: List[str]) -> str:
    """
    Stable signature for current settings to bind generated posts to active filters.
    Stored in Post.input_key prefix: F:<sig>:...
    """
    lang = (language or "ru").strip().lower()
    kws = [k.strip().lower() for k in (keywords_lc or []) if k and k.strip()]
    kws_sorted = sorted(set(kws))
    payload = f"lang={lang}|kw={'|'.join(kws_sorted)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# ----------------------------
# News filtering
# ----------------------------
def _matches_keywords(item: NewsItem, keywords_lc: List[str]) -> bool:
    if not keywords_lc:
        return True

    parts = [
        (item.title or ""),
        (item.summary or ""),
        (item.raw_text or ""),
        (item.text100 or ""),
        (item.url or ""),
    ]
    hay = " ".join(parts).lower()

    for kw in keywords_lc:
        if kw in hay:
            return True
    return False


def _dedupe_news(items: List[NewsItem]) -> List[NewsItem]:
    seen_url = set()
    seen_title = set()
    out: List[NewsItem] = []
    for n in items:
        url = (n.url or "").strip().lower()
        title = (n.title or "").strip().lower()

        if url and url in seen_url:
            continue
        if title and title in seen_title:
            continue

        if url:
            seen_url.add(url)
        if title:
            seen_title.add(title)

        out.append(n)
    return out


def _filter_news_strict(session: Session, raw_news: List[NewsItem]) -> List[NewsItem]:
    """
    STRICT MODE:
    - if keywords are active and there are 0 matches -> return []
    """
    keywords_lc = _load_active_keywords(session)

    if not keywords_lc:
        return raw_news

    filtered = [n for n in raw_news if _matches_keywords(n, keywords_lc)]
    logger.info(
        "active_keywords: применили фильтр (%d слов) -> %d/%d новостей прошло",
        len(keywords_lc),
        len(filtered),
        len(raw_news),
    )

    if not filtered:
        logger.warning("active_keywords: совпадений 0 -> строгий режим, генерацию пропускаем")
        return []

    return filtered


# ----------------------------
# Redis lock helpers
# ----------------------------
_LOCK_KEY = "aibot:publish_lock"


def _get_redis_url() -> Optional[str]:
    for attr in ("REDIS_URL", "CELERY_BROKER_URL", "BROKER_URL"):
        url = getattr(settings, attr, None)
        if url:
            return str(url)
    return None


def _acquire_publish_lock(ttl_seconds: int = 60) -> Optional[str]:
    """
    Returns lock token if acquired, else None.
    Uses SET NX EX.
    """
    redis_url = _get_redis_url()
    if not redis_url:
        logger.warning("publish lock: REDIS url not found in settings, lock disabled")
        return "NOLOCK"

    try:
        import redis  # type: ignore

        r = redis.Redis.from_url(redis_url)
        token = str(uuid.uuid4())
        ok = r.set(_LOCK_KEY, token, nx=True, ex=ttl_seconds)
        if ok:
            return token
        return None
    except Exception as e:
        logger.warning("publish lock: failed to use redis (%s), lock disabled", e)
        return "NOLOCK"


def _release_publish_lock(token: Optional[str]) -> None:
    if not token or token == "NOLOCK":
        return

    redis_url = _get_redis_url()
    if not redis_url:
        return

    try:
        import redis  # type: ignore

        r = redis.Redis.from_url(redis_url)

        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        r.eval(script, 1, _LOCK_KEY, token)
    except Exception:
        pass


# ----------------------------
# Celery task: parse_news
# ----------------------------
@celery_app.task(name="app.tasks.parse_news")
def parse_news() -> Dict[str, Any]:
    """
    Парсим ВСЕ enabled sources.
    SITE -> parse_site_source(session, source)
    TG   -> parse_telegram_source(session, source)

    parse_* добавляет NewsItem в session (commit делаем здесь).
    """
    results: List[Dict[str, Any]] = []

    with get_db_sync() as session:
        sources: List[Source] = session.query(Source).filter(Source.enabled.is_(True)).all()

        total_added = 0
        for s in sources:
            try:
                if (s.type or "").lower() == "site":
                    added = parse_site_source(session, s)
                elif (s.type or "").lower() in ("tg", "telegram"):
                    added = parse_telegram_source(session, s)
                else:
                    logger.warning("parse_news: unknown source type=%s name=%s url=%s", s.type, s.name, s.url)
                    added = 0

                session.commit()
                total_added += int(added or 0)

                results.append(
                    {
                        "source_id": s.id,
                        "name": s.name,
                        "type": s.type,
                        "added": int(added or 0),
                    }
                )
            except Exception as e:
                session.rollback()
                logger.exception("parse_news: failed source=%s (%s)", s.name, e)
                results.append(
                    {
                        "source_id": s.id,
                        "name": s.name,
                        "type": s.type,
                        "added": 0,
                        "error": str(e),
                    }
                )

    return {"status": "ok", "parsed": sum(x.get("added", 0) for x in results), "sources": results}


# ----------------------------
# Celery task: generate_chain_post
# ----------------------------
@celery_app.task(name="app.tasks.generate_chain_post")
def generate_chain_post_task() -> Dict[str, Any]:
    from app.ai.generator import generate_chain_post  # returns tuple (text, status, error, input_news_ids_json, input_key)

    with get_db_sync() as session:
        window = max(int(getattr(settings, "PARSE_THREADS", 10)) * 3, 30)

        raw_news: List[NewsItem] = (
            session.query(NewsItem)
            .order_by(NewsItem.created_at.desc())
            .limit(window)
            .all()
        )

        raw_news = _dedupe_news(raw_news)

        lang = _load_selected_language(session, default="ru")
        keywords_lc = _load_active_keywords(session)

        filtered = _filter_news_strict(session, raw_news)

        if not filtered:
            sig = _filter_signature(lang, keywords_lc)
            kw_display = ", ".join(keywords_lc) if keywords_lc else "—"
            msg = f"⚠️ По выбранным ключевым словам новостей нет: {kw_display}"

            logger.warning(
                "no_news_for_active_keywords: lang=%s keywords=%s sig=%s",
                lang,
                keywords_lc,
                sig,
            )

            return {
                "status": "skipped",
                "reason": "no_news_for_active_keywords",
                "message": msg,
                "generated": 0,
                "post_status": None,
                "has_text": False,
                "post_id": None,
                "language": lang,
                "keywords": keywords_lc,
                "filter_sig": sig,
            }

        limit_n = max(int(getattr(settings, "PARSE_THREADS", 10)), 1)
        selected_news = filtered[:limit_n]

        sig = _filter_signature(lang, keywords_lc)

        # !!! FIX: generator возвращает tuple, не строку
        generated_text, gen_status, gen_error, input_news_ids_json, legacy_input_key = generate_chain_post(
            selected_news,
            language=lang,
        )

        # всегда приводим к str, чтобы SQLite не получил tuple/None
        generated_text = (generated_text or "")
        if not isinstance(generated_text, str):
            generated_text = str(generated_text)

        has_text = bool(generated_text.strip())

        # привязка к текущим фильтрам
        legacy_key = "-".join([n.id[:6] for n in selected_news])
        input_key = f"F:{sig}:{legacy_key}"

        post = Post(
            id=str(uuid.uuid4()),
            news_id=None,
            generated_text=generated_text if has_text else "",
            status=PostStatus.GENERATED,
            published_at=None,
            created_at=_utcnow(),
            telegram_message_id=None,
            error=(gen_error or None),
            input_news_ids=input_news_ids_json or json.dumps([n.id for n in selected_news], ensure_ascii=False),
            input_key=input_key,
        )

        session.add(post)
        session.commit()

        return {
            "status": "success",
            "generated": 1,
            "post_status": str(post.status),
            "has_text": has_text,
            "post_id": post.id,
            "language": lang,
            "keywords": keywords_lc,
            "filter_sig": sig,
            "news_count": len(selected_news),
            "gen_status": gen_status,
        }


# ----------------------------
# Celery task: publish_latest_post
# ----------------------------
@celery_app.task(name="app.tasks.publish_latest_post")
def publish_latest_post() -> Dict[str, Any]:
    """
    Публикует максимум 1 пост за запуск.
    - clears stuck CLAIM by TTL (CLAIM_TTL_MINUTES)
    - Redis-lock prevents parallel publish
    - публикует только посты под текущую сигнатуру фильтра F:<sig>:
    """
    batch_limit = int(getattr(settings, "PUBLISH_BATCH_LIMIT", 5))
    claim_ttl_min = int(getattr(settings, "CLAIM_TTL_MINUTES", 25))

    effective_limit = 1

    lock_token: Optional[str] = None
    try:
        lock_token = _acquire_publish_lock(ttl_seconds=90)
        if lock_token is None:
            return {
                "status": "skipped",
                "reason": "locked",
                "published": 0,
                "claimed_elsewhere": 0,
                "retryable": 0,
                "failed_empty": 0,
                "batch_limit": batch_limit,
                "effective_limit": effective_limit,
            }

        with get_db_sync() as session:
            lang = _load_selected_language(session, default="ru")
            keywords_lc = _load_active_keywords(session)
            sig = _filter_signature(lang, keywords_lc)
            prefix = f"F:{sig}:"

            # 1) clear stuck claims
            cutoff = _utcnow() - timedelta(minutes=claim_ttl_min)
            cleared = (
                session.query(Post)
                .filter(
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.like("CLAIM:%"),
                    Post.created_at < cutoff,
                )
                .update({Post.telegram_message_id: None}, synchronize_session=False)
            )
            session.commit()
            if cleared:
                logger.warning("publish: cleared stuck CLAIM=%s", cleared)

            # 2) select ONE candidate
            candidates: List[Post] = (
                session.query(Post)
                .filter(
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.is_(None),
                    Post.input_key.like(prefix + "%"),
                )
                .order_by(Post.created_at.asc())
                .limit(effective_limit)
                .all()
            )

            if not candidates:
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "language": lang,
                    "keywords": keywords_lc,
                }

            publisher = TelegramPublisher()
            post = candidates[0]

            # 3) atomic claim
            claim = f"CLAIM:{uuid.uuid4()}"
            updated = (
                session.query(Post)
                .filter(
                    Post.id == post.id,
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.is_(None),
                )
                .update({Post.telegram_message_id: claim}, synchronize_session=False)
            )
            session.commit()

            if updated != 1:
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 1,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                }

            p: Optional[Post] = session.query(Post).filter(Post.id == post.id).first()
            if not p or not (p.generated_text or "").strip():
                session.query(Post).filter(Post.id == post.id).update(
                    {Post.telegram_message_id: None},
                    synchronize_session=False,
                )
                session.commit()
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 1,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                }

            try:
                # !!! FIX: используем метод publish_text (и он будет в TelegramPublisher)
                msg_id = publisher.publish_text(p.generated_text)

                session.query(Post).filter(Post.id == post.id).update(
                    {
                        Post.status: PostStatus.PUBLISHED,
                        Post.published_at: _utcnow(),
                        Post.telegram_message_id: str(msg_id),
                        Post.error: None,
                    },
                    synchronize_session=False,
                )
                session.commit()

                return {
                    "status": "done",
                    "published": 1,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "post_id": post.id,
                }

            except Exception as e:
                logger.exception("publish: telegram error, marking RETRYABLE: %s", e)
                session.query(Post).filter(Post.id == post.id).update(
                    {
                        Post.status: PostStatus.RETRYABLE,
                        Post.error: str(e)[:2000],
                        Post.telegram_message_id: None,
                    },
                    synchronize_session=False,
                )
                session.commit()

                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 1,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "post_id": post.id,
                }

    finally:
        _release_publish_lock(lock_token)