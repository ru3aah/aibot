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
    return datetime.now(timezone.utc)


def make_text100(summary: Optional[str], raw_text: Optional[str]) -> str:
    s = (summary or "").strip()
    if s:
        return s[:100]
    return (raw_text or "").strip()[:100]


def is_duplicate_news(session: Session, title: str, text100: str) -> bool:
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
    key = f"{(source.name or '').lower()} {(source.url or '').lower()}".strip()

    if "techcrunch" in key:
        return TechCrunchParser()
    if "theverge" in key or "the verge" in key or "verge.com" in key:
        return TheVergeParser()
    if "habr" in key:
        return HabrParser()

    # если не можем понять — пропускаем, чтобы не было неконтролируемых дублей
    return None


def parse_site_source(session: Session, source: Source) -> int:
    parser = _pick_site_parser(source)
    if parser is None:
        logger.warning("Unknown SITE source (no parser match): name=%s url=%s", source.name, source.url)
        return 0

    added = 0
    try:
        articles = parser.parse()
        for a in articles[: settings.PARSE_THREADS]:
            if _save_article(session, source, a):
                added += 1
    except Exception as e:
        logger.exception("Site parser failed for %s: %s", source.name, e)

    return added


def parse_telegram_source(session: Session, source: Source) -> int:
    """
    Если нет TG ключей — проект должен жить -> просто skip.
    source.url: https://t.me/<channel> или @<channel> или <channel>
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