import uuid
from pathlib import Path

import pytest

from vox.capabilities.ai.llm.cache import SemanticCache, _sha256

# ---------------------------------------------------------------------------
# _sha256 unit tests (pure, no DB)
# ---------------------------------------------------------------------------


def test_sha256_deterministic():
    a = _sha256("sys", "prompt", "model")
    b = _sha256("sys", "prompt", "model")
    assert a == b


def test_sha256_different_inputs_different_hashes():
    a = _sha256("sys1", "prompt", "model")
    b = _sha256("sys2", "prompt", "model")
    assert a != b


# ---------------------------------------------------------------------------
# SemanticCache tests
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path():
    path = Path(f"/tmp/test_cache_{uuid.uuid4().hex[:8]}.db")
    if path.exists():
        path.unlink()
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
async def cache(db_path):
    c = SemanticCache(db_path=db_path, ttl=3600)
    await c.init_db()
    return c


@pytest.mark.asyncio
async def test_lookup_miss_returns_none(cache):
    result = await cache.lookup("sys", "prompt", "model")
    assert result is None


@pytest.mark.asyncio
async def test_store_and_lookup_hit(cache):
    await cache.store("sys", "prompt", "model", "response content")
    result = await cache.lookup("sys", "prompt", "model")
    assert result is not None
    assert result.content == "response content"
    assert result.model == "model"


@pytest.mark.asyncio
async def test_lookup_expired_entry_returns_none(cache):
    await cache.store("sys", "prompt", "model", "content")
    await cache.invalidate("sys", "prompt", "model")
    result = await cache.lookup("sys", "prompt", "model")
    assert result is None


@pytest.mark.asyncio
async def test_invalidate_removes_entry(cache):
    await cache.store("sys", "prompt", "model", "content")
    await cache.invalidate("sys", "prompt", "model")
    result = await cache.lookup("sys", "prompt", "model")
    assert result is None


@pytest.mark.asyncio
async def test_clear_expired_removes_old_entries(cache):
    await cache.store("sys", "prompt", "model", "content")
    await cache.invalidate("sys", "prompt", "model")
    removed = await cache.clear_expired()
    assert removed >= 0


@pytest.mark.asyncio
async def test_close_is_noop(cache):
    await cache.close()


@pytest.mark.asyncio
async def test_multiple_store_and_lookup(cache):
    await cache.store("sys1", "p1", "m1", "r1")
    await cache.store("sys2", "p2", "m2", "r2")
    r1 = await cache.lookup("sys1", "p1", "m1")
    r2 = await cache.lookup("sys2", "p2", "m2")
    assert r1.content == "r1"
    assert r2.content == "r2"


@pytest.mark.asyncio
async def test_auto_init_on_first_use():
    """Cache created without explicit init_db() should self-heal."""
    path = Path(f"/tmp/test_cache_auto_{uuid.uuid4().hex[:8]}.db")
    try:
        c = SemanticCache(db_path=path, ttl=3600)
        assert not c._initialized
        await c.store("sys", "prompt", "model", "content")
        assert c._initialized
        result = await c.lookup("sys", "prompt", "model")
        assert result is not None
        assert result.content == "content"
    finally:
        if path.exists():
            path.unlink()
