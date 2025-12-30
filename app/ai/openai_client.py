import logging

from openai import OpenAI, RateLimitError, OpenAIError
from app.config import settings

logger = logging.getLogger(__name__)


def _build_client() -> OpenAI | None:
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


client: OpenAI | None = _build_client()


def make_request(
    instructions: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> str | None:
    """
    Возвращает сгенерированный текст или None.
    """
    if not settings.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is not set (check .env)")
        return None

    if not settings.OPENAI_MODEL:
        logger.error("OPENAI_MODEL is not set (check .env)")
        return None

    global client
    if client is None:
        client = _build_client()

    try:
        response = client.responses.create(
            model=settings.OPENAI_MODEL,
            instructions=instructions,
            input=prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        return response.output_text

    except RateLimitError as e:
        logger.error("OpenAI rate limit: %s", e)
        return None

    except OpenAIError as e:
        # сюда попадёт и 401 invalid_api_key, и прочие ошибки API
        logger.error("OpenAI API error: %s", e)
        return None

    except Exception as e:
        logger.exception("Unexpected OpenAI client error: %s", e)
        return None