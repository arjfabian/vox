import concurrent.futures
import os
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from vox.agents.store import VOXAgentStore


class TestVOXAgentStore(unittest.TestCase):

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_store_{id(self)}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.store = VOXAgentStore(self.tmp)

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_init_creates_dirs_and_db(self):
        self.assertTrue((self.tmp / "assets").exists())
        self.assertTrue((self.tmp / "memory" / "memory.db").exists())

    def test_create_table(self):
        ok = self.store.create_table(
            "CREATE TABLE IF NOT EXISTS test_table (id INT PRIMARY KEY)"
        )
        self.assertTrue(ok)

    def test_create_table_invalid_ddl(self):
        ok = self.store.create_table("NOT VALID SQL")
        self.assertFalse(ok)

    def test_execute_insert_and_query(self):
        self.store.create_table("CREATE TABLE IF NOT EXISTS kv (k TEXT, v TEXT)")
        self.store.execute("INSERT INTO kv VALUES (?, ?)", ("key1", "val1"))
        rows = self.store.query("SELECT * FROM kv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["k"], "key1")
        self.assertEqual(rows[0]["v"], "val1")

    def test_execute_invalid_sql(self):
        ok = self.store.execute("NOT VALID SQL")
        self.assertFalse(ok)

    def test_query_invalid_sql_returns_error(self):
        rows = self.store.query("NOT VALID SQL")
        self.assertIn("error", rows[0])

    def test_store_and_retrieve_file(self):
        data = b"hello world"
        result = self.store.store_file(data, "test.txt", "test", "text")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "test.txt")
        self.assertEqual(result["file_size"], len(data))

        retrieved = self.store.retrieve_file(result["id"])
        self.assertEqual(retrieved, data)

    def test_store_duplicate_returns_existing(self):
        data = b"dedup content"
        first = self.store.store_file(data, "a.txt", "test", "text")
        second = self.store.store_file(data, "b.txt", "test", "text")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])

    def test_retrieve_nonexistent(self):
        result = self.store.retrieve_file("nonexistent-id")
        self.assertIsNone(result)

    def test_search_files_by_type(self):
        self.store.store_file(b"data1", "a.txt", "test", "text")
        self.store.store_file(b"data2", "b.png", "test", "image")
        results = self.store.search_files(asset_type="text")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "a.txt")

    def test_search_files_by_name(self):
        self.store.store_file(b"data1", "report.txt", "test", "text")
        self.store.store_file(b"data2", "notes.txt", "test", "text")
        results = self.store.search_files(name="report")
        self.assertEqual(len(results), 1)

    def test_delete_file(self):
        result = self.store.store_file(b"data", "del.txt", "test", "text")
        ok = self.store.delete_file(result["id"])
        self.assertTrue(ok)
        retrieved = self.store.retrieve_file(result["id"])
        self.assertIsNone(retrieved)

    def test_delete_nonexistent(self):
        ok = self.store.delete_file("nonexistent-id")
        self.assertFalse(ok)

    def test_store_file_returns_none_on_error(self):
        with patch("vox.agents.store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchone=lambda: None),
                Exception("db error"),
            ]
            result = self.store.store_file(b"data", "f.txt", "test", "text")
            self.assertIsNone(result)

    def test_store_file_concurrent_same_content_no_duplicate_rows(self):
        """Two concurrent calls with identical content never create two rows."""
        data = b"concurrent dedup"
        pool_size = 4

        def store():
            return self.store.store_file(data, "same.txt", "test", "text")

        with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as pool:
            futures = [pool.submit(store) for _ in range(pool_size)]
            results = [f.result() for f in futures]

        results_none = [r for r in results if r is None]
        results_dup = [r for r in results if r is not None and r.get("duplicate")]
        results_primary = [r for r in results if r is not None and not r.get("duplicate")]

        self.assertEqual(len(results_none), 0,
                         "No call should return None from a non-DB error")
        self.assertEqual(len(results_primary), 1,
                         "Exactly one call should be the primary (non-duplicate)")
        self.assertEqual(len(results_dup), pool_size - 1,
                         "All other calls should return duplicate=True")
        primary_id = results_primary[0]["id"]
        for r in results_dup:
            self.assertEqual(r["id"], primary_id,
                             "Duplicate entries must reference the same asset_id")

        rows = self.store.query("SELECT COUNT(*) AS cnt FROM asset_index")
        self.assertEqual(rows[0]["cnt"], 1,
                         "Only one row should exist in asset_index")

    def test_store_file_insert_failure_cleans_up_file(self):
        """When the INSERT after write_bytes fails, the file is removed and None returned."""
        assets_dir = self.tmp / "assets"
        before_files = set(os.listdir(assets_dir))

        with patch("vox.agents.store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchone=lambda: None),
                Exception("simulated INSERT failure"),
            ]
            result = self.store.store_file(b"orphan-check", "orphan.txt", "test", "text")
            self.assertIsNone(result)

        after_files = set(os.listdir(assets_dir))
        self.assertEqual(before_files, after_files,
                         "No new file should remain on disk after a failed insert")

    def test_delete_file_failure_leaves_file_and_row(self):
        """When the DELETE fails, the file stays on disk and the row remains."""
        result = self.store.store_file(b"will-not-delete", "keep.txt", "test", "text")
        self.assertIsNotNone(result)
        asset_id = result["id"]
        path = self.tmp / "assets" / result["file_path"]
        self.assertTrue(path.exists(), "File should exist before delete")

        with patch("vox.agents.store.sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchone=lambda: None),
                Exception("simulated DELETE failure"),
            ]
            ok = self.store.delete_file(asset_id)
            self.assertFalse(ok, "delete_file should return False on DB failure")

        self.assertTrue(path.exists(),
                        "File should remain on disk when DELETE fails")
        rows = self.store.query("SELECT id FROM asset_index WHERE id = ?", (asset_id,))
        self.assertEqual(len(rows), 1,
                         "Asset index row should remain when DELETE fails")


class TestVOXAgentStoreInitDedup(unittest.TestCase):
    """Tests for _init_db() duplicate-checksum detection and cleanup."""

    def setUp(self):
        self.tmp = Path("/tmp") / f"test_store_dedup_{id(self)}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.db_path = self.tmp / "memory" / "memory.db"
        self.assets_dir = self.tmp / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def _populate_schema_and_duplicates(self, checksum: str, now: float):
        """Create schema and insert duplicate rows without using VOXAgentStore."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_index (
                    id TEXT PRIMARY KEY,
                    archived_at REAL NOT NULL,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_size INT NOT NULL,
                    checksum TEXT NOT NULL,
                    last_accessed REAL,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checksum ON asset_index(checksum)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)")
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("keep-uuid", now - 10, "original.txt", "text", "test", "original.txt", 4, checksum, "[]"),
            )
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("remove-uuid", now, "duplicate.txt", "text", "test", "duplicate.txt", 4, checksum, "[]"),
            )
        (self.assets_dir / "original.txt").write_text("data")
        (self.assets_dir / "duplicate.txt").write_text("data")

    def test_dedup_removes_duplicate_rows_and_files(self):
        """Constructing VOXAgentStore over a DB with duplicate checksums succeeds
        and cleans up: exactly one row per checksum, orphaned files removed."""
        checksum = "dedup-test-" + "a" * 55
        now = 1234567890.0
        self._populate_schema_and_duplicates(checksum, now)

        # Construction must not raise (this was the bug: IntegrityError on index creation)
        store = VOXAgentStore(self.tmp)

        rows = store.query("SELECT id, file_path, archived_at FROM asset_index WHERE checksum = ?", (checksum,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "keep-uuid",
                         "Earliest archived_at row should survive")
        self.assertTrue((self.assets_dir / "original.txt").exists(),
                        "Survivor file must exist")
        self.assertFalse((self.assets_dir / "duplicate.txt").exists(),
                         "Removed row's file must be deleted")

        # Verify the unique index now exists by attempting a direct duplicate
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("new-uuid", time.time(), "new.txt", "text", "test", "new.txt", 4, checksum, "[]"),
                )

    def test_dedup_idempotent(self):
        """Re-running _init_db (via second VOXAgentStore construction) after
        cleanup must not error and must not change state."""
        checksum = "idempotent-" + "b" * 53
        now = 1234567890.0
        self._populate_schema_and_duplicates(checksum, now)

        store1 = VOXAgentStore(self.tmp)

        # Capture state after first pass
        rows1 = store1.query("SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,))
        survivor_id_1 = rows1[0]["id"]
        survivor_path_1 = self.assets_dir / rows1[0]["file_path"]
        self.assertTrue(survivor_path_1.exists())

        # Second construction — must be a no-op
        store2 = VOXAgentStore(self.tmp)

        rows2 = store2.query("SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,))
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["id"], survivor_id_1,
                         "Same row must survive after second init")
        self.assertTrue((self.assets_dir / rows2[0]["file_path"]).exists(),
                        "Survivor file must still exist")
        self.assertFalse((self.assets_dir / "duplicate.txt").exists(),
                         "Removed file must stay removed")

    def test_init_no_duplicates(self):
        """A store without duplicate checksums initialises normally —
        no extra rows/files touched, index created."""
        store = VOXAgentStore(self.tmp)
        data = b"clean-init-data"
        first = store.store_file(data, "a.txt", "test", "text")
        self.assertIsNotNone(first)
        second = store.store_file(data, "b.txt", "test", "text")
        self.assertIsNotNone(second)
        self.assertTrue(second.get("duplicate"))

        rows = store.query("SELECT COUNT(*) AS cnt FROM asset_index")
        self.assertEqual(rows[0]["cnt"], 1)

    def test_dedup_ordering_delete_failure_does_not_orphan_files(self):
        """When a DELETE in _dedup_checksums raises, the transaction rolls back
        and no files are orphaned — because unlink runs only after a successful
        DELETE (the ordering fix)."""
        checksum = "ordering-" + "c" * 55
        now = 1234567890.0
        self._populate_schema_and_duplicates(checksum, now)

        class _FailingDeleteProxy:
            def __init__(self, real, fail_id):
                self._real = real
                self._fail_id = fail_id

            def execute(self, sql, params=None):
                if (self._fail_id is not None
                        and isinstance(sql, str)
                        and sql.strip().upper().startswith("DELETE")
                        and params is not None
                        and len(params) > 0
                        and params[0] == self._fail_id):
                    raise Exception("Simulated DELETE failure")
                if params is not None:
                    return self._real.execute(sql, params)
                return self._real.execute(sql)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._real.__exit__(exc_type, exc_val, exc_tb)

            def close(self):
                self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        proxy = _FailingDeleteProxy(sqlite3.connect(self.db_path), "remove-uuid")

        with patch("vox.agents.store.sqlite3.connect", return_value=proxy):
            with self.assertRaises(Exception):
                VOXAgentStore(self.tmp)

        # Verify state with a fresh (real) connection
        with sqlite3.connect(self.db_path) as check:
            check.row_factory = sqlite3.Row
            rows = check.execute(
                "SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,)
            ).fetchall()
            self.assertEqual(
                len(rows), 2,
                "Transaction rolled back: both rows should still exist",
            )
            for row in rows:
                fp = self.assets_dir / row["file_path"]
                self.assertTrue(
                    fp.exists(),
                    f"File {row['file_path']} must exist after rollback "
                    f"(unlink was never reached because DELETE failed first)",
                )

    def test_dedup_two_groups_atomic_rollback(self):
        """With two duplicate-checksum groups, if the second group's DELETE
        raises, the SQL transaction rolls back atomically (first group's
        DELETE undone) but file unlink is NOT transactional.  A second
        VOXAgentStore construction self-heals: re-processes the stale group
        and converges to 1 row per checksum."""
        checksum1 = "two-group-a-" + "x" * 52
        checksum2 = "two-group-b-" + "y" * 52
        now = 1234567890.0

        # Populate two duplicate groups using raw sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_index (
                    id TEXT PRIMARY KEY,
                    archived_at REAL NOT NULL,
                    name TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    file_size INT NOT NULL,
                    checksum TEXT NOT NULL,
                    last_accessed REAL,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checksum ON asset_index(checksum)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)")
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("keep-uuid-1", now, "g1_o.txt", "text", "test",
                 "g1_o.txt", 4, checksum1, "[]"),
            )
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("remove-uuid-1", now + 60, "g1_d.txt", "text", "test",
                 "g1_d.txt", 4, checksum1, "[]"),
            )
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("keep-uuid-2", now + 120, "g2_o.txt", "text", "test",
                 "g2_o.txt", 4, checksum2, "[]"),
            )
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("remove-uuid-2", now + 180, "g2_d.txt", "text", "test",
                 "g2_d.txt", 4, checksum2, "[]"),
            )
        (self.assets_dir / "g1_o.txt").write_text("data")
        (self.assets_dir / "g1_d.txt").write_text("data")
        (self.assets_dir / "g2_o.txt").write_text("data")
        (self.assets_dir / "g2_d.txt").write_text("data")

        class _FailingDeleteProxy:
            def __init__(self, real, fail_id):
                self._real = real
                self._fail_id = fail_id

            def execute(self, sql, params=None):
                if (self._fail_id is not None
                        and isinstance(sql, str)
                        and sql.strip().upper().startswith("DELETE")
                        and params is not None
                        and len(params) > 0
                        and params[0] == self._fail_id):
                    raise Exception("Simulated DELETE failure")
                if params is not None:
                    return self._real.execute(sql, params)
                return self._real.execute(sql)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._real.__exit__(exc_type, exc_val, exc_tb)

            def close(self):
                self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        proxy = _FailingDeleteProxy(sqlite3.connect(self.db_path), "remove-uuid-2")

        with patch("vox.agents.store.sqlite3.connect", return_value=proxy):
            with self.assertRaises(Exception):
                VOXAgentStore(self.tmp)

        # Verify with a fresh connection
        with sqlite3.connect(self.db_path) as check:
            check.row_factory = sqlite3.Row
            rows1 = check.execute(
                "SELECT id, file_path FROM asset_index WHERE checksum = ? ORDER BY archived_at",
                (checksum1,),
            ).fetchall()
            rows2 = check.execute(
                "SELECT id, file_path FROM asset_index WHERE checksum = ? ORDER BY archived_at",
                (checksum2,),
            ).fetchall()

        # Group 1: DELETE was rolled back by the SQL transaction — both rows
        # exist.  But the file for the removed row was already unlinked
        # (filesystem ops are not transactional), so it stays deleted.
        self.assertEqual(
            len(rows1), 2,
            "Group 1: both rows exist (SQL transaction rolled back the DELETE)",
        )
        self.assertTrue(
            (self.assets_dir / "g1_o.txt").exists(),
            "Group 1 survivor file exists",
        )
        self.assertFalse(
            (self.assets_dir / "g1_d.txt").exists(),
            "Group 1 removed-row file is gone (unlink not transactional)",
        )

        # Group 2: DELETE never succeeded; both rows and files intact
        self.assertEqual(
            len(rows2), 2,
            "Group 2: both rows exist (never processed)",
        )
        self.assertTrue(
            (self.assets_dir / "g2_o.txt").exists(),
            "Group 2 survivor file exists",
        )
        self.assertTrue(
            (self.assets_dir / "g2_d.txt").exists(),
            "Group 2 removed-row file exists",
        )

        # --- Self-healing: re-init over the same agent_dir ---
        # The second construction must not raise, and must converge both
        # groups to exactly one surviving row each.
        store = VOXAgentStore(self.tmp)

        rows1 = store.query(
            "SELECT id FROM asset_index WHERE checksum = ?", (checksum1,),
        )
        self.assertEqual(
            len(rows1), 1,
            "Group 1 converges to 1 row after re-init",
        )
        rows2 = store.query(
            "SELECT id FROM asset_index WHERE checksum = ?", (checksum2,),
        )
        self.assertEqual(
            len(rows2), 1,
            "Group 2 converges to 1 row after re-init",
        )

        # Both survivors' files must exist
        self.assertTrue(
            (self.assets_dir / "g1_o.txt").exists(),
            "Group 1 survivor file still exists after re-init",
        )
        self.assertTrue(
            (self.assets_dir / "g2_o.txt").exists(),
            "Group 2 survivor file exists after re-init",
        )
