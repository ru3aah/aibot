from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import AsyncGenerator, Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

# ================================
# Globals
# ================================

async_engine: Optional[object] = None
sync_engine: Optional[object] = None

AsyncSessionLocal: Optional[async_sessionmaker[AsyncSession]] = None
SessionLocal: Optional[sessionmaker[Session]] = None


# ================================
# Helpers
# ================================

def _ensure_sqlite_dir(db_url: str) -> None:
    """Ensure directory exists for SQLite file DB."""
    if not db_url.startswith("sqlite"):
        return

    u = make_url(db_url)
    db_path = u.database or ""
    if not db_path or db_path == ":memory:":
        return

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _sqlite_forced_sync_url() -> str:
    """
    ВАЖНО: когда мы не можем await settings.choose_base_url() (например внутри running event loop),
    мы всё равно должны использовать тот же sqlite-файл, что и в choose_base_url().
    """
    # choose_base_url() внутри себя делает _sqlite_force_into_app_database()
    # Повторим этот же путь синхронно.
    forced = settings._sqlite_force_into_app_database(settings.SQLITE_URL)  # type: ignore[attr-defined]
    url = make_url(str(forced))
    return str(url.set(drivername="sqlite"))


# ================================
# Engines init
# ================================

async def init_engines() -> None:
    """Initialize async + sync engines (FastAPI context)."""
    global async_engine, sync_engine, AsyncSessionLocal, SessionLocal

    if async_engine is not None:
        return

    base_url = str(await settings.choose_base_url())
    url = make_url(base_url)

    if url.drivername.startswith("sqlite"):
        async_url = str(url.set(drivername="sqlite+aiosqlite"))
        sync_url = str(url.set(drivername="sqlite"))
    else:
        async_url = str(url.set(drivername="postgresql+asyncpg"))
        sync_url = str(url.set(drivername="postgresql+psycopg"))

    _ensure_sqlite_dir(async_url)
    _ensure_sqlite_dir(sync_url)

    logger.info("DB async_url=%s", async_url)
    logger.info("DB sync_url=%s", sync_url)

    async_engine = create_async_engine(
        async_url,
        echo=settings.DEBUG,
        poolclass=StaticPool if async_url.startswith("sqlite") else None,
    )

    AsyncSessionLocal = async_sessionmaker(
        async_engine,
        expire_on_commit=False,
    )

    sync_engine = create_engine(
        sync_url,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False} if sync_url.startswith("sqlite") else {},
        poolclass=StaticPool if sync_url.startswith("sqlite") else None,
    )

    SessionLocal = sessionmaker(
        sync_engine,
        expire_on_commit=False,
    )


def init_engines_sync() -> None:
    """
    Initialize sync engine/session for contexts that can't await
    (Celery, CLI scripts, aiogram handlers, etc.)
    """
    global sync_engine, SessionLocal

    if sync_engine is not None and SessionLocal is not None:
        return

    try:
        base_url = asyncio.run(settings.choose_base_url())
        url = make_url(str(base_url))
        if url.drivername.startswith("sqlite"):
            sync_url = str(url.set(drivername="sqlite"))
        else:
            sync_url = str(url.set(drivername="postgresql+psycopg"))

    except RuntimeError:
        # running event loop (aiogram и т.п.)
        # ВАЖНО: не используем settings.SQLITE_URL напрямую (sqlite:///aibot.db),
        # а форсим тот же путь, что выбирается в choose_base_url() -> app/database/aibot.db
        sync_url = _sqlite_forced_sync_url()

    _ensure_sqlite_dir(sync_url)

    logger.info("DB sync_url=%s", sync_url)

    sync_engine = create_engine(
        sync_url,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False} if sync_url.startswith("sqlite") else {},
        poolclass=StaticPool if sync_url.startswith("sqlite") else None,
    )

    SessionLocal = sessionmaker(
        sync_engine,
        expire_on_commit=False,
    )


# ================================
# Schema init
# ================================

async def init_db() -> None:
    """Create DB schema (FastAPI startup)."""
    await init_engines()
    async with async_engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)


# ================================
# Session providers
# ================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async DB session dependency for FastAPI."""
    if AsyncSessionLocal is None:
        await init_engines()

    session = AsyncSessionLocal()  # type: ignore[misc]
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextmanager
def get_db_sync() -> Generator[Session, None, None]:
    """Sync DB session (Celery, background jobs, aiogram handlers)."""
    if SessionLocal is None:
        init_engines_sync()

    if SessionLocal is None:
        raise RuntimeError("Sync DB not initialized")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()