import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.agents.base import VOXAgent
from vox.orchestration.base import (
    CapabilityEntry,
    VOXOrchestrator,
)


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
        self.assertEqual(self.orc.active_agents, {})
        self.assertEqual(self.orc.inactive_agents, {})

    def test_init_loads_speaker_profile(self):
        self.assertIsNotNone(self.orc.speaker_profile)

    def test_init_with_injectable_dirs(self):
        caps_dir = Path("/custom/capabilities")
        agents_dir = Path("/custom/agents")
        identity_dir = Path("/custom/identity")
        orc = VOXOrchestrator(
            config=self.config,
            logger=self.logger,
            capabilities_dir=caps_dir,
            agents_dir=agents_dir,
            identity_dir=identity_dir,
        )
        self.assertEqual(orc.capabilities_dir, caps_dir)
        self.assertEqual(orc.agents_dir, agents_dir)
        self.assertEqual(orc.identity_dir, identity_dir)

    def test_injectable_dirs_fall_back_to_defaults(self):
        base = Path(__file__).resolve().parent.parent / "src" / "vox"
        self.assertEqual(self.orc.capabilities_dir, base / "capabilities")
        self.assertEqual(
            self.orc.agents_dir, Path(__file__).resolve().parent.parent / "agents"
        )

    def test_all_agents_merges_active_and_inactive(self):
        agent_a = MagicMock()
        agent_b = MagicMock()
        self.orc.active_agents["a1"] = agent_a
        self.orc.inactive_agents["a2"] = agent_b
        merged = self.orc._all_agents
        self.assertIn("a1", merged)
        self.assertIn("a2", merged)
        self.assertIs(merged["a1"], agent_a)
        self.assertIs(merged["a2"], agent_b)

    def test_all_agents_reflects_changes(self):
        agent = MagicMock()
        self.orc.inactive_agents["a1"] = agent
        self.assertIn("a1", self.orc._all_agents)
        self.orc.active_agents["a1"] = agent
        del self.orc.inactive_agents["a1"]
        self.assertIn("a1", self.orc._all_agents)

    def test_all_agents_empty_when_none(self):
        self.assertEqual(self.orc._all_agents, {})

    def test_get_fleet_snapshot_structure(self):
        snapshot = self.orc.get_fleet_snapshot()
        self.assertIn("system", snapshot)
        self.assertIn("capabilities", snapshot)
        self.assertIn("agents", snapshot)
        self.assertIn("hierarchy", snapshot)

    def test_get_fleet_snapshot_system(self):
        snapshot = self.orc.get_fleet_snapshot()
        system = snapshot["system"]
        self.assertEqual(system["version"], "VOX+1.0")
        self.assertIn("speaker_profile", system)

    def test_get_fleet_snapshot_no_agents(self):
        snapshot = self.orc.get_fleet_snapshot()
        self.assertEqual(snapshot["agents"], [])
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

    def test_shutdown_active_agent_error_does_not_propagate(self):
        agent = MagicMock()
        agent.shutdown = AsyncMock(side_effect=RuntimeError("oops"))
        self.orc.active_agents["test-agent"] = agent

        asyncio.run(self.orc.shutdown())

        agent.shutdown.assert_awaited_once()
        self.logger.error.assert_called_once()

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
        self._tmp_agents: list[Path] = []

    def tearDown(self):
        import shutil

        for p in self._tmp_agents:
            shutil.rmtree(p, ignore_errors=True)

    def _make_agent(self, name: str, agent_id: str):
        agent = MagicMock()
        agent.name = name
        agent.id = agent_id
        return agent

    def _make_real_agent_dir(self, name: str, agent_id: str) -> tuple[Path, MagicMock]:
        tmp = Path("/tmp") / f"test_orc_agent_{id(self)}_{name}"
        (tmp / "roles").mkdir(parents=True, exist_ok=True)
        (tmp / "agent.yml").write_text(
            f"name: {name}\nid: {agent_id}\nautostart: true\n"
        )
        (tmp / "roles" / "main.py").write_text(
            "from vox.roles import VOXRole\n"
            "REQUIRES = set()\n"
            "class MainRole(VOXRole):\n"
            "    def __init__(self, agent): super().__init__(agent)\n"
            "    def get_commands(self): return {}\n"
        )
        (tmp / ".env").write_text("TELEGRAM_BOT_TOKEN=dummy\nTELEGRAM_USER_ID=dummy\n")
        self._tmp_agents.append(tmp)
        agent = VOXAgent(
            tmp,
            logger=self.logger,
            orchestrator=MagicMock(),
        )
        return tmp, agent

    def test_resolve_agent_id_by_name(self):
        agent = self._make_agent("tina", "a1")
        self.orc.active_agents["a1"] = agent
        self.assertEqual(self.orc.resolve_agent_id("tina"), "a1")

    def test_resolve_agent_id_case_insensitive(self):
        agent = self._make_agent("Tina", "a1")
        self.orc.active_agents["a1"] = agent
        self.assertEqual(self.orc.resolve_agent_id("tina"), "a1")

    def test_resolve_agent_id_none_if_not_found(self):
        self.assertIsNone(self.orc.resolve_agent_id("ghost"))

    def test_resolve_agent_id_searches_inactive_too(self):
        agent = self._make_agent("leah", "a2")
        self.orc.inactive_agents["a2"] = agent
        self.assertEqual(self.orc.resolve_agent_id("leah"), "a2")

    def test_stop_agent_moves_to_inactive(self):
        agent = self._make_agent("tina", "a1")
        agent.stop = AsyncMock()
        self.orc.active_agents["a1"] = agent
        result = asyncio.run(self.orc.stop_agent("a1"))
        self.assertTrue(result)
        self.assertNotIn("a1", self.orc.active_agents)
        self.assertIn("a1", self.orc.inactive_agents)
        agent.stop.assert_awaited_once()

    def test_stop_agent_not_found(self):
        result = asyncio.run(self.orc.stop_agent("ghost"))
        self.assertFalse(result)

    def test_start_agent_by_name_moves_to_active(self):
        agent = self._make_agent("leah", "a2")
        agent.boot = AsyncMock(return_value=True)
        self.orc.inactive_agents["a2"] = agent
        result = asyncio.run(self.orc.start_agent_by_name("leah"))
        self.assertTrue(result)
        self.assertIn("a2", self.orc.active_agents)
        self.assertNotIn("a2", self.orc.inactive_agents)
        agent.boot.assert_awaited_once()

    def test_start_agent_by_name_boot_failure(self):
        agent = self._make_agent("leah", "a2")
        agent.boot = AsyncMock(return_value=False)
        self.orc.inactive_agents["a2"] = agent
        result = asyncio.run(self.orc.start_agent_by_name("leah"))
        self.assertFalse(result)
        self.assertNotIn("a2", self.orc.active_agents)

    def test_start_agent_by_name_already_active(self):
        agent = self._make_agent("tina", "a1")
        self.orc.active_agents["a1"] = agent
        result = asyncio.run(self.orc.start_agent_by_name("tina"))
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
        self.orc.capability_registry["comm.gateway"] = CapabilityEntry(
            cls=type(mock_cap),
            healthy=True,
            instance=mock_cap,
        )

    def test_restart_agent(self):
        self._register_mock_system_cap()

        _folder, agent = self._make_real_agent_dir("tina", "a1")
        agent.shutdown = AsyncMock()
        self.orc.active_agents["a1"] = agent
        result = asyncio.run(self.orc.restart_agent("tina"))
        self.assertTrue(result)
        agent.shutdown.assert_awaited_once()

    def test_restart_agent_from_inactive(self):
        self._register_mock_system_cap()

        _folder, agent = self._make_real_agent_dir("leah", "a2")
        agent.shutdown = AsyncMock()
        self.orc.inactive_agents["a2"] = agent
        result = asyncio.run(self.orc.restart_agent("leah"))
        self.assertTrue(result)
        new_agent = self.orc.active_agents.get("a2")
        self.assertIsNotNone(new_agent)
        agent.shutdown.assert_awaited_once()

    def test_restart_agent_not_found(self):
        result = asyncio.run(self.orc.restart_agent("ghost"))
        self.assertFalse(result)

    def test_pause_agent(self):
        agent = self._make_agent("tina", "a1")
        agent.pause = AsyncMock()
        self.orc.active_agents["a1"] = agent
        result = asyncio.run(self.orc.pause_agent("tina"))
        self.assertTrue(result)
        agent.pause.assert_awaited_once()

    def test_pause_agent_not_active(self):
        agent = self._make_agent("leah", "a2")
        self.orc.inactive_agents["a2"] = agent
        result = asyncio.run(self.orc.pause_agent("leah"))
        self.assertFalse(result)

    def test_pause_agent_not_found(self):
        result = asyncio.run(self.orc.pause_agent("ghost"))
        self.assertFalse(result)

    def test_resume_agent(self):
        agent = self._make_agent("tina", "a1")
        agent.resume = AsyncMock()
        self.orc.active_agents["a1"] = agent
        result = asyncio.run(self.orc.resume_agent("tina"))
        self.assertTrue(result)
        agent.resume.assert_awaited_once()

    def test_resume_agent_not_active(self):
        agent = self._make_agent("leah", "a2")
        self.orc.inactive_agents["a2"] = agent
        result = asyncio.run(self.orc.resume_agent("leah"))
        self.assertFalse(result)

    def test_concurrent_stop_agent_calls_no_race(self):
        """Two concurrent stop_agent calls on the same agent must not corrupt dicts."""
        agent = self._make_agent("tina", "a1")
        agent.stop = AsyncMock()
        self.orc.active_agents["a1"] = agent

        async def race():
            results = await asyncio.gather(
                self.orc.stop_agent("a1"),
                self.orc.stop_agent("a1"),
                return_exceptions=True,
            )
            return results

        results = asyncio.run(race())
        for r in results:
            if isinstance(r, Exception):
                raise r
            self.assertIsInstance(r, bool)

        trues = sum(1 for r in results if r is True)
        self.assertEqual(trues, 1, "Exactly one stop_agent call should succeed")
        self.assertNotIn(
            "a1", self.orc.active_agents, "Agent must not remain in active_agents"
        )
        self.assertIn(
            "a1", self.orc.inactive_agents, "Agent must end up in inactive_agents"
        )
        agent.stop.assert_called_once()

    def test_concurrent_start_and_stop_no_corruption(self):
        """Concurrent start (from inactive) and stop (of different agent) don't interfere."""
        agent_a = self._make_agent("tina", "a1")
        agent_a.boot = AsyncMock(return_value=True)
        agent_a.stop = AsyncMock()
        agent_b = self._make_agent("leah", "a2")
        agent_b.stop = AsyncMock()
        self.orc.inactive_agents["a1"] = agent_a
        self.orc.active_agents["a2"] = agent_b

        async def race():
            results = await asyncio.gather(
                self.orc.start_agent_by_name("tina"),
                self.orc.stop_agent("a2"),
                return_exceptions=True,
            )
            return results

        results = asyncio.run(race())
        for r in results:
            if isinstance(r, Exception):
                raise r
            self.assertTrue(r, "Both operations should succeed")

        self.assertIn("a1", self.orc.active_agents, "Tina should be active after start")
        self.assertIn(
            "a2", self.orc.inactive_agents, "Leah should be inactive after stop"
        )
