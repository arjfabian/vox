"""BaseAdapter — abstract interface for channel-specific inbound/outbound translation.

Every adapter implements three operations:
  parse_inbound(raw)      — normalise a channel-native payload into VOXInboundMessage
  send_outbound(msg)      — translate a VOXOutboundMessage into a channel-native API call
  verify_request(request) — validate an inbound HTTP request's authenticity (HMAC, headers, etc.)
"""

from abc import ABC, abstractmethod
from typing import Any

from ..models import VOXInboundMessage, VOXOutboundMessage


class BaseAdapter(ABC):

    CHANNEL: str = ""

    @abstractmethod
    def parse_inbound(self, raw_data: dict) -> VOXInboundMessage:
        ...

    @abstractmethod
    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        ...

    @abstractmethod
    def verify_request(self, request: Any) -> bool:
        ...
