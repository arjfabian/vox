import time

import pytest

from vox.agents.memory import VOXAgentMemory


@pytest.fixture
async def memory(tmp_path):
    m = VOXAgentMemory(tmp_path)
    await m.init_db()
    yield m


@pytest.mark.asyncio
async def test_init_creates_db(tmp_path):
    m = VOXAgentMemory(tmp_path)
    db_path = tmp_path / "memory" / "logs.db"
    assert not db_path.exists()
    await m.init_db()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_record_returns_event_id(memory):
    event_id = await memory.record("test_event", "test_action")
    assert isinstance(event_id, str)
    assert len(event_id) > 0


@pytest.mark.asyncio
async def test_record_with_all_fields(memory):
    event_id = await memory.record(
        event_type="cmd",
        action="run",
        actor="tina",
        details={"key": "value"},
        ref_id="ref-1",
        status="PENDING",
    )
    assert isinstance(event_id, str)


@pytest.mark.asyncio
async def test_get_recent_returns_recorded_events(memory):
    await memory.record("type1", "action1")
    await memory.record("type2", "action2")
    recent = await memory.get_recent(limit=10)
    assert len(recent) == 2


@pytest.mark.asyncio
async def test_get_recent_respects_limit(memory):
    for i in range(5):
        await memory.record("type", f"action{i}")
    recent = await memory.get_recent(limit=3)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_get_recent_orders_by_timestamp_desc(memory):
    await memory.record("type", "first")
    time.sleep(0.01)
    await memory.record("type", "second")
    recent = await memory.get_recent(limit=10)
    assert recent[0]["action"] == "second"
    assert recent[1]["action"] == "first"


@pytest.mark.asyncio
async def test_get_thread_by_ref_id(memory):
    ref = "thread-1"
    await memory.record("type", "step1", ref_id=ref)
    await memory.record("type", "step2", ref_id=ref)
    await memory.record("type", "other", ref_id="other")
    thread = await memory.get_thread(ref)
    assert len(thread) == 2


@pytest.mark.asyncio
async def test_get_thread_orders_by_timestamp_asc(memory):
    ref = "thread-2"
    await memory.record("type", "first", ref_id=ref)
    time.sleep(0.01)
    await memory.record("type", "second", ref_id=ref)
    thread = await memory.get_thread(ref)
    assert thread[0]["action"] == "first"
    assert thread[1]["action"] == "second"


@pytest.mark.asyncio
async def test_get_pending_returns_pending_and_running(memory):
    await memory.record("type", "running", status="RUNNING")
    await memory.record("type", "pending", status="PENDING")
    await memory.record("type", "done", status="COMPLETED")
    pending = await memory.get_pending()
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_get_recent_empty_db(memory):
    recent = await memory.get_recent(limit=10)
    assert recent == []


@pytest.mark.asyncio
async def test_get_pending_empty_db(memory):
    pending = await memory.get_pending()
    assert pending == []


@pytest.mark.asyncio
async def test_get_thread_no_matches(memory):
    thread = await memory.get_thread("nonexistent")
    assert thread == []
