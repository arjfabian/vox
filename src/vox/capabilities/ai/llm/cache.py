"""Semantic cache for LLM responses.

SQLite-backed store with exact SHA-256 keyed lookup and TTL eviction.
Write transactions are serialized via ``asyncio.Lock`` to guarantee
safe concurrent access when shared across parallel agent invocations.
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

import aiosqlite

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
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS exact_cache (
                    key       TEXT PRIMARY KEY,
                    response  TEXT NOT NULL,
                    model     TEXT NOT NULL,
                    cached_at REAL NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exact_model ON exact_cache(model)"
            )
            await conn.commit()
        self._initialized = True

    async def close(self) -> None:
        if hasattr(self, "_lock"):
            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup(
        self,
        system: str,
        prompt: str,
        model: str,
    ) -> LLMGenerationResult | None:
        if not self._initialized:
            await self.init_db()

        key = _sha256(system, prompt, model)

        async with self._lock:
            row = await self._query_one(
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
        if not self._initialized:
            await self.init_db()

        key = _sha256(system, prompt, model)
        payload = json.dumps({"content": content})

        async with self._lock:
            await self._execute(
                "INSERT OR REPLACE INTO exact_cache (key, response, model, cached_at) "
                "VALUES (?, ?, ?, ?)",
                (key, payload, model, time.time()),
            )

        logger.info("Cached response for key=%s", key[:12])

    async def invalidate(self, system: str, prompt: str, model: str) -> None:
        if not self._initialized:
            await self.init_db()

        key = _sha256(system, prompt, model)
        async with self._lock:
            await self._execute("DELETE FROM exact_cache WHERE key = ?", (key,))

    async def clear_expired(self) -> int:
        if not self._initialized:
            await self.init_db()

        cutoff = time.time() - self._ttl
        async with self._lock:
            await self._execute(
                "DELETE FROM exact_cache WHERE cached_at < ?", (cutoff,)
            )
            row = await self._query_one("SELECT changes() AS cnt")
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _query_one(self, sql: str, params: tuple = ()) -> dict | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchmany(1)
            return dict(rows[0]) if rows else None

    async def _execute(self, sql: str, params: tuple = ()) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(sql, params)
            await conn.commit()
