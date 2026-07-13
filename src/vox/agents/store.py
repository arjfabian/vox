"""Private persistent workspace for VOX agents.

Each agent owns its own isolated SQLite workspace and asset sandbox.
"""

import hashlib
import json
import logging
import sqlite3
import time
import uuid

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_LIMIT = 50


class VOXAgentStore:

    def __init__(self, agent_dir: Path) -> None:
        self._agent_dir = agent_dir
        self._assets_dir = agent_dir / "assets"
        self._db_path = agent_dir / "memory" / "memory.db"
        self._assets_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checksum ON asset_index(checksum)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)")

            # If a UNIQUE index on checksum already exists, skip migration.
            existing_indexes = {
                row[2] for row in conn.execute("PRAGMA index_list(asset_index)").fetchall()
            }
            if "idx_checksum_unique" not in existing_indexes:
                self._dedup_checksums(conn)
                try:
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_checksum_unique "
                        "ON asset_index(checksum)"
                    )
                except sqlite3.OperationalError as e:
                    logger.error("Failed to create unique index on checksum: %s", e)
                    raise

    def _dedup_checksums(self, conn: sqlite3.Connection) -> None:
        """Remove duplicate-checkum rows so a UNIQUE index can be created.

        Keeps the row with the earliest *archived_at* for each checksum.
        Orphaned files on disk for removed rows are deleted.
        Safe to re-run (idempotent): already-cleaned checksums produce no rows.

        Crash between a group's DELETE and its succeeding unlink leaves a
        transient dangling DB row (file gone, row present) until the next
        VOXAgentStore() construction.  This is acceptable because
        retrieve_file() returns None on missing files and _dedup_checksums
        re-processes the group idempotently (the second unlink is a no-op
        via fp.exists()).
        """
        dup_rows = conn.execute("""
            SELECT checksum, COUNT(*) AS cnt
            FROM asset_index
            GROUP BY checksum
            HAVING cnt > 1
        """).fetchall()

        for (checksum, _cnt) in dup_rows:
            survivor = conn.execute("""
                SELECT id, file_path FROM asset_index
                WHERE checksum = ?
                ORDER BY archived_at ASC
                LIMIT 1
            """, (checksum,)).fetchone()
            if not survivor:
                continue
            keep_id, _keep_path = survivor

            to_remove = conn.execute("""
                SELECT id, file_path FROM asset_index
                WHERE checksum = ? AND id != ?
            """, (checksum, keep_id)).fetchall()

            for row_id, file_path in to_remove:
                conn.execute("DELETE FROM asset_index WHERE id = ?", (row_id,))
                fp = self._assets_dir / file_path
                try:
                    if fp.exists():
                        fp.unlink()
                except OSError:
                    logger.warning("Could not remove orphaned file %s", fp)
                logger.info(
                    "Deduplicated asset %s (checksum=%s): removed in favour of %s",
                    row_id, checksum, keep_id,
                )

    def create_table(self, ddl: str) -> bool:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(ddl)
            return True
        except Exception as e:
            logger.exception("Table creation failed: %s", e)
            return False

    def execute(self, sql: str, params: Tuple = ()) -> bool:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(sql, params)
            return True
        except Exception as e:
            logger.exception("SQL execute failed: %s", e)
            return False

    def query(self, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.exception("query failed: %s", exc)
            return [{"error": str(exc)}]

    def store_file(
        self, data: bytes, filename: str, origin: str, asset_type: str,
        tags: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        checksum = hashlib.sha256(data).hexdigest()
        path = None
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                existing = conn.execute(
                    "SELECT * FROM asset_index WHERE checksum = ?", (checksum,)
                ).fetchone()
                if existing:
                    item = dict(existing)
                    item["duplicate"] = True
                    return item

                asset_id = str(uuid.uuid4())
                stored_name = f"{asset_id}{Path(filename).suffix}"
                path = self._assets_dir / stored_name
                path.write_bytes(data)
                try:
                    conn.execute(
                        "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, last_accessed, tags) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (asset_id, time.time(), filename, asset_type, origin, stored_name,
                         len(data), checksum, None, json.dumps(tags or [])),
                    )
                except sqlite3.IntegrityError:
                    # Race lost: another connection inserted this checksum first.
                    # Clean up our file and return the existing record.
                    if path.exists():
                        path.unlink()
                    existing = conn.execute(
                        "SELECT * FROM asset_index WHERE checksum = ?", (checksum,)
                    ).fetchone()
                    if existing:
                        item = dict(existing)
                        item["duplicate"] = True
                        return item
                    raise
                conn.commit()
            return {
                "id": asset_id,
                "name": filename,
                "asset_type": asset_type,
                "origin": origin,
                "file_path": stored_name,
                "file_size": len(data),
                "checksum": checksum,
            }
        except Exception as e:
            logger.exception("Store file failed: %s", e)
            if path is not None and path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass
            return None

    def retrieve_file(self, asset_id: str) -> Optional[bytes]:
        rows = self.query("SELECT file_path FROM asset_index WHERE id = ?", (asset_id,))
        if not rows or "error" in rows[0]:
            return None
        path = self._assets_dir / rows[0]["file_path"]
        if not path.exists():
            return None
        data = path.read_bytes()
        self.execute("UPDATE asset_index SET last_accessed = ? WHERE id = ?", (time.time(), asset_id))
        return data

    def search_files(
        self, asset_type: Optional[str] = None, name: Optional[str] = None,
        tag: Optional[str] = None, limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> List[Dict[str, Any]]:
        conditions = []
        params = []
        if asset_type:
            conditions.append("asset_type = ?")
            params.append(asset_type)
        if name:
            conditions.append("name LIKE ?")
            params.append(f"%{name}%")
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self.query(f"SELECT * FROM asset_index {where} ORDER BY archived_at DESC LIMIT ?", tuple(params))
        for row in rows:
            if "tags" in row:
                row["tags"] = json.loads(row["tags"])
        return rows

    def delete_file(self, asset_id: str) -> bool:
        rows = self.query("SELECT file_path FROM asset_index WHERE id = ?", (asset_id,))
        if not rows or "error" in rows[0]:
            return False
        path = self._assets_dir / rows[0]["file_path"]
        ok = self.execute("DELETE FROM asset_index WHERE id = ?", (asset_id,))
        if ok and path.exists():
            path.unlink()
        return ok
