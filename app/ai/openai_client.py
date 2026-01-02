import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OpenAIResponse:
    """
    Low-level OpenAI response wrapper (raw JSON + extracted content).
    """
    content: str
    raw: Dict[str, Any]


@dataclass
class ChatResult:
    """
    High-level result expected by generator.py: .text + .error
    """
    text: str
    error: Optional[str] = None


class OpenAIClient:
    """
    Minimal OpenAI HTTP client (no SDK).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", None)
        self.base_url = (
            base_url
            or getattr(settings, "OPENAI_BASE_URL", None)
            or "https://api.openai.com"
        ).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)

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
        timeout_s: Optional[float] = None,
    ) -> OpenAIResponse:
        url = f"{self.base_url}/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": float(temperature),
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        eff_timeout = float(timeout_s) if timeout_s is not None else self.timeout_seconds

        with httpx.Client(timeout=eff_timeout) as client:
            r = client.post(url, headers=headers, json=payload)

        if r.status_code >= 400:
            body = (r.text or "")[:2000]
            raise RuntimeError(f"OpenAI HTTP {r.status_code}: {body}")

        data = r.json()
        content = (
            (((data.get("choices") or [None])[0] or {}).get("message") or {}).get("content")
            or ""
        )
        return OpenAIResponse(content=str(content).strip(), raw=data)

    def chat(
        self,
        *,
        system: str,
        user: str,
        timeout_s: int = 45,
        temperature: float = 0.6,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> ChatResult:
        """
        Compatibility wrapper expected by app/ai/generator.py
        Returns ChatResult(text=..., error=...) and never raises.
        """
        try:
            m = model or getattr(settings, "OPENAI_MODEL", None) or "gpt-4o-mini"
            resp = self.chat_completions_create(
                model=m,
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=float(timeout_s),
            )
            return ChatResult(text=resp.content, error=None)
        except Exception as e:
            logger.warning("OpenAIClient.chat failed: %s", e)
            return ChatResult(text="", error=str(e))