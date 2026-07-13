import unittest
from pathlib import Path

from vox.agents.memory import VOXAgentMemory


class TestVOXAgentMemory(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_memory_{id(self)}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.memory = VOXAgentMemory(self.tmp)

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_init_creates_db(self):
        db_path = self.tmp / "memory" / "logs.db"
        self.assertTrue(db_path.exists())

    def test_record_returns_event_id(self):
        event_id = self.memory.record("test_event", "test_action")
        self.assertIsInstance(event_id, str)
        self.assertTrue(len(event_id) > 0)

    def test_record_with_all_fields(self):
        event_id = self.memory.record(
            event_type="cmd",
            action="run",
            actor="tina",
            details={"key": "value"},
            ref_id="ref-1",
            status="PENDING",
        )
        self.assertIsInstance(event_id, str)

    def test_get_recent_returns_recorded_events(self):
        self.memory.record("type1", "action1")
        self.memory.record("type2", "action2")
        recent = self.memory.get_recent(limit=10)
        self.assertEqual(len(recent), 2)

    def test_get_recent_respects_limit(self):
        for i in range(5):
            self.memory.record("type", f"action{i}")
        recent = self.memory.get_recent(limit=3)
        self.assertEqual(len(recent), 3)

    def test_get_recent_orders_by_timestamp_desc(self):
        e1 = self.memory.record("type", "first")
        import time
        time.sleep(0.01)
        e2 = self.memory.record("type", "second")
        recent = self.memory.get_recent(limit=10)
        self.assertEqual(recent[0]["action"], "second")
        self.assertEqual(recent[1]["action"], "first")

    def test_get_thread_by_ref_id(self):
        ref = "thread-1"
        self.memory.record("type", "step1", ref_id=ref)
        self.memory.record("type", "step2", ref_id=ref)
        self.memory.record("type", "other", ref_id="other")
        thread = self.memory.get_thread(ref)
        self.assertEqual(len(thread), 2)

    def test_get_thread_orders_by_timestamp_asc(self):
        ref = "thread-2"
        self.memory.record("type", "first", ref_id=ref)
        import time
        time.sleep(0.01)
        self.memory.record("type", "second", ref_id=ref)
        thread = self.memory.get_thread(ref)
        self.assertEqual(thread[0]["action"], "first")
        self.assertEqual(thread[1]["action"], "second")

    def test_get_pending_returns_pending_and_running(self):
        self.memory.record("type", "running", status="RUNNING")
        self.memory.record("type", "pending", status="PENDING")
        self.memory.record("type", "done", status="COMPLETED")
        pending = self.memory.get_pending()
        self.assertEqual(len(pending), 2)

    def test_get_recent_empty_db(self):
        recent = self.memory.get_recent(limit=10)
        self.assertEqual(recent, [])

    def test_get_pending_empty_db(self):
        pending = self.memory.get_pending()
        self.assertEqual(pending, [])

    def test_get_thread_no_matches(self):
        thread = self.memory.get_thread("nonexistent")
        self.assertEqual(thread, [])
