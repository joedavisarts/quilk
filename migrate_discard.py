#!/usr/bin/env python3
"""
migrate_discard.py — Adds the discard/trash system columns.

Idempotent — guarded with PRAGMA table_info checks, safe to run multiple times.

Usage:
    python migrate_discard.py [path/to/ledger.db]

Defaults to ledger.db in the current directory when no path is given.
"""

import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else 'ledger.db'


def col_exists(cursor, table, column):
    cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


print(f"Connecting to: {DB_PATH}")
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.execute("PRAGMA journal_mode=WAL")

changes = []

# ── documents table ──────────────────────────────────────────────────────────
if not col_exists(cur, 'documents', 'discarded'):
    cur.execute(
        "ALTER TABLE documents ADD COLUMN discarded INTEGER NOT NULL DEFAULT 0"
    )
    changes.append('documents.discarded')

if not col_exists(cur, 'documents', 'discarded_at'):
    cur.execute("ALTER TABLE documents ADD COLUMN discarded_at TEXT")
    changes.append('documents.discarded_at')

# Tracks whether a doc was discarded as a cascade of its job being discarded.
# Used to selectively restore docs when a job is restored.
if not col_exists(cur, 'documents', 'discarded_with_job'):
    cur.execute(
        "ALTER TABLE documents ADD COLUMN discarded_with_job INTEGER NOT NULL DEFAULT 0"
    )
    changes.append('documents.discarded_with_job')

# ── jobs table ───────────────────────────────────────────────────────────────
if not col_exists(cur, 'jobs', 'discarded'):
    cur.execute(
        "ALTER TABLE jobs ADD COLUMN discarded INTEGER NOT NULL DEFAULT 0"
    )
    changes.append('jobs.discarded')

if not col_exists(cur, 'jobs', 'discarded_at'):
    cur.execute("ALTER TABLE jobs ADD COLUMN discarded_at TEXT")
    changes.append('jobs.discarded_at')

con.commit()
con.close()

if changes:
    print("Added columns:", ', '.join(changes))
else:
    print("All columns already present — nothing to do.")
