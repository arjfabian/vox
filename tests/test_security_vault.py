import os
import unittest
from pathlib import Path
from unittest.mock import patch

from vox.security.vault import AgentVault


class TestAgentVault(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_vault_{id(self)}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        os.environ["VOX_MASTER_KEY"] = "test-master-key-32bytes!!"
        self.vault = AgentVault(self.tmp, "agent-uuid-1234")

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        os.environ.pop("VOX_MASTER_KEY", None)

    def test_init_creates_vault_file(self):
        self.assertTrue(self.vault.exists())

    def test_set_creates_vault(self):
        self.vault.set("llm", "api_key", "sk-1234")
        self.assertTrue(self.vault.exists())

    def test_get_returns_stored_value(self):
        self.vault.set("llm", "api_key", "sk-1234")
        value = self.vault.get("llm", "api_key")
        self.assertEqual(value, "sk-1234")

    def test_get_nonexistent_returns_none(self):
        value = self.vault.get("nonexistent", "key")
        self.assertIsNone(value)

    def test_get_falls_back_to_config(self):
        vault = AgentVault(self.tmp, "agent-uuid", config={"api_key": "from-env"})
        value = vault.get("llm", "api_key")
        self.assertEqual(value, "from-env")

    def test_disable_and_activate(self):
        self.vault.set("llm", "api_key", "sk-1234")
        self.vault.disable("llm", "api_key")
        value = self.vault.get("llm", "api_key")
        self.assertIsNone(value)
        self.vault.activate("llm", "api_key")
        value = self.vault.get("llm", "api_key")
        self.assertEqual(value, "sk-1234")

    def test_list_inactive(self):
        self.vault.set("llm", "key1", "val1", status="inactive")
        self.vault.set("llm", "key2", "val2")
        inactive = self.vault.list_inactive()
        self.assertIn(("llm", "key1"), inactive)
        self.assertNotIn(("llm", "key2"), inactive)

    def test_list_active(self):
        self.vault.set("llm", "k1", "v1")
        self.vault.set("llm", "k2", "v2")
        active = self.vault.list_active()
        self.assertIn("llm", active)
        self.assertEqual(active["llm"]["k1"], "v1")
        self.assertEqual(active["llm"]["k2"], "v2")

    def test_inactive_entries_excluded_from_list_active(self):
        self.vault.set("llm", "k1", "v1", status="inactive")
        active = self.vault.list_active()
        self.assertNotIn("llm", active)

    def test_encrypt_decrypt_roundtrip(self):
        self.vault.set("test", "secret", "sensitive-value")
        value = self.vault.get("test", "secret")
        self.assertEqual(value, "sensitive-value")

    def test_missing_master_key_raises(self):
        os.environ.pop("VOX_MASTER_KEY", None)
        with self.assertRaises(RuntimeError):
            AgentVault(self.tmp, "agent-uuid")

    def test_get_returns_none_for_inactive(self):
        self.vault.set("llm", "key", "val")
        self.vault.disable("llm", "key")
        result = self.vault.get("llm", "key")
        self.assertIsNone(result)

    def test_fallback_to_config_only_when_no_db_entry(self):
        self.vault.set("llm", "key", "from-vault")
        vault_with_fallback = AgentVault(
            self.tmp, "agent-uuid-1234",
            config={"key": "from-env"},
        )
        value = vault_with_fallback.get("llm", "key")
        self.assertEqual(value, "from-vault")
