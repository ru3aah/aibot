from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.engine import make_url

from app.config import settings
from app.database.models import Base


async_engine = None
sync_engine = None

AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None
SessionLocal: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(db_url: str) -> None:
    if not db_url.startswith("sqlite"):
        return

    u = make_url(db_url)
    db_path = u.database or ""
    if not db_path or db_path == ":memory:":
        return

    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)



async def init_engines() -> None:
    """Initialize sync + async engines once."""
    global async_engine, sync_engine, AsyncSessionLocal, SessionLocal

    if async_engine is not None:
        return

    base_url = await settings.choose_base_url()
    base_url = str(base_url)

    u = make_url(base_url)

    if u.drivername.startswith("sqlite"):
        async_url = str(u.set(drivername="sqlite+aiosqlite"))
        sync_url = str(u.set(drivername="sqlite"))
    else:
        async_url = str(u.set(drivername="postgresql+asyncpg"))
        sync_url = str(u.set(drivername="postgresql+psycopg"))

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


async def init_db() -> None:
    await init_engines()
    async with async_engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
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