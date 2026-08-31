"""FleetMessenger — War Room broadcast singleton.

Posts operational alerts to a configured Telegram channel using the root-level
bot token.
"""

import httpx

from vox.observability import VOXForensicLogger


class FleetMessenger:
    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        logger: VOXForensicLogger,
    ) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self.logger = logger
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"https://api.telegram.org/bot{self._bot_token}",
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def post_message(self, text: str) -> bool:
        try:
            client = await self._ensure_client()
            resp = await client.post(
                "/sendMessage",
                json={
                    "chat_id": self._channel_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 — send failure
            self.logger.error(f"FleetMessenger send failed: {exc}")
            return False

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
