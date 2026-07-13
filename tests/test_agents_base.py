import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.agents.base import VOXAgent, AgentProvisionError
from vox.agents.lifecycle import AgentState
from vox.capabilities.base import VOXCapability


class TestVOXAgentInit(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_agent_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "agent.yml").write_text("name: TestAgent\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.orchestrator = MagicMock()

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_init_sets_basic_attributes(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(agent.dir, self.tmp)
        self.assertEqual(agent.state, AgentState.IDLE)
        self.assertIsNotNone(agent.memory)
        self.assertIsNotNone(agent.store)

    def test_name_from_config(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(agent.name, "TestAgent")

    def test_id_from_config(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(agent.id, "test-uuid-1234")

    def test_master_id_none_by_default(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertIsNone(agent.master_id)

    def test_must_start_defaults_false(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(agent.must_start)

    def test_must_start_from_config(self):
        (self.tmp / "agent.yml").write_text(
            "name: TestAgent\nid: test-uuid-1234\nautostart: true\n"
        )
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertTrue(agent.must_start)

    def test_conversational_defaults_false(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(agent.conversational)

    def test_orchestrator_property(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertIs(agent.orchestrator, self.orchestrator)

    def test_orchestrator_none_allowed(self):
        agent = VOXAgent(self.tmp, self.logger, None)
        self.assertIsNone(agent.orchestrator)

    def test_health_check_empty_roles(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(agent.health_check())

    def test_describe_includes_basic_fields(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        desc = agent.describe()
        self.assertEqual(desc["name"], "TestAgent")
        self.assertEqual(desc["state"], "IDLE")
        self.assertIn("capabilities", desc)
        self.assertIn("commands", desc)
        self.assertIn("subordinates", desc)


class TestVOXAgentManifest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_manifest_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        self.logger = MagicMock()
        self.orchestrator = MagicMock()

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_missing_manifest_raises(self):
        with self.assertRaises(AgentProvisionError):
            VOXAgent(self.tmp, self.logger, self.orchestrator)

    def test_invalid_yaml_raises(self):
        (self.tmp / "agent.yml").write_text(":: invalid yaml ::")
        with self.assertRaises(AgentProvisionError):
            VOXAgent(self.tmp, self.logger, self.orchestrator)

    def test_missing_name_in_manifest_raises(self):
        (self.tmp / "agent.yml").write_text("id: test-uuid\n")
        with self.assertRaises(AgentProvisionError):
            VOXAgent(self.tmp, self.logger, self.orchestrator)

    def test_missing_id_in_manifest_raises(self):
        (self.tmp / "agent.yml").write_text("name: Test\n")
        with self.assertRaises(AgentProvisionError):
            VOXAgent(self.tmp, self.logger, self.orchestrator)

    def test_wrong_type_for_name_raises(self):
        (self.tmp / "agent.yml").write_text("name: 42\nid: test-uuid\n")
        with self.assertRaises(AgentProvisionError):
            VOXAgent(self.tmp, self.logger, self.orchestrator)

    def test_log_source_returns_valid_source(self):
        (self.tmp / "agent.yml").write_text("name: TestAgent\nid: test-uuid\n")
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        src = agent.log_source
        self.assertEqual(src.source_type, "agent")
        self.assertEqual(src.source_name, "testagent")


class TestVOXAgentStateTransitions(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_state_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "agent.yml").write_text("name: TestAgent\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.agent = VOXAgent(self.tmp, self.logger, self.orchestrator)

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_initial_state_is_idle(self):
        self.assertEqual(self.agent.state, AgentState.IDLE)

    def test_valid_transition(self):
        self.agent.state = AgentState.STOPPING
        self.assertEqual(self.agent.state, AgentState.STOPPING)

    def test_invalid_transition_raises(self):
        with self.assertRaises(RuntimeError):
            self.agent.state = AgentState.ACTIVE

    def test_boot_sets_active(self):
        result = asyncio.run(self.agent.boot())
        self.assertTrue(result)
        self.assertEqual(self.agent.state, AgentState.ACTIVE)

    def test_stop_sets_stopped(self):
        asyncio.run(self.agent.boot())
        asyncio.run(self.agent.stop())
        self.assertEqual(self.agent.state, AgentState.STOPPED)

    def test_pause_and_resume(self):
        asyncio.run(self.agent.boot())
        asyncio.run(self.agent.pause())
        self.assertEqual(self.agent.state, AgentState.PAUSED)
        asyncio.run(self.agent.resume())
        self.assertEqual(self.agent.state, AgentState.ACTIVE)


class TestVOXAgentSafePath(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_path_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "agent.yml").write_text("name: TestAgent\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.agent = VOXAgent(self.tmp, self.logger, MagicMock())

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_get_safe_path_within_sandbox(self):
        path = self.agent.get_safe_path("evidence", "test.png")
        self.assertTrue(str(path).endswith("test.png"))
        self.assertIn("assets", str(path))

    def test_get_safe_path_prevents_escape(self):
        with self.assertRaises(PermissionError):
            self.agent.get_safe_path("../../etc", "passwd")


class TestVOXAgentBootCapabilities(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_boot_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "agent.yml").write_text("name: TestAgent\nid: test-uuid-1234\n")
        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"test_cap"}\n'
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.mock_cap = MagicMock()
        self.mock_bound = MagicMock()
        self.mock_bound.initialize = AsyncMock()
        self.mock_bound.boot = AsyncMock()
        self.mock_cap.mount.return_value = self.mock_bound
        self.mock_cap.CAPABILITY_NAME = ""
        type(self.mock_cap).EXPOSED_COMMANDS = []
        self.orchestrator.get_capability_instance.return_value = self.mock_cap

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_boot_calls_capability_initialize_and_boot(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(agent.boot())
        self.assertTrue(result)
        self.mock_bound.initialize.assert_awaited_once()
        self.mock_bound.boot.assert_awaited_once()

    def test_boot_failure_when_capability_fails(self):
        self.mock_bound.initialize.side_effect = Exception("fail")
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(agent.boot())
        self.assertFalse(result)
        self.assertEqual(agent.state, AgentState.FAILED)


class TestVOXAgentDegradedParams(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_degraded_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "agent.yml").write_text(
            "name: TestAgent\nid: test-uuid-5678\nautostart: false\n"
        )
        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"strict_cap"}\n'
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()

        class _StrictCap(VOXCapability):
            CAPABILITY_NAME = "strict_cap"
            PARAMS = {"API_KEY": ["Required API key", None]}

        self.cap = _StrictCap()
        self.cap.id = "strict_cap"
        self.cap.logger = self.logger
        self.orchestrator.get_capability_instance.return_value = self.cap

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_missing_required_param_routes_to_degraded(self):
        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertTrue(agent._degraded)
        self.assertFalse(agent.health_check())
        self.assertIn("strict_cap", agent.capabilities)
        agent_logger = self.logger.get_child()
        agent_logger.warning.assert_any_call(
            "Capability 'strict_cap' missing required params: ['API_KEY'] — "
            "agent will be degraded"
        )

    def test_missing_params_plus_missing_cap_does_not_crash(self):
        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"missing_cap", "strict_cap"}\n'
        )

        def _get_cap(cap_id):
            if cap_id == "missing_cap":
                return None
            return self.cap
        self.orchestrator.get_capability_instance.side_effect = _get_cap

        agent = VOXAgent(self.tmp, self.logger, self.orchestrator)
        self.assertTrue(agent._degraded)
        self.assertFalse(agent.health_check())
        self.assertNotIn("missing_cap", agent.capabilities)
        self.assertIn("strict_cap", agent.capabilities)
