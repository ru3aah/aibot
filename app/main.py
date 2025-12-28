from typing import AsyncGenerator
from contextlib import contextmanager, asynccontextmanager

from sqlalchemy.ext.asyncio import (create_async_engine, async_sessionmaker,
                                    AsyncSession)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.database.models import Base
from fastapi import FastAPI

def get_async_database_url(sync_url: str) -> str:
    """Convert synchronous SQLite URL to async SQLite URL"""
    if sync_url.startswith("sqlite:///"):
        return sync_url.replace("sqlite://", "sqlite+aiosqlite://")
    elif sync_url.startswith("sqlite://"):
        return sync_url.replace("sqlite://", "sqlite+aiosqlite://")
    else:
        return sync_url

async_database_url = get_async_database_url(settings.DATABASE_URL)

async_engine = create_async_engine(
    async_database_url,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=0
)

sync_engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=0
)

async_session_factory = async_sessionmaker(async_engine)
sync_session_factory = sessionmaker(sync_engine)

@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@contextmanager
def get_db_sync():
    session = sync_session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await async_engine.dispose()


app = FastAPI(
    title="AIBot",
    version="0.1.0",
    description="AIBot - Telegram bot for AI news",
    lifespan=lifespan
)


@app.get("/")
async def read_root():
    return {"message": "Hello World"}