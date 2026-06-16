#!/usr/bin/env python3
"""
migrate_aureum_rename.py — rename existing Aureum doc/job IDs and update
all doc/job prefixes for the aureum user ONLY.

Jobs performed (both idempotent — safe to re-run):
  1. Rename doc_number  ALEINV003 → ALEI003
  2. Rename job_number  ALEJOB1111 → ALEJ1111
  3. Update users.doc_prefix_invoice  ALEINV → ALEI
  4. Update users.doc_prefix_quote    ALEQT  → ALEQ
  5. Update users.doc_prefix_receipt  ALERCP → ALER
  6. Update users.job_prefix          ALEJOB → ALEJ

Usage (on Render shell):
    python3 migrate_aureum_rename.py [path/to/ledger.db]
"""

import os
import sqlite3
import sys


def run(db_path):
    print(f"Database: {db_path}")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # ------------------------------------------------------------------ #
    # Locate aureum user
    # ------------------------------------------------------------------ #
    row = c.execute("SELECT id FROM users WHERE username='aureum'").fetchone()
    if not row:
        print("ERROR: aureum user not found — aborting.")
        conn.close()
        return
    aureum_id = row['id']
    print(f"Found aureum user (id={aureum_id})")

    # ------------------------------------------------------------------ #
    # 1. Rename invoice doc_number: ALEINV003 → ALEI003
    # ------------------------------------------------------------------ #
    doc_row = c.execute(
        "SELECT doc_number FROM documents WHERE user_id=? AND doc_number IN ('ALEINV003','ALEI003')",
        (aureum_id,),
    ).fetchone()
    if doc_row is None:
        print("WARNING: no document found with doc_number ALEINV003 or ALEI003 for aureum")
    elif doc_row['doc_number'] == 'ALEI003':
        print("doc_number already ALEI003 — skipping")
    else:
        c.execute(
            "UPDATE documents SET doc_number='ALEI003' WHERE doc_number='ALEINV003' AND user_id=?",
            (aureum_id,),
        )
        print(f"Updated doc_number: ALEINV003 → ALEI003 ({c.rowcount} row(s))")

    # ------------------------------------------------------------------ #
    # 2. Rename job_number: ALEJOB1111 → ALEJ1111
    # ------------------------------------------------------------------ #
    job_row = c.execute(
        "SELECT job_number FROM jobs WHERE user_id=? AND job_number IN ('ALEJOB1111','ALEJ1111')",
        (aureum_id,),
    ).fetchone()
    if job_row is None:
        print("WARNING: no job found with job_number ALEJOB1111 or ALEJ1111 for aureum")
    elif job_row['job_number'] == 'ALEJ1111':
        print("job_number already ALEJ1111 — skipping")
    else:
        c.execute(
            "UPDATE jobs SET job_number='ALEJ1111' WHERE job_number='ALEJOB1111' AND user_id=?",
            (aureum_id,),
        )
        print(f"Updated job_number: ALEJOB1111 → ALEJ1111 ({c.rowcount} row(s))")

    # ------------------------------------------------------------------ #
    # 3–6. Update prefix columns in users table
    # ------------------------------------------------------------------ #
    prefix_updates = [
        ('doc_prefix_invoice', 'ALEINV', 'ALEI'),
        ('doc_prefix_quote',   'ALEQT',  'ALEQ'),
        ('doc_prefix_receipt', 'ALERCP', 'ALER'),
        ('job_prefix',         'ALEJOB', 'ALEJ'),
    ]
    user_row = c.execute(
        "SELECT doc_prefix_invoice, doc_prefix_quote, doc_prefix_receipt, job_prefix "
        "FROM users WHERE id=?",
        (aureum_id,),
    ).fetchone()

    for col, old_val, new_val in prefix_updates:
        current = user_row[col] if user_row else None
        if current == new_val:
            print(f"users.{col} already '{new_val}' — skipping")
        elif current == old_val:
            c.execute(f"UPDATE users SET {col}=? WHERE id=?", (new_val, aureum_id))
            print(f"Updated users.{col}: '{old_val}' → '{new_val}' ({c.rowcount} row(s))")
        else:
            print(f"WARNING: users.{col} = '{current}' (expected '{old_val}') — skipping")

    conn.commit()
    conn.close()
    print("\nDone. All changes committed.")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = os.path.join(
            os.environ.get('DATA_DIR', os.path.dirname(__file__)),
            'ledger.db',
        )
    run(path)
