from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Generator, Optional

from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database.models import Base, Source

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


def _render_url(u: URL) -> str:
    return u.render_as_string(hide_password=False)


def _sqlite_forced_sync_url() -> str:
    forced = settings._sqlite_force_into_app_database(settings.SQLITE_URL)
    url = make_url(str(forced))
    return _render_url(url.set(drivername="sqlite"))


def _seed_sources_path() -> Path:
    # app/database/seed_sources.json рядом с этим файлом
    return Path(__file__).with_name("seed_sources.json")


async def _seed_sources_if_empty(session: AsyncSession) -> int:
    """
    Seed table `sources` from seed_sources.json only if `sources` is empty.
    Returns inserted count.
    """
    total = await session.scalar(select(func.count()).select_from(Source))
    if (total or 0) > 0:
        logger.info("seed_sources: skipped (sources already exist: %s)", total)
        return 0

    p = _seed_sources_path()
    if not p.exists():
        logger.warning("seed_sources: file not found: %s (skipped)", p)
        return 0

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("seed_sources.json must be a JSON array")
    except Exception as e:
        logger.warning("seed_sources: failed to read %s: %s (skipped)", p, e)
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    inserted = 0
    for r in data:
        if not isinstance(r, dict):
            continue
        try:
            src = Source(
                id=str(r["id"]),
                type=r["type"],
                name=str(r["name"]),
                url=str(r.get("url") or ""),
                enabled=bool(r.get("enabled", True)),
                created_at=now,
            )
            session.add(src)
            inserted += 1
        except Exception:
            logger.warning("seed_sources: bad record skipped: %r", r)

    if inserted > 0:
        await session.commit()
        logger.info("seed_sources: inserted=%s from %s", inserted, p)
        return inserted

    logger.info("seed_sources: nothing to insert (file empty or invalid "
                "records)")
    return 0


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
        async_url = _render_url(url.set(drivername="sqlite+aiosqlite"))
        sync_url = _render_url(url.set(drivername="sqlite"))
    else:
        async_url = _render_url(url.set(drivername="postgresql+asyncpg"))
        sync_url = _render_url(url.set(drivername="postgresql+psycopg"))

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
        connect_args={"check_same_thread": False} if sync_url.startswith(
            "sqlite") else {},
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
            sync_url = _render_url(url.set(drivername="sqlite"))
        else:
            sync_url = _render_url(url.set(drivername="postgresql+psycopg"))

    except RuntimeError:
        sync_url = _sqlite_forced_sync_url()

    _ensure_sqlite_dir(sync_url)

    logger.info("DB sync_url=%s", sync_url)

    sync_engine = create_engine(
        sync_url,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False} if sync_url.startswith(
            "sqlite") else {},
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
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # seed sources after schema is created
    try:
        if AsyncSessionLocal is None:
            raise RuntimeError("AsyncSessionLocal is not initialized")
        async with AsyncSessionLocal() as session:
            await _seed_sources_if_empty(session)
    except Exception as e:
        # не валим startup из-за сидинга
        logger.warning("seed_sources: skipped due to error: %s", e)


# ================================
# Session providers
# ================================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async DB session dependency for FastAPI."""
    if AsyncSessionLocal is None:
        await init_engines()

    session = AsyncSessionLocal()
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