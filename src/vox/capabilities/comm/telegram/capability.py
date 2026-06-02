import asyncio

from vox.capabilities.base import VOXCapability

from .client import TelegramClient
from .models import TelegramConfig


class TelegramCapability(VOXCapability):

    PARAMS = {
        "TELEGRAM_BOT_TOKEN": ["Telegram bot token", None],
        "TELEGRAM_USER_ID": ["Authorized Telegram user ID", None],
        "TELEGRAM_LONG_TIMEOUT": ["Long polling timeout", 20],
    }

    @classmethod
    async def health_check(cls) -> bool:
        return True

    def _build_client(self) -> TelegramClient:
        config = TelegramConfig(
            bot_token=self.TELEGRAM_BOT_TOKEN,
            user_id=self.TELEGRAM_USER_ID,
            long_timeout=int(self.TELEGRAM_LONG_TIMEOUT),
        )
        return TelegramClient(config)

    async def send(self, text: str) -> None:
        self.log("Sending message...")
        client = self._build_client()
        await client.send_message(text)
        self.log("Message sent")

    async def send_typing(self) -> None:
        client = self._build_client()
        await client.send_typing()

    async def send_picture(self, image_path: str, caption: str = "") -> None:
        self.log("Sending picture...")
        client = self._build_client()
        await client.send_picture(image_path, caption)
        self.log("Picture sent")

    async def boot(self) -> None:
        self._client = self._build_client()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def shutdown(self) -> None:
        if hasattr(self, "_poll_task"):
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "_client"):
            await self._client.close()

    async def _poll_loop(self) -> None:
        offset = 0
        try:
            while True:
                try:
                    updates = await self._client.get_updates(offset)
                    for update in updates:
                        update_id = update.get("update_id", 0)
                        if update_id >= offset:
                            offset = update_id + 1
                        text = update.get("message", {}).get("text", "")
                        if text:
                            await self._agent.emit("inbound_message", content=text)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.log(f"Poll error (retry in 5s): {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
