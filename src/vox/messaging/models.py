"""VOXMessage — the inter-agent envelope dataclass.

Conforms to the VOX Messaging Contract v1.0 envelope fields.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VOXMessage:
    """Universal message envelope for inter-agent communication.

    Every message exchanged between agents MUST use this structure.
    See ``vox.wiki/Messaging-Contract.md`` for the full specification.
    """

    message_id: str
    message_source: str
    emitted_at: str
    source: str
    target: str
    type: str
    details: dict[str, Any] = field(default_factory=dict)
    reply_to: str | None = None
