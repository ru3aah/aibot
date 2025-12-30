import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.ai.generator import generate_posts
from celery_worker import celery_app
from app.database.data_types import PostStatus, SourceType
from app.database.db import get_db_sync
from app.database.models import NewsItem, Post, Source
from app.utils import parse_site_source, parse_telegram_source

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.parse_news", bind=True, max_retries=3)
def parse_news(self):
    """Парсит новости из всех активных источников и сохраняет их в БД."""
    logger.info("Начало парсинга новостей…")

    try:
        with get_db_sync() as session:  # type: Session
            sources = session.query(Source).filter(Source.enabled.is_(True)).all()

            if not sources:
                logger.warning("Не найдено активных источников для парсинга")
                return {"status": "success", "saved": 0, "sources_processed": 0}

            logger.info("Найдено активных источников: %s", len(sources))

            total_saved = 0
            for source in sources:
                try:
                    if source.type == SourceType.SITE:
                        saved = parse_site_source(session, source)
                    elif source.type == SourceType.TG:
                        saved = parse_telegram_source(session, source)
                    else:
                        logger.warning(
                            "Неизвестный тип источника %s для '%s'",
                            source.type,
                            source.name,
                        )
                        saved = 0

                    total_saved += int(saved or 0)

                except Exception as e:
                    logger.error(
                        "Ошибка при обработке источника '%s': %s",
                        source.name,
                        e,
                        exc_info=True,
                    )
                    session.rollback()
                    continue

            logger.info("Парсинг завершен. Всего сохранено новостей: %s", total_saved)

            if total_saved > 0:
                logger.info("Запускаем генерацию постов после парсинга")
                generate_posts_task.delay()

            return {
                "status": "success",
                "saved": total_saved,
                "sources_processed": len(sources),
            }

    except Exception as e:
        logger.error("Критическая ошибка при парсинге новостей: %s", e, exc_info=True)
        raise self.retry(exc=e, countdown=60)


@celery_app.task(name="app.tasks.generate_posts", bind=True, max_retries=3)
def generate_posts_task(self):
    """Генерирует посты для новостей, у которых ещё нет Post."""
    logger.info("Выполняем задачу генерации постов по новостям")

    try:
        with get_db_sync() as session:  # type: Session
            news_without_posts = (
                session.query(NewsItem)
                .outerjoin(Post, Post.news_id == NewsItem.id)
                .filter(Post.id.is_(None))
                .all()
            )

            if not news_without_posts:
                logger.info("Нет новостей без постов для генерации")
                return {"status": "success", "generated": 0}

            generated_count = 0

            for news_item in news_without_posts:
                try:
                    post_text = generate_posts(news_item)

                    session.add(
                        Post(
                            news_id=news_item.id,
                            generated_text=post_text,
                            status=PostStatus.DRAFT if post_text else PostStatus.FAILED,
                            published_at=None,
                            created_at=datetime.now(),
                        )
                    )

                    if post_text:
                        generated_count += 1
                        logger.info("Сгенерирован пост для новости %s", news_item.id)
                    else:
                        logger.warning(
                            "Не удалось сгенерировать пост для новости %s",
                            news_item.id,
                        )

                except Exception as e:
                    logger.error(
                        "Ошибка при генерации поста для новости %s: %s",
                        news_item.id,
                        e,
                        exc_info=True,
                    )
                    session.add(
                        Post(
                            news_id=news_item.id,
                            generated_text=None,
                            status=PostStatus.FAILED,
                            published_at=None,
                            created_at=datetime.now(),
                        )
                    )

            logger.info("Генерация завершена. Сгенерировано постов: %s", generated_count)
            return {"status": "success", "generated": generated_count}

    except Exception as e:
        logger.error("Критическая ошибка при генерации постов: %s", e, exc_info=True)
        raise self.retry(exc=e, countdown=60)