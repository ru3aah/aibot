from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import NewsItem, Source
from app.news_parser.sites import TechCrunchParser, TheVergeParser, HabrParser, Article
from app.news_parser.telegram import TelegramChannelParser

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    """
    Get the current UTC date and time.

    This function retrieves the current date and time in Coordinated Universal
    Time (UTC) as a `datetime` object with timezone information.

    :return: The current UTC date and time as a timezone-aware `datetime` object.
    :rtype: datetime
    """
    return datetime.now(timezone.utc)


def make_text100(summary: Optional[str], raw_text: Optional[str]) -> str:
    """
    Generates a trimmed version of the given text respecting a limit of 100 characters.

    This function takes in an optional summary and a raw text input, determines which text to use
    based on the presence and non-emptiness of the summary, trims any leading or trailing whitespace,
    and limits the resulting string to a maximum of 100 characters.

    :param summary: A string representing the brief summary, which serves as the primary text to trim.
    :param raw_text: A string containing the fallback text to use if no valid summary is provided.
    :return: A string containing up to the first 100 characters of the processed input.
    """
    s = (summary or "").strip()
    if s:
        return s[:100]
    return (raw_text or "").strip()[:100]


def is_duplicate_news(session: Session, title: str, text100: str) -> bool:
    """
    Determine if a news item is a duplicate based on the given title and text snippet.
    This function checks if a news item with either the provided title or the first
    100 characters of its text (or both) already exists in the database.

    It utilizes the provided database session to query for any matching records. If
    neither the title nor the text snippet is provided, the function returns False
    immediately. Otherwise, it constructs conditions for matching and checks the
    database for duplicates.

    :param session: Database session object used to query the database.
    :type session: Session
    :param title: The title of the news item to be checked.
    :type title: str
    :param text100: A snippet of the news item's text limited to 100 characters.
    :type text100: str
    :return: Returns True if a duplicate news item exists, False otherwise.
    :rtype: bool
    """
    title = (title or "").strip()
    text100 = (text100 or "").strip()

    if not title and not text100:
        return False

    conditions = []
    if title:
        conditions.append(NewsItem.title == title)
    if text100:
        conditions.append(NewsItem.text100 == text100)

    return session.query(NewsItem.id).filter(or_(*conditions)).first() is not None


def _save_article(session: Session, source: Source, a: Article) -> bool:
    """
    Saves an article to the database if it is not a duplicate. The function compares the
    title and a shortened version of the article text to identify duplicates. If the
    article is unique, it is stored as a new `NewsItem` record.

    The function trims and processes certain fields of the article before they are
    saved. The `summary` and `raw_text` fields are used to generate a shortened
    text (`text100`) for duplicate checking. A new database session entry will
    only be added if the article is not found to be a duplicate based on the given
    parameters.

    :param session: The active database session used to store and check for
        duplicates.
    :type session: Session
    :param source: The source object associated with the article, providing
        metadata like source identifier.
    :type source: Source
    :param a: The article object containing its details such as title, summary,
        raw text, and published date.
    :type a: Article

    :return: `True` if the article was saved successfully; `False` if it was
        identified as a duplicate and not stored.
    :rtype: bool
    """
    title = (a.title or "").strip()
    summary = (a.summary or "").strip() or None
    raw_text = (a.raw_text or "").strip() or None
    url = (a.url or "").strip() or None

    text100 = make_text100(summary, raw_text)
    if is_duplicate_news(session, title, text100):
        return False

    item = NewsItem(
        title=title,
        url=url,
        summary=summary,
        raw_text=raw_text,
        text100=text100 or None,
        source_id=source.id,
        published_at=a.published_at,
        created_at=now_utc(),
    )
    session.add(item)
    return True


def _pick_site_parser(source: Source):
    """
    Determines and returns the appropriate site parser based on the provided source.
    The function evaluates specific patterns in the source's name or URL to identify
    the suitable parser. If no matching pattern is found, it returns None.

    :param source: The source object containing information about the site such as its
        name and URL.
    :type source: Source
    :return: An instance of a site-specific parser matching the given source,
        or None if no match is found.
    :rtype: TechCrunchParser | TheVergeParser | HabrParser | None
    """
    key = f"{(source.name or '').lower()} {(source.url or '').lower()}".strip()

    if "techcrunch" in key:
        return TechCrunchParser()
    if "theverge" in key or "the verge" in key or "verge.com" in key:
        return TheVergeParser()
    if "habr" in key:
        return HabrParser()
    return None


def parse_site_source(session: Session, source: Source) -> int:
    """
    Parses a given site's source using a specific site parser and saves the parsed
    articles into the database through the given session.

    The function identifies the appropriate parser for the provided source and
    processes its articles. If no parser is found for the source, a warning is
    logged, and the function returns `0`. The processing limits the number of
    articles to a predefined thread count (`PARSE_THREADS`) specified in the
    application's settings. When an article is successfully saved, the count of
    added articles is incremented. If any exceptions occur during the parsing
    process, they are logged with details for debugging, and the error is
    gracefully handled without stopping the entire function.

    :param session: Database session used for storing parsed articles.
    :type session: Session
    :param source: The source object containing details about the site or
                   feed to be parsed.
    :type source: Source
    :return: The number of newly added articles successfully saved to the
             database.
    :rtype: int
    """
    parser = _pick_site_parser(source)
    if parser is None:
        logger.warning("Unknown SITE source (no parser match): name=%s url=%s",
                       source.name, source.url)
        return 0

    added = 0
    try:
        articles = parser.parse()
        for a in articles[: settings.PARSE_THREADS]:
            if _save_article(session, source, a):
                added += 1
    except Exception as e:
        logger.exception("Site parser failed for %s: %s",
                         source.name, e)

    return added


def parse_telegram_source(session: Session, source: Source) -> int:
    """
    Parses a Telegram source and extracts articles from it. The method looks for a specific Telegram
    channel URL in the provided `source` and processes its content through a parsing mechanism. Relevant
    articles are extracted, created, and saved using helper functions. The number of successfully saved
    articles is returned as the result. If an error occurs during parsing, it skips processing the source.

    :param session: Database session used to save the extracted articles.
    :param source: The source object containing details about the Telegram channel, including the URL.
    :return: The number of articles successfully added to the system.
    :rtype: int
    """
    channel = (source.url or "").strip()
    if not channel:
        return 0

    if "t.me/" in channel:
        channel = channel.split("t.me/")[-1]
    channel = channel.lstrip("@").strip()

    added = 0
    try:
        parser = TelegramChannelParser(channel_username=channel, limit=settings.PARSE_THREADS)
        items = parser.parse()
    except Exception as e:
        logger.warning("Telegram parse skipped for %s: %s", source.name, e)
        return 0

    for it in items:
        a = Article(
            source=source.name,
            title=it.get("title", "") or "",
            summary=it.get("summary", "") or "",
            published_at=it.get("published_at"),
            url=it.get("url", "") or "",
            raw_text=it.get("raw_text"),
        )
        if _save_article(session, source, a):
            added += 1

    return added