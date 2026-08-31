"""VOXMessage — the inter-workload envelope model.

Conforms to the VOX Messaging Contract v1.0 envelope fields.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class VOXMessage(BaseModel):
    """Universal message envelope for inter-workload communication.

    Every message exchanged between workloads MUST use this structure.
    See ``vox.wiki/Messaging-Contract.md`` for the full specification.
    """

    message_id: UUID = Field(
        description="Globally unique UUID for the message"
    )
    message_source: str = Field(
        description="Source of truth (e.g., email address, interface name)"
    )
    emitted_at: str = Field(
        description=(
            "ISO-8601 datetime with timezone when the message "
            "was emitted"
        )
    )
    source: UUID = Field(description="UUID of the sender workload")
    target: UUID = Field(description="UUID of the recipient workload")
    type: str = Field(
        description=(
            "Semantic intent of the message (e.g., job_detected, "
            "data_request)"
        )
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific variable payload",
    )
    reply_to: UUID | None = Field(
        default=None,
        description=(
            "UUID of the message this message is replying to"
        ),
    )

    model_config = {
        "frozen": True,  # enforces immutability
        "populate_by_name": True,
    }

    @field_validator("emitted_at")
    @classmethod
    def validate_iso8601_datetime(cls, v: str) -> str:
        """Ensure emitted_at matches a parseable ISO-8601 format."""
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"emitted_at '{v}' must be a valid ISO-8601 format."
            )
        return v
