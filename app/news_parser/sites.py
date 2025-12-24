import os
import logging
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from pprint import pprint

from datetime import timezone

import requests
import feedparser
from bs4 import BeautifulSoup

from telethon import TelegramClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")


class Article:
    def __init__(
        self,
        source: str,
        s_type: str,
        title: str,
        summary: str,
        published_at: Optional[datetime],
        url: str,
    ):
        self.source = source
        self.type = s_type
        self.title = title
        self.summary = summary
        self.published_at = published_at
        self.url = url

def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


class SiteParser(ABC):
    def __init__(self, base_url: str, article_path: str = ""):
        self.base_url = base_url
        self.article_path = article_path

    @abstractmethod
    def parse(self) -> List[Article]:
        pass

    @abstractmethod
    def normalize_url(self, article_id: str):
        pass



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
        result = []

        for entry in feed.entries[:self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed"):
                published_at = datetime(*entry.published_parsed[:6])

            summary = BeautifulSoup(
                entry.summary, "html.parser"
            ).get_text(strip=True)

            result.append(
                Article(
                    source=self.source,
                    s_type=self.type,
                    title=entry.title,
                    summary=summary,
                    published_at=published_at,
                    url=entry.link,
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
        result = []

        for entry in feed.entries[:self.MAX_ARTICLES]:
            published_at = None
            if hasattr(entry, "published_parsed"):
                published_at = datetime(*entry.published_parsed[:6])

            summary = BeautifulSoup(
                entry.summary, "html.parser"
            ).get_text(strip=True)

            result.append(
                Article(
                    source=self.source,
                    s_type=self.type,
                    title=entry.title,
                    summary=summary,
                    published_at=published_at,
                    url=entry.link,
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
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsParser/1.0)"
        }

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
            logging.warning(f"HTTP error {url}: {e}")
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
            logging.warning("Habr articles container not found")
            return []

        result = []
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
                    published_at = to_utc(datetime.fromisoformat(
                        time_tag["datetime"]
                    ))
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



class TelegramParser(SiteParser):

    MAX_MESSAGES = 10

    def __init__(self, channel: str, source: str, api_id: int, api_hash: str):
        super().__init__("https://t.me/")
        self.channel = channel
        self.source = source
        self.type = "telegram"
        self.api_id = api_id
        self.api_hash = api_hash

    def normalize_url(self, msg_id: int):
        return f"https://t.me/{self.channel}/{msg_id}"

    async def _parse_async(self) -> List[Article]:
        result = []

        async with TelegramClient(
            "news_parser", self.api_id, self.api_hash
        ) as client:
            async for msg in client.iter_messages(
                self.channel, limit=self.MAX_MESSAGES
            ):
                if not msg.text:
                    continue

                first_paragraph = msg.text.split("\n\n")[0]

                result.append(
                    Article(
                        source=self.source,
                        s_type=self.type,
                        title=first_paragraph[:80],
                        summary=first_paragraph,
                        published_at=to_utc(msg.date),
                        url=self.normalize_url(msg.id),
                    )
                )

        return result

    def parse(self) -> List[Article]:
        return asyncio.run(self._parse_async())


class WowITeParser(TelegramParser):
    def __init__(self, api_id, api_hash):
        super().__init__(
            channel="wowite",
            source="WOW IT",
            api_id=api_id,
            api_hash=api_hash,
        )


class TechNewsPlusParser(TelegramParser):
    def __init__(self, api_id, api_hash):
        super().__init__(
            channel="technewsplus",
            source="TechNewsPlus",
            api_id=api_id,
            api_hash=api_hash,
        )



if __name__ == "__main__":

    parsers = [
        HabrParser(),
        TechCrunchParser(),
        TheVergeParser(),
    ]

    if TG_API_ID and TG_API_HASH:
        parsers.extend([WowITeParser(int(TG_API_ID), TG_API_HASH),
                        TechNewsPlusParser(int(TG_API_ID), TG_API_HASH),
                        ])
    else:
        logging.warning("Telegram parsers disabled (no TG_API_ID / TG_API_HASH)")

    all_articles: List[Article] = []

    for parser in parsers:
        all_articles.extend(parser.parse())

    pprint([vars(a) for a in all_articles])