#!/usr/bin/env python3
"""
migrate_to_supabase.py — Run ONCE in the Render shell after deploying the new code.

BEFORE RUNNING:
  1. Confirm SUPABASE_URL and SUPABASE_SERVICE_KEY are set in the environment.
  2. Run the following SQL in the Supabase SQL editor to add missing columns:

       ALTER TABLE clients ADD COLUMN IF NOT EXISTS discarded  boolean     DEFAULT false;
       ALTER TABLE clients ADD COLUMN IF NOT EXISTS discarded_at timestamptz;
       ALTER TABLE gigs    ADD COLUMN IF NOT EXISTS discarded  boolean     DEFAULT false;
       ALTER TABLE gigs    ADD COLUMN IF NOT EXISTS discarded_at timestamptz;

  3. Deploy the new app.py first (so init_db() adds client_uuid / discarded columns
     to the SQLite DB on startup), THEN run this script.

AFTER RUNNING:
  - Verify counts in the Supabase dashboard.
  - The SQLite clients and jobs tables are left intact as read-only backups.
  - Delete or archive this file once you are satisfied.
"""

import os
import sqlite3
import sys
import uuid

# ---------------------------------------------------------------------------
# Boot check
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment.")
    sys.exit(1)

from supabase import create_client  # noqa: E402 (import after env check)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DATA_DIR, 'ledger.db')

if not os.path.exists(DB_PATH):
    print(f"ERROR: SQLite DB not found at {DB_PATH}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col_names(cursor, table):
    return [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]

def _get(row, key, default=None):
    try:
        val = row[key]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default

def _concat_address(row):
    parts = [
        (_get(row, 'address_line1') or '').strip(),
        (_get(row, 'address_line2') or '').strip(),
        (_get(row, 'city') or '').strip(),
        (_get(row, 'country') or '').strip(),
    ]
    return ', '.join(p for p in parts if p) or None

def _abort(msg):
    print(f"\nABORTED: {msg}")
    conn.close()
    sys.exit(1)

# ---------------------------------------------------------------------------
# Pre-flight: check Supabase is reachable and tables exist
# ---------------------------------------------------------------------------

print("Pre-flight checks...")
for table in ('clients', 'gigs'):
    try:
        r = sb.table(table).select('id').limit(1).execute()
    except Exception as e:
        _abort(f"Cannot reach Supabase table '{table}': {e}")

# Warn if Supabase already has rows (guard against double-migration)
existing_clients = sb.table('clients').select('id', count='exact').execute()
existing_gigs    = sb.table('gigs').select('id', count='exact').execute()
ec = existing_clients.count or 0
eg = existing_gigs.count or 0

if ec > 0 or eg > 0:
    print(f"\nWARNING: Supabase already has {ec} client(s) and {eg} gig(s).")
    answer = input("Continue anyway? Duplicates will be skipped. [y/N] ").strip().lower()
    if answer != 'y':
        _abort("User chose not to continue.")

# ---------------------------------------------------------------------------
# Migrate clients  (SQLite → Supabase)
# ---------------------------------------------------------------------------

print("\n=== CLIENTS ===")
client_cols = _col_names(c, 'clients')
has_discarded = 'discarded' in client_cols

clients_rows = c.execute("SELECT * FROM clients").fetchall()
print(f"Found {len(clients_rows)} client(s) in SQLite.")

client_id_map = {}   # sqlite integer id → new UUID string

ok = 0
skip = 0
fail = 0

for row in clients_rows:
    sqlite_id = row['id']
    user_id   = _get(row, 'user_id', 1)
    name      = (_get(row, 'name') or '').strip()
    company   = (_get(row, 'company_name') or '').strip() or None
    label     = company or name or f"client#{sqlite_id}"
    discarded    = bool(_get(row, 'discarded', 0)) if has_discarded else False
    discarded_at = _get(row, 'discarded_at') if has_discarded else None

    new_id = str(uuid.uuid4())
    payload = {
        'id':                  new_id,
        'quilk_user_id':       user_id,
        'client_name':         name,
        'artist_company_name': company,
        'email':               _get(row, 'email'),
        'phone':               _get(row, 'phone'),
        'address':             _concat_address(row),
        'notes':               _get(row, 'notes'),
        'discarded':           discarded,
        'discarded_at':        discarded_at,
    }

    try:
        result = sb.table('clients').insert(payload).execute()
        if result.data:
            client_id_map[sqlite_id] = new_id
            print(f"  ✓  [{sqlite_id}] {label}  →  {new_id}")
            ok += 1
        else:
            print(f"  ✗  [{sqlite_id}] {label}  —  empty response: {result}")
            fail += 1
    except Exception as e:
        err_str = str(e)
        if 'duplicate' in err_str.lower() or '23505' in err_str:
            print(f"  –  [{sqlite_id}] {label}  —  skipped (already exists)")
            skip += 1
        else:
            print(f"  ✗  [{sqlite_id}] {label}  —  {e}")
            fail += 1

print(f"\nClients: {ok} inserted, {skip} skipped, {fail} failed.")

# ---------------------------------------------------------------------------
# Back-fill documents.client_uuid  (SQLite update)
# ---------------------------------------------------------------------------

print("\n=== BACK-FILL documents.client_uuid ===")
doc_rows = c.execute(
    "SELECT id, client_id FROM documents WHERE client_id IS NOT NULL"
).fetchall()
print(f"Found {len(doc_rows)} document(s) with a client_id.")

updated_docs = 0
for doc in doc_rows:
    new_uuid = client_id_map.get(doc['client_id'])
    if new_uuid:
        c.execute("UPDATE documents SET client_uuid=? WHERE id=?", (new_uuid, doc['id']))
        updated_docs += 1

conn.commit()
print(f"  ✓  Updated {updated_docs} document(s).")

# ---------------------------------------------------------------------------
# Back-fill client_templates.client_uuid  (SQLite update)
# ---------------------------------------------------------------------------

print("\n=== BACK-FILL client_templates.client_uuid ===")
tmpl_rows = c.execute(
    "SELECT id, client_id FROM client_templates"
).fetchall()
print(f"Found {len(tmpl_rows)} template(s).")

updated_tmpls = 0
for t in tmpl_rows:
    new_uuid = client_id_map.get(t['client_id'])
    if new_uuid:
        c.execute("UPDATE client_templates SET client_uuid=? WHERE id=?",
                  (new_uuid, t['id']))
        updated_tmpls += 1

conn.commit()
print(f"  ✓  Updated {updated_tmpls} template(s).")

# ---------------------------------------------------------------------------
# Migrate jobs / gigs  (SQLite → Supabase)
# ---------------------------------------------------------------------------

print("\n=== JOBS (gigs) ===")
job_cols = _col_names(c, 'jobs')
has_job_discarded = 'discarded' in job_cols

jobs_rows = c.execute("SELECT * FROM jobs").fetchall()
print(f"Found {len(jobs_rows)} job(s) in SQLite.")

ok_j = skip_j = fail_j = 0

for row in jobs_rows:
    job_id   = row['job_id']
    user_id  = _get(row, 'user_id', 1)
    job_num  = _get(row, 'job_number', '')
    job_title = _get(row, 'job_title')
    created  = _get(row, 'created_at')
    discarded    = bool(_get(row, 'discarded', 0)) if has_job_discarded else False
    discarded_at = _get(row, 'discarded_at') if has_job_discarded else None

    payload = {
        'id':            job_id,
        'quilk_user_id': user_id,
        'job_number':    job_num,
        'job_title':     job_title,
        'created_at':    created,
        'discarded':     discarded,
        'discarded_at':  discarded_at,
    }

    try:
        result = sb.table('gigs').insert(payload).execute()
        if result.data:
            print(f"  ✓  {job_id}  ({job_num})")
            ok_j += 1
        else:
            print(f"  ✗  {job_id}  —  empty response: {result}")
            fail_j += 1
    except Exception as e:
        err_str = str(e)
        if 'duplicate' in err_str.lower() or '23505' in err_str:
            print(f"  –  {job_id}  —  skipped (already exists)")
            skip_j += 1
        else:
            print(f"  ✗  {job_id}  —  {e}")
            fail_j += 1

print(f"\nJobs: {ok_j} inserted, {skip_j} skipped, {fail_j} failed.")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

conn.close()

print("\n" + "=" * 60)
print("MIGRATION COMPLETE")
print(f"  Clients : {ok} inserted, {skip} skipped, {fail} failed")
print(f"  Jobs    : {ok_j} inserted, {skip_j} skipped, {fail_j} failed")
print(f"  docs back-filled    : {updated_docs}")
print(f"  templates back-filled: {updated_tmpls}")
print("=" * 60)

if fail or fail_j:
    print("\nSome records failed. Review the output above before deploying app.py.")
    sys.exit(1)
else:
    print("\nAll records migrated. Verify counts in Supabase, then the app is ready.")
