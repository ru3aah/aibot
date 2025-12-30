from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncGenerator, Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.models import Base

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
        connect_args={"check_same_thread": False}
        if sync_url.startswith("sqlite")
        else {},
        poolclass=StaticPool if sync_url.startswith("sqlite") else None,
    )

    SessionLocal = sessionmaker(
        sync_engine,
        expire_on_commit=False,
    )


def init_engines_sync() -> None:
    """
    Initialize sync engine/session for contexts that can't await
    (Celery, CLI scripts, etc.)
    """
    global sync_engine, SessionLocal

    if sync_engine is not None and SessionLocal is not None:
        return

    try:
        base_url = asyncio.run(settings.choose_base_url())
    except RuntimeError:
        # fallback if event loop already running
        base_url = settings.SQLITE_URL

    url = make_url(str(base_url))

    if url.drivername.startswith("sqlite"):
        sync_url = str(url.set(drivername="sqlite"))
    else:
        sync_url = str(url.set(drivername="postgresql+psycopg"))

    _ensure_sqlite_dir(sync_url)

    sync_engine = create_engine(
        sync_url,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False}
        if sync_url.startswith("sqlite")
        else {},
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
    async with async_engine.begin():  # type: ignore[union-attr]
        await async_engine.run_sync(Base.metadata.create_all)  # type: ignore[union-attr]


# ================================
# Session providers
# ================================

@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async DB session (FastAPI)."""
    if AsyncSessionLocal is None:
        await init_engines()

    async with AsyncSessionLocal() as session:  # type: ignore[misc]
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@contextmanager
def get_db_sync() -> Generator[Session, None, None]:
    """Sync DB session (Celery, background jobs)."""
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