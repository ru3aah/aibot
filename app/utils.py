import logging
from datetime import datetime
from typing import List, Dict, Any

from sqlalchemy.orm import Session

from app.database.models import Source, NewsItem
from app.news_parser.sites import HabrParser, SiteParser

logger = logging.getLogger(__name__)


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


def save_news_items(session: Session, news_items: List[Dict[str, Any]]) -> int:
    saved_count = 0

    for item_data in news_items:
        if check_duplicate(
            session, url=item_data.get("url"), title=item_data.get("title")
        ):
            logger.debug(f"Пропущен дубликат: {item_data.get('title', 'Без названия')}")
            continue

        try:
            news_item = NewsItem(
                title=item_data["title"],
                url=item_data.get("url"),
                summary=item_data.get("summary", ""),
                source=item_data.get("source", "unknown"),
                published_at=item_data.get("published_at", datetime.now()),
                raw_text=item_data.get("raw_text"),
            )
            session.add(news_item)
            saved_count += 1
            logger.debug(f"Добавлена новость: {item_data.get('title', 'Без названия')}")
        except Exception as e:
            logger.error(
                f"Ошибка при сохранении новости '{item_data.get('title', 'Без названия')}': {e}"
            )
            continue

    try:
        session.commit()
        logger.info(f"Сохранено новостей: {saved_count} из {len(news_items)}")
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при коммите транзакции: {e}")
        raise

    return saved_count


def parse_site_source(session: Session, source: Source) -> int:
    source_type = getattr(source, "type", "site")
    if source_type != "site" or not source.enabled:
        return 0

    try:
        parser: SiteParser
        if "habr" in source.name.lower() or "habr" in (source.url or "").lower():
            parser = HabrParser()
        else:
            logger.warning(f"Парсер для источника '{source.name}' не найден")
            return 0

        logger.info(f"Парсинг новостей с источника: {source.name}")
        news_items = parser.parse()

        if not news_items:
            logger.warning(f"Не найдено новостей с источника: {source.name}")
            return 0

        saved = save_news_items(session, news_items)
        logger.info(f"Источник '{source.name}': сохранено {saved} новостей")
        return saved

    except Exception as e:
        logger.error(
            f"Ошибка при парсинге источника '{source.name}': {e}", exc_info=True
        )
        return 0


def parse_telegram_source(session: Session, source: Source) -> int:
    source_type = getattr(
        source, "type", "site"
    )  # По умолчанию 'site' если поле отсутствует
    if source_type != "tg" or not source.enabled:
        return 0

    # TODO: Реализовать парсинг Telegram-каналов
    logger.warning(
        f"Парсинг Telegram-каналов еще не реализован для источника: {source.name}"
    )
    return 0
