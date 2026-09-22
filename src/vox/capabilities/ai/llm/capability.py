"""ai.llm — provider-agnostic LLM inference capability (port).

Exposes text generation (``generate``), chat completion (``chat``) and vision
inference (``generate_vision``) behind a provider-neutral interface. Concrete
backends are explicit adapters under ``adapters/``, each owning its own
``config.yml``, credentials and provider protocol (the canonical pattern is
established by ``comm.gateway``).

  * The port contains no provider-specific implementation detail and performs
    no provider selection.
  * Adapter availability is decided per bound workload at boot time via each
    adapter's ``is_configured()`` (provisioning, never selection).
  * The adapter used by a particular operation is selected explicitly at
    execution time through the required ``adapter`` keyword.
  * Model selection is independent from adapter identity: a per-operation
    ``model`` override is applied by the selected adapter on top of its own
    configured defaults.

The generation pipeline (sanitize -> semantic cache -> RAG injection ->
generation -> cache commit) is port-wide and provider-agnostic; the selected
adapter only performs the generation step. Vision inference bypasses the
pipeline and consumes raw image bytes (no intermediate files). NLU intent
parsing is also provided here as ``parse_intent()``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from vox.capabilities.base import (
    CapabilityContract,
    VOXCapability,
    load_capability_yaml,
    load_config_yml,
)

from .adapters import ADAPTER_REGISTRY
from .adapters.base import LLMAdapter
from .cache import SemanticCache
from .models import LLMChatMessage
from .rag import RAGRetriever
from .sanitizer import sanitize

logger = logging.getLogger(__name__)


class LLMAdapterUnavailableError(RuntimeError):
    """Raised when an operation selects an adapter that is not mounted."""


class LLMCapability(VOXCapability):
    CAPABILITY_NAME = "ai.llm"

    _adapters: dict[str, LLMAdapter]
    _cache: SemanticCache
    _rag: RAGRetriever

    # --------------------------------------------------------------------------
    # Contract loading — aggregate adapter config.yml files
    # --------------------------------------------------------------------------

    @classmethod
    def load_contract(
        cls,
        capability_file: Path,
    ) -> CapabilityContract | None:
        """Load the ai.llm contract by merging per-adapter config.yml files.

        The port's own ``capability.yml`` provides the provider-neutral pipeline
        params. Each adapter directory contains a ``config.yml`` with ``params``
        and ``secrets`` sections merged into the single CapabilityContract —
        the comm.gateway aggregation mechanism.
        """
        contract = load_capability_yaml(capability_file)

        if contract is None:
            return None

        adapter_dir = capability_file.parent / "adapters"

        if adapter_dir.is_dir():
            for adapter_entry in sorted(adapter_dir.iterdir()):
                config_yml = adapter_entry / "config.yml"

                if not config_yml.is_file():
                    continue

                params, secrets = load_config_yml(config_yml)

                overlap = set(params) & set(contract.secrets)
                if overlap:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"secret(s) already in contract: {sorted(overlap)}"
                    )

                overlap = set(secrets) & set(contract.params)
                if overlap:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"param(s) already in contract: {sorted(overlap)}"
                    )

                conflict = set(params) & set(contract.params)
                if conflict:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"param(s): {sorted(conflict)}"
                    )

                conflict = set(secrets) & set(contract.secrets)
                if conflict:
                    raise ValueError(
                        f"Adapter '{adapter_entry.name}' config.yml redeclares "
                        f"secret(s): {sorted(conflict)}"
                    )

                contract.params.update(params)
                contract.secrets.update(secrets)

        cls._contract = contract
        return contract

    # --------------------------------------------------------------------------
    # Adapter parameter introspection
    # --------------------------------------------------------------------------

    @classmethod
    def adapter_param_specs(cls) -> dict[str, dict[str, list[Any]]]:
        """Per-adapter param+secret specs from adapter config.yml files."""
        adapter_dir = Path(__file__).resolve().parent / "adapters"
        specs: dict[str, dict[str, list[Any]]] = {}
        for adapter_id in ADAPTER_REGISTRY:
            config_yml = adapter_dir / adapter_id / "config.yml"
            if not config_yml.is_file():
                specs[adapter_id] = {}
                continue
            params, secrets = load_config_yml(config_yml)
            specs[adapter_id] = {
                **{
                    name: [meta.description, meta.default]
                    for name, meta in params.items()
                },
                **{name: [meta.description, ""] for name, meta in secrets.items()},
            }
        return specs

    # --------------------------------------------------------------------------
    # Health
    # --------------------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        # The port is offline-checkable without probing any provider;
        # provider readiness is an adapter concern.
        return True

    # --------------------------------------------------------------------------
    # Adapter availability (provisioning per bound workload)
    # --------------------------------------------------------------------------

    async def _build_adapters(self) -> dict[str, LLMAdapter]:
        """Instantiate this bound's own configured adapters.

        Each adapter is built from this bound's own params and injected Vault
        secrets; an adapter is skipped when it is not configured for this
        workload (e.g. its credential was not injected). The instances belong
        exclusively to this bound capability/workload and are never shared.
        """
        adapters: dict[str, LLMAdapter] = {}
        adapter_dir = Path(__file__).resolve().parent / "adapters"

        for adapter_id, adapter_cls in ADAPTER_REGISTRY.items():
            config_yml = adapter_dir / adapter_id / "config.yml"

            if not config_yml.is_file():
                logger.warning(
                    "Adapter '%s' has no config.yml — skipping",
                    adapter_id,
                )
                continue

            params, secrets = load_config_yml(config_yml)

            adapter_keys = set(params) | set(secrets)

            adapter_config = {
                key: value
                for key, value in {
                    **self._params,
                    **self._secrets,
                }.items()
                if key in adapter_keys
            }

            if not adapter_cls.is_configured(adapter_config):
                continue

            adapters[adapter_id] = adapter_cls(adapter_config)

        return adapters

    def available_adapters(self) -> list[str]:
        """Adapter ids provisioned for this bound workload.

        Availability only — it says nothing about which adapter a particular
        operation uses.
        """
        return sorted(self._adapters)

    def _adapter(self, adapter_id: str) -> LLMAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise LLMAdapterUnavailableError(
                f"adapter '{adapter_id}' is not available for this workload"
            )
        return adapter

    # --------------------------------------------------------------------------
    # Public operations — the adapter is selected per operation
    # --------------------------------------------------------------------------

    async def generate(
        self,
        system: str,
        prompt: str,
        *,
        adapter: str,
        model: str | None = None,
        json_mode: bool = False,
        context_token: str | None = None,
    ) -> str:
        adp = self._adapter(adapter)

        # 1. Sanitize
        sanitized = await sanitize(
            prompt,
            max_input_chars=int(self.LLM_MAX_INPUT_TOKENS),
        )
        if sanitized.tokens_saved > 0:
            self.log(f"Sanitizer saved {sanitized.tokens_saved} tokens")

        # 2. Cache check (adapter identity and model are part of the key)
        cache_enabled = bool(self.LLM_CACHE_ENABLED)
        effective_model = adp.resolve_model(model)
        if cache_enabled:
            cached = await self._cache.lookup(
                system, sanitized.text, effective_model, adapter
            )
            if cached is not None:
                self.log("Cache HIT — returning cached response")
                return cached.content

        # 3. RAG context injection
        augmented_system = system
        if bool(self.LLM_RAG_ENABLED) and context_token:
            snippets = await self._rag.retrieve(context_token, sanitized.text)
            if snippets:
                context_block = "\n".join(f"- {s}" for s in snippets)
                augmented_system = (
                    f"{system}\n\n"
                    "Relevant context from workload memory:"
                    f"\n{context_block}"
                )
                self.log(f"Injected {len(snippets)} RAG snippet(s)")

        # 4. Generation
        result = await adp.generate(
            model=effective_model,
            system=augmented_system,
            prompt=sanitized.text,
            temperature=float(self.LLM_TEMPERATURE),
            max_tokens=int(self.LLM_MAX_TOKENS),
            json_mode=json_mode,
        )

        # 5. Cache commit
        if cache_enabled:
            await self._cache.store(
                system, sanitized.text, effective_model, adapter, result.content
            )

        return result.content

    async def chat(
        self,
        messages: list[LLMChatMessage],
        *,
        adapter: str,
        model: str | None = None,
    ) -> str:
        adp = self._adapter(adapter)
        result = await adp.chat(
            model=model,
            messages=messages,
            temperature=float(self.LLM_TEMPERATURE),
            max_tokens=int(self.LLM_MAX_TOKENS),
        )
        return result.content

    async def generate_vision(
        self,
        system: str,
        prompt: str,
        image_bytes: bytes,
        *,
        adapter: str,
        model: str | None = None,
    ) -> str:
        adp = self._adapter(adapter)
        self.log(
            f"Generating vision response via adapter [{adapter}] "
            f"({len(image_bytes)} bytes)"
        )
        result = await adp.generate_vision(
            model=model,
            system=system,
            prompt=prompt,
            image_bytes=image_bytes,
            temperature=float(self.LLM_TEMPERATURE),
            max_tokens=int(self.LLM_MAX_TOKENS),
        )
        return result.content

    # --------------------------------------------------------------------------
    # NLU intent parsing
    # --------------------------------------------------------------------------

    async def parse_intent(
        self,
        text: str,
        command_signatures: dict[str, str],
        *,
        adapter: str,
        model: str | None = None,
    ) -> dict:
        """Classify a user message into a structured intent.

        Args:
            text: Raw user message.
            command_signatures: ``{command_name: description}`` mapping.
            adapter: The ai.llm adapter to generate with.
            model: Optional per-operation model override.

        Returns:
            ``{"command": str, "confidence": float, "entities": dict}``
        """
        lines = [
            "You are a command classifier for a multi-workload system.",
            (
                "Match the user message to one command, or 'general_chat' "
                "if it is conversational."
            ),
            "",
            "Available commands:",
        ]
        for cmd, desc in sorted(command_signatures.items()):
            lines.append(f"- {cmd}: {desc}")
        lines += [
            "- general_chat: casual conversation, no specific command.",
            "",
            "Rules:",
            (
                "- You MUST return ONLY valid JSON. "
                "No prose, no explanations, no greetings."
            ),
            (
                "- If the user message does not clearly match "
                "a specific command, use 'general_chat' "
                "with confidence 1.0."
            ),
            (
                "- 'entities' contains extracted values. "
                "For general_chat, include the original "
                'user text as "user_message".'
            ),
            "",
            (
                'Schema: {"command": "string", "confidence": float, '
                '"entities": {"key": "value"}}'
            ),
        ]
        system = "\n".join(lines)

        raw = await self.generate(
            system=system,
            prompt=text,
            adapter=adapter,
            model=model,
        )

        valid = list(command_signatures.keys()) + ["general_chat"]
        return LLMCapability._parse_intent_response(raw, valid)

    @staticmethod
    def _parse_intent_response(raw: str, valid_commands: list[str]) -> dict:
        result: dict = {
            "command": "general_chat",
            "confidence": 0.0,
            "entities": {},
        }
        if not raw:
            return result

        data = None
        stripped = raw.strip()

        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                pass

        if data is None:
            block_match = re.search(
                r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", stripped, re.DOTALL
            )
            if block_match:
                try:
                    data = json.loads(block_match.group(1))
                except json.JSONDecodeError:
                    pass

        if data is None:
            brace_match = re.search(r"\{.*\}", stripped, re.DOTALL)
            if brace_match:
                try:
                    data = json.loads(brace_match.group(0))
                except json.JSONDecodeError:
                    pass

        if data is None:
            return result

        command = str(data.get("command", "general_chat")).strip().lower()
        if command not in valid_commands:
            return result

        result["command"] = command
        result["confidence"] = float(data.get("confidence", 0.0))
        entities = data.get("entities", {}) or {}
        args = data.get("args", {}) or {}
        if args:
            entities.update(args)
        result["entities"] = entities
        return result

    # --------------------------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------------------------

    async def boot(self) -> None:
        # This bound owns its adapter instances; never shared across workloads.
        self._adapters = await self._build_adapters()

        cache_param = self.LLM_CACHE_DB_PATH
        cache_db_path = (
            Path(cache_param)
            if Path(cache_param).is_absolute()
            else Path(os.getcwd()) / cache_param
        )
        cache_db_path.parent.mkdir(parents=True, exist_ok=True)

        self._cache = SemanticCache(
            db_path=str(cache_db_path),
            ttl=int(self.LLM_CACHE_TTL),
        )
        await self._cache.init_db()
        self._rag = RAGRetriever(
            max_snippets=int(self.LLM_RAG_MAX_SNIPPETS),
        )

        # Lifecycle is part of the adapter contract, not duck-typed.
        for adapter in self._adapters.values():
            await adapter.start()

        if self._adapters:
            self.ok(
                "ai.llm ready with adapters: "
                f"{', '.join(self.available_adapters())}"
            )
        else:
            self.ok("No adapters configured — ai.llm idle")

    async def shutdown(self) -> None:
        for adapter in getattr(self, "_adapters", {}).values():
            await adapter.shutdown()

        cache = getattr(self, "_cache", None)
        if cache is not None:
            await cache.close()

        rag = getattr(self, "_rag", None)
        if rag is not None:
            await rag.close()