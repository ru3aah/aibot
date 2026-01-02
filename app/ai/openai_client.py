import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OpenAIResponse:
    """
    Represents a response received from OpenAI.

    This class encapsulates the structured response provided by OpenAI's API, including both
    the processed content and the raw response. It is primarily designed to help users work with
    the responses in an organized manner.

    :ivar content: The processed content extracted from the OpenAI response, typically
                   representing the main data or message intended for use.
    :type content: str
    :ivar raw: The full raw response received from OpenAI's API, as a dictionary containing
               all returned metadata and information.
    :type raw: Dict[str, Any]
    """
    content: str
    raw: Dict[str, Any]


class OpenAIClient:
    """
    A client for interacting with the OpenAI API.

    This class provides functionality to communicate with the OpenAI API by
    allowing users to create chat completions using the API. It handles
    authentication and request construction, and abstracts the API interactions
    into a simple interface for ease of use.

    :ivar api_key: The API key used for authenticating with the OpenAI API.
    :type api_key: Optional[str]
    :ivar base_url: The base URL for the OpenAI API. Defaults to the official
        OpenAI API endpoint if not provided.
    :type base_url: Optional[str]
    :ivar timeout_seconds: The timeout period, in seconds, for API requests.
        Defaults to 60.0 seconds.
    :type timeout_seconds: float
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", None)
        self.base_url = (base_url or getattr(settings, "OPENAI_BASE_URL",
                                             None) or
                         "https://api.openai.com").rstrip("/")
        self.timeout_seconds = timeout_seconds

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def chat_completions_create(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.6,
        max_tokens: Optional[int] = None,
    ) -> OpenAIResponse:
        url = f"{self.base_url}/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                r = client.post(url, headers=headers, json=payload)

            if r.status_code >= 400:
                body = (r.text or "")[:2000]
                raise RuntimeError(f"OpenAI HTTP {r.status_code}: {body}")

            data = r.json()
            content = (
                (((data.get("choices") or [None])[0] or {}).get("message") or
                 {}).get("content")
                or ""
            )
            return OpenAIResponse(content=str(content).strip(), raw=data)

        except Exception as e:
            logger.warning("OpenAIClient failed: %s", e)
            raise