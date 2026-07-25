"""Telegram Bot API adapter for comm.gateway.

Parses the standard Telegram Webhook JSON (Bot API v5+) into
VOXInboundMessage, and sends outbound messages via the Bot API
sendMessage endpoint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from ..models import VOXInboundMessage, VOXOutboundMessage
from .base import BaseAdapter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


class TelegramAdapter(BaseAdapter):

    CHANNEL = "telegram"

    def __init__(self, config: dict) -> None:
        self._bot_token: str = config.get("TELEGRAM_BOT_TOKEN", "")
        self._webhook_secret: str = config.get("TELEGRAM_WEBHOOK_SECRET", "")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Request verification
    # ------------------------------------------------------------------

    def verify_request(self, request: Any) -> bool:
        if not self._webhook_secret:
            return True
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        import hmac as _hmac
        return _hmac.compare_digest(token, self._webhook_secret)

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(self, raw_data: dict) -> VOXInboundMessage:
        """Parse a Telegram Bot API update into VOXInboundMessage.

        Supports message, edited_message, and callback_query update types.
        """
        update_id = raw_data.get("update_id")

        message = raw_data.get("message") or raw_data.get("edited_message")
        callback = raw_data.get("callback_query")

        if callback:
            return self._parse_callback(callback, update_id)
        if message:
            return self._parse_message(message, update_id)

        return VOXInboundMessage(
            message_id=str(update_id or ""),
            channel=self.CHANNEL,
            sender_id="unknown",
            content_type="event",
            raw_payload=raw_data,
        )

    def _parse_message(self, msg: dict, update_id: int | None) -> VOXInboundMessage:
        sender = msg.get("from", {})
        chat = msg.get("chat", {})
        text = msg.get("text", "")
        caption = msg.get("caption", "")

        content_type, text, media_url = self._classify_content(msg, text or caption)

        return VOXInboundMessage(
            message_id=str(update_id or msg.get("message_id", "")),
            channel=self.CHANNEL,
            sender_id=str(chat.get("id", sender.get("id", ""))),
            sender_metadata={
                "username": sender.get("username"),
                "first_name": sender.get("first_name"),
                "last_name": sender.get("last_name"),
                "is_bot": sender.get("is_bot", False),
                "chat_type": chat.get("type"),
                "message_id": msg.get("message_id"),
            },
            content_type=content_type,
            text=text,
            media_url=media_url,
            raw_payload=msg,
        )

    def _parse_callback(self, cb: dict, update_id: int | None) -> VOXInboundMessage:
        sender = cb.get("from", {})
        message = cb.get("message", {})
        chat = message.get("chat", {})

        return VOXInboundMessage(
            message_id=str(update_id or cb.get("id", "")),
            channel=self.CHANNEL,
            sender_id=str(chat.get("id", sender.get("id", ""))),
            sender_metadata={
                "username": sender.get("username"),
                "first_name": sender.get("first_name"),
                "is_bot": sender.get("is_bot", False),
                "chat_type": chat.get("type"),
                "callback_query_id": cb.get("id"),
                "callback_data": cb.get("data"),
            },
            content_type="event",
            text=cb.get("data"),
            raw_payload=cb,
        )

    @staticmethod
    def _classify_content(msg: dict, fallback_text: str) -> tuple[str, str | None, str | None]:
        if msg.get("photo"):
            return "image", msg.get("caption"), None
        if msg.get("voice") or msg.get("audio"):
            return "audio", None, None
        if msg.get("video") or msg.get("document"):
            return "file", msg.get("caption"), None
        if msg.get("sticker"):
            return "event", msg.get("sticker", {}).get("emoji"), None
        return "text", fallback_text, None

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        if not self._bot_token:
            logger.error("Telegram send failed: no TELEGRAM_BOT_TOKEN configured")
            return False

        try:
            client = await self._ensure_client()
            payload = {
                "chat_id": message.recipient_id,
                "text": message.text,
            }
            if message.parse_mode and message.parse_mode.upper() != "PLAIN":
                payload["parse_mode"] = message.parse_mode

            reply_markup = message.metadata.get("reply_markup")
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = await client.post(
                f"/bot{self._bot_token}/sendMessage",
                json=payload,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_TELEGRAM_API,
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
