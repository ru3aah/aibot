from contextlib import asynccontextmanager

from fastapi import FastAPI

from db.db import init_db, async_engine


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
