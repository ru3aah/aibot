import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Article:
    def __init__(
        self,
        source: str,
        title: str,
        summary: str,
        published_at: Optional[datetime],
        url: str,
        raw_text: Optional[str] = None,
    ):
        self.source = source
        self.title = title
        self.summary = summary
        self.published_at = published_at
        self.url = url
        self.raw_text = raw_text


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SiteParser(ABC):
    @abstractmethod
    def parse(self) -> List[Article]:
        raise NotImplementedError


class TechCrunchParser(SiteParser):
    FEED_URL = "https://techcrunch.com/feed/"
    MAX_ARTICLES = 10

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in feed.entries[: self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary = ""
            if hasattr(entry, "summary") and entry.summary:
                summary = BeautifulSoup(entry.summary, "html.parser").get_text(strip=True)

            result.append(
                Article(
                    source="TechCrunch",
                    title=getattr(entry, "title", "") or "",
                    summary=summary,
                    published_at=published_at,
                    url=getattr(entry, "link", "") or "",
                )
            )

        return result


class TheVergeParser(SiteParser):
    FEED_URL = "https://www.theverge.com/rss/index.xml"
    MAX_ARTICLES = 10

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in feed.entries[: self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary = ""
            if hasattr(entry, "summary") and entry.summary:
                summary = BeautifulSoup(entry.summary, "html.parser").get_text(strip=True)

            result.append(
                Article(
                    source="The Verge",
                    title=getattr(entry, "title", "") or "",
                    summary=summary,
                    published_at=published_at,
                    url=getattr(entry, "link", "") or "",
                )
            )

        return result


class HabrParser(SiteParser):
    """
    ВАЖНО:
    HTML на Habr часто меняется, поэтому парсить /ru/news/ через BeautifulSoup нестабильно.
    RSS у Habr гораздо стабильнее.
    """
    FEED_URL = "https://habr.com/ru/rss/news/?fl=ru"
    MAX_ARTICLES = 10

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in feed.entries[: self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary = ""
            if hasattr(entry, "summary") and entry.summary:
                summary = BeautifulSoup(entry.summary, "html.parser").get_text(strip=True)

            result.append(
                Article(
                    source="Habr",
                    title=getattr(entry, "title", "") or "",
                    summary=summary,
                    published_at=published_at,
                    url=getattr(entry, "link", "") or "",
                )
            )

        return result