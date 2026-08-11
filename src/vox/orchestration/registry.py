"""VOXRegistry — agent and capability discovery from disk.

Part of the v0.5.0 service-oriented orchestrator decomposition.
Isolates all directory-traversal, YAML parsing, and dynamic
import logic for capabilities and agents.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import dotenv_values

from vox.agents import VOXAgent
from vox.capabilities import VOXCapability
from vox.observability import VOXForensicLogger

if TYPE_CHECKING:
    from vox.orchestration.base import VOXOrchestrator


@dataclass
class CapabilityEntry:
    cls: type
    healthy: bool
    instance: VOXCapability | None = None


class VOXRegistry:
    """Filesystem scanner and loader for capabilities and agents.

    Responsible for:
    * Recursive discovery of ``capability.py`` files under the
      capabilities directory.
    * Dynamic import and health-checking of each ``VOXCapability``
      subclass.
    * Scanning ``agent.yml`` manifests in the agents directory.
    * Hiring (constructing) ``VOXAgent`` instances from disk.
    """

    def __init__(
        self,
        orchestrator: VOXOrchestrator,
        logger: VOXForensicLogger,
    ) -> None:
        self._orc = orchestrator
        self._logger = logger

    # ------------------------------------------------------------------
    # Capability discovery
    # ------------------------------------------------------------------

    async def discover_capabilities(self) -> bool:
        for capability_file in self._orc.capabilities_dir.rglob("capability.py"):
            capability_id = ".".join(
                capability_file.relative_to(self._orc.capabilities_dir).parent.parts
            )
            await self._load_capability(capability_id, capability_file)
        self._logger.info(
            f"Discovered {len(self._orc.capability_registry)} capabilities "
            f"({sum(1 for e in self._orc.capability_registry.values() if e.healthy)} healthy)"
        )
        return True

    async def _load_capability(
        self,
        capability_id: str,
        capability_file: Path,
    ) -> bool:
        module_name = "vox.capabilities." + ".".join(
            capability_file.relative_to(self._orc.capabilities_dir)
            .with_suffix("")
            .parts
        )
        try:
            module = importlib.import_module(module_name)
            capability_class = next(
                (
                    obj
                    for obj in module.__dict__.values()
                    if isinstance(obj, type)
                    and issubclass(obj, VOXCapability)
                    and obj is not VOXCapability
                ),
                None,
            )
            if capability_class is None:
                raise ImportError(
                    f"{module_name} does not export a VOXCapability subclass"
                )
            healthy = await capability_class.health_check()
            self._orc.capability_registry[capability_id] = CapabilityEntry(
                cls=capability_class,
                healthy=healthy,
            )
            if healthy:
                self._logger.ok(f"Capability discovered: [{capability_id}]")
            else:
                self._logger.error(f"Capability [{capability_id}] failed health check")
            return healthy
        except Exception as exc:  # noqa: BLE001 — capability load failure is non-fatal
            self._logger.error(f"Failed to load capability [{capability_id}]: {exc}")
            return False

    def get_capability_instance(self, cap_id: str) -> VOXCapability | None:
        entry = self._orc.capability_registry.get(cap_id)
        if not entry:
            self._logger.error(f"Capability [{cap_id}] not found in registry")
            return None
        if not entry.healthy:
            self._logger.error(
                f"Capability [{cap_id}] is unhealthy \u2014 cannot mount"
            )
            return None
        if entry.instance is not None:
            return entry.instance
        instance = entry.cls()
        instance.id = cap_id
        instance.logger = self._logger
        entry.instance = instance
        self._logger.ok(f"Capability loaded: [{cap_id}]")
        return instance

    # ------------------------------------------------------------------
    # Agent discovery
    # ------------------------------------------------------------------

    def scan_agent_manifests(self):
        agent_specs = []
        for agent_folder in sorted(self._orc.agents_dir.iterdir()):
            if (
                not agent_folder.is_dir()
                or agent_folder.name.startswith(".")
                or agent_folder.name == "__pycache__"
            ):
                continue
            manifest_path = agent_folder / "agent.yml"
            if not manifest_path.exists():
                self._logger.warning(f"Missing manifest: {agent_folder.name}")
                continue
            try:
                data = yaml.safe_load(manifest_path.read_text()) or {}
                agent_specs.append(
                    {
                        "folder": agent_folder,
                        "id": data.get("id"),
                        "master_id": data.get("master_id"),
                        "autostart": data.get("autostart", False),
                        "name": data.get("name"),
                    }
                )
            except Exception as e:  # noqa: BLE001 — defensive catch at manifest parse
                self._logger.error(f"Failed parsing {agent_folder}: {e}")
        agents_by_id = {}
        for spec in agent_specs:
            if spec["id"]:
                agents_by_id[spec["id"]] = spec
        self._logger.info(f"Found {len(agent_specs)} agent manifest(s)")
        return agent_specs, agents_by_id

    async def discover_agents(self) -> bool:
        agent_specs, agents_by_id = self.scan_agent_manifests()

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

                agent = self.hire_agent(spec["folder"], spec["id"])
                if agent:
                    if agent._degraded or not agent.health_check():
                        self._orc.degraded_agents[agent.id] = agent
                        agent.logger.warning(
                            "Agent DEGRADED \u2014 No active roles available. "
                            "Skipping onboarding."
                        )
                    elif spec["autostart"]:
                        ok = await agent.boot()
                        if ok:
                            self._orc.active_agents[agent.id] = agent
                            agent.logger.ok(f"Agent {agent.name} onboarded and active.")
                        else:
                            failed.add(spec["id"])
                            pending.remove(spec)
                            progress = True
                            continue
                    else:
                        self._orc.inactive_agents[agent.id] = agent
                        agent.logger.info("Onboarded \u2014 autostart disabled.")
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
                        self._logger.error(
                            f"Master Agent '{master_name}' not available \u2014 "
                            f"cannot create {spec['name']}"
                        )
                    else:
                        self._logger.error(
                            f"Unresolvable dependency for agent {spec['name']} "
                            f"(master_id={master})"
                        )
                return False

        degraded = len(self._orc.degraded_agents)
        msg = (
            f"Fleet ready: {len(self._orc.active_agents)} active, "
            f"{len(self._orc.inactive_agents)} inactive"
        )
        if degraded:
            msg += f", {degraded} degraded"
        self._logger.info(msg)
        return True

    def hire_agent(self, folder: Path, agent_id: str | None = None):
        try:
            env = {**dotenv_values(".env"), **os.environ}
            global_env = {}
            for key in VOXAgent.GLOBAL_AGENT_KEYS:
                if key in env:
                    global_env[key] = env[key]
            agent = VOXAgent(
                folder,
                orchestrator=self._orc,
                logger=self._logger,
                global_env=global_env,
            )
            return agent
        except Exception as e:  # noqa: BLE001 — defensive catch at agent hire
            self._logger.error(f"hire_agent failed for {folder}: {e}")
            return None
