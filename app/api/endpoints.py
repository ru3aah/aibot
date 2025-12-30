import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

from app.api.schemas import SourceResponse, SourceCreate, SourceUpdate, PostResponse
from app.database.db import get_db
from app.database.models import Source, Post

from datetime import datetime
from sqlalchemy import or_
from app.api.schemas import (KeywordResponse, KeywordCreate, KeywordUpdate, 
                             NewsItemResponse, TaskTriggerResponse)
from app.database.models import Keyword, NewsItem

from celery_worker import celery_app

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Constants
MAX_LIMIT = 100
DEFAULT_LIMIT = 20

def validate_pagination(offset: int, limit: int) -> tuple[int, int]:
    """Validate and sanitize pagination parameters"""
    offset = max(0, offset)
    limit = min(max(1, limit), MAX_LIMIT)
    return offset, limit

def validate_search_query(q: Optional[str]) -> Optional[str]:
    """Validate and sanitize search query"""
    if not q:
        return None
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 2 characters long"
        )
    if len(q) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query too long"
        )
    return q

# =========================
# Sources
# =========================

@router.get("/sources/", response_model=List[SourceResponse], 
            tags=["sources"]) 
async def list_sources(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    try:
        offset, limit = validate_pagination(offset, limit)
        result = await db.execute(select(Source).offset(offset).limit(limit))
        sources = result.scalars().all()
        logger.info(f"Retrieved {len(sources)} sources")
        return sources
    except SQLAlchemyError as e:
        logger.error(f"Database error in list_sources: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )

@router.post(
    "/sources/",
    status_code=status.HTTP_201_CREATED,
    response_model=SourceResponse,
    tags=["sources"],
)
async def create_source(
    source_data: SourceCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        source = Source(**source_data.model_dump())
        db.add(source)
        await db.commit()
        await db.refresh(source)
        logger.info(f"Created source: {source.id}")
        return source
    except IntegrityError as e:
        await db.rollback()
        logger.error(f"Integrity error creating source: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source with this data already exists"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error creating source: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create source"
        )

@router.patch("/sources/{source_id}", response_model=SourceResponse, 
              tags=["sources"])
async def update_source(
    source_id: str,
    source_data: SourceUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found",
            )

        data = source_data.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(source, key, value)

        await db.commit()
        await db.refresh(source)
        logger.info(f"Updated source: {source.id}")
        return source
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error(f"Integrity error updating source {source_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update violates data constraints"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error updating source {source_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update source"
        )

@router.delete("/sources/{source_id}", 
               status_code=status.HTTP_204_NO_CONTENT, 
               tags=["sources"])
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found",
            )

        await db.delete(source)
        await db.commit()
        logger.info(f"Deleted source: {source_id}")
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error deleting source {source_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete source"
        )

# =========================
# Keywords with improved search
# =========================

@router.get("/keywords/", response_model=List[KeywordResponse], 
            tags=["keywords"])
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
            # Use parameter binding instead of f-strings for security
            stmt = stmt.where(Keyword.word.ilike(f"%{search_query}%"))
        
        stmt = stmt.offset(offset).limit(limit)
        result = await db.execute(stmt)
        keywords = result.scalars().all()
        
        logger.info(f"Retrieved {len(keywords)} keywords with query: {search_query}")
        return keywords
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error in list_keywords: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )

@router.post("/keywords/", status_code=status.HTTP_201_CREATED, 
             response_model=KeywordResponse, 
             tags=["keywords"])
async def create_keyword(
    payload: KeywordCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        # Validate word
        word = payload.word.strip()
        if len(word) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Keyword must be at least 2 characters long"
            )
        
        # Check uniqueness
        exists = await db.execute(select(Keyword).where(Keyword.word == word))
        if exists.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, 
                detail="Keyword already exists"
            )

        keyword_data = payload.model_dump()
        keyword_data['word'] = word
        keyword = Keyword(**keyword_data)
        db.add(keyword)
        await db.commit()
        await db.refresh(keyword)
        logger.info(f"Created keyword: {keyword.id}")
        return keyword
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error creating keyword: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create keyword"
        )

@router.patch("/keywords/{keyword_id}", response_model=KeywordResponse, 
              tags=["keywords"])
async def update_keyword(
    keyword_id: str,
    payload: KeywordUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Resource not found"
            )

        data = payload.model_dump(exclude_unset=True)
        if "word" in data and data["word"]:
            new_word = data["word"].strip()
            if len(new_word) < 2:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Keyword must be at least 2 characters long"
                )
            
            # Check uniqueness
            exists = await db.execute(
                select(Keyword).where(
                    Keyword.word == new_word, 
                    Keyword.id != keyword_id
                )
            )
            if exists.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, 
                    detail="Keyword already exists"
                )
            keyword.word = new_word

        await db.commit()
        await db.refresh(keyword)
        logger.info(f"Updated keyword: {keyword.id}")
        return keyword
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error updating keyword {keyword_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update keyword"
        )

# =========================
# News with improved search
# =========================

@router.get("/news/", response_model=List[NewsItemResponse], 
            tags=["news"])
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
                )
            )

        if published_from:
            stmt = stmt.where(NewsItem.published_at >= published_from)

        if published_to:
            stmt = stmt.where(NewsItem.published_at <= published_to)

        stmt = stmt.order_by(NewsItem.created_at.desc()).offset(offset).limit(limit)
        result = await db.execute(stmt)
        news_items = result.scalars().all()
        
        logger.info(f"Retrieved {len(news_items)} news items")
        return news_items
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error in list_news: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )

# =========================
# Task triggers with error handling
# =========================

@router.post(
    "/tasks/parse",
    response_model=TaskTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
async def trigger_parse_news():
    """
    Ручной триггер: запустить Celery задачу парсинга новостей.
    """
    try:
        result = celery_app.send_task("app.tasks.parse_news", queue="aibot")
        logger.info(f"Triggered parse news task: {result.id}")
        return {"task_id": result.id, "task_name": "app.tasks.parse_news"}
    except Exception as e:
        logger.error(f"Failed to trigger parse news task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger task"
        )

@router.post(
    "/tasks/generate",
    response_model=TaskTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
async def trigger_generate_posts():
    """
    Ручной триггер: запустить Celery задачу генерации постов.
    """
    try:
        result = celery_app.send_task("app.tasks.generate_posts", queue="aibot")
        logger.info(f"Triggered generate posts task: {result.id}")
        return {"task_id": result.id, "task_name": "app.tasks.generate_posts"}
    except Exception as e:
        logger.error(f"Failed to trigger generate posts task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to trigger task"
        )