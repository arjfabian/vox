"""Focused tests for the ai.llm capability port and its provider adapters.

Covers:
  * the port vs. adapter split (no provider logic inside the port);
  * adapter registration/discovery and aggregated per-adapter config.yml;
  * execution-time adapter selection with explicit per-operation identity;
  * adapter availability (provisioning) distinct from selection, scoped per
    bound workload;
  * per-workload adapter instances and Vault-only credential injection;
  * model selection independent from adapter identity;
  * the formal LLMAdapter lifecycle contract (start/shutdown), never duck-typed;
  * no process-global adapter state;
  * Ollama/Gemini request shapes against mocked HTTP transports;
  * the port pipeline (sanitize -> cache -> generation) and adapter-aware cache
    keys;
  * consumer decoupling guards (no concrete-adapter imports, no provider names).
"""

from __future__ import annotations

import base64
import inspect
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from vox.capabilities.ai.llm import LLMAdapterUnavailableError, LLMCapability
from vox.capabilities.ai.llm.adapters import ADAPTER_REGISTRY
from vox.capabilities.ai.llm.adapters.base import LLMAdapter
from vox.capabilities.ai.llm.adapters.gemini import GeminiAdapter
from vox.capabilities.ai.llm.adapters.ollama import OllamaAdapter
from vox.capabilities.ai.llm.models import LLMChatMessage, LLMGenerationResult

ROOT = Path(__file__).resolve().parent.parent
LLM_DIR = ROOT / "src" / "vox" / "capabilities" / "ai" / "llm"
CAPABILITIES = ROOT / "src" / "vox" / "capabilities"

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

TEST_KEY = "test-key-not-real"

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"


class _FakeLLMAdapter(LLMAdapter):
    """Stub adapter that records operations instead of calling a provider."""

    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id
        self.calls: list[tuple[str, dict]] = []

    def resolve_model(self, model: str | None) -> str:
        return model or f"{self.adapter_id}:default"

    async def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMGenerationResult:
        self.calls.append(
            (
                "generate",
                {
                    "model": model,
                    "system": system,
                    "prompt": prompt,
                    "json_mode": json_mode,
                },
            )
        )
        return LLMGenerationResult(content=f"{self.adapter_id}:gen", model=model)

    async def chat(
        self,
        *,
        model: str | None,
        messages: list[LLMChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult:
        self.calls.append(("chat", {"model": model, "messages": messages}))
        return LLMGenerationResult(
            content=f"{self.adapter_id}:chat",
            model=model or f"{self.adapter_id}:default",
        )

    async def generate_vision(
        self,
        *,
        model: str | None,
        system: str,
        prompt: str,
        image_bytes: bytes,
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult:
        self.calls.append(("vision", {"model": model, "image_bytes": image_bytes}))
        return LLMGenerationResult(
            content=f"{self.adapter_id}:vision",
            model=model or f"{self.adapter_id}:default",
        )


class TestLLMCapabilityArchitecture(unittest.IsolatedAsyncioTestCase):
    """The ai.llm port and its adapters honor the comm.gateway pattern."""

    @classmethod
    def setUpClass(cls):
        LLMCapability.load_contract(LLM_DIR / "capability.py")

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def _bound(self, overrides=None, secrets=None, cap=None):
        if cap is None:
            cap = LLMCapability()
            cap.id = "ai.llm"
            cap.logger = MagicMock()
        base = {
            "LLM_CACHE_DB_PATH": str(
                Path(self._tmp) / f"cache_{uuid.uuid4().hex[:8]}.db"
            ),
        }
        base.update(overrides or {})
        bound = cap.mount(host=MagicMock(), overrides=base)
        bound.logger = MagicMock()
        bound._secrets.update(secrets or {})
        return bound

    async def _booted(self, overrides=None, secrets=None):
        bound = self._bound(overrides=overrides, secrets=secrets)
        await bound.boot()
        return bound

    # -- registration / contract -----------------------------------------------

    def test_registry_contains_concrete_adapters_only(self):
        self.assertEqual(set(ADAPTER_REGISTRY), {"ollama", "gemini"})
        for adapter_cls in ADAPTER_REGISTRY.values():
            self.assertTrue(issubclass(adapter_cls, LLMAdapter))

    def test_adapter_param_specs_from_config_yml(self):
        specs = LLMCapability.adapter_param_specs()
        self.assertIn("ollama", specs)
        self.assertIn("gemini", specs)
        self.assertIn("OLLAMA_API_BASE_URL", specs["ollama"])
        self.assertIn("OLLAMA_VISION_MODEL", specs["ollama"])
        self.assertIn("GEMINI_MODEL", specs["gemini"])
        self.assertIn("GEMINI_API_KEY", specs["gemini"])

    # -- port purity -----------------------------------------------------------

    def test_port_source_contains_no_provider_logic(self):
        source = (LLM_DIR / "capability.py").read_text().lower()
        for token in (
            "ollama",
            "gemini",
            "11434",
            "generativelanguage",
            "x-goog-api-key",
            "api/chat",
        ):
            self.assertNotIn(token, source, f"port must not reference {token}")

    def test_port_public_surface_is_provider_agnostic(self):
        methods = {
            name
            for name, member in inspect.getmembers(LLMCapability)
            if not name.startswith("_") and callable(member)
        }
        for expected in (
            "generate",
            "chat",
            "generate_vision",
            "parse_intent",
            "available_adapters",
        ):
            self.assertIn(expected, methods)
        for forbidden in ("ollama_generate", "gemini_chat"):
            self.assertNotIn(forbidden, methods)

    def test_port_requires_explicit_adapter_per_operation(self):
        sig = inspect.signature(LLMCapability.generate)
        adapter_param = sig.parameters["adapter"]
        self.assertIs(
            adapter_param.kind, inspect.Parameter.KEYWORD_ONLY
        )
        self.assertIs(adapter_param.default, inspect.Parameter.empty)
        self.assertIs(sig.parameters["model"].default, None)

    # -- lifecycle -------------------------------------------------------------

    def test_adapter_lifecycle_is_contract_not_duck_typed(self):
        self.assertTrue(inspect.iscoroutinefunction(LLMAdapter.start))
        self.assertTrue(inspect.iscoroutinefunction(LLMAdapter.shutdown))
        for adapter_cls in ADAPTER_REGISTRY.values():
            self.assertIsNotNone(adapter_cls.start)
            self.assertIsNotNone(adapter_cls.shutdown)
        source = (LLM_DIR / "capability.py").read_text()
        self.assertIn("await adapter.start()", source)
        self.assertIn("await adapter.shutdown()", source)
        self.assertNotIn("hasattr(", source)

    def test_no_module_global_adapter_state(self):
        import vox.capabilities.ai.llm.capability as llm_cap

        self.assertFalse(hasattr(llm_cap, "_adapters"))
        source = Path(llm_cap.__file__).read_text()
        self.assertNotIn("_shared_adapters", source)
        self.assertNotIn("global _adapters", source)

    # -- provisioning vs selection ---------------------------------------------

    async def test_gemini_availability_is_workload_scoped(self):
        with_secret = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        without_secret = await self._booted(secrets={})
        try:
            # Availability is decided per bound workload from Vault secrets.
            self.assertEqual(with_secret.available_adapters(), ["gemini", "ollama"])
            self.assertEqual(without_secret.available_adapters(), ["ollama"])
            # A workload without the key can never select Gemini, regardless of
            # what another workload has provisioned.
            with self.assertRaises(LLMAdapterUnavailableError):
                without_secret._adapter("gemini")
            self.assertIs(with_secret._adapter("gemini"), with_secret._adapters["gemini"])
        finally:
            await with_secret.shutdown()
            await without_secret.shutdown()

    async def test_adapter_instances_isolated_per_workload(self):
        a = self._bound(secrets={"GEMINI_API_KEY": f"{TEST_KEY}-a"})
        b = self._bound(secrets={"GEMINI_API_KEY": f"{TEST_KEY}-b"})
        await a.boot()
        await b.boot()
        try:
            self.assertIsNot(a._adapters["ollama"], b._adapters["ollama"])
            self.assertIsNot(a._adapters["gemini"], b._adapters["gemini"])
            self.assertEqual(a._adapters["gemini"]._api_key, f"{TEST_KEY}-a")
            self.assertEqual(b._adapters["gemini"]._api_key, f"{TEST_KEY}-b")
        finally:
            await a.shutdown()
            await b.shutdown()

    def test_credentials_injected_via_secrets_never_env(self):
        self.assertFalse(GeminiAdapter.is_configured({}))
        self.assertFalse(
            GeminiAdapter.is_configured({"GEMINI_MODEL": "gemini-2.5-flash"})
        )
        self.assertTrue(
            GeminiAdapter.is_configured({"GEMINI_API_KEY": TEST_KEY})
        )
        for adapter_file in ("gemini/adapter.py", "ollama/adapter.py"):
            source = (LLM_DIR / "adapters" / adapter_file).read_text()
            self.assertNotIn("os.environ", source)
            self.assertNotIn("getenv", source)

    # -- model selection independent from adapter ------------------------------

    def test_model_defaults_reside_in_adapters(self):
        self.assertEqual(OllamaAdapter({}).resolve_model(None), "llama3.2:1b")
        self.assertEqual(
            OllamaAdapter({"OLLAMA_MODEL": "custom"}).resolve_model(None),
            "custom",
        )
        self.assertEqual(OllamaAdapter({}).resolve_model("m"), "m")
        self.assertEqual(
            GeminiAdapter({}).resolve_model(None), "gemini-2.5-flash"
        )
        self.assertEqual(
            GeminiAdapter({"GEMINI_MODEL": "custom"}).resolve_model(None),
            "custom",
        )
        self.assertEqual(GeminiAdapter({}).resolve_model("m"), "m")

    async def test_model_selection_independent_from_adapter(self):
        bound = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        ollama_fake = _FakeLLMAdapter("ollama")
        gemini_fake = _FakeLLMAdapter("gemini")
        bound._adapters = {"ollama": ollama_fake, "gemini": gemini_fake}
        try:
            await bound.generate("s", "p", adapter="ollama", model="llama3.2:1b")
            await bound.generate("s", "p", adapter="gemini", model="gemini-2.5-flash")
        finally:
            await bound.shutdown()

        ollama_call = ollama_fake.calls[0][1]
        gemini_call = gemini_fake.calls[0][1]
        self.assertEqual(ollama_call["model"], "llama3.2:1b")
        self.assertEqual(gemini_call["model"], "gemini-2.5-flash")

    # -- execution-time selection ----------------------------------------------

    async def test_operations_select_adapter_explicitly(self):
        bound = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        ollama_fake = _FakeLLMAdapter("ollama")
        gemini_fake = _FakeLLMAdapter("gemini")
        bound._adapters = {"ollama": ollama_fake, "gemini": gemini_fake}
        try:
            gen = await bound.generate("s", "p", adapter="ollama")
            chat = await bound.chat(
                [LLMChatMessage(role="user", content="hi")], adapter="gemini"
            )
        finally:
            await bound.shutdown()

        self.assertEqual(gen, "ollama:gen")
        self.assertEqual(chat, "gemini:chat")
        self.assertEqual([c[0] for c in ollama_fake.calls], ["generate"])
        self.assertEqual([c[0] for c in gemini_fake.calls], ["chat"])

    async def test_same_workload_uses_different_adapters_per_operation(self):
        bound = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        ollama_fake = _FakeLLMAdapter("ollama")
        gemini_fake = _FakeLLMAdapter("gemini")
        bound._adapters = {"ollama": ollama_fake, "gemini": gemini_fake}
        try:
            gen = await bound.generate("s", "p", adapter="ollama")
            vision = await bound.generate_vision(
                "s", "p", image_bytes=JPEG_BYTES, adapter="gemini"
            )
        finally:
            await bound.shutdown()

        self.assertEqual(gen, "ollama:gen")
        self.assertEqual(vision, "gemini:vision")
        self.assertEqual([c[0] for c in ollama_fake.calls], ["generate"])
        self.assertEqual([c[0] for c in gemini_fake.calls], ["vision"])
        # The vision adapter receives the raw image bytes it needs.
        self.assertEqual(gemini_fake.calls[0][1]["image_bytes"], JPEG_BYTES)

    async def test_missing_adapter_raises_llm_adapter_unavailable(self):
        bound = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        try:
            with self.assertRaises(LLMAdapterUnavailableError):
                await bound.generate("s", "p", adapter="nope")
        finally:
            await bound.shutdown()

    # -- port pipeline ---------------------------------------------------------

    async def test_cache_key_includes_adapter_identity(self):
        bound = await self._booted(secrets={"GEMINI_API_KEY": TEST_KEY})
        ollama_fake = _FakeLLMAdapter("ollama")
        gemini_fake = _FakeLLMAdapter("gemini")
        bound._adapters = {"ollama": ollama_fake, "gemini": gemini_fake}
        try:
            # Same system, prompt and effective model through two adapters:
            # each adapter must get its own cache key and its own generation.
            first = await bound.generate("s", "p", adapter="ollama")
            second = await bound.generate("s", "p", adapter="gemini")
        finally:
            await bound.shutdown()

        self.assertEqual(first, "ollama:gen")
        self.assertEqual(second, "gemini:gen")
        self.assertEqual([c[0] for c in ollama_fake.calls], ["generate"])
        self.assertEqual([c[0] for c in gemini_fake.calls], ["generate"])

    # -- consumer decoupling guards --------------------------------------------

    def test_consumers_never_import_or_name_concrete_adapters(self):
        for rel in ("ai/parsing/capability.py", "image/ocr/capability.py"):
            source = (CAPABILITIES / rel).read_text()
            self.assertNotIn("vox.capabilities.ai.llm.adapters", source, rel)
            for token in ("ollama", "gemini"):
                self.assertNotIn(token, source.lower(), f"{rel}: names {token}")


class TestOllamaAdapterRequests(unittest.IsolatedAsyncioTestCase):
    """Ollama request/response shape against a mocked HTTP transport."""

    @classmethod
    def setUpClass(cls):
        LLMCapability.load_contract(LLM_DIR / "capability.py")

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def _mock_transport(self, captured):
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"message": {"content": "hello"}, "eval_count": 12}
            )

        async def fake_ensure_client(self) -> httpx.AsyncClient:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    transport=httpx.MockTransport(handler),
                )
            return self._client

        return fake_ensure_client

    async def _booted(self, captured, overrides=None):
        with patch.object(
            OllamaAdapter, "_ensure_client", new=self._mock_transport(captured)
        ):
            bound = LLMCapability()
            base = {
                "LLM_CACHE_ENABLED": False,
                "LLM_RAG_ENABLED": False,
                "LLM_CACHE_DB_PATH": str(
                    Path(self._tmp) / f"cache_{uuid.uuid4().hex[:8]}.db"
                ),
            }
            base.update(overrides or {})
            bound = bound.mount(host=MagicMock(), overrides=base)
            bound.logger = MagicMock()
            await bound.boot()
            return bound

    async def test_generate_pipeline_and_request_shape(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            out = await bound.generate(
                "sys", "prompt", adapter="ollama", json_mode=True
            )
        finally:
            await bound.shutdown()

        self.assertEqual(out, "hello")
        self.assertTrue(captured["url"].endswith("/api/chat"))
        body = captured["body"]
        self.assertEqual(body["model"], "llama3.2:1b")
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["format"], "json")
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "prompt"},
            ],
        )
        self.assertEqual(body["options"]["temperature"], 0.7)
        self.assertEqual(body["options"]["num_predict"], 2048)

    async def test_chat_request_uses_messages_and_model_override(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            out = await bound.chat(
                [
                    LLMChatMessage(role="system", content="be terse"),
                    LLMChatMessage(role="user", content="hi"),
                ],
                adapter="ollama",
                model="custom-ollama-model",
            )
        finally:
            await bound.shutdown()

        self.assertEqual(out, "hello")
        body = captured["body"]
        self.assertEqual(body["model"], "custom-ollama-model")
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "be terse", "images": []},
                {"role": "user", "content": "hi", "images": []},
            ],
        )

    async def test_vision_request_embeds_image_and_uses_vision_model(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            out = await bound.generate_vision(
                "sys", "prompt", image_bytes=JPEG_BYTES, adapter="ollama"
            )
        finally:
            await bound.shutdown()

        self.assertEqual(out, "hello")
        body = captured["body"]
        self.assertEqual(body["model"], "llava")
        self.assertEqual(body["messages"][0]["role"], "system")
        user = body["messages"][1]
        self.assertEqual(user["content"], "prompt")
        self.assertEqual(user["images"], [base64.b64encode(JPEG_BYTES).decode()])

    async def test_ollama_requires_no_secret_and_is_always_available(self):
        self.assertTrue(OllamaAdapter.is_configured({}))
        bound = await self._booted({})
        try:
            self.assertEqual(bound.available_adapters(), ["ollama"])
        finally:
            await bound.shutdown()


class TestGeminiAdapterRequests(unittest.IsolatedAsyncioTestCase):
    """Gemini request/response shape against a mocked HTTP transport."""

    @classmethod
    def setUpClass(cls):
        LLMCapability.load_contract(LLM_DIR / "capability.py")

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def _mock_transport(self, captured):
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("x-goog-api-key")
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "from gemini"}]}}
                    ],
                    "usageMetadata": {"totalTokenCount": 9},
                },
            )

        async def fake_ensure_client(self) -> httpx.AsyncClient:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=_GEMINI_ENDPOINT,
                    transport=httpx.MockTransport(handler),
                )
            return self._client

        return fake_ensure_client

    async def _booted(self, captured, overrides=None):
        with patch.object(
            GeminiAdapter, "_ensure_client", new=self._mock_transport(captured)
        ):
            bound = LLMCapability()
            base = {
                "LLM_CACHE_ENABLED": False,
                "LLM_RAG_ENABLED": False,
                "LLM_CACHE_DB_PATH": str(
                    Path(self._tmp) / f"cache_{uuid.uuid4().hex[:8]}.db"
                ),
            }
            base.update(overrides or {})
            bound = bound.mount(host=MagicMock(), overrides=base)
            bound.logger = MagicMock()
            bound._secrets["GEMINI_API_KEY"] = TEST_KEY
            await bound.boot()
            return bound

    async def test_generate_request_shape_and_auth(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            out = await bound.generate(
                "sys", "prompt", adapter="gemini", json_mode=True
            )
        finally:
            await bound.shutdown()

        self.assertEqual(out, "from gemini")
        self.assertTrue(captured["url"].endswith("/models/gemini-2.5-flash:generateContent"))
        self.assertEqual(captured["auth"], TEST_KEY)
        body = captured["body"]
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "prompt"}]}])
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["generationConfig"]["temperature"], 0.7)
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 2048)

    async def test_chat_maps_roles_and_system(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            await bound.chat(
                [
                    LLMChatMessage(role="system", content="be terse"),
                    LLMChatMessage(role="user", content="hi"),
                    LLMChatMessage(role="assistant", content="ok"),
                ],
                adapter="gemini",
            )
        finally:
            await bound.shutdown()

        body = captured["body"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "be terse"}]})
        self.assertEqual(
            body["contents"],
            [
                {"role": "user", "parts": [{"text": "hi"}]},
                {"role": "model", "parts": [{"text": "ok"}]},
            ],
        )

    async def test_vision_request_inlines_image_with_sniffed_mime(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            out = await bound.generate_vision(
                "sys", "prompt", image_bytes=PNG_BYTES, adapter="gemini"
            )
        finally:
            await bound.shutdown()

        self.assertEqual(out, "from gemini")
        body = captured["body"]
        self.assertTrue(body["contents"][0]["role"] == "user")
        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(
            parts[0]["inline_data"]["data"],
            base64.b64encode(PNG_BYTES).decode(),
        )
        self.assertEqual(parts[1]["text"], "prompt")

    async def test_vision_uses_vision_model_default(self):
        captured = {}
        bound = await self._booted(captured)
        try:
            await bound.generate_vision(
                "sys", "prompt", image_bytes=JPEG_BYTES, adapter="gemini"
            )
        finally:
            await bound.shutdown()

        self.assertTrue(
            captured["url"].endswith("/models/gemini-2.5-flash:generateContent")
        )

    async def test_non_2xx_logs_sanitized_body_and_preserves_exception(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("x-goog-api-key")
            return httpx.Response(
                404,
                json={
                    "error": {
                        "code": 404,
                        "message": (
                            "models/gemini-2.5-flash-lite is not found for "
                            "API version v1beta, or is not supported for "
                            "generateContent. Call ListModels to see the list "
                            "of available Gemini models and their supported "
                            "methods."
                        ),
                        "status": "NOT_FOUND",
                    }
                },
            )

        async def fake_ensure_client(self) -> httpx.AsyncClient:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=_GEMINI_ENDPOINT,
                    transport=httpx.MockTransport(handler),
                )
            return self._client

        with patch.object(
            GeminiAdapter, "_ensure_client", new=fake_ensure_client
        ):
            bound = LLMCapability()
            base = {
                "LLM_CACHE_ENABLED": False,
                "LLM_RAG_ENABLED": False,
                "LLM_CACHE_DB_PATH": str(
                    Path(self._tmp) / f"cache_{uuid.uuid4().hex[:8]}.db"
                ),
            }
            bound = bound.mount(host=MagicMock(), overrides=base)
            bound.logger = MagicMock()
            bound._secrets["GEMINI_API_KEY"] = TEST_KEY
            await bound.boot()

            try:
                with self.assertLogs(
                    "vox.capabilities.ai.llm.adapters.gemini.adapter",
                    level="ERROR",
                ) as logs, self.assertRaises(httpx.HTTPStatusError) as ctx:
                    await bound.generate(
                        "sys",
                        "prompt",
                        adapter="gemini",
                        model="gemini-2.5-flash-lite",
                    )
            finally:
                await bound.shutdown()

        self.assertEqual(ctx.exception.response.status_code, 404)
        log_text = "\n".join(logs.output)
        self.assertIn("Gemini API HTTP 404 calling", log_text)
        self.assertIn(
            "/models/gemini-2.5-flash-lite:generateContent", log_text
        )
        self.assertIn("not found for API version v1beta", log_text)
        self.assertIn("NOT_FOUND", log_text)
        self.assertIn(str(captured["url"]), log_text)
        self.assertNotIn(TEST_KEY, log_text)
        self.assertNotIn(captured["auth"], log_text)


if __name__ == "__main__":
    unittest.main()