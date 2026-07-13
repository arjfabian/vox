from typing import Protocol, runtime_checkable


@runtime_checkable
class CapabilityProviderProtocol(Protocol):
    """Slim provider interface for VOXAgent to consume from VOXOrchestrator.

    Replaces the concrete orchestrator reference to break bidirectional
    coupling. Only exposes what agents actually need.
    """

    def get_capability_instance(self, cap_id: str) -> object | None:
        ...

    def get_children(self, agent_id: str) -> list:
        ...

    async def dispatch_inbound_message(
        self, source: str, payload: dict
    ) -> None:
        ...
