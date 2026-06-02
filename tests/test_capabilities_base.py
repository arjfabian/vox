import asyncio
import unittest
from unittest.mock import MagicMock

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

    def test_mount_with_missing_required_params(self):
        class StrictCap(VOXCapability):
            PARAMS = {"REQUIRED_KEY": ["desc", None]}
        cap = StrictCap()
        agent = MagicMock()
        with self.assertRaises(ValueError):
            cap.mount(agent, {})

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
