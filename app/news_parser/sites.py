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
        s_type: str,  # "site"
        title: str,
        summary: str,
        published_at: Optional[datetime],
        url: Optional[str],
        raw_text: Optional[str] = None,
    ):
        self.source = source
        self.type = s_type
        self.title = title
        self.summary = summary
        self.published_at = published_at
        self.url = url
        self.raw_text = raw_text


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize datetime to timezone-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # считаем, что это уже UTC (чаще всего RSS даёт UTC без tzinfo)
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SiteParser(ABC):
    def __init__(self, base_url: str, article_path: str = ""):
        self.base_url = base_url
        self.article_path = article_path

    @abstractmethod
    def parse(self) -> List[Article]:
        raise NotImplementedError

    @abstractmethod
    def normalize_url(self, article_id: str):
        raise NotImplementedError


class TechCrunchParser(SiteParser):
    FEED_URL = "https://techcrunch.com/feed/"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://techcrunch.com/")
        self.source = "TechCrunch"
        self.type = "site"

    def normalize_url(self, url: str):
        return url

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in feed.entries[: self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary_html = getattr(entry, "summary", "") or ""
            summary = BeautifulSoup(summary_html, "html.parser").get_text(strip=True)

            result.append(
                Article(
                    source=self.source,
                    s_type=self.type,
                    title=getattr(entry, "title", "") or "",
                    summary=summary,
                    published_at=published_at,
                    url=getattr(entry, "link", None),
                )
            )

        return result


class TheVergeParser(SiteParser):
    FEED_URL = "https://www.theverge.com/rss/index.xml"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://www.theverge.com/")
        self.source = "The Verge"
        self.type = "site"

    def normalize_url(self, url: str):
        return url

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in feed.entries[: self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary_html = getattr(entry, "summary", "") or ""
            summary = BeautifulSoup(summary_html, "html.parser").get_text(strip=True)

            result.append(
                Article(
                    source=self.source,
                    s_type=self.type,
                    title=getattr(entry, "title", "") or "",
                    summary=summary,
                    published_at=published_at,
                    url=getattr(entry, "link", None),
                )
            )

        return result


class HabrParser(SiteParser):
    LIST_URL = "https://habr.com/ru/news/"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://habr.com/", "ru/news/")
        self.source = "Habr"
        self.type = "site"
        self.headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsParser/1.0)"}

    def normalize_url(self, article_id: str):
        if not article_id:
            return None
        article_id = article_id.replace("post_", "")
        return f"{self.base_url}{self.article_path}{article_id}/"

    def fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning("HTTP error %s: %s", url, e)
            return None

    def make_soup(self, html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def parse_summary_from_article(self, url: str) -> str:
        html = self.fetch_html(url)
        if not html:
            return ""

        soup = self.make_soup(html)
        body = soup.find(
            "div",
            class_="article-formatted-body article-formatted-body article-formatted-body_version-2",
        )
        if not body:
            return ""

        p = body.find("p")
        return p.get_text(strip=True) if p else ""

    def parse(self) -> List[Article]:
        html = self.fetch_html(self.LIST_URL)
        if not html:
            return []

        soup = self.make_soup(html)
        container = soup.find("div", class_="tm-articles-list")
        if not container:
            logger.warning("Habr articles container not found")
            return []

        result: List[Article] = []
        articles = container.find_all("article")

        for idx, article in enumerate(articles, start=1):
            if idx > self.MAX_ARTICLES:
                break

            article_id = article.get("id")
            url = self.normalize_url(article_id)
            if not url:
                continue

            title = ""
            h2 = article.find("h2")
            if h2 and h2.a and h2.a.span:
                title = h2.a.span.text.strip()

            published_at = None
            time_tag = article.find("time")
            if time_tag and time_tag.get("datetime"):
                try:
                    published_at = to_utc(datetime.fromisoformat(time_tag["datetime"]))
                except ValueError:
                    pass

            summary = self.parse_summary_from_article(url)

            result.append(
                Article(
                    source=self.source,
                    s_type=self.type,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    url=url,
                )
            )

        return result