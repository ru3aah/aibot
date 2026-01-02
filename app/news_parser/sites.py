import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Article:
    """
    Represents a news article with attributes such as title, summary, source, and
    publication details.

    The `Article` class is designed to encapsulate the core details of a news
    article. It stores information such as the source of the article, its title,
    summary, publication date, URL, and optionally, the raw text content. This
    class can be utilized to manage and manipulate article data for various
    applications such as content aggregation, analysis, or display.

    :ivar source: The source or publisher of the article.
    :type source: str
    :ivar title: The title of the article.
    :type title: str
    :ivar summary: A brief summary or description of the article's content.
    :type summary: str
    :ivar published_at: The date and time when the article was published.
    :type published_at: Optional[datetime]
    :ivar url: The URL where the article can be accessed.
    :type url: str
    :ivar raw_text: The raw text content of the article (if available).
    :type raw_text: Optional[str]
    """
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
    """
    Converts a given datetime object to a UTC datetime object.

    This function takes a datetime object and returns it converted to UTC. If the
    input datetime is naive (lacking timezone information), it assigns the UTC
    timezone. If the datetime already has a timezone, it converts it to UTC.

    :param dt: A datetime object, optionally with timezone information.
               It may also be None.
    :type dt: Optional[datetime]
    :return: A datetime object in UTC or None if the input was None.
    :rtype: Optional[datetime]
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SiteParser(ABC):
    """
    Abstract base class for site parsers.

    This class defines the interface for site parsers which are responsible for
    extracting articles from a website. It is intended to be subclassed, with
    the `parse` method implemented by the child classes which define the specific
    parsing logic for a given site.

    :ivar attribute1: Description of attribute1.
    :type attribute1: Any
    :ivar attribute2: Description of attribute2.
    :type attribute2: Any
    """
    @abstractmethod
    def parse(self) -> List[Article]:
        raise NotImplementedError


class TechCrunchParser(SiteParser):
    """
    Parses the TechCrunch RSS feed to extract articles.

    This class inherits from the SiteParser base class and is specifically designed to retrieve articles from the TechCrunch
    RSS feed. It processes the feed entries and extracts relevant details, such as the article title, summary, publication
    date, and URL, creating an `Article` object for each entry. The parsed articles are then returned as a list.

    :ivar FEED_URL: The URL of the TechCrunch RSS feed.
    :type FEED_URL: str
    :ivar MAX_ARTICLES: The maximum number of articles to process from the feed.
    :type MAX_ARTICLES: int
    """
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
                summary = BeautifulSoup(entry.summary,
                                        "html.parser").get_text(strip=True)

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
    """
    Parses RSS feed from The Verge.

    This class is designed to fetch and parse articles from the RSS feed provided
    by The Verge. It processes the feed to extract relevant details, such as title,
    summary, publication date, and URL, and returns them in the form of `Article`
    objects. Only a limited number of articles, as defined by `MAX_ARTICLES`, are
    processed from the feed.

    :ivar FEED_URL: The URL of The Verge RSS feed.
    :type FEED_URL: str
    :ivar MAX_ARTICLES: The maximum number of articles to parse from the RSS feed.
    :type MAX_ARTICLES: int
    """
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
                summary = BeautifulSoup(entry.summary,
                                        "html.parser").get_text(strip=True)

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
    Parses the RSS feed from Habr to extract and return articles.

    The class is designed to fetch the latest news articles from Habr's RSS feed,
    parse the feed to extract details like title, summary, publication date, and URL,
    and store this information in an `Article` object. It limits the number of
    articles retrieved to a predefined maximum value. The parsed articles can be used
    for further processing or integration with other systems.

    :ivar FEED_URL: The URL of the RSS feed used for fetching articles.
    :type FEED_URL: str
    :ivar MAX_ARTICLES: The maximum number of articles to fetch from the RSS feed.
    :type MAX_ARTICLES: int
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
                summary = BeautifulSoup(entry.summary,
                                        "html.parser").get_text(strip=True)

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