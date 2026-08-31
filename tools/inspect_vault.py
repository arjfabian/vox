"""
tools/inspect_vault.py

Diagnose and inspect a workload's vault status.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT_DIR / "instance" / "personas"


def main():
    parser = argparse.ArgumentParser(
        description="Debug and inspect workload vault"
    )
    parser.add_argument(
        "--workload",
        required=True,
        help="Workload folder name",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Purge all secrets in the vault to start clean",
    )
    args = parser.parse_args()

    persona_dir = PERSONAS_DIR / args.workload.lower()
    db_path = persona_dir / "secrets.vault"

    if not db_path.exists():
        print(f"[-] No vault file found at {db_path}")
        sys.exit(1)

    print(f"[*] Reading physical database: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if args.purge:
        confirm = input(
            "[!] WARNING: This will delete ALL encrypted "
            "secrets for this workload. Proceed? [y/N]: "
        )
        if confirm.lower() == "y":
            cursor.execute("DELETE FROM secrets;")
            conn.commit()
            print(
                "[+] Vault secrets database purged. "
                "Ready for fresh provision_vault run."
            )
            conn.close()
            sys.exit(0)
        else:
            print("Purge aborted.")
            conn.close()
            sys.exit(0)

    # Inspect tables
    try:
        cursor.execute("SELECT tbl_name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"[+] Found tables: {', '.join(tables)}")

        # Check metadata
        if "vault_meta" in tables:
            cursor.execute("SELECT * FROM vault_meta;")
            meta = cursor.fetchall()
            print("\n--- Vault Metadata ---")
            for row in meta:
                print(
                    f"  Key: {row[0]} | "
                    f"Value Size: "
                    f"{len(row[1]) if row[1] else 0} bytes"
                )

        # Check secrets (using dynamic column discovery)
        if "secrets" in tables:
            # Discover columns dynamically
            cursor.execute("PRAGMA table_info(secrets);")
            columns = [col[1] for col in cursor.fetchall()]
            print(f"\n[+] 'secrets' table columns: {', '.join(columns)}")

            # Build a safe query depending on what columns actually exist
            # Likely columns: capability/cap_id, parameter/key_name,
            # status, ciphertext/value
            col_cap = (
                "capability"
                if "capability" in columns
                else ("cap_id" if "cap_id" in columns else columns[0])
            )
            col_param = (
                "parameter"
                if "parameter" in columns
                else ("key_name" if "key_name" in columns else columns[1])
            )
            col_status = "status" if "status" in columns else columns[2]
            col_cipher = (
                "encrypted_value"
                if "encrypted_value" in columns
                else (
                    "ciphertext"
                    if "ciphertext" in columns
                    else ("value" if "value" in columns else columns[2])
                )
            )

            cursor.execute(
                f"SELECT {col_cap}, {col_param}, {col_status}, "
                f"length({col_cipher}) FROM secrets;"
            )
            secrets = cursor.fetchall()
            print("\n--- Encrypted Records Exist ---")
            if not secrets:
                print("  (No secrets stored)")
            for cap, param, status, cipher_len in secrets:
                print(
                    f"  Capability: {cap:<20} | "
                    f"Param: {param:<20} | "
                    f"Status: {status:<10} | "
                    f"Size: {cipher_len} bytes"
                )

    except sqlite3.Error as e:
        print(f"[-] SQLite Error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
