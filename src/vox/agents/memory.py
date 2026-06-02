"""Append-only forensic audit log for VOX agents.

This is NOT semantic memory.
This is an immutable operational ledger used for observability, causality tracing,
debugging, auditability, and event reconstruction.
"""

import json
import logging
import sqlite3
import time
import uuid

from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

_DEFAULT_LOG_LIMIT = 100


class VOXAgentMemory:

    def __init__(self, agent_dir: Path) -> None:
        self._db_path = agent_dir / "memory" / "logs.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON activity_log(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON activity_log(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_id ON activity_log(ref_id)")

    def record(
        self,
        event_type: str,
        action: str,
        actor: str = "SYSTEM",
        details: Optional[Dict[str, Any]] = None,
        ref_id: Optional[str] = None,
        status: str = "COMPLETED",
    ) -> str:
        event_id = str(uuid.uuid4())
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO activity_log (id, timestamp, event_type, actor, action, details, ref_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, time.time(), event_type, actor, action,
                     json.dumps(details or {}), ref_id, status),
                )
        except Exception as e:
            logger.exception("Forensic record failed [%s]: %s", event_type, e)
        return event_id

    def get_recent(self, limit: int = _DEFAULT_LOG_LIMIT) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_recent failed: %s", exc)
            return [{"error": str(exc)}]

    def get_thread(self, ref_id: str) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM activity_log WHERE ref_id = ? ORDER BY timestamp ASC", (ref_id,),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_thread failed: %s", exc)
            return [{"error": str(exc)}]

    def get_pending(self) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM activity_log WHERE status IN ('PENDING', 'RUNNING') ORDER BY timestamp ASC",
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.get("details") or "{}")
                result.append(item)
            return result
        except Exception as exc:
            logger.exception("get_pending failed: %s", exc)
            return [{"error": str(exc)}]
