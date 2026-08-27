"""Tests for the WorkloadFileWatcher."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vox.config import VOXConfig
from vox.orchestration import VOXOrchestrator
from vox.orchestration.watcher import WorkloadFileWatcher

_ROLE_TEMPLATE = """\
from vox.roles import VOXRole

REQUIRES = set()

class MainRole(VOXRole):
    def __init__(self, workload):
        super().__init__(workload)
    def get_commands(self):
        return {}
"""


class TestWorkloadFileWatcherStandalone(unittest.TestCase):
    """Unit tests for WorkloadFileWatcher — snapshot logic, polling."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_watcher_{id(self)}"
        self.personas_dir = self.tmp / "instance" / "personas"
        self.persona_dir = self.personas_dir / "test_workload"
        self.identity_dir = self.tmp / "identity"
        self.identity_dir.mkdir(parents=True, exist_ok=True)
        (self.persona_dir / "roles").mkdir(parents=True, exist_ok=True)
        (self.persona_dir / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-watcher-uuid\nautostart: true\n"
        )
        (self.persona_dir / "roles" / "main.py").write_text(_ROLE_TEMPLATE)
        (self.persona_dir / ".env").write_text(
            "TELEGRAM_BOT_TOKEN=dummy_bot_token\nTELEGRAM_USER_ID=dummy_user_id\n"
        )
        self.logger = MagicMock()
        self.config = VOXConfig(
            verbose_logging=False,
            log_path="logs/vox.log",
            uds_path="/tmp/vox.sock",
            war_room_id="",
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_orchestrator(self):
        return VOXOrchestrator(
            self.config,
            logger=self.logger,
            personas_dir=self.personas_dir,
            identity_dir=self.identity_dir,
        )

    def test_take_snapshot_includes_yml_and_roles(self):
        orc = self._make_orchestrator()
        watcher = WorkloadFileWatcher(orc, self.personas_dir)
        snap = watcher._take_snapshot(self.persona_dir)
        self.assertIn("manifest.yml", snap)
        self.assertTrue(any("roles/main.py" in k for k in snap))

    def test_take_snapshot_excludes_test_files(self):
        (self.persona_dir / "roles" / "util_test.py").write_text("")
        orc = self._make_orchestrator()
        watcher = WorkloadFileWatcher(orc, self.personas_dir)
        snap = watcher._take_snapshot(self.persona_dir)
        self.assertNotIn("roles/util_test.py", snap)

    def test_take_snapshot_excludes_pycache(self):
        pycache = self.persona_dir / "roles" / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-314.pyc").write_text("fake")
        orc = self._make_orchestrator()
        watcher = WorkloadFileWatcher(orc, self.personas_dir)
        snap = watcher._take_snapshot(self.persona_dir)
        self.assertNotIn("roles/__pycache__/main.cpython-314.pyc", snap)

    def test_snapshot_changes_on_file_modification(self):
        orc = self._make_orchestrator()
        watcher = WorkloadFileWatcher(orc, self.personas_dir)
        snap1 = watcher._take_snapshot(self.persona_dir)
        (self.persona_dir / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-watcher-uuid\nversion: 2\n"
        )
        snap2 = watcher._take_snapshot(self.persona_dir)
        self.assertNotEqual(snap1, snap2)


class TestFileWatcherHotRestart(unittest.TestCase):
    """Integration — watcher detects change and triggers restart."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_watcher_{id(self)}"
        self.personas_dir = self.tmp / "instance" / "personas"
        self.persona_dir = self.personas_dir / "test_workload"
        self.identity_dir = self.tmp / "identity"
        self.identity_dir.mkdir(parents=True, exist_ok=True)
        (self.persona_dir / "roles").mkdir(parents=True, exist_ok=True)
        (self.persona_dir / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-watcher-uuid\nautostart: true\n"
        )
        (self.persona_dir / "roles" / "main.py").write_text(_ROLE_TEMPLATE)
        (self.persona_dir / ".env").write_text(
            "TELEGRAM_BOT_TOKEN=dummy_bot_token\nTELEGRAM_USER_ID=dummy_user_id\n"
        )
        self.logger = MagicMock()
        self.config = VOXConfig(
            verbose_logging=False,
            log_path="logs/vox.log",
            uds_path="/tmp/vox.sock",
            war_room_id="",
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _run_watcher_test(self, interval: float = 0.1):
        orc = VOXOrchestrator(
            self.config,
            logger=self.logger,
            personas_dir=self.personas_dir,
            identity_dir=self.identity_dir,
        )
        with (
            patch.dict(os.environ, {"VOX_WATCH_DISABLED": "true"}, clear=False),
            patch(
                "vox.workloads.capability_binder.CapabilityBinder.inject_vault_secrets",
                new_callable=AsyncMock,
            ),
        ):
            await orc.boot()

            watcher = WorkloadFileWatcher(orc, self.personas_dir, interval=interval)
            await watcher.start()

            watcher._snapshots = {}

            await asyncio.sleep(interval * 1.5)
            watcher._snapshots["TestWorkload"] = watcher._take_snapshot(self.persona_dir)

            (self.persona_dir / "manifest.yml").write_text(
                "name: TestWorkload\nid: test-watcher-uuid\nversion: 2\n"
            )

            await asyncio.sleep(interval * 2)

            await watcher.stop()
            return orc

    def test_file_change_triggers_restart(self):
        async def run():
            orc = await self._run_watcher_test(interval=0.05)
            workload = orc.active_workloads.get("test-watcher-uuid")
            self.assertIsNotNone(workload, "Workload should be active after restart")
            self.assertEqual(workload.name, "TestWorkload")

        asyncio.run(run())

    def test_no_change_does_not_restart(self):
        async def run():
            orc = VOXOrchestrator(
                self.config,
                logger=self.logger,
                personas_dir=self.personas_dir,
                identity_dir=self.identity_dir,
            )
            with (
                patch.dict(os.environ, {"VOX_WATCH_DISABLED": "true"}, clear=False),
                patch("vox.workloads.capability_binder.CapabilityBinder.inject_vault_secrets", new_callable=AsyncMock),
            ):
                await orc.boot()
            watcher = WorkloadFileWatcher(orc, self.personas_dir, interval=0.05)
            await watcher.start()
            watcher._snapshots["TestWorkload"] = watcher._take_snapshot(self.persona_dir)
            await asyncio.sleep(0.15)
            await watcher.stop()
            workload = orc.active_workloads.get("test-watcher-uuid")
            self.assertIsNotNone(workload)

        asyncio.run(run())
