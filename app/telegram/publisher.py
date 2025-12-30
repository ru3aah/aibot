import asyncio
import logging
from typing import Optional

from telethon import TelegramClient

from app.config import settings

logger = logging.getLogger(__name__)


class TelegramPublisher:
    def __init__(self, target_channel: Optional[str] = None):
        # target_channel может быть "@channel" или "channel"
        self.target_channel = (target_channel or settings.TELEGRAM_CHANNEL_USERNAME or settings.TELEGRAM_CHANNEL)
        if self.target_channel:
            self.target_channel = self.target_channel.lstrip("@")

    async def _send_async(self, text: str) -> str:
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            raise ValueError("TG_API_ID / TG_API_HASH не заданы")

        if not self.target_channel:
            raise ValueError("TELEGRAM_CHANNEL_USERNAME/TELEGRAM_CHANNEL не задан")

        api_id = int(settings.TG_API_ID)
        api_hash = settings.TG_API_HASH

        async with TelegramClient(settings.TELEGRAM_SESSION_NAME, api_id, api_hash) as client:
            msg = await client.send_message(self.target_channel, text)
            # msg.id — int
            return str(msg.id)

    def send(self, text: str) -> str:
        return asyncio.run(self._send_async(text))