import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vox.capabilities.base import VOXCapability
from vox.workloads.base import VOXWorkload, WorkloadProvisionError
from vox.workloads.lifecycle import WorkloadState


class TestVOXWorkloadInit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_workload_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.orchestrator = MagicMock()

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_init_sets_basic_attributes(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(workload.dir, self.tmp)
        self.assertEqual(workload.state, WorkloadState.IDLE)
        self.assertIsNotNone(workload.memory)
        self.assertIsNotNone(workload.store)

    def test_name_from_config(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(workload.name, "TestWorkload")

    def test_id_from_config(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertEqual(workload.id, "test-uuid-1234")

    def test_master_id_none_by_default(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertIsNone(workload.master_id)

    def test_must_start_defaults_false(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(workload.must_start)

    def test_must_start_from_config(self):
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-1234\nautostart: true\n"
        )
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertTrue(workload.must_start)

    def test_conversational_defaults_false(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(workload.conversational)

    def test_orchestrator_property(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertIs(workload.orchestrator, self.orchestrator)

    def test_orchestrator_none_allowed(self):
        workload = VOXWorkload(self.tmp, self.logger, None)
        self.assertIsNone(workload.orchestrator)

    def test_health_check_empty_roles(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertFalse(workload.health_check())

    def test_describe_includes_basic_fields(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        desc = workload.describe()
        self.assertEqual(desc["name"], "TestWorkload")
        self.assertEqual(desc["state"], "IDLE")
        self.assertIn("capabilities", desc)
        self.assertIn("commands", desc)
        self.assertIn("subordinates", desc)


class TestVOXWorkloadManifest(unittest.TestCase):
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
        with self.assertRaises(WorkloadProvisionError):
            VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_invalid_yaml_raises(self):
        (self.tmp / "manifest.yml").write_text(":: invalid yaml ::")
        with self.assertRaises(WorkloadProvisionError):
            VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_missing_name_in_manifest_raises(self):
        (self.tmp / "manifest.yml").write_text("id: test-uuid\n")
        with self.assertRaises(WorkloadProvisionError):
            VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_missing_id_in_manifest_raises(self):
        (self.tmp / "manifest.yml").write_text("name: Test\n")
        with self.assertRaises(WorkloadProvisionError):
            VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_wrong_type_for_name_raises(self):
        (self.tmp / "manifest.yml").write_text("name: 42\nid: test-uuid\n")
        with self.assertRaises(WorkloadProvisionError):
            VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_log_source_returns_valid_source(self):
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid\n")
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        src = workload.log_source
        self.assertEqual(src.source_type, "workload")
        self.assertEqual(src.source_name, "testworkload")


class _MessengerMockMixin:
    """Provide a working comm.gateway mock for workloads that need it during boot."""

    @staticmethod
    def _make_messenger_mocks():
        mock_cap = MagicMock()
        mock_bound = MagicMock()
        mock_bound.initialize = AsyncMock()
        mock_bound.boot = AsyncMock()
        mock_bound.validate_params = MagicMock(return_value=[])
        mock_cap.mount.return_value = mock_bound
        mock_cap.CAPABILITY_NAME = "comm.gateway"
        mock_cap.get_secret_names.return_value = []
        mock_bound._capability = mock_cap
        mock_bound.get_exposed_commands.return_value = []
        return mock_cap, mock_bound


class TestVOXWorkloadStateTransitions(_MessengerMockMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_state_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        mock_cap, _ = self._make_messenger_mocks()
        self.orchestrator.get_capability_instance.return_value = mock_cap
        self.workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_initial_state_is_idle(self):
        self.assertEqual(self.workload.state, WorkloadState.IDLE)

    def test_valid_transition(self):
        self.workload.state = WorkloadState.STOPPING
        self.assertEqual(self.workload.state, WorkloadState.STOPPING)

    def test_invalid_transition_raises(self):
        with self.assertRaises(RuntimeError):
            self.workload.state = WorkloadState.ACTIVE

    def test_boot_sets_active(self):
        result = asyncio.run(self.workload.boot())
        self.assertTrue(result)
        self.assertEqual(self.workload.state, WorkloadState.ACTIVE)

    def test_stop_sets_stopped(self):
        asyncio.run(self.workload.boot())
        asyncio.run(self.workload.stop())
        self.assertEqual(self.workload.state, WorkloadState.STOPPED)

    def test_pause_and_resume(self):
        asyncio.run(self.workload.boot())
        asyncio.run(self.workload.pause())
        self.assertEqual(self.workload.state, WorkloadState.PAUSED)
        asyncio.run(self.workload.resume())
        self.assertEqual(self.workload.state, WorkloadState.ACTIVE)


class TestVOXWorkloadSafePath(_MessengerMockMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_path_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        orchestrator = MagicMock()
        mock_cap, _ = self._make_messenger_mocks()
        orchestrator.get_capability_instance.return_value = mock_cap
        self.workload = VOXWorkload(self.tmp, self.logger, orchestrator)

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_get_safe_path_within_sandbox(self):
        path = self.workload.get_safe_path("evidence", "test.png")
        self.assertTrue(str(path).endswith("test.png"))
        self.assertIn("assets", str(path))

    def test_get_safe_path_prevents_escape(self):
        with self.assertRaises(PermissionError):
            self.workload.get_safe_path("../../etc", "passwd")


class TestVOXWorkloadDispatchInbound(unittest.TestCase):
    """The narrow capability-facing inbound dispatch entry point."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_dispatch_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: Test\nid: test-uuid-1234\n")
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.orchestrator.dispatch_inbound_message = AsyncMock()
        self.workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_dispatch_inbound_delegates_to_shared_dispatcher(self):
        self.orchestrator.dispatch_inbound_message.return_value = True
        result = asyncio.run(
            self.workload.dispatch_inbound("comm.gateway", {"text": "hi"})
        )
        self.assertTrue(result)
        self.orchestrator.dispatch_inbound_message.assert_awaited_once_with(
            "comm.gateway", {"text": "hi"}
        )

    def test_dispatch_inbound_false_when_no_dispatch_target(self):
        """Host returns False when the provider reports no target received it."""
        self.orchestrator.dispatch_inbound_message.return_value = False
        self.workload.logger.reset_mock()
        result = asyncio.run(
            self.workload.dispatch_inbound("comm.gateway", {"text": "hi"})
        )
        self.assertFalse(result)
        self.workload.logger.warning.assert_not_called()

    def test_dispatch_inbound_false_when_no_provider(self):
        workload = VOXWorkload(self.tmp, self.logger, None)
        workload.logger.reset_mock()
        result = asyncio.run(
            workload.dispatch_inbound("comm.gateway", {"text": "hi"})
        )
        self.assertFalse(result)
        workload.logger.warning.assert_not_called()

    def test_dispatch_inbound_absorbs_guardrail_security_error(self):
        from vox.security import SecurityError

        self.orchestrator.dispatch_inbound_message.side_effect = SecurityError(
            "blocked"
        )
        self.workload.logger.reset_mock()
        result = asyncio.run(
            self.workload.dispatch_inbound("comm.gateway", {"text": "bad"})
        )
        self.assertFalse(result)
        self.workload.logger.warning.assert_called()


class TestVOXWorkloadBootCapabilities(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_boot_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid-1234\n")
        (self.tmp / "roles" / "chat.py").write_text('REQUIRES = {"test_cap"}\n')
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.mock_cap = MagicMock()
        self.mock_bound = MagicMock()
        self.mock_bound.initialize = AsyncMock()
        self.mock_bound.boot = AsyncMock()
        self.mock_cap.mount.return_value = self.mock_bound
        self.mock_cap.CAPABILITY_NAME = ""
        self.mock_cap.get_secret_names.return_value = []
        self.mock_bound._capability = self.mock_cap
        self.mock_bound.get_exposed_commands.return_value = []
        self.orchestrator.get_capability_instance.return_value = self.mock_cap

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_boot_calls_capability_initialize_and_boot(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(workload.boot())
        self.assertTrue(result)
        # initialize/boot is called once per mounted capability (test_cap + comm.gateway)
        self.assertEqual(self.mock_bound.initialize.await_count, 2)
        self.assertEqual(self.mock_bound.boot.await_count, 2)

    def test_boot_failure_when_capability_fails(self):
        self.mock_bound.initialize.side_effect = Exception("fail")
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(workload.boot())
        self.assertFalse(result)
        self.assertEqual(workload.state, WorkloadState.FAILED)


class TestVOXWorkloadDegradedParams(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_degraded_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-5678\nautostart: false\n"
        )
        (self.tmp / "roles" / "chat.py").write_text('REQUIRES = {"strict_cap"}\n')
        self.logger = MagicMock()
        self.orchestrator = MagicMock()

        class _StrictCap(VOXCapability):
            CAPABILITY_NAME = "strict_cap"

        _StrictCap._contract = CapabilityContract(
            name="strict_cap",
            params={"API_KEY": ParamMeta(name="API_KEY", description="Required API key", type="string", default=None)},
            secrets={},
        )

        self.cap = _StrictCap()
        self.cap.id = "strict_cap"
        self.cap.logger = self.logger
        self.orchestrator.get_capability_instance.return_value = self.cap

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_missing_required_param_routes_to_degraded(self):
        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertIn("strict_cap", workload.capabilities)
        bound = workload.capabilities["strict_cap"]
        self.assertEqual(bound.validate_params(), ["API_KEY"])
        self.assertFalse(workload.health_check())

    def test_missing_params_plus_missing_cap_does_not_crash(self):
        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"missing_cap", "strict_cap"}\n'
        )

        def _get_cap(cap_id):
            if cap_id == "missing_cap":
                return None
            return self.cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap

        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        self.assertTrue(workload._degraded)
        self.assertFalse(workload.health_check())
        self.assertNotIn("missing_cap", workload.capabilities)
        self.assertIn("strict_cap", workload.capabilities)


from vox.capabilities.base import CapabilityContract, ParamMeta, SecretMeta


class _SensitiveCapForTest(VOXCapability):
    CAPABILITY_NAME = "sensitive_cap"


_SensitiveCapForTest._contract = CapabilityContract(
    name="sensitive_cap",
    params={},
    secrets={"API_KEY": SecretMeta(name="API_KEY", description="API key", type="string", required=True)},
)


class _PlainCapForTest(VOXCapability):
    CAPABILITY_NAME = "plain_cap"


_PlainCapForTest._contract = CapabilityContract(
    name="plain_cap",
    params={},
    secrets={},
)


class TestVOXWorkloadVaultFailFast(_MessengerMockMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vox_vault_fail_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text("name: TestWorkload\nid: test-uuid-1234\n")
        self._saved_key = os.environ.pop("VOX_MASTER_KEY", None)
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.mock_messenger_cap, _ = self._make_messenger_mocks()

    def tearDown(self):
        if self._saved_key is not None:
            os.environ["VOX_MASTER_KEY"] = self._saved_key
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_boot_fails_when_vault_missing_with_sensitive_params(self):
        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"sensitive_cap"}\n'
            'REQUIRED_SECRETS = {"sensitive_cap": ["API_KEY"]}\n'
        )

        sensitive_cap = _SensitiveCapForTest()
        sensitive_cap.id = "sensitive_cap"
        sensitive_cap.logger = self.logger

        def _get_cap(cap_id):
            if cap_id == "comm.gateway":
                return self.mock_messenger_cap
            return sensitive_cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap

        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(workload.boot())
        self.assertFalse(result)
        self.assertEqual(workload.state, WorkloadState.FAILED)

    def test_boot_succeeds_without_vault_when_no_sensitive_params(self):
        (self.tmp / "roles" / "chat.py").write_text('REQUIRES = {"plain_cap"}\n')

        plain_cap = _PlainCapForTest()
        plain_cap.id = "plain_cap"
        plain_cap.logger = self.logger

        def _get_cap(cap_id):
            if cap_id == "comm.gateway":
                return self.mock_messenger_cap
            return plain_cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap

        workload = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(workload.boot())
        self.assertTrue(result)
        self.assertEqual(workload.state, WorkloadState.ACTIVE)


class TestCollectRequiredSecrets(_MessengerMockMixin, unittest.TestCase):
    """_collect_required_secrets() intersects AST REQUIRED_SECRETS with YAML contract."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_collect_secrets_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-collect\nautostart: false\n"
        )
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.mock_messenger_cap, _ = self._make_messenger_mocks()
        self.orchestrator.get_capability_instance.return_value = self.mock_messenger_cap

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_workload(self, role_content: str, cap) -> tuple:
        (self.tmp / "roles" / "chat.py").write_text(role_content)

        def _get_cap(cap_id):
            if cap_id == "comm.gateway":
                return self.mock_messenger_cap
            return cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap
        return VOXWorkload(self.tmp, self.logger, self.orchestrator)

    def test_full_intersection(self):
        """Role declares API_KEY, YAML marks it required → required."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _Cap(VOXCapability):
            CAPABILITY_NAME = "test_cap"

        _Cap._contract = CapabilityContract(
            name="test_cap",
            params={},
            secrets={
                "API_KEY": SecretMeta(name="API_KEY", description="", type="string", required=True),
            },
        )
        cap = _Cap()
        cap.id = "test_cap"
        cap.logger = self.logger

        wl = self._make_workload(
            'REQUIRES = {"test_cap"}\n'
            'REQUIRED_SECRETS = {"test_cap": ["API_KEY"]}\n',
            cap,
        )
        result = wl._capability_binder._collect_required_secrets()
        self.assertEqual(result, {"test_cap": {"API_KEY"}})

    def test_partial_intersection(self):
        """Role declares K1+K2, YAML only marks K1 required → {K1}."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _Cap(VOXCapability):
            CAPABILITY_NAME = "test_cap"

        _Cap._contract = CapabilityContract(
            name="test_cap",
            params={},
            secrets={
                "K1": SecretMeta(name="K1", description="", type="string", required=True),
                "K2": SecretMeta(name="K2", description="", type="string", required=False),
            },
        )
        cap = _Cap()
        cap.id = "test_cap"
        cap.logger = self.logger

        wl = self._make_workload(
            'REQUIRES = {"test_cap"}\n'
            'REQUIRED_SECRETS = {"test_cap": ["K1", "K2"]}\n',
            cap,
        )
        result = wl._capability_binder._collect_required_secrets()
        self.assertEqual(result, {"test_cap": {"K1"}})

    def test_no_intersection(self):
        """Role declares K2, YAML requires only K1 → empty."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _Cap(VOXCapability):
            CAPABILITY_NAME = "test_cap"

        _Cap._contract = CapabilityContract(
            name="test_cap",
            params={},
            secrets={
                "K1": SecretMeta(name="K1", description="", type="string", required=True),
            },
        )
        cap = _Cap()
        cap.id = "test_cap"
        cap.logger = self.logger

        wl = self._make_workload(
            'REQUIRES = {"test_cap"}\n'
            'REQUIRED_SECRETS = {"test_cap": ["K2"]}\n',
            cap,
        )
        result = wl._capability_binder._collect_required_secrets()
        self.assertEqual(result, {})

    def test_no_roles_returns_empty(self):
        """No role files → empty."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _Cap(VOXCapability):
            CAPABILITY_NAME = "test_cap"

        _Cap._contract = CapabilityContract(
            name="test_cap",
            params={},
            secrets={
                "K1": SecretMeta(name="K1", description="", type="string", required=True),
            },
        )
        cap = _Cap()
        cap.id = "test_cap"
        cap.logger = self.logger

        wl = self._make_workload("", cap)
        result = wl._capability_binder._collect_required_secrets()
        self.assertEqual(result, {})

    def test_union_across_multiple_roles(self):
        """Two roles declare different secrets for same cap → union then intersect."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _Cap(VOXCapability):
            CAPABILITY_NAME = "test_cap"

        _Cap._contract = CapabilityContract(
            name="test_cap",
            params={},
            secrets={
                "K1": SecretMeta(name="K1", description="", type="string", required=True),
                "K2": SecretMeta(name="K2", description="", type="string", required=True),
                "K3": SecretMeta(name="K3", description="", type="string", required=True),
            },
        )
        cap = _Cap()
        cap.id = "test_cap"
        cap.logger = self.logger

        (self.tmp / "roles" / "role_a.py").write_text(
            'REQUIRES = {"test_cap"}\n'
            'REQUIRED_SECRETS = {"test_cap": ["K1"]}\n'
        )
        (self.tmp / "roles" / "role_b.py").write_text(
            'REQUIRES = {"test_cap"}\n'
            'REQUIRED_SECRETS = {"test_cap": ["K2"]}\n'
        )

        def _get_cap(cap_id):
            if cap_id == "comm.gateway":
                return self.mock_messenger_cap
            return cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap
        wl = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = wl._capability_binder._collect_required_secrets()
        self.assertEqual(result, {"test_cap": {"K1", "K2"}})


class TestInjectVaultSecretsRequiredVsOptional(_MessengerMockMixin, unittest.TestCase):
    """inject_vault_secrets() distinguishes required vs optional on vault-unavailable."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_inject_opt_{id(self)}"
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "manifest.yml").write_text(
            "name: TestWorkload\nid: test-uuid-inject\nautostart: false\n"
        )
        self._saved_key = os.environ.pop("VOX_MASTER_KEY", None)
        self.logger = MagicMock()
        self.orchestrator = MagicMock()
        self.mock_messenger_cap, _ = self._make_messenger_mocks()
        self.orchestrator.get_capability_instance.return_value = self.mock_messenger_cap

    def tearDown(self):
        if self._saved_key is not None:
            os.environ["VOX_MASTER_KEY"] = self._saved_key
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vault_unavailable_only_optional_no_error(self):
        """Vault unavailable + only optional secrets → warning, no VaultAccessError."""
        from vox.capabilities.base import CapabilityContract, SecretMeta

        class _OptCap(VOXCapability):
            CAPABILITY_NAME = "opt_cap"

        _OptCap._contract = CapabilityContract(
            name="opt_cap",
            params={},
            secrets={
                "OPT_KEY": SecretMeta(name="OPT_KEY", description="", type="string", required=False),
            },
        )
        cap = _OptCap()
        cap.id = "opt_cap"
        cap.logger = self.logger

        (self.tmp / "roles" / "chat.py").write_text(
            'REQUIRES = {"opt_cap"}\n'
            'REQUIRED_SECRETS = {"opt_cap": ["OPT_KEY"]}\n'
        )

        def _get_cap(cap_id):
            if cap_id == "comm.gateway":
                return self.mock_messenger_cap
            return cap

        self.orchestrator.get_capability_instance.side_effect = _get_cap
        wl = VOXWorkload(self.tmp, self.logger, self.orchestrator)
        result = asyncio.run(wl.boot())
        self.assertTrue(result)
        self.assertEqual(wl.state, WorkloadState.ACTIVE)
