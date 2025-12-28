import logging

from celery import Celery
from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "aibot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True

celery_app.conf.beat_schedule = {
    "parse-news-interval": {
        "task": "app.tasks.parse_news",
        "schedule": settings.PARSE_INTERVAL_MINUTES*60,
    },
}


@celery_app.task(name="app.tasks.parse_news")
def parse_news():
    logger.info("Parsing news…")