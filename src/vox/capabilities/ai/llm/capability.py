"""ai.llm — Unified local/cloud LLM inference capability.

Routes every inbound payload through the sequential pipeline:

  1. Sanitize      — strip control chars, boilerplate, collapse JSON
  2. Cache Check   — SHA-256 exact match lookup with TTL
  3. RAG Retrieval — FTS5 search against agent private memory.db
  4. Router        — complexity classification (local vs. cloud model)
  5. Generation    — dispatch to selected LLM backend
  6. Cache Commit  — store response for future cache hits

Vision inference bypasses the pipeline and always routes to
the local vision model.
NLU intent parsing is also provided here as ``parse_intent()``.
"""

import json
import os
import re
import base64
from pathlib import Path

from vox.capabilities.base import VOXCapability

from .client import LLMClient
from .models import LLMChatMessage, LLMChatRequest, LLMGenerationOptions
from .sanitizer import sanitize
from .cache import SemanticCache
from .rag import RAGRetriever
from .router import Router, ComplexityClass


class LLMCapability(VOXCapability):

    CAPABILITY_NAME = "ai.llm"

    PARAMS = {
        "LLM_API_BASE_URL":     ["API base URL", "http://localhost:11434"],
        "LLM_MODEL_NAME":       ["Primary model", "llama3.2:3b"],
        "LLM_VISION_MODEL_NAME": ["Vision model", "llava"],
        "LLM_TEMPERATURE":      ["Temperature (0.0-1.0)", 0.7],
        "LLM_MAX_TOKENS":       ["Max generated tokens", 2048],
        "LLM_TIMEOUT":          ["Request timeout (s)", 60.0],
        "LLM_CACHE_ENABLED":    ["Enable semantic cache", True],
        "LLM_CACHE_TTL":        ["Cache TTL (s)", 3600],
        "LLM_RAG_ENABLED":      ["Enable RAG injection", True],
        "LLM_RAG_MAX_SNIPPETS": ["Max RAG snippets", 5],
        "LLM_MAX_INPUT_TOKENS": ["Max prompt chars", 16384],
        "LLM_FORCE_LOCAL":      ["Force local backend", False],
        "LLM_CACHE_DB_PATH":    ["Absolute or project-root path to shared cache DB", "var/cache/ai.llm/llm_cache.db"],
        "LLM_FORCE_CLOUD":      ["Force cloud backend", False],
    }

    _client: LLMClient
    _cache: SemanticCache
    _rag: RAGRetriever
    _router: Router

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        client = LLMClient(
            base_url=cls.PARAMS["LLM_API_BASE_URL"][1],
            timeout=5.0,
        )
        result = await client.is_healthy()
        await client.aclose()
        return result

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def generate(
        self,
        system: str,
        prompt: str,
        *,
        model: str | None = None,
        json_mode: bool = False,
        context_token: str | None = None,
    ) -> str:
        # 1. Sanitize
        sanitized = await sanitize(
            prompt,
            max_input_chars=int(self.LLM_MAX_INPUT_TOKENS),
        )
        if sanitized.tokens_saved > 0:
            self.log(f"Sanitizer saved {sanitized.tokens_saved} tokens")

        # 2. Cache check
        cache_enabled = bool(self.LLM_CACHE_ENABLED)
        effective_model = model or self.LLM_MODEL_NAME
        if cache_enabled:
            cached = await self._cache.lookup(system, sanitized.text, effective_model)
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
                    f"{system}\n\nRelevant context from agent memory:\n{context_block}"
                )
                self.log(f"Injected {len(snippets)} RAG snippet(s)")

        # 4. Router
        if model is None:
            decision = self._router.decide(sanitized, system=augmented_system)
            effective_model = decision.model
            self.log(f"Router: {decision.reason} → {decision.backend} [{effective_model}]")

        # 5. Generation
        result = await self._client.generate(
            model=effective_model,
            prompt=sanitized.text,
            system=augmented_system,
            temperature=float(self.LLM_TEMPERATURE),
            max_tokens=int(self.LLM_MAX_TOKENS),
            json_mode=json_mode,
        )

        # 6. Cache commit
        if cache_enabled:
            await self._cache.store(system, sanitized.text, effective_model, result.content)

        return result.content

    async def chat(
        self,
        messages: list[LLMChatMessage],
        *,
        model: str | None = None,
    ) -> str:
        effective = model or self.LLM_MODEL_NAME
        request = LLMChatRequest(
            model=effective,
            messages=messages,
            stream=False,
            options=LLMGenerationOptions(
                temperature=float(self.LLM_TEMPERATURE),
                max_tokens=int(self.LLM_MAX_TOKENS),
            ),
        )
        result = await self._client.chat(request)
        return result.content

    async def generate_vision(
        self,
        system: str,
        prompt: str,
        image_path: str,
    ) -> str:
        self.log(f"Generating vision response using [{self.LLM_VISION_MODEL_NAME}]")
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        request = LLMChatRequest(
            model=self.LLM_VISION_MODEL_NAME,
            messages=[
                LLMChatMessage(role="system", content=system),
                LLMChatMessage(role="user", content=prompt, images=[image_b64]),
            ],
            stream=False,
            options=LLMGenerationOptions(
                temperature=float(self.LLM_TEMPERATURE),
                max_tokens=int(self.LLM_MAX_TOKENS),
            ),
        )
        result = await self._client.chat(request)
        return result.content

    # ------------------------------------------------------------------
    # NLU intent parsing
    # ------------------------------------------------------------------

    async def parse_intent(
        self,
        text: str,
        command_signatures: dict[str, str],
        model: str | None = None,
    ) -> dict:
        """Classify a user message into a structured intent.

        Args:
            text: Raw user message.
            command_signatures: ``{command_name: description}`` mapping.
            model: Optional model override (defaults to instance model).

        Returns:
            ``{"command": str, "confidence": float, "entities": dict}``
        """
        lines = [
            "You are a command classifier for a multi-agent system.",
            "Match the user message to one command, or 'general_chat' "
            "if it is conversational.",
            "",
            "Available commands:",
        ]
        for cmd, desc in sorted(command_signatures.items()):
            lines.append(f"- {cmd}: {desc}")
        lines += [
            "- general_chat: casual conversation, no specific command.",
            "",
            "Rules:",
            "- You MUST return ONLY valid JSON. No prose, no explanations, no greetings.",
            "- If the user message does not clearly match a specific command, "
            "use 'general_chat' with confidence 1.0.",
            "- 'entities' contains extracted values. For general_chat, include "
            'the original user text as "user_message".',
            "",
            'Schema: {"command": "string", "confidence": float, '
            '"entities": {"key": "value"}}',
        ]
        system = "\n".join(lines)

        raw = await self.generate(
            system=system,
            prompt=text,
            model=model or self.LLM_MODEL_NAME,
        )

        valid = list(command_signatures.keys()) + ["general_chat"]
        return LLMCapability._parse_intent_response(raw, valid)

    @staticmethod
    def _parse_intent_response(raw: str, valid_commands: list[str]) -> dict:
        result: dict = {"command": "general_chat", "confidence": 0.0, "entities": {}}
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> None:
        self._client = LLMClient(
            base_url=self.LLM_API_BASE_URL,
            timeout=float(self.LLM_TIMEOUT),
        )

        cache_param = self.LLM_CACHE_DB_PATH
        cache_db_path = Path(cache_param) if Path(cache_param).is_absolute() else Path(os.getcwd()) / cache_param
        cache_db_path.parent.mkdir(parents=True, exist_ok=True)

        self._cache = SemanticCache(
            db_path=str(cache_db_path),
            ttl=int(self.LLM_CACHE_TTL),
        )
        self._rag = RAGRetriever(
            max_snippets=int(self.LLM_RAG_MAX_SNIPPETS),
        )
        self._router = Router(
            local_model=self.LLM_MODEL_NAME,
            cloud_model=self.LLM_MODEL_NAME,
            force_local=bool(self.LLM_FORCE_LOCAL),
            force_cloud=bool(self.LLM_FORCE_CLOUD),
        )

    async def shutdown(self) -> None:
        if hasattr(self, "_client"):
            await self._client.aclose()
        if hasattr(self, "_cache"):
            await self._cache.close()
        if hasattr(self, "_rag"):
            await self._rag.close()
