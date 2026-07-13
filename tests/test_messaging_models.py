import unittest

from vox.messaging.models import VOXMessage


class TestVOXMessage(unittest.TestCase):

    def test_minimal_construction(self):
        msg = VOXMessage(
            message_id="msg-1",
            message_source="agent_a",
            emitted_at="2025-01-01T00:00:00Z",
            source="agent_a",
            target="agent_b",
            type="command",
        )
        self.assertEqual(msg.message_id, "msg-1")
        self.assertEqual(msg.type, "command")

    def test_full_construction(self):
        msg = VOXMessage(
            message_id="msg-2",
            message_source="agent_a",
            emitted_at="2025-06-15T12:30:00Z",
            source="agent_a",
            target="agent_b",
            type="event",
            details={"key": "value"},
            reply_to="msg-1",
        )
        self.assertEqual(msg.reply_to, "msg-1")
        self.assertEqual(msg.details, {"key": "value"})

    def test_details_defaults_to_empty_dict(self):
        msg = VOXMessage(
            message_id="msg-3",
            message_source="agent_a",
            emitted_at="2025-01-01T00:00:00Z",
            source="agent_a",
            target="agent_b",
            type="ping",
        )
        self.assertEqual(msg.details, {})

    def test_reply_to_defaults_to_none(self):
        msg = VOXMessage(
            message_id="msg-4",
            message_source="agent_a",
            emitted_at="2025-01-01T00:00:00Z",
            source="agent_a",
            target="agent_b",
            type="ping",
        )
        self.assertIsNone(msg.reply_to)

    def test_message_is_mutable_dataclass(self):
        msg = VOXMessage(
            message_id="msg-5",
            message_source="agent_a",
            emitted_at="2025-01-01T00:00:00Z",
            source="agent_a",
            target="agent_b",
            type="ping",
        )
        msg.details["extra"] = "data"
        self.assertEqual(msg.details["extra"], "data")
