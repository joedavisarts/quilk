"""
Migration: normalize document statuses to the new per-type model.

New status sets:
  Invoices → 'paid'   (all existing invoices treated as paid)
  Quotes   → 'accepted' (all existing quotes treated as accepted)
  Receipts → 'sent'   (all existing receipts treated as sent)

Also guards the source_document_id column (already present on the live DB
since a previous migration; the guard makes this script safe to re-run).

Idempotent — safe to run multiple times.
"""
import os
import sqlite3

DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "ledger.db")

conn = sqlite3.connect(DB_PATH)

# Step 1: Ensure source_document_id column exists (already exists on live DB;
# guard makes this idempotent).
cols = [row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
if "source_document_id" not in cols:
    conn.execute(
        "ALTER TABLE documents ADD COLUMN"
        " source_document_id INTEGER DEFAULT NULL REFERENCES documents(id)"
    )
    conn.commit()
    print("✓ Added source_document_id column.")
else:
    print("source_document_id already exists — skipping column add.")

# Step 2: Normalize statuses to the new per-type model.
r = conn.execute("UPDATE documents SET status='paid'     WHERE doc_type='invoice'")
print(f"✓ Set {r.rowcount} invoice(s) → 'paid'.")

r = conn.execute("UPDATE documents SET status='accepted' WHERE doc_type='quote'")
print(f"✓ Set {r.rowcount} quote(s)   → 'accepted'.")

r = conn.execute("UPDATE documents SET status='sent'     WHERE doc_type='receipt'")
print(f"✓ Set {r.rowcount} receipt(s) → 'sent'.")

conn.commit()
conn.close()
print("Migration complete.")
