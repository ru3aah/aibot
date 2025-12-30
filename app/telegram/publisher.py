from __future__ import annotations

import asyncio
import logging
from typing import Optional, Union

from telethon import TelegramClient

from app.config import settings

logger = logging.getLogger(__name__)


def _normalize_target(target: str) -> str:
    """
    target может быть:
      - "@channel"
      - "channel"
      - "https://t.me/channel"
      - "-1001234567890" (id супергруппы/канала)
    """
    t = (target or "").strip()
    if not t:
        return t

    if "t.me/" in t:
        t = t.split("t.me/")[-1].strip()

    # числовой id оставляем как есть
    if t.lstrip("-").isdigit():
        return t

    # username приводим к виду "@name"
    if not t.startswith("@"):
        t = "@" + t
    return t


class TelegramPublisher:
    def __init__(self, target_channel: Optional[str] = None):
        target = target_channel or settings.TELEGRAM_CHANNEL_USERNAME or settings.TELEGRAM_CHANNEL
        if not target:
            self.target = ""
        else:
            self.target = _normalize_target(target)

    async def _send_async(self, text: str) -> int:
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            raise ValueError("TG_API_ID / TG_API_HASH не заданы")

        if not self.target:
            raise ValueError("TELEGRAM_CHANNEL_USERNAME/TELEGRAM_CHANNEL не задан")

        api_id = int(settings.TG_API_ID)
        api_hash = settings.TG_API_HASH

        async with TelegramClient(settings.TELEGRAM_SESSION_NAME, api_id, api_hash) as client:
            msg = await client.send_message(self.target, text, link_preview=False)
            return int(msg.id)

    def send(self, text: str) -> int:
        return asyncio.run(self._send_async(text))