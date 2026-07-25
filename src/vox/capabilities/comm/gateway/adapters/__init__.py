"""comm.gateway.adapters — channel-specific inbound/outbound translators."""

from .base import BaseAdapter
from .telegram import TelegramAdapter
from .webhook import WebhookAdapter

__all__ = [
    "BaseAdapter",
    "TelegramAdapter",
    "WebhookAdapter",
]
