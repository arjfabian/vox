import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from vox.orchestration.base import (
    VOXOrchestrator,
    CapabilityEntry,
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

    def test_init_sets_up_directories(self):
        base = Path(__file__).resolve().parent.parent / "src" / "vox"
        self.assertEqual(self.orc.capabilities_dir, base / "capabilities")
        self.assertEqual(self.orc.agents_dir, base / "agents")

    def test_init_initialises_empty_registries(self):
        self.assertEqual(self.orc.capability_registry, {})
        self.assertEqual(self.orc.active_agents, {})
        self.assertEqual(self.orc.inactive_agents, {})

    def test_init_loads_speaker_profile(self):
        self.assertIsNotNone(self.orc.speaker_profile)

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
            cls=MagicMock, healthy=True,
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
            cls=MagicMock, healthy=True, instance=cap,
        )

        asyncio.run(self.orc.shutdown())

        cap.shutdown.assert_awaited_once()

    def test_shutdown_skips_unloaded_capabilities(self):
        cap = MagicMock()
        self.orc.capability_registry["unloaded.cap"] = CapabilityEntry(
            cls=MagicMock, healthy=True, instance=None,
        )
        asyncio.run(self.orc.shutdown())
        self.logger.ok.assert_any_call("VOX fleet shut down.")
