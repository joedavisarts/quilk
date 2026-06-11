#!/usr/bin/env python3
"""
Idempotent migration: add job_id, deposit_amount, deposit_type, invoice_type,
voided, void_reason columns to documents and backfill existing rows.

IMPORTANT: Do NOT run against a live database without a backup.
See the Render shell commands at the bottom of this docstring.

Render backup + run commands
─────────────────────────────
# 1. Open the Render shell for your service, then:

# Backup
cp /data/ledger.db /data/ledger_backup_$(date +%Y%m%d_%H%M%S).db

# Run migration (Python is on PATH in the Render environment)
python migrate_jobs.py /data/ledger.db

# Verify (spot-check)
sqlite3 /data/ledger.db "SELECT id, job_id, invoice_type, voided FROM documents LIMIT 10;"
"""
import sqlite3
import uuid
import os
import sys


def col_exists(c, table, col):
    return col in [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]


def run(db_path: str) -> None:
    print(f"Connecting to: {db_path}")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    new_columns = [
        ("job_id",         "TEXT"),
        ("deposit_amount", "REAL"),
        ("deposit_type",   "TEXT"),
        ("invoice_type",   "TEXT"),
        ("voided",         "INTEGER NOT NULL DEFAULT 0"),
        ("void_reason",    "TEXT"),
    ]

    for col, defn in new_columns:
        if not col_exists(c, "documents", col):
            c.execute(f"ALTER TABLE documents ADD COLUMN {col} {defn}")
            print(f"  Added column: documents.{col}")
        else:
            print(f"  Column already exists, skipping: documents.{col}")

    # Backfill job_id: every document without one becomes its own standalone job
    rows = c.execute("SELECT id FROM documents WHERE job_id IS NULL").fetchall()
    print(f"\nBackfilling job_id for {len(rows)} document(s)...")
    for row in rows:
        c.execute(
            "UPDATE documents SET job_id = ? WHERE id = ?",
            (str(uuid.uuid4()), row["id"]),
        )

    # Ensure voided is 0 (not NULL) for all existing rows
    updated = c.execute(
        "UPDATE documents SET voided = 0 WHERE voided IS NULL"
    ).rowcount
    if updated:
        print(f"Set voided=0 on {updated} row(s) where it was NULL.")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/data/ledger.db")
    run(path)
