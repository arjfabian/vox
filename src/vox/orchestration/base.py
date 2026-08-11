"""VOXOrchestrator — unified fleet coordination facade.

Coordinates agent discovery, capability mounting,
and agent lifecycle management by delegating to specialised
service components (VOXRegistry, AgentGraph, FleetController).
"""

import asyncio
import os
from pathlib import Path

from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values

from vox.agents import VOXAgent
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.orchestration.controller import FleetController
from vox.orchestration.graph import AgentGraph
from vox.orchestration.registry import CapabilityEntry, VOXRegistry
from vox.orchestration.war_room import VOXWarRoom, VOXWarRoomMaster, WarRoomMessage
from vox.orchestration.watcher import AgentFileWatcher
from vox.security import InputSanitizer, SecurityError, VOXSpeakerProfile
from vox.services import FleetMessenger

if TYPE_CHECKING:
    from vox.capabilities.base import VOXCapability


class VOXOrchestrator:
    def __init__(
        self,
        config: VOXConfig,
        logger: VOXForensicLogger,
        capabilities_dir: Path | None = None,
        agents_dir: Path | None = None,
        identity_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self.capability_registry: dict[str, CapabilityEntry] = {}
        base_dir = Path(__file__).resolve().parent.parent

        self.capabilities_dir = capabilities_dir or (base_dir / "capabilities")
        self.active_agents: dict[str, VOXAgent] = {}
        self.inactive_agents: dict[str, VOXAgent] = {}
        self.degraded_agents: dict[str, VOXAgent] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}

        project_root = base_dir.parent.parent
        self.agents_dir = agents_dir or (project_root / "agents")
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

        self._watcher: AgentFileWatcher | None = None

        # ------------------------------------------------------------------
        # Service components
        # ------------------------------------------------------------------
        self._registry = VOXRegistry(self, self.logger)
        self._graph = AgentGraph(
            self.active_agents,
            self.inactive_agents,
            self.degraded_agents,
            self.logger,
        )
        self._controller = FleetController(
            self._registry,
            self._graph,
            self.logger,
            self._agent_locks,
            self.active_agents,
            self.inactive_agents,
            self.degraded_agents,
        )

    # ------------------------------------------------------------------
    # Fleet-wide properties
    # ------------------------------------------------------------------

    @property
    def _all_agents(self) -> dict[str, "VOXAgent"]:
        return self._graph.all_agents

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def boot(self) -> bool:
        self.logger.info("Bootstrapping VOX orchestrator")
        self.logger.info("Initiating Capability discovery")
        await self._registry.discover_capabilities()
        self.logger.info("Initiating Agent discovery")
        await self._registry.discover_agents()
        if not self.active_agents:
            self.logger.error(
                "Boot aborted: zero operative agents. "
                "Check agent manifests, role loading, and health checks."
            )
            return False
        degraded_count = len(self.degraded_agents)
        msg = f"Fleet is operative: {len(self.active_agents)} agent(s) active"
        if degraded_count:
            msg += f", {degraded_count} degraded"
        self.logger.ok(msg)

        if os.environ.get("VOX_WATCH_DISABLED", "").lower() not in ("1", "true", "yes"):
            self._watcher = AgentFileWatcher(self, self.agents_dir)
            await self._watcher.start()

        await self._war_room_master.start()
        self.logger.info("War room dispatcher active")

        return True

    async def shutdown(self) -> None:
        await self._war_room_master.stop()
        if self._watcher is not None:
            await self._watcher.stop()
        self.logger.info("Shutting down VOX fleet")
        all_agents = (
            list(self.active_agents.values())
            + list(self.inactive_agents.values())
            + list(self.degraded_agents.values())
        )
        for agent in all_agents:
            try:
                await agent.shutdown()
            except Exception as e:  # noqa: BLE001 — defensive catch at fleet shutdown
                self.logger.error(f"Agent shutdown failed [{agent.name}]: {e}")
        self.active_agents.clear()
        self.inactive_agents.clear()
        self.degraded_agents.clear()
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
        all_agents = self._all_agents
        agents = [
            agent.describe() for agent in all_agents.values() if not agent.master_id
        ]
        degraded = [agent.describe() for agent in self.degraded_agents.values()]
        hierarchy = self._graph.get_hierarchy_snapshot()
        return {
            "system": system,
            "capabilities": capabilities,
            "agents": agents,
            "degraded_agents": degraded,
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

    def resolve_agent_id(self, agent_name: str) -> str | None:
        return self._graph.resolve_agent_id(agent_name)

    def get_hierarchy_snapshot(self) -> dict[str, list[str]]:
        return self._graph.get_hierarchy_snapshot()

    def get_children(self, agent_id: str) -> list:
        return self._graph.get_children(agent_id)

    def _resolve_agent(self, identifier: str):
        return self._graph.resolve_agent(identifier)

    # ------------------------------------------------------------------
    # Controller lifecycle (delegated)
    # ------------------------------------------------------------------

    async def stop_agent(self, agent_id: str) -> bool:
        return await self._controller.stop_agent(agent_id)

    async def restart_agent(self, agent_name: str) -> bool:
        return await self._controller.restart_agent(agent_name)

    async def start_agent_by_name(self, agent_name: str) -> bool:
        return await self._controller.start_agent_by_name(agent_name)

    async def pause_agent(self, agent_name: str) -> bool:
        return await self._controller.pause_agent(agent_name)

    async def resume_agent(self, agent_name: str) -> bool:
        return await self._controller.resume_agent(agent_name)

    # ------------------------------------------------------------------
    # Panic shutdown (core compromise)
    # ------------------------------------------------------------------

    def panic_shutdown(self) -> None:
        """Synchronous, uninterruptible emergency stop.

        Freezes all agent tasks, closes control sockets, purges
        cryptographic material from AgentVault instances, and flushes
        the war room queue.
        """
        self.logger.critical("PANIC SHUTDOWN initiated")

        # 1. Freeze all agent tasks
        for agent in self._all_agents.values():
            for task in agent._tasks:
                task.cancel()

        # 2. Stop war room dispatcher
        self._war_room_master._running = False

        # 3. Purge cryptographic material from AgentVault instances
        for agent in self._all_agents.values():
            vault = getattr(agent, "_vault", None)
            if vault is not None:
                vault._key = b"\x00" * 32
                vault._key = None
                agent._vault = None

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

        for agent in self.active_agents.values():
            if source in agent.capabilities:
                await agent.emit("inbound_message", source=source, **payload)
                return
