import json
import logging
from typing import Sequence, Tuple, Optional

from app.ai.openai_client import make_request
from app.database.models import NewsItem
from app.database.data_types import PostStatus

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
На основе набора новостей сделай один Telegram-пост.
6–10 строк, эмодзи, 1 call-to-action.
"""

def generate_chain_post(
    news_items: Sequence[NewsItem],
) -> Tuple[Optional[str], PostStatus, Optional[str], str, str]:

    if not news_items:
        return None, PostStatus.FAILED, "no news", "[]", "empty"

    input_ids = [n.id for n in news_items]
    input_key = "-".join(n.id[:6] for n in news_items)

    parts = []
    for i, n in enumerate(news_items, 1):
        body = (n.raw_text or n.summary or "")[:800]
        parts.append(f"{i}. {n.title}\n{body}\n")

    prompt = "\n".join(parts)

    text, status, error = make_request(INSTRUCTIONS, prompt)

    if status == PostStatus.GENERATED and text:
        return text, status, None, json.dumps(input_ids), input_key

    if status in (PostStatus.SKIPPED_QUOTA, PostStatus.RETRYABLE):
        return None, status, error, json.dumps(input_ids), input_key

    fallback = "🧩 Новости:\n" + "\n".join(f"• {n.title}" for n in news_items)
    return fallback, PostStatus.GENERATED, "fallback_used", json.dumps(input_ids), input_key