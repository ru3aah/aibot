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
    """
    Validates and adjusts pagination parameters to ensure they stay within acceptable bounds.
    This function ensures `offset` is non-negative and `limit` is clamped between a defined
    minimum and maximum range.

    :param offset: The starting point for pagination. Must be a non-negative integer.
    :type offset: int
    :param limit: The maximum number of items to return, constrained within a specific range.
    :type limit: int
    :return: A tuple containing the validated offset and limit values.
    :rtype: tuple[int, int]
    """
    offset = max(0, offset)
    limit = min(max(1, limit), MAX_LIMIT)
    return offset, limit


def validate_search_query(q: Optional[str]) -> Optional[str]:
    """
    Validate the search query string provided by the user. Ensures that the query meets
    specific length requirements. If the query does not meet the requirements, an
    HTTPException is raised. Otherwise, a cleaned version of the query is returned.
    Returns None if the query is empty.

    :param q: Search query string to validate.
    :type q: Optional[str]
    :return: Cleaned search query string if valid, or None if the input query is empty.
    :rtype: Optional[str]
    :raises HTTPException: Raised if the query is shorter than 2 characters or longer
        than 100 characters.
    """
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
    """
    Retrieves a paginated list of sources from the database. The result supports
    pagination using the `offset` and `limit` parameters. This operation queries
    the database for the sources within the specified range and returns the
    retrieved list.

    :param offset: The starting point of the query for paginated results.
    :type offset: int
    :param limit: The maximum number of sources to be retrieved.
    :type limit: int
    :param db: The database session used to query the sources.
    :type db: AsyncSession
    :return: A list of sources retrieved from the database.
    :rtype: List[SourceResponse]
    :raises HTTPException: If a database error occurs during the query execution.
    """
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
    """
    Handles the retrieval of a Source by its unique identifier. The endpoint interacts
    with the database to fetch the Source specified by the `source_id`. If the Source is
    not found, a 404 HTTPException is raised. In case of a database error, a 500 HTTPException
    is thrown.

    :param source_id: The unique identifier of the Source to retrieve
    :param db: The database session dependency used for querying the database
    :return: The Source object corresponding to the provided `source_id`
    :rtype: SourceResponse
    :raises HTTPException: 404 error if the Source is not found
    :raises HTTPException: 500 error if a database error occurs
    """
    try:
        source = await db.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Источник с данным id не найден")
        return source
    except SQLAlchemyError as e:
        logger.error("Database error in get_source(%s): %s", source_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.post("/sources/", status_code=status.HTTP_201_CREATED,
             response_model=SourceResponse, tags=["sources"])
async def create_source(source_data: SourceCreate, db: AsyncSession =
Depends(get_db)):
    """
    Creates a new source entry in the database utilizing the provided source
    data. This function accepts source information and adds it to the
    database. If the operation succeeds, the new source is returned.
    Otherwise, appropriate HTTP exceptions are raised based on the type of
    error encountered.

    :param source_data: Data required to create a new source
    :type source_data: SourceCreate
    :param db: The database session used to interact with the database
    :type db: AsyncSession
    :return: The newly created source data
    :rtype: SourceResponse
    :raises HTTPException: If a source with the same data already exists,
        HTTP status code 409 is raised. If a general database error occurs,
        HTTP status code 500 is raised.
    """
    try:
        source = Source(**source_data.model_dump())
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error creating source: %s", e)
        raise HTTPException(status_code=409, detail="Source with this data "
                                                    "already exists")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error creating source: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create source")


@router.patch("/sources/{source_id}", response_model=SourceResponse,
              tags=["sources"])
async def update_source(source_id: str, source_data: SourceUpdate,
                        db: AsyncSession = Depends(get_db)):
    """
    Updates an existing source entry in the database with new data provided.

    This function retrieves a source object based on the source_id passed as a path parameter.
    If the source is not found, a 404 HTTP Exception is raised. The source is updated with any
    supplied fields, provided in `source_data`. The database is committed to save the changes
    and the updated source object is returned. Integrity errors such as constraints violations
    and overall database errors during execution result in respective HTTP 409 or 500
    exceptions.

    :param source_id: Identifier of the source to be updated.
    :type source_id: str
    :param source_data: An object containing updated fields for the source.
    :type source_data: SourceUpdate
    :param db: The asynchronous session for database interaction.
    :type db: AsyncSession
    :return: Updated source object.
    :rtype: Source
    :raises HTTPException: Raised if the source ID is not found, if the update violates
        data constraints, or if a database error occurs.
    """
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
        raise HTTPException(status_code=409, detail="Update violates data "
                                                    "constraints")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error updating source %s: %s", source_id, e)
        raise HTTPException(status_code=500, detail="Failed to update source")


@router.delete("/sources/{source_id}",
               status_code=status.HTTP_204_NO_CONTENT, tags=["sources"])
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    """
    Deletes a source from the database by its ID. This function handles database
    operations to locate and delete the specified source. It returns no content
    when the deletion is successful. In case the source is not found or in case
    of database errors, appropriate HTTP exceptions are raised.

    :param source_id: Identifier of the source to be deleted.
    :type source_id: str
    :param db: Asynchronous database session dependency.
    :type db: AsyncSession
    :return: None
    :rtype: None
    :raises HTTPException: 404 if the source is not found, 500 if a database error occurs.
    """
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
    """
    Fetches a paginated list of posts from the database.

    This function retrieves a list of posts using SQLAlchemy. It performs
    validation on pagination parameters and utilizes dependency injection
    to get an asynchronous database session.

    :param offset: The starting index for pagination (non-negative integer).
    :param limit: The maximum number of posts to return, constrained to a
        default and maximum value as defined by `DEFAULT_LIMIT` and
        `MAX_LIMIT` constants, respectively.
    :param db: The asynchronous database session dependency provided
        via `Depends()`.
    :return: A list of posts retrieved from the database.
    :rtype: List[PostResponse]
    :raises HTTPException: Raises a 500 HTTPException if a database
        error occurs during execution.
    """
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
    """
    Retrieve a specific post by its ID from the database. This endpoint fetches the
    post data associated with a given `post_id`. If the post is not found, a 404
    HTTP exception is raised. If a database error occurs during retrieval, a 500
    HTTP exception is raised.

    :param post_id: Unique identifier for the post to retrieve
    :type post_id: str
    :param db: Database session dependency for executing queries
    :type db: AsyncSession
    :return: The post corresponding to the provided `post_id`
    :rtype: PostResponse
    """
    try:
        post = await db.get(Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Пост с данным id не "
                                                        "найден")
        return post
    except SQLAlchemyError as e:
        logger.error("Database error in get_post(%s): %s", post_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


# =========================
# Keywords
# =========================

@router.get("/keywords/", response_model=List[KeywordResponse],
            tags=["keywords"])
async def list_keywords(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetches a paginated list of keywords from the database with optional search filtering.

    This endpoint retrieves a list of keyword entries, allowing filtering by a search query.
    Pagination is implemented via offset and limit parameters. If a search query is provided,
    only keywords matching the query (case-insensitively) are returned.

    :param offset: An integer defining the starting point of the pagination. Must be
        greater than or equal to 0.
    :param limit: An integer defining the maximum number of items to be returned per
        request. Must be between 1 and MAX_LIMIT (inclusive).
    :param q: An optional string representing the search query. If provided, it must
        have a minimum length of 2 and a maximum length of 100 characters. The query
        is used to filter keywords based on partial matches.
    :param db: An instance of AsyncSession provided via FastAPI's dependency injection.
        Used for executing the database query.
    :return: A list of KeywordResponse objects containing the matching keywords.
    :raises HTTPException: Raised if an HTTP-specific error occurs, such as invalid input
        or database-related errors reported to the client.
    :raises SQLAlchemyError: Raised internally for database-related errors. These errors
        are logged and transformed into a generic HTTP 500 error response.
    """
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


@router.get("/keywords/{keyword_id}", response_model=KeywordResponse,
            tags=["keywords"])
async def get_keyword(keyword_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retrieve a keyword by its ID from the database. The endpoint fetches the keyword
    record based on the provided keyword ID. If the keyword does not exist, a 404
    error is raised. In case of a database error during the retrieval process,
    an appropriate 500 error is raised.

    :param keyword_id: ID of the keyword to be retrieved
    :type keyword_id: str
    :param db: Database session dependency
    :type db: AsyncSession
    :return: The retrieved keyword record
    :rtype: KeywordResponse
    """
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404, detail="Ключевое слово не "
                                                        "найдено")
        return keyword
    except SQLAlchemyError as e:
        logger.error("Database error in get_keyword(%s): %s", keyword_id, e)
        raise HTTPException(status_code=500, detail="Database error occurred")


@router.post("/keywords/", status_code=status.HTTP_201_CREATED,
             response_model=KeywordResponse, tags=["keywords"])
async def create_keyword(payload: KeywordCreate, db: AsyncSession = Depends(
    get_db)):
    """
    Handles the creation of a new keyword. A keyword must be at least 2 characters long
    and should not already exist in the database. If the provided keyword does not meet
    these requirements, appropriate exceptions are raised. This function interacts with
    the database to store and retrieve the newly created keyword entry. If any database
    errors occur during the process, they will also result in exceptions being raised.

    :param payload: Includes the data for the keyword that needs to be created.
    :type payload: KeywordCreate
    :param db: Database session dependency for interacting with the database.
    :type db: AsyncSession
    :return: The created Keyword object after being stored in the database.
    :rtype: KeywordResponse
    :raises HTTPException: 400 if the keyword is less than 2 characters;
                           409 if the keyword already exists in the database;
                           500 for a generic server error during database interaction.
    """
    try:
        word = payload.word.strip()
        if len(word) < 2:
            raise HTTPException(status_code=400, detail="Keyword must be at "
                                                        "least 2 characters "
                                                        "long")

        exists = await db.execute(select(Keyword).where(Keyword.word == word))
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Keyword already "
                                                        "exists")

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


@router.patch("/keywords/{keyword_id}", response_model=KeywordResponse,
              tags=["keywords"])
async def update_keyword(keyword_id: str, payload: KeywordUpdate,
                         db: AsyncSession = Depends(get_db)):
    """
    Updates an existing keyword in the database with the provided details. The
    endpoint allows updating certain attributes of a keyword, ensuring constraints
    such as uniqueness and minimum length for words are met. If the keyword does
    not exist or constraints are violated, appropriate HTTP exceptions are raised.

    :param keyword_id: The unique identifier of the keyword to be updated.
    :type keyword_id: str
    :param payload: The details to update the keyword. Only provided fields will
        be updated.
    :type payload: KeywordUpdate
    :param db: An asynchronous database session dependency.
    :type db: AsyncSession
    :return: The updated keyword details.
    :rtype: KeywordResponse
    :raises HTTPException: If the keyword is not found, data constraints are
        violated, or a database error occurs.
    """
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404, detail="Ключевое слово не найдено")

        data = payload.model_dump(exclude_unset=True)
        if "word" in data and data["word"] is not None:
            new_word = data["word"].strip()
            if len(new_word) < 2:
                raise HTTPException(status_code=400, detail="Keyword must be "
                                                            "at least 2 "
                                                            "characters long")

            exists = await db.execute(
                select(Keyword).where(Keyword.word == new_word, Keyword.id != keyword_id)
            )
            if exists.scalar_one_or_none():
                raise HTTPException(status_code=409,
                                    detail="Keyword already exists")

            keyword.word = new_word

        await db.commit()
        await db.refresh(keyword)
        return keyword
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error("Integrity error updating keyword %s: %s",
                     keyword_id, e)
        raise HTTPException(status_code=409,
                            detail="Update violates data constraints")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error updating keyword %s: %s",
                     keyword_id, e)
        raise HTTPException(status_code=500, detail="Failed to update keyword")


@router.delete("/keywords/{keyword_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               tags=["keywords"])
async def delete_keyword(keyword_id: str, db: AsyncSession = Depends(get_db)):
    """
    Deletes a keyword by its identifier.

    This function performs the deletion of a keyword specified by the given
    identifier (`keyword_id`) from the database. If the keyword is not found,
    an HTTP 404 exception is raised. In the event of a database-related issue,
    appropriate logging is performed, and an HTTP 500 exception is raised.

    :param keyword_id: The unique identifier for the keyword to be deleted.
    :param db: Database session dependency provided as an asynchronous
               session.
    :return: None
    :raises HTTPException: If the keyword is not found or a database error
                            occurs.
    """
    try:
        keyword = await db.get(Keyword, keyword_id)
        if not keyword:
            raise HTTPException(status_code=404,
                                detail="Ключевое слово не найдено")

        await db.delete(keyword)
        await db.commit()
        return None
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Database error deleting keyword %s: %s",
                     keyword_id, e)
        raise HTTPException(status_code=500,
                            detail="Failed to delete keyword")


# =========================
# News
# =========================

@router.get("/news/",
            response_model=List[NewsItemResponse], tags=["news"])
async def list_news(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    source_id: Optional[str] = None,
    q: Optional[str] = Query(None, min_length=2, max_length=100),
    published_from: Optional[datetime] = None,
    published_to: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetches a list of news items filtered and paginated based on given parameters.

    This endpoint retrieves a list of news items from the database,
    allowing optional filtering criteria such as source ID, search query,
    and publication date range. Pagination is supported through `offset`
    and `limit` parameters. The function ensures query validation and
    proper database interaction, returning the results in a structured format.

    :param offset: The number of items to skip before starting to fetch
                   the news items. Must be a non-negative integer.
    :param limit: The maximum number of news items to return. Must be a
                  positive integer not exceeding a predefined maximum.
    :param source_id: Optional unique identifier of the news source to filter
                      the news items.
    :param q: Optional search query string to search the news items by their
              title, summary, or raw text. Must have a length between 2 and 100
              characters if provided.
    :param published_from: Optional datetime filtering news items published
                           on or after the given date.
    :param published_to: Optional datetime filtering news items published
                         on or before the given date.
    :param db: The database session dependency used to interact with the database.

    :return: A list of news items that match the specified filtering criteria.
    """
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
    """
    Retrieves a single news item by its unique identifier (news_id). This function
    interfaces with the database to fetch news item details. If the news item is
    not found, a 404 error is raised, and if a database operation fails, a 500
    error is returned.

    :param news_id: Unique identifier for the requested news item.
    :type news_id: str
    :param db: Database session dependency for database interactions.
    :type db: AsyncSession
    :return: The retrieved news item corresponding to the provided news_id.
    :rtype: NewsItemResponse
    """
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

@router.post("/tasks/parse", response_model=TaskTriggerResponse,
             status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_parse_news():
    """
    Triggers a task for parsing news using the Celery task queue. This endpoint
    sends an asynchronous parsing task to the configured worker queue and returns
    the information about the triggered task including its ID and name.

    :return: A dictionary containing the task ID and task name of the triggered
             parsing task.
    :rtype: dict
    :raises HTTPException: Raised with status code 500 if the task could not be
                            triggered due to an internal error.
    """
    try:
        result = celery_app.send_task("app.tasks.parse_news", queue="aibot")
        return {"task_id": result.id, "task_name": "app.tasks.parse_news"}
    except Exception as e:
        logger.error("Failed to trigger parse news task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")


@router.post("/tasks/generate", response_model=TaskTriggerResponse,
             status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_generate_chain_post():
    """
    Triggers the `generate_chain_post` asynchronous task using Celery. This endpoint
    enables clients to initiate task execution and receive a task identifier for tracking.

    :raises HTTPException: If the task cannot be triggered due to an internal server error.

    :return: A dictionary containing the task ID and the name of the initiated task.
    :rtype: dict
    """
    try:
        result = celery_app.send_task("app.tasks.generate_chain_post",
                                      queue="aibot")
        return {"task_id": result.id, "task_name":
            "app.tasks.generate_chain_post"}
    except Exception as e:
        logger.error("Failed to trigger generate_chain_post task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")


@router.post("/tasks/publish", response_model=TaskTriggerResponse,
             status_code=status.HTTP_202_ACCEPTED, tags=["tasks"])
async def trigger_publish_latest_post():
    """
    Triggers the asynchronous publishing of the latest post using a Celery task.

    This endpoint initiates a background task to publish the latest post by
    sending the task to the "aibot" Celery queue. It operates asynchronously,
    allowing the user to continue interacting with the API without waiting for
    the task to complete.

    :return: A dictionary containing the ID and name of the triggered Celery task.
    :rtype: dict
    :raises HTTPException: If there is an error while triggering the task.
    """
    try:
        result = celery_app.send_task("app.tasks.publish_latest_post",
                                      queue="aibot")
        return {"task_id": result.id, "task_name":
            "app.tasks.publish_latest_post"}
    except Exception as e:
        logger.error("Failed to trigger publish_latest_post task: %s", e)
        raise HTTPException(status_code=500, detail="Failed to trigger task")