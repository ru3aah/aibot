# app/utils.py
import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from app.database.models import Source, NewsItem
from app.database.data_types import SourceType
from app.news_parser.sites import HabrParser, TechCrunchParser, TheVergeParser, SiteParser
from app.news_parser.telegram import TelegramChannelParser

from app.config import settings

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_duplicate(session: Session, url: str | None = None, title: str | None = None) -> bool:
    if url:
        if session.query(NewsItem).filter(NewsItem.url == url).first():
            return True
    if title:
        if session.query(NewsItem).filter(NewsItem.title == title).first():
            return True
    return False


def save_news_items(session: Session, source: Source, items: List[dict]) -> int:
    saved = 0

    for item in items:
        url = item.get("url")
        title = item.get("title")
        if check_duplicate(session, url=url, title=title):
            logger.debug("Дубликат пропущен: %s", title or url)
            continue

        published_at = item.get("published_at") or now_utc()
        created_at = now_utc()

        # защитимся: если published_at без tzinfo — считаем UTC
        if isinstance(published_at, datetime) and published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        try:
            with session.begin_nested():
                news = NewsItem(
                    title=title or "Без названия",
                    url=url,
                    summary=item.get("summary") or "",
                    raw_text=item.get("raw_text"),
                    source_id=source.id,
                    published_at=published_at,
                    created_at=created_at,
                )
                session.add(news)
                saved += 1
        except Exception as e:
            logger.error("Ошибка сохранения новости '%s': %s", title, e, exc_info=True)

    return saved


def _pick_site_parser(source: Source) -> SiteParser | None:
    name = (source.name or "").lower()
    url = (source.url or "").lower()

    if "habr" in name or "habr.com" in url:
        return HabrParser()
    if "techcrunch" in name or "techcrunch.com" in url:
        return TechCrunchParser()
    if "theverge" in name or "theverge.com" in url or "the verge" in name:
        return TheVergeParser()

    return None


def parse_site_source(session: Session, source: Source) -> int:
    if source.type != SourceType.SITE or not source.enabled:
        return 0

    parser = _pick_site_parser(source)
    if not parser:
        logger.warning("Парсер для site-источника не найден: %s (%s)", source.name, source.url)
        return 0

    logger.info("Парсинг сайта: %s", source.name)
    articles = parser.parse()

    items = [
        {
            "title": a.title,
            "url": a.url,
            "summary": a.summary,
            "published_at": a.published_at,
            "raw_text": None,
        }
        for a in articles
    ]

    saved = save_news_items(session, source, items)
    logger.info("Источник '%s': сохранено %s новостей", source.name, saved)
    return saved


def parse_telegram_source(session: Session, source: Source) -> int:
    if source.type != SourceType.TELEGRAM or not source.enabled:
        return 0

    # канал можно хранить в source.url или source.name — выберем что есть
    channel = (source.url or source.name or "").strip()
    if not channel:
        logger.warning("TG source без channel/url: %s", source.id)
        return 0

    # TG_API_ID / TG_API_HASH берём из settings (не из строк 'your_TG_API_ID')
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        logger.warning("Telegram парсер не запущен: нет TG_API_ID / TG_API_HASH в .env")
        return 0

    try:
        api_id = int(settings.TG_API_ID)
        api_hash = str(settings.TG_API_HASH)
    except Exception as e:
        logger.warning("Telegram парсер не запущен: TG_API_ID/TG_API_HASH некорректны: %s", e)
        return 0

    session_name = getattr(settings, "TELEGRAM_SESSION_NAME", "aibot_session")
    limit = 10

    logger.info("Парсинг Telegram-канала: %s", channel)
    parser = TelegramChannelParser(
        channel=channel,
        source_name=source.name,
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        limit=limit,
    )

    articles = parser.parse()
    items = [
        {
            "title": a.title,
            "url": a.url,
            "summary": a.summary,
            "published_at": a.published_at,
            "raw_text": None,
        }
        for a in articles
    ]

    saved = save_news_items(session, source, items)
    logger.info("TG источник '%s': сохранено %s сообщений", source.name, saved)
    return saved