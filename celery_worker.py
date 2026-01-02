import platform
from datetime import timedelta

from celery import Celery
from app.config import settings

celery_app = Celery(
    "aibot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

conf: dict = {
    # сериализация
    "task_serializer": "json",
    "accept_content": ["json"],
    "result_serializer": "json",

    # очереди
    "task_default_queue": "aibot",
    "task_routes": {
        "app.tasks.parse_news": {"queue": "aibot"},
        "app.tasks.generate_chain_post": {"queue": "aibot"},
        "app.tasks.publish_latest_post": {"queue": "aibot"},
        "app.tasks.run_pipeline": {"queue": "aibot"},
    },

    # надёжность
    "task_acks_late": True,
    "task_time_limit": 30,
    "task_soft_time_limit": 25,

    # таймзона
    "timezone": "Europe/Madrid",
    "enable_utc": True,

    # Celery Beat — ИСТОЧНИК ИСТИНЫ: PARSE_INTERVAL_MINUTES
    "beat_schedule": {
        "run_pipeline": {
            "task": "app.tasks.run_pipeline",
            "schedule": timedelta(
                minutes=int(settings.PARSE_INTERVAL_MINUTES)
            ),
        }
    },
}

# Для Windows Celery стабильнее в single-process режиме
if platform.system() == "Windows":
    conf["worker_pool"] = "solo"
    conf["worker_concurrency"] = 1

celery_app.conf.update(**conf)