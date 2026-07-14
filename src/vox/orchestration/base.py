"""VOX orchestration layer.

Coordinates agent discovery, capability mounting,
and agent lifecycle management.
"""

import asyncio
import importlib
import os
import yaml

from dataclasses import dataclass
from dotenv import dotenv_values
from pathlib import Path
from typing import Optional

from vox.orchestration.watcher import AgentFileWatcher
from vox.provider import CapabilityProviderProtocol
from vox.agents import VOXAgent
from vox.capabilities import VOXCapability
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.security import InputSanitizer, SecurityError, VOXSpeakerProfile
from vox.services import FleetMessenger


@dataclass
class CapabilityEntry:
    cls: type
    healthy: bool
    instance: Optional[VOXCapability] = None


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
                    "FleetMessenger disabled — no TELEGRAM_BOT_TOKEN in environment"
                )

        self._watcher: AgentFileWatcher | None = None

    def _get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        if agent_id not in self._agent_locks:
            self._agent_locks[agent_id] = asyncio.Lock()
        return self._agent_locks[agent_id]

    @property
    def _all_agents(self) -> dict[str, VOXAgent]:
        return {**self.active_agents, **self.inactive_agents, **self.degraded_agents}

    async def boot(self) -> bool:
        self.logger.info("Bootstrapping VOX orchestrator")
        self.logger.info("Initiating Capability discovery")
        await self._discover_capabilities()
        self.logger.info("Initiating Agent discovery")
        await self._discover_agents()
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

        return True

    async def shutdown(self) -> None:
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
            except Exception as e:
                self.logger.error(f"Agent shutdown failed [{agent.name}]: {e}")
        self.active_agents.clear()
        self.inactive_agents.clear()
        self.degraded_agents.clear()
        for cap_id in reversed(list(self.capability_registry)):
            entry = self.capability_registry[cap_id]
            if entry.instance is not None:
                try:
                    await entry.instance.shutdown()
                except Exception as e:
                    self.logger.error(
                        f"Capability shutdown failed [{cap_id}]: {e}"
                    )
        if self.fleet_messenger is not None:
            try:
                await self.fleet_messenger.shutdown()
            except Exception as e:
                self.logger.error(f"FleetMessenger shutdown failed: {e}")
        self.logger.ok("VOX fleet shut down.")

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
            agent.describe()
            for agent in all_agents.values()
            if not agent.master_id
        ]
        degraded = [
            agent.describe() for agent in self.degraded_agents.values()
        ]
        hierarchy = self.get_hierarchy_snapshot()
        return {
            "system": system,
            "capabilities": capabilities,
            "agents": agents,
            "degraded_agents": degraded,
            "hierarchy": hierarchy,
        }

    async def _discover_capabilities(self) -> bool:
        for capability_file in self.capabilities_dir.rglob("capability.py"):
            capability_id = ".".join(
                capability_file.relative_to(self.capabilities_dir).parent.parts
            )
            await self._load_capability(capability_id, capability_file)
        self.logger.info(
            f"Discovered {len(self.capability_registry)} capabilities "
            f"({sum(1 for e in self.capability_registry.values() if e.healthy)} healthy)"
        )
        return True

    async def _load_capability(
        self, capability_id: str, capability_file: Path
    ) -> bool:
        module_name = (
            "vox.capabilities."
            + ".".join(
                capability_file.relative_to(self.capabilities_dir)
                .with_suffix("").parts
            )
        )
        try:
            module = importlib.import_module(module_name)
            capability_class = next(
                (obj for obj in module.__dict__.values()
                 if isinstance(obj, type)
                 and issubclass(obj, VOXCapability)
                 and obj is not VOXCapability),
                None,
            )
            if capability_class is None:
                raise ImportError(
                    f"{module_name} does not export a VOXCapability subclass"
                )
            healthy = await capability_class.health_check()
            self.capability_registry[capability_id] = CapabilityEntry(
                cls=capability_class, healthy=healthy,
            )
            if healthy:
                self.logger.ok(f"Capability discovered: [{capability_id}]")
            else:
                self.logger.error(
                    f"Capability [{capability_id}] failed health check"
                )
            return healthy
        except Exception as exc:
            self.logger.error(
                f"Failed to load capability [{capability_id}]: {exc}"
            )
            return False

    def get_capability_instance(self, cap_id: str) -> VOXCapability | None:
        entry = self.capability_registry.get(cap_id)
        if not entry:
            self.logger.error(f"Capability [{cap_id}] not found in registry")
            return None
        if not entry.healthy:
            self.logger.error(f"Capability [{cap_id}] is unhealthy — cannot mount")
            return None
        if entry.instance is not None:
            return entry.instance
        instance = entry.cls()
        instance.id = cap_id
        instance.logger = self.logger
        entry.instance = instance
        self.logger.ok(f"Capability loaded: [{cap_id}]")
        return instance

    def _scan_agent_manifests(self):
        agent_specs = []
        for agent_folder in sorted(self.agents_dir.iterdir()):
            if (
                not agent_folder.is_dir()
                or agent_folder.name.startswith(".")
                or agent_folder.name == "__pycache__"
            ):
                continue
            manifest_path = agent_folder / "agent.yml"
            if not manifest_path.exists():
                self.logger.warning(f"Missing manifest: {agent_folder.name}")
                continue
            try:
                data = yaml.safe_load(manifest_path.read_text()) or {}
                agent_specs.append({
                    "folder": agent_folder,
                    "id": data.get("id"),
                    "master_id": data.get("master_id"),
                    "autostart": data.get("autostart", False),
                    "name": data.get("name"),
                })
            except Exception as e:
                self.logger.error(f"Failed parsing {agent_folder}: {e}")
        agents_by_id = {}
        for spec in agent_specs:
            if spec["id"]:
                agents_by_id[spec["id"]] = spec
        self.logger.info(f"Found {len(agent_specs)} agent manifest(s)")
        return agent_specs, agents_by_id

    async def _discover_agents(self) -> bool:
        agent_specs, agents_by_id = self._scan_agent_manifests()

        created = set()
        failed = set()
        pending = list(agent_specs)

        def _can_create(spec):
            master = spec["master_id"] or ""
            return master == "" or master in created

        while pending:
            progress = False
            for spec in list(pending):
                if not _can_create(spec):
                    continue

                agent = self._hire_agent(spec["folder"], spec["id"])
                if agent:
                    if agent._degraded or not agent.health_check():
                        self.degraded_agents[agent.id] = agent
                        agent.logger.warning(
                            f"Agent DEGRADED — No active roles available. "
                            f"Skipping onboarding."
                        )
                    elif spec["autostart"]:
                        ok = await agent.boot()
                        if ok:
                            self.active_agents[agent.id] = agent
                            agent.logger.ok(f"Agent {agent.name} onboarded and active.")
                        else:
                            failed.add(spec["id"])
                            pending.remove(spec)
                            progress = True
                            continue
                    else:
                        self.inactive_agents[agent.id] = agent
                        agent.logger.info("Onboarded — autostart disabled.")
                    created.add(spec["id"])
                    pending.remove(spec)
                    progress = True
                else:
                    failed.add(spec["id"])
                    pending.remove(spec)
                    progress = True

            if not progress:
                for spec in pending:
                    master = spec["master_id"]
                    if master in failed:
                        master_name = agents_by_id.get(master, {}).get("name", master)
                        self.logger.error(
                            f"Master Agent '{master_name}' not available — "
                            f"cannot create {spec['name']}"
                        )
                    else:
                        self.logger.error(
                            f"Unresolvable dependency for agent {spec['name']} "
                            f"(master_id={master})"
                        )
                return False

        degraded = len(self.degraded_agents)
        msg = (
            f"Fleet ready: {len(self.active_agents)} active, "
            f"{len(self.inactive_agents)} inactive"
        )
        if degraded:
            msg += f", {degraded} degraded"
        self.logger.info(msg)
        return True

    def _hire_agent(self, folder: Path, agent_id: str | None = None):
        try:
            env = {**dotenv_values(".env"), **os.environ}
            global_env = {}
            for key in VOXAgent.GLOBAL_AGENT_KEYS:
                if key in env:
                    global_env[key] = env[key]
            agent = VOXAgent(
                folder,
                orchestrator=self,
                logger=self.logger,
                global_env=global_env,
            )
            return agent
        except Exception as e:
            self.logger.error(f"hire_agent failed for {folder}: {e}")
            return None

    def get_hierarchy_snapshot(self) -> dict[str, list[str]]:
        hierarchy: dict[str, list[str]] = {}
        for agent in self.active_agents.values():
            if not agent.master_id:
                continue
            hierarchy.setdefault(agent.master_id, []).append(agent.id)
        return hierarchy

    def resolve_agent_id(self, agent_name: str) -> str | None:
        all_agents = self._all_agents
        for agent in all_agents.values():
            if agent.name.lower() == agent_name.lower():
                return agent.id
        return None

    def get_children(self, agent_id: str) -> list:
        all_agents = self._all_agents
        return [
            agent for agent in all_agents.values()
            if agent.master_id == agent_id
        ]

    async def dispatch_inbound_message(
        self, source: str, payload: dict
    ) -> None:
        try:
            payload = self._guardrail.sanitize(payload)
        except SecurityError:
            return

        # Source is a capability ID (e.g. "comm.telegram") —
        # match directly against capability keys.
        for agent in self.active_agents.values():
            if source in agent.capabilities:
                await agent.emit("inbound_message", source=source, **payload)
                return

    def _resolve_agent(self, identifier: str):
        """Look up an agent by UUID or name. Returns agent or None."""
        all_agents = self._all_agents
        agent = all_agents.get(identifier)
        if agent:
            return agent
        agent_id = self.resolve_agent_id(identifier)
        if agent_id:
            return all_agents.get(agent_id)
        return None

    async def stop_agent(self, agent_id: str) -> bool:
        async with self._get_agent_lock(agent_id):
            agent = self.active_agents.get(agent_id)
            if not agent:
                self.logger.error(f"Agent '{agent_id}' not found in active agents")
                return False
            await agent.stop()
            self.active_agents.pop(agent_id)
            self.inactive_agents[agent_id] = agent
            self.logger.ok(f"Agent '{agent.name}' stopped and moved to inactive")
            return True

    async def restart_agent(self, agent_name: str) -> bool:
        agent_id = self.resolve_agent_id(agent_name)
        if not agent_id:
            self.logger.error(f"Agent '{agent_name}' not found")
            return False
        async with self._get_agent_lock(agent_id):
            agent = (
                self.active_agents.get(agent_id)
                or self.inactive_agents.get(agent_id)
                or self.degraded_agents.get(agent_id)
            )
            if not agent:
                self.logger.error(f"Agent '{agent_name}' not found in fleet")
                return False

            folder = agent.dir

            await agent.shutdown()
            self.active_agents.pop(agent_id, None)
            self.inactive_agents.pop(agent_id, None)
            self.degraded_agents.pop(agent_id, None)
            self._agent_locks.pop(agent_id, None)

            new_agent = self._hire_agent(folder)
            if not new_agent:
                self.logger.error(f"Failed to re-hire agent '{agent_name}'")
                return False

            if new_agent._degraded or not new_agent.health_check():
                self.degraded_agents[new_agent.id] = new_agent
                self.logger.warning(
                    f"Agent '{agent_name}' restarted in DEGRADED state"
                )
                return True

            ok = await new_agent.boot()
            if ok:
                self.active_agents[new_agent.id] = new_agent
                self.logger.ok(f"Agent '{agent_name}' restarted and active")
            else:
                self.degraded_agents[new_agent.id] = new_agent
                self.logger.warning(
                    f"Agent '{agent_name}' restarted in DEGRADED state (boot failed)"
                )
            return ok

    async def start_agent_by_name(self, agent_name: str) -> bool:
        agent_id = self.resolve_agent_id(agent_name)
        if not agent_id:
            self.logger.error(f"Agent '{agent_name}' not found")
            return False
        async with self._get_agent_lock(agent_id):
            agent = self.inactive_agents.get(agent_id)
            if not agent:
                self.logger.error(f"Agent '{agent_name}' is already active or not found")
                return False
            ok = await agent.boot()
            if ok:
                self.inactive_agents.pop(agent_id)
                self.active_agents[agent_id] = agent
                self.logger.ok(f"Agent '{agent.name}' started")
            return ok

    async def pause_agent(self, agent_name: str) -> bool:
        agent_id = self.resolve_agent_id(agent_name)
        if not agent_id:
            self.logger.error(f"Agent '{agent_name}' not found")
            return False
        agent = self.active_agents.get(agent_id)
        if not agent:
            self.logger.error(f"Agent '{agent_name}' is not active")
            return False
        await agent.pause()
        return True

    async def resume_agent(self, agent_name: str) -> bool:
        agent_id = self.resolve_agent_id(agent_name)
        if not agent_id:
            self.logger.error(f"Agent '{agent_name}' not found")
            return False
        agent = self.active_agents.get(agent_id)
        if not agent:
            self.logger.error(f"Agent '{agent_name}' is not active")
            return False
        await agent.resume()
        return True
