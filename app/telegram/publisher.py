# app/telegram/publisher.py
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telethon import TelegramClient


def _load_dotenv_no_override(dotenv_path: Path) -> None:
    """
    Минимальный загрузчик .env:
    - читает KEY=VALUE
    - игнорирует пустые строки и комментарии
    - НЕ перезаписывает уже существующие переменные окружения
    """
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


class TelegramPublisher:
    def __init__(self) -> None:
        # <project_root>/.env
        project_root = Path(__file__).resolve().parents[2]
        _load_dotenv_no_override(project_root / ".env")

        api_id = os.getenv("TG_API_ID")
        api_hash = os.getenv("TG_API_HASH")
        session_name = os.getenv("TELEGRAM_SESSION_NAME")
        channel_username = os.getenv("TELEGRAM_CHANNEL_USERNAME")

        if not api_id or not api_hash:
            raise RuntimeError("TG_API_ID/TG_API_HASH are not set")
        if not session_name:
            raise RuntimeError("TELEGRAM_SESSION_NAME is not set")
        if not channel_username:
            raise RuntimeError("TELEGRAM_CHANNEL_USERNAME is not set")

        try:
            self.api_id: int = int(api_id)
        except ValueError as e:
            raise RuntimeError("TG_API_ID must be an integer") from e

        self.api_hash: str = api_hash
        self.session_name: str = session_name
        self.channel_username: str = channel_username

    async def _publish_text_async(self, text: str) -> int:
        async with TelegramClient(self.session_name, self.api_id, self.api_hash) as client:
            msg = await client.send_message(self.channel_username, text, link_preview=False)
            return msg.id

    def publish_text(self, text: str) -> int:
        """
        Синхронная обёртка для Celery.
        Возвращает int message_id.
        """
        return asyncio.run(self._publish_text_async(text))