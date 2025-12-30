from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database.db import init_db
from app.api.endpoints import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}