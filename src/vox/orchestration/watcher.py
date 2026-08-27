"""Asynchronous workload file watcher.

Polls persona directories for SHA-256 hash changes on ``manifest.yml``
and ``roles/**/*.py`` and triggers a hot-restart of the
affected workload.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from vox.orchestration import VOXOrchestrator


class WorkloadFileWatcher:
    """Polling file watcher that hot-restarts workloads on config/code changes.

    Only monitors ``manifest.yml`` and ``roles/**/*.py`` (excluding
    ``_test.py`` files and ``__pycache__`` directories).
    """

    def __init__(
        self,
        orchestrator: "VOXOrchestrator",
        personas_dir: Path,
        interval: float = 2.0,
    ) -> None:
        self._orc = orchestrator
        self._personas_dir = Path(personas_dir)
        self.logger = orchestrator.logger
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._snapshots: dict[str, dict[str, str]] = {}

    async def start(self) -> None:
        self.logger.info(
            "WorkloadFileWatcher started — polling every %ss",
            self._interval,
        )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._poll()

    async def _poll(self) -> None:
        if not self._personas_dir.is_dir():
            return

        for persona_folder in sorted(self._personas_dir.iterdir()):
            if (
                not persona_folder.is_dir()
                or persona_folder.name.startswith(".")
                or persona_folder.name == "__pycache__"
            ):
                continue

            manifest = persona_folder / "manifest.yml"
            if not manifest.exists():
                continue

            try:
                data = yaml.safe_load(manifest.read_text()) or {}
            except Exception:  # noqa: BLE001, S112 — resilient scan, skip malformed manifests
                continue
            workload_name = data.get("name") or persona_folder.name

            current = self._take_snapshot(persona_folder)
            previous = self._snapshots.get(workload_name)

            if previous is not None and current != previous:
                self.logger.warning(
                    "Change detected in workload '%s'. Triggering hot-restart...",
                    workload_name,
                )
                ok = await self._orc.restart_workload(workload_name)
                if ok:
                    self.logger.ok(
                        "Workload '%s' booted back online",
                        workload_name,
                    )
                else:
                    self.logger.error(
                        "Workload '%s' hot-restart failed",
                        workload_name,
                    )

            self._snapshots[workload_name] = current

    def _take_snapshot(self, persona_dir: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}

        manifest = persona_dir / "manifest.yml"
        if manifest.exists():
            snapshot["manifest.yml"] = hashlib.sha256(manifest.read_bytes()).hexdigest()

        roles_dir = persona_dir / "roles"
        if roles_dir.is_dir():
            for py_file in sorted(roles_dir.rglob("*.py")):
                if py_file.name.endswith("_test.py"):
                    continue
                if "__pycache__" in py_file.parts:
                    continue
                rel = py_file.relative_to(persona_dir)
                snapshot[str(rel)] = hashlib.sha256(py_file.read_bytes()).hexdigest()

        return snapshot
