import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from vox.capabilities.base import VOXCapability, VOXBoundCapability


class TestVOXCapability(unittest.TestCase):

    def test_default_health_check_passes(self):
        self.assertTrue(asyncio.run(VOXCapability.health_check()))

    def test_default_boot_shutdown_noop(self):
        cap = VOXCapability()
        asyncio.run(cap.boot())
        asyncio.run(cap.shutdown())

    def test_params_initialized_as_class_attributes(self):
        """PARAMS keys become instance attributes with default values."""
        cap = VOXCapability()
        cap.id = "test"
        cap.logger = MagicMock()
        for key, (desc, default) in cap.PARAMS.items():
            self.assertTrue(
                hasattr(cap, key),
                f"Expected {key} to be set from PARAMS",
            )
            self.assertEqual(getattr(cap, key), default)

    def test_mount_creates_bound(self):
        cap = VOXCapability()
        agent = MagicMock()
        bound = cap.mount(agent, {})
        self.assertIsInstance(bound, VOXBoundCapability)

    def test_initialize_raises_for_missing_required_params(self):
        class StrictCap(VOXCapability):
            PARAMS = {"REQUIRED_KEY": ["desc", None]}
        cap = StrictCap()
        cap.id = "test"
        cap.logger = MagicMock()
        agent = MagicMock()
        bound = cap.mount(agent, {})
        with self.assertRaises(ValueError):
            asyncio.run(bound.initialize())

    def test_explain_config_returns_string(self):
        text = VOXCapability.explain_config()
        self.assertIsInstance(text, str)
        self.assertIn("Requirements for", text)

    def test_get_params_returns_list(self):
        class TestCap(VOXCapability):
            PARAMS = {"A": ["desc a", 1], "B": ["desc b", 2]}
        params = TestCap.get_params()
        self.assertEqual(params, ["A", "B"])


class TestVOXBoundCapability(unittest.TestCase):

    def setUp(self):
        self.agent = MagicMock()
        self.agent.logger = MagicMock()
        self.agent.emit = AsyncMock()
        self.cap = VOXCapability()
        self.cap.id = "test"
        self.cap.logger = MagicMock()
        self.bound = VOXBoundCapability(self.cap, self.agent, {})

    def test_capability_name(self):
        self.assertEqual(self.bound.name, "VOXCapability")
        self.bound._capability.CAPABILITY_NAME = "custom"
        self.assertEqual(self.bound.name, "custom")

    def test_delegates_params(self):
        """Accessing unknown attr falls through to capability."""
        self.cap.custom_attr = 42
        self.assertEqual(self.bound.custom_attr, 42)

    def test_log_delegates_to_logger(self):
        self.bound.log("hello")
        self.bound.logger.info.assert_called_once()

    def test_warning_delegates_to_logger(self):
        self.bound.warning("warn")
        self.bound.logger.warning.assert_called_once()

    def test_error_delegates_to_logger(self):
        self.bound.error("err")
        self.bound.logger.error.assert_called_once()

    def test_health_check_delegates(self):
        with unittest.mock.patch.object(
            VOXCapability, "health_check", return_value=False
        ):
            self.assertFalse(asyncio.run(self.bound.health_check()))

    def test_freeze_prevents_setattr(self):
        self.bound.freeze()
        with self.assertRaises(AttributeError):
            self.bound.new_attr = "forbidden"

    def test_ok_delegates_to_logger(self):
        self.bound.ok("all good")
        self.bound.logger.ok.assert_called_once()

    def test_get_safe_path_delegates_to_agent(self):
        self.agent.get_safe_path.return_value = "/safe/path"
        result = self.bound.get_safe_path("evidence", "test.png")
        self.agent.get_safe_path.assert_called_once_with("evidence", "test.png")
        self.assertEqual(result, "/safe/path")

    def test_emit_delegates_to_agent(self):
        self.agent.orchestrator = None
        asyncio.run(self.bound.emit("inbound_message", content="hi"))
        self.agent.emit.assert_called_once_with("inbound_message", content="hi")

    def test_get_capability_found(self):
        self.agent.capabilities = {"ollama": "ollama_instance"}
        result = self.bound.get_capability("ollama")
        self.assertEqual(result, "ollama_instance")

    def test_get_capability_not_found(self):
        self.agent.capabilities = {}
        result = self.bound.get_capability("nonexistent")
        self.assertIsNone(result)


class TestVOXBoundCapabilityEdgeCases(unittest.TestCase):

    def setUp(self):
        self.agent = MagicMock()
        self.agent.logger = MagicMock()
        self.cap = VOXCapability()
        self.cap.id = "test"
        self.cap.logger = MagicMock()

    def test_ok_without_logger_set(self):
        bound = VOXBoundCapability(self.cap, self.agent, {})
        bound.logger = MagicMock()
        bound.ok("ok msg")
        bound.logger.ok.assert_called_once()

    def test_get_safe_path_no_agent_get_safe_path(self):
        del self.agent.get_safe_path
        bound = VOXBoundCapability(self.cap, self.agent, {})
        with self.assertRaises(AttributeError):
            bound.get_safe_path("a", "b")

    def test_emit_with_orchestrator_delegates_to_dispatch(self):
        self.agent.orchestrator = MagicMock()
        self.agent.orchestrator.dispatch_inbound_message = AsyncMock()
        bound = VOXBoundCapability(self.cap, self.agent, {})
        bound.logger = MagicMock()
        asyncio.run(bound.emit("evt"))
        self.agent.orchestrator.dispatch_inbound_message.assert_awaited_once()
