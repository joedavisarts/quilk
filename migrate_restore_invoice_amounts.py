"""
One-time migration: restore amount_due on invoices that were zeroed when a receipt was generated.

When a receipt was created from an invoice, the old code did:
    paid_amount = invoice.amount_due   (captured first)
    UPDATE SET paid_amount=?, amount_due=0  (then zeroed amount_due)

So paid_amount on the invoice = the original amount_due. This script restores it.

Run on Render shell:
    python3 migrate_restore_invoice_amounts.py

Safe to re-run — only touches rows where amount_due=0 AND paid_amount>0 AND user_id!=2.
"""

import os
import sqlite3
import shutil
from datetime import datetime

DATA_DIR = os.environ.get('DATA_DIR', '.')
DB_PATH = os.path.join(DATA_DIR, 'ledger.db')

# ── Backup ─────────────────────────────────────────────────────────────────────
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_path = DB_PATH + f'.bak_{ts}'
shutil.copy2(DB_PATH, backup_path)
print(f"✓ Backup created: {backup_path}")

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# ── Dry run: show what will change ─────────────────────────────────────────────
affected = c.execute("""
    SELECT id, doc_number, doc_type, invoice_type, user_id, amount_due, paid_amount
    FROM documents
    WHERE doc_type = 'invoice'
      AND amount_due = 0
      AND paid_amount > 0
      AND user_id != 2
    ORDER BY user_id, id
""").fetchall()

if not affected:
    print("No rows to fix — nothing to do.")
    conn.close()
    exit(0)

print(f"\n{'─'*70}")
print(f"{'ID':<6} {'DOC NUMBER':<14} {'TYPE':<8} {'USER':<6} {'amount_due':<12} {'paid_amount':<12} {'→ will set'}")
print(f"{'─'*70}")
for r in affected:
    label = r['invoice_type'] or 'full'
    print(f"{r['id']:<6} {r['doc_number']:<14} {label:<8} {r['user_id']:<6} {r['amount_due']:<12} {r['paid_amount']:<12} amount_due = {r['paid_amount']}")
print(f"{'─'*70}")
print(f"{len(affected)} row(s) will be updated.\n")

confirm = input("Apply these changes? [y/N]: ").strip().lower()
if confirm != 'y':
    print("Aborted. No changes made.")
    conn.close()
    exit(0)

# ── Apply ──────────────────────────────────────────────────────────────────────
c.execute("""
    UPDATE documents
    SET amount_due = paid_amount
    WHERE doc_type = 'invoice'
      AND amount_due = 0
      AND paid_amount > 0
      AND user_id != 2
""")
conn.commit()
print(f"✓ Updated {c.rowcount} row(s).")
conn.close()
print("Done. Verify by refreshing any affected job pages.")
