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

        news = list(reversed(news))
        if not news:
            logger.info("Нет новостей для генерации")
            return {"status": "empty", "generated": 0}

        text, status, error, ids, key = generate_chain_post(news)

        post = Post(
            generated_text=text,
            status=status,
            created_at=now_utc(),
            error=error,
            input_news_ids=ids,
            input_key=key,
        )

        session.add(post)
        session.commit()

        # по твоему требованию НЕ публикуем автоматически
        return {"status": "success", "generated": 1, "post_status": str(status)}