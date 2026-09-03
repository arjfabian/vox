"""Runtime-silo architecture enforcement.

The runtime layer is the process composition root: it wires the top-level
components (``factory.py``), runs the daemon loop (``daemon.py``), and exposes
the UDS control plane (``control_plane.py``). These tests guard the runtime
boundary:

* submodules import from concrete leaf modules, never from the package
  ``__init__`` (acyclic imports — no circular re-entry);
* no parallel runtime configuration dataclass duplicates ``vox.config.VOXConfig``;
* runtime code interacts with ``VOXOrchestrator``/``VOXAPIServer`` through their
  public surface only — never their privates;
* ``VOXRuntime`` is a pure composition container with a fixed wiring shape.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from vox.runtime.models import VOXRuntime

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "src" / "vox" / "runtime"


class TestRuntimeSubmoduleImportsAreAcyclic(unittest.TestCase):
    """Runtime submodules must not re-import the package public surface.

    Importing from ``vox.runtime`` (the package ``__init__``) inside a submodule
    re-enters the composition graph and requires import-order hacks. Leaf
    submodule imports are the target form.
    """

    def test_no_submodule_imports_from_package_init(self):
        forbidden = re.compile(r"from\s+vox\.runtime\s+import|import\s+vox\.runtime\s*$")
        violations = []
        for py in RUNTIME.glob("*.py"):
            if py.name == "__init__.py":
                continue
            text = py.read_text()
            for line in text.splitlines():
                if forbidden.search(line):
                    violations.append((str(py), line.strip()))
        self.assertEqual(violations, [])

    def test_package_init_imports_submodules_alphabetically(self):
        init_py = (RUNTIME / "__init__.py").read_text()
        # No ordering-suppressing noqa should remain now that the cycle is gone.
        self.assertNotIn("noqa: I001", init_py)


class TestRuntimeConfigOwnership(unittest.TestCase):
    """Runtime must not declare a parallel config duplicating vox.config."""

    def test_no_parallel_runtime_config_dataclass(self):
        for py in RUNTIME.glob("*.py"):
            text = py.read_text()
            self.assertNotIn("VOXRuntimeConfig", text, str(py))
            self.assertNotIn("keepalive_interval", text, str(py))
            self.assertNotIn("uds_path: Path", text, str(py))

    def test_uds_comes_from_vox_config_only(self):
        # control_plane must read the process UDS path from the composed
        # VOXRuntime.config (a VOXConfig), not from a runtime-local Path.
        cp = (RUNTIME / "control_plane.py").read_text()
        self.assertIn("runtime.config.uds_path", cp)
        self.assertIn("from vox.runtime.models import VOXRuntime", cp)


class TestRuntimeNoPrivateReach(unittest.TestCase):
    """Runtime interacts with composed components via public surface only."""

    def test_no_private_orchestrator_reach(self):
        forbidden = re.compile(
            r"\._guardrail\b|\._orc\b|\._registry\b|\._controller\b|\._war_room\b"
        )
        violations = []
        for py in RUNTIME.glob("*.py"):
            text = py.read_text()
            hits = [h for h in forbidden.findall(text) if h]
            if hits:
                violations.append((str(py), hits))
        self.assertEqual(violations, [])

    def test_control_plane_uses_only_public_fleet_operations(self):
        cp = (RUNTIME / "control_plane.py").read_text()
        for public in (
            "get_fleet_snapshot",
            "resolve_workload_id",
            "stop_workload",
            "restart_workload",
            "start_workload_by_name",
            "pause_workload",
            "resume_workload",
        ):
            self.assertIn(public, cp, public)
        self.assertNotIn("_guardrail", cp)


class TestRuntimeIsCompositionContainer(unittest.TestCase):
    """VOXRuntime holds exactly the wired components and nothing more."""

    def test_runtime_holds_only_wiring_fields(self):
        fields = {f.name for f in VOXRuntime.__dataclass_fields__.values()}
        self.assertEqual(
            fields,
            {"config", "logger", "orchestrator", "api_server"},
        )

    def test_runtime_is_a_plain_dataclass(self):
        import dataclasses

        self.assertTrue(dataclasses.is_dataclass(VOXRuntime))
        # No methods of its own beyond the generated dataclass surface.
        own_methods = {
            k
            for k in vars(VOXRuntime)
            if not k.startswith("_") and callable(getattr(VOXRuntime, k, None))
        }
        self.assertEqual(own_methods, set())