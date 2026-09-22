"""LLMAdapter — abstract contract for ai.llm provider backends.

Mirror of the comm.gateway adapter pattern for the ai.llm port:

  * provider-specific protocol, endpoint, SDK, model and authentication
    details live inside the adapter, never in the port;
  * configuration and credentials come from the adapter's own ``config.yml``,
    aggregated into the ai.llm ``CapabilityContract`` (see
    ``LLMCapability.load_contract``);
  * ``is_configured(config)`` declares availability for a bound workload
    (provisioning), never runtime selection;
  * adapter instances are owned by the bound capability (per workload) and
    never shared between workloads;
  * lifecycle (``start``/``shutdown``) is part of the contract, not duck-typed.

Adapter identity (``ADAPTER_ID``) and model selection are separate dimensions:
a caller selects the adapter per operation through the port's explicit
``adapter`` keyword, and may override the model per operation. Model defaults
reside in the adapter's own configuration and are resolved by the adapter
(``resolve_model``).
"""

from abc import ABC, abstractmethod

from ..models import LLMChatMessage, LLMGenerationResult


class LLMAdapter(ABC):
    """Provider-agnostic contract every ai.llm concrete backend implements."""

    ADAPTER_ID: str = ""

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        """Whether this adapter should be mounted for the given bound workload.

        Availability only — ``is_configured`` says nothing about which adapter a
        particular operation uses. The base implementation configures every
        adapter; credential-gated backends (e.g. Gemini) override it.
        """
        return True

    @abstractmethod
    def resolve_model(self, model: str | None) -> str:
        """Return the effective text-generation model for an operation.

        ``model`` is an explicit per-operation override; ``None`` means the
        adapter's own configured default model.
        """

    @abstractmethod
    async def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMGenerationResult: ...

    @abstractmethod
    async def chat(
        self,
        *,
        model: str | None,
        messages: list[LLMChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult: ...

    @abstractmethod
    async def generate_vision(
        self,
        *,
        model: str | None,
        system: str,
        prompt: str,
        image_bytes: bytes,
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult: ...

    async def start(self) -> None:
        """Allocate provider runtime resources for this adapter instance.

        No-op by default; adapters with long-lived clients allocate them here
        and release them in ``shutdown``.
        """
        return

    async def shutdown(self) -> None:
        """Release runtime resources this adapter instance acquired.

        Each bound workload shuts down only the adapter instances it owns.
        The base implementation is a no-op.
        """
        return