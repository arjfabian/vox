"""Telegram Bot API adapter for comm.gateway.

Bridges the Telegram Bot API in both directions:

  * Inbound via ``getUpdates`` long-polling (or the standard Telegram
    webhook payload) parsed into VOXInboundMessage and forwarded to the
    caller-supplied dispatch callback.
  * Outbound via the Bot API ``sendMessage`` endpoint.

Configuration is supplied by the bound ``comm.gateway`` capability.
Non-secret parameters come from ``capability.yml``; secrets are injected
by the workload Vault.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ...models import VOXInboundMessage, VOXOutboundMessage
from ..base import BaseAdapter

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_POLL_RETRY_DELAY = 5

# The client read timeout must exceed the long-poll timeout.
_POLL_TIMEOUT_BUFFER = 5


class TelegramAdapter(BaseAdapter):
    """Telegram Bot API adapter.

    The adapter is intentionally configuration-agnostic. Configuration is
    resolved by the bound capability before the adapter is used.

    Required secret:
        TELEGRAM_BOT_TOKEN

    Optional secret:
        TELEGRAM_WEBHOOK_SECRET

    Non-secret parameter:
        TELEGRAM_LONG_TIMEOUT
    """

    CHANNEL = "telegram"
    WEBHOOK_PATH = "/webhook/telegram"

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        return bool(config.get("TELEGRAM_BOT_TOKEN"))

    def __init__(
        self,
        config: dict[str, Any],
        dispatch: Callable[[VOXInboundMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._bot_token: str = str(config.get("TELEGRAM_BOT_TOKEN") or "")
        self._webhook_secret: str = str(config.get("TELEGRAM_WEBHOOK_SECRET") or "")

        self._dispatch = dispatch
        self._client: httpx.AsyncClient | None = None
        self._poll_task: asyncio.Task | None = None
        self._offset = 0

        raw_long_timeout = config.get("TELEGRAM_LONG_TIMEOUT", 25)

        self._long_timeout = (
            int(raw_long_timeout) if raw_long_timeout is not None else 25
        )

        self._client_timeout = self._long_timeout + _POLL_TIMEOUT_BUFFER

        self._validate_timeouts()

    def _validate_timeouts(self) -> None:
        """Validate the Telegram long-poll/client timeout relationship."""
        if self._long_timeout < 1:
            raise ValueError(
                f"TELEGRAM_LONG_TIMEOUT must be >= 1, got {self._long_timeout}"
            )

        if self._client_timeout <= self._long_timeout:
            raise ValueError(
                "Incompatible Telegram timeouts: client read timeout "
                f"({self._client_timeout}s) must exceed the long-poll timeout "
                f"({self._long_timeout}s). Otherwise every quiet getUpdates "
                "poll dies with a ReadTimeout and retries forever."
            )

    # ------------------------------------------------------------------
    # Request verification
    # ------------------------------------------------------------------

    def verify_request(
        self,
        request: Any,
        body: bytes | None = None,
    ) -> bool:
        """Verify an inbound Telegram webhook request.

        The webhook secret is optional. If it is not configured, webhook
        verification is intentionally skipped.
        """
        if not self._webhook_secret:
            return True

        token = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        import hmac

        return hmac.compare_digest(
            token,
            self._webhook_secret,
        )

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def parse_inbound(
        self,
        raw_data: dict,
    ) -> VOXInboundMessage:
        """Parse a Telegram Bot API update into VOXInboundMessage.

        Supports message, edited_message, and callback_query update types.
        """
        update_id = raw_data.get("update_id")

        message = raw_data.get("message") or raw_data.get("edited_message")
        callback = raw_data.get("callback_query")

        if callback:
            return self._parse_callback(
                callback,
                update_id,
            )

        if message:
            return self._parse_message(
                message,
                update_id,
            )

        return VOXInboundMessage(
            message_id=str(update_id or ""),
            channel=self.CHANNEL,
            sender_id="unknown",
            content_type="event",
            raw_payload=raw_data,
        )

    def _parse_message(
        self,
        msg: dict,
        update_id: int | None,
    ) -> VOXInboundMessage:
        sender = msg.get("from", {})
        chat = msg.get("chat", {})

        text = msg.get("text", "")
        caption = msg.get("caption", "")

        content_type, text, media_url = self._classify_content(
            msg,
            text or caption,
        )

        return VOXInboundMessage(
            message_id=str(update_id or msg.get("message_id", "")),
            channel=self.CHANNEL,
            sender_id=str(
                chat.get(
                    "id",
                    sender.get("id", ""),
                )
            ),
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

    def _parse_callback(
        self,
        cb: dict,
        update_id: int | None,
    ) -> VOXInboundMessage:
        sender = cb.get("from", {})
        message = cb.get("message", {})
        chat = message.get("chat", {})

        return VOXInboundMessage(
            message_id=str(update_id or cb.get("id", "")),
            channel=self.CHANNEL,
            sender_id=str(
                chat.get(
                    "id",
                    sender.get("id", ""),
                )
            ),
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
    def _classify_content(
        msg: dict,
        fallback_text: str,
    ) -> tuple[str, str | None, str | None]:
        if msg.get("photo"):
            return "image", msg.get("caption"), None

        if msg.get("voice") or msg.get("audio"):
            return "audio", None, None

        if msg.get("video") or msg.get("document"):
            return "file", msg.get("caption"), None

        if msg.get("sticker"):
            return (
                "event",
                msg.get("sticker", {}).get("emoji"),
                None,
            )

        return "text", fallback_text, None

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_outbound(
        self,
        message: VOXOutboundMessage,
    ) -> bool:
        if not self._bot_token:
            logger.error("Telegram send failed: no TELEGRAM_BOT_TOKEN configured")
            return False

        try:
            client = await self._ensure_client()

            payload: dict[str, Any] = {
                "chat_id": message.recipient_id,
                "text": message.text,
            }

            if message.parse_mode and message.parse_mode.upper() != "PLAIN":
                payload["parse_mode"] = message.parse_mode

            reply_markup = message.metadata.get("reply_markup")

            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = await client.post(
                f"/bot{self._bot_token}/sendMessage",
                json=payload,
            )

            response.raise_for_status()

            return True

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Telegram send failed: %s",
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Inbound — getUpdates long-polling
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start long-polling when Telegram is configured.

        Long-polling only requires TELEGRAM_BOT_TOKEN. The webhook secret
        is unrelated to getUpdates and therefore remains optional.
        """
        if self._bot_token and self._dispatch is not None and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

            logger.info("TelegramAdapter long-polling started (getUpdates)")

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    updates = await self._get_updates()

                    for update in updates:
                        await self._handle_update(update)

                    await asyncio.sleep(0)

                except asyncio.CancelledError:
                    break

                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Telegram poll error (retry in %ss): %s",
                        _POLL_RETRY_DELAY,
                        exc,
                    )

                    await asyncio.sleep(_POLL_RETRY_DELAY)

        except asyncio.CancelledError:
            pass

    async def _get_updates(self) -> list[dict]:
        client = await self._ensure_client()

        response = await client.get(
            f"/bot{self._bot_token}/getUpdates",
            params={
                "offset": self._offset,
                "timeout": self._long_timeout,
            },
        )

        response.raise_for_status()

        updates = response.json().get("result", [])

        for update in updates:
            update_id = update.get("update_id", 0)

            if update_id >= self._offset:
                self._offset = update_id + 1

        return updates

    async def _handle_update(
        self,
        update: dict,
    ) -> None:
        try:
            inbound = self.parse_inbound(update)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Telegram update parse failed: %s",
                exc,
            )
            return

        if self._dispatch is not None:
            try:
                await self._dispatch(inbound)

            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Telegram dispatch failed: %s",
                    exc,
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_TELEGRAM_API,
                timeout=httpx.Timeout(self._client_timeout),
            )

        return self._client

    async def shutdown(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()

            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

            self._poll_task = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None
