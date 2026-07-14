"""Asynchronous agent file watcher.

Polls agent directories for SHA-256 hash changes on ``agent.yml``
and ``roles/**/*.py`` and triggers a hot-restart of the
affected agent.
"""

import asyncio
import hashlib
import yaml

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vox.orchestration import VOXOrchestrator


class AgentFileWatcher:
    """Polling file watcher that hot-restarts agents on config/code changes.

    Only monitors ``agent.yml`` and ``roles/**/*.py`` (excluding
    ``_test.py`` files and ``__pycache__`` directories).
    """

    def __init__(
        self,
        orchestrator: "VOXOrchestrator",
        agents_dir: Path,
        interval: float = 2.0,
    ) -> None:
        self._orc = orchestrator
        self._agents_dir = Path(agents_dir)
        self.logger = orchestrator.logger
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._snapshots: dict[str, dict[str, str]] = {}

    async def start(self) -> None:
        self.logger.info(
            "AgentFileWatcher started — polling every %ss", self._interval,
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
        if not self._agents_dir.is_dir():
            return

        for agent_folder in sorted(self._agents_dir.iterdir()):
            if (
                not agent_folder.is_dir()
                or agent_folder.name.startswith(".")
                or agent_folder.name == "__pycache__"
            ):
                continue

            manifest = agent_folder / "agent.yml"
            if not manifest.exists():
                continue

            try:
                data = yaml.safe_load(manifest.read_text()) or {}
            except Exception:
                continue
            agent_name = data.get("name") or agent_folder.name

            current = self._take_snapshot(agent_folder)
            previous = self._snapshots.get(agent_name)

            if previous is not None and current != previous:
                self.logger.warning(
                    "Change detected in agent '%s'. Triggering hot-restart...",
                    agent_name,
                )
                ok = await self._orc.restart_agent(agent_name)
                if ok:
                    self.logger.ok(
                        "Agent '%s' booted back online", agent_name,
                    )
                else:
                    self.logger.error(
                        "Agent '%s' hot-restart failed", agent_name,
                    )

            self._snapshots[agent_name] = current

    def _take_snapshot(self, agent_dir: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}

        manifest = agent_dir / "agent.yml"
        if manifest.exists():
            snapshot["agent.yml"] = hashlib.sha256(manifest.read_bytes()).hexdigest()

        roles_dir = agent_dir / "roles"
        if roles_dir.is_dir():
            for py_file in sorted(roles_dir.rglob("*.py")):
                if py_file.name.endswith("_test.py"):
                    continue
                if "__pycache__" in py_file.parts:
                    continue
                rel = py_file.relative_to(agent_dir)
                snapshot[str(rel)] = hashlib.sha256(py_file.read_bytes()).hexdigest()

        return snapshot
