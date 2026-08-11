"""Tests for comm.gateway — Telegram adapter long-polling and parsing."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from vox.capabilities.comm.gateway.adapters.telegram import TelegramAdapter

_TELEGRAM_API = "https://api.telegram.org"


def _adapter_with_transport(handler, token="123:ABC", dispatch=None):
    adapter = TelegramAdapter(
        {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_WEBHOOK_SECRET": ""},
        dispatch=dispatch,
    )
    adapter._client = httpx.AsyncClient(
        base_url=_TELEGRAM_API,
        transport=httpx.MockTransport(handler),
    )
    return adapter


class TestTelegramAdapterPolling(unittest.TestCase):
    def test_poll_dispatches_parsed_updates_and_tracks_offset(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "/getUpdates" in str(request.url):
                calls["n"] += 1
                if calls["n"] == 1:
                    return httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": [
                                {
                                    "update_id": 1,
                                    "message": {
                                        "message_id": 10,
                                        "from": {"id": 42, "username": "jose"},
                                        "chat": {"id": 42, "type": "private"},
                                        "text": "/hola",
                                    },
                                },
                                {
                                    "update_id": 2,
                                    "message": {
                                        "message_id": 11,
                                        "from": {"id": 42},
                                        "chat": {"id": 42},
                                        "text": "hola",
                                    },
                                },
                            ],
                        },
                    )
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(404)

        dispatch = AsyncMock()
        adapter = _adapter_with_transport(handler, dispatch=dispatch)

        async def run():
            await adapter.start()
            await asyncio.sleep(0.2)
            await adapter.shutdown()

        asyncio.run(run())

        self.assertEqual(adapter._offset, 3)
        self.assertEqual(dispatch.await_count, 2)
        texts = [call.args[0].text for call in dispatch.await_args_list]
        self.assertEqual(texts, ["/hola", "hola"])

    def test_parse_inbound_text_message(self):
        adapter = _adapter_with_transport(lambda r: httpx.Response(404))
        inbound = adapter.parse_inbound(
            {
                "update_id": 7,
                "message": {
                    "message_id": 99,
                    "from": {"id": 42, "username": "jose"},
                    "chat": {"id": 42, "type": "private"},
                    "text": "/visit_website https://example.com",
                },
            }
        )
        self.assertEqual(inbound.channel, "telegram")
        self.assertEqual(inbound.sender_id, "42")
        self.assertEqual(inbound.text, "/visit_website https://example.com")

    def test_parse_inbound_callback_query(self):
        adapter = _adapter_with_transport(lambda r: httpx.Response(404))
        inbound = adapter.parse_inbound(
            {
                "update_id": 8,
                "callback_query": {
                    "id": "cb_1",
                    "from": {"id": 42},
                    "message": {"chat": {"id": 42, "type": "private"}},
                    "data": "approve",
                },
            }
        )
        self.assertEqual(inbound.content_type, "event")
        self.assertEqual(inbound.text, "approve")

    def test_parse_inbound_unknown_update_type(self):
        adapter = _adapter_with_transport(lambda r: httpx.Response(404))
        inbound = adapter.parse_inbound({"update_id": 9})
        self.assertEqual(inbound.content_type, "event")
        self.assertEqual(inbound.sender_id, "unknown")


class TestTelegramAdapterWebhookVerification(unittest.TestCase):
    def test_verify_request_without_secret_allows_all(self):
        adapter = TelegramAdapter({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_WEBHOOK_SECRET": ""})
        self.assertTrue(adapter.verify_request(object()))

    def test_verify_request_with_secret_rejects_mismatch(self):
        adapter = TelegramAdapter(
            {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_WEBHOOK_SECRET": "s3cret"}
        )
        req = httpx.Request("POST", "https://example.com")
        self.assertFalse(adapter.verify_request(req))
        req.headers["X-Telegram-Bot-Api-Secret-Token"] = "s3cret"
        self.assertTrue(adapter.verify_request(req))


class TestTelegramAdapterClientTimeout(unittest.TestCase):
    def test_client_timeout_accommodates_long_poll(self):
        """The HTTP client read timeout must exceed the getUpdates long-poll
        timeout, otherwise quiet polls die with an empty ReadTimeout."""
        adapter = TelegramAdapter(
            {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_LONG_TIMEOUT": 25}
        )
        self.assertEqual(adapter._long_timeout, 25)
        self.assertGreater(adapter._client_timeout, adapter._long_timeout)

        async def run():
            client = await adapter._ensure_client()
            return client.timeout.read

        read_timeout = asyncio.run(run())
        self.assertEqual(read_timeout, adapter._client_timeout)

    def test_configurable_long_timeout(self):
        adapter = TelegramAdapter(
            {"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_LONG_TIMEOUT": "40"}
        )
        self.assertEqual(adapter._long_timeout, 40)
        self.assertEqual(adapter._client_timeout, 40 + 5)

    def test_default_long_timeout(self):
        adapter = TelegramAdapter({"TELEGRAM_BOT_TOKEN": "x"})
        self.assertEqual(adapter._long_timeout, 25)

    def test_invalid_long_timeout_rejected(self):
        with self.assertRaises(ValueError):
            TelegramAdapter({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_LONG_TIMEOUT": 0})

    def test_clashing_timeouts_rejected(self):
        """Guard against the client timeout no longer exceeding the poll
        timeout (e.g. if the derivation buffer is ever changed or removed)."""
        import vox.capabilities.comm.gateway.adapters.telegram as tg

        with (
            patch.object(tg, "_POLL_TIMEOUT_BUFFER", 0),
            self.assertRaises(ValueError),
        ):
            TelegramAdapter({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_LONG_TIMEOUT": 25})


if __name__ == "__main__":
    unittest.main()
