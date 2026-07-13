import asyncio
import sqlite3
import unittest
from pathlib import Path

from vox.capabilities.ai.llm.rag import RAGRetriever


class TestRAGRetriever(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_rag_{id(self)}.db"
        if self.tmp.exists():
            self.tmp.unlink()
        self._setup_db()
        self.retriever = RAGRetriever(max_snippets=5)

    def tearDown(self):
        if self.tmp.exists():
            self.tmp.unlink()

    def _setup_db(self):
        with sqlite3.connect(self.tmp) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_log (
                    id TEXT PRIMARY KEY,
                    details TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_index (
                    name TEXT,
                    tags TEXT
                )
            """)
            conn.execute('INSERT INTO activity_log (id, details) VALUES (?, ?)', ('1', '{"msg": "hello world"}'))
            conn.execute("INSERT INTO asset_index (name, tags) VALUES ('report.pdf', '[\"important\"]')")

    def test_retrieve_from_empty_db(self):
        empty = Path("/tmp") / f"empty_rag_{id(self)}.db"
        try:
            result = asyncio.run(self.retriever.retrieve(empty, "test"))
            self.assertEqual(result, [])
        finally:
            if empty.exists():
                empty.unlink()

    def test_retrieve_with_query(self):
        result = asyncio.run(self.retriever.retrieve(self.tmp, "hello"))
        self.assertIsInstance(result, list)

    def test_retrieve_no_match_returns_empty_list(self):
        result = asyncio.run(self.retriever.retrieve(self.tmp, "zzzzzzz"))
        self.assertEqual(result, [])

    def test_close_is_noop(self):
        asyncio.run(self.retriever.close())

    def test_tokenize_short_words_excluded(self):
        tokens = self.retriever._tokenize("a be cat dog")
        self.assertNotIn("a", tokens)
        self.assertNotIn("be", tokens)
        self.assertIn("cat", tokens)
        self.assertIn("dog", tokens)

    def test_tokenize_punctuation_stripped(self):
        tokens = self.retriever._tokenize("hello, world!")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
