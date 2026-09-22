import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.orchestration.base import (
    CapabilityEntry,
    VOXOrchestrator,
)
from vox.workloads.base import VOXWorkload


class TestCapabilityEntry(unittest.TestCase):
    def test_default_instance_is_none(self):
        entry = CapabilityEntry(cls=str, healthy=True)
        self.assertIs(entry.cls, str)
        self.assertTrue(entry.healthy)
        self.assertIsNone(entry.instance)

    def test_instance_can_be_set(self):
        entry = CapabilityEntry(cls=str, healthy=True, instance="hello")
        self.assertEqual(entry.instance, "hello")


class TestVOXOrchestrator(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.uds_path = "/tmp/test.sock"
        self.logger = MagicMock()
        self.orc = VOXOrchestrator(config=self.config, logger=self.logger)

    def test_init_initialises_empty_registries(self):
        self.assertEqual(self.orc.capability_registry, {})
        self.assertEqual(self.orc.active_workloads, {})
        self.assertEqual(self.orc.inactive_workloads, {})

    def test_init_loads_speaker_profile(self):
        self.assertIsNotNone(self.orc.speaker_profile)

    def test_init_with_injectable_dirs(self):
        caps_dir = Path("/custom/capabilities")
        personas_dir = Path("/custom/personas")
        identity_dir = Path("/custom/identity")
        orc = VOXOrchestrator(
            config=self.config,
            logger=self.logger,
            capabilities_dir=caps_dir,
            personas_dir=personas_dir,
            identity_dir=identity_dir,
        )
        self.assertEqual(orc.capabilities_dir, caps_dir)
        self.assertEqual(orc.personas_dir, personas_dir)
        self.assertEqual(orc.identity_dir, identity_dir)

    def test_injectable_dirs_fall_back_to_defaults(self):
        base = Path(__file__).resolve().parent.parent / "src" / "vox"
        self.assertEqual(self.orc.capabilities_dir, base / "capabilities")
        self.assertEqual(
            self.orc.personas_dir,
            Path(__file__).resolve().parent.parent / "instance" / "personas",
        )

    def test_all_workloads_merges_active_and_inactive(self):
        workload_a = MagicMock()
        workload_b = MagicMock()
        self.orc.active_workloads["a1"] = workload_a
        self.orc.inactive_workloads["a2"] = workload_b
        merged = self.orc._all_workloads
        self.assertIn("a1", merged)
        self.assertIn("a2", merged)
        self.assertIs(merged["a1"], workload_a)
        self.assertIs(merged["a2"], workload_b)

    def test_all_workloads_reflects_changes(self):
        workload = MagicMock()
        self.orc.inactive_workloads["a1"] = workload
        self.assertIn("a1", self.orc._all_workloads)
        self.orc.active_workloads["a1"] = workload
        del self.orc.inactive_workloads["a1"]
        self.assertIn("a1", self.orc._all_workloads)

    def test_all_workloads_empty_when_none(self):
        self.assertEqual(self.orc._all_workloads, {})

    def test_get_fleet_snapshot_structure(self):
        snapshot = self.orc.get_fleet_snapshot()
        self.assertIn("system", snapshot)
        self.assertIn("capabilities", snapshot)
        self.assertIn("workloads", snapshot)
        self.assertIn("hierarchy", snapshot)

    def test_get_fleet_snapshot_system(self):
        snapshot = self.orc.get_fleet_snapshot()
        system = snapshot["system"]
        self.assertEqual(system["version"], "VOX+1.0")
        self.assertIn("speaker_profile", system)

    def test_get_fleet_snapshot_no_workloads(self):
        snapshot = self.orc.get_fleet_snapshot()
        self.assertEqual(snapshot["workloads"], [])
        self.assertEqual(snapshot["capabilities"], [])

    def test_get_hierarchy_snapshot_empty(self):
        self.assertEqual(self.orc.get_hierarchy_snapshot(), {})

    def test_get_fleet_snapshot_includes_capabilities(self):
        self.orc.capability_registry["test.cap"] = CapabilityEntry(
            cls=MagicMock,
            healthy=True,
        )
        snapshot = self.orc.get_fleet_snapshot()
        caps = snapshot["capabilities"]
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["id"], "test.cap")
        self.assertTrue(caps[0]["healthy"])
        self.assertFalse(caps[0]["loaded"])


class TestVOXOrchestratorShutdown(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.logger = MagicMock()
        self.orc = VOXOrchestrator(config=self.config, logger=self.logger)

    def test_shutdown_empty_fleet(self):
        asyncio.run(self.orc.shutdown())
        self.logger.info.assert_called()

    def test_shutdown_active_workload_error_does_not_propagate(self):
        workload = MagicMock()
        workload.shutdown = AsyncMock(side_effect=RuntimeError("oops"))
        self.orc.active_workloads["test-workload"] = workload

        asyncio.run(self.orc.shutdown())

        workload.shutdown.assert_awaited_once()
        self.logger.exception.assert_called_once()

    def test_shutdown_capability_error_does_not_propagate(self):
        cap = MagicMock()
        cap.shutdown = AsyncMock(side_effect=RuntimeError("oops"))
        self.orc.capability_registry["test.cap"] = CapabilityEntry(
            cls=MagicMock,
            healthy=True,
            instance=cap,
        )

        asyncio.run(self.orc.shutdown())

        cap.shutdown.assert_awaited_once()
        self.logger.exception.assert_called_once()

    def test_shutdown_skips_unloaded_capabilities(self):
        self.orc.capability_registry["unloaded.cap"] = CapabilityEntry(
            cls=MagicMock,
            healthy=True,
            instance=None,
        )
        asyncio.run(self.orc.shutdown())
        self.logger.ok.assert_any_call("VOX fleet shut down.")


class TestVOXOrchestratorCapabilityMethods(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.logger = MagicMock()
        self.orc = VOXOrchestrator(config=self.config, logger=self.logger)

    def test_get_capability_instance_not_found(self):
        result = self.orc.get_capability_instance("nonexistent")
        self.assertIsNone(result)

    def test_get_capability_instance_unhealthy(self):
        self.orc.capability_registry["broken"] = CapabilityEntry(
            cls=MagicMock,
            healthy=False,
        )
        result = self.orc.get_capability_instance("broken")
        self.assertIsNone(result)

    def test_get_capability_instance_returns_cached(self):
        inst = MagicMock()
        self.orc.capability_registry["my.cap"] = CapabilityEntry(
            cls=MagicMock,
            healthy=True,
            instance=inst,
        )
        result = self.orc.get_capability_instance("my.cap")
        self.assertIs(result, inst)

    def test_get_capability_instance_lazy_instantiates(self):
        cls = MagicMock()
        self.orc.capability_registry["lazy.cap"] = CapabilityEntry(
            cls=cls,
            healthy=True,
        )
        result = self.orc.get_capability_instance("lazy.cap")
        cls.assert_called_once()
        self.assertIsNotNone(result)
        entry = self.orc.capability_registry["lazy.cap"]
        self.assertIsNotNone(entry.instance)


class TestVOXOrchestratorLifecycleMethods(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.logger = MagicMock()
        self.orc = VOXOrchestrator(config=self.config, logger=self.logger)
        self._tmp_workloads: list[Path] = []

    def tearDown(self):
        import shutil

        for p in self._tmp_workloads:
            shutil.rmtree(p, ignore_errors=True)

    def _make_workload(self, name: str, workload_id: str):
        workload = MagicMock()
        workload.name = name
        workload.id = workload_id
        return workload

    def _make_real_workload_dir(self, name: str, workload_id: str) -> tuple[Path, MagicMock]:
        tmp = Path("/tmp") / f"test_orc_workload_{id(self)}_{name}"
        (tmp / "roles").mkdir(parents=True, exist_ok=True)
        (tmp / "manifest.yml").write_text(
            f"name: {name}\nid: {workload_id}\nautostart: true\n"
        )
        (tmp / "roles" / "main.py").write_text(
            "from vox.roles import VOXRole\n"
            "REQUIRES = set()\n"
            "class MainRole(VOXRole):\n"
            "    def __init__(self, workload): super().__init__(workload)\n"
            "    def get_commands(self): return {}\n"
        )
        (tmp / ".env").write_text("TELEGRAM_BOT_TOKEN=dummy\nTELEGRAM_USER_ID=dummy\n")
        self._tmp_workloads.append(tmp)
        workload = VOXWorkload(
            tmp,
            logger=self.logger,
            orchestrator=MagicMock(),
        )
        return tmp, workload

    def test_resolve_workload_id_by_name(self):
        workload = self._make_workload("workload1", "a1")
        self.orc.active_workloads["a1"] = workload
        self.assertEqual(self.orc.resolve_workload_id("workload1"), "a1")

    def test_resolve_workload_id_case_insensitive(self):
        workload = self._make_workload("Workload1", "a1")
        self.orc.active_workloads["a1"] = workload
        self.assertEqual(self.orc.resolve_workload_id("workload1"), "a1")

    def test_resolve_workload_id_none_if_not_found(self):
        self.assertIsNone(self.orc.resolve_workload_id("ghost"))

    def test_resolve_workload_id_searches_inactive_too(self):
        workload = self._make_workload("Workload2", "a2")
        self.orc.inactive_workloads["a2"] = workload
        self.assertEqual(self.orc.resolve_workload_id("workload2"), "a2")

    def test_stop_workload_moves_to_inactive(self):
        workload = self._make_workload("workload1", "a1")
        workload.stop = AsyncMock()
        self.orc.active_workloads["a1"] = workload
        result = asyncio.run(self.orc.stop_workload("a1"))
        self.assertTrue(result)
        self.assertNotIn("a1", self.orc.active_workloads)
        self.assertIn("a1", self.orc.inactive_workloads)
        workload.stop.assert_awaited_once()

    def test_stop_workload_not_found(self):
        result = asyncio.run(self.orc.stop_workload("ghost"))
        self.assertFalse(result)

    def test_start_workload_by_name_moves_to_active(self):
        workload = self._make_workload("workload2", "a2")
        workload.boot = AsyncMock(return_value=True)
        self.orc.inactive_workloads["a2"] = workload
        result = asyncio.run(self.orc.start_workload_by_name("workload2"))
        self.assertTrue(result)
        self.assertIn("a2", self.orc.active_workloads)
        self.assertNotIn("a2", self.orc.inactive_workloads)
        workload.boot.assert_awaited_once()

    def test_start_workload_by_name_boot_failure(self):
        workload = self._make_workload("workload2", "a2")
        workload.boot = AsyncMock(return_value=False)
        self.orc.inactive_workloads["a2"] = workload
        result = asyncio.run(self.orc.start_workload_by_name("workload2"))
        self.assertFalse(result)
        self.assertNotIn("a2", self.orc.active_workloads)

    def test_start_workload_by_name_already_active(self):
        workload = self._make_workload("workload1", "a1")
        self.orc.active_workloads["a1"] = workload
        result = asyncio.run(self.orc.start_workload_by_name("workload1"))
        self.assertFalse(result)

    def _register_mock_system_cap(self):
        """Register a lightweight mock capability for system-cap mounting."""
        mock_bound = MagicMock()
        mock_bound.initialize = AsyncMock()
        mock_bound.boot = AsyncMock()
        mock_bound.validate_params = MagicMock(return_value=[])
        mock_bound._params = {}
        mock_cap = MagicMock()
        mock_cap.mount.return_value = mock_bound
        mock_cap.get_secret_names.return_value = []
        mock_bound._capability = mock_cap
        self.orc.capability_registry["comm.gateway"] = CapabilityEntry(
            cls=type(mock_cap),
            healthy=True,
            instance=mock_cap,
        )

    def test_restart_workload(self):
        self._register_mock_system_cap()

        _folder, workload = self._make_real_workload_dir("workload1", "a1")
        workload.shutdown = AsyncMock()
        self.orc.active_workloads["a1"] = workload
        result = asyncio.run(self.orc.restart_workload("workload1"))
        self.assertTrue(result)
        workload.shutdown.assert_awaited_once()

    def test_restart_workload_from_inactive(self):
        self._register_mock_system_cap()

        _folder, workload = self._make_real_workload_dir("workload2", "a2")
        workload.shutdown = AsyncMock()
        self.orc.inactive_workloads["a2"] = workload
        result = asyncio.run(self.orc.restart_workload("workload2"))
        self.assertTrue(result)
        new_workload = self.orc.active_workloads.get("a2")
        self.assertIsNotNone(new_workload)
        workload.shutdown.assert_awaited_once()

    def test_restart_workload_not_found(self):
        result = asyncio.run(self.orc.restart_workload("ghost"))
        self.assertFalse(result)

    def test_pause_workload(self):
        workload = self._make_workload("workload1", "a1")
        workload.pause = AsyncMock()
        self.orc.active_workloads["a1"] = workload
        result = asyncio.run(self.orc.pause_workload("workload1"))
        self.assertTrue(result)
        workload.pause.assert_awaited_once()

    def test_pause_workload_not_active(self):
        workload = self._make_workload("workload2", "a2")
        self.orc.inactive_workloads["a2"] = workload
        result = asyncio.run(self.orc.pause_workload("workload2"))
        self.assertFalse(result)

    def test_pause_workload_not_found(self):
        result = asyncio.run(self.orc.pause_workload("ghost"))
        self.assertFalse(result)

    def test_resume_workload(self):
        workload = self._make_workload("workload1", "a1")
        workload.resume = AsyncMock()
        self.orc.active_workloads["a1"] = workload
        result = asyncio.run(self.orc.resume_workload("workload1"))
        self.assertTrue(result)
        workload.resume.assert_awaited_once()

    def test_resume_workload_not_active(self):
        workload = self._make_workload("workload2", "a2")
        self.orc.inactive_workloads["a2"] = workload
        result = asyncio.run(self.orc.resume_workload("workload2"))
        self.assertFalse(result)

    def test_concurrent_stop_workload_calls_no_race(self):
        """Two concurrent stop_workload calls on the same workload must not corrupt dicts."""
        workload = self._make_workload("workload1", "a1")
        workload.stop = AsyncMock()
        self.orc.active_workloads["a1"] = workload

        async def race():
            results = await asyncio.gather(
                self.orc.stop_workload("a1"),
                self.orc.stop_workload("a1"),
                return_exceptions=True,
            )
            return results

        results = asyncio.run(race())
        for r in results:
            if isinstance(r, Exception):
                raise r
            self.assertIsInstance(r, bool)

        trues = sum(1 for r in results if r is True)
        self.assertEqual(trues, 1, "Exactly one stop_workload call should succeed")
        self.assertNotIn(
            "a1", self.orc.active_workloads, "Workload must not remain in active_workloads"
        )
        self.assertIn(
            "a1", self.orc.inactive_workloads, "Workload must end up in inactive_workloads"
        )
        workload.stop.assert_called_once()

    def test_concurrent_start_and_stop_no_corruption(self):
        """Concurrent start (from inactive) and stop (of different workload) don't interfere."""
        workload_a = self._make_workload("workload1", "a1")
        workload_a.boot = AsyncMock(return_value=True)
        workload_a.stop = AsyncMock()
        workload_b = self._make_workload("workload2", "a2")
        workload_b.stop = AsyncMock()
        self.orc.inactive_workloads["a1"] = workload_a
        self.orc.active_workloads["a2"] = workload_b

        async def race():
            results = await asyncio.gather(
                self.orc.start_workload_by_name("workload1"),
                self.orc.stop_workload("a2"),
                return_exceptions=True,
            )
            return results

        results = asyncio.run(race())
        for r in results:
            if isinstance(r, Exception):
                raise r
            self.assertTrue(r, "Both operations should succeed")

        self.assertIn(
            "a1", self.orc.active_workloads, "Workload1 should be active after start"
        )
        self.assertIn(
            "a2", self.orc.inactive_workloads, "Workload2 should be inactive after stop"
        )
