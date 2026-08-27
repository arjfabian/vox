import os
import uuid
from pathlib import Path

import pytest

from vox.security.vault import WorkloadVault


@pytest.fixture(autouse=True)
def _master_key():
    os.environ["VOX_MASTER_KEY"] = "test-master-key-32bytes!!"
    yield
    os.environ.pop("VOX_MASTER_KEY", None)


@pytest.fixture
def vault_dir():
    d = Path(f"/tmp/test_vault_{uuid.uuid4().hex[:8]}")
    d.mkdir(parents=True, exist_ok=True)
    yield d
    import shutil

    if d.exists():
        shutil.rmtree(d)


@pytest.fixture
def vault(vault_dir):
    return WorkloadVault(vault_dir, "workload-uuid-1234")


@pytest.mark.asyncio
async def test_init_creates_vault_file(vault):
    assert vault.exists()


@pytest.mark.asyncio
async def test_set_creates_vault(vault):
    await vault.set("llm", "api_key", "sk-1234")
    assert vault.exists()


@pytest.mark.asyncio
async def test_get_returns_stored_value(vault):
    await vault.set("llm", "api_key", "sk-1234")
    value = await vault.get("llm", "api_key")
    assert value == "sk-1234"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(vault):
    value = await vault.get("nonexistent", "key")
    assert value is None


@pytest.mark.asyncio
async def test_get_falls_back_to_config(vault_dir):
    v = WorkloadVault(vault_dir, "workload-uuid", config={"api_key": "from-env"})
    value = await v.get("llm", "api_key")
    assert value == "from-env"


@pytest.mark.asyncio
async def test_disable_and_activate(vault):
    await vault.set("llm", "api_key", "sk-1234")
    await vault.disable("llm", "api_key")
    value = await vault.get("llm", "api_key")
    assert value is None
    await vault.activate("llm", "api_key")
    value = await vault.get("llm", "api_key")
    assert value == "sk-1234"


@pytest.mark.asyncio
async def test_list_inactive(vault):
    await vault.set("llm", "key1", "val1", status="inactive")
    await vault.set("llm", "key2", "val2")
    inactive = await vault.list_inactive()
    assert ("llm", "key1") in inactive
    assert ("llm", "key2") not in inactive


@pytest.mark.asyncio
async def test_list_active(vault):
    await vault.set("llm", "k1", "v1")
    await vault.set("llm", "k2", "v2")
    active = await vault.list_active()
    assert "llm" in active
    assert active["llm"]["k1"] == "v1"
    assert active["llm"]["k2"] == "v2"


@pytest.mark.asyncio
async def test_inactive_entries_excluded_from_list_active(vault):
    await vault.set("llm", "k1", "v1", status="inactive")
    active = await vault.list_active()
    assert "llm" not in active


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip(vault):
    await vault.set("test", "secret", "sensitive-value")
    value = await vault.get("test", "secret")
    assert value == "sensitive-value"


def test_missing_master_key_raises(vault_dir):
    key = os.environ.pop("VOX_MASTER_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            WorkloadVault(vault_dir, "workload-uuid")
    finally:
        if key is not None:
            os.environ["VOX_MASTER_KEY"] = key


@pytest.mark.asyncio
async def test_get_returns_none_for_inactive(vault):
    await vault.set("llm", "key", "val")
    await vault.disable("llm", "key")
    result = await vault.get("llm", "key")
    assert result is None


@pytest.mark.asyncio
async def test_fallback_to_config_only_when_no_db_entry(vault_dir):
    v1 = WorkloadVault(vault_dir, "workload-uuid-1234")
    await v1.set("llm", "key", "from-vault")

    v2 = WorkloadVault(
        vault_dir,
        "workload-uuid-1234",
        config={"key": "from-env"},
    )
    value = await v2.get("llm", "key")
    assert value == "from-vault"
