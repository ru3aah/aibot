# app/ai/openai_client.py
import logging

from openai import OpenAI, RateLimitError, OpenAIError
from app.config import settings

logger = logging.getLogger(__name__)


def _get_client() -> OpenAI | None:
    key = getattr(settings, "OPEN_AI_API_KEY", None)
    if not key:
        return None
    return OpenAI(api_key=key)


def make_request(
    instructions: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str | None:
    client = _get_client()
    if not client:
        logger.warning("OPEN_AI_API_KEY не задан — генерация пропущена")
        return None

    model = getattr(settings, "OPEN_AI_MODEL", None)
    if not model:
        logger.warning("OPEN_AI_MODEL не задан — генерация пропущена")
        return None

    try:
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        return response.output_text

    except RateLimitError as e:
        logger.error("Rate limit error: %s", e)
        return None
    except OpenAIError as e:
        logger.error("OpenAI API error: %s", e)
        return None
    except Exception as e:
        logger.error("OpenAI unknown error: %s", e, exc_info=True)
        return None