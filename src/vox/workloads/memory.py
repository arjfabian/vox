"""Append-only forensic audit log for VOX workloads.

This is NOT semantic memory.
This is an immutable operational ledger used for observability, causality tracing,
debugging, auditability, and event reconstruction.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_LOG_LIMIT = 100


class VOXWorkloadMemory:
    def __init__(self, persona_dir: Path) -> None:
        self._db_path = persona_dir / "memory" / "logs.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        """Asynchronously initialize database schema and indexes."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_log (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    ref_id TEXT,
                    status TEXT NOT NULL DEFAULT 'COMPLETED'
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON activity_log(timestamp)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_type ON activity_log(event_type)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ref_id ON activity_log(ref_id)"
            )
            await conn.commit()

    async def record(
        self,
        event_type: str,
        action: str,
        actor: str = "SYSTEM",
        details: dict[str, Any] | None = None,
        ref_id: str | None = None,
        status: str = "COMPLETED",
    ) -> str:
        event_id = str(uuid.uuid4())
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                await conn.execute(
                    "INSERT INTO activity_log (id, timestamp, event_type, actor, action, details, ref_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        time.time(),
                        event_type,
                        actor,
                        action,
                        json.dumps(details or {}),
                        ref_id,
                        status,
                    ),
                )
                await conn.commit()
        except Exception:
            logger.exception("Forensic record failed [%s]", event_type)
        return event_id

    async def get_recent(self, limit: int = _DEFAULT_LOG_LIMIT) -> list[dict[str, Any]]:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ) as cursor:
                    rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_recent failed")
            return [{"error": str(exc)}]

    async def get_thread(self, ref_id: str) -> list[dict[str, Any]]:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM activity_log WHERE ref_id = ? ORDER BY timestamp ASC",
                    (ref_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_thread failed")
            return [{"error": str(exc)}]

    async def get_pending(self) -> list[dict[str, Any]]:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM activity_log WHERE status IN ('PENDING', 'RUNNING') ORDER BY timestamp ASC",
                ) as cursor:
                    rows = await cursor.fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_pending failed")
            return [{"error": str(exc)}]
