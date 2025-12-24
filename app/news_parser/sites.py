import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pprint import pprint
from typing import List, Optional

import requests
from bs4 import BeautifulSoup


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


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


class SiteParser(ABC):
    def __init__(self, base_url: str, article_path: str = ""):
        self.base_url = base_url
        self.article_path = article_path

    @abstractmethod
    def parse(self) -> List[Article]:
        pass

    @abstractmethod
    def normalize_url(self, article_id: str) -> Optional[str]:
        pass


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


    def normalize_url(self, article_id: str) -> Optional[str]:
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
        logging.info("Fetching Habr news feed...")

        html = self.fetch_html(self.LIST_URL)
        if not html:
            return []

        soup = self.make_soup(html)

        container = soup.find("div", class_="tm-articles-list")
        if not container:
            logging.warning("Articles container not found")
            return []

        articles = container.find_all("article")
        result: List[Article] = []

        for idx, article in enumerate(articles, start=1):
            if idx > self.MAX_ARTICLES:
                break

            logging.info(f"Parsing article {idx}/{self.MAX_ARTICLES}")

            try:
                article_id = article.get("id")
                url = self.normalize_url(article_id)
                if not url:
                    continue

                title = None
                h2 = article.find("h2")
                if h2 and h2.a and h2.a.span:
                    title = h2.a.span.text.strip()

                published_at = None
                time_tag = article.find("time")
                if time_tag and time_tag.get("datetime"):
                    try:
                        published_at = datetime.fromisoformat(
                            time_tag["datetime"]
                        )
                    except ValueError:
                        logging.warning(
                            f"Invalid datetime: {time_tag['datetime']}"
                        )

                summary = self.parse_summary_from_article(url)

                result.append(
                    Article(
                        source=self.source,
                        s_type=self.type,
                        title=title or "",
                        summary=summary,
                        published_at=published_at,
                        url=url,
                    )
                )

            except Exception as e:
                logging.warning(f"Parse error: {e}")
                continue

        logging.info(f"Done. Parsed {len(result)} articles.")
        return result



if __name__ == "__main__":
    parser = HabrParser()
    articles = parser.parse()
    pprint([vars(article) for article in articles])