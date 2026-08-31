"""comm.gateway — multi-channel communication gateway capability.

Provides inbound webhook ingestion and outbound message delivery across
configured channels. All inbound traffic is normalised into VOXInboundMessage
before dispatching to the orchestrator.
"""

from .adapters import (
    BaseAdapter,
    TelegramAdapter,
    WebhookAdapter,
    WhatsAppAdapter,
)
from .capability import CommGatewayCapability
from .models import VOXInboundMessage, VOXOutboundMessage
from .server import IngressServer

__all__ = [
    "BaseAdapter",
    "CommGatewayCapability",
    "IngressServer",
    "TelegramAdapter",
    "VOXInboundMessage",
    "VOXOutboundMessage",
    "WebhookAdapter",
    "WhatsAppAdapter",
]
