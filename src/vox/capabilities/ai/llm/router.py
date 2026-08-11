"""Request router — classifies task complexity for backend selection.

Rules-based classification: prompts exceeding a character threshold
or containing multiple complexity-trigger keywords are routed to
the cloud model. All other traffic stays on the local backend.

Extensible by swapping ``decide()`` for an ML-based classifier.
"""

from dataclasses import dataclass

from .models import SanitizedPrompt


class ComplexityClass:
    SIMPLE = "local"
    COMPLEX = "cloud"


@dataclass
class RoutingDecision:
    backend: str
    reason: str
    model: str


class Router:
    # noqa: RUF012 — immutable frozenset-like set; intentionally shared, never mutated.
    _COMPLEX_KEYWORDS = {
        "reason",
        "explain",
        "analyze",
        "compare",
        "contrast",
        "synthesize",
        "evaluate",
        "critique",
        "summarize",
        "translate",
        "code",
        "function",
        "algorithm",
        "debug",
    }

    def __init__(
        self,
        local_model: str = "llama3.2:3b",
        cloud_model: str = "gpt-4o",
        complexity_threshold: int = 512,
        force_local: bool = False,
        force_cloud: bool = False,
    ) -> None:
        self._local_model = local_model
        self._cloud_model = cloud_model
        self._complexity_threshold = complexity_threshold
        self._force_local = force_local
        self._force_cloud = force_cloud

    def decide(
        self,
        sanitized: SanitizedPrompt,
        system: str = "",
    ) -> RoutingDecision:
        if self._force_cloud:
            return RoutingDecision(
                backend=ComplexityClass.COMPLEX,
                reason="forced to cloud",
                model=self._cloud_model,
            )
        if self._force_local:
            return RoutingDecision(
                backend=ComplexityClass.SIMPLE,
                reason="forced to local",
                model=self._local_model,
            )

        combined = f"{system} {sanitized.text}"

        if len(combined) > self._complexity_threshold:
            return RoutingDecision(
                backend=ComplexityClass.COMPLEX,
                reason=f"length {len(combined)} > threshold {self._complexity_threshold}",
                model=self._cloud_model,
            )

        words = set(combined.lower().split())
        matched = words & self._COMPLEX_KEYWORDS
        if len(matched) >= 2:
            return RoutingDecision(
                backend=ComplexityClass.COMPLEX,
                reason=f"complexity keywords matched: {matched}",
                model=self._cloud_model,
            )

        return RoutingDecision(
            backend=ComplexityClass.SIMPLE,
            reason="task is simple",
            model=self._local_model,
        )
