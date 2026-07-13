"""RAG context retriever — agent-private store integration.

Queries the calling agent's isolated SQLite ``memory.db`` for
relevant context snippets via FTS5 full-text search, falling
back to keyword matching on asset index entries.

Only receives a ``store_path`` string — never a direct reference
to the agent object — preserving strict zero-coupling.
"""

import logging
import sqlite3
import json

from pathlib import Path


logger = logging.getLogger(__name__)

_MAX_SNIPPETS = 5
_SNIPPET_CHAR_LIMIT = 2000


class RAGRetriever:

    def __init__(self, max_snippets: int = _MAX_SNIPPETS) -> None:
        self._max_snippets = max_snippets

    async def close(self) -> None:
        pass

    async def retrieve(
        self,
        store_path: str | Path,
        query: str,
    ) -> list[str]:
        db_path = Path(store_path)
        if not db_path.exists():
            return []

        tokens = self._tokenize(query)
        snippets: list[tuple[str, float]] = []

        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row

                fts_rows = self._fts_search(conn, tokens)
                snippets.extend(fts_rows)

                if len(snippets) < self._max_snippets:
                    asset_rows = self._asset_search(conn, tokens)
                    snippets.extend(asset_rows)
        except Exception as exc:
            logger.warning("RAG retrieve failed: %s", exc)
            return []

        snippets.sort(key=lambda x: x[1], reverse=True)
        return [
            text[:_SNIPPET_CHAR_LIMIT]
            for text, _ in snippets[: self._max_snippets]
        ]

    # ------------------------------------------------------------------
    # Internal search strategies
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        return [w.strip(".,!?;:()[]{}") for w in text.lower().split() if len(w) > 2]

    def _fts_search(self, conn: sqlite3.Connection, tokens: list[str]) -> list[tuple[str, float]]:
        try:
            query = " OR ".join(tokens)
            rows = conn.execute(
                "SELECT details, rank FROM activity_log_fts "
                "WHERE details MATCH ? ORDER BY rank LIMIT ?",
                (query, self._max_snippets),
            ).fetchall()
            return [
                (json.dumps(row["details"]), float(row["rank"]))
                for row in rows
            ]
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return []

    def _asset_search(self, conn: sqlite3.Connection, tokens: list[str]) -> list[tuple[str, float]]:
        results: list[tuple[str, int]] = []
        for token in tokens:
            like = f"%{token}%"
            rows = conn.execute(
                "SELECT name, tags FROM asset_index WHERE name LIKE ? OR tags LIKE ?",
                (like, like),
            ).fetchall()
            for row in rows:
                text = f"asset:{row['name']} tags:{row['tags']}"
                results.append((text, len(token)))
        return results
