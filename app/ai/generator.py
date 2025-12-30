# app/ai/generator.py
import logging

from app.ai.openai_client import make_request
from app.database.models import NewsItem

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
Вы — редактор Telegram-канала.
Сделай краткое, интересное описание новости для Telegram-поста:
- 1–3 предложения
- 1–2 emoji
- лёгкий call to action
"""


def generate_post_text(news: NewsItem) -> str | None:
    source_name = None
    try:
        source_name = news.source.name if news.source else None
    except Exception:
        source_name = None

    prompt = f"""
Новость: {news.title}
Содержание: {news.summary}
Источник: {source_name or "unknown"}
"""

    logger.info("Генерация поста для новости: %s", news.id)
    post_text = make_request(INSTRUCTIONS, prompt)

    if not post_text:
        logger.warning("OpenAI вернул пустой результат для новости %s", news.id)
        return None

    return post_text.strip()
