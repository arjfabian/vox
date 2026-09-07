"""ai.parsing — natural-language command/intent resolution capability.

Owns intent resolution. Kept generic and capability-oriented: it is decoupled
from any particular channel, role, workload, or fleet topology. Given a user
message and a caller-supplied command vocabulary, it returns a normalized
``ParsedIntent`` (command, confidence, entities).

The intended-resolution path queries the generic ``ai.llm`` capability (reached
exclusively via the capability host ``get_capability``), which remains the
provider and does not own parsing semantics. ``ai.parsing`` owns the prompt
assembly, the vocabulary contract, and the JSON response normalization.

The public ``parse`` entry point is the stable extension point: a future
cosine/semantic resolver fallback can be added inside the resolution step
without changing this capability's public contract or the caller's.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vox.capabilities.base import VOXCapability

from .models import CommandSpec, ParsedIntent

# Default sentinel returned when no vocabulary command matches (empty command
# follows the repository convention that ``""`` means "unconfigured/inactive").
UNRESOLVED: ParsedIntent = ParsedIntent(command="", confidence=0.0, entities={})

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"(\{.*\})", re.DOTALL)


class ParsingUnavailableError(RuntimeError):
    """Raised when no intent resolver (e.g. the ai.llm provider) is mounted."""


class ParsingCapability(VOXCapability):
    CAPABILITY_NAME = "ai.parsing"

    _llm: Any | None = None

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    async def health_check(cls) -> bool:
        return True

    async def boot(self) -> None:
        self._llm = self.get_capability("ai.llm")

    async def shutdown(self) -> None:
        self._llm = None

    # -- public operation -----------------------------------------------------

    async def parse(
        self,
        text: str,
        vocabulary: list[CommandSpec],
        *,
        model: str | None = None,
    ) -> ParsedIntent:
        """Resolve a user message into a normalized ``ParsedIntent``.

        Args:
            text: Raw user message.
            vocabulary: Caller-supplied command vocabulary (name + description).
            model: Optional ai.llm model override.

        Returns:
            A ``ParsedIntent`` whose ``command`` is one of the vocabulary names,
            or ``""`` with ``confidence=0.0`` when no command matched.
        """
        if not text.strip():
            return UNRESOLVED
        if not vocabulary:
            return UNRESOLVED

        resolver = self._resolve_if_available()
        if resolver is None:
            self.warning("ai.llm provider unavailable; intent unresolved")
            return UNRESOLVED

        return await resolver(text, vocabulary, model=model)

    # -- resolution -----------------------------------------------------------

    def _resolve_if_available(self):
        """Return the active intent resolver, or ``None`` if none is mounted.

        A future cosine/semantic resolver fallback slots in here (returning its
        resolver before/alongside the LLM path) without changing ``parse``.
        """
        return self._resolve_with_llm if self._llm is not None else None

    async def _resolve_with_llm(
        self,
        text: str,
        vocabulary: list[CommandSpec],
        *,
        model: str | None = None,
    ) -> ParsedIntent:
        system = ParsingCapability._build_prompt(vocabulary)
        raw = await self._llm.generate(
            system=system,
            prompt=text,
            model=model,
            json_mode=True,
        )
        return ParsingCapability._normalize(raw, vocabulary)

    # -- prompt + normalization (shared by resolvers) -------------------------

    @staticmethod
    def _build_prompt(vocabulary: list[CommandSpec]) -> str:
        lines = [
            "You are a command classifier.",
            (
                "Resolve the user message into EXACTLY ONE of the available "
                "commands, or return an empty string command if it matches none."
            ),
            "",
            "Available commands:",
        ]
        for spec in vocabulary:
            lines.append(f"- {spec.name}: {spec.description}")
        lines += [
            "",
            "Rules:",
            (
                "- You MUST return ONLY valid JSON. No prose, no explanations, "
                "no greetings."
            ),
            (
                "- 'command' MUST be exactly one of the names above, or "
                '"" (empty string) if none match.'
            ),
            (
                "- 'entities' contains key/value pairs extracted from the "
                "input that are relevant to the chosen command."
            ),
            "",
            (
                'Schema: {"command": "string", "confidence": float, '
                '"entities": {"key": "value"}}'
            ),
        ]
        return "\n".join(lines)

    @staticmethod
    def _normalize(raw: str, vocabulary: list[CommandSpec]) -> ParsedIntent:
        valid = {spec.name for spec in vocabulary}
        data = ParsingCapability._extract_json(raw)
        if data is None:
            return UNRESOLVED

        command = str(data.get("command", "")).strip().lower()
        if command and command not in valid:
            return UNRESOLVED

        confidence = float(data.get("confidence", 0.0))
        entities = data.get("entities", {}) or {}
        if not isinstance(entities, dict):
            entities = {}
        return ParsedIntent(command=command, confidence=confidence, entities=entities)

    @staticmethod
    def _extract_json(raw: str) -> dict | None:
        """Best-effort JSON extraction from an LLM response (plain or fenced)."""
        if not raw:
            return None
        stripped = raw.strip()

        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(data, dict):
                    return data

        for pattern in (_JSON_BLOCK_RE, _JSON_OBJECT_RE):
            match = pattern.search(stripped)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return data
        return None
