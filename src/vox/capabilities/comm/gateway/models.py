"""Unified inbound/outbound messaging contracts for comm.gateway.

All inbound messages — regardless of originating channel — are normalised
into VOXInboundMessage before reaching agent roles. Outbound messages use
VOXOutboundMessage and are channel-adapted by the appropriate adapter.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class VOXInboundMessage(BaseModel):
    """Channel-agnostic inbound message contract."""

    message_id: str
    channel: str
    tenant_id: str = "default"
    sender_id: str
    sender_metadata: Dict[str, Any] = Field(default_factory=dict)
    content_type: str = "text"
    text: Optional[str] = None
    media_url: Optional[str] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VOXOutboundMessage(BaseModel):
    """Channel-agnostic outbound message contract."""

    channel: str
    recipient_id: str
    text: str
    parse_mode: str = "HTML"
    metadata: Dict[str, Any] = Field(default_factory=dict)
