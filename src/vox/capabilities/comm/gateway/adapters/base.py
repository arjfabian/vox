"""BaseAdapter — abstract interface for channel-specific inbound/outbound translation.

Every adapter implements the inbound/outbound contract:
  parse_inbound(raw)          — normalise a channel-native payload into VOXInboundMessage
  send_outbound(msg)          — translate a VOXOutboundMessage into a channel-native API call
  verify_request(request, body) — validate an inbound HTTP request's authenticity (HMAC,
                                  signature headers, etc.). ``body`` is the raw request
                                  body read before JSON parsing — required by channels
                                  that sign the payload bytes (e.g. WhatsApp/Meta).
  handle_verification(query)  — optional GET webhook subscription handshake (e.g. Meta's
                                ``hub.challenge`` echo). Returns the challenge to echo, or
                                ``None`` to reject the request with 403.

Adapters also declare their own HTTP route (``WEBHOOK_PATH``) so the shared IngressServer
registers channels generically — no server changes when a new channel is plugged in.
"""

from abc import ABC, abstractmethod
from typing import Any

from ..models import VOXInboundMessage, VOXOutboundMessage


class BaseAdapter(ABC):
    CHANNEL: str = ""
    # HTTP path served by the shared IngressServer for this channel's inbound
    # webhook. The server registers POST (and GET, for handshakes) generically.
    WEBHOOK_PATH: str = ""
    # Adapter-scoped configuration keys. Adapters no longer declare PARAMS
    # or SENSITIVE_PARAMS — the gateway's YAML contract is the single source
    # of truth. These stubs remain for backward compatibility with adapter
    # code that references ``cls.PARAMS`` or ``cls.SENSITIVE_PARAMS``.
    PARAMS: dict[str, list[Any]] = {}  # noqa: RUF012
    SENSITIVE_PARAMS: set[str] = set()  # noqa: RUF012

    @abstractmethod
    def parse_inbound(self, raw_data: dict) -> VOXInboundMessage: ...

    @abstractmethod
    async def send_outbound(self, message: VOXOutboundMessage) -> bool: ...

    @abstractmethod
    def verify_request(self, request: Any, body: bytes | None = None) -> bool:
        """Validate an inbound webhook request.

        ``body`` is the raw, unparsed request body — channels that sign the
        payload (HMAC over the exact bytes) must verify against it before the
        server attempts ``request.json()``.
        """

    def handle_verification(self, query: dict) -> str | None:
        """Optional GET webhook subscription handshake.

        Subclasses that require a provider verification step (e.g. Meta's
        ``hub.mode`` / ``hub.verify_token`` / ``hub.challenge``) override this
        and echo the challenge. Return ``None`` to reject the handshake.
        """
        return None

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        """Whether this channel should be mounted for the given config."""
        return True
