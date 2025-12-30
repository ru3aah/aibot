# app/utils.py
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from app.database.models import Source, NewsItem
from app.news_parser.sites import HabrParser, SiteParser
from app.news_parser.telegram import TelegramChannelParser

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def check_duplicate(session: Session, url: str = None, title: str = None) -> bool:
    if url:
        existing = session.query(NewsItem).filter(NewsItem.url == url).first()
        if existing:
            return True

    if title:
        existing = session.query(NewsItem).filter(NewsItem.title == title).first()
        if existing:
            return True

    return False


def save_news_items(session: Session, source_id: str, news_items: List[Dict[str, Any]]) -> int:
    saved_count = 0

    for item_data in news_items:
        if check_duplicate(
            session, url=item_data.get("url"), title=item_data.get("title")
        ):
            logger.debug("Пропущен дубликат: %s", item_data.get("title", "Без названия"))
            continue

        try:
            published_at = item_data.get("published_at") or _now_utc()
            created_at = _now_utc()

            news_item = NewsItem(
                title=item_data["title"],
                url=item_data.get("url"),
                summary=item_data.get("summary", ""),
                raw_text=item_data.get("raw_text"),
                source_id=source_id,
                published_at=published_at,
                created_at=created_at,
            )
            session.add(news_item)
            saved_count += 1
            logger.debug("Добавлена новость: %s", item_data.get("title", "Без названия"))
        except Exception as e:
            logger.error(
                "Ошибка при сохранении новости '%s': %s",
                item_data.get("title", "Без названия"),
                e,
                exc_info=True,
            )
            continue

    try:
        session.commit()
        logger.info("Сохранено новостей: %s из %s", saved_count, len(news_items))
    except Exception as e:
        session.rollback()
        logger.error("Ошибка при коммите транзакции: %s", e, exc_info=True)
        raise

    return saved_count


def parse_site_source(session: Session, source: Source) -> int:
    source_type = getattr(source, "type", "site")
    if source_type != "site" or not source.enabled:
        return 0

    try:
        parser: Optional[SiteParser] = None

        # Сейчас поддерживаем Habr. Остальные — по аналогии добавишь.
        if "habr" in source.name.lower() or "habr" in (source.url or "").lower():
            parser = HabrParser()
        else:
            logger.warning("Парсер для источника '%s' не найден", source.name)
            return 0

        logger.info("Парсинг новостей с источника: %s", source.name)
        articles = parser.parse()

        if not articles:
            logger.warning("Не найдено новостей с источника: %s", source.name)
            return 0

        # приводим к словарям
        payload: List[Dict[str, Any]] = []
        for a in articles:
            payload.append(
                {
                    "title": a.title,
                    "url": a.url,
                    "summary": a.summary,
                    "raw_text": None,
                    "published_at": a.published_at or _now_utc(),
                }
            )

        saved = save_news_items(session, source.id, payload)
        logger.info("Источник '%s': сохранено %s новостей", source.name, saved)
        return saved

    except Exception as e:
        logger.error("Ошибка при парсинге источника '%s': %s", source.name, e, exc_info=True)
        return 0


def parse_telegram_source(session: Session, source: Source) -> int:
    """
    Ожидаем, что у источника Telegram:
    - source.type == "tg"
    - source.url содержит @channel или https://t.me/channel или просто channel
      (можно и name использовать — но лучше url)
    """
    source_type = getattr(source, "type", "site")
    if source_type != "tg" or not source.enabled:
        return 0

    channel = (source.url or source.name or "").strip()
    if not channel:
        logger.warning("TG источник без channel/url: '%s' (%s)", source.name, source.id)
        return 0

    try:
        logger.info("Парсинг Telegram-канала: %s", channel)

        parser = TelegramChannelParser(
            channel=channel,
            source_name=source.name or "Telegram",
            limit=10,
        )
        articles = parser.parse()

        if not articles:
            logger.warning("Не найдено сообщений в Telegram-канале: %s", channel)
            return 0

        payload: List[Dict[str, Any]] = []
        for a in articles:
            payload.append(
                {
                    "title": a.title,
                    "url": a.url,
                    "summary": a.summary,
                    "raw_text": None,
                    "published_at": a.published_at or _now_utc(),
                }
            )

        saved = save_news_items(session, source.id, payload)
        logger.info("Telegram '%s': сохранено %s новостей", source.name, saved)
        return saved

    except ValueError as e:
        # нет ключей — это ожидаемо, пока отложили
        logger.warning("Telegram парсер не запущен: %s", e)
        return 0
    except Exception as e:
        logger.error("Ошибка при парсинге Telegram '%s': %s", source.name, e, exc_info=True)
        return 0