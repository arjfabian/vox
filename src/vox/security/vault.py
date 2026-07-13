"""AgentVault — per-agent encrypted secret store.

Uses AES-256-GCM with PBKDF2HMAC key derivation.
Each agent gets an isolated ``secrets.vault`` SQLite file.
Fallback chain: vault → local ``.env`` (via config dict).
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


_VAULT_FILENAME = "secrets.vault"
_PBKDF2_ITERATIONS = 200_000
_AES_KEY_SIZE = 32


class AgentVault:

    def __init__(self, agent_dir: Path, agent_id: str, config: dict | None = None) -> None:
        self._vault_path = agent_dir / _VAULT_FILENAME
        self._agent_id = agent_id
        self._config = config or {}
        self._key: bytes | None = None

        # 1. Fail-fast check: Validate environment BEFORE touching the disk
        master_key = self._check_master_key_presence()

        # 2. Initialize storage and derive key cleanly passing the validated token
        self._ensure_db()
        self._key = self._derive_key(master_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, capability_name: str, secret_key: str) -> Optional[str]:
        """Return decrypted value from vault, falling back to config/.env.

        If the key exists in the database but is marked *inactive*, returns
        ``None`` explicitly.  Only falls back to ``self._config`` (the
        local ``.env``) when the key does **not** exist in the database at
        all.
        """
        if self.exists():
            row = self._query(
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

    def set(self, capability_name: str, secret_key: str, value: str, status: str = "active") -> None:
        encrypted = self._encrypt(value)
        self._execute(
            "INSERT OR REPLACE INTO secrets (capability_name, secret_key, encrypted_value, status) "
            "VALUES (?, ?, ?, ?)",
            (capability_name, secret_key, encrypted, status),
        )

    def list_inactive(self) -> set[tuple[str, str]]:
        """Return ``{(capability_name, secret_key)}`` for all inactive rows."""
        rows = self._query_all(
            "SELECT capability_name, secret_key FROM secrets WHERE status = 'inactive'"
        )
        return {(cap, key) for cap, key in rows}

    def list_active(self) -> dict[str, dict[str, str]]:
        """Return ``{capability_name: {secret_key: value}}`` for all active entries."""
        rows = self._query_all(
            "SELECT capability_name, secret_key, encrypted_value FROM secrets WHERE status = 'active'"
        )
        result: dict[str, dict[str, str]] = {}
        for cap, key, blob in rows:
            result.setdefault(cap, {})[key] = self._decrypt(blob)
        return result

    def exists(self) -> bool:
        return self._vault_path.exists()

    def disable(self, capability_name: str, secret_key: str) -> None:
        self._execute(
            "UPDATE secrets SET status = 'inactive' "
            "WHERE capability_name = ? AND secret_key = ?",
            (capability_name, secret_key),
        )

    def activate(self, capability_name: str, secret_key: str) -> None:
        self._execute(
            "UPDATE secrets SET status = 'active' "
            "WHERE capability_name = ? AND secret_key = ?",
            (capability_name, secret_key),
        )

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
        salt = self._get_or_create_salt()
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_AES_KEY_SIZE,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        return kdf.derive(master_key.encode())

    def _get_or_create_salt(self) -> bytes:
        """Retrieve the existing salt or safely insert a new one, avoiding race conditions."""
        with sqlite3.connect(self._vault_path) as conn:
            # First, check if a salt already exists
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'salt'"
            ).fetchone()
            if row:
                return row[0]
            
            # If not, generate a new high-entropy candidate
            candidate_salt = os.urandom(16)
            
            # Use INSERT OR IGNORE to guarantee atomicity at the database level.
            # SQLite's internal locking makes this safe. Real structural failures (disk full, etc.)
            # will cleanly propagate instead of failing downstream with a NoneType error.
            conn.execute(
                "INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('salt', ?)",
                (candidate_salt,)
            )
            conn.commit()
            
            # Final fetch ensures we always return the definitive winning row from the DB
            final_row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'salt'"
            ).fetchone()
            return final_row[0]

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
    # Internal — storage
    # ------------------------------------------------------------------

    def _ensure_db(self) -> None:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._vault_path) as conn:
            # Create metadata table for non-encrypted structural values like salt
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_meta (
                    key   TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                )
            """)
            
            # Encrypted secrets table mapping capabilities to credentials
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

    def _query(self, sql: str, params: tuple = ()) -> Optional[tuple]:
        # Removed redundant self._ensure_db() since __init__ guarantees layout
        with sqlite3.connect(self._vault_path) as conn:
            rows = conn.execute(sql, params).fetchmany(1)
            return rows[0] if rows else None

    def _query_all(self, sql: str, params: tuple = ()) -> list[tuple]:
        # Removed redundant self._ensure_db()
        with sqlite3.connect(self._vault_path) as conn:
            return conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        # Removed redundant self._ensure_db()
        with sqlite3.connect(self._vault_path) as conn:
            conn.execute(sql, params)
            conn.commit()