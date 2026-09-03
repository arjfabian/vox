"""Capability-subsystem interface contracts.

These protocols are the explicit interfaces through which the Capability
subsystem consumes its operating environment. Capabilities and their binding
code depend only on these abstractions, never on the concrete Workload or
Orchestrator implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from vox.security import WorkloadVault


@runtime_checkable
class CapabilityProviderProtocol(Protocol):
    """Provider interface a workload consumes from the orchestrator.

    Replaces the concrete orchestrator reference to break bidirectional
    coupling between the workload/capability side and the orchestrator.
    Only exposes what workloads actually need.
    """

    def get_capability_instance(self, cap_id: str) -> object | None: ...

    def get_children(self, workload_id: str) -> list: ...

    async def dispatch_inbound_message(self, source: str, payload: dict) -> bool: ...


@runtime_checkable
class CapabilityHostProtocol(Protocol):
    """Explicit services the Workload runtime supplies to capability code.

    The CapabilityBinder and VOXBoundCapability depend only on this interface
    when they need behaviour from the workload. It deliberately exposes a
    narrow, capability-shaped surface (configuration, secret backend, provider,
    safe-path, capability registry, degradation and command registration)
    rather than the whole ``VOXWorkload`` object.
    """

    # -- identity / configuration --------------------------------------------
    @property
    def name(self) -> str: ...

    @property
    def id(self) -> str | None: ...

    @property
    def dir(self) -> Path: ...

    @property
    def config(self) -> dict[str, Any]: ...

    # -- secret backend ------------------------------------------------------
    @property
    def vault(self) -> WorkloadVault | None: ...

    @vault.setter
    def vault(self, value: WorkloadVault | None) -> None: ...

    # -- capability provider / registry ---------------------------------------
    @property
    def capability_provider(self) -> CapabilityProviderProtocol | None: ...

    @property
    def capabilities(self) -> dict[str, Any]: ...

    def get_capability(self, cap_id: str) -> Any | None: ...

    def register_capability(self, cap_id: str, bound: Any) -> None: ...

    # -- inbound message dispatch ----------------------------------------------
    async def dispatch_inbound(self, source: str, payload: dict) -> bool:
        """Deliver an inbound message to the container's mounted workload(s).

        Returns ``True`` when a dispatch target actually received the message and
        ``False`` when there is no provider, the guardrail rejected the payload,
        or no matching workload was mounted. This is the narrow capability-facing
        entry point for inbound traffic; a capability never reaches for the whole
        ``capability_provider``.
        """

    # -- safe path resolution -------------------------------------------------
    def get_safe_path(self, sub_dir: str, filename: str) -> Path: ...

    # -- lifecycle / degradation -----------------------------------------------
    def mark_degraded(self) -> None: ...

    def disable_roles(
        self,
        reason_for: Callable[[Any], str | None],
    ) -> dict[str, str]:
        """Remove the role objects for which ``reason_for`` returns a reason.

        ``reason_for`` is applied to each role object; a non-``None`` return
        disables that role and is recorded as its reason. Returns
        ``{role_name: reason}`` for the roles disabled. The role registry is
        never exposed: the host performs the mutation itself.
        """

    def register_capability_command(self, name: str, handler: Any) -> None: ...
