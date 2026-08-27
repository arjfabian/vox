"""Tests for the YAML-authoritative capability contract system.

Validates that:
- Parameters are loaded from capability.yml (single source of truth)
- YAML defaults are used when workload config does not override
- Workload config overrides YAML defaults
- None default means required (degraded if missing)
- False/0/empty-string are valid values (not treated as missing)
- Descriptions, types, and sensitivity come from YAML
- explain_config() reflects YAML metadata
- validate_params() reflects YAML metadata
- mount() resolves YAML defaults correctly
- Router contains no hard-coded model defaults
- ai.llm uses LLM_MODEL_NAME.default from YAML
- Gateway params are not dependent on adapter Python PARAMS
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from vox.capabilities.base import (
    CapabilityContract,
    ParamMeta,
    SecretMeta,
    VOXCapability,
    load_capability_yaml,
)


class TestLoadCapabilityYAML(unittest.TestCase):
    """Tests for the YAML loader itself."""

    def test_load_valid_yaml(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="capability.py", delete=False
        ) as f:
            f.write("# placeholder")
            cap_file = Path(f.name)
        yml = cap_file.with_name("capability.yml")
        yml.write_text(
            "name: test.cap\n"
            "version: 1.0\n"
            "params:\n"
            "  FOO:\n"
            "    description: A foo\n"
            "    type: string\n"
            "    default: bar\n"
            "secrets:\n"
            "  SECRET:\n"
            "    description: A secret\n"
            "    type: string\n"
            "    required: true\n"
        )
        try:
            contract = load_capability_yaml(cap_file)
            self.assertIsNotNone(contract)
            self.assertEqual(contract.name, "test.cap")
            self.assertEqual(contract.version, "1.0")
            self.assertIn("FOO", contract.params)
            self.assertEqual(contract.params["FOO"].default, "bar")
            self.assertNotIn("SECRET", contract.params)
            self.assertIn("SECRET", contract.secrets)
            self.assertTrue(contract.secrets["SECRET"].required)
        finally:
            cap_file.unlink()
            yml.unlink(missing_ok=True)

    def test_missing_yaml_returns_none(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="capability.py", delete=False
        ) as f:
            f.write("# placeholder")
            cap_file = Path(f.name)
        try:
            result = load_capability_yaml(cap_file)
            self.assertIsNone(result)
        finally:
            cap_file.unlink()

    def test_invalid_yaml_returns_none(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="capability.py", delete=False
        ) as f:
            f.write("# placeholder")
            cap_file = Path(f.name)
        yml = cap_file.with_name("capability.yml")
        yml.write_text("::: invalid yaml :::")
        try:
            result = load_capability_yaml(cap_file)
            self.assertIsNone(result)
        finally:
            cap_file.unlink()
            yml.unlink(missing_ok=True)

    def test_yaml_with_no_params(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="capability.py", delete=False
        ) as f:
            f.write("# placeholder")
            cap_file = Path(f.name)
        yml = cap_file.with_name("capability.yml")
        yml.write_text("name: empty\ndescription: No params\n")
        try:
            contract = load_capability_yaml(cap_file)
            self.assertIsNotNone(contract)
            self.assertEqual(contract.params, {})
        finally:
            cap_file.unlink()
            yml.unlink(missing_ok=True)


class TestParamMeta(unittest.TestCase):
    def test_frozen_dataclass(self):
        meta = ParamMeta(name="X", description="desc", type="string", default="val")
        self.assertEqual(meta.name, "X")
        with self.assertRaises(AttributeError):
            meta.name = "Y"


class TestCapabilityContract(unittest.TestCase):
    def test_required_secret_names(self):
        contract = CapabilityContract(
            name="test",
            params={
                "A": ParamMeta(name="A", description="", type="string", default="x"),
            },
            secrets={
                "S1": SecretMeta(name="S1", description="", type="string", required=True),
                "S2": SecretMeta(name="S2", description="", type="string", required=False),
            },
        )
        self.assertEqual(contract.required_secret_names, {"S1"})

    def test_required_params_property(self):
        contract = CapabilityContract(
            name="test",
            params={
                "A": ParamMeta(name="A", description="", type="string", default="x"),
                "B": ParamMeta(name="B", description="", type="string", default=None),
                "C": ParamMeta(name="C", description="", type="bool", default=False),
            },
        )
        self.assertEqual(contract.required_params, {"B"})


class TestYAMLContractLoading(unittest.TestCase):
    """Tests that load_contract() correctly populates the class."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._cap_file = self._tmpdir / "capability.py"
        self._cap_file.write_text("# placeholder")
        self._yml = self._tmpdir / "capability.yml"
        self._yml.write_text(
            "name: yaml.test\n"
            "version: 2.0\n"
            "params:\n"
            "  FOO:\n"
            "    description: The foo\n"
            "    type: string\n"
            "    default: baz\n"
            "  REQUIRED_FIELD:\n"
            "    description: Required\n"
            "    type: string\n"
            "    default: null\n"
            "secrets:\n"
            "  SECRET_KEY:\n"
            "    description: A secret\n"
            "    type: string\n"
            "    required: true\n"
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_contract_populates_params(self):
        class YamlCap(VOXCapability):
            CAPABILITY_NAME = "yaml.test"

        contract = YamlCap.load_contract(self._cap_file)
        self.assertIsNotNone(contract)
        self.assertEqual(contract.name, "yaml.test")
        self.assertEqual(contract.version, "2.0")
        self.assertIn("FOO", contract.params)
        self.assertIn("REQUIRED_FIELD", contract.params)
        self.assertIn("SECRET_KEY", contract.secrets)

    def test_get_params_reads_from_yaml(self):
        class YamlCap(VOXCapability):
            CAPABILITY_NAME = "yaml.test"

        YamlCap.load_contract(self._cap_file)
        params = YamlCap.get_params()
        self.assertEqual(params, ["FOO", "REQUIRED_FIELD"])

    def test_get_secret_names_reads_from_yaml(self):
        class YamlCap(VOXCapability):
            CAPABILITY_NAME = "yaml.test"

        YamlCap.load_contract(self._cap_file)
        secrets = YamlCap.get_secret_names()
        self.assertEqual(secrets, {"SECRET_KEY"})

    def test_get_param_meta(self):
        class YamlCap(VOXCapability):
            CAPABILITY_NAME = "yaml.test"

        YamlCap.load_contract(self._cap_file)
        meta = YamlCap.get_param_meta("FOO")
        self.assertIsNotNone(meta)
        self.assertEqual(meta.description, "The foo")
        self.assertEqual(meta.default, "baz")

    def test_explain_config_reads_from_yaml(self):
        class YamlCap(VOXCapability):
            CAPABILITY_NAME = "yaml.test"

        YamlCap.load_contract(self._cap_file)
        text = YamlCap.explain_config()
        self.assertIn("The foo", text)
        self.assertIn("[Default: baz]", text)
        self.assertIn("[REQUIRED]", text)


class TestMountResolvesYAMLDefaults(unittest.TestCase):
    """Tests that mount() correctly resolves config against YAML defaults."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._cap_file = self._tmpdir / "capability.py"
        self._cap_file.write_text("# placeholder")
        self._yml = self._tmpdir / "capability.yml"
        self._yml.write_text(
            "name: mount.test\n"
            "params:\n"
            "  HOST:\n"
            "    description: Server host\n"
            "    type: string\n"
            "    default: localhost\n"
            "  PORT:\n"
            "    description: Server port\n"
            "    type: integer\n"
            "    default: 8080\n"
            "  REQUIRED:\n"
            "    description: Required param\n"
            "    type: string\n"
            "    default: null\n"
            "secrets:\n"
            "  SECRET:\n"
            "    description: Secret\n"
            "    type: string\n"
            "    required: true\n"
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_cap(self):
        class MountCap(VOXCapability):
            CAPABILITY_NAME = "mount.test"

        MountCap.load_contract(self._cap_file)
        cap = MountCap()
        cap.id = "mount.test"
        cap.logger = MagicMock()
        return cap

    def test_yaml_default_used_when_config_absent(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload, {})
        self.assertEqual(bound._params["HOST"], "localhost")
        self.assertEqual(bound._params["PORT"], 8080)

    def test_workload_config_overrides_yaml_default(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload, {"HOST": "0.0.0.0", "PORT": 9090})
        self.assertEqual(bound._params["HOST"], "0.0.0.0")
        self.assertEqual(bound._params["PORT"], 9090)

    def test_none_default_remains_none_when_not_in_config(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload, {})
        self.assertIsNone(bound._params["REQUIRED"])

    def test_false_zero_empty_are_valid_values(self):
        """False, 0, and '' should NOT be treated as missing."""
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(
            workload,
            {"HOST": "", "PORT": 0, "REQUIRED": False},
        )
        self.assertEqual(bound._params["HOST"], "")
        self.assertEqual(bound._params["PORT"], 0)
        self.assertEqual(bound._params["REQUIRED"], False)

    def test_bound_capability_accesses_params_as_attributes(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload, {"HOST": "custom"})
        self.assertEqual(bound.HOST, "custom")
        self.assertEqual(bound.PORT, 8080)

    def test_unknown_override_raises_value_error(self):
        cap = self._make_cap()
        workload = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            cap.mount(workload, overrides={"UNKNOWN_PARAM": "value"})
        self.assertIn("UNKNOWN_PARAM", str(ctx.exception))

    def test_overrides_applied_after_defaults(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload, overrides={"HOST": "from_override"})
        self.assertEqual(bound.HOST, "from_override")
        self.assertEqual(bound.PORT, 8080)

    def test_overrides_validated_against_contract(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(
            workload, overrides={"HOST": "override_host", "PORT": 9999}
        )
        self.assertEqual(bound.HOST, "override_host")
        self.assertEqual(bound.PORT, 9999)

    def test_no_overrides_still_works(self):
        cap = self._make_cap()
        workload = MagicMock()
        bound = cap.mount(workload)
        self.assertEqual(bound.HOST, "localhost")
        self.assertEqual(bound.PORT, 8080)


class TestValidateParamsReflectsYAML(unittest.TestCase):
    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._cap_file = self._tmpdir / "capability.py"
        self._cap_file.write_text("# placeholder")
        self._yml = self._tmpdir / "capability.yml"
        self._yml.write_text(
            "name: validate.test\n"
            "params:\n"
            "  OPTIONAL:\n"
            "    description: Optional\n"
            "    type: string\n"
            "    default: hello\n"
            "  REQUIRED:\n"
            "    description: Required\n"
            "    type: string\n"
            "    default: null\n"
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_required_param_detected(self):
        class ValCap(VOXCapability):
            CAPABILITY_NAME = "validate.test"

        ValCap.load_contract(self._cap_file)
        cap = ValCap()
        cap.id = "validate.test"
        cap.logger = MagicMock()
        workload = MagicMock()
        bound = cap.mount(workload, {})
        missing = bound.validate_params()
        self.assertEqual(missing, ["REQUIRED"])

    def test_provided_required_param_not_missing(self):
        class ValCap(VOXCapability):
            CAPABILITY_NAME = "validate.test"

        ValCap.load_contract(self._cap_file)
        cap = ValCap()
        cap.id = "validate.test"
        cap.logger = MagicMock()
        workload = MagicMock()
        bound = cap.mount(workload, {"REQUIRED": "value"})
        missing = bound.validate_params()
        self.assertEqual(missing, [])

    def test_empty_string_is_not_missing(self):
        class ValCap(VOXCapability):
            CAPABILITY_NAME = "validate.test"

        ValCap.load_contract(self._cap_file)
        cap = ValCap()
        cap.id = "validate.test"
        cap.logger = MagicMock()
        workload = MagicMock()
        bound = cap.mount(workload, {"REQUIRED": ""})
        missing = bound.validate_params()
        self.assertEqual(missing, [])

    def test_initialize_raises_for_missing(self):
        class ValCap(VOXCapability):
            CAPABILITY_NAME = "validate.test"

        ValCap.load_contract(self._cap_file)
        cap = ValCap()
        cap.id = "validate.test"
        cap.logger = MagicMock()
        workload = MagicMock()
        bound = cap.mount(workload, {})
        with self.assertRaises(ValueError):
            asyncio.run(bound.initialize())


class TestGatewayParamsFromYAML(unittest.TestCase):
    """Gateway params should come from adapter config.yml, secrets from vault."""

    @classmethod
    def setUpClass(cls):
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        cap_file = Path(
            "src/vox/capabilities/comm/gateway/capability.py"
        )
        CommGatewayCapability.load_contract(cap_file)

    def test_gateway_has_all_adapter_params_in_contract(self):
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        params = CommGatewayCapability.get_params()
        self.assertIn("GATEWAY_PORT", params)
        self.assertIn("GATEWAY_HOST", params)
        self.assertIn("TELEGRAM_LONG_TIMEOUT", params)
        self.assertIn("WHATSAPP_PHONE_NUMBER_ID", params)
        self.assertIn("WHATSAPP_API_VERSION", params)

    def test_gateway_has_all_adapter_secrets_in_contract(self):
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        secrets = CommGatewayCapability.get_secret_names()
        self.assertIn("TELEGRAM_BOT_TOKEN", secrets)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", secrets)
        self.assertIn("GATEWAY_WEBHOOK_SECRET", secrets)
        self.assertIn("WHATSAPP_ACCESS_TOKEN", secrets)
        self.assertIn("WHATSAPP_APP_SECRET", secrets)
        self.assertIn("WHATSAPP_VERIFY_TOKEN", secrets)

    def test_gateway_required_secrets(self):
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        required = CommGatewayCapability._ensure_contract().required_secret_names
        self.assertIn("TELEGRAM_BOT_TOKEN", required)
        self.assertIn("WHATSAPP_ACCESS_TOKEN", required)
        self.assertIn("WHATSAPP_APP_SECRET", required)
        self.assertNotIn("TELEGRAM_WEBHOOK_SECRET", required)
        self.assertNotIn("WHATSAPP_VERIFY_TOKEN", required)
        self.assertNotIn("GATEWAY_WEBHOOK_SECRET", required)

    def test_gateway_no_class_level_PARAMS_dict(self):
        """The gateway class should NOT have PARAMS as a plain dict in __dict__."""
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        raw = CommGatewayCapability.__dict__.get("PARAMS")
        # Should be the descriptor or absent — not a plain dict
        self.assertFalse(
            isinstance(raw, dict),
            "Gateway should not have a plain PARAMS dict in __dict__",
        )

    def test_adapter_param_specs_still_works(self):
        """adapter_param_specs() returns per-channel specs from adapter PARAMS."""
        from vox.capabilities.comm.gateway.capability import CommGatewayCapability

        specs = CommGatewayCapability.adapter_param_specs()
        self.assertIn("telegram", specs)
        self.assertIn("webhook", specs)
        self.assertIn("whatsapp", specs)
        self.assertIn("GATEWAY_WEBHOOK_SECRET", specs["webhook"])
        self.assertIn("WHATSAPP_ACCESS_TOKEN", specs["whatsapp"])


class TestAILLMParamsFromYAML(unittest.TestCase):
    """ai.llm params should come from YAML, not from Python PARAMS."""

    @classmethod
    def setUpClass(cls):
        from vox.capabilities.ai.llm.capability import LLMCapability

        cap_file = Path("src/vox/capabilities/ai/llm/capability.py")
        LLMCapability.load_contract(cap_file)

    def test_llm_has_all_params(self):
        from vox.capabilities.ai.llm.capability import LLMCapability

        params = LLMCapability.get_params()
        self.assertIn("LLM_API_BASE_URL", params)
        self.assertIn("LLM_MODEL_NAME", params)
        self.assertIn("LLM_VISION_MODEL_NAME", params)
        self.assertIn("LLM_TEMPERATURE", params)
        self.assertIn("LLM_MAX_TOKENS", params)
        self.assertIn("LLM_TIMEOUT", params)
        self.assertIn("LLM_CACHE_ENABLED", params)
        self.assertIn("LLM_CACHE_TTL", params)
        self.assertIn("LLM_CACHE_DB_PATH", params)
        self.assertIn("LLM_RAG_ENABLED", params)
        self.assertIn("LLM_RAG_MAX_SNIPPETS", params)
        self.assertIn("LLM_MAX_INPUT_TOKENS", params)
        self.assertEqual(len(params), 12)

    def test_llm_no_class_level_PARAMS_dict(self):
        from vox.capabilities.ai.llm.capability import LLMCapability

        raw = LLMCapability.__dict__.get("PARAMS")
        self.assertFalse(
            isinstance(raw, dict),
            "LLM capability should not have a plain PARAMS dict in __dict__",
        )

    def test_llm_yaml_default_is_authoritative(self):
        """Changing the YAML default should change the runtime behavior."""
        from vox.capabilities.ai.llm.capability import LLMCapability

        meta = LLMCapability.get_param_meta("LLM_MODEL_NAME")
        self.assertIsNotNone(meta)
        self.assertEqual(meta.default, "llama3.2:1b")

    def test_llm_no_secondary_model(self):
        from vox.capabilities.ai.llm.capability import LLMCapability

        params = LLMCapability.get_params()
        self.assertNotIn("LLM_SECONDARY_MODEL", params)
        self.assertNotIn("LLM_FORCE_PREFERRED", params)
        self.assertNotIn("LLM_FORCE_SECONDARY", params)

    def test_llm_router_requires_model(self):
        """Router requires a model — no hardcoded fallbacks."""
        from vox.capabilities.ai.llm.router import Router

        with self.assertRaises(ValueError):
            Router(model="")


class TestCapabilityName(unittest.TestCase):
    def test_name_from_contract(self):
        class NamedCap(VOXCapability):
            CAPABILITY_NAME = ""

        NamedCap._contract = CapabilityContract(name="from.yaml")
        cap = NamedCap()
        self.assertEqual(cap.name, "from.yaml")

    def test_name_from_CAPABILITY_NAME(self):
        class NamedCap(VOXCapability):
            CAPABILITY_NAME = "my.cap"

        cap = NamedCap()
        self.assertEqual(cap.name, "my.cap")
