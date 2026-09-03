"""Roles-domain architecture enforcement.

The role contract lives in ``src/vox/roles/`` as a leaf package; the role
runtime is owned by the workloads silo. These tests guard the roles boundary:

* the ``VOXRole`` contract exposes a public routing surface
  (``get_route_names`` / ``get_commands``) that the workload host uses, so the
  host never needs to reach ``role._handlers``;
* the roles contract is a leaf (imports no internal vox modules);
* role code never reaches orchestration/fleet topology.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from vox.roles import VOXRole, command

ROOT = Path(__file__).resolve().parent.parent
ROLES = ROOT / "src" / "vox" / "roles"
WORKLOADS = ROOT / "src" / "vox" / "workloads"


class TestRoleContractSurface(unittest.TestCase):
    """VOXRole exposes the routing surface the host needs, without privates."""

    def test_role_exposes_public_route_enumeration(self):
        self.assertTrue(hasattr(VOXRole, "get_route_names"))
        self.assertTrue(hasattr(VOXRole, "get_commands"))
        self.assertTrue(hasattr(VOXRole, "handle_event"))

    def test_route_names_cover_commands_and_events(self):
        class _R(VOXRole):
            @command("do_it", description="Does it")
            async def do_it(self, **kwargs):
                pass

        class _FakeHost:
            pass

        role = _R(_FakeHost())

        @role.on("some_event")
        async def _handler(**kwargs):
            pass

        names = role.get_route_names()
        self.assertIn("do_it", names)
        self.assertIn("some_event", names)
        self.assertIn("do_it", role.get_commands())

    def test_route_names_is_a_snapshot(self):
        class _FakeHost:
            pass

        role = VOXRole(_FakeHost())
        names = role.get_route_names()
        names.add("mutated")
        self.assertNotIn("mutated", role.get_route_names())


class TestWorkloadHostUsesPublicRoleSurface(unittest.TestCase):
    """The host builds its event router without reaching role privates."""

    def test_no_role_private_handlers_read_in_host(self):
        base_py = WORKLOADS / "base.py"
        source = base_py.read_text()
        # The host must not touch role._handlers; it uses get_route_names().
        self.assertIn("get_route_names", source)
        self.assertNotIn('getattr(role, "_handlers"', source)
        self.assertNotIn("role._handlers", source)


class TestRolesContractIsLeaf(unittest.TestCase):
    """src/vox/roles imports no internal vox modules (outward-coupling free)."""

    def test_roles_contract_imports_only_stdlib(self):
        forbidden = re.compile(
            r"\bfrom\s+vox\.|\bimport\s+vox\.|VOXWorkload\b|VOXOrchestrator\b"
        )
        violations = []
        for py in ROLES.glob("*.py"):
            text = py.read_text()
            if forbidden.search(text):
                violations.append(str(py))
        self.assertEqual(violations, [])


class TestWorkloadHostConcreteOrchestrationReach(unittest.TestCase):
    """The host's role path must not couple to concrete orchestration."""

    def test_no_concrete_orchestration_in_role_host_path(self):
        base_py = WORKLOADS / "base.py"
        source = base_py.read_text()
        forbidden = re.compile(
            r"VOXOrchestrator\b|FleetGraph\b|VOXRegistry\b|FleetController\b"
        )
        hits = forbidden.findall(source)
        self.assertEqual(hits, [])