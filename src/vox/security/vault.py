"""WorkloadVault — per-workload encrypted secret store.

Uses AES-256-GCM with PBKDF2HMAC key derivation.
Each workload gets an isolated ``secrets.vault`` SQLite file.
Fallback chain: vault → local ``.env`` (via config dict).

Synchronous ``sqlite3`` is used only inside ``__init__`` for schema
creation and salt derivation (one-time bootstrap path).  All runtime
public methods use ``aiosqlite`` and are fully async.
"""

import os
import sqlite3 as _sync_sqlite3
from pathlib import Path

import aiosqlite
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_VAULT_FILENAME = "secrets.vault"
_PBKDF2_ITERATIONS = 200_000
_AES_KEY_SIZE = 32


class VaultAccessError(RuntimeError):
    """Raised when the vault is required but cannot be initialised
    (e.g. missing VOX_MASTER_KEY)."""


class WorkloadVault:
    def __init__(
        self, persona_dir: Path, workload_id: str, config: dict | None = None
    ) -> None:
        self._vault_path = persona_dir / _VAULT_FILENAME
        self._workload_id = workload_id
        self._config = config or {}
        self._key: bytes | None = None

        # 1. Fail-fast check: Validate environment BEFORE touching the disk
        master_key = self._check_master_key_presence()

        # 2. Initialize storage and derive key cleanly passing the validated token
        self._ensure_db_sync()
        self._key = self._derive_key(master_key)

    # ------------------------------------------------------------------
    # Public API — async runtime path
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Async schema creation (runtime path)."""
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._vault_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_meta (
                    key   TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    capability_name TEXT NOT NULL,
                    secret_key      TEXT NOT NULL,
                    encrypted_value BLOB NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY (capability_name, secret_key)
                )
            """)
            await conn.commit()

            # --- one-time capability rename migration -------------------
            async with conn.execute(
                "SELECT COUNT(*) FROM secrets WHERE capability_name = ?",
                ("comm.messenger",),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] > 0:
                    await conn.execute(
                        "UPDATE secrets SET capability_name = ? "
                        "WHERE capability_name = ?",
                        ("comm.gateway", "comm.messenger"),
                    )
                    await conn.commit()

    async def get(self, capability_name: str, secret_key: str) -> str | None:
        """Return decrypted value from vault, falling back to config/.env.

        If the key exists in the database but is marked *inactive*, returns
        ``None`` explicitly.  Only falls back to ``self._config`` (the
        local ``.env``) when the key does **not** exist in the database at
        all.
        """
        if self.exists():
            row = await self._query(
                "SELECT encrypted_value, status FROM secrets "
                "WHERE capability_name = ? AND secret_key = ?",
                (capability_name, secret_key),
            )
            if row:
                value, status = row
                if status == "active":
                    return self._decrypt(value)
                return None

        return self._config.get(secret_key)

    async def set(
        self, capability_name: str, secret_key: str, value: str, status: str = "active"
    ) -> None:
        encrypted = self._encrypt(value)
        await self._execute(
            "INSERT OR REPLACE INTO secrets (capability_name, secret_key, encrypted_value, status) "
            "VALUES (?, ?, ?, ?)",
            (capability_name, secret_key, encrypted, status),
        )

    async def list_inactive(self) -> set[tuple[str, str]]:
        """Return ``{(capability_name, secret_key)}`` for all inactive rows."""
        rows = await self._query_all(
            "SELECT capability_name, secret_key FROM secrets WHERE status = 'inactive'"
        )
        return {(cap, key) for cap, key in rows}

    async def list_active(self) -> dict[str, dict[str, str]]:
        """Return ``{capability_name: {secret_key: value}}`` for all active entries."""
        rows = await self._query_all(
            "SELECT capability_name, secret_key, encrypted_value FROM secrets WHERE status = 'active'"
        )
        result: dict[str, dict[str, str]] = {}
        for cap, key, blob in rows:
            result.setdefault(cap, {})[key] = self._decrypt(blob)
        return result

    def exists(self) -> bool:
        return self._vault_path.exists()

    async def disable(self, capability_name: str, secret_key: str) -> None:
        await self._execute(
            "UPDATE secrets SET status = 'inactive' "
            "WHERE capability_name = ? AND secret_key = ?",
            (capability_name, secret_key),
        )

    async def activate(self, capability_name: str, secret_key: str) -> None:
        await self._execute(
            "UPDATE secrets SET status = 'active' "
            "WHERE capability_name = ? AND secret_key = ?",
            (capability_name, secret_key),
        )

    # ------------------------------------------------------------------
    # Synchronous get() for bootstrap path
    # ------------------------------------------------------------------

    def get_sync(self, capability_name: str, secret_key: str) -> str | None:
        """Synchronous get() variant for use during ``__init__``-time bootstrap.

        Shares the exact same decryption logic as the async ``get()``.
        """
        if self.exists():
            conn = _sync_sqlite3.connect(self._vault_path)
            try:
                row = conn.execute(
                    "SELECT encrypted_value, status FROM secrets "
                    "WHERE capability_name = ? AND secret_key = ?",
                    (capability_name, secret_key),
                ).fetchone()
            finally:
                conn.close()
            if row:
                value, status = row
                if status == "active":
                    return self._decrypt(value)
                return None

        return self._config.get(secret_key)

    # ------------------------------------------------------------------
    # Internal — encryption
    # ------------------------------------------------------------------

    def _check_master_key_presence(self) -> str:
        """Ensure VOX_MASTER_KEY is available before any side-effects occur."""
        master_key = os.environ.get("VOX_MASTER_KEY")
        if not master_key:
            raise RuntimeError(
                "VOX_MASTER_KEY environment variable is not set. "
                "Set it to a strong passphrase before using the vault."
            )
        return master_key

    def _derive_key(self, master_key: str) -> bytes:
        """Derive the encryption key using the persistent unique salt."""
        salt = self._get_or_create_salt_sync()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_AES_KEY_SIZE,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        return kdf.derive(master_key.encode())

    def _get_or_create_salt_sync(self) -> bytes:
        """Retrieve the existing salt or safely insert a new one, avoiding race conditions."""
        conn = _sync_sqlite3.connect(self._vault_path)
        try:
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'salt'"
            ).fetchone()
            if row:
                return row[0]

            candidate_salt = os.urandom(16)

            conn.execute(
                "INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('salt', ?)",
                (candidate_salt,),
            )
            conn.commit()

            final_row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'salt'"
            ).fetchone()
            return final_row[0]
        finally:
            conn.close()

    def _encrypt(self, plaintext: str) -> bytes:
        aesgcm = AESGCM(self._key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
        return nonce + ciphertext

    def _decrypt(self, blob: bytes) -> str:
        aesgcm = AESGCM(self._key)
        nonce = blob[:12]
        ciphertext = blob[12:]
        return aesgcm.decrypt(nonce, ciphertext, None).decode()

    # ------------------------------------------------------------------
    # Internal — storage (sync bootstrap)
    # ------------------------------------------------------------------

    def _ensure_db_sync(self) -> None:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _sync_sqlite3.connect(self._vault_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_meta (
                    key   TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    capability_name TEXT NOT NULL,
                    secret_key      TEXT NOT NULL,
                    encrypted_value BLOB NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'active',
                    PRIMARY KEY (capability_name, secret_key)
                )
            """)
            conn.commit()

            # --- one-time capability rename migration -------------------
            cursor = conn.execute(
                "SELECT COUNT(*) FROM secrets WHERE capability_name = ?",
                ("comm.messenger",),
            )
            if cursor.fetchone()[0] > 0:
                conn.execute(
                    "UPDATE secrets SET capability_name = ? WHERE capability_name = ?",
                    ("comm.gateway", "comm.messenger"),
                )
                conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal — storage (async runtime)
    # ------------------------------------------------------------------

    async def _query(self, sql: str, params: tuple = ()) -> tuple | None:
        async with aiosqlite.connect(self._vault_path) as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchmany(1)
            return rows[0] if rows else None

    async def _query_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        async with aiosqlite.connect(self._vault_path) as conn:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()

    async def _execute(self, sql: str, params: tuple = ()) -> None:
        async with aiosqlite.connect(self._vault_path) as conn:
            await conn.execute(sql, params)
            await conn.commit()
