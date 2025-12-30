# app/news_parser/sites.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Article:
    source: str
    title: str
    summary: str
    published_at: Optional[datetime]
    url: Optional[str]


class SiteParser(ABC):
    def __init__(self, base_url: str):
        self.base_url = base_url

    @abstractmethod
    def parse(self) -> List[Article]:
        raise NotImplementedError


class TechCrunchParser(SiteParser):
    FEED_URL = "https://techcrunch.com/feed/"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://techcrunch.com/")
        self.source_name = "TechCrunch"

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in getattr(feed, "entries", [])[: self.MAX_ARTICLES]:
            published_at: Optional[datetime] = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary_html = getattr(entry, "summary", "") or ""
            summary = BeautifulSoup(summary_html, "html.parser").get_text(strip=True)

            url = getattr(entry, "link", None)
            title = getattr(entry, "title", "").strip()

            result.append(
                Article(
                    source=self.source_name,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    url=url,
                )
            )

        return result


class TheVergeParser(SiteParser):
    FEED_URL = "https://www.theverge.com/rss/index.xml"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://www.theverge.com/")
        self.source_name = "The Verge"

    def parse(self) -> List[Article]:
        feed = feedparser.parse(self.FEED_URL)
        result: List[Article] = []

        for entry in getattr(feed, "entries", [])[: self.MAX_ARTICLES]:
            published_at: Optional[datetime] = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = to_utc(datetime(*entry.published_parsed[:6]))

            summary_html = getattr(entry, "summary", "") or ""
            summary = BeautifulSoup(summary_html, "html.parser").get_text(strip=True)

            url = getattr(entry, "link", None)
            title = getattr(entry, "title", "").strip()

            result.append(
                Article(
                    source=self.source_name,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    url=url,
                )
            )

        return result


class HabrParser(SiteParser):
    LIST_URL = "https://habr.com/ru/news/"
    MAX_ARTICLES = 10

    def __init__(self):
        super().__init__("https://habr.com/")
        self.source_name = "Habr"
        self.headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsParser/1.0)"}

    def _normalize_article_url(self, article_id: str) -> Optional[str]:
        if not article_id:
            return None
        article_id = article_id.replace("post_", "").strip()
        if not article_id:
            return None
        return f"{self.base_url}/ru/news/{article_id}/"

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning("HTTP error %s: %s", url, e)
            return None

    def _make_soup(self, html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def _parse_summary_from_article(self, url: str) -> str:
        html = self._fetch_html(url)
        if not html:
            return ""

        soup = self._make_soup(html)

        body = soup.find("div", class_="article-formatted-body")
        if not body:
            body = soup.find("div", {"class": lambda x: x and "article-formatted-body" in x})

        if not body:
            return ""

        p = body.find("p")
        return p.get_text(strip=True) if p else ""

    def parse(self) -> List[Article]:
        html = self._fetch_html(self.LIST_URL)
        if not html:
            return []

        soup = self._make_soup(html)
        container = soup.find("div", class_="tm-articles-list")
        if not container:
            logger.warning("Habr articles container not found")
            return []

        result: List[Article] = []
        articles = container.find_all("article")

        for article in articles[: self.MAX_ARTICLES]:
            article_id = article.get("id", "")
            url = self._normalize_article_url(article_id)
            if not url:
                continue

            title = ""
            h2 = article.find("h2")
            if h2:
                a = h2.find("a")
                if a:
                    span = a.find("span")
                    title = (span.get_text(strip=True) if span else a.get_text(strip=True)) or ""

            published_at: Optional[datetime] = None
            time_tag = article.find("time")
            if time_tag and time_tag.get("datetime"):
                try:
                    published_at = to_utc(datetime.fromisoformat(time_tag["datetime"]))
                except Exception:
                    published_at = None

            summary = self._parse_summary_from_article(url)

            result.append(
                Article(
                    source=self.source_name,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    url=url,
                )
            )

        return result