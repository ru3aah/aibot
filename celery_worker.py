from celery import Celery

from app.config import settings, Settings

celery_app = Celery(
    'aibot',
    broker=Settings.CELERY_BROKER_URL,
    backend=Settings.CELERY_RESULT_BACKEND,
    include=['app.tasks']
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Madrid',
    enable_utc=True,
    beat_schedule={
        'parse_news': {
            'task': 'app.tasks.parse_news',
            'schedule': settings.NEWS_PARSE_INTERVAL * 5,
        }
    }
)


if __name__ == '__main__':
    celery_app.start()
