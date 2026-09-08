"""VOXRegistry — workload and capability discovery from disk.

Isolates all directory-traversal, YAML parsing, and dynamic import logic for
capabilities and workloads.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import dotenv_values

from vox.capabilities import VOXCapability
from vox.observability import VOXForensicLogger
from vox.workloads import VOXWorkload

if TYPE_CHECKING:
    from vox.orchestration.base import VOXOrchestrator


@dataclass
class CapabilityEntry:
    cls: type
    healthy: bool
    instance: VOXCapability | None = None


class VOXRegistry:
    """Filesystem scanner and loader for capabilities and workloads.

    Responsible for:
    * Recursive discovery of ``capability.py`` files under the capabilities dir.
    * Dynamic import and health-checking of each ``VOXCapability`` subclass.
    * Scanning ``manifest.yml`` manifests in the personas directory.
    * Hiring (constructing) ``VOXWorkload`` instances from disk.
    """

    def __init__(
        self,
        orchestrator: VOXOrchestrator,
        logger: VOXForensicLogger,
    ) -> None:
        self._orc = orchestrator
        self._logger = logger

    # --------------------------------------------------------------------------
    # Capability discovery
    # --------------------------------------------------------------------------

    async def discover_capabilities(self) -> bool:
        for capability_file in self._orc.capabilities_dir.rglob("capability.py"):
            capability_id = ".".join(
                capability_file.relative_to(self._orc.capabilities_dir).parent.parts
            )
            await self._load_capability(capability_id, capability_file)
        healthy = sum(1 for e in self._orc.capability_registry.values() if e.healthy)
        self._logger.info(
            f"Discovered {len(self._orc.capability_registry)} capabilities "
            f"({healthy} healthy)"
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
            capability_class.load_contract(capability_file)
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
        except Exception as exc:  # noqa: BLE001 — load failures are non-fatal
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

    # --------------------------------------------------------------------------
    # Workload discovery
    # --------------------------------------------------------------------------

    def scan_workload_manifests(self):
        workload_specs = []
        for persona_folder in sorted(self._orc.personas_dir.iterdir()):
            if (
                not persona_folder.is_dir()
                or persona_folder.name.startswith(".")
                or persona_folder.name == "__pycache__"
            ):
                continue
            manifest_path = persona_folder / "manifest.yml"
            if not manifest_path.exists():
                self._logger.warning(f"Missing manifest: {persona_folder.name}")
                continue
            try:
                data = yaml.safe_load(manifest_path.read_text()) or {}
                workload_specs.append(
                    {
                        "folder": persona_folder,
                        "id": data.get("id"),
                        "master_id": data.get("master_id"),
                        "autostart": data.get("autostart", False),
                        "name": data.get("name"),
                    }
                )
            except Exception as e:  # noqa: BLE001 — malformed manifest skipped
                self._logger.error(f"Failed parsing {persona_folder}: {e}")
        workloads_by_id = {}
        for spec in workload_specs:
            if spec["id"]:
                workloads_by_id[spec["id"]] = spec
        self._logger.info(f"Found {len(workload_specs)} workload manifest(s)")
        return workload_specs, workloads_by_id

    async def discover_workloads(self) -> bool:
        workload_specs, workloads_by_id = self.scan_workload_manifests()

        created = set()
        failed = set()
        pending = list(workload_specs)

        def _can_create(spec):
            master = spec["master_id"] or ""
            return master == "" or master in created

        while pending:
            progress = False
            for spec in list(pending):
                if not _can_create(spec):
                    continue

                workload = self.hire_workload(spec["folder"], spec["id"])
                if workload:
                    if not workload.health_check():
                        self._orc.degraded_workloads[workload.id] = workload
                        workload.logger.warning(
                            "Workload DEGRADED \u2014 "
                            "No active roles available. "
                            "Skipping onboarding."
                        )
                    elif spec["autostart"]:
                        ok = await workload.boot()
                        if ok:
                            self._orc.active_workloads[workload.id] = workload
                            workload.logger.ok(
                                f"Workload {workload.name} onboarded and active."
                            )
                        else:
                            failed.add(spec["id"])
                            pending.remove(spec)
                            progress = True
                            continue
                    else:
                        self._orc.inactive_workloads[workload.id] = workload
                        workload.logger.info("Onboarded \u2014 autostart disabled.")
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
                        master_name = workloads_by_id.get(master, {}).get(
                            "name", master
                        )
                        self._logger.error(
                            f"Master Workload '{master_name}' not available "
                            f"\u2014 cannot create {spec['name']}"
                        )
                    else:
                        self._logger.error(
                            f"Unresolvable dependency for workload "
                            f"{spec['name']} (master_id={master})"
                        )
                return False

        degraded = len(self._orc.degraded_workloads)
        msg = (
            f"Fleet ready: {len(self._orc.active_workloads)} active, "
            f"{len(self._orc.inactive_workloads)} inactive"
        )
        if degraded:
            msg += f", {degraded} degraded"
        self._logger.info(msg)
        return True

    def hire_workload(self, folder: Path, workload_id: str | None = None):
        try:
            env = {**dotenv_values(".env"), **os.environ}
            global_env = {}
            for key in VOXWorkload.GLOBAL_WORKLOAD_KEYS:
                if key in env:
                    global_env[key] = env[key]
            workload = VOXWorkload(
                folder,
                orchestrator=self._orc,
                logger=self._logger,
                global_env=global_env,
            )
            return workload
        except Exception as e:  # noqa: BLE001 — hire failures are non-fatal
            self._logger.error(f"hire_workload failed for {folder}: {e}")
            return None
