"""Request router — model resolution for LLM inference.

Validates that a model name is configured and returns it.
The capability's YAML declares the default model; workloads may
override it.  If the resolved model cannot be used, the
capability degrades — no silent substitution.
"""

from dataclasses import dataclass

from .models import SanitizedPrompt


class ComplexityClass:
    PRIMARY = "primary"


@dataclass
class RoutingDecision:
    tier: str
    reason: str
    model: str


class Router:

    def __init__(
        self,
        model: str,
    ) -> None:
        if not model:
            raise ValueError("model is required — no silent fallback")
        self._model = model

    def decide(
        self,
        sanitized: SanitizedPrompt,
        system: str = "",
    ) -> RoutingDecision:
        return RoutingDecision(
            tier=ComplexityClass.PRIMARY,
            reason="resolved model",
            model=self._model,
        )
