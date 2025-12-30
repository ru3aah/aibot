import logging
from typing import Optional, Tuple

from openai import OpenAI
from openai import RateLimitError, OpenAIError, AuthenticationError, BadRequestError, APIError

from app.config import settings
from app.database.data_types import PostStatus

logger = logging.getLogger(__name__)


def _get_client() -> Optional[OpenAI]:
    if not settings.OPEN_AI_API_KEY:
        return None
    return OpenAI(api_key=settings.OPEN_AI_API_KEY)


def make_request(
    instructions: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 700,
) -> Tuple[Optional[str], PostStatus, Optional[str]]:

    client = _get_client()
    if not client:
        return None, PostStatus.SKIPPED_QUOTA, "OPEN_AI_API_KEY missing"

    if not settings.OPEN_AI_MODEL:
        return None, PostStatus.FAILED, "OPEN_AI_MODEL missing"

    try:
        response = client.responses.create(
            model=settings.OPEN_AI_MODEL,
            instructions=instructions,
            input=prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        text = getattr(response, "output_text", None)
        if text and text.strip():
            return text.strip(), PostStatus.GENERATED, None

        return None, PostStatus.RETRYABLE, "empty output_text"

    except AuthenticationError as e:
        return None, PostStatus.FAILED, str(e)

    except BadRequestError as e:
        return None, PostStatus.FAILED, str(e)

    except RateLimitError as e:
        msg = str(e)
        if "insufficient_quota" in msg:
            return None, PostStatus.SKIPPED_QUOTA, msg
        return None, PostStatus.RETRYABLE, msg

    except APIError as e:
        return None, PostStatus.RETRYABLE, str(e)

    except OpenAIError as e:
        return None, PostStatus.RETRYABLE, str(e)

    except Exception as e:
        return None, PostStatus.RETRYABLE, str(e)