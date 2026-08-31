"""BaseAdapter — abstract interface for inbound/outbound translation.

Every adapter implements the inbound/outbound contract:

  parse_inbound(raw)
      Normalize a channel-native payload into VOXInboundMessage.
  send_outbound(msg)
      Translate a VOXOutboundMessage into a channel-native API call.
  verify_request(request, body)
      Validate an inbound HTTP request's authenticity (HMAC, signature headers,
      etc.). ``body`` is the raw request body read before JSON parsing —
      required by channels that sign the payload bytes (e.g. WhatsApp/Meta).
  handle_verification(query)
      Optional GET webhook subscription handshake (e.g. Meta's ``hub.challenge``
      echo). Returns the challenge to echo, or ``None`` to reject the request
      with 403.

Adapters also declare their own HTTP route (``WEBHOOK_PATH``) so the shared
IngressServer registers channels generically — no server changes when a new
channel is plugged in.
"""

from abc import ABC, abstractmethod
from typing import Any

from ..models import VOXInboundMessage, VOXOutboundMessage


class BaseAdapter(ABC):
    CHANNEL: str = ""
    # HTTP path served by the shared IngressServer for this channel's inbound
    # webhook. The server registers POST (and GET, for handshakes) generically.
    WEBHOOK_PATH: str = ""
    # Adapter-scoped configuration lives in the adapter's ``config.yml``,
    # aggregated into the gateway's single CapabilityContract. Adapters must
    # not redeclare ``PARAMS`` or ``SENSITIVE_PARAMS`` in Python.

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
