# app/news_parser/telegram.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from telethon import TelegramClient

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
    title: str
    summary: str
    published_at: Optional[datetime]
    url: Optional[str]


class TelegramChannelParser:
    """
    Парсер публичного канала Telegram через Telethon.

    channel:
      - "somechannel"
      - "@somechannel"
      - "https://t.me/somechannel" (тоже поддержим)
    """

    def __init__(
        self,
        channel: str,
        source_name: str,
        api_id: int,
        api_hash: str,
        session_name: str = "aibot_session",
        limit: int = 10,
    ):
        self.channel = self._normalize_channel(channel)
        self.source_name = source_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.limit = limit

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        c = (channel or "").strip()
        if c.startswith("https://t.me/"):
            c = c.replace("https://t.me/", "").strip("/")
        if c.startswith("@"):
            c = c[1:]
        return c

    def _message_url(self, msg_id: int) -> str:
        return f"https://t.me/{self.channel}/{msg_id}"

    async def _parse_async(self) -> List[TgArticle]:
        result: List[TgArticle] = []

        async with TelegramClient(
            self.session_name,
            self.api_id,
            self.api_hash,
        ) as client:
            async for msg in client.iter_messages(self.channel, limit=self.limit):
                text = (msg.text or "").strip()
                if not text:
                    continue

                first_para = text.split("\n\n")[0].strip()
                title = (first_para[:80] + "…") if len(first_para) > 80 else first_para

                result.append(
                    TgArticle(
                        source=self.source_name,
                        title=title,
                        summary=first_para,
                        published_at=to_utc(getattr(msg, "date", None)),
                        url=self._message_url(msg.id),
                    )
                )

        return result

    def parse(self) -> List[TgArticle]:
        """
        В Celery-процессе часто нет running loop — asyncio.run ок.
        Если loop уже есть — создаём задачу в нём.
        """
        try:
            return asyncio.run(self._parse_async())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._parse_async())
