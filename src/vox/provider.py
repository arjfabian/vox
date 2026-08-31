"""Capability-subsystem interface contracts.

These protocols are the explicit interfaces through which the Capability
subsystem consumes its operating environment. Capabilities and their binding
code depend only on these abstractions, never on the concrete Workload or
Orchestrator implementations.
"""

from __future__ import annotations

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

    async def dispatch_inbound_message(self, source: str, payload: dict) -> None: ...


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

    # -- safe path resolution -------------------------------------------------
    def get_safe_path(self, sub_dir: str, filename: str) -> Path: ...

    # -- lifecycle / degradation -----------------------------------------------
    def mark_degraded(self) -> None: ...

    def register_capability_command(self, name: str, handler: Any) -> None: ...
