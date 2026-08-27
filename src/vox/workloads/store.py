"""Private persistent workspace for VOX workloads.

Each workload owns its own isolated SQLite workspace and asset sandbox.
"""

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_SEARCH_LIMIT = 50


class VOXWorkloadStore:
    def __init__(self, persona_dir: Path) -> None:
        self._persona_dir = persona_dir
        self._assets_dir = persona_dir / "assets"
        self._db_path = persona_dir / "memory" / "memory.db"
        self._assets_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        """Asynchronously initialize database, indexes and perform dedup migrations."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("""
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
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_type ON asset_index(asset_type)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checksum ON asset_index(checksum)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_name ON asset_index(name)"
            )

            # Check for unique index migration requirements
            async with conn.execute("PRAGMA index_list(asset_index)") as cursor:
                existing_indexes = {row[2] for row in await cursor.fetchall()}

            if "idx_checksum_unique" not in existing_indexes:
                await self._dedup_checksums(conn)
                try:
                    await conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_checksum_unique "
                        "ON asset_index(checksum)"
                    )
                except aiosqlite.OperationalError as e:
                    logger.error("Failed to create unique index on checksum: %s", e)
                    raise
            await conn.commit()

    async def _dedup_checksums(self, conn: aiosqlite.Connection) -> None:
        """Remove duplicate-checksum rows asynchronously."""
        async with conn.execute("""
            SELECT checksum, COUNT(*) AS cnt
            FROM asset_index
            GROUP BY checksum
            HAVING cnt > 1
        """) as cursor:
            dup_rows = await cursor.fetchall()

        for checksum, _cnt in dup_rows:
            async with conn.execute(
                """
                SELECT id, file_path FROM asset_index
                WHERE checksum = ?
                ORDER BY archived_at ASC
                LIMIT 1
            """,
                (checksum,),
            ) as cursor:
                survivor = await cursor.fetchone()

            if not survivor:
                continue
            keep_id, _keep_path = survivor

            async with conn.execute(
                """
                SELECT id, file_path FROM asset_index
                WHERE checksum = ? AND id != ?
            """,
                (checksum, keep_id),
            ) as cursor:
                to_remove = await cursor.fetchall()

            for row_id, file_path in to_remove:
                await conn.execute("DELETE FROM asset_index WHERE id = ?", (row_id,))
                fp = self._assets_dir / file_path
                try:
                    if fp.exists():
                        fp.unlink()
                except OSError:
                    logger.warning("Could not remove orphaned file %s", fp)
                logger.info(
                    "Deduplicated asset %s (checksum=%s): removed in favour of %s",
                    row_id,
                    checksum,
                    keep_id,
                )

    async def create_table(self, ddl: str) -> bool:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                await conn.execute(ddl)
                await conn.commit()
            return True
        except Exception:
            logger.exception("Table creation failed")
            return False

    async def execute(self, sql: str, params: tuple = ()) -> bool:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                await conn.execute(sql, params)
                await conn.commit()
            return True
        except Exception:
            logger.exception("SQL execute failed")
            return False

    async def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.exception("query failed")
            return [{"error": str(exc)}]

    async def store_file(
        self,
        data: bytes,
        filename: str,
        origin: str,
        asset_type: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any] | None:
        checksum = hashlib.sha256(data).hexdigest()
        path = None
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM asset_index WHERE checksum = ?", (checksum,)
                ) as cursor:
                    existing = await cursor.fetchone()

                if existing:
                    item = dict(existing)
                    item["duplicate"] = True
                    return item

                asset_id = str(uuid.uuid4())
                stored_name = f"{asset_id}{Path(filename).suffix}"
                path = self._assets_dir / stored_name
                path.write_bytes(data)

                try:
                    await conn.execute(
                        "INSERT INTO asset_index (id, archived_at, name, asset_type, origin, file_path, file_size, checksum, last_accessed, tags) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            asset_id,
                            time.time(),
                            filename,
                            asset_type,
                            origin,
                            stored_name,
                            len(data),
                            checksum,
                            None,
                            json.dumps(tags or []),
                        ),
                    )
                except aiosqlite.IntegrityError:
                    if path.exists():
                        path.unlink()
                    async with conn.execute(
                        "SELECT * FROM asset_index WHERE checksum = ?", (checksum,)
                    ) as cursor:
                        existing = await cursor.fetchone()
                    if existing:
                        item = dict(existing)
                        item["duplicate"] = True
                        return item
                    raise
                await conn.commit()
            return {
                "id": asset_id,
                "name": filename,
                "asset_type": asset_type,
                "origin": origin,
                "file_path": stored_name,
                "file_size": len(data),
                "checksum": checksum,
            }
        except Exception:
            logger.exception("Store file failed")
            if path is not None and path.exists():
                try:
                    path.unlink()
                except Exception:  # noqa: BLE001, S110 — best-effort cleanup during error path
                    pass
            return None

    async def retrieve_file(self, asset_id: str) -> bytes | None:
        rows = await self.query(
            "SELECT file_path FROM asset_index WHERE id = ?", (asset_id,)
        )
        if not rows or "error" in rows[0]:
            return None
        path = self._assets_dir / rows[0]["file_path"]
        if not path.exists():
            return None
        data = path.read_bytes()
        await self.execute(
            "UPDATE asset_index SET last_accessed = ? WHERE id = ?",
            (time.time(), asset_id),
        )
        return data

    async def search_files(
        self,
        asset_type: str | None = None,
        name: str | None = None,
        tag: str | None = None,
        limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> list[dict[str, Any]]:
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
        rows = await self.query(
            f"SELECT * FROM asset_index {where} ORDER BY archived_at DESC LIMIT ?",
            tuple(params),
        )
        for row in rows:
            if "tags" in row:
                row["tags"] = json.loads(row["tags"])
        return rows

    async def delete_file(self, asset_id: str) -> bool:
        rows = await self.query(
            "SELECT file_path FROM asset_index WHERE id = ?", (asset_id,)
        )
        if not rows or "error" in rows[0]:
            return False
        path = self._assets_dir / rows[0]["file_path"]
        ok = await self.execute("DELETE FROM asset_index WHERE id = ?", (asset_id,))
        if ok and path.exists():
            path.unlink()
        return ok
