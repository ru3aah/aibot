import json
import logging
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from celery import chain
from sqlalchemy import text
from sqlalchemy.orm import Session

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import NewsItem, Post, PostStatus, Source
from app.telegram.publisher import TelegramPublisher

from app.utils import parse_site_source, parse_telegram_source

logger = logging.getLogger(__name__)


# ----------------------------
# Time helpers
# ----------------------------
def _utcnow() -> datetime:
    """
    Provides the current UTC time as a timezone-aware datetime object.

    This function returns the current datetime in Coordinated Universal Time
    (UTC) with timezone information included. It is useful for applications
    that require time-aware operations or comparisons in a standard
    timezone.

    :return: The current UTC time with timezone awareness.
    :rtype: datetime
    """
    return datetime.now(timezone.utc)


# ----------------------------
# FilterSettings (raw SQL, tolerant to schema drift)
# ----------------------------
def _get_filter_settings_row(session: Session) -> Optional[Dict[str, Any]]:
    """
    Retrieves the most recent row from the `filter_settings` table ordered by the
    `updated_at` field. This function executes an SQL query using the given session
    and fetches the latest record. If a row is found, it returns the row in a
    dictionary format. If no row exists, the function returns None.

    :param session: A SQLAlchemy Session object used for database access.
    :type session: Session
    :return: The most recent row from the `filter_settings` table as a dictionary
        or None if no row exists.
    :rtype: Optional[Dict[str, Any]]
    """
    row = session.execute(
        text(
            """
            SELECT id, language, updated_at, active_keywords_json
            FROM filter_settings
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    return dict(row) if row else None


def _load_active_keywords(session: Session) -> List[str]:
    """
    Loads and returns a deduplicated and processed list of active keywords stored in
    a database filter settings row. This function retrieves settings, processes the
    JSON data to extract and clean a list of strings, and ensures that the returned
    list contains unique lowercase non-empty strings.

    :param session: Active SQLAlchemy session used to query the filter settings
        row.
    :type session: Session

    :return: A list of unique active keywords, formatted as lowercase non-empty
        strings. If no valid data is found or an error occurs during processing, an
        empty list is returned.
    :rtype: List[str]
    """
    fs = _get_filter_settings_row(session)
    if not fs:
        return []

    raw = fs.get("active_keywords_json") or ""
    try:
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            return []
        out: List[str] = []
        seen = set()
        for x in data:
            if not isinstance(x, str):
                continue
            s = x.strip().lower()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    except Exception:
        return []


def _load_selected_language(session: Session, default: str = "ru") -> str:
    """
    Retrieve the selected language from filter settings or a default language.

    This function accesses filter settings stored in the session to check for
    a user-selected language preference. If no language is found in the filter
    settings or the session is empty, it returns the default language. The
    retrieved language is processed to ensure it is in lowercase and has no
    leading or trailing whitespaces.

    :param session: The session object used to retrieve the stored filter
        settings.
    :param default: The default language code to return if no language is
        found. Defaults to "ru".
    :return: The selected language code as a string.
    """
    fs = _get_filter_settings_row(session)
    if not fs:
        return default
    lang = (fs.get("language") or default).strip().lower()
    return lang or default


def _filter_signature(language: str, keywords_lc: List[str]) -> str:
    """
    Constructs a unique filtered signature string based on the given language and keywords.
    The function accepts a language string and a list of keywords, processes them to construct
    a formatted payload, and computes a SHA-1 hash of the payload. The first 12 characters of
    the hash string are returned as the resulting signature.

    :param language: Language code as a string. Defaults to 'ru' if not provided or empty.
    :param keywords_lc: A list of keywords in lowercase to be included in the signature.
                        Empty or None items are ignored, and duplicates are removed.
    :return: A 12-character string representing the hash-based signature.
    :rtype: str
    """
    lang = (language or "ru").strip().lower()
    kws = [k.strip().lower() for k in (keywords_lc or []) if k and k.strip()]
    kws_sorted = sorted(set(kws))
    payload = f"lang={lang}|kw={'|'.join(kws_sorted)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# ----------------------------
# News filtering
# ----------------------------
def _matches_keywords(item: NewsItem, keywords_lc: List[str]) -> bool:
    """
    Checks whether any of the specified keywords are present in the content of a given
    NewsItem object. The function retrieves textual content from various attributes of
    the NewsItem, aggregates them into a single string, and performs a case-insensitive
    search for the keywords.

    :param item: A NewsItem object containing the content to be searched.
    :type item: NewsItem
    :param keywords_lc: A list of keywords in lowercase to search within the item's content.
    :type keywords_lc: List[str]
    :return: True if at least one keyword is found in the content of the NewsItem;
             False otherwise.
    :rtype: bool
    """
    if not keywords_lc:
        return True

    parts = [
        (item.title or ""),
        (item.summary or ""),
        (item.raw_text or ""),
        (item.text100 or ""),
        (item.url or ""),
    ]
    hay = " ".join(parts).lower()

    for kw in keywords_lc:
        if kw in hay:
            return True
    return False


def _dedupe_news(items: List[NewsItem]) -> List[NewsItem]:
    """
    Remove duplicate news items from a list based on their URL and title.

    This function iterates through the provided list of news items and removes duplicates
    by checking the URL and title of each item. Both the URL and title are normalized
    (by stripping whitespace and converting to lowercase) during comparison. If a duplicate
    is found (based on either the URL or title), the item is excluded from the output list.

    :param items: A list of NewsItem objects to be filtered for duplicates.
    :type items: List[NewsItem]
    :return: A list of NewsItem objects with duplicates removed.
    :rtype: List[NewsItem]
    """
    seen_url = set()
    seen_title = set()
    out: List[NewsItem] = []
    for n in items:
        url = (n.url or "").strip().lower()
        title = (n.title or "").strip().lower()

        if url and url in seen_url:
            continue
        if title and title in seen_title:
            continue

        if url:
            seen_url.add(url)
        if title:
            seen_title.add(title)

        out.append(n)
    return out


def _filter_news_strict(session: Session, raw_news: List[NewsItem]) -> List[NewsItem]:
    """
    Filters the given raw news list based on active keywords loaded from the session
    in strict mode, ensuring only news items matching the keywords are included. If
    no news items match, an empty list is returned.

    :param session: A database session used to load active keywords
    :type session: Session
    :param raw_news: List of news items to be filtered
    :type raw_news: List[NewsItem]
    :return: A list of filtered news items that match the active keywords
    :rtype: List[NewsItem]
    """
    keywords_lc = _load_active_keywords(session)

    if not keywords_lc:
        return raw_news

    filtered = [n for n in raw_news if _matches_keywords(n, keywords_lc)]
    logger.info(
        "active_keywords: применили фильтр (%d слов) -> %d/%d новостей прошло",
        len(keywords_lc),
        len(filtered),
        len(raw_news),
    )

    if not filtered:
        logger.warning("active_keywords: совпадений 0 -> строгий режим, генерацию пропускаем")
        return []

    return filtered


# ----------------------------
# Redis lock helpers
# ----------------------------
_LOCK_KEY = "aibot:publish_lock"


def _get_redis_url() -> Optional[str]:
    """
    Retrieves the Redis URL from the settings module.

    This function checks for the presence of specific attributes in the
    settings module in the given order: "REDIS_URL", "CELERY_BROKER_URL",
    and "BROKER_URL". If any of these attributes are found and not None,
    their value is returned as a string. If none of these attributes are
    set, the function returns None.

    :return: The Redis URL as a string or None if no relevant attribute
        is set in the settings module.
    :rtype: Optional[str]
    """
    for attr in ("REDIS_URL", "CELERY_BROKER_URL", "BROKER_URL"):
        url = getattr(settings, attr, None)
        if url:
            return str(url)
    return None


def _acquire_publish_lock(ttl_seconds: int = 60) -> Optional[str]:
    """
    Acquires a publish lock using a Redis backend to ensure no simultaneous
    publishing processes utilize the same resource. The lock is set with a
    time-to-live (TTL) to ensure it eventually expires.

    This function handles the initialization of a connection to a Redis server,
    attempts to acquire the lock with a unique token, and returns the token if
    successful. If the connection to Redis cannot be established or any other
    errors occur, the lock acquisition is disabled.

    :param ttl_seconds: The time-to-live in seconds for the acquired lock. Defaults
        to 60 seconds.
    :return: A unique token if the lock is successfully acquired, "NOLOCK" if the
        lock acquisition fails or is disabled, or None if the lock is already
        acquired.
    """
    redis_url = _get_redis_url()
    if not redis_url:
        logger.warning("publish lock: REDIS url not found in settings, lock disabled")
        return "NOLOCK"

    try:
        import redis  # type: ignore

        r = redis.Redis.from_url(redis_url)
        token = str(uuid.uuid4())
        ok = r.set(_LOCK_KEY, token, nx=True, ex=ttl_seconds)
        if ok:
            return token
        return None
    except Exception as e:
        logger.warning("publish lock: failed to use redis (%s), lock disabled", e)
        return "NOLOCK"


def _release_publish_lock(token: Optional[str]) -> None:
    """
    Releases a publish lock in the Redis database. The function checks whether a
    given token is valid and exists. If the token is valid and matches the key in
    Redis, it deletes the lock. If the token is invalid or doesn't exist, it skips
    processing. This function ensures safe handling of distributed locks in Redis
    by verifying tokens explicitly.

    :param token: The lock token to verify and release. If set to None or "NOLOCK",
                  the function will exit early without performing any operations.
    :type token: Optional[str]
    :raises Exception: This function suppresses exceptions that may arise from
                       Redis-related operations or connectivity issues.

    :rtype: None
    :return: None
    """
    if not token or token == "NOLOCK":
        return

    redis_url = _get_redis_url()
    if not redis_url:
        return

    try:
        import redis  # type: ignore

        r = redis.Redis.from_url(redis_url)

        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        r.eval(script, 1, _LOCK_KEY, token)
    except Exception:
        pass


# ----------------------------
# Celery task: parse_news
# ----------------------------
@celery_app.task(name="app.tasks.parse_news")
def parse_news() -> Dict[str, Any]:
    """
    Parses news from multiple sources, processes the data, and logs the output. It can handle different types
    of sources (e.g., websites, Telegram channels) and appropriately calls the relevant parsing function for
    each type. The function commits parsed data to the database session, maintains a summary of the parsing
    results, and rolls back the transaction in case of errors to maintain data consistency.

    :param session: Database session used for querying and committing information. Retrieved and managed via
        a context manager to handle sessions safely.
    :param sources: List of Source objects to be processed. Each source represents an individual source of
        news/data with its metadata for parsing.

    :return: A dictionary containing the status of the operation, the total count of news items added, and a
        list of detailed information for each processed source, including any errors encountered.

    :rtype: Dict[str, Any]
    """
    results: List[Dict[str, Any]] = []

    with get_db_sync() as session:
        sources: List[Source] = session.query(Source).filter(
            Source.enabled.is_(True)).all()

        total_added = 0
        for s in sources:
            try:
                if (s.type or "").lower() == "site":
                    added = parse_site_source(session, s)
                elif (s.type or "").lower() in ("tg", "telegram"):
                    added = parse_telegram_source(session, s)
                else:
                    logger.warning("parse_news: unknown source type=%s name=%s url=%s",
                                   s.type, s.name, s.url)
                    added = 0

                session.commit()
                total_added += int(added or 0)

                results.append(
                    {
                        "source_id": s.id,
                        "name": s.name,
                        "type": s.type,
                        "added": int(added or 0),
                    }
                )
            except Exception as e:
                session.rollback()
                logger.exception("parse_news: failed source=%s (%s)",
                                 s.name, e)
                results.append(
                    {
                        "source_id": s.id,
                        "name": s.name,
                        "type": s.type,
                        "added": 0,
                        "error": str(e),
                    }
                )

    return {"status": "ok", "parsed": sum(x.get("added", 0) for x in results),
            "sources": results}


# ----------------------------
# Celery task: generate_chain_post
# ----------------------------
@celery_app.task(name="app.tasks.generate_chain_post")
def generate_chain_post_task(_prev: Any = None) -> Dict[str, Any]:
    """
    Generates a chain post by fetching, filtering, and processing news items from
    the database, then creates and saves a generated post in the system. If no
    filtered news items match the active keywords, the task is skipped, and a
    status message is returned.

    :param _prev: Previous task result or input data necessary for chaining in
        Celery tasks.
    :type _prev: Any
    :return: A dictionary containing details of the generated post or status
        information in case of task skipping.
    :rtype: Dict[str, Any>
    """
    from app.ai.generator import generate_chain_post
    # returns tuple (text, status, error, input_news_ids_json, input_key)

    with get_db_sync() as session:
        window = max(int(getattr(settings, "PARSE_THREADS", 10)) * 3, 30)

        raw_news: List[NewsItem] = (
            session.query(NewsItem)
            .order_by(NewsItem.created_at.desc())
            .limit(window)
            .all()
        )

        raw_news = _dedupe_news(raw_news)

        lang = _load_selected_language(session, default="ru")
        keywords_lc = _load_active_keywords(session)

        filtered = _filter_news_strict(session, raw_news)

        if not filtered:
            sig = _filter_signature(lang, keywords_lc)
            kw_display = ", ".join(keywords_lc) if keywords_lc else "—"
            msg = f"⚠️ По выбранным ключевым словам новостей нет: {kw_display}"

            logger.warning(
                "no_news_for_active_keywords: lang=%s keywords=%s sig=%s",
                lang,
                keywords_lc,
                sig,
            )

            return {
                "status": "skipped",
                "reason": "no_news_for_active_keywords",
                "message": msg,
                "generated": 0,
                "post_status": None,
                "has_text": False,
                "post_id": None,
                "language": lang,
                "keywords": keywords_lc,
                "filter_sig": sig,
            }

        limit_n = max(int(getattr(settings, "PARSE_THREADS", 10)), 1)
        selected_news = filtered[:limit_n]

        sig = _filter_signature(lang, keywords_lc)

        generated_text, gen_status, gen_error, input_news_ids_json, legacy_input_key = generate_chain_post(
            selected_news,
            language=lang,
        )

        generated_text = (generated_text or "")
        if not isinstance(generated_text, str):
            generated_text = str(generated_text)

        has_text = bool(generated_text.strip())

        legacy_key = "-".join([n.id[:6] for n in selected_news])
        input_key = f"F:{sig}:{legacy_key}"

        post = Post(
            id=str(uuid.uuid4()),
            news_id=None,
            generated_text=generated_text if has_text else "",
            status=PostStatus.GENERATED,
            published_at=None,
            created_at=_utcnow(),
            telegram_message_id=None,
            error=(gen_error or None),
            input_news_ids=input_news_ids_json or json.dumps([n.id for n in
                                                              selected_news],
                                                             ensure_ascii=False),
            input_key=input_key,
        )

        session.add(post)
        session.commit()

        return {
            "status": "success",
            "generated": 1,
            "post_status": str(post.status),
            "has_text": has_text,
            "post_id": post.id,
            "language": lang,
            "keywords": keywords_lc,
            "filter_sig": sig,
            "news_count": len(selected_news),
            "gen_status": gen_status,
        }


# ----------------------------
# Celery task: publish_latest_post
# ----------------------------
@celery_app.task(name="app.tasks.publish_latest_post")
def publish_latest_post(_prev: Any = None) -> Dict[str, Any]:
    """
    Publishes the latest eligible post to a Telegram channel by acquiring a lock,
    filtering eligible posts, and updating their status in the database. If no post
    is eligible, it returns the appropriate metadata indicating skipped actions.

    :param _prev: Placeholder parameter for potential future use cases.
    :type _prev: Any
    :return: A dictionary containing metadata about the publishing process,
        including the status of the operation, number of posts published,
        and any failures or retryable errors.
    :rtype: Dict[str, Any]
    :raises: This function does not raise errors explicitly. Exceptions encountered
        during Telegram publishing or database updates are handled internally and
        logged, with the status returned in the result metadata.
    """
    batch_limit = int(getattr(settings, "PUBLISH_BATCH_LIMIT", 5))
    claim_ttl_min = int(getattr(settings, "CLAIM_TTL_MINUTES", 25))

    effective_limit = 1

    lock_token: Optional[str] = None
    try:
        lock_token = _acquire_publish_lock(ttl_seconds=90)
        if lock_token is None:
            return {
                "status": "skipped",
                "reason": "locked",
                "published": 0,
                "claimed_elsewhere": 0,
                "retryable": 0,
                "failed_empty": 0,
                "batch_limit": batch_limit,
                "effective_limit": effective_limit,
            }

        with get_db_sync() as session:
            lang = _load_selected_language(session, default="ru")
            keywords_lc = _load_active_keywords(session)
            sig = _filter_signature(lang, keywords_lc)
            prefix = f"F:{sig}:"

            cutoff = _utcnow() - timedelta(minutes=claim_ttl_min)
            cleared = (
                session.query(Post)
                .filter(
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.like("CLAIM:%"),
                    Post.created_at < cutoff,
                )
                .update({Post.telegram_message_id: None},
                        synchronize_session=False)
            )
            session.commit()
            if cleared:
                logger.warning("publish: cleared stuck CLAIM=%s", cleared)

            candidates: List[Post] = (
                session.query(Post)
                .filter(
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.is_(None),
                    Post.input_key.like(prefix + "%"),
                )
                .order_by(Post.created_at.asc())
                .limit(effective_limit)
                .all()
            )

            if not candidates:
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "language": lang,
                    "keywords": keywords_lc,
                }

            publisher = TelegramPublisher()
            post = candidates[0]

            claim = f"CLAIM:{uuid.uuid4()}"
            updated = (
                session.query(Post)
                .filter(
                    Post.id == post.id,
                    Post.status == PostStatus.GENERATED,
                    Post.telegram_message_id.is_(None),
                )
                .update({Post.telegram_message_id: claim},
                        synchronize_session=False)
            )
            session.commit()

            if updated != 1:
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 1,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                }

            p: Optional[Post] = session.query(Post).filter(Post.id ==
                                                           post.id).first()
            if not p or not (p.generated_text or "").strip():
                session.query(Post).filter(Post.id == post.id).update(
                    {Post.telegram_message_id: None},
                    synchronize_session=False,
                )
                session.commit()
                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 1,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                }

            try:
                msg_id = publisher.publish_text(p.generated_text)

                session.query(Post).filter(Post.id == post.id).update(
                    {
                        Post.status: PostStatus.PUBLISHED,
                        Post.published_at: _utcnow(),
                        Post.telegram_message_id: str(msg_id),
                        Post.error: None,
                    },
                    synchronize_session=False,
                )
                session.commit()

                return {
                    "status": "done",
                    "published": 1,
                    "claimed_elsewhere": 0,
                    "retryable": 0,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "post_id": post.id,
                }

            except Exception as e:
                logger.exception("publish: telegram error, marking RETRYABLE: %s",
                                 e)
                session.query(Post).filter(Post.id == post.id).update(
                    {
                        Post.status: PostStatus.RETRYABLE,
                        Post.error: str(e)[:2000],
                        Post.telegram_message_id: None,
                    },
                    synchronize_session=False,
                )
                session.commit()

                return {
                    "status": "done",
                    "published": 0,
                    "claimed_elsewhere": 0,
                    "retryable": 1,
                    "failed_empty": 0,
                    "batch_limit": batch_limit,
                    "effective_limit": effective_limit,
                    "filter_sig": sig,
                    "post_id": post.id,
                }

    finally:
        _release_publish_lock(lock_token)


# ----------------------------
# Celery task: run_pipeline (parse -> generate -> publish)
# ----------------------------
@celery_app.task(name="app.tasks.run_pipeline")
def run_pipeline() -> Dict[str, Any]:
    """
    Executes a pipeline of tasks to process, generate, and publish news
    content. This function initiates a Celery task chain consisting of
    parsing news, generating posts, and publishing the latest content,
    and returns a dictionary containing the task execution status and
    the root task ID.

    :returns:
        A dictionary containing the status of the execution and the root
        task ID of the Celery chain.

    :rtype: Dict[str, Any]
    """
    result = chain(
        parse_news.s(),
        generate_chain_post_task.s(),
        publish_latest_post.s(),
    ).apply_async()

    return {"status": "started", "root_task_id": result.id}