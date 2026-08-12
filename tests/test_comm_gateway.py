"""Tests for comm.gateway — Telegram adapter long-polling and parsing."""

import asyncio
import hashlib
import hmac
import json
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from aiohttp import web

from vox.capabilities.comm.gateway.adapters.base import BaseAdapter
from vox.capabilities.comm.gateway.adapters.telegram import TelegramAdapter
from vox.capabilities.comm.gateway.adapters.webhook import WebhookAdapter
from vox.capabilities.comm.gateway.adapters.whatsapp import WhatsAppAdapter
from vox.capabilities.comm.gateway.models import VOXInboundMessage, VOXOutboundMessage
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
            set(specs), {"telegram", "webhook", "whatsapp"}
        )
        self.assertIn("TELEGRAM_BOT_TOKEN", specs["telegram"])
        self.assertIn("GATEWAY_WEBHOOK_SECRET", specs["webhook"])
        self.assertIn("WHATSAPP_ACCESS_TOKEN", specs["whatsapp"])
        self.assertIn("WHATSAPP_PHONE_NUMBER_ID", specs["whatsapp"])

    def test_is_configured(self):
        from vox.capabilities.comm.gateway.adapters.telegram import TelegramAdapter
        from vox.capabilities.comm.gateway.adapters.webhook import WebhookAdapter

        self.assertFalse(TelegramAdapter.is_configured({"TELEGRAM_BOT_TOKEN": ""}))
        self.assertTrue(
            TelegramAdapter.is_configured({"TELEGRAM_BOT_TOKEN": "123:ABC"})
        )
        self.assertTrue(WebhookAdapter.is_configured({}))
        self.assertFalse(WhatsAppAdapter.is_configured({}))
        self.assertFalse(
            WhatsAppAdapter.is_configured({"WHATSAPP_PHONE_NUMBER_ID": "123"})
        )
        self.assertFalse(
            WhatsAppAdapter.is_configured({"WHATSAPP_ACCESS_TOKEN": "EAAG"})
        )
        self.assertTrue(
            WhatsAppAdapter.is_configured(
                {"WHATSAPP_ACCESS_TOKEN": "EAAG", "WHATSAPP_PHONE_NUMBER_ID": "123"}
            )
        )


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


_APP_SECRET = "my-test-app-secret"
_ACCESS_TOKEN = "EAAG_test_token"
_PHONE_NUMBER_ID = "1234567890"
_VERIFY_TOKEN = "my-verify-token"


class TestWhatsAppAdapter(unittest.TestCase):
    def _adapter(self, **overrides):

        config = {
            "WHATSAPP_ACCESS_TOKEN": _ACCESS_TOKEN,
            "WHATSAPP_PHONE_NUMBER_ID": _PHONE_NUMBER_ID,
            "WHATSAPP_APP_SECRET": _APP_SECRET,
            "WHATSAPP_VERIFY_TOKEN": _VERIFY_TOKEN,
            "WHATSAPP_API_VERSION": "v25.0",
        }
        config.update(overrides)
        return WhatsAppAdapter(config)

    # ---- parse_inbound ---------------------------------------------------

    def test_parse_inbound_text_message(self):
        adapter = self._adapter()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+1 555 078 3881",
                                    "phone_number_id": _PHONE_NUMBER_ID,
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "John Doe"},
                                        "wa_id": "5511999999999",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.ABGGN1XX102",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "Hello there"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        inbound = adapter.parse_inbound(payload)

        self.assertEqual(inbound.channel, "whatsapp")
        self.assertEqual(inbound.sender_id, "5511999999999")
        self.assertEqual(inbound.text, "Hello there")
        self.assertEqual(inbound.content_type, "text")
        self.assertEqual(inbound.message_id, "wamid.ABGGN1XX102")
        self.assertEqual(inbound.sender_metadata["profile_name"], "John Doe")
        self.assertEqual(inbound.sender_metadata["phone_number_id"], _PHONE_NUMBER_ID)
        self.assertEqual(inbound.sender_metadata["msg_type"], "text")

    def test_parse_inbound_status_update(self):
        adapter = self._adapter()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "statuses": [
                                    {
                                        "id": "wamid.ABGGN1XX203",
                                        "status": "delivered",
                                        "timestamp": "1700000001",
                                        "recipient_id": "5511888888888",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
        }
        inbound = adapter.parse_inbound(payload)

        self.assertEqual(inbound.channel, "whatsapp")
        self.assertEqual(inbound.sender_id, "5511888888888")
        self.assertEqual(inbound.content_type, "event")
        self.assertEqual(inbound.text, "delivered")
        self.assertEqual(inbound.message_id, "wamid.ABGGN1XX203")
        self.assertEqual(inbound.sender_metadata["status"], "delivered")

    def test_parse_inbound_image_message(self):
        adapter = self._adapter()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "contacts": [
                                    {"profile": {"name": "Jane"}, "wa_id": "5511777777777"}
                                ],
                                "messages": [
                                    {
                                        "from": "5511777777777",
                                        "id": "wamid.IMG001",
                                        "timestamp": "1700000002",
                                        "type": "image",
                                        "image": {
                                            "caption": "Check this out",
                                            "mime_type": "image/jpeg",
                                            "sha256": "abc123",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        inbound = adapter.parse_inbound(payload)

        self.assertEqual(inbound.content_type, "image")
        self.assertEqual(inbound.text, "Check this out")

    def test_parse_inbound_malformed_payload_raises(self):
        adapter = self._adapter()
        with self.assertRaises(ValueError):
            adapter.parse_inbound({})
        with self.assertRaises(ValueError):
            adapter.parse_inbound({"entry": []})

    def test_parse_inbound_unknown_type_returns_event(self):
        adapter = self._adapter()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messages": [
                                    {
                                        "from": "5511666666666",
                                        "id": "wamid.UNKNOWN",
                                        "timestamp": "1700000003",
                                        "type": "reaction",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        inbound = adapter.parse_inbound(payload)
        self.assertEqual(inbound.content_type, "event")

    # ---- verify_request (X-Hub-Signature-256) ---------------------------

    def test_verify_request_no_secret_allows_all(self):
        adapter = self._adapter(WHATSAPP_APP_SECRET="")
        self.assertTrue(adapter.verify_request(object()))

    def test_verify_request_valid_signature(self):
        adapter = self._adapter()
        import httpx as _httpx

        body = b'{"entry":[{"changes":[{"value":{"messages":[]}}]}]}'
        sig = "sha256=" + hmac.new(
            _APP_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        req = _httpx.Request("POST", "https://example.com", content=body)
        req.headers["X-Hub-Signature-256"] = sig
        self.assertTrue(adapter.verify_request(req, body))

    def test_verify_request_rejects_wrong_signature(self):
        adapter = self._adapter()
        import httpx as _httpx

        body = b'{"hello":"world"}'
        req = _httpx.Request("POST", "https://example.com", content=body)
        req.headers["X-Hub-Signature-256"] = "sha256=deadbeef"
        self.assertFalse(adapter.verify_request(req, body))

    def test_verify_request_rejects_missing_header(self):
        adapter = self._adapter()
        import httpx as _httpx

        req = _httpx.Request("POST", "https://example.com", content=b"{}")
        self.assertFalse(adapter.verify_request(req, b"{}"))

    def test_verify_request_rejects_non_sha256_prefix(self):
        adapter = self._adapter()
        import httpx as _httpx

        req = _httpx.Request("POST", "https://example.com", content=b"{}")
        req.headers["X-Hub-Signature-256"] = "md5=abc"
        self.assertFalse(adapter.verify_request(req, b"{}"))

    # ---- handle_verification (hub.challenge) -----------------------------

    def test_handle_verification_echoes_challenge(self):
        adapter = self._adapter()
        result = adapter.handle_verification(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": _VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            }
        )
        self.assertEqual(result, "1158201444")

    def test_handle_verification_rejects_wrong_token(self):
        adapter = self._adapter()
        result = adapter.handle_verification(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "1158201444",
            }
        )
        self.assertIsNone(result)

    def test_handle_verification_rejects_wrong_mode(self):
        adapter = self._adapter()
        result = adapter.handle_verification(
            {
                "hub.mode": "unsubscribe",
                "hub.verify_token": _VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            }
        )
        self.assertIsNone(result)

    # ---- send_outbound ---------------------------------------------------

    def test_send_outbound_success(self):
        async def run():
            adapter = self._adapter()
            import httpx as _httpx

            def handler(request: _httpx.Request) -> _httpx.Response:
                self.assertIn("/v25.0/1234567890/messages", str(request.url))
                self.assertEqual(
                    request.headers["authorization"], f"Bearer {_ACCESS_TOKEN}"
                )
                import json
                body = json.loads(request.content)
                self.assertEqual(body["messaging_product"], "whatsapp")
                self.assertEqual(body["to"], "5511999999999")
                self.assertEqual(body["text"]["body"], "Hello from VOX")
                return _httpx.Response(200, json={"messages": [{"id": "wamid.SENT"}]})

            adapter._client = _httpx.AsyncClient(
                base_url="https://graph.facebook.com",
                headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
                transport=_httpx.MockTransport(handler),
            )
            msg = VOXOutboundMessage(channel="whatsapp", recipient_id="5511999999999", text="Hello from VOX")
            result = await adapter.send_outbound(msg)
            self.assertTrue(result)

        asyncio.run(run())

    def test_send_outbound_failure(self):
        async def run():
            adapter = self._adapter()
            import httpx as _httpx

            def handler(request: _httpx.Request) -> _httpx.Response:
                return _httpx.Response(401, json={"error": "OAuthException"})

            adapter._client = _httpx.AsyncClient(
                base_url="https://graph.facebook.com",
                headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
                transport=_httpx.MockTransport(handler),
            )
            msg = VOXOutboundMessage(channel="whatsapp", recipient_id="5511999999999", text="test")
            result = await adapter.send_outbound(msg)
            self.assertFalse(result)

        asyncio.run(run())

    def test_send_outbound_no_token_fails(self):
        async def run():

            adapter = WhatsAppAdapter(
                {"WHATSAPP_ACCESS_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": "123"}
            )
            msg = VOXOutboundMessage(channel="whatsapp", recipient_id="x", text="test")
            result = await adapter.send_outbound(msg)
            self.assertFalse(result)

        asyncio.run(run())


class _ProviderAdapter(BaseAdapter):
    """Minimal adapter for testing the abstraction with an arbitrary name."""

    CHANNEL = "test_provider"
    WEBHOOK_PATH = "/webhook/test-provider"
    PARAMS = {"TEST_PROVIDER_TOKEN": {"label": "API Token", "required": True}}  # noqa: RUF012
    SENSITIVE_PARAMS = ("TEST_PROVIDER_TOKEN",)

    def __init__(self, config: dict):
        self._config = config

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        return bool(config.get("TEST_PROVIDER_TOKEN"))

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def verify_request(self, request, body: bytes | None = None) -> bool:
        return request.headers.get("X-Secret") == "valid"

    def handle_verification(self, query: dict) -> str | None:
        return None

    async def parse_inbound(self, request, body: bytes) -> VOXInboundMessage | None:
        import json

        data = json.loads(body)
        return VOXInboundMessage(
            channel="test_provider",
            sender_id=data["sender_id"],
            text=data.get("text", ""),
            message_id=data.get("message_id", "1"),
        )

    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        return True


class _ProviderAdapterNoWebhook(BaseAdapter):
    """Adapter without WEBHOOK_PATH for testing skip logic."""

    CHANNEL = "no_webhook_provider"
    PARAMS = {}  # noqa: RUF012

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        return True

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def verify_request(self, request, body: bytes | None = None) -> bool:
        return True

    def handle_verification(self, query: dict) -> str | None:
        return None

    async def parse_inbound(self, request, body: bytes) -> VOXInboundMessage | None:
        return None

    async def send_outbound(self, message: VOXOutboundMessage) -> bool:
        return True


class TestIngressServerAbstraction(unittest.TestCase):
    def test_adapters_without_webhook_path_skipped(self):
        """Adapter without WEBHOOK_PATH doesn't register a route."""
        server = IngressServer.__new__(IngressServer)
        server._app = web.Application()
        server._adapters = {"no_webhook_provider": _ProviderAdapterNoWebhook()}
        server._bot = AsyncMock()
        server._bot.config = {}
        server._verification_handler = lambda req: web.Response(text="ok")
        server._register_routes()
        resource_paths = [r.canonical for r in server._app.router.resources()]
        self.assertNotIn("/no_webhook_provider", resource_paths)
        self.assertIn("/health", resource_paths)

    def test_adapters_without_webhook_path_still_registered(self):
        """Adapter without WEBHOOK_PATH still in _adapters dict."""
        server = IngressServer.__new__(IngressServer)
        server._app = web.Application()
        server._adapters = {"no_webhook_provider": _ProviderAdapterNoWebhook()}
        server._bot = AsyncMock()
        server._bot.config = {}
        server._verification_handler = lambda req: web.Response(text="ok")
        server._register_routes()
        self.assertIn("no_webhook_provider", server._adapters)

    def test_adapter_without_webhook_path_parse_inbound(self):
        """Adapter without webhook path can still handle inbound messages."""
        adapter = _ProviderAdapter({"TEST_PROVIDER_TOKEN": "tok"})
        import json

        async def run():
            request = AsyncMock()
            body = json.dumps(
                {"sender_id": "u1", "text": "hello", "message_id": "42"}
            ).encode()
            msg = await adapter.parse_inbound(request, body)
            self.assertEqual(msg.channel, "test_provider")
            self.assertEqual(msg.sender_id, "u1")
            self.assertEqual(msg.text, "hello")

        asyncio.run(run())

    def test_lifecycle_start_shutdown(self):
        """Adapters can be started and shut down."""

        async def run():
            adapter = _ProviderAdapter({"TEST_PROVIDER_TOKEN": "tok"})
            await adapter.start()
            await adapter.shutdown()

        asyncio.run(run())

    def test_gateway_configured_for_adapter(self):
        """CommGatewayCapability PARAMS includes params from registered adapters."""
        from vox.capabilities.comm.gateway import CommGatewayCapability

        self.assertIn("TELEGRAM_BOT_TOKEN", CommGatewayCapability.PARAMS)
        self.assertIn("WHATSAPP_ACCESS_TOKEN", CommGatewayCapability.PARAMS)
        self.assertIn("GATEWAY_WEBHOOK_SECRET", CommGatewayCapability.PARAMS)

    def test_adapter_base_params_union(self):
        """BaseAdapter.PARAMS is empty; specific adapters override."""
        self.assertEqual(BaseAdapter.PARAMS, {})


_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "whatsapp"


def _load_whatsapp_fixture(name: str) -> dict:
    """Load a WhatsApp webhook fixture exactly as captured (JSON round-trip)."""
    with open(_FIXTURES_DIR / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


class TestWhatsAppCompatibilityFixtures(unittest.TestCase):
    """Real Meta WhatsApp Cloud API v25.0 webhook payloads, as regression fixtures.

    WhatsApp Cloud API v25.0

    Tested payload shapes:
    - inbound text message
    - outbound status: sent
    - outbound status: delivered

    Fixtures under ``tests/fixtures/whatsapp/`` were captured from Meta's
    developer console (Graph API v25.0) and preserve the observed payload
    topology; all real identifiers are replaced with deterministic synthetic
    placeholders (see ``test_fixtures_contain_no_real_captured_identifiers``).
    They pin the provider→VOXInboundMessage normalization contract; no claim
    is made that the complete v25.0 surface is supported.
    """

    def _adapter(self, **overrides):
        config = {
            "WHATSAPP_ACCESS_TOKEN": _ACCESS_TOKEN,
            "WHATSAPP_PHONE_NUMBER_ID": _PHONE_NUMBER_ID,
            "WHATSAPP_APP_SECRET": _APP_SECRET,
            "WHATSAPP_VERIFY_TOKEN": _VERIFY_TOKEN,
            "WHATSAPP_API_VERSION": "v25.0",
        }
        config.update(overrides)
        return WhatsAppAdapter(config)

    def test_parse_inbound_text_fixture(self):
        adapter = self._adapter()
        message = adapter.parse_inbound(_load_whatsapp_fixture("inbound_text"))

        self.assertIsInstance(message, VOXInboundMessage)
        self.assertEqual(message.channel, "whatsapp")
        self.assertEqual(message.message_id, "TEST_MESSAGE_002")
        self.assertEqual(message.sender_id, "+5490000000000")
        self.assertEqual(message.content_type, "text")
        self.assertEqual(message.text, "Thanks for the confirmation")
        self.assertEqual(message.sender_metadata["profile_name"], "Test User One")

    def test_inbound_text_raw_payload_preserved(self):
        adapter = self._adapter()
        message = adapter.parse_inbound(_load_whatsapp_fixture("inbound_text"))

        # Provider-specific data stays in raw_payload, not the generic contract.
        self.assertEqual(message.raw_payload["type"], "text")
        self.assertEqual(message.raw_payload["from_user_id"], "TEST_USER_ID_001")
        self.assertEqual(
            message.raw_payload["internal_1p_only_data"]["account_context"][
                "waac_id"
            ],
            "TEST_WAAC_ID",
        )

    def test_parse_sent_status_fixture(self):
        adapter = self._adapter()
        message = adapter.parse_inbound(_load_whatsapp_fixture("status_sent"))

        self.assertIsInstance(message, VOXInboundMessage)
        self.assertEqual(message.channel, "whatsapp")
        self.assertEqual(message.content_type, "event")
        self.assertEqual(message.text, "sent")
        self.assertEqual(message.message_id, "TEST_MESSAGE_001")
        # Recipient is preserved in the normalized representation.
        self.assertEqual(message.sender_id, "+5490000000000")

    def test_parse_delivered_status_fixture(self):
        adapter = self._adapter()
        message = adapter.parse_inbound(_load_whatsapp_fixture("status_delivered"))

        self.assertIsInstance(message, VOXInboundMessage)
        self.assertEqual(message.channel, "whatsapp")
        self.assertEqual(message.content_type, "event")
        self.assertEqual(message.text, "delivered")
        self.assertEqual(message.message_id, "TEST_MESSAGE_001")
        self.assertEqual(message.sender_id, "+5490000000000")

    def test_status_raw_payload_preserved(self):
        adapter = self._adapter()
        message = adapter.parse_inbound(_load_whatsapp_fixture("status_delivered"))

        self.assertEqual(message.sender_metadata["status"], "delivered")
        self.assertEqual(message.raw_payload["pricing"]["category"], "utility")
        self.assertEqual(
            message.raw_payload["conversation"]["origin"]["type"], "utility"
        )

    def test_fixtures_contain_no_real_captured_identifiers(self):
        """Regression guard: real Meta test-environment values must not return.

        The compatibility fixtures were sanitized to deterministic placeholders;
        if any known real captured identifier reappears, the suite fails.
        """
        known_real_values = [
            "José Fabián",
            "5491122543927",
            "15556765588",
            "1181524698387615",
            "2568720346912662",
            "1605439644524932",
            "2007496036688892",
            "95378389123273",
            "AR.2279416016208233",
            "06e2f0b67a9d5d5ff6c45f59a7282c4b",
            "wamid.HBgNNTQ5MTEyMjU0MzkyNxUCABEYEjcxQkU3NkY3Q0FDODc3NzU2MgA=",
            "wamid.HBgNNTQ5MTEyMjU0MzkyNxUCABIYIEFDRTBCQkUxMDg4QzFCOTEzQTA5OTgyNkFFMzc0QTg4AA==",
            "1786472061",
            "1786472062",
            "1786472485",
            "FQAWiIbDu/GI2gUWyLXD0Ao2rN7w/JmPkAkYCWdyYXBoX2FwaUwWvtKG/+SlmQQWrN7w/JmPkAkWiIbDu/GI2gUlAgAA",
        ]
        for name in ("inbound_text", "status_sent", "status_delivered"):
            serialized = json.dumps(
                _load_whatsapp_fixture(name), ensure_ascii=False
            )
            for value in known_real_values:
                self.assertNotIn(
                    value, serialized, f"{name}.json still contains {value!r}"
                )

    def test_fixtures_use_deterministic_placeholders(self):
        """Sanitized fixtures keep cross-fixture synthetic identity values."""
        for name in ("inbound_text", "status_sent", "status_delivered"):
            serialized = json.dumps(_load_whatsapp_fixture(name))
            self.assertIn("TEST_WABA_ID", serialized, name)
            self.assertIn("TEST_PHONE_NUMBER_ID", serialized, name)
            self.assertIn("TEST_USER_ID_001", serialized, name)
            self.assertIn("+5490000000000", serialized, name)

    def test_send_outbound_graph_api_request_shape(self):
        async def run():
            adapter = self._adapter()

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.method, "POST")
                self.assertIn("/v25.0/1234567890/messages", str(request.url))
                self.assertEqual(
                    request.headers["authorization"], f"Bearer {_ACCESS_TOKEN}"
                )
                body = json.loads(request.content)
                self.assertEqual(body["messaging_product"], "whatsapp")
                self.assertEqual(body["recipient_type"], "individual")
                self.assertEqual(body["to"], "5491122543927")
                self.assertEqual(body["type"], "text")
                self.assertEqual(body["text"]["body"], "Thanks for the confirmation")
                return httpx.Response(200, json={"messages": [{"id": "wamid.XYZ"}]})

            adapter._client = httpx.AsyncClient(
                base_url="https://graph.facebook.com",
                headers={"Authorization": f"Bearer {_ACCESS_TOKEN}"},
                transport=httpx.MockTransport(handler),
            )
            msg = VOXOutboundMessage(
                channel="whatsapp",
                recipient_id="5491122543927",
                text="Thanks for the confirmation",
            )
            result = await adapter.send_outbound(msg)
            self.assertTrue(result)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
