"""VOX orchestration layer.

Coordinates agent discovery, capability mounting,
and agent lifecycle management.
"""

import importlib.util
import sys
import yaml

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from vox.agents import VOXAgent
from vox.capabilities import VOXCapability
from vox.config import VOXConfig
from vox.observability import VOXForensicLogger
from vox.security import VOXSpeakerProfile


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
    ) -> None:
        self.config = config
        self.logger = logger
        self.capability_registry: dict[str, CapabilityEntry] = {}
        base_dir = Path(__file__).resolve().parent.parent

        self.capabilities_dir = base_dir / "capabilities"
        self.agents_dir = base_dir / "agents"
        self.active_agents: dict[str, VOXAgent] = {}
        self.inactive_agents: dict[str, VOXAgent] = {}

        project_root = base_dir.parent.parent
        self.identity_dir = project_root / "identity"
        self.speaker_profile = VOXSpeakerProfile(self.identity_dir, self.logger)
        self.speaker_profile.load()

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
        self.logger.ok(
            f"Fleet is operative: {len(self.active_agents)} agent(s) active"
        )
        return True

    async def shutdown(self) -> None:
        self.logger.info("Shutting down VOX fleet")
        for agent in self.active_agents.values():
            try:
                await agent.shutdown()
            except Exception as e:
                self.logger.error(f"Agent shutdown failed [{agent.name}]: {e}")
        for cap_id in reversed(list(self.capability_registry)):
            entry = self.capability_registry[cap_id]
            if entry.instance is not None:
                try:
                    await entry.instance.shutdown()
                except Exception as e:
                    self.logger.error(
                        f"Capability shutdown failed [{cap_id}]: {e}"
                    )
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
        all_agents = {**self.active_agents, **self.inactive_agents}
        agents = [agent.describe() for agent in all_agents.values()]
        hierarchy = self.get_hierarchy_snapshot()
        return {
            "system": system,
            "capabilities": capabilities,
            "agents": agents,
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
            if module_name in sys.modules:
                del sys.modules[module_name]
            spec = importlib.util.spec_from_file_location(module_name, capability_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Unable to create module spec for {module_name}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
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

    def _get_capability_instance(self, cap_id: str) -> VOXCapability | None:
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
                    "capabilities": data.get("capabilities", []),
                })
            except Exception as e:
                self.logger.error(f"Failed parsing {agent_folder}: {e}")
        agents_by_id = {}
        for spec in agent_specs:
            if spec["id"]:
                agents_by_id[spec["id"]] = spec
        self.logger.info(f"Found {len(agent_specs)} agent manifest(s)")
        return agent_specs, agents_by_id

    async def _check_capability_gate(self, spec: dict) -> bool:
        for cap_id in spec.get("capabilities", []):
            entry = self.capability_registry.get(cap_id)
            if not entry or not entry.healthy:
                self.logger.error(
                    f"Agent [{spec['name']}] requires "
                    f"capability [{cap_id}] which is "
                    f"{'not found' if not entry else 'unhealthy'} — rejecting"
                )
                return False
        return True

    async def _discover_agents(self) -> bool:
        agent_specs, agents_by_id = self._scan_agent_manifests()

        created = set()
        failed = set()
        pending = list(agent_specs)

        def _can_create(spec):
            master = spec["master_id"]
            return master == "" or master in created

        while pending:
            progress = False
            for spec in list(pending):
                if not _can_create(spec):
                    continue
                if not await self._check_capability_gate(spec):
                    failed.add(spec["id"])
                    pending.remove(spec)
                    progress = True
                    continue

                agent = self._hire_agent(spec["folder"], spec["id"])
                if agent:
                    if spec["autostart"]:
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

        self.logger.info(
            f"Fleet ready: {len(self.active_agents)} active, "
            f"{len(self.inactive_agents)} inactive"
        )
        return True

    def _hire_agent(self, folder: Path, agent_id: str | None = None):
        try:
            agent = VOXAgent(folder, orchestrator=self, logger=self.logger)
            if not agent.health_check():
                return None
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
