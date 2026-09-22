"""Focused tests for the ai.parsing capability.

Covers the YAML contract, the normalized intent result model, end-to-end
``parse`` against a mocked ``ai.llm`` provider reached through the host, JSON
normalization edge cases, unresolved-input behaviour, and the stable extension
point for a future cosine/semantic resolver fallback.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.capabilities.ai.parsing import (
    CommandSpec,
    ParsedIntent,
    ParsingCapability,
)
from vox.capabilities.ai.parsing.capability import UNRESOLVED
from vox.capabilities.base import VOXBoundCapability

ROOT = Path(__file__).resolve().parent.parent
PARSING_DIR = ROOT / "src" / "vox" / "capabilities" / "ai" / "parsing"


def _vocabulary():
    return [
        CommandSpec(name="send_email", description="compose and send an email"),
        CommandSpec(name="weather", description="current weather for a city"),
    ]


async def _bound_capability(llm=None):
    """Build a mounted ParsingCapability bound proxy with a stubbed host."""
    ParsingCapability.load_contract(PARSING_DIR / "capability.py")
    cap = ParsingCapability()
    cap.id = "ai.parsing"
    cap.logger = MagicMock()

    host = MagicMock()
    host.get_capability.return_value = llm
    host.logger = MagicMock()
    bound = VOXBoundCapability(cap, host, {})
    await bound.boot()
    return bound


class TestParsingContract(unittest.TestCase):
    def test_class_name_matches_yaml_name(self):
        contract = ParsingCapability.load_contract(PARSING_DIR / "capability.py")
        self.assertIsNotNone(contract)
        self.assertEqual(contract.name, "ai.parsing")
        self.assertEqual(ParsingCapability.CAPABILITY_NAME, "ai.parsing")

    def test_provides_intent_parsing(self):
        contract = ParsingCapability.load_contract(PARSING_DIR / "capability.py")
        self.assertIn("intent_parsing", contract.provides)

    def test_no_config_params_or_secrets(self):
        """Parsing config comes from the ai.llm provider, not its own params."""
        contract = ParsingCapability.load_contract(PARSING_DIR / "capability.py")
        self.assertEqual(contract.params, {})
        self.assertEqual(contract.secrets, {})

    def test_public_api_is_small_and_capability_oriented(self):
        import inspect

        methods = {
            name
            for name, member in inspect.getmembers(ParsingCapability)
            if not name.startswith("_") and callable(member)
        }
        # The capability's public surface is exactly the lifecycle + one op.
        self.assertIn("parse", methods)
        self.assertNotIn("chat", methods)
        self.assertNotIn("generate", methods)


class TestResultModel(unittest.TestCase):
    def test_frozen_fields(self):
        intent = ParsedIntent(
            command="weather", confidence=0.9, entities={"city": "berlin"}
        )
        with self.assertRaises(FrozenInstanceError):
            intent.command = "send_email"

    def test_entities_default_to_empty(self):
        intent = ParsedIntent(command="weather", confidence=0.9)
        self.assertEqual(intent.entities, {})
        self.assertEqual(intent.command, "weather")
        self.assertEqual(intent.confidence, 0.9)

    def test_unresolved_sentinel(self):
        self.assertEqual(UNRESOLVED.command, "")
        self.assertEqual(UNRESOLVED.confidence, 0.0)
        self.assertEqual(UNRESOLVED.entities, {})


class TestParseEndToEnd(unittest.TestCase):
    def test_returns_parsed_intent_from_llm(self):
        async def scenario():
            llm = MagicMock()
            llm.generate = AsyncMock(return_value='{"command": "weather", "confidence": 0.85, "entities": {"city": "berlin"}}')
            bound = await _bound_capability(llm)
            intent = await bound.parse("What's the weather in Berlin?", _vocabulary(), adapter="ollama")
            self.assertIsInstance(intent, ParsedIntent)
            self.assertEqual(intent.command, "weather")
            self.assertEqual(intent.confidence, 0.85)
            self.assertEqual(intent.entities, {"city": "berlin"})
        asyncio.run(scenario())

    def test_provider_is_reached_through_host_not_direct_import(self):
        async def scenario():
            llm = MagicMock()
            llm.generate = AsyncMock(return_value='{"command": "weather", "confidence": 0.9, "entities": {}}')
            bound = await _bound_capability(llm)
            await bound.parse("weather berlin", _vocabulary(), adapter="ollama")
            bound._host.get_capability.assert_called_once_with("ai.llm")
            llm.generate.assert_called_once()
            self.assertEqual(
                llm.generate.await_args.kwargs.get("adapter"), "ollama"
            )
        asyncio.run(scenario())

    def test_empty_text_returns_unresolved_without_llm(self):
        async def scenario():
            llm = MagicMock()
            llm.generate = AsyncMock()
            bound = await _bound_capability(llm)
            intent = await bound.parse("   ", _vocabulary(), adapter="ollama")
            self.assertIs(intent, UNRESOLVED)
            llm.generate.assert_not_called()
        asyncio.run(scenario())

    def test_empty_vocabulary_returns_unresolved(self):
        async def scenario():
            llm = MagicMock()
            llm.generate = AsyncMock()
            bound = await _bound_capability(llm)
            intent = await bound.parse("do something", [], adapter="ollama")
            self.assertIs(intent, UNRESOLVED)
            llm.generate.assert_not_called()
        asyncio.run(scenario())


class TestNormalization(unittest.TestCase):
    def _normalize(self, raw: str, vocabulary=None):
        return ParsingCapability._normalize(raw, vocabulary or _vocabulary())

    def test_plain_json_object(self):
        result = self._normalize('{"command": "send_email", "confidence": 0.7, "entities": {"to": "a@b.c"}}')
        self.assertEqual(result.command, "send_email")
        self.assertEqual(result.entities, {"to": "a@b.c"})

    def test_fenced_json_block(self):
        raw = '```json\n{"command": "weather", "confidence": 0.9, "entities": {"city": "berlin"}}\n```'
        result = self._normalize(raw)
        self.assertEqual(result.command, "weather")

    def test_json_wrapped_in_prose(self):
        raw = 'Sure! Here you go: {"command": "weather", "confidence": 0.8, "entities": {}}'
        result = self._normalize(raw)
        self.assertEqual(result.command, "weather")

    def test_unknown_command_is_unresolved(self):
        result = self._normalize('{"command": "hack_the_mainframe", "confidence": 1.0, "entities": {}}')
        self.assertEqual(result, UNRESOLVED)

    def test_command_outside_vocabulary_is_unresolved(self):
        result = self._normalize('{"command": "general_chat", "confidence": 1.0, "entities": {}}')
        self.assertEqual(result, UNRESOLVED)

    def test_non_entities_is_folded_to_empty(self):
        result = self._normalize('{"command": "weather", "confidence": 0.5, "entities": "oops"}')
        self.assertEqual(result.entities, {})

    def test_unparseable_is_unresolved(self):
        self.assertEqual(self._normalize("no json here"), UNRESOLVED)
        self.assertEqual(self._normalize(""), UNRESOLVED)


class TestFutureResolverExtension(unittest.TestCase):
    def test_resolver_present_when_llm_mounted(self):
        async def scenario():
            llm = MagicMock()
            bound = await _bound_capability(llm)
            self.assertIsNotNone(bound._resolve_if_available())
        asyncio.run(scenario())

    def test_no_resolver_without_llm_returns_unresolved(self):
        async def scenario():
            bound = await _bound_capability(None)
            self.assertIsNone(bound._resolve_if_available())
            intent = await bound.parse("weather berlin", _vocabulary(), adapter="ollama")
            self.assertEqual(intent, UNRESOLVED)
        asyncio.run(scenario())

    def test_parse_signature_does_not_change_with_resolver_selection(self):
        """A future fallback resolver slots into _resolve_if_available; the
        public parse contract (text + vocabulary -> ParsedIntent) is stable and
        the ai.llm adapter is selected explicitly per operation."""
        import inspect

        params = inspect.signature(ParsingCapability.parse).parameters
        self.assertEqual(
            list(params), ["self", "text", "vocabulary", "adapter", "model"]
        )


class TestDecoupling(unittest.TestCase):
    def test_no_chatrole_telegram_coupling(self):
        source = (PARSING_DIR / "capability.py").read_text().lower()
        for token in ("chatrole", "telegram"):
            self.assertNotIn(token, source, f"ai.parsing must not reference {token}")
        # The capability must never import workload/orchestration/role modules.
        for silo in ("vox.workloads", "vox.orchestration", "vox.roles"):
            self.assertNotIn(silo, source)

    def test_no_direct_ai_llm_import(self):
        source = (PARSING_DIR / "capability.py").read_text()
        self.assertNotIn("import vox.capabilities.ai.llm", source)
        self.assertNotIn("vox.capabilities.ai.llm", source)

    def test_no_concrete_llm_adapter_import(self):
        """ai.parsing consumes the ai.llm port and never a concrete adapter."""
        source = (PARSING_DIR / "capability.py").read_text().lower()
        for token in ("ollama", "gemini", "adapters", "gemini-2.5-flash"):
            self.assertNotIn(
                token, source, f"ai.parsing must not reference {token}"
            )


if __name__ == "__main__":
    unittest.main()