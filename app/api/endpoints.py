import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from celery_worker import celery_app
from app.api.schemas import (
    KeywordCreate,
    KeywordResponse,
    KeywordUpdate,
    NewsItemResponse,
    PostResponse,
    SourceCreate,
    SourceResponse,
    SourceUpdate,
    TaskTriggerResponse,
)
from app.database.db import get_db
from app.database.models import Keyword, NewsItem, Post, Source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_LIMIT = 100
DEFAULT_LIMIT = 20


def validate_pagination(offset: int, limit: int) -> tuple[int, int]:
    offset = max(0, offset)
    limit = min(max(1, limit), MAX_LIMIT)
    return offset, limit


def validate_search_query(q: Optional[str]) -> Optional[str]:
    if not q:
        return None
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 2 characters long",
        )
    if len(q) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query too long",
        )
    return q


# =========================
# Sources
# =========================

@router.get("/sources/", response_model=List[SourceResponse], tags=["sources"])
async def list_sources(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    try:
        offset, limit = validate_pagination(offset, limit)
        result = await db.execute(select(Source).offset(offset).limit(limit))
        sources = result.scalars().all()
        return sources
    except SQLAlchemyError as e:
        logger.error("Database error in list_sources: %s", e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.get("/sources/{source_id}", response_model=SourceResponse, tags=["sources"])
async def get_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Источник с данным id не найден")
        return source
    except SQLAlchemyError as e:
        logger.error("Database error in get_source(%s): %s", source_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.post("/sources/", status_code=status.HTTP_201_CREATED, response_model=SourceResponse, tags=["sources"])
async def create_source(source_data: SourceCreate, db: AsyncSession = Depends(get_db)):
    try:
        source = Source(**source_data.model_dump())
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error creating source: %s", e)
        raise HTTPException(status_code=409, detail="Source with this data already exists")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error creating source: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create source")


@router.patch("/sources/{source_id}", response_model=SourceResponse, tags=["sources"])
async def update_source(source_id: str, source_data: SourceUpdate, db: AsyncSession = Depends(get_db)):
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Источник с данным id не найден")

        data = source_data.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(source, key, value)

        await db.commit()
        await db.refresh(source)
        return source
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error updating source %s: %s", source_id, e)
        raise HTTPException(status_code=409, detail="Update violates data constraints")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error updating source %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Failed to update source")


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["sources"])
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Источник с данным id не найден")

        await db.delete(source)
        await db.commit()
        return None
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error deleting source %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Failed to delete source")


# =========================
# Posts
# =========================

@router.get("/posts/", response_model=List[PostResponse], tags=["posts"])
async def list_posts(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    try:
        offset, limit = validate_pagination(offset, limit)
        result = await db.execute(select(Post).offset(offset).limit(limit))
        posts = result.scalars().all()
        return posts
    except SQLAlchemyError as e:
        logger.error("Database error in list_posts: %s", e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.get("/posts/{post_id}", response_model=PostResponse, tags=["posts"])
async def get_post(post_id: str, db: AsyncSession = Depends(get_db)):
    try:
        post = await db.get(Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Пост с данным id не найден")
        return post
    except SQLAlchemyError as e:
        logger.error("Database error in get_post(%s): %s", post_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


# =========================
# Keywords
# =========================

@router.get("/keywords/", response_model=List[KeywordResponse], tags=["keywords"])
async def list_keywords(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        offset, limit = validate_pagination(offset, limit)
        search_query = validate_search_query(q)

        stmt = select(Keyword)
        if search_query:
            stmt = stmt.where(Keyword.word.ilike(f"%{search_query}%"))

        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        return result.scalars().all()
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error("Database error in list_keywords: %s", e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.get("/keywords/{keyword_id}", response_model=KeywordResponse, tags=["keywords"])
async def get_keyword(keyword_id: str, db: AsyncSession = Depends(get_db)):
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404, detail="Ключевое слово не найдено")
        return keyword
    except SQLAlchemyError as e:
        logger.error("Database error in get_keyword(%s): %s", keyword_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.post("/keywords/", status_code=status.HTTP_201_CREATED, response_model=KeywordResponse, tags=["keywords"])
async def create_keyword(payload: KeywordCreate, db: AsyncSession = Depends(get_db)):
    try:
        word = payload.word.strip()
        if len(word) < 2:
            raise HTTPException(status_code=400, detail="Keyword must be at least 2 characters long")

        exists = await db.execute(select(Keyword).where(Keyword.word == word))
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Keyword already exists")

        keyword = Keyword(word=word)
        db.add(keyword)
        await db.commit()
        await db.refresh(keyword)
        return keyword
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error creating keyword: %s", e)
        raise HTTPException(status_code=409, detail="Keyword already exists")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error creating keyword: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create keyword")


@router.patch("/keywords/{keyword_id}", response_model=KeywordResponse, tags=["keywords"])
async def update_keyword(keyword_id: str, payload: KeywordUpdate, db: AsyncSession = Depends(get_db)):
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404, detail="Ключевое слово не найдено")

        data = payload.model_dump(exclude_unset=True)
        if "word" in data and data["word"] is not None:
            new_word = data["word"].strip()
            if len(new_word) < 2:
                raise HTTPException(status_code=400, detail="Keyword must be at least 2 characters long")

            exists = await db.execute(
                select(Keyword).where(Keyword.word == new_word, Keyword.id != keyword_id)
            )
            if exists.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Keyword already exists")

            keyword.word = new_word

        await db.commit()
        await db.refresh(keyword)
        return keyword
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error updating keyword %s: %s", keyword_id, e)
        raise HTTPException(status_code=409, detail="Update violates data constraints")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error updating keyword %s: %s", keyword_id, e)
        raise HTTPException(status_code=500, detail="Failed to update keyword")


@router.delete("/keywords/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["keywords"])
async def delete_keyword(keyword_id: str, db: AsyncSession = Depends(get_db)):
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404, detail="Ключевое слово не найдено")

        await db.delete(keyword)
        await db.commit()
        return None
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error deleting keyword %s: %s", keyword_id, e)
        raise HTTPException(status_code=500, detail="Failed to delete keyword")


# =========================
# News
# =========================

@router.get("/news/", response_model=List[NewsItemResponse], tags=["news"])
async def list_news(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    source_id: Optional[str] = None,
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    published_from: Optional[datetime] = None,
    published_to: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    try:
        offset, limit = validate_pagination(offset, limit)
        search_query = validate_search_query(q)

        stmt = select(NewsItem)

        if source_id:
            stmt = stmt.where(NewsItem.source_id == source_id)

        if search_query:
            stmt = stmt.where(
                or_(
                    NewsItem.title.ilike(f"%{search_query}%"),
                    NewsItem.summary.ilike(f"%{search_query}%"),
                    NewsItem.raw_text.ilike(f"%{search_query}%"),
                )
            )

        if published_from:
            stmt = stmt.where(NewsItem.published_at >= published_from)

        if published_to:
            stmt = stmt.where(NewsItem.published_at <= published_to)

        stmt = stmt.order_by(NewsItem.created_at.desc()).offset(offset).limit(limit)

        result = await db.execute(stmt)
        return result.scalars().all()
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error("Database error in list_news: %s", e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.get("/news/{news_id}", response_model=NewsItemResponse, tags=["news"])
async def get_news_item(news_id: str, db: AsyncSession = Depends(get_db)):
    try:
        item = await db.get(NewsItem, news_id)
        if not item:
            raise HTTPException(status_code=404, detail="Новость не найдена")
        return item
    except SQLAlchemyError as e:
        logger.error("Database error in get_news_item(%s): %s", news_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


# =========================
# Task triggers
# =========================

@router.post("/tasks/parse", response_model=TaskTriggerResponse, status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_parse_news():
    try:
        result = celery_app.send_task("app.tasks.parse_news", queue="aibot")
        return {"task_id": result.id, "task_name": "app.tasks.parse_news"}
    except Exception as e:
        logger.error("Failed to trigger parse news task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")


@router.post("/tasks/generate", response_model=TaskTriggerResponse, status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_generate_chain_post():
    """
    Ручной триггер: сгенерировать 1 агрегированный пост по последним PARSE_THREADS новостям из БД.
    """
    try:
        result = celery_app.send_task("app.tasks.generate_chain_post", queue="aibot")
        return {"task_id": result.id, "task_name": "app.tasks.generate_chain_post"}
    except Exception as e:
        logger.error("Failed to trigger generate_chain_post task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")


@router.post("/tasks/publish", response_model=TaskTriggerResponse, status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_publish_latest_post():
    """
    Ручной триггер: опубликовать 1 самый свежий пост со статусом GENERATED.
    """
    try:
        result = celery_app.send_task("app.tasks.publish_latest_post", queue="aibot")
        return {"task_id": result.id, "task_name": "app.tasks.publish_latest_post"}
    except Exception as e:
        logger.error("Failed to trigger publish_latest_post task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")