"""Tests for ASTWorkloadAnalyzer — static role-file scanning."""

import tempfile
import unittest
from pathlib import Path

from vox.workloads.ast_analyzer import ASTWorkloadAnalyzer


class TestScanRequiredSecrets(unittest.TestCase):
    """scan_required_secrets() extracts REQUIRED_SECRETS from role files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_role(self, name: str, content: str) -> Path:
        p = self.tmp / f"{name}.py"
        p.write_text(content)
        return p

    def test_single_cap_single_secret(self):
        path = self._write_role(
            "chat",
            'REQUIRED_SECRETS = {"comm.gateway": ["TELEGRAM_BOT_TOKEN"]}\n',
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {"comm.gateway": ["TELEGRAM_BOT_TOKEN"]})

    def test_single_cap_multiple_secrets(self):
        path = self._write_role(
            "chat",
            'REQUIRED_SECRETS = {"whatsapp": ["ACCESS_TOKEN", "APP_SECRET"]}\n',
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {"whatsapp": ["ACCESS_TOKEN", "APP_SECRET"]})

    def test_multiple_caps(self):
        path = self._write_role(
            "chat",
            'REQUIRED_SECRETS = {"comm.gateway": ["TOKEN"], "whatsapp": ["SECRET"]}\n',
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(
            result,
            {"comm.gateway": ["TOKEN"], "whatsapp": ["SECRET"]},
        )

    def test_no_required_secrets_returns_empty(self):
        path = self._write_role("chat", "x = 1\n")
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_empty_dict_returns_empty(self):
        path = self._write_role("chat", "REQUIRED_SECRETS = {}\n")
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_list_value_is_ignored(self):
        path = self._write_role(
            "chat",
            'REQUIRED_SECRETS = ["bad", "format"]\n',
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_non_string_keys_ignored(self):
        path = self._write_role(
            "chat",
            "REQUIRED_SECRETS = {123: ['val']}\n",
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_non_string_values_ignored(self):
        path = self._write_role(
            "chat",
            'REQUIRED_SECRETS = {"cap": [123]}\n',
        )
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_syntax_error_returns_empty(self):
        path = self._write_role("chat", "def (broken\n")
        result = ASTWorkloadAnalyzer.scan_required_secrets(path)
        self.assertEqual(result, {})

    def test_missing_file_returns_empty(self):
        result = ASTWorkloadAnalyzer.scan_required_secrets(self.tmp / "nonexistent.py")
        self.assertEqual(result, {})


class TestScanRoleCapabilities(unittest.TestCase):
    """scan_role_capabilities() is unchanged — basic sanity checks."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_role(self, name: str, content: str) -> Path:
        p = self.tmp / f"{name}.py"
        p.write_text(content)
        return p

    def test_requires_set(self):
        path = self._write_role("chat", 'REQUIRES = {"cap_a", "cap_b"}\n')
        result = ASTWorkloadAnalyzer.scan_role_capabilities(path)
        self.assertEqual(result, {"cap_a", "cap_b"})

    def test_requires_list(self):
        path = self._write_role("chat", 'REQUIRES = ["cap_a"]\n')
        result = ASTWorkloadAnalyzer.scan_role_capabilities(path)
        self.assertEqual(result, {"cap_a"})

    def test_subscript_access(self):
        path = self._write_role(
            "chat",
            'x = self.workload.capabilities["cap_c"]\n',
        )
        result = ASTWorkloadAnalyzer.scan_role_capabilities(path)
        self.assertEqual(result, {"cap_c"})

    def test_get_call(self):
        path = self._write_role(
            "chat",
            'x = self.workload.capabilities.get("cap_d")\n',
        )
        result = ASTWorkloadAnalyzer.scan_role_capabilities(path)
        self.assertEqual(result, {"cap_d"})


if __name__ == "__main__":
    unittest.main()
