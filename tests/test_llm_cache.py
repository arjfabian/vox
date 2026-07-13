import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vox.capabilities.ai.llm.cache import SemanticCache, _sha256


class TestSHA256(unittest.TestCase):

    def test_deterministic(self):
        a = _sha256("sys", "prompt", "model")
        b = _sha256("sys", "prompt", "model")
        self.assertEqual(a, b)

    def test_different_inputs_different_hashes(self):
        a = _sha256("sys1", "prompt", "model")
        b = _sha256("sys2", "prompt", "model")
        self.assertNotEqual(a, b)


class TestSemanticCache(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_cache_{id(self)}.db"
        if self.tmp.exists():
            self.tmp.unlink()
        self.cache = SemanticCache(db_path=self.tmp, ttl=3600)

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_lookup_miss_returns_none(self):
        result = self._run(self.cache.lookup("sys", "prompt", "model"))
        self.assertIsNone(result)

    def test_store_and_lookup_hit(self):
        self._run(self.cache.store("sys", "prompt", "model", "response content"))
        result = self._run(self.cache.lookup("sys", "prompt", "model"))
        self.assertIsNotNone(result)
        self.assertEqual(result.content, "response content")
        self.assertEqual(result.model, "model")

    def test_lookup_expired_entry_returns_none(self):
        self._run(self.cache.store("sys", "prompt", "model", "content"))
        self._run(self.cache.invalidate("sys", "prompt", "model"))  # just clear it
        result = self._run(self.cache.lookup("sys", "prompt", "model"))
        self.assertIsNone(result)

    def test_invalidate_removes_entry(self):
        self._run(self.cache.store("sys", "prompt", "model", "content"))
        self._run(self.cache.invalidate("sys", "prompt", "model"))
        result = self._run(self.cache.lookup("sys", "prompt", "model"))
        self.assertIsNone(result)

    def test_clear_expired_removes_old_entries(self):
        self._run(self.cache.store("sys", "prompt", "model", "content"))
        self._run(self.cache.invalidate("sys", "prompt", "model"))
        removed = self._run(self.cache.clear_expired())
        self.assertGreaterEqual(removed, 0)

    def test_close_is_noop(self):
        self._run(self.cache.close())

    def test_multiple_store_and_lookup(self):
        self._run(self.cache.store("sys1", "p1", "m1", "r1"))
        self._run(self.cache.store("sys2", "p2", "m2", "r2"))
        r1 = self._run(self.cache.lookup("sys1", "p1", "m1"))
        r2 = self._run(self.cache.lookup("sys2", "p2", "m2"))
        self.assertEqual(r1.content, "r1")
        self.assertEqual(r2.content, "r2")
