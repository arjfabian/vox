"""Architecture enforcement tests for the capabilities silo.

These guard the target architecture in ``.agents/architecture/capability.md``:
param/secret namespace discipline, YAML-first contract metadata, capability
identity consistency, and a narrow host interface with no reach into workload
privates. They are guardrails, not prose.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

from vox.capabilities.base import (
    CapabilityContract,
    VOXBoundCapability,
    VOXCapability,
    load_capability_yaml,
)

ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES = ROOT / "src" / "vox" / "capabilities"

# Ignored / obsolete metadata that must never reappear in capability.yml.
# A parameter may be a secret only via the ``secrets:`` namespace.
FORBIDDEN_TOP_LEVEL_KEYS = {
    "capability_class",
    "dependencies",
    "runtime",
    "module",
}

# Cross-boundary private state that capability/binding code must never touch.
# These belong solely to the workload runtime (VOXWorkload), not to the
# capability silo, whose sole coupling surface is CapabilityHostProtocol.
FORBIDDEN_ACCESS_PATTERNS = re.compile(
    r"\._workload\b"
    r"|\._capability_provider\b"
    r"|\._degraded\b"
    r"|\._capability_commands\b"
    r"|\.orchestrator\b"
)

FORBIDDEN_BOUND_PRIVATE = re.compile(r"\._capability\b")

# Files that may reference concrete workload attributes (the host
# implementation itself and its binder entry points are excluded).
BOUNDARY_FILES = [
    CAPABILITIES / "comm" / "email" / "capability.py",
    CAPABILITIES / "comm" / "gateway" / "capability.py",
    CAPABILITIES / "comm" / "voicetotext" / "capability.py",
    CAPABILITIES / "ai" / "llm" / "capability.py",
    CAPABILITIES / "net" / "browser" / "capability.py",
]


def _capability_ymls() -> list[Path]:
    return sorted(CAPABILITIES.glob("**/capability.yml"))


def _contract_for(yml: Path) -> CapabilityContract | None:
    return load_capability_yaml(yml.with_suffix(".py"))


class TestParamSecretSeparation(unittest.TestCase):
    """every capability.yml keeps params and secrets in disjoint namespaces."""

    def test_no_param_is_also_a_secret(self):
        for yml in _capability_ymls():
            contract = _contract_for(yml)
            self.assertIsNotNone(contract, yml)
            overlap = set(contract.params) & set(contract.secrets)
            self.assertEqual(overlap, set(), str(yml))

    def test_no_ignored_or_obsolete_metadata(self):
        for yml in _capability_ymls():
            raw = yaml.safe_load(yml.read_text()) or {}
            forbidden = FORBIDDEN_TOP_LEVEL_KEYS & set(raw)
            self.assertEqual(forbidden, set(), str(yml))

    def test_email_credentials_are_secrets_not_params(self):
        contract = _contract_for(
            CAPABILITIES / "comm" / "email" / "capability.yml"
        )
        self.assertTrue(contract)
        self.assertIn("SMTP_USER", contract.secrets)
        self.assertIn("SMTP_PASS", contract.secrets)
        self.assertNotIn("SMTP_USER", contract.params)
        self.assertNotIn("SMTP_PASS", contract.params)

    def test_email_exposed_commands_come_from_contract(self):
        contract = _contract_for(
            CAPABILITIES / "comm" / "email" / "capability.yml"
        )
        self.assertTrue(contract)
        names = [cmd["name"] for cmd in contract.exposed_commands]
        self.assertIn("send_email", names)

    def test_no_python_exposed_command_duplication(self):
        """Exposed commands are declared in YAML, not as Python class attrs."""
        import ast

        for py in BOUNDARY_FILES:
            if not py.exists():
                continue
            tree = ast.parse(py.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "EXPOSED_COMMANDS"
                        ):
                            self.fail(f"Python EXPOSED_COMMANDS in {py}")


class TestDeclaredConfigOnly(unittest.TestCase):
    """Capability code reads config only from its declared YAML contract.

    Every ``self.<UPPER_CASE>`` attribute a capability references at runtime must
    be declared as a ``param`` or ``secret`` in its contract. The gateway's
    contract is the aggregate of its per-adapter ``config.yml`` files (GATEWAY_*
    and adapter params/secrets), so it is resolved through ``load_contract``.
    This guards against any drift back to undeclared Python-side configuration.
    """

    def test_self_attrs_are_declared_in_contract(self):
        import re

        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        ATTR = re.compile(r"\bself\.([A-Z][A-Z0-9_]*)\b")

        for py in BOUNDARY_FILES:
            if not py.exists():
                continue
            contract = (
                CommGatewayCapability.load_contract(py)
                if py.parent.name == "gateway"
                else load_capability_yaml(py)
            )
            self.assertIsNotNone(contract, py)
            declared = set(contract.params) | set(contract.secrets)
            used = set(ATTR.findall(py.read_text()))
            undeclared = used - declared
            self.assertEqual(
                undeclared,
                set(),
                f"{py}: reads config attr(s) not declared in YAML contract: "
                f"{sorted(undeclared)}",
            )


class TestContractParsing(unittest.TestCase):
    """load_capability_yaml is strict: bad shape raises, never falls back."""

    def _write(self, text: str) -> Path:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        py = tmp / "capability.py"
        py.write_text("# placeholder\n")
        yml = tmp / "capability.yml"
        yml.write_text(text)
        return py

    def test_overlap_raises(self):
        py = self._write(
            "params:\n  TOKEN:\n    description: t\n"
            "secrets:\n  TOKEN:\n    description: t\n"
        )
        with self.assertRaises(ValueError):
            load_capability_yaml(py)

    def test_bad_provides_raises(self):
        py = self._write("provides: nope\n")
        with self.assertRaises(TypeError):
            load_capability_yaml(py)

    def test_bad_exposed_command_raises(self):
        py = self._write("exposed_commands:\n  - foo\n")
        with self.assertRaises(TypeError):
            load_capability_yaml(py)

    def test_missing_contract_returns_none(self):
        import tempfile

        py = Path(tempfile.mkdtemp()) / "capability.py"
        py.write_text("# placeholder\n")
        self.assertIsNone(load_capability_yaml(py))

    def test_no_contract_means_no_silent_fallback(self):
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        (tmp / "capability.yml").write_text(
            "name: test.no_contract\nparams:\n  X:\n    default: 1\n"
        )

        class NoContractCap(VOXCapability):
            CAPABILITY_NAME = "test.no_contract"

        with self.assertRaises(RuntimeError):
            NoContractCap._ensure_contract()


class TestCapabilityIdentity(unittest.TestCase):
    """CAPABILITY_NAME must agree with the YAML contract name."""

    def test_class_name_matches_contract_name(self):
        for yml in _capability_ymls():
            cap_id = ".".join(
                yml.relative_to(CAPABILITIES).parts[:-1]
            )
            contract = _contract_for(yml)
            self.assertIsNotNone(contract, yml)
            self.assertEqual(contract.name, cap_id, str(yml))


class TestBoundaryNoPrivates(unittest.TestCase):
    """capability/binding source never reaches into workload privates."""

    def test_no_workload_private_access_in_boundary(self):
        for py in BOUNDARY_FILES:
            if not py.exists():
                continue
            hits = FORBIDDEN_ACCESS_PATTERNS.findall(py.read_text())
            self.assertEqual(hits, [], str(py))

    def test_no_workload_private_access_in_binder(self):
        binder = ROOT / "src" / "vox" / "workloads" / "capability_binder.py"
        source = binder.read_text()
        hits = FORBIDDEN_ACCESS_PATTERNS.findall(source)
        self.assertEqual(hits, [], str(binder))
        # The role registry is never reached directly; enabling/disabling roles
        # traverses the declared disable_roles host operation instead.
        self.assertNotIn("host.roles", source)
        self.assertNotIn("_host.roles", source)

    def test_binder_disables_roles_only_via_declared_operation(self):
        """Role disabling goes through disable_roles, never host.roles."""
        binder = ROOT / "src" / "vox" / "workloads" / "capability_binder.py"
        source = binder.read_text()
        self.assertIn("disable_roles", source)
        self.assertNotIn("host.roles", source)
        self.assertNotIn("_host.roles", source)


class TestBinderDependsOnHostProtocol(unittest.TestCase):
    """binder must run against any CapabilityHostProtocol, not just VOXWorkload."""

    def test_mount_flow_uses_only_protocol(self):
        import tempfile
        from unittest.mock import MagicMock

        from vox.workloads.capability_binder import CapabilityBinder

        roles_dir = Path(tempfile.mkdtemp())
        cap_dir = Path(tempfile.mkdtemp())
        (cap_dir / "capability.py").write_text("# placeholder\n")
        (cap_dir / "capability.yml").write_text(
            "name: fake.cap\nparams:\n  X:\n    description: x\n    type: string\n    default: 1\n"
        )

        class FakeCap(VOXCapability):
            CAPABILITY_NAME = "fake.cap"

        FakeCap.load_contract(cap_dir / "capability.py")
        fake_cap = FakeCap()
        fake_cap.id = "fake.cap"
        fake_cap.logger = MagicMock()

        host = MagicMock()
        host.name = "fake"
        host.id = "wk-1"
        host.dir = "/tmp/fake-host"
        host.config = {}
        host.roles = {}
        host.capabilities = {}
        host.capability_provider.get_capability_instance.return_value = fake_cap
        host.vault = None

        binder = CapabilityBinder(host, host.logger)
        binder.discover_and_mount(roles_dir, system_capabilities={"fake.cap"})

        host.capability_provider.get_capability_instance.assert_called_once_with(
            "fake.cap"
        )
        host.register_capability.assert_called()
        self.assertEqual(host.register_capability.call_args[0][0], "fake.cap")

        bound = host.register_capability.call_args[0][1]
        self.assertIsInstance(bound, VOXBoundCapability)
        self.assertIs(bound.get_capability("x"), host.get_capability.return_value)

    def test_role_disabling_traverses_declared_operation(self):
        """Role disabling uses only the declared disable_roles host operation.

        The host is a strict protocol-conforming object with no public ``roles``
        attribute, so any direct roles access by the binder would fail loudly.
        """
        from unittest.mock import MagicMock

        from vox.workloads.capability_binder import CapabilityBinder

        calls = []

        class MinimalHost:
            def __init__(self):
                self._roles = {}

            def add_role(self, name, role):
                self._roles[name] = role

            def disable_roles(self, reason_for):
                calls.append(reason_for)
                removed = {}
                for name, role in list(self._roles.items()):
                    reason = reason_for(role)
                    if reason is not None:
                        self._roles.pop(name)
                        removed[name] = reason
                return removed

        class RoleA:
            REQUIRES: ClassVar[set[str]] = {"x"}

        class RoleB:
            REQUIRES: ClassVar[set[str]] = {"y"}

        host = MinimalHost()
        host.add_role("a", RoleA())
        host.add_role("b", RoleB())

        binder = CapabilityBinder(host, MagicMock())
        binder._disable_roles_with_missing_capabilities({"x"})

        self.assertEqual(len(calls), 1)
        # Only the affected role is disabled, and only via the host operation.
        self.assertEqual(set(host._roles), {"b"})
        self.assertEqual(
            binder.logger.warning.call_args[0][0],
            "Role 'a' disabled — missing capabilities: ['x']",
        )

        binder._disable_roles_for_capability("nope")
        self.assertEqual(set(host._roles), {"b"})
        # A second call with no matching capability leaves roles untouched.
        binder._disable_roles_for_capability("y")
        self.assertEqual(host._roles, {})


class TestInboundDispatchBoundary(unittest.TestCase):
    """The gateway routes inbound traffic through the host's narrow
    ``dispatch_inbound`` service, never the whole provider abstraction."""

    def test_gateway_uses_host_dispatch_not_provider(self):
        gateway_py = (CAPABILITIES / "comm" / "gateway" / "capability.py").read_text()
        self.assertIn("_host.dispatch_inbound", gateway_py)
        self.assertNotIn(".capability_provider", gateway_py)
        self.assertNotIn("dispatch_inbound_message", gateway_py)
        self.assertNotIn("SecurityError", gateway_py)

    def test_host_protocol_declares_narrow_dispatch_service(self):
        from vox.provider import CapabilityHostProtocol

        self.assertTrue(hasattr(CapabilityHostProtocol, "dispatch_inbound"))

    def test_no_capability_reaches_for_whole_provider(self):
        for path in BOUNDARY_FILES:
            source = path.read_text()
            self.assertNotIn(
                "dispatch_inbound_message", source,
                f"{path}: must use host.dispatch_inbound, not the provider",
            )
            self.assertNotIn(
                ".capability_provider", source,
                f"{path}: must not depend on the whole provider abstraction",
            )
