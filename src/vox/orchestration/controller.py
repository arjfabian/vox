"""FleetController — lock-guarded workload lifecycle transitions.

Coordinates safe, transactional state transitions for individual workloads
(stop, start, restart, pause, resume) using per-workload ``asyncio.Lock`` to
prevent race conditions during concurrent control-plane requests.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vox.observability import VOXForensicLogger

if TYPE_CHECKING:
    from vox.orchestration.graph import FleetGraph
    from vox.orchestration.registry import VOXRegistry


class FleetController:
    """Lock-guarded lifecycle controller for the workload fleet.

    Each workload ID has a dedicated ``asyncio.Lock`` so that concurrent
    control-plane operations (e.g. simultaneous HTTP requests) are serialised
    per workload without blocking unrelated workloads.
    """

    def __init__(
        self,
        registry: VOXRegistry,
        graph: FleetGraph,
        logger: VOXForensicLogger,
        workload_locks: dict[str, asyncio.Lock],
        active_workloads: dict[str, Any],
        inactive_workloads: dict[str, Any],
        degraded_workloads: dict[str, Any],
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._logger = logger
        self._workload_locks = workload_locks
        self._active_workloads = active_workloads
        self._inactive_workloads = inactive_workloads
        self._degraded_workloads = degraded_workloads

    def _get_workload_lock(self, workload_id: str) -> asyncio.Lock:
        if workload_id not in self._workload_locks:
            self._workload_locks[workload_id] = asyncio.Lock()
        return self._workload_locks[workload_id]

    async def stop_workload(self, workload_id: str) -> bool:
        async with self._get_workload_lock(workload_id):
            workload = self._active_workloads.get(workload_id)
            if not workload:
                self._logger.error(
                    f"Workload '{workload_id}' not found in active workloads"
                )
                return False
            await workload.stop()
            self._active_workloads.pop(workload_id)
            self._inactive_workloads[workload_id] = workload
            self._logger.ok(f"Workload '{workload.name}' stopped and moved to inactive")
            return True

    async def restart_workload(self, workload_name: str) -> bool:
        workload_id = self._graph.resolve_workload_id(workload_name)
        if not workload_id:
            self._logger.error(f"Workload '{workload_name}' not found")
            return False
        async with self._get_workload_lock(workload_id):
            workload = (
                self._active_workloads.get(workload_id)
                or self._inactive_workloads.get(workload_id)
                or self._degraded_workloads.get(workload_id)
            )
            if not workload:
                self._logger.error(f"Workload '{workload_name}' not found in fleet")
                return False

            folder = workload.dir

            await workload.shutdown()
            self._active_workloads.pop(workload_id, None)
            self._inactive_workloads.pop(workload_id, None)
            self._degraded_workloads.pop(workload_id, None)
            self._workload_locks.pop(workload_id, None)

            new_workload = self._registry.hire_workload(folder)
            if not new_workload:
                self._logger.error(f"Failed to re-hire workload '{workload_name}'")
                return False

            if new_workload._degraded or not new_workload.health_check():
                self._degraded_workloads[new_workload.id] = new_workload
                self._logger.warning(
                    f"Workload '{workload_name}' restarted in DEGRADED state"
                )
                return True

            ok = await new_workload.boot()
            if ok:
                self._active_workloads[new_workload.id] = new_workload
                self._logger.ok(f"Workload '{workload_name}' restarted and active")
            else:
                self._degraded_workloads[new_workload.id] = new_workload
                self._logger.warning(
                    f"Workload '{workload_name}' restarted in DEGRADED "
                    "state (boot failed)"
                )
            return ok

    async def start_workload_by_name(self, workload_name: str) -> bool:
        workload_id = self._graph.resolve_workload_id(workload_name)
        if not workload_id:
            self._logger.error(f"Workload '{workload_name}' not found")
            return False
        async with self._get_workload_lock(workload_id):
            workload = self._inactive_workloads.get(workload_id)
            if not workload:
                self._logger.error(
                    f"Workload '{workload_name}' is already active or not found"
                )
                return False
            ok = await workload.boot()
            if ok:
                self._inactive_workloads.pop(workload_id)
                self._active_workloads[workload_id] = workload
                self._logger.ok(f"Workload '{workload.name}' started")
            return ok

    async def pause_workload(self, workload_name: str) -> bool:
        workload_id = self._graph.resolve_workload_id(workload_name)
        if not workload_id:
            self._logger.error(f"Workload '{workload_name}' not found")
            return False
        workload = self._active_workloads.get(workload_id)
        if not workload:
            self._logger.error(f"Workload '{workload_name}' is not active")
            return False
        await workload.pause()
        return True

    async def resume_workload(self, workload_name: str) -> bool:
        workload_id = self._graph.resolve_workload_id(workload_name)
        if not workload_id:
            self._logger.error(f"Workload '{workload_name}' not found")
            return False
        workload = self._active_workloads.get(workload_id)
        if not workload:
            self._logger.error(f"Workload '{workload_name}' is not active")
            return False
        await workload.resume()
        return True
