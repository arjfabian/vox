"""Focused coverage for role-scoped capability attribution.

Four-behaviour guard over the investigated design:

* capability requirements are attributed to successfully loaded Roles via
  their AST ``self.workload.capabilities`` references (never ``REQUIRES``);
* a Role that fails to load contributes no requirements;
* a missing capability disables only the loading Roles that reference it, and
  unrelated Roles stay operational;
* ``comm.gateway`` is no longer implicitly mounted on every workload: it is
  mounted only when a loaded Role references it; ``_system_capabilities`` is
  empty and shared capabilities are mounted exactly once.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vox.config import VOXConfig
from vox.orchestration import VOXOrchestrator
from vox.workloads.base import VOXWorkload
from vox.workloads.lifecycle import WorkloadState

MISSING = "missing.cap"


def _mock_cap(cap_id: str):
    """Build (capability, bound-capability) mocks for a mountable capability."""
    mock_cap = MagicMock()
    mock_bound = MagicMock()
    mock_bound.initialize = AsyncMock()
    mock_bound.boot = AsyncMock()
    mock_bound.validate_params = MagicMock(return_value=[])
    mock_cap.mount.return_value = mock_bound
    mock_cap.CAPABILITY_NAME = cap_id
    mock_cap.get_secret_names.return_value = []
    mock_bound._capability = mock_cap
    mock_bound.get_exposed_commands.return_value = []
    return mock_cap, mock_bound


def _write_role(roles_dir: Path, name: str, caps) -> None:
    """Write a loadable VOXRole module referencing capabilities by subscript."""
    lines = ["from vox.roles import VOXRole", ""]
    lines.append("class Role(VOXRole):")
    lines.append("    def handle(self, text: str = '') -> str:")
    for cap_id in sorted(caps):
        lines.append(f"        cap = self.workload.capabilities[{cap_id!r}]")
    lines.append('        return "handled"')
    (roles_dir / f"{name}.py").write_text("\n".join(lines) + "\n")


def _write_routing_role(roles_dir: Path, name: str, events=(), commands=()) -> None:
    """Write a loadable VOXRole registering event and command routes.

    Handlers append to ``role.received`` so tests can observe dispatch.
    """
    lines = ["from vox.roles import VOXRole, command", ""]
    lines.append("class Role(VOXRole):")
    for cmd_name in commands:
        lines.append(f"    @command({cmd_name!r}, description='cmd')")
        lines.append(f"    async def cmd_{cmd_name}(self, **kwargs):")
        lines.append("        self.received.append(('cmd', kwargs))")
    lines.append("    def __init__(self, workload):")
    lines.append("        super().__init__(workload)")
    lines.append("        self.received = []")
    for evt in events:
        lines.append(f"        @self.on({evt!r})")
        lines.append("        def _handler(**kwargs):")
        lines.append("            self.received.append(('event', kwargs))")
    (roles_dir / f"{name}.py").write_text("\n".join(lines) + "\n")


def _write_secret_role(
    roles_dir: Path,
    name: str,
    caps: set[str],
    required_secrets: dict[str, list[str]],
) -> None:
    """Write a loadable VOXRole with module-level REQUIRED_SECRETS."""
    lines = ["from vox.roles import VOXRole", ""]
    for cap_id, names in required_secrets.items():
        lines.append(f"REQUIRED_SECRETS = {{{cap_id!r}: {sorted(names)!r}}}")
    lines.append("")
    lines.append("class Role(VOXRole):")
    lines.append("    def handle(self):")
    for cap_id in sorted(caps):
        lines.append(f"        cap = self.workload.capabilities[{cap_id!r}]")
    lines.append('        return "handled"')
    (roles_dir / f"{name}.py").write_text("\n".join(lines) + "\n")


class _WorkloadFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_role_scoped_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        self.logger = MagicMock()
        self._orchestrator: MagicMock | None = None

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_workload(
        self,
        roles: dict[str, set[str]],
        provider=None,
    ) -> VOXWorkload:
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-rolescop\nautostart: false\n"
        )
        for name, caps in roles.items():
            _write_role(self.tmp / "roles", name, caps)
        orchestrator = MagicMock()
        orchestrator.get_capability_instance.side_effect = provider or (
            lambda cap_id: None
        )
        self._orchestrator = orchestrator
        return VOXWorkload(self.tmp, self.logger, orchestrator)

    def _requested_caps(self) -> list:
        return [
            call.args[0]
            for call in self._orchestrator.get_capability_instance.call_args_list
        ]


class TestFailedRoleContributesNothing(_WorkloadFixture):
    def test_failed_role_contributes_no_requirements(self):
        roles = {"good": {"ok.cap"}}
        (self.tmp / "roles" / "broken.py").write_text(
            "def (  # syntax error — module never loads\n"
            "self.workload.capabilities['gone.cap']\n"
        )

        def _provider(cap_id):
            if cap_id == "ok.cap":
                return _mock_cap(cap_id)[0]
            return None

        wl = self._make_workload(roles, _provider)

        # Only the successfully loaded role was scanned; the broken file never
        # contributed "gone.cap".
        self.assertIn("good", wl.roles)
        self.assertNotIn("broken", wl.roles)
        self.assertNotIn("gone.cap", self._requested_caps())
        self.assertIn("ok.cap", wl.capabilities)
        self.assertFalse(wl.degraded)


class TestMissingCapabilityDisablesOnlyReferencingRoles(_WorkloadFixture):
    def _make(self):
        roles = {
            "role_a": {"present.cap", MISSING},
            "role_b": {"present.cap"},
        }

        def _provider(cap_id):
            if cap_id == "present.cap":
                return _mock_cap(cap_id)[0]
            return None

        return self._make_workload(roles, _provider)

    def test_only_referencing_role_is_disabled(self):
        wl = self._make()
        self.assertNotIn(MISSING, wl.capabilities)
        self.assertIn("present.cap", wl.capabilities)
        self.assertNotIn("role_a", wl.roles)
        self.assertIn("role_b", wl.roles)
        self.assertTrue(wl.degraded)
        self.assertTrue(wl.health_check())

    def test_unrelated_role_remains_operational(self):
        wl = self._make()
        role_b = wl.roles["role_b"]
        self.assertIsNotNone(role_b)
        self.assertIn("role_b", wl.roles)
        # role_b can still receive emitted events through the router.
        self.assertIn("role_b", wl.roles)

    def test_ast_references_drive_disabling_without_requires(self):
        wl = self._make()
        role_source = (self.tmp / "roles" / "role_a.py").read_text()
        self.assertNotIn("REQUIRES", role_source)
        self.assertNotIn("role_a", wl.roles)


class TestDegradedWorkloadBootSemantics(_WorkloadFixture):
    def test_degraded_workload_with_operational_role_still_boots(self):
        roles = {
            "role_a": {MISSING},
            "role_b": set(),
        }
        wl = self._make_workload(roles, lambda cap_id: None)

        self.assertTrue(wl.degraded)
        self.assertNotIn("role_a", wl.roles)
        self.assertIn("role_b", wl.roles)
        self.assertTrue(wl.health_check())

        result = asyncio_run(wl.boot())
        self.assertTrue(result)
        self.assertEqual(wl.state, WorkloadState.ACTIVE)

    def test_zero_operational_roles_is_not_healthy(self):
        roles = {"role_a": {MISSING}}
        wl = self._make_workload(roles, lambda cap_id: None)

        self.assertTrue(wl.degraded)
        self.assertNotIn("role_a", wl.roles)
        self.assertFalse(wl.health_check())


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


class TestSharedAndEmptySystemCapabilities(_WorkloadFixture):
    def test_shared_capability_is_mounted_once(self):
        roles = {
            "role_a": {"shared.cap"},
            "role_b": {"shared.cap"},
        }
        wl = self._make_workload(
            roles,
            lambda cap_id: _mock_cap(cap_id)[0],
        )

        self.assertEqual(list(wl.capabilities), ["shared.cap"])
        self.assertEqual(self._requested_caps().count("shared.cap"), 1)

    def test_empty_system_capabilities_is_valid_and_comm_gateway_not_mounted(self):
        wl = self._make_workload({"main": set()}, lambda cap_id: None)

        self.assertEqual(wl.capabilities, {})
        self.assertNotIn("comm.gateway", wl.capabilities)
        self.assertEqual(self._requested_caps(), [])

    def test_comm_gateway_mounts_only_when_a_role_references_it(self):
        roles = {"chat": {"comm.gateway"}}
        wl = self._make_workload(
            roles,
            lambda cap_id: _mock_cap(cap_id)[0],
        )

        self.assertIn("comm.gateway", wl.capabilities)
        self.assertEqual(self._requested_caps().count("comm.gateway"), 1)
        self.assertIn("chat", wl.roles)


class TestDisabledRolesUnreachableByRouting(_WorkloadFixture):
    """A disabled Role must be removed from workload routing/dispatch."""

    def _build(self, role_files: dict[str, tuple[tuple, tuple]]) -> VOXWorkload:
        (self.tmp / "manifest.yml").write_text(
            "name: RouteWorkload\nid: test-uuid-route\nautostart: false\n"
        )
        for name, (events, commands) in role_files.items():
            _write_routing_role(
                self.tmp / "roles", name, events=events, commands=commands
            )
        orchestrator = MagicMock()
        orchestrator.get_capability_instance.side_effect = lambda cap_id: None
        self._orchestrator = orchestrator
        return VOXWorkload(self.tmp, self.logger, orchestrator)

    def _disable(self, wl: VOXWorkload, cap_id: str, role_name: str) -> None:
        wl._capability_binder._role_capabilities = {role_name: {cap_id}}
        wl._capability_binder._disable_roles_for_capability(cap_id)

    def test_disabled_role_no_longer_receives_events(self):
        wl = self._build(
            {
                "role_a": (("inbound_message",), ()),
                "role_b": (("inbound_message",), ()),
            }
        )
        disabled_role = wl.roles["role_a"]
        self._disable(wl, "test_cap", "role_a")

        self.assertNotIn("role_a", wl.roles)
        self.assertEqual(wl.event_router["inbound_message"], [wl.roles["role_b"]])

        asyncio_run(wl.boot())
        asyncio_run(wl.emit("inbound_message", text="hi"))

        self.assertEqual(disabled_role.received, [])
        self.assertEqual(wl.roles["role_b"].received, [("event", {"text": "hi"})])

    def test_disabled_role_kept_out_of_event_router_and_metadata(self):
        wl = self._build(
            {
                "solo": (
                    ("private_event",),
                    ("solo_cmd",),
                ),
            }
        )
        disabled_role = wl.roles["solo"]

        self.assertIn("private_event", wl.events)
        self.assertIn("solo_cmd", wl.commands)

        self._disable(wl, "test_cap", "solo")

        self.assertNotIn("solo", wl.roles)
        self.assertNotIn("private_event", wl.event_router)
        self.assertNotIn("private_event", wl.events)
        self.assertNotIn("solo_cmd", wl.commands)
        self.assertNotIn("solo_cmd", wl.get_command_map())

        asyncio_run(wl.boot())
        asyncio_run(wl.emit("private_event", text="hi"))
        self.assertEqual(disabled_role.received, [])

    def test_shared_routes_are_retained_for_remaining_roles(self):
        wl = self._build(
            {
                "role_a": (("shared_event", "a_only_event"), ("shared_cmd", "a_cmd")),
                "role_b": (("shared_event",), ("shared_cmd",)),
            }
        )
        self._disable(wl, "test_cap", "role_a")

        self.assertEqual(wl.event_router["shared_event"], [wl.roles["role_b"]])
        self.assertEqual(wl.event_router["shared_cmd"], [wl.roles["role_b"]])
        self.assertNotIn("a_only_event", wl.event_router)
        self.assertNotIn("a_cmd", wl.event_router)

        self.assertIn("shared_event", wl.events)
        self.assertNotIn("a_only_event", wl.events)
        self.assertIn("shared_cmd", wl.commands)
        self.assertNotIn("a_cmd", wl.commands)

        command_map = wl.get_command_map()
        self.assertIn("shared_cmd", command_map)
        self.assertNotIn("a_cmd", command_map)


from vox.capabilities.base import (
    CapabilityContract,
    SecretMeta,
    VOXCapability,
)


class _SecretCapForTest(VOXCapability):
    CAPABILITY_NAME = "secret_cap"


_SecretCapForTest._contract = CapabilityContract(
    name="secret_cap",
    params={},
    secrets={
        "API_KEY": SecretMeta(
            name="API_KEY",
            description="Required API key",
            type="string",
            required=True,
        ),
    },
)


class TestLastRoleDisabledDuringBoot(_WorkloadFixture):
    """Zero operational Roles must never become ACTIVE — even when the last
    Role is disabled during boot while resolving required Vault secrets."""

    def _build(
        self,
        role_files: dict[str, set[str]],
        required_secrets: dict[str, list[str]] | None = None,
    ) -> VOXWorkload:
        (self.tmp / "manifest.yml").write_text(
            "name: SecretWorkload\nid: test-uuid-secret\nautostart: false\n"
        )
        for name, caps in role_files.items():
            if caps:
                _write_secret_role(
                    self.tmp / "roles",
                    name,
                    caps,
                    required_secrets or {},
                )
            else:
                _write_role(self.tmp / "roles", name, caps)
        cap = _SecretCapForTest()
        cap.id = "secret_cap"
        cap.logger = self.logger
        orchestrator = MagicMock()
        orchestrator.get_capability_instance.side_effect = lambda cap_id: cap
        self._orchestrator = orchestrator
        self._saved_key = os.environ.get("VOX_MASTER_KEY")
        os.environ["VOX_MASTER_KEY"] = "test-master-key-for-boot-guard"
        return VOXWorkload(self.tmp, self.logger, orchestrator)

    def tearDown(self):
        if getattr(self, "_saved_key", None):
            os.environ["VOX_MASTER_KEY"] = self._saved_key
        else:
            os.environ.pop("VOX_MASTER_KEY", None)
        super().tearDown()

    def test_last_role_disabled_during_boot_never_becomes_active(self):
        wl = self._build(
            {"only_role": {"secret_cap"}},
            {"secret_cap": ["API_KEY"]},
        )

        self.assertIn("only_role", wl.roles)
        self.assertTrue(wl.health_check())

        result = asyncio_run(wl.boot())

        self.assertFalse(result)
        self.assertNotEqual(wl.state, WorkloadState.ACTIVE)
        self.assertEqual(wl.state, WorkloadState.FAILED)
        self.assertNotIn("only_role", wl.roles)
        self.assertFalse(wl.health_check())
        self.assertTrue(wl.degraded)

    def test_operational_role_allows_boot_despite_missing_secret(self):
        wl = self._build(
            {
                "role_a": {"secret_cap"},
                "role_b": set(),
            },
            {"secret_cap": ["API_KEY"]},
        )

        result = asyncio_run(wl.boot())

        self.assertTrue(result)
        self.assertEqual(wl.state, WorkloadState.ACTIVE)
        self.assertIn("role_b", wl.roles)
        self.assertNotIn("role_a", wl.roles)
        self.assertTrue(wl.degraded)
        self.assertTrue(wl.health_check())


class TestFleetOnboardingRoleScoped(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_with_roles_boots_but_zero_roles_stay_parked(self):
        import shutil

        tmp = Path("/tmp") / f"test_role_scoped_fleet_{id(self)}"
        personas = tmp / "personas"
        caps = tmp / "caps"
        identity = tmp / "identity"
        for d in (personas, caps, identity):
            d.mkdir(parents=True, exist_ok=True)

        def _persona(name: str, wid: str, role_files: dict[str, set[str]]) -> None:
            base = personas / name
            (base / "roles").mkdir(parents=True, exist_ok=True)
            (base / "manifest.yml").write_text(
                f"name: {name}\nid: {wid}\nautostart: true\n"
            )
            for role_name, role_caps in role_files.items():
                _write_role(base / "roles", role_name, role_caps)

        _persona("stable", "wid-stable", {"main": set()})
        _persona("degraded-ok", "wid-degok", {"role_a": {MISSING}, "role_b": set()})
        _persona("dead", "wid-dead", {"role_a": {MISSING}})

        config = VOXConfig(
            verbose_logging=False,
            log_path="logs/vox.log",
            uds_path="/tmp/vox.sock",
            war_room_id="",
        )
        orc = VOXOrchestrator(
            config,
            logger=MagicMock(),
            personas_dir=personas,
            identity_dir=identity,
            capabilities_dir=caps,
        )

        try:
            with (
                patch.dict(os.environ, {"VOX_WATCH_DISABLED": "true"}, clear=False),
                patch(
                    "vox.workloads.capability_binder."
                    "CapabilityBinder.inject_vault_secrets",
                    new_callable=AsyncMock,
                ),
            ):
                ok = await orc.boot()
            self.assertTrue(ok)

            # healthy workload boots
            self.assertIn("wid-stable", orc.active_workloads)

            # degraded but with an operational role still boots and is ACTIVE
            self.assertIn("wid-degok", orc.active_workloads)
            self.assertTrue(orc.active_workloads["wid-degok"].degraded)

            # zero operational roles stays parked in degraded_workloads
            self.assertNotIn("wid-dead", orc.active_workloads)
            self.assertIn("wid-dead", orc.degraded_workloads)

            # hot-restart path uses the same corrected gate: a degraded workload
            # with an operational role is rehired and boots back to ACTIVE.
            restarted = await orc.restart_workload("degraded-ok")
            self.assertTrue(restarted)
            self.assertIn("wid-degok", orc.active_workloads)
            self.assertNotIn("wid-degok", orc.degraded_workloads)
        finally:
            await orc.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)


class TestDispatchFailureObservability(_WorkloadFixture):
    """A raising Role must surface its root exception, never a swallowed one-liner."""

    def test_failing_role_logs_root_exception_with_traceback(self):
        logger = MagicMock()

        (self.tmp / "manifest.yml").write_text(
            "name: FailWorkload\nid: test-uuid-fail\nautostart: false\n"
        )
        (self.tmp / "roles" / "boom.py").write_text(
            "from vox.roles import VOXRole\n\n"
            "class Role(VOXRole):\n"
            "    def __init__(self, workload):\n"
            "        super().__init__(workload)\n"
            "        @self.on('parse_receipt')\n"
            "        def _handler(**kwargs):\n"
            "            raise RuntimeError('simulated vision timeout')\n"
        )
        orchestrator = MagicMock()
        orchestrator.get_capability_instance.side_effect = lambda cap_id: None
        wl = VOXWorkload(self.tmp, logger, orchestrator)
        asyncio_run(wl.boot())
        asyncio_run(wl.emit("parse_receipt"))

        # boot() swaps the injected logger for a per-workload child; emit
        # failures are recorded on the workload logger with the full traceback
        # attached (.exception semantics), not reduced to a bare one-liner.
        wl.logger.exception.assert_called_once()
        args = wl.logger.exception.call_args.args
        self.assertEqual(args[0], "Role dispatch failure [%s]")
        self.assertEqual(args[1], "parse_receipt")


if __name__ == "__main__":
    unittest.main()