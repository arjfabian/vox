"""VOXOrchestrator — unified fleet coordination facade.

Coordinates workload discovery, capability mounting,
and workload lifecycle management by delegating to specialised
service components (VOXRegistry, FleetGraph, FleetController).
"""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values

from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration.controller import FleetController
from vox.orchestration.graph import FleetGraph
from vox.orchestration.registry import CapabilityEntry, VOXRegistry
from vox.orchestration.war_room import VOXWarRoom, VOXWarRoomMaster, WarRoomMessage
from vox.orchestration.watcher import WorkloadFileWatcher
from vox.security import InputSanitizer, SecurityError, VOXSpeakerProfile
from vox.services import FleetMessenger
from vox.workloads import VOXWorkload

if TYPE_CHECKING:
    from vox.capabilities.base import VOXCapability


class VOXOrchestrator:
    def __init__(
        self,
        config: VOXConfig,
        logger: VOXForensicLogger,
        capabilities_dir: Path | None = None,
        personas_dir: Path | None = None,
        identity_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.capability_registry: dict[str, CapabilityEntry] = {}
        base_dir = Path(__file__).resolve().parent.parent

        self.capabilities_dir = capabilities_dir or (base_dir / "capabilities")
        self.active_workloads: dict[str, VOXWorkload] = {}
        self.inactive_workloads: dict[str, VOXWorkload] = {}
        self.degraded_workloads: dict[str, VOXWorkload] = {}
        self._workload_locks: dict[str, asyncio.Lock] = {}

        project_root = base_dir.parent.parent
        self.personas_dir = personas_dir or (project_root / "instance" / "personas")
        resolved_identity = identity_dir or (project_root / "identity")
        self.identity_dir = resolved_identity
        self.speaker_profile = VOXSpeakerProfile(self.identity_dir, self.logger)
        self.speaker_profile.load()
        self._guardrail = InputSanitizer(self.logger)

        env = {**dotenv_values(".env"), **os.environ}
        bot_token = env.get("TELEGRAM_BOT_TOKEN")
        if bot_token and config.war_room_id:
            self.fleet_messenger = FleetMessenger(
                bot_token=bot_token,
                channel_id=config.war_room_id,
                logger=self.logger,
            )
        else:
            self.fleet_messenger = None
            if not bot_token:
                self.logger.warning(
                    "FleetMessenger disabled \u2014 no TELEGRAM_BOT_TOKEN in environment"
                )

        # Pub/Sub war room
        self._war_room = VOXWarRoom()

        async def _noop_broadcast(text: str) -> bool:
            return True

        broadcast_fn: Any = _noop_broadcast
        if self.fleet_messenger is not None:
            broadcast_fn = self.fleet_messenger.post_message
        self._war_room_master = VOXWarRoomMaster(
            war_room=self._war_room,
            broadcast_fn=broadcast_fn,
            orchestrator=self,
        )

        self._watcher: WorkloadFileWatcher | None = None

        # ------------------------------------------------------------------
        # Service components
        # ------------------------------------------------------------------
        self._registry = VOXRegistry(self, self.logger)
        self._graph = FleetGraph(
            self.active_workloads,
            self.inactive_workloads,
            self.degraded_workloads,
            self.logger,
        )
        self._controller = FleetController(
            self._registry,
            self._graph,
            self.logger,
            self._workload_locks,
            self.active_workloads,
            self.inactive_workloads,
            self.degraded_workloads,
        )

    # ------------------------------------------------------------------
    # Fleet-wide properties
    # ------------------------------------------------------------------

    @property
    def _all_workloads(self) -> dict[str, "VOXWorkload"]:
        return self._graph.all_workloads

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> bool:
        self.logger.info("Bootstrapping VOX orchestrator")
        self.logger.info("Initiating Capability discovery")
        await self._registry.discover_capabilities()
        self.logger.info("Initiating Workload discovery")
        await self._registry.discover_workloads()
        if not self.active_workloads:
            self.logger.error(
                "Boot aborted: zero operative workloads. "
                "Check workload manifests, role loading, and health checks."
            )
            return False
        degraded_count = len(self.degraded_workloads)
        msg = f"Fleet is operative: {len(self.active_workloads)} workload(s) active"
        if degraded_count:
            msg += f", {degraded_count} degraded"
        self.logger.ok(msg)

        if os.environ.get("VOX_WATCH_DISABLED", "").lower() not in ("1", "true", "yes"):
            self._watcher = WorkloadFileWatcher(self, self.personas_dir)
            await self._watcher.start()

        await self._war_room_master.start()
        self.logger.info("War room dispatcher active")

        return True

    async def shutdown(self) -> None:
        await self._war_room_master.stop()
        if self._watcher is not None:
            await self._watcher.stop()
        self.logger.info("Shutting down VOX fleet")
        all_workloads = (
            list(self.active_workloads.values())
            + list(self.inactive_workloads.values())
            + list(self.degraded_workloads.values())
        )
        for workload in all_workloads:
            try:
                await workload.shutdown()
            except Exception as e:  # noqa: BLE001 — defensive catch at fleet shutdown
                self.logger.error(f"Workload shutdown failed [{workload.name}]: {e}")
        self.active_workloads.clear()
        self.inactive_workloads.clear()
        self.degraded_workloads.clear()
        for cap_id in reversed(list(self.capability_registry)):
            entry = self.capability_registry[cap_id]
            if entry.instance is not None:
                try:
                    await entry.instance.shutdown()
                except Exception as e:  # noqa: BLE001 — defensive catch at fleet shutdown
                    self.logger.error(f"Capability shutdown failed [{cap_id}]: {e}")
        if self.fleet_messenger is not None:
            try:
                await self.fleet_messenger.shutdown()
            except Exception as e:  # noqa: BLE001 — defensive catch at fleet shutdown
                self.logger.error(f"FleetMessenger shutdown failed: {e}")
        self.logger.ok("VOX fleet shut down.")

    # ------------------------------------------------------------------
    # Fleet snapshot
    # ------------------------------------------------------------------

    def get_fleet_snapshot(self) -> dict:
        system = {
            "version": "VOX+1.0",
            "speaker_profile": bool(self.speaker_profile.is_enrolled),
        }
        capabilities = [
            {
                "id": cap_id,
                "healthy": entry.healthy,
                "loaded": entry.instance is not None,
            }
            for cap_id, entry in self.capability_registry.items()
        ]
        all_workloads = self._all_workloads
        workloads = [
            workload.describe() for workload in all_workloads.values() if not workload.master_id
        ]
        degraded = [workload.describe() for workload in self.degraded_workloads.values()]
        hierarchy = self._graph.get_hierarchy_snapshot()
        return {
            "system": system,
            "capabilities": capabilities,
            "workloads": workloads,
            "degraded_workloads": degraded,
            "hierarchy": hierarchy,
        }

    # ------------------------------------------------------------------
    # Capability registry (delegated)
    # ------------------------------------------------------------------

    def get_capability_instance(self, cap_id: str) -> "VOXCapability | None":
        return self._registry.get_capability_instance(cap_id)

    # ------------------------------------------------------------------
    # Graph queries (delegated)
    # ------------------------------------------------------------------

    def resolve_workload_id(self, workload_name: str) -> str | None:
        return self._graph.resolve_workload_id(workload_name)

    def get_hierarchy_snapshot(self) -> dict[str, list[str]]:
        return self._graph.get_hierarchy_snapshot()

    def get_children(self, workload_id: str) -> list:
        return self._graph.get_children(workload_id)

    def _resolve_workload(self, identifier: str):
        return self._graph.resolve_workload(identifier)

    # ------------------------------------------------------------------
    # Controller lifecycle (delegated)
    # ------------------------------------------------------------------

    async def stop_workload(self, workload_id: str) -> bool:
        return await self._controller.stop_workload(workload_id)

    async def restart_workload(self, workload_name: str) -> bool:
        return await self._controller.restart_workload(workload_name)

    async def start_workload_by_name(self, workload_name: str) -> bool:
        return await self._controller.start_workload_by_name(workload_name)

    async def pause_workload(self, workload_name: str) -> bool:
        return await self._controller.pause_workload(workload_name)

    async def resume_workload(self, workload_name: str) -> bool:
        return await self._controller.resume_workload(workload_name)

    # ------------------------------------------------------------------
    # Panic shutdown (core compromise)
    # ------------------------------------------------------------------

    def panic_shutdown(self) -> None:
        """Synchronous, uninterruptible emergency stop.

        Freezes all workload tasks, closes control sockets, purges
        cryptographic material from WorkloadVault instances, and flushes
        the war room queue.
        """
        self.logger.critical("PANIC SHUTDOWN initiated")

        # 1. Freeze all workload tasks
        for workload in self._all_workloads.values():
            for task in workload._tasks:
                task.cancel()

        # 2. Stop war room dispatcher
        self._war_room_master._running = False

        # 3. Purge cryptographic material from WorkloadVault instances
        for workload in self._all_workloads.values():
            vault = getattr(workload, "_vault", None)
            if vault is not None:
                vault._key = b"\x00" * 32
                vault._key = None
                workload._vault = None

        # 4. Flush war room queue to in-memory history
        self._war_room.flush_to_disk()

        # 5. Close FleetMessenger HTTP client if present
        if self.fleet_messenger is not None:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self.fleet_messenger.shutdown())
            except RuntimeError:
                pass

        self.logger.critical("PANIC SHUTDOWN complete")

    # ------------------------------------------------------------------
    # Inbound message routing (temporary facade)
    # ------------------------------------------------------------------

    async def dispatch_inbound_message(self, source: str, payload: dict) -> None:
        try:
            payload = self._guardrail.sanitize(payload)
        except SecurityError:
            return

        if payload.get("type") == "alert":
            msg = WarRoomMessage(
                source=source,
                payload=payload,
            )
            await self._war_room.publish(msg)
            return

        for workload in self.active_workloads.values():
            if source in workload.capabilities:
                await workload.emit("inbound_message", source=source, **payload)
                return
