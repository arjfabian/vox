"""Tests for provision_vault — workload-scoped, YAML-based secret discovery.

Provisioning mirrors ``CapabilityBinder``: a secret is required only when the
aggregated capability contract marks it ``required: true`` AND at least one
loaded role declares it in ``REQUIRED_SECRETS``. Adapter existence elsewhere in
the repository NEVER makes a secret required for a workload.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vox.capabilities.base import CapabilityContract, ParamMeta, SecretMeta

GATEWAY_SECRETS = {
    "TELEGRAM_BOT_TOKEN": SecretMeta(
        name="TELEGRAM_BOT_TOKEN",
        description="Telegram Bot API token",
        type="string",
        required=True,
    ),
    "TELEGRAM_WEBHOOK_SECRET": SecretMeta(
        name="TELEGRAM_WEBHOOK_SECRET",
        description="Telegram webhook secret token",
        type="string",
        required=False,
    ),
    "WHATSAPP_ACCESS_TOKEN": SecretMeta(
        name="WHATSAPP_ACCESS_TOKEN",
        description="WhatsApp Cloud API access token",
        type="string",
        required=True,
    ),
    "WHATSAPP_APP_SECRET": SecretMeta(
        name="WHATSAPP_APP_SECRET",
        description="Meta app secret for webhook HMAC verification",
        type="string",
        required=True,
    ),
    "WHATSAPP_VERIFY_TOKEN": SecretMeta(
        name="WHATSAPP_VERIFY_TOKEN",
        description="Webhook verification token",
        type="string",
        required=False,
    ),
    "GATEWAY_WEBHOOK_SECRET": SecretMeta(
        name="GATEWAY_WEBHOOK_SECRET",
        description="Shared secret for generic webhook validation",
        type="string",
        required=False,
    ),
}

LLM_SECRETS = {
    "GEMINI_API_KEY": SecretMeta(
        name="GEMINI_API_KEY",
        description="Google AI Studio API key",
        type="string",
        required=True,
    ),
}


def _gateway_contract() -> CapabilityContract:
    return CapabilityContract(name="comm.gateway", secrets=dict(GATEWAY_SECRETS))


def _llm_contract() -> CapabilityContract:
    return CapabilityContract(name="ai.llm", secrets=dict(LLM_SECRETS))


def _ensure_tools_on_path() -> None:
    tools_dir = str(Path(__file__).resolve().parent.parent / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)


class TestProvisioningScope(unittest.TestCase):
    """_discover_sensitive_params() is workload-scoped via REQUIRED_SECRETS."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "roles").mkdir(parents=True, exist_ok=True)
        _ensure_tools_on_path()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    # --------------------------------------------------------------------------
    # Fixtures
    # --------------------------------------------------------------------------

    def _write_role(
        self,
        name: str,
        cap_subscripts: tuple[str, ...] = (),
        required_secrets: dict | None = None,
    ) -> None:
        lines = ["from vox.roles import VOXRole", ""]
        if required_secrets:
            lines.append(f"REQUIRED_SECRETS = {required_secrets!r}")
            lines.append("")
        lines.append("class Role(VOXRole):")
        lines.append("    def __init__(self, agent):")
        lines.append("        super().__init__(agent)")
        lines.append("")
        lines.append("    def use(self):")
        for cap in cap_subscripts:
            lines.append(
                f'        self.workload.capabilities["{cap}"].send_text'
            )
        (self.tmp / "roles" / f"{name}.py").write_text("\n".join(lines))

    def _discover(self, contracts: dict) -> dict:
        from provision_vault import _discover_sensitive_params

        def _fake(cap_id):
            contract = contracts.get(cap_id)
            if contract is None:
                return None, f"no contract for {cap_id}"
            return contract, None

        with mock.patch(
            "provision_vault._load_capability_contract", side_effect=_fake
        ):
            return _discover_sensitive_params(self.tmp)

    def _sensitive_keys(self, result: dict, cap_id: str) -> set[str]:
        return {key for key, _ in result[cap_id]["sensitive"]}

    # --------------------------------------------------------------------------
    # The reported regression: an unused adapter's secrets are NOT required
    # --------------------------------------------------------------------------

    def test_unused_whatsapp_secrets_are_not_required(self):
        self._write_role(
            "chat",
            cap_subscripts=("comm.gateway",),
            required_secrets={"comm.gateway": ["TELEGRAM_BOT_TOKEN"]},
        )

        result = self._discover({"comm.gateway": _gateway_contract()})

        self.assertEqual(
            self._sensitive_keys(result, "comm.gateway"),
            {"TELEGRAM_BOT_TOKEN"},
        )
        for unused in (
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_APP_SECRET",
            "WHATSAPP_VERIFY_TOKEN",
            "GATEWAY_WEBHOOK_SECRET",
            "TELEGRAM_WEBHOOK_SECRET",
        ):
            self.assertNotIn(unused, self._sensitive_keys(result, "comm.gateway"))

    def test_unused_adapter_secrets_absent_without_any_declaration(self):
        self._write_role("chat", cap_subscripts=("comm.gateway",))

        result = self._discover({"comm.gateway": _gateway_contract()})

        self.assertEqual(result, {})

    def test_optional_secret_is_never_provisioned(self):
        self._write_role(
            "chat",
            cap_subscripts=("comm.gateway",),
            required_secrets={"comm.gateway": ["GATEWAY_WEBHOOK_SECRET"]},
        )

        result = self._discover({"comm.gateway": _gateway_contract()})

        self.assertEqual(result, {})

    def test_declared_secret_not_in_contract_is_ignored(self):
        self._write_role(
            "chat",
            cap_subscripts=("comm.gateway",),
            required_secrets={"comm.gateway": ["NOT_A_REAL_SECRET"]},
        )

        result = self._discover({"comm.gateway": _gateway_contract()})

        self.assertEqual(result, {})

    def test_declaration_alone_does_not_pull_in_unreferenced_capability(self):
        self._write_role(
            "chat",
            cap_subscripts=(),
            required_secrets={"comm.gateway": ["TELEGRAM_BOT_TOKEN"]},
        )

        result = self._discover({"comm.gateway": _gateway_contract()})

        self.assertEqual(result, {})

    # --------------------------------------------------------------------------
    # ai.llm: a newly added adapter must not leak its secret to every workload
    # --------------------------------------------------------------------------

    def test_gemini_key_required_only_when_declared(self):
        self._write_role(
            "assistant",
            cap_subscripts=("ai.llm",),
            required_secrets={"ai.llm": ["GEMINI_API_KEY"]},
        )

        result = self._discover({"ai.llm": _llm_contract()})

        self.assertEqual(
            self._sensitive_keys(result, "ai.llm"),
            {"GEMINI_API_KEY"},
        )

    def test_gemini_key_not_required_when_not_declared(self):
        self._write_role("assistant", cap_subscripts=("ai.llm",))

        result = self._discover({"ai.llm": _llm_contract()})

        self.assertEqual(result, {})

    # --------------------------------------------------------------------------
    # Param/secret separation (Vault-only credential model)
    # --------------------------------------------------------------------------

    def test_params_are_never_treated_as_secrets(self):
        self._write_role(
            "chat",
            cap_subscripts=("test_cap",),
            required_secrets={"test_cap": ["API_KEY"]},
        )
        contract = CapabilityContract(
            name="test_cap",
            params={
                "API_URL": ParamMeta(
                    name="API_URL",
                    description="URL",
                    type="string",
                    default="",
                ),
            },
            secrets={
                "API_KEY": SecretMeta(
                    name="API_KEY",
                    description="Key",
                    type="string",
                    required=True,
                ),
            },
        )

        result = self._discover({"test_cap": contract})

        sensitive = self._sensitive_keys(result, "test_cap")
        self.assertIn("API_KEY", sensitive)
        self.assertNotIn("API_URL", sensitive)


if __name__ == "__main__":
    unittest.main()