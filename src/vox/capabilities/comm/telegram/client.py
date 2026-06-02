"""Telegram Bot API client.

Low-level async wrapper around the Telegram Bot API.
"""

import httpx

from .models import TelegramConfig


CLIENT_TIMEOUT_BUFFER = 5


class TelegramClient:

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.api_url = f"https://api.telegram.org/bot{config.bot_token}"
        client_timeout = config.long_timeout + CLIENT_TIMEOUT_BUFFER
        self.client = httpx.AsyncClient(timeout=client_timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def get_updates(self, offset: int) -> list[dict]:
        response = await self.client.get(
            f"{self.api_url}/getUpdates",
            params={"offset": offset, "timeout": self.config.long_timeout},
        )
        response.raise_for_status()
        return response.json().get("result", [])

    async def send_message(self, content: str) -> None:
        response = await self.client.post(
            f"{self.api_url}/sendMessage",
            json={
                "chat_id": self.config.user_id,
                "text": content,
                "parse_mode": "Markdown",
            },
        )
        response.raise_for_status()

    async def send_typing(self) -> None:
        response = await self.client.post(
            f"{self.api_url}/sendChatAction",
            json={"chat_id": self.config.user_id, "action": "typing"},
        )
        response.raise_for_status()

    async def send_picture(self, image_path: str, caption: str = "") -> None:
        with open(image_path, "rb") as image:
            response = await self.client.post(
                f"{self.api_url}/sendPhoto",
                data={"chat_id": self.config.user_id, "caption": caption},
                files={"photo": image},
            )
        response.raise_for_status()

    async def download_file(self, file_id: str) -> bytes:
        metadata = await self.client.get(
            f"{self.api_url}/getFile", params={"file_id": file_id},
        )
        metadata.raise_for_status()
        file_path = metadata.json()["result"]["file_path"]
        download_url = (
            f"https://api.telegram.org/file/bot{self.config.bot_token}/{file_path}"
        )
        response = await self.client.get(download_url)
        response.raise_for_status()
        return response.content
