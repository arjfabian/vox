import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.capabilities.base import (
    CapabilityContract,
    ParamMeta,
    VOXBoundCapability,
    VOXCapability,
)


class TestVOXCapability(unittest.TestCase):
    def test_default_health_check_passes(self):
        self.assertTrue(asyncio.run(VOXCapability.health_check()))

    def test_default_boot_shutdown_noop(self):
        cap = VOXCapability()
        asyncio.run(cap.boot())
        asyncio.run(cap.shutdown())

    def test_mount_creates_bound(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            cap_file = tmpdir / "capability.py"
            cap_file.write_text("# placeholder")
            yml = tmpdir / "capability.yml"
            yml.write_text("name: test.cap\nparams:\n  X:\n    description: x\n    type: string\n    default: hello\n")

            class MountCap(VOXCapability):
                CAPABILITY_NAME = "test.cap"

            MountCap.load_contract(cap_file)
            cap = MountCap()
            cap.id = "test.cap"
            cap.logger = MagicMock()
            workload = MagicMock()
            bound = cap.mount(workload)
            self.assertIsInstance(bound, VOXBoundCapability)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_initialize_raises_for_missing_required_params(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            cap_file = tmpdir / "capability.py"
            cap_file.write_text("# placeholder")
            yml = tmpdir / "capability.yml"
            yml.write_text(
                "name: strict.cap\n"
                "params:\n"
                "  REQUIRED_KEY:\n"
                "    description: Required\n"
                "    type: string\n"
                "    default: null\n"
            )

            class StrictCap(VOXCapability):
                CAPABILITY_NAME = "strict.cap"

            StrictCap.load_contract(cap_file)
            cap = StrictCap()
            cap.id = "strict.cap"
            cap.logger = MagicMock()
            workload = MagicMock()
            bound = cap.mount(workload)
            with self.assertRaises(ValueError):
                asyncio.run(bound.initialize())
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_explain_config_returns_string(self):
        class ExplainedCap(VOXCapability):
            CAPABILITY_NAME = "test.explain"

        ExplainedCap._contract = CapabilityContract(
            name="test.explain",
            params={"X": ParamMeta(name="X", description="an x", type="string", default=42)},
        )
        text = ExplainedCap.explain_config()
        self.assertIsInstance(text, str)
        self.assertIn("Requirements for", text)

    def test_get_params_returns_list(self):
        class TestCap(VOXCapability):
            CAPABILITY_NAME = "test.get_params"

        TestCap._contract = CapabilityContract(
            name="test.get_params",
            params={
                "A": ParamMeta(name="A", description="desc a", type="string", default=1),
                "B": ParamMeta(name="B", description="desc b", type="string", default=2),
            },
        )

        params = TestCap.get_params()
        self.assertEqual(params, ["A", "B"])


class TestVOXBoundCapability(unittest.TestCase):
    def setUp(self):
        self.workload = MagicMock()
        self.workload.logger = MagicMock()
        self.workload.emit = AsyncMock()
        self._orig_contract = VOXCapability.__dict__.get("_contract")
        VOXCapability._contract = CapabilityContract(name="test.bound")
        self.cap = VOXCapability()
        self.cap.id = "test"
        self.cap.logger = MagicMock()
        self.bound = VOXBoundCapability(self.cap, self.workload, {})

    def tearDown(self):
        VOXCapability._contract = self._orig_contract

    def test_capability_name(self):
        self.assertEqual(self.bound.name, "test.bound")
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

    def test_get_safe_path_delegates_to_workload(self):
        self.workload.get_safe_path.return_value = "/safe/path"
        result = self.bound.get_safe_path("evidence", "test.png")
        self.workload.get_safe_path.assert_called_once_with("evidence", "test.png")
        self.assertEqual(result, "/safe/path")

    def test_emit_delegates_to_workload_removed(self):
        """Capabilities must not emit workload events through bound internals;
        the host interface is the only coupling surface."""
        self.assertFalse(hasattr(self.bound, "emit"))

    def test_get_capability_found(self):
        self.workload.get_capability.return_value = "ollama_instance"
        result = self.bound.get_capability("ollama")
        self.assertEqual(result, "ollama_instance")

    def test_get_capability_not_found(self):
        self.workload.get_capability.return_value = None
        result = self.bound.get_capability("nonexistent")
        self.assertIsNone(result)


class TestVOXBoundCapabilityEdgeCases(unittest.TestCase):
    def setUp(self):
        self.workload = MagicMock()
        self.workload.logger = MagicMock()
        self._orig_contract = VOXCapability.__dict__.get("_contract")
        VOXCapability._contract = CapabilityContract(name="test.edge")
        self.cap = VOXCapability()
        self.cap.id = "test"
        self.cap.logger = MagicMock()

    def tearDown(self):
        VOXCapability._contract = self._orig_contract

    def test_ok_without_logger_set(self):
        bound = VOXBoundCapability(self.cap, self.workload, {})
        bound.logger = MagicMock()
        bound.ok("ok msg")
        bound.logger.ok.assert_called_once()

    def test_get_safe_path_no_workload_get_safe_path(self):
        """Delegating to a host that lacks the service surfaces AttributeError."""
        self.workload.configure_mock(**{"get_safe_path.side_effect": AttributeError("no host get_safe_path")})
        bound = VOXBoundCapability(self.cap, self.workload, {})
        with self.assertRaises(AttributeError):
            bound.get_safe_path("a", "b")
