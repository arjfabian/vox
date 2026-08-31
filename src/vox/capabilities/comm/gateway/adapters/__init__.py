"""comm.gateway.adapters — channel-specific inbound/outbound translators."""

from .base import BaseAdapter
from .telegram import TelegramAdapter
from .webhook import WebhookAdapter
from .whatsapp import WhatsAppAdapter

# Channel registry. The gateway capability instantiates every registered
# adapter; channel-specific configuration lives in each adapter's ``config.yml``,
# aggregated into the gateway's CapabilityContract.
ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    cls.CHANNEL: cls for cls in (TelegramAdapter, WebhookAdapter, WhatsAppAdapter)
}

__all__ = [
    "ADAPTER_REGISTRY",
    "BaseAdapter",
    "TelegramAdapter",
    "WebhookAdapter",
    "WhatsAppAdapter",
]
