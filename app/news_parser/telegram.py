# app/news_parser/telegram.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from telethon import TelegramClient
from telethon.errors import RPCError

from app.config import settings

logger = logging.getLogger(__name__)


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class TgArticle:
    source: str
    type: str  # "tg"
    title: str
    summary: str
    published_at: Optional[datetime]
    url: Optional[str]


class TelegramChannelParser:
    """
    Парсер публичного Telegram-канала через Telethon.
    Требует TG_API_ID и TG_API_HASH в .env (но код можно подключить заранее).
    """

    def __init__(
        self,
        channel: str,                   # username или ссылка
        source_name: str = "Telegram",
        limit: int = 10,
        session_name: Optional[str] = None,
    ):
        self.channel = self._normalize_channel(channel)
        self.source_name = source_name
        self.limit = max(1, min(limit, 100))
        self.session_name = session_name or settings.TELEGRAM_SESSION_NAME

    def _normalize_channel(self, channel: str) -> str:
        c = (channel or "").strip()
        if not c:
            return c
        # https://t.me/<name> -> <name>
        if "t.me/" in c:
            c = c.split("t.me/")[-1]
        c = c.lstrip("@").strip("/")
        return c

    def _message_url(self, msg_id: int) -> Optional[str]:
        if not self.channel:
            return None
        return f"https://t.me/{self.channel}/{msg_id}"

    async def _parse_async(self) -> List[TgArticle]:
        api_id = settings.TG_API_ID
        api_hash = settings.TG_API_HASH

        if not api_id or not api_hash:
            raise ValueError("TG_API_ID / TG_API_HASH не заданы в .env")

        results: List[TgArticle] = []

        async with TelegramClient(self.session_name, int(api_id), api_hash) as client:
            try:
                async for msg in client.iter_messages(self.channel, limit=self.limit):
                    text = (msg.text or "").strip()
                    if not text:
                        continue

                    # title = первая строка/абзац, summary = первый абзац
                    first_line = text.splitlines()[0].strip()
                    first_paragraph = text.split("\n\n")[0].strip()

                    results.append(
                        TgArticle(
                            source=self.source_name,
                            type="tg",
                            title=first_line[:120],
                            summary=first_paragraph[:2000],
                            published_at=to_utc(msg.date),
                            url=self._message_url(msg.id),
                        )
                    )
            except RPCError as e:
                logger.error("Telethon RPC error for channel '%s': %s", self.channel, e, exc_info=True)
                return []
            except Exception as e:
                logger.error("Unexpected TG parse error for channel '%s': %s", self.channel, e, exc_info=True)
                return []

        return results

    def parse(self) -> List[TgArticle]:
        """
        Синхронная обёртка для Celery / обычного кода.
        """
        try:
            return asyncio.run(self._parse_async())
        except RuntimeError:
            # если вдруг уже есть event loop (редко для Celery, но на всякий)
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._parse_async())