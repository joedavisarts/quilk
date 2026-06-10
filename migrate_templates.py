"""
Migration: create email_templates table for per-user, per-doc-type email templates.
Idempotent — safe to run multiple times.
"""
import os
import sqlite3

DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "ledger.db")

conn = sqlite3.connect(DB_PATH)
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()}

if "email_templates" not in tables:
    conn.execute("""
        CREATE TABLE email_templates (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id),
            doc_type         TEXT NOT NULL,
            subject_template TEXT NOT NULL,
            body_template    TEXT NOT NULL,
            UNIQUE(user_id, doc_type)
        )
    """)
    conn.commit()
    print("✓ Created email_templates table.")
else:
    print("email_templates already exists — nothing to do.")

conn.close()
