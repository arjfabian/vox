"""Tests for comm.gateway — Telegram adapter long-polling and parsing."""

import asyncio
import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from aiohttp import web

from vox.capabilities.comm.gateway.adapters.base import BaseAdapter
from vox.capabilities.comm.gateway.adapters.telegram import TelegramAdapter
from vox.capabilities.comm.gateway.adapters.webhook import WebhookAdapter
from vox.capabilities.comm.gateway.models import VOXInboundMessage
from vox.capabilities.comm.gateway.server import IngressServer

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


class TestCommGatewayAdapterParams(unittest.TestCase):
    """Telegram params live in the adapter, and the gateway aggregates them."""

    def test_gateway_aggregates_adapter_params(self):
        from vox.capabilities.comm.gateway import CommGatewayCapability
        from vox.capabilities.comm.gateway.adapters import ADAPTER_REGISTRY

        for adapter in ADAPTER_REGISTRY.values():
            for param, (desc, default) in adapter.PARAMS.items():
                self.assertIn(param, CommGatewayCapability.PARAMS)
                self.assertEqual(CommGatewayCapability.PARAMS[param], [desc, default])
            self.assertLessEqual(
                adapter.SENSITIVE_PARAMS, CommGatewayCapability.SENSITIVE_PARAMS
            )

        self.assertIn("TELEGRAM_BOT_TOKEN", CommGatewayCapability.PARAMS)
        self.assertIn("TELEGRAM_BOT_TOKEN", CommGatewayCapability.SENSITIVE_PARAMS)
        self.assertIn("GATEWAY_WEBHOOK_SECRET", CommGatewayCapability.PARAMS)

    def test_adapter_param_specs_are_nested_per_channel(self):
        from vox.capabilities.comm.gateway import CommGatewayCapability

        specs = CommGatewayCapability.adapter_param_specs()
        self.assertEqual(
            set(specs), {"telegram", "webhook"}
        )
        self.assertIn("TELEGRAM_BOT_TOKEN", specs["telegram"])
        self.assertIn("GATEWAY_WEBHOOK_SECRET", specs["webhook"])

    def test_is_configured(self):
        from vox.capabilities.comm.gateway.adapters.telegram import TelegramAdapter
        from vox.capabilities.comm.gateway.adapters.webhook import WebhookAdapter

        self.assertFalse(TelegramAdapter.is_configured({"TELEGRAM_BOT_TOKEN": ""}))
        self.assertTrue(
            TelegramAdapter.is_configured({"TELEGRAM_BOT_TOKEN": "123:ABC"})
        )
        self.assertTrue(WebhookAdapter.is_configured({}))


class _BodySigningAdapter(BaseAdapter):
    """Minimal adapter that signs/verifies the raw request body (Meta-style)."""

    CHANNEL = "signing"
    WEBHOOK_PATH = "/webhook/signing"
    SECRET = b"test-app-secret"

    def __init__(self, config=None, dispatch=None):
        self._dispatch = dispatch

    def parse_inbound(self, raw_data):
        return VOXInboundMessage(
            message_id="1", channel=self.CHANNEL, sender_id="x", text="hi"
        )

    async def send_outbound(self, message):
        return True

    def verify_request(self, request, body=None):
        sig = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            self.SECRET, body or b"", hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)


class _HandshakeAdapter(_BodySigningAdapter):
    """Adds a provider GET verification handshake (Meta hub.challenge echo)."""

    CHANNEL = "handshake"
    WEBHOOK_PATH = "/webhook/handshake"

    def handle_verification(self, query):
        if (
            query.get("hub.mode") == "subscribe"
            and query.get("hub.verify_token") == "verify-token"
        ):
            return query.get("hub.challenge")
        return None


class TestIngressServerGenericRoutes(unittest.TestCase):
    def test_routes_registered_from_adapter_webhook_paths(self):
        adapters = {
            "telegram": TelegramAdapter({"TELEGRAM_BOT_TOKEN": "x"}),
            "webhook": WebhookAdapter({}),
            "handshake": _HandshakeAdapter(),
        }
        server = IngressServer(adapters=adapters, dispatch=AsyncMock())
        server._app = web.Application()
        server._register_routes()

        pairs = {
            (route.method, route.resource.canonical)
            for route in server._app.router.routes()
            if route.method in {"GET", "POST"}
        }
        expected = {
            ("POST", "/webhook/telegram"),
            ("GET", "/webhook/telegram"),
            ("POST", "/webhook/generic"),
            ("GET", "/webhook/generic"),
            ("POST", "/webhook/handshake"),
            ("GET", "/webhook/handshake"),
            ("GET", "/health"),
        }
        self.assertEqual(pairs, expected)

    def test_get_handshake_echoes_challenge(self):
        from aiohttp.test_utils import TestClient, TestServer

        async def run():
            server = IngressServer(adapters={"handshake": _HandshakeAdapter()}, dispatch=AsyncMock())
            server._app = web.Application()
            server._register_routes()
            async with TestServer(server._app) as ts, TestClient(ts) as client:
                ok = await client.get(
                    "/webhook/handshake",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": "verify-token",
                        "hub.challenge": "1158201444",
                    },
                )
                self.assertEqual(ok.status, 200)
                self.assertEqual(await ok.text(), "1158201444")

                bad = await client.get(
                    "/webhook/handshake",
                    params={
                        "hub.mode": "subscribe",
                        "hub.verify_token": "wrong-token",
                        "hub.challenge": "1158201444",
                    },
                )
                self.assertEqual(bad.status, 403)

        asyncio.run(run())

    def test_post_verifies_signature_against_raw_body(self):
        from aiohttp.test_utils import TestClient, TestServer

        dispatch = AsyncMock()
        adapter = _BodySigningAdapter(dispatch=dispatch)
        payload = b'{"hello": "world"}'
        sig = "sha256=" + hmac.new(
            adapter.SECRET, payload, hashlib.sha256
        ).hexdigest()

        async def run():
            server = IngressServer(adapters={"signing": adapter}, dispatch=dispatch)
            server._app = web.Application()
            server._register_routes()
            async with TestServer(server._app) as ts, TestClient(ts) as client:
                ok = await client.post(
                    "/webhook/signing",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": sig,
                    },
                )
                self.assertEqual(ok.status, 200)
                self.assertEqual(dispatch.await_count, 1)
                self.assertEqual(dispatch.await_args.args[0].channel, "signing")

                forged = await client.post(
                    "/webhook/signing",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": "sha256=deadbeef",
                    },
                )
                self.assertEqual(forged.status, 403)
                self.assertEqual(dispatch.await_count, 1)

        asyncio.run(run())

    def test_missing_webhook_path_adapter_gets_no_route(self):
        adapter = _BodySigningAdapter()
        adapter.WEBHOOK_PATH = ""
        server = IngressServer(adapters={"signing": adapter}, dispatch=AsyncMock())
        server._app = web.Application()
        server._register_routes()

        paths = {
            route.resource.canonical
            for route in server._app.router.routes()
            if route.method in {"GET", "POST"}
        }
        self.assertNotIn("/webhook/signing", paths)
        self.assertIn("/health", paths)


if __name__ == "__main__":
    unittest.main()
