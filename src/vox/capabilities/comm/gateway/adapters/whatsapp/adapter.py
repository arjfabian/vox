"""WhatsApp Business Cloud API adapter for comm.gateway.

Bridges the Meta WhatsApp Cloud API in both directions:
  * Inbound via webhook (``entry[].changes[].value.messages[]``) normalised into
    VOXInboundMessage and forwarded to the caller-supplied dispatch callback.
  * Outbound via the Graph API ``POST /{version}/{phone_id}/messages`` endpoint
    with ``Authorization: Bearer <ACCESS_TOKEN>``.

Webhook verification uses Meta's ``hub.mode`` / ``hub.verify_token`` /
``hub.challenge`` handshake, and every inbound POST is HMAC-SHA256 signed via
``X-Hub-Signature-256``.

All keys are drawn from the workload's ``secrets.vault`` (injected as bound
capability secrets declared in the adapter's ``config.yml``: ``WHATSAPP_ACCESS_TOKEN``,
``WHATSAPP_APP_SECRET``, ``WHATSAPP_VERIFY_TOKEN``).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ...models import VOXInboundMessage, VOXOutboundMessage
from ..base import BaseAdapter

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com"


class WhatsAppAdapter(BaseAdapter):
    CHANNEL = "whatsapp"
    WEBHOOK_PATH = "/webhook/whatsapp"

    def __init__(
        self,
        config: dict,
        dispatch: Callable[[VOXInboundMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._access_token: str = config.get("WHATSAPP_ACCESS_TOKEN", "")
        self._phone_number_id: str = config.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self._app_secret: str = config.get("WHATSAPP_APP_SECRET", "")
        self._verify_token: str = config.get("WHATSAPP_VERIFY_TOKEN", "")
        self._api_version: str = config.get("WHATSAPP_API_VERSION", "v25.0")
        self._dispatch = dispatch
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        return bool(config.get("WHATSAPP_ACCESS_TOKEN")) and bool(
            config.get("WHATSAPP_PHONE_NUMBER_ID")
        )

    # --------------------------------------------------------------------------
    # Request verification — X-Hub-Signature-256 HMAC
    # --------------------------------------------------------------------------

    def verify_request(self, request: Any, body: bytes | None = None) -> bool:
        if not self._app_secret:
            return True

        sig = request.headers.get("X-Hub-Signature-256", "")
        if not sig.startswith("sha256="):
            return False

        expected = _hmac.new(
            self._app_secret.encode(),
            body or b"",
            hashlib.sha256,
        ).hexdigest()
        return _hmac.compare_digest(sig, f"sha256={expected}")

    # --------------------------------------------------------------------------
    # Webhook verification handshake — hub.challenge echo
    # --------------------------------------------------------------------------

    def handle_verification(self, query: dict) -> str | None:
        if query.get("hub.mode") == "subscribe" and _hmac.compare_digest(
            query.get("hub.verify_token", ""), self._verify_token
        ):
            return query.get("hub.challenge")
        return None

    # --------------------------------------------------------------------------
    # Inbound
    # --------------------------------------------------------------------------

    def parse_inbound(self, raw_data: dict) -> VOXInboundMessage:
        """Parse a WhatsApp Cloud API webhook payload into VOXInboundMessage.

        Handles both ``messages[]`` (user text/media) and ``statuses[]``
        (delivery/read receipts) update types.
        """
        try:
            value = raw_data["entry"][0]["changes"][0]["value"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid WhatsApp payload structure: {exc}") from exc

        # Status update (sent/delivered/read receipt) — surfaced as event.
        statuses = value.get("statuses")
        if statuses:
            return self._parse_status(statuses[0])

        messages = value.get("messages")
        if messages:
            return self._parse_message(messages[0], value)

        # Unknown shape — return a minimal event envelope.
        return VOXInboundMessage(
            message_id="",
            channel=self.CHANNEL,
            sender_id="",
            content_type="event",
            raw_payload=raw_data,
        )

    def _parse_message(self, msg: dict, value: dict) -> VOXInboundMessage:
        msg_type = msg.get("type", "text")
        sender_id = msg.get("from", "")
        message_id = msg.get("id", "")
        ts = msg.get("timestamp", "")

        # Resolve sender profile name from contacts[].
        contact_name = ""
        for contact in value.get("contacts", []):
            if contact.get("wa_id") == sender_id:
                contact_name = contact.get("profile", {}).get("name", "")
                break

        text, content_type = self._extract_text(msg, msg_type)

        return VOXInboundMessage(
            message_id=message_id,
            channel=self.CHANNEL,
            sender_id=sender_id,
            sender_metadata={
                "profile_name": contact_name,
                "phone_number_id": value.get("metadata", {}).get("phone_number_id", ""),
                "display_phone_number": value.get("metadata", {}).get(
                    "display_phone_number", ""
                ),
                "msg_type": msg_type,
                "timestamp": ts,
            },
            content_type=content_type,
            text=text,
            raw_payload=msg,
        )

    @staticmethod
    def _extract_text(msg: dict, msg_type: str) -> tuple[str | None, str]:
        """Return (text, content_type) for a WhatsApp message."""
        if msg_type == "text":
            return msg.get("text", {}).get("body"), "text"
        if msg_type == "image":
            return msg.get("image", {}).get("caption"), "image"
        if msg_type == "audio":
            return None, "audio"
        if msg_type == "document":
            return msg.get("document", {}).get("caption"), "document"
        if msg_type == "video":
            return msg.get("video", {}).get("caption"), "video"
        if msg_type == "sticker":
            return None, "event"
        return None, "event"

    def _parse_status(self, status: dict) -> VOXInboundMessage:
        return VOXInboundMessage(
            message_id=status.get("id", ""),
            channel=self.CHANNEL,
            sender_id=status.get("recipient_id", ""),
            sender_metadata={
                "status": status.get("status", ""),
                "timestamp": status.get("timestamp", ""),
            },
            content_type="event",
            text=status.get("status"),
            raw_payload=status,
        )

    # --------------------------------------------------------------------------
    # Outbound
    # --------------------------------------------------------------------------

    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        if not self._access_token or not self._phone_number_id:
            logger.error(
                "WhatsApp send failed: no WHATSAPP_ACCESS_TOKEN or "
                "WHATSAPP_PHONE_NUMBER_ID configured"
            )
            return False

        try:
            client = await self._ensure_client()
            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": message.recipient_id,
                "type": "text",
                "text": {"body": message.text},
            }

            resp = await client.post(
                f"{self._api_version}/{self._phone_number_id}/messages",
                json=payload,
            )
            resp.raise_for_status()
            return True
        except Exception:
            logger.exception("WhatsApp send failed")
            return False

    # --------------------------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_GRAPH_BASE,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
