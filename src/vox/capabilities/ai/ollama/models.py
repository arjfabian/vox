"""
Ollama capability models.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OllamaMessage:
    role: str
    content: str


@dataclass(slots=True)
class OllamaOptions:
    temperature: float
    num_predict: int


@dataclass(slots=True)
class OllamaChatRequest:
    model: str
    messages: list[OllamaMessage]
    stream: bool = False
    format: str | None = None
    options: OllamaOptions | None = None


@dataclass(slots=True)
class OllamaChatResponse:
    content: str
    raw: dict[str, Any]