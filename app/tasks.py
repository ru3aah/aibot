import logging
from datetime import datetime, timezone

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import NewsItem, Post, Source
from app.database.data_types import PostStatus, SourceType
from app.ai.generator import generate_chain_post
from app.utils import parse_site_source, parse_telegram_source

logger = logging.getLogger(__name__)


def now_utc():
    return datetime.now(timezone.utc)


def chain_len() -> int:
    return settings.PARSE_THREADS


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

    # генерация после завершения парсинга
    celery_app.send_task("app.tasks.generate_chain_post", queue="aibot")


@celery_app.task(name="app.tasks.generate_chain_post")
def generate_chain_post_task():
    with get_db_sync() as session:
        news = (
            session.query(NewsItem)
            .order_by(NewsItem.created_at.desc())
            .limit(chain_len())
            .all()
        )

        # порядок: старые -> новые
        news = list(reversed(news))
        if not news:
            logger.info("Нет новостей для генерации")
            return {"status": "empty", "generated": 0}

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

        return {
            "status": "success",
            "generated": 1,
            "post_status": str(post.status),
            "has_text": bool(post.generated_text),
        }


@celery_app.task(name="app.tasks.publish_latest_post")
def publish_latest_post_task():
    """
    Публикует 1 самый свежий пост со статусом GENERATED.
    Защита от дублей: если telegram_message_id уже есть -> не публикуем.
    """
    from app.telegram.publisher import TelegramPublisher  # локальный импорт, чтобы избежать циклов

    with get_db_sync() as session:
        post = (
            session.query(Post)
            .filter(Post.status == PostStatus.GENERATED)
            .order_by(Post.created_at.desc())
            .first()
        )

        if not post:
            logger.info("publish: нет постов GENERATED")
            return {"status": "empty"}

        if post.telegram_message_id:
            logger.info("publish: post already has telegram_message_id=%s (skip)", post.telegram_message_id)
            return {"status": "already_published", "post_id": post.id, "telegram_message_id": post.telegram_message_id}

        text = (post.generated_text or "").strip()
        if not text:
            post.status = PostStatus.FAILED
            post.error = "generated_text is empty"
            session.commit()
            return {"status": "failed", "post_id": post.id, "error": "empty generated_text"}

        try:
            publisher = TelegramPublisher()
            mid = publisher.send(text)

            post.status = PostStatus.PUBLISHED
            post.telegram_message_id = str(mid)
            post.published_at = now_utc()

            session.commit()
            return {"status": "published", "post_id": post.id, "telegram_message_id": str(mid)}

        except Exception as e:
            logger.exception("publish failed: %s", e)
            post.status = PostStatus.RETRYABLE
            post.error = str(e)
            session.commit()
            return {"status": "retryable", "post_id": post.id, "error": str(e)}