from __future__ import annotations

from pathlib import Path
from typing import Optional, Literal

from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

APP_DIR = Path(__file__).resolve().parent
APP_DATABASE_DIR = APP_DIR / "database"


class Settings(BaseSettings):
    DB_MODE: Literal["auto", "postgres", "sqlite"] = "auto"

    POSTGRES_URL: Optional[str] = None
    SQLITE_URL: str = "sqlite:///aibot.db"

    REDIS_URL: str = "redis://localhost:6379/0"

    TG_API_ID: Optional[str] = None
    TG_API_HASH: Optional[str] = None
    TELEGRAM_SESSION_NAME: str = "aibot_session"
    TELEGRAM_CHANNEL: Optional[str] = None
    TELEGRAM_CHANNEL_USERNAME: Optional[str] = None

    # TG Bot (optional)
    TG_BOT_TOKEN: Optional[str] = None

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    PARSE_INTERVAL_MINUTES: int = 5
    PARSE_THREADS: int = 10

    #stuck post delete interval
    CLAIM_TTL_MINUTES: int = 25

    #post publish batch max size
    PUBLISH_BATCH_LIMIT: int = 1

    DEBUG: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"

    def _sqlite_force_into_app_database(self, url_str: str) -> str:
        u = make_url(url_str)
        if not u.drivername.startswith("sqlite"):
            return url_str

        db_path = u.database or ""
        if not db_path or db_path == ":memory:":
            return url_str

        p = Path(db_path)
        if p.is_absolute():
            return str(u)

        parts = list(p.parts)
        if parts[:2] == ["app", "database"]:
            parts = parts[2:]
        elif parts[:1] == ["database"]:
            parts = parts[1:]

        rel = Path(*parts) if parts else Path(p.name)
        APP_DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        forced = (APP_DATABASE_DIR / rel).resolve()
        return f"{u.drivername}:///{forced.as_posix()}"

    async def choose_base_url(self) -> str:
        sqlite_url = self._sqlite_force_into_app_database(self.SQLITE_URL)

        if self.DB_MODE == "sqlite" or not self.POSTGRES_URL:
            return sqlite_url

        if self.DB_MODE == "postgres":
            return self.POSTGRES_URL

        try:
            import asyncpg  # type: ignore
            u = make_url(self.POSTGRES_URL)
            conn = await asyncpg.connect(
                user=u.username or "postgres",
                password=u.password or "",
                database=(u.database or "").lstrip("/"),
                host=u.host or "localhost",
                port=u.port or 5432,
                timeout=2,
            )
            await conn.close()
            return self.POSTGRES_URL
        except Exception:
            return sqlite_url


settings = Settings()