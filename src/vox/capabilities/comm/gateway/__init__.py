"""comm.gateway — multi-channel communication gateway capability.

Provides inbound webhook ingestion and outbound message delivery across
configured channels.  All inbound traffic is normalised into
VOXInboundMessage before dispatching to the orchestrator.
"""

from .models import VOXInboundMessage, VOXOutboundMessage
from .capability import CommGatewayCapability
from .server import IngressServer
from .adapters import BaseAdapter, TelegramAdapter, WebhookAdapter

__all__ = [
    "VOXInboundMessage",
    "VOXOutboundMessage",
    "CommGatewayCapability",
    "IngressServer",
    "BaseAdapter",
    "TelegramAdapter",
    "WebhookAdapter",
]
