import unittest

from vox.messaging.models import VOXMessage


class TestVOXMessage(unittest.TestCase):

    def test_minimal_construction(self):
        msg = VOXMessage(
            message_id="00000000-0000-4000-8000-000000000001",
            message_source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            emitted_at="2025-01-01T00:00:00Z",
            source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            target="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            type="command",
        )
        self.assertEqual(str(msg.message_id), "00000000-0000-4000-8000-000000000001")
        self.assertEqual(msg.type, "command")

    def test_full_construction(self):
        msg = VOXMessage(
            message_id="00000000-0000-4000-8000-000000000002",
            message_source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            emitted_at="2025-06-15T12:30:00Z",
            source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            target="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            type="event",
            details={"key": "value"},
            reply_to="00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(str(msg.reply_to), "00000000-0000-4000-8000-000000000001")
        self.assertEqual(msg.details, {"key": "value"})

    def test_details_defaults_to_empty_dict(self):
        msg = VOXMessage(
            message_id="00000000-0000-4000-8000-000000000003",
            message_source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            emitted_at="2025-01-01T00:00:00Z",
            source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            target="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            type="ping",
        )
        self.assertEqual(msg.details, {})

    def test_reply_to_defaults_to_none(self):
        msg = VOXMessage(
            message_id="00000000-0000-4000-8000-000000000004",
            message_source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            emitted_at="2025-01-01T00:00:00Z",
            source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            target="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            type="ping",
        )
        self.assertIsNone(msg.reply_to)

    def test_message_is_mutable_dataclass(self):
        msg = VOXMessage(
            message_id="00000000-0000-4000-8000-000000000005",
            message_source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            emitted_at="2025-01-01T00:00:00Z",
            source="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
            target="bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb",
            type="ping",
        )
        msg.details["extra"] = "data"
        self.assertEqual(msg.details["extra"], "data")
