"""comm.telegram — Telegram I/O Gateway (Sensor/Actuator).

Pure inbound/outbound communication bridge. No agent-specific logic.
Inbound messages are dispatched to the orchestrator, which routes them
to the appropriate bound agent context.
"""

import asyncio

from vox.capabilities.base import VOXCapability

from .client import TelegramClient
from .models import TelegramConfig


class TelegramCapability(VOXCapability):

    CAPABILITY_NAME = "comm.telegram"

    SENSITIVE_PARAMS = {"TELEGRAM_BOT_TOKEN"}

    PARAMS = {
        "TELEGRAM_BOT_TOKEN": ["Telegram bot token", None],
        "TELEGRAM_USER_ID": ["Authorized Telegram user ID", None],
        "TELEGRAM_LONG_TIMEOUT": ["Long polling timeout", 20],
    }

    _client: TelegramClient
    _poll_task: asyncio.Task | None

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

    # ------------------------------------------------------------------
    # Public operations (Actuator)
    # ------------------------------------------------------------------

    async def send(self, text: str) -> None:
        self.log("Sending message...")
        await self._client.send_message(text)
        self.log("Message sent")

    async def send_typing(self) -> None:
        await self._client.send_typing()

    async def send_picture(self, image_path: str, caption: str = "") -> None:
        self.log("Sending picture...")
        await self._client.send_picture(image_path, caption)
        self.log("Picture sent")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        self._client = self._build_client()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def shutdown(self) -> None:
        if getattr(self, "_poll_task", None) is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if getattr(self, "_client", None) is not None:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal: long-poll loop (Sensor)
    # ------------------------------------------------------------------

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
                            orch = self._agent.orchestrator
                            if orch:
                                await orch.dispatch_inbound_message(
                                    source="comm.telegram",
                                    payload={"content": text},
                                )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.log(f"Poll error (retry in 5s): {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
