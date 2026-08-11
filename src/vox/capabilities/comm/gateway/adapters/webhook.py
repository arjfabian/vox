"""Generic HTTP Webhook adapter for comm.gateway.

Accepts arbitrary JSON POST payloads and normalises them into
VOXInboundMessage. Outbound messages are logged only (the generic
adapter has no target API).

Customise parse_inbound() for your webhook vendor's payload shape.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import VOXInboundMessage, VOXOutboundMessage
from .base import BaseAdapter

logger = logging.getLogger(__name__)


class WebhookAdapter(BaseAdapter):
    CHANNEL = "webhook"
    WEBHOOK_PATH = "/webhook/generic"

    PARAMS: dict[str, list[Any]] = {  # noqa: RUF012
        "GATEWAY_WEBHOOK_SECRET": [
            "Shared secret for generic webhook validation",
            "",
        ],
    }
    SENSITIVE_PARAMS: set[str] = {  # noqa: RUF012
        "GATEWAY_WEBHOOK_SECRET",
    }

    def __init__(self, config: dict, dispatch: Any | None = None) -> None:
        self._webhook_secret: str = config.get("GATEWAY_WEBHOOK_SECRET", "")

    # ------------------------------------------------------------------
    # Request verification
    # ------------------------------------------------------------------

    def verify_request(self, request: Any, body: bytes | None = None) -> bool:
        if not self._webhook_secret:
            return True
        token = request.headers.get("X-Webhook-Secret", "")
        import hmac as _hmac

        return _hmac.compare_digest(token, self._webhook_secret)

    def parse_inbound(self, raw_data: dict) -> VOXInboundMessage:
        """Parse a generic JSON webhook payload.

        Default strategy: treat ``sender_id`` as ``sender.id`` or
        ``from.id`` or ``"anonymous"``, and ``text`` as ``text`` or
        ``message`` or ``body``. Override this method or provide a
        custom adapter subclass for vendor-specific payloads.
        """
        sender_id = (
            raw_data.get("sender", {}).get("id")
            or raw_data.get("from", {}).get("id")
            or raw_data.get("user_id")
            or raw_data.get("sender_id")
            or "anonymous"
        )

        text = (
            raw_data.get("text")
            or raw_data.get("message")
            or raw_data.get("body")
            or raw_data.get("content")
            or ""
        )

        content_type = self._detect_content_type(raw_data)

        return VOXInboundMessage(
            message_id=raw_data.get("id") or raw_data.get("event_id", ""),
            channel=self.CHANNEL,
            sender_id=str(sender_id),
            sender_metadata=self._extract_metadata(raw_data),
            content_type=content_type,
            text=text if content_type == "text" else None,
            media_url=raw_data.get("media_url") or raw_data.get("image_url"),
            raw_payload=raw_data,
        )

    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        """Generic outbound: log only. Override in a vendor-specific subclass."""
        logger.info(
            "WebhookAdapter outbound to %s: %s",
            message.recipient_id,
            message.text[:120],
        )
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_content_type(data: dict) -> str:
        if data.get("media_url") or data.get("image_url") or data.get("file_url"):
            return "image"
        if data.get("event_type") or data.get("type"):
            return "event"
        return "text"

    @staticmethod
    def _extract_metadata(data: dict) -> dict[str, Any]:
        excluded = {
            "id",
            "text",
            "message",
            "body",
            "content",
            "sender_id",
            "from",
            "sender",
            "user_id",
            "media_url",
            "image_url",
            "file_url",
            "event_type",
            "type",
            "event_id",
        }
        return {k: v for k, v in data.items() if k not in excluded}
