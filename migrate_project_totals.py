"""
One-time migration: backfill project_discount and project_total on all existing documents.

Run on Render:
  python3 migrate_project_totals.py

The script:
  1. Adds the two columns if missing (safe no-op if already present).
  2. Sets project_discount = 0 and project_total = subtotal for all docs (safe default).
  3. Applies the known real-discount corrections for historical documents.
  4. Prints a summary of every row touched.

After running, verify with:
  python3 -c "
  import sqlite3, os
  conn = sqlite3.connect(os.environ.get('DATA_DIR','.') + '/ledger.db')
  conn.row_factory = sqlite3.Row
  rows = conn.execute('SELECT doc_number, subtotal, discount, project_discount, project_total FROM documents WHERE voided=0 AND discarded=0 ORDER BY doc_number').fetchall()
  for r in rows: print(dict(r))
  "
"""

import os
import sqlite3
import shutil
from datetime import datetime

DATA_DIR = os.environ.get('DATA_DIR', '.')
DB_PATH = os.path.join(DATA_DIR, 'ledger.db')

# ── Backup ────────────────────────────────────────────────────────────────────
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_path = DB_PATH + f'.bak_{ts}'
shutil.copy2(DB_PATH, backup_path)
print(f"✓ Backup created: {backup_path}")

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# ── Add columns if missing ────────────────────────────────────────────────────
def _add_col(table, col, definition):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        print(f"  Added column {table}.{col}")
    else:
        print(f"  Column {table}.{col} already exists — skipped")

_add_col('documents', 'project_discount', 'REAL DEFAULT 0')
_add_col('documents', 'project_total',    'REAL DEFAULT 0')

# ── Step 1: set defaults for ALL rows ────────────────────────────────────────
# project_discount = 0, project_total = subtotal
c.execute("""
    UPDATE documents
    SET project_discount = 0,
        project_total    = COALESCE(subtotal, 0)
    WHERE project_total = 0 OR project_total IS NULL
""")
print(f"  Default pass: {c.rowcount} rows updated (project_discount=0, project_total=subtotal)")

# ── Step 2: apply real-discount corrections ───────────────────────────────────
# Format: (doc_number, project_discount, project_total)
# project_total = full rate (subtotal) minus the real friend/goodwill discount given
corrections = [
    # Joe (user_id=1)
    ('JDAMI200', 750.0,     750.0),      # Kristen Walker — subtotal=1500, discount=750 real
    ('JDAMI222', 67500.0,   370000.0),   # Observer Food Awards — subtotal=437500 JMD, discount=67500 real
    ('JDAMI227', 960.0,     400.0),      # Britanya deposit — subtotal=1360, discount=960 real
    ('JDAMI231', 380.0,     500.0),      # Peter Richardson deposit — subtotal=880, discount=380 real
    ('JDAMI233', 380.0,     500.0),      # Peter Richardson balance — subtotal=880, discount=380 real
    ('JDAMI234', 0.0,       30000.0),    # VSNRY — subtotal=30000 JMD, discount=0 (revenue split, not a real discount)
    ('JDAMI236', 960.0,     400.0),      # Britanya balance — subtotal=1360, discount=960 real
    # Aureum (user_id=2)
    ('ALEQ003',  330.0,     1320.0),     # Aureum quote — subtotal=1650, discount=330 real
    ('ALEQ005',  300.0,     1270.0),     # Aureum quote — subtotal=1570, discount=300 real
]

for doc_number, project_discount, project_total in corrections:
    c.execute(
        "UPDATE documents SET project_discount=?, project_total=? WHERE doc_number=?",
        (project_discount, project_total, doc_number),
    )
    if c.rowcount:
        print(f"  ✓ {doc_number}: project_discount={project_discount}, project_total={project_total}")
    else:
        print(f"  ⚠ {doc_number}: NOT FOUND (may have been voided/discarded — OK to ignore)")

conn.commit()

# ── Step 3: verify summary ────────────────────────────────────────────────────
rows = c.execute("""
    SELECT doc_number, doc_type, subtotal, discount, project_discount, project_total
    FROM documents
    WHERE voided=0 AND discarded=0
    ORDER BY user_id, doc_number
""").fetchall()

print("\n── Verification (non-voided, non-discarded) ─────────────────────────────")
print(f"{'DOC':12} {'TYPE':8} {'SUBTOTAL':>10} {'DISCOUNT':>10} {'PROJ_DISC':>10} {'PROJ_TOT':>10}")
print("-" * 66)
for r in rows:
    print(f"{r['doc_number']:12} {r['doc_type']:8} {r['subtotal']:>10.2f} {r['discount']:>10.2f} {r['project_discount']:>10.2f} {r['project_total']:>10.2f}")

conn.close()
print("\n✓ Migration complete.")
