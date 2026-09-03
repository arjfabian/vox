"""Orchestration-silo architecture enforcement.

Orchestration owns routing policy and fleet-level coordination. It is a
consumer of the public workload contract — never of workload private
implementation details. This module guards that boundary:

* orchestration code must not reference workload/``WorkloadVault`` privates
  (``_degraded``, ``_tasks``, ``_vault``, ``vault._key``, ...);
* lifecycle/resource/panic operations go through public workload-owned APIs
  (``cancel_tasks``, ``purge_vault``), never workload internals.
"""

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
ORCH = SRC / "vox" / "orchestration"

# Attributes a consumer must never read off a workload / WorkloadVault.
FORBIDDEN_WORKLOAD_PRIVATE = re.compile(
    r"\._degraded\b"
    r"|\._tasks\b"
    r"|\._vault\b"
    r"|vault\._key\b"
    r"|\._capability_commands\b"
    r"|\._system_capabilities\b"
)

ORCHESTRATION_FILES = [p for p in ORCH.glob("*.py") if p.name != "__init__.py"]


class TestNoWorkloadPrivateAccess(unittest.TestCase):
    def test_orchestration_never_reaches_workload_or_vault_privates(self):
        violations = []
        for py in ORCHESTRATION_FILES:
            text = py.read_text()
            for match in FORBIDDEN_WORKLOAD_PRIVATE.finditer(text):
                violations.append(f"{py.name}:{match.group(0)}")
        self.assertEqual(violations, [])


class TestUsesPublicWorkloadApis(unittest.TestCase):
    def test_panic_path_uses_workload_owned_apis(self):
        """panic_shutdown must delegate teardown to public workload APIs."""
        text = (ORCH / "base.py").read_text()
        # It must not iterate workload._tasks or read workload._vault.
        self.assertNotIn("workload._tasks", text)
        self.assertNotIn("workload._vault", text)
        # It must call the public workload-owned teardown methods.
        self.assertIn(".cancel_tasks()", text)
        self.assertIn(".purge_vault()", text)
