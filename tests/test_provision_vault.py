"""Tests for provision_vault — YAML-based secret discovery."""

import tempfile
import unittest
from pathlib import Path

from vox.capabilities.base import CapabilityContract, ParamMeta, SecretMeta


class TestLoadGatewayContractSecrets(unittest.TestCase):
    """_load_gateway_contract_secrets() aggregates secrets from adapter config.yml."""

    def test_aggregates_from_all_adapters(self):
        import sys

        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from provision_vault import _load_gateway_contract_secrets

        result = _load_gateway_contract_secrets()
        self.assertIn("comm.gateway", result)
        info = result["comm.gateway"]
        secret_keys = {key for key, _ in info["sensitive"]}
        self.assertIn("TELEGRAM_BOT_TOKEN", secret_keys)
        self.assertIn("WHATSAPP_ACCESS_TOKEN", secret_keys)
        self.assertIn("GATEWAY_WEBHOOK_SECRET", secret_keys)

    def test_required_flags_preserved(self):
        import sys

        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from provision_vault import _load_gateway_contract_secrets

        result = _load_gateway_contract_secrets()
        info = result["comm.gateway"]
        sensitive = dict(info["sensitive"])
        self.assertTrue(sensitive["TELEGRAM_BOT_TOKEN"])
        self.assertFalse(sensitive["TELEGRAM_WEBHOOK_SECRET"])
        self.assertFalse(sensitive["GATEWAY_WEBHOOK_SECRET"])


class TestDiscoverSensitiveParams(unittest.TestCase):
    """_discover_sensitive_params() reads from YAML contract secrets."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_from_contract_secrets_not_params(self):
        import sys
        import unittest.mock

        tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from provision_vault import _discover_sensitive_params

        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        (self.tmp / "roles" / "chat.py").write_text(
            "from vox.roles import VOXRole\n"
            "\n"
            "class ChatRole(VOXRole):\n"
            '    REQUIRES = {"test_cap"}\n'
        )

        test_contract = CapabilityContract(
            name="test_cap",
            params={
                "API_URL": ParamMeta(name="API_URL", description="URL", type="string", default=""),
            },
            secrets={
                "API_KEY": SecretMeta(name="API_KEY", description="Key", type="string", required=True),
            },
        )

        with unittest.mock.patch(
            "provision_vault._load_capability_contract",
            return_value=(test_contract, None),
        ):
            result = _discover_sensitive_params(self.tmp)

        self.assertIn("test_cap", result)
        sensitive_keys = {key for key, _ in result["test_cap"]["sensitive"]}
        self.assertIn("API_KEY", sensitive_keys)
        self.assertNotIn("API_URL", sensitive_keys)


if __name__ == "__main__":
    unittest.main()
