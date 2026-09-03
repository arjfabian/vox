"""Workloads-silo architecture enforcement.

VOXWorkload is the sole host implementation of ``CapabilityHostProtocol``: it
owns host-facing services and workload-local state. This module guards the
workload layer's boundaries:

* degradation state is encapsulated behind a public read-only ``degraded``
  accessor (the host protocol mutates it via ``mark_degraded`` only) — external
  layers must never read ``_degraded`` directly;
* the workload silo depends only on the ``vox.provider`` protocol abstractions,
  never on orchestrator internals.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from vox.workloads.base import VOXWorkload

SRC = Path(__file__).resolve().parent.parent / "src"


def _make_messenger_mocks():
    """comm.gateway mock so a workload mounts system capability and stays healthy."""
    mock_cap = MagicMock()
    mock_bound = MagicMock()
    mock_bound.initialize = MagicMock()
    mock_bound.boot = MagicMock()
    mock_bound.validate_params = MagicMock(return_value=[])
    mock_cap.mount.return_value = mock_bound
    mock_cap.CAPABILITY_NAME = "comm.gateway"
    mock_cap.get_secret_names.return_value = []
    mock_bound._capability = mock_cap
    mock_bound.get_exposed_commands.return_value = []
    return mock_cap, mock_bound


class TestDegradationEncapsulation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_workload_arch_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "roles" / "chat.py").write_text(
            "from vox.roles import VOXRole\n\n"
            "class TestRole(VOXRole):\n"
            "    REQUIRES = set()\n"
        )
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-1234\nautostart: false\n"
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        mock_cap, _ = _make_messenger_mocks()
        self.orchestrator.get_capability_instance.return_value = mock_cap

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_degraded_is_read_only_public_accessor(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(workload.degraded)
        # mutation goes through the host-protocol service, not direct assignment
        with self.assertRaises(AttributeError):
            workload.degraded = True

    def test_mark_degraded_flips_the_flag(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(workload.degraded)
        workload.mark_degraded()
        self.assertTrue(workload.degraded)
        self.assertFalse(workload.health_check())


class TestWorkloadSiloBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_workload_arch_boundary_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "roles" / "chat.py").write_text(
            "from vox.roles import VOXRole\n\n"
            "class TestRole(VOXRole):\n"
            "    REQUIRES = set()\n"
        )
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-1234\nautostart: false\n"
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        mock_cap, _ = _make_messenger_mocks()
        self.orchestrator.get_capability_instance.return_value = mock_cap

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_workload_silo_does_not_import_orchestration_internals(self):
        import re

        workloads_dir = SRC / "vox" / "workloads"
        forbidden = re.compile(
            r"from\s+vox\.orchestration"
            r"|import\s+vox\.orchestration"
            r"|VOXOrchestrator\b"
        )
        violations = []
        for py in workloads_dir.glob("*.py"):
            text = py.read_text()
            if forbidden.search(text):
                violations.append(str(py))
        self.assertEqual(violations, [])

    def test_degraded_is_a_public_property(self):
        prop = VOXWorkload.__dict__.get("degraded")
        self.assertIsInstance(prop, property)

    def test_describe_reports_degraded(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        workload.mark_degraded()
        self.assertTrue(workload.describe()["degraded"])
