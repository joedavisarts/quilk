# Quilk — Claude Session Handoff

## What Quilk Is
Flask + SQLite invoicing app for Joe Davis (JDAM). Two users:
- `user_id=1` — joedavisarts (Joe, JDAM prefix, gold branding)
- `user_id=2` — aureum (Verlando Small, ALE prefix, green branding)

**Never touch user_id=2 rows. Every Supabase query must be scoped by `quilk_user_id`.**

Hosted on Render at `ledger-jdam.onrender.com`. SQLite at `/data/ledger.db`.
Push to GitHub main → Render auto-deploys.

Deploy command (Joe runs this, never Claude):
```
git add . && git commit -m "message" && git push
```

---

## Analytics Route — Current State (as of 2026-10-06)

**File:** `app.py`, route at line ~2790 (`@app.route('/analytics')`)

### Known issue: local file is behind render

The commits below were pushed from the Render shell directly and are in git,
but the local `app.py` SELECT query is missing columns. Before editing locally,
always confirm the SELECT includes these columns:

```sql
SELECT doc_type, invoice_type, status, subtotal, discount, project_discount,
       project_total, tax_amount, paid_amount, amount_due,
       currency, created_at, pay_by_date, client_uuid, job_id, voided, discarded,
       doc_number
FROM documents
WHERE user_id=? AND voided=0 AND discarded=0
ORDER BY created_at ASC
```

If the local SELECT is missing `invoice_type`, `project_total`, or `doc_number`,
the local file is stale. Run `git log --oneline -8` and compare to render shell
`git log` to find the divergence.

---

### Analytics logic — confirmed correct on Render

**FX rates (hardcoded, match frontend):**
```python
_FX = {'USD': 1, 'JMD': 158.0, 'GBP': 0.787, 'EUR': 0.918, 'CAD': 1.353}
```

All amounts stored in native currency. All analytics values must be converted
to JMD before summing, then sent to the frontend as JMD floats.
Frontend `toDisplay(amount, 'JMD')` handles display conversion.

**`invoice_type` values:** `'deposit'`, `'balance'`, `'full'`, or `None`

**Rule:** balance invoices contribute $0 to every metric — deposit already
captures the full `project_total`, so counting balance too would double-count.

**`_invoice_amt(doc)` — what was invoiced:**
```python
def _invoice_amt(doc):
    if doc.get('invoice_type') == 'balance':
        return 0
    pt = doc.get('project_total') or 0
    amt = pt if pt else (doc.get('subtotal') or 0)
    return _to_jmd(amt, doc.get('currency'))
```

**`_invoice_collected(doc)` — what was actually paid:**
```python
def _invoice_collected(doc):
    if doc.get('status') != 'paid':
        return 0
    if doc.get('invoice_type') == 'balance':
        return 0
    pt = doc.get('project_total') or 0
    amt = pt if pt else (doc.get('subtotal') or 0)
    return _to_jmd(amt, doc.get('currency'))
```

**`_invoice_outstanding(doc)` — what's owed but unpaid:**
```python
def _invoice_outstanding(doc):
    if doc.get('invoice_type') == 'balance':
        return 0
    if doc.get('status') not in ('sent', 'pending'):
        return 0
    amt = doc.get('amount_due') or 0
    cur = doc.get('currency') or 'JMD'
    return (amt / _FX.get(cur, 1)) * 158.0
```

**Helper:**
```python
def _to_jmd(amount, currency):
    cur = currency or 'JMD'
    return (amount / _FX.get(cur, 1)) * 158.0
```

**Sums:**
```python
total_collected = sum(_invoice_collected(d) for d in invoices)
total_invoiced  = sum(_invoice_amt(d) for d in invoices)
total_outstanding = sum(_invoice_outstanding(d) for d in invoices)
avg_invoice = (total_invoiced / len(invoices)) if invoices else 0
```

---

### What's confirmed correct on Render (live)

From Render shell manual check (2026-10-06):
- `total_collected` = J$1,194,000 ✅
- `total_invoiced` = J$1,360,000 ✅
- `total_outstanding` = J$147,250 ✅ (fixed in commit `9f4bbd2`)
  - 3 outstanding invoices: JDAMI200 (USD $750), JDAMI243 (JMD 10,000), JDAMI248 (JMD 18,750 deposit)

### What's still wrong on Render (pending fix)

- `avg_invoice` showing ~J$34,380 instead of expected ~J$50,370
- Root cause: `_invoice_amt` and `_invoice_collected` are NOT doing FX conversion
  on the deployed version either — confirmed by sqlite query showing total=931,020
  (not 1,360,000) when FX is omitted
- Fix needed: add `_FX`, `_to_jmd`, and apply to both functions (see code above)
- Also the `_FX` dict inside `_invoice_outstanding` should be removed since it'll
  be defined above

### Pending features (don't forget)
- Period filtering on analytics by specific time range (Joe asked for this)

---

## Security constraints (never violate)
- SUPABASE_URL and SUPABASE_SERVICE_KEY must come from env, never hardcoded
- Always back up DB before migrations
- Show Joe migrations before running on live DB
- Service role key never exposed to browser
- Every Supabase query scoped by `.eq('quilk_user_id', current_user.id)`
- Joe runs all git push commands himself — never push from Claude
