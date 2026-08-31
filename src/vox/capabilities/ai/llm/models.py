"""ai.llm — data model definitions.

Immutable dataclasses for request construction, response parsing, sanitization
results, and cache entries across the full LLM pipeline.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LLMChatMessage:
    role: str
    content: str
    images: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMGenerationOptions:
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass(slots=True)
class LLMChatRequest:
    model: str
    messages: list[LLMChatMessage]
    stream: bool = False
    format: str | None = None
    options: LLMGenerationOptions | None = None


@dataclass(slots=True)
class LLMGenerationResult:
    content: str
    model: str
    tokens_generated: int = 0
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class SanitizedPrompt:
    text: str
    original_length: int
    trimmed_length: int
    tokens_saved: int
    truncated: bool
