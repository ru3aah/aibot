import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from telethon import TelegramClient

from app.config import settings

logger = logging.getLogger(__name__)


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Converts a datetime object to UTC timezone. If the input datetime is naive,
    it is replaced with UTC timezone information. If the datetime object already
    has timezone information, it is converted to UTC accordingly. A None input
    will result in a None output.

    :param dt: The datetime object to be converted. Can be a naive datetime,
               an aware datetime, or None.
    :type dt: Optional[datetime]
    :return: A datetime object with UTC timezone information or None if the
             input is None.
    :rtype: Optional[datetime]
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TelegramChannelParser:
    """
    TelegramChannelParser class.

    This class is designed for parsing messages from a specified Telegram
    channel. It provides asynchronous and synchronous capabilities to fetch
    and process messages within a defined limit. The messages are parsed
    to extract relevant details such as title, summary, publication date,
    and message link.

    :ivar channel_username: The username of the Telegram channel to be parsed,
        stripped of the '@' symbol if present.
    :type channel_username: str
    :ivar limit: The number of messages to fetch from the channel.
    :type limit: int
    """
    def __init__(self, channel_username: str, limit: int = 10):
        self.channel_username = channel_username.lstrip("@")
        self.limit = limit

    async def _parse_async(self) -> List[Dict[str, Any]]:
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning("TG_API_ID / TG_API_HASH не заданы -> пропускаем парсинг Telegram")
            return []

        api_id = int(settings.TG_API_ID)
        api_hash = settings.TG_API_HASH

        result: List[Dict[str, Any]] = []

        async with TelegramClient(settings.TELEGRAM_SESSION_NAME, api_id,
                                  api_hash) as client:
            async for msg in client.iter_messages(self.channel_username,
                                                  limit=self.limit):
                if not getattr(msg, "text", None):
                    continue

                text = msg.text.strip()
                first = text.split("\n\n")[0].strip()

                result.append(
                    {
                        "title": first[:120],
                        "url": f"https://t.me/{self.channel_username}/{msg.id}",
                        "summary": first[:1000],
                        "raw_text": text,
                        "published_at": to_utc(msg.date) or datetime.now(
                            timezone.utc),
                        "source_name": f"@{self.channel_username}",
                    }
                )

        return result

    def parse(self) -> List[Dict[str, Any]]:
        return asyncio.run(self._parse_async())