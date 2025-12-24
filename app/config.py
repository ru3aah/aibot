import pydantic_settings


class Settings(pydantic_settings):
    DATABASE_URL: str = "sqlite:///./aibot.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELEGRAM_SESSION_NAME: str = "aibot_session"
    TELEGRAM_CHANNEL: str
    TELEGRAM_CHANNEL_USERNAME: str

    OPEN_AI_API_KEY: str
    OPEN_AI_MODEL: str = "gpt-3.5-turbo"
    OPEN_AI_TEMPERATURE: float = 0.7

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    PARSE_INTERVAL_MINUTES: int = 30
    PARSE_THREADS: int = 10

    DEBUG: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


