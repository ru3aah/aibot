from typing import Optional

import pydantic_settings


class Settings(pydantic_settings):
    DATABASE_URL: str = "sqlite:///db/aibot.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    TELEGRAM_API_ID: Optional[str] = None
    TELEGRAM_API_HASH: Optional[str] = None
    TELEGRAM_SESSION_NAME: str = "aibot_session"
    TELEGRAM_CHANNEL: Optional[str] = None
    TELEGRAM_CHANNEL_USERNAME: Optional[str] = None

    OPEN_AI_API_KEY: Optional[str] = None
    OPEN_AI_MODEL: str = "gpt-3.5-turbo"

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    PARSE_INTERVAL_MINUTES: int = 30
    PARSE_THREADS: int = 10

    DEBUG: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()


