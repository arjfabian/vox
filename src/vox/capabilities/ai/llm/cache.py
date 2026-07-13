"""Semantic cache for LLM responses.

SQLite-backed store with exact SHA-256 keyed lookup and TTL eviction.
Write transactions are serialized via ``asyncio.Lock`` to guarantee
safe concurrent access when shared across parallel agent invocations.
"""

import hashlib
import json
import sqlite3
import time
import asyncio
import logging

from pathlib import Path
from typing import Optional

from .models import LLMGenerationResult


logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "llm_cache.db"
_DEFAULT_TTL = 3600


def _sha256(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class SemanticCache:

    def __init__(
        self,
        db_path: str | Path = _DEFAULT_DB_PATH,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._db_path = Path(db_path)
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup(
        self,
        system: str,
        prompt: str,
        model: str,
    ) -> Optional[LLMGenerationResult]:
        key = _sha256(system, prompt, model)

        async with self._lock:
            row = self._query_one(
                "SELECT response, model, cached_at FROM exact_cache WHERE key = ?",
                (key,),
            )

        if row is not None:
            elapsed = time.time() - row["cached_at"]
            if elapsed < self._ttl:
                logger.info("Exact cache HIT for key=%s", key[:12])
                data = json.loads(row["response"])
                return LLMGenerationResult(
                    content=data["content"],
                    model=row["model"],
                )

        return None

    async def store(
        self,
        system: str,
        prompt: str,
        model: str,
        content: str,
    ) -> None:
        key = _sha256(system, prompt, model)
        payload = json.dumps({"content": content})

        async with self._lock:
            self._execute(
                "INSERT OR REPLACE INTO exact_cache (key, response, model, cached_at) "
                "VALUES (?, ?, ?, ?)",
                (key, payload, model, time.time()),
            )

        logger.info("Cached response for key=%s", key[:12])

    async def invalidate(self, system: str, prompt: str, model: str) -> None:
        key = _sha256(system, prompt, model)
        async with self._lock:
            self._execute("DELETE FROM exact_cache WHERE key = ?", (key,))

    async def clear_expired(self) -> int:
        cutoff = time.time() - self._ttl
        async with self._lock:
            self._execute("DELETE FROM exact_cache WHERE cached_at < ?", (cutoff,))
            removed = self._query_one("SELECT changes() AS cnt")
        return removed["cnt"] if removed else 0

    async def close(self) -> None:
        if hasattr(self, "_lock"):
            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exact_cache (
                    key       TEXT PRIMARY KEY,
                    response  TEXT NOT NULL,
                    model     TEXT NOT NULL,
                    cached_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exact_model "
                "ON exact_cache(model)"
            )

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchmany(1)
            return dict(rows[0]) if rows else None

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(sql, params)
