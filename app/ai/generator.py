import logging

from app.ai.openai_client import make_request
from app.database.models import NewsItem

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
Вы — профессиональный новостной редактор.
Сделай краткий, интересный пост для Telegram по новости: добавь emoji, суть, и один call-to-action.
Тон: живой, но без кликбейта. 1–3 абзаца, до ~700 знаков.
"""


def generate_post_text(news: NewsItem) -> str | None:
    source_name = None
    try:
        # relationship Source (если selectin сработал)
        if getattr(news, "source", None) is not None:
            source_name = getattr(news.source, "name", None)
    except Exception:
        source_name = None

    if not source_name:
        source_name = news.source_id

    prompt = f"""
Новость: {news.title}

Краткое содержание:
{news.summary}

Источник: {source_name}
"""

    logger.info("Генерация поста для новости: %s", news.id)
    text = make_request(INSTRUCTIONS, prompt)

    if not text:
        logger.warning("OpenAI вернул пустой результат для новости %s", news.id)
        return None

    return text