import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from vox.workloads.store import VOXWorkloadStore


@pytest.fixture
async def store(tmp_path):
    s = VOXWorkloadStore(tmp_path)
    await s.init_db()
    yield s


# ---------------------------------------------------------------------------
# Basic store operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_creates_dirs_and_db(tmp_path):
    s = VOXWorkloadStore(tmp_path)
    assert (tmp_path / "assets").exists()
    assert not (tmp_path / "memory" / "memory.db").exists()
    await s.init_db()
    assert (tmp_path / "memory" / "memory.db").exists()


@pytest.mark.asyncio
async def test_create_table(store):
    ok = await store.create_table(
        "CREATE TABLE IF NOT EXISTS test_table (id INT PRIMARY KEY)"
    )
    assert ok


@pytest.mark.asyncio
async def test_create_table_invalid_ddl(store):
    ok = await store.create_table("NOT VALID SQL")
    assert not ok


@pytest.mark.asyncio
async def test_execute_insert_and_query(store):
    await store.create_table("CREATE TABLE IF NOT EXISTS kv (k TEXT, v TEXT)")
    await store.execute("INSERT INTO kv VALUES (?, ?)", ("key1", "val1"))
    rows = await store.query("SELECT * FROM kv")
    assert len(rows) == 1
    assert rows[0]["k"] == "key1"
    assert rows[0]["v"] == "val1"


@pytest.mark.asyncio
async def test_execute_invalid_sql(store):
    ok = await store.execute("NOT VALID SQL")
    assert not ok


@pytest.mark.asyncio
async def test_query_invalid_sql_returns_error(store):
    rows = await store.query("NOT VALID SQL")
    assert "error" in rows[0]


@pytest.mark.asyncio
async def test_store_and_retrieve_file(store):
    data = b"hello world"
    result = await store.store_file(data, "test.txt", "test", "text")
    assert result is not None
    assert result["name"] == "test.txt"
    assert result["file_size"] == len(data)

    retrieved = await store.retrieve_file(result["id"])
    assert retrieved == data


@pytest.mark.asyncio
async def test_store_duplicate_returns_existing(store):
    data = b"dedup content"
    first = await store.store_file(data, "a.txt", "test", "text")
    second = await store.store_file(data, "b.txt", "test", "text")
    assert second["duplicate"]
    assert first["id"] == second["id"]


@pytest.mark.asyncio
async def test_retrieve_nonexistent(store):
    result = await store.retrieve_file("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_search_files_by_type(store):
    await store.store_file(b"data1", "a.txt", "test", "text")
    await store.store_file(b"data2", "b.png", "test", "image")
    results = await store.search_files(asset_type="text")
    assert len(results) == 1
    assert results[0]["name"] == "a.txt"


@pytest.mark.asyncio
async def test_search_files_by_name(store):
    await store.store_file(b"data1", "report.txt", "test", "text")
    await store.store_file(b"data2", "notes.txt", "test", "text")
    results = await store.search_files(name="report")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_delete_file(store):
    result = await store.store_file(b"data", "del.txt", "test", "text")
    ok = await store.delete_file(result["id"])
    assert ok
    retrieved = await store.retrieve_file(result["id"])
    assert retrieved is None


@pytest.mark.asyncio
async def test_delete_nonexistent(store):
    ok = await store.delete_file("nonexistent-id")
    assert not ok


# ---------------------------------------------------------------------------
# Mock-based error-path tests
# ---------------------------------------------------------------------------


class _RaisingResult:
    """Mimics aiosqlite.context.Result but raises on async with entry or await."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        pass

    def __await__(self):
        return self._await_impl().__await__()

    async def _await_impl(self):
        raise self._exc


def _mock_cursor(fetchone_return=None):
    """Build an AsyncMock cursor whose fetchone returns the given value."""
    c = AsyncMock()
    c.fetchone.return_value = fetchone_return
    c.fetchall.return_value = []
    return c


@pytest.mark.asyncio
async def test_store_file_returns_none_on_error(tmp_path):
    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    mock_cursor = _mock_cursor(fetchone_return=None)
    ok_result = AsyncMock()
    ok_result.__aenter__.return_value = mock_cursor

    mock_conn = MagicMock()
    mock_conn.row_factory = None
    mock_conn.execute.side_effect = [
        ok_result,
        _RaisingResult(Exception("db error")),
    ]

    with patch(
        "vox.workloads.store.aiosqlite.connect", new_callable=MagicMock
    ) as mock_connect:
        mock_connect.return_value.__aenter__.return_value = mock_conn
        result = await store.store_file(b"data", "f.txt", "test", "text")
        assert result is None


@pytest.mark.asyncio
async def test_store_file_insert_failure_cleans_up_file(tmp_path):
    """When the INSERT after write_bytes fails, the file is removed and None returned."""
    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    assets_dir = tmp_path / "assets"
    before_files = set(os.listdir(assets_dir))

    mock_cursor = _mock_cursor(fetchone_return=None)
    ok_result = AsyncMock()
    ok_result.__aenter__.return_value = mock_cursor

    mock_conn = MagicMock()
    mock_conn.row_factory = None
    mock_conn.execute.side_effect = [
        ok_result,
        _RaisingResult(Exception("simulated INSERT failure")),
    ]

    with patch(
        "vox.workloads.store.aiosqlite.connect", new_callable=MagicMock
    ) as mock_connect:
        mock_connect.return_value.__aenter__.return_value = mock_conn
        result = await store.store_file(b"orphan-check", "orphan.txt", "test", "text")
        assert result is None

    after_files = set(os.listdir(assets_dir))
    assert before_files == after_files, (
        "No new file should remain on disk after a failed insert"
    )


@pytest.mark.asyncio
async def test_delete_file_failure_leaves_file_and_row(tmp_path):
    """When the DELETE fails, the file stays on disk and the row remains."""
    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    # First store a file normally
    result = await store.store_file(b"will-not-delete", "keep.txt", "test", "text")
    assert result is not None
    asset_id = result["id"]
    path = tmp_path / "assets" / result["file_path"]
    assert path.exists(), "File should exist before delete"

    mock_cursor = _mock_cursor(fetchone_return=None)
    ok_result = AsyncMock()
    ok_result.__aenter__.return_value = mock_cursor

    mock_conn = MagicMock()
    mock_conn.row_factory = None
    mock_conn.execute.side_effect = [
        ok_result,
        _RaisingResult(Exception("simulated DELETE failure")),
    ]

    with patch(
        "vox.workloads.store.aiosqlite.connect", new_callable=MagicMock
    ) as mock_connect:
        mock_connect.return_value.__aenter__.return_value = mock_conn
        ok = await store.delete_file(asset_id)
        assert not ok, "delete_file should return False on DB failure"

    assert path.exists(), "File should remain on disk when DELETE fails"
    rows = await store.query("SELECT id FROM asset_index WHERE id = ?", (asset_id,))
    assert len(rows) == 1, "Asset index row should remain when DELETE fails"


# ---------------------------------------------------------------------------
# Concurrency test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_file_concurrent_same_content_no_duplicate_rows(store):
    """Two concurrent calls with identical content never create two rows."""
    data = b"concurrent dedup"
    pool_size = 4

    async def store_it():
        return await store.store_file(data, "same.txt", "test", "text")

    results = await asyncio.gather(*[store_it() for _ in range(pool_size)])

    results_none = [r for r in results if r is None]
    results_dup = [r for r in results if r is not None and r.get("duplicate")]
    results_primary = [r for r in results if r is not None and not r.get("duplicate")]

    assert len(results_none) == 0, "No call should return None from a non-DB error"
    assert len(results_primary) == 1, (
        "Exactly one call should be the primary (non-duplicate)"
    )
    assert len(results_dup) == pool_size - 1, (
        "All other calls should return duplicate=True"
    )
    primary_id = results_primary[0]["id"]
    for r in results_dup:
        assert r["id"] == primary_id, (
            "Duplicate entries must reference the same asset_id"
        )

    rows = await store.query("SELECT COUNT(*) AS cnt FROM asset_index")
    assert rows[0]["cnt"] == 1, "Only one row should exist in asset_index"


# ---------------------------------------------------------------------------
# Dedup (init_db) tests
# ---------------------------------------------------------------------------


@pytest.fixture
def dedup_env(tmp_path):
    """Set up a directory with pre-populated duplicate rows (sync sqlite3)."""
    db_path = tmp_path / "memory" / "memory.db"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return tmp_path, db_path, assets_dir


def _populate_duplicates(db_path, assets_dir, checksum, now):
    """Insert duplicate rows and create asset files using synchronous sqlite3."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)")
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "keep-uuid",
                now - 10,
                "original.txt",
                "text",
                "test",
                "original.txt",
                4,
                checksum,
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "remove-uuid",
                now,
                "duplicate.txt",
                "text",
                "test",
                "duplicate.txt",
                4,
                checksum,
                "[]",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    (assets_dir / "original.txt").write_text("data")
    (assets_dir / "duplicate.txt").write_text("data")


@pytest.mark.asyncio
async def test_dedup_removes_duplicate_rows_and_files(dedup_env):
    """Constructing VOXWorkloadStore over a DB with duplicate checksums succeeds
    and cleans up: exactly one row per checksum, orphaned files removed."""
    tmp_path, db_path, assets_dir = dedup_env
    checksum = "dedup-test-" + "a" * 55
    now = 1234567890.0
    _populate_duplicates(db_path, assets_dir, checksum, now)

    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    rows = await store.query(
        "SELECT id, file_path, archived_at FROM asset_index WHERE checksum = ?",
        (checksum,),
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "keep-uuid", "Earliest archived_at row should survive"
    assert (assets_dir / "original.txt").exists(), "Survivor file must exist"
    assert not (assets_dir / "duplicate.txt").exists(), (
        "Removed row's file must be deleted"
    )

    # Verify the unique index now exists by attempting a direct duplicate
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "new-uuid",
                    time.time(),
                    "new.txt",
                    "text",
                    "test",
                    "new.txt",
                    4,
                    checksum,
                    "[]",
                ),
            )
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_dedup_idempotent(dedup_env):
    """Re-running init_db (via second VOXWorkloadStore construction) after
    cleanup must not error and must not change state."""
    tmp_path, db_path, assets_dir = dedup_env
    checksum = "idempotent-" + "b" * 53
    now = 1234567890.0
    _populate_duplicates(db_path, assets_dir, checksum, now)

    store1 = VOXWorkloadStore(tmp_path)
    await store1.init_db()

    rows1 = await store1.query(
        "SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,)
    )
    survivor_id_1 = rows1[0]["id"]
    survivor_path_1 = assets_dir / rows1[0]["file_path"]
    assert survivor_path_1.exists()

    # Second construction — must be a no-op
    store2 = VOXWorkloadStore(tmp_path)
    await store2.init_db()

    rows2 = await store2.query(
        "SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,)
    )
    assert len(rows2) == 1
    assert rows2[0]["id"] == survivor_id_1, "Same row must survive after second init"
    assert (assets_dir / rows2[0]["file_path"]).exists(), (
        "Survivor file must still exist"
    )
    assert not (assets_dir / "duplicate.txt").exists(), "Removed file must stay removed"


@pytest.mark.asyncio
async def test_init_no_duplicates(dedup_env):
    """A store without duplicate checksums initialises normally —
    no extra rows/files touched, index created."""
    tmp_path, _db_path, _assets_dir = dedup_env
    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    data = b"clean-init-data"
    first = await store.store_file(data, "a.txt", "test", "text")
    assert first is not None
    second = await store.store_file(data, "b.txt", "test", "text")
    assert second is not None
    assert second.get("duplicate")

    rows = await store.query("SELECT COUNT(*) AS cnt FROM asset_index")
    assert rows[0]["cnt"] == 1


# ---------------------------------------------------------------------------
# Error-path dedup tests (mock aiosqlite.connect to inject DELETE failure)
# ---------------------------------------------------------------------------


_real_aiosqlite_connect = aiosqlite.connect


class _FailingAsyncConnection:
    """Wraps a real aiosqlite connection but fails on a specific DELETE."""

    def __init__(self, real, fail_id):
        self._real = real
        self._fail_id = fail_id

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._real.row_factory = value

    def execute(self, sql, params=None):
        if (
            self._fail_id is not None
            and isinstance(sql, str)
            and sql.strip().upper().startswith("DELETE")
            and params is not None
            and len(params) > 0
            and params[0] == self._fail_id
        ):
            return _RaisingResult(Exception("Simulated DELETE failure"))
        return self._real.execute(sql, params)


class _FailingConnectProxy:
    """Replaces aiosqlite.connect; returns a _FailingAsyncConnection."""

    def __init__(self, db_path, fail_id):
        self._db_path = db_path
        self._fail_id = fail_id

    async def __aenter__(self):
        real = await _real_aiosqlite_connect(self._db_path)
        real.row_factory = aiosqlite.Row
        self._conn = _FailingAsyncConnection(real, self._fail_id)
        return self._conn

    async def __aexit__(self, *args):
        await self._conn._real.close()


@pytest.mark.asyncio
async def test_dedup_ordering_delete_failure_does_not_orphan_files(dedup_env):
    """When a DELETE in _dedup_checksums raises, the transaction rolls back
    and no files are orphaned — because unlink runs only after a successful
    DELETE (the ordering fix)."""
    tmp_path, db_path, assets_dir = dedup_env
    checksum = "ordering-" + "c" * 55
    now = 1234567890.0
    _populate_duplicates(db_path, assets_dir, checksum, now)

    proxy = _FailingConnectProxy(db_path, "remove-uuid")

    with (
        patch("vox.workloads.store.aiosqlite.connect", return_value=proxy),
        pytest.raises(Exception),  # noqa: B017 — proxy raises arbitrary errors by design
    ):
        store = VOXWorkloadStore(tmp_path)
        await store.init_db()

    # Verify state with a fresh (real) connection
    import sqlite3

    check = sqlite3.connect(db_path)
    try:
        check.row_factory = sqlite3.Row
        rows = check.execute(
            "SELECT id, file_path FROM asset_index WHERE checksum = ?", (checksum,)
        ).fetchall()
        assert len(rows) == 2, "Transaction rolled back: both rows should still exist"
        for row in rows:
            fp = assets_dir / row["file_path"]
            assert fp.exists(), f"File {row['file_path']} must exist after rollback"
    finally:
        check.close()


@pytest.mark.asyncio
async def test_dedup_two_groups_atomic_rollback(dedup_env):
    """With two duplicate-checksum groups, if the second group's DELETE
    raises, the SQL transaction rolls back atomically (first group's DELETE
    DELETE undone) but file unlink is NOT transactional.
    VOXWorkloadStore construction self-heals: re-processes the stale group
    and converges to 1 row per checksum."""
    tmp_path, db_path, assets_dir = dedup_env
    checksum1 = "two-group-a-" + "x" * 52
    checksum2 = "two-group-b-" + "y" * 52
    now = 1234567890.0

    import sqlite3

    # Populate two duplicate groups using raw sqlite3
    conn = sqlite3.connect(db_path)
    try:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)")
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "keep-uuid-1",
                now,
                "g1_o.txt",
                "text",
                "test",
                "g1_o.txt",
                4,
                checksum1,
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "remove-uuid-1",
                now + 60,
                "g1_d.txt",
                "text",
                "test",
                "g1_d.txt",
                4,
                checksum1,
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "keep-uuid-2",
                now + 120,
                "g2_o.txt",
                "text",
                "test",
                "g2_o.txt",
                4,
                checksum2,
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "remove-uuid-2",
                now + 180,
                "g2_d.txt",
                "text",
                "test",
                "g2_d.txt",
                4,
                checksum2,
                "[]",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    (assets_dir / "g1_o.txt").write_text("data")
    (assets_dir / "g1_d.txt").write_text("data")
    (assets_dir / "g2_o.txt").write_text("data")
    (assets_dir / "g2_d.txt").write_text("data")

    proxy = _FailingConnectProxy(db_path, "remove-uuid-2")

    with (
        patch("vox.workloads.store.aiosqlite.connect", return_value=proxy),
        pytest.raises(Exception),  # noqa: B017 — proxy raises arbitrary errors by design
    ):
        store = VOXWorkloadStore(tmp_path)
        await store.init_db()

    # Verify with a fresh connection
    check = sqlite3.connect(db_path)
    try:
        check.row_factory = sqlite3.Row
        rows1 = check.execute(
            "SELECT id, file_path FROM asset_index WHERE checksum = ? ORDER BY archived_at",
            (checksum1,),
        ).fetchall()
        rows2 = check.execute(
            "SELECT id, file_path FROM asset_index WHERE checksum = ? ORDER BY archived_at",
            (checksum2,),
        ).fetchall()
    finally:
        check.close()

    # Group 1: DELETE was rolled back by the SQL transaction — both rows exist.
    assert len(rows1) == 2, (
        "Group 1: both rows exist (SQL transaction rolled back the DELETE)"
    )
    assert (assets_dir / "g1_o.txt").exists(), "Group 1 survivor file exists"
    assert not (assets_dir / "g1_d.txt").exists(), (
        "Group 1 removed-row file is gone (unlink not transactional)"
    )

    # Group 2: DELETE never succeeded; both rows and files intact
    assert len(rows2) == 2, "Group 2: both rows exist (never processed)"
    assert (assets_dir / "g2_o.txt").exists(), "Group 2 survivor file exists"
    assert (assets_dir / "g2_d.txt").exists(), "Group 2 removed-row file exists"

    # --- Self-healing: re-init over the same persona_dir ---
    store = VOXWorkloadStore(tmp_path)
    await store.init_db()

    rows1 = await store.query(
        "SELECT id FROM asset_index WHERE checksum = ?",
        (checksum1,),
    )
    assert len(rows1) == 1, "Group 1 converges to 1 row after re-init"
    rows2 = await store.query(
        "SELECT id FROM asset_index WHERE checksum = ?",
        (checksum2,),
    )
    assert len(rows2) == 1, "Group 2 converges to 1 row after re-init"

    assert (assets_dir / "g1_o.txt").exists(), (
        "Group 1 survivor file still exists after re-init"
    )
    assert (assets_dir / "g2_o.txt").exists(), (
        "Group 2 survivor file exists after re-init"
    )
