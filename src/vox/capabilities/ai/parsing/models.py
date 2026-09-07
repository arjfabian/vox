"""ai.parsing — normalized intent data model.

Immutable dataclasses describing the command vocabulary a caller supplies and
the normalized parsed intent the capability returns. The result model is the
stable extension point: a future cosine/semantic resolver can be added without
changing what ``parse`` returns to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One command the caller wants the natural-language input resolved to.

    Name and a short description used to disambiguate between similar commands.
    """

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    """Normalized result of intent resolution.

    ``command`` is one of the supplied vocabulary names, or ``""`` (empty
    string) when no command could be resolved — the sentinel for "no intent",
    which the caller maps to its own default (e.g. a conversational fallback).

    ``confidence`` is in ``[0.0, 1.0]``; ``0.0`` accompanies an unresolved
    (empty) command. ``entities`` holds key/value pairs extracted from the
    input (``command``-specific), empty when none were found.
    """

    command: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
