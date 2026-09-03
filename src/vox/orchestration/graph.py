"""FleetGraph — in-memory fleet topology index.

Maintains indexed lookups of active, inactive, and degraded workloads, resolves
workload identities, and exposes the hierarchical topology of the running fleet.
"""

from __future__ import annotations

from typing import Any

from vox.observability import VOXForensicLogger


class FleetGraph:
    """Runtime index of the workload fleet.

    Provides read-only query methods over the three workload registries (active,
    inactive, degraded).
    """

    def __init__(
        self,
        active_workloads: dict[str, Any],
        inactive_workloads: dict[str, Any],
        degraded_workloads: dict[str, Any],
        logger: VOXForensicLogger,
    ) -> None:
        self._active_workloads = active_workloads
        self._inactive_workloads = inactive_workloads
        self._degraded_workloads = degraded_workloads
        self._logger = logger

    @property
    def all_workloads(self) -> dict[str, Any]:
        return {
            **self._active_workloads,
            **self._inactive_workloads,
            **self._degraded_workloads,
        }

    def resolve_workload_id(self, workload_name: str) -> str | None:
        """Find a workload UUID by display name (case-insensitive)."""
        for workload in self.all_workloads.values():
            if workload.name.lower() == workload_name.lower():
                return workload.id
        return None

    def resolve_workload(self, identifier: str) -> Any | None:
        """Look up a workload by UUID or name. Returns workload or None."""
        all_workloads = self.all_workloads
        workload = all_workloads.get(identifier)
        if workload:
            return workload
        workload_id = self.resolve_workload_id(identifier)
        if workload_id:
            return all_workloads.get(workload_id)
        return None

    def get_hierarchy_snapshot(self) -> dict[str, list[str]]:
        hierarchy: dict[str, list[str]] = {}
        for workload in self._active_workloads.values():
            if not workload.master_id:
                continue
            hierarchy.setdefault(workload.master_id, []).append(workload.id)
        return hierarchy
