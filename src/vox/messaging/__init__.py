"""VOX inter-workload messaging.

Message types, envelope contract, and delivery primitives
for the VOX delegation protocol.
"""

from vox.messaging.models import VOXMessage

MSG_COMMAND_REQUEST = "command_request"
MSG_ACKNOWLEDGED = "message_acknowledged"
MSG_COMMAND_RESULT = "command_result"
MSG_COMMAND_ERROR = "command_error"

__all__ = [
    "MSG_ACKNOWLEDGED",
    "MSG_COMMAND_ERROR",
    "MSG_COMMAND_REQUEST",
    "MSG_COMMAND_RESULT",
    "VOXMessage",
]
