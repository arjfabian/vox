"""ai.llm.adapters — concrete LLM provider backends behind the ai.llm port."""

from .base import LLMAdapter
from .gemini import GeminiAdapter
from .ollama import OllamaAdapter

# Adapter registry. The ai.llm port instantiates every registered adapter for a
# bound workload and skips those not configured for it; adapter-specific
# configuration lives in each adapter's ``config.yml``, aggregated into the
# ai.llm's CapabilityContract.
ADAPTER_REGISTRY: dict[str, type[LLMAdapter]] = {
    cls.ADAPTER_ID: cls for cls in (OllamaAdapter, GeminiAdapter)
}

__all__ = [
    "ADAPTER_REGISTRY",
    "GeminiAdapter",
    "LLMAdapter",
    "OllamaAdapter",
]