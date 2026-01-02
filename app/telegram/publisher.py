import asyncio
import threading

from telethon import TelegramClient
from app.config import settings


class TelegramPublisher:
    """
    Синхронный интерфейс для Celery tasks:
      msg_id = TelegramPublisher().publish_text(text)

    """

    def __init__(self) -> None:
        api_id = getattr(settings, "TG_API_ID", None)
        api_hash = getattr(settings, "TG_API_HASH", None)
        channel_username = getattr(settings, "TELEGRAM_CHANNEL_USERNAME", None)
        session_name = (getattr(settings, "TELEGRAM_SESSION_NAME", None) or
                        "aibot")

        if not api_id:
            raise RuntimeError("TG_API_ID is not set")
        if not api_hash:
            raise RuntimeError("TG_API_HASH is not set")
        if not channel_username:
            raise RuntimeError("TELEGRAM_CHANNEL_USERNAME is not set")

        try:
            self.api_id = int(api_id)
        except Exception as e:
            raise RuntimeError(f"TG_API_ID must be int, got: {api_id!r}") from e

        self.api_hash = str(api_hash)
        self.channel_username = str(channel_username)
        self.session_name = str(session_name)

    async def _publish_async(self, text: str) -> int:
        client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await client.connect()

        try:
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telegram session is not authorized. "
                    "Authorize the Telethon session once (create session "
                    "file) and rerun."
                )

            msg = await client.send_message(self.channel_username, text)
            return int(msg.id)
        finally:
            await client.disconnect()

    def publish_text(self, text: str) -> int:
        """
        Sync wrapper. Возвращает message_id (int).
        """
        coro = self._publish_async(text)

        try:
            running_loop = asyncio.get_running_loop()
            if running_loop.is_running():
                result: dict = {}
                error: dict = {}

                def _runner() -> None:
                    loop = asyncio.new_event_loop()
                    try:
                        asyncio.set_event_loop(loop)
                        result["msg_id"] = loop.run_until_complete(coro)
                    except Exception as e:
                        error["e"] = e
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                t = threading.Thread(target=_runner, daemon=True)
                t.start()
                t.join()

                if "e" in error:
                    raise error["e"]
                return int(result.get("msg_id", 0))
        except RuntimeError:
            pass

        return int(asyncio.run(coro))