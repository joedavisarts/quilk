"""
One-time migration: add multi-user support.

Run ONCE on the server after deploying the updated code:
    python migrate.py

The script is idempotent — every step checks before acting.
Set DATA_DIR env var if your database lives outside the project root
(e.g. export DATA_DIR=/data on Render).
"""
import json
import os
import sys

from werkzeug.security import generate_password_hash

# Make sure database module uses the right DATA_DIR before importing
# (caller should export DATA_DIR before running this script)
from database import get_db, DATA_DIR

# ---------------------------------------------------------------------------
# User credentials — read from env vars so they never live in plain text
# ---------------------------------------------------------------------------
JOE_PASSWORD = os.environ.get('JOE_PASSWORD', '')
if not JOE_PASSWORD:
    sys.exit('ERROR: Set the JOE_PASSWORD env var before running this migration.')

VERLANDO_PASSWORD = 'AureumLuxe2026'  # hashed immediately below; plain text only here

# ---------------------------------------------------------------------------
# Branding data
# ---------------------------------------------------------------------------

JOE_SOCIAL_LINKS = json.dumps([
    {'label': 'Instagram', 'url': 'https://www.instagram.com/joedavismusic'},
    {'label': 'YouTube',   'url': 'https://www.youtube.com/JoeDavisMusic'},
    {'label': 'TikTok',    'url': 'https://www.tiktok.com/@joedavismusic'},
    {'label': 'LinkedIn',  'url': 'https://jm.linkedin.com/in/joedavisarts'},
])

JOE_PAYMENT_METHODS = json.dumps([
    {
        'group': 'PREFERRED',
        'items': [
            {
                'label': '🇯🇲 Bank Transfer (Jamaica)',
                'details': (
                    'Joseph Lucas Fonsil Davis\n'
                    'Bank of Nova Scotia (Scotiabank)\n'
                    'Branch/Transit: 90365\n'
                    'Account Type: Chequing\n'
                    'Account Number: 90365 000658126\n'
                    'Currency: Jamaican Dollars'
                ),
            },
            {
                'label': '🇬🇧 Bank Transfer (England)',
                'details': (
                    'Joseph Davis\n'
                    'Sort Code: 30-91-83\n'
                    'Account Number: 52642068\n'
                    'IBAN: GB55LOYD30918352642068\n'
                    'BIC/SWIFT: LOYDGB21236'
                ),
            },
            {
                'label': '🇺🇸 Zelle (United States)',
                'details': 'bookings@joedavisarts.com',
            },
        ],
    },
    {
        'group': 'ALTERNATE',
        'items': [
            {
                'label': '🇺🇸 Venmo (America)',
                'details': '@joedavismusic',
            },
            {
                'label': '🇯🇲🇬🇧🇺🇸 PayPal (Worldwide)',
                'details': 'paypal.me/joedavisartsnew',
            },
            {
                'label': '🇯🇲🇬🇧🇺🇸 Bank Transfer (Worldwide, US Bank)',
                'details': (
                    'Joseph Lucas Fonsil Davis\n'
                    'Community Federal Savings Bank\n'
                    'Account Number: 822001057565\n'
                    'ACH and Wire Routing: 026073150\n'
                    'Account Type: Checking\n'
                    'Swift/BIC: CMFGUS33\n'
                    '89-16 Jamaica Avenue, Woodhaven, NY 11421, United States'
                ),
            },
        ],
    },
])

VERLANDO_SOCIAL_LINKS = json.dumps([
    {'label': 'Instagram', 'url': 'https://www.instagram.com/verlandosmall'},
    {'label': 'TikTok',    'url': 'https://www.tiktok.com/@verlandosmall'},
    {'label': 'YouTube',   'url': 'https://www.youtube.com/@verlandosmall'},
])

VERLANDO_PAYMENT_METHODS = json.dumps([
    {
        'group': 'PREFERRED',
        'items': [
            {
                'label': 'Bank Transfer — Scotiabank',
                'details': (
                    'Name on Account: VERLANDO SMALL MUSIC\n'
                    'Bank of Nova Scotia (Scotiabank)\n'
                    'Account Type: Business/Checking\n'
                    'Branch: Constant Spring Financial Centre\n'
                    'Branch Transit Code: 21725\n'
                    'Account Number: 000074819\n'
                    'Currency: USD'
                ),
            },
            {
                'label': 'Zelle',
                'details': '8452648461',
            },
        ],
    },
])


CHROMATICS_PASSWORD = 'chr0m@t!c5'  # hashed immediately below; plain text only here

CHROMATICS_SOCIAL_LINKS = json.dumps([
    {'label': 'Instagram', 'url': 'https://www.instagram.com/chromaticscreativestudios'},
    {'label': 'YouTube',   'url': 'https://www.youtube.com/@chromaticscreativestudios'},
    {'label': 'TikTok',    'url': 'https://www.tiktok.com/@chromaticscreativestudios'},
])

CHROMATICS_PAYMENT_METHODS = json.dumps([
    {
        'group': 'Preferred',
        'items': [
            {
                'label': 'Bank Transfer (Jamaica)',
                'details': (
                    'Joseph Lucas Fonsil Davis\n'
                    'Bank of Nova Scotia (Scotiabank)\n'
                    'Branch/Transit: 90365\n'
                    'Account Type: Savings\n'
                    'Account Number: 90365 000853610\n'
                    'Currency: Jamaican Dollars'
                ),
            },
            {
                'label': 'Bank Transfer (England)',
                'details': (
                    'Joseph Davis\n'
                    'Sort Code: 30-91-83\n'
                    'Account Number: 52642068\n'
                    'IBAN: GB55LOYD30918352642068\n'
                    'BIC/SWIFT: LOYDGB21236'
                ),
            },
            {
                'label': 'Zelle (United States)',
                'details': 'josephdavismusic@gmail.com',
            },
        ],
    },
    {
        'group': 'Alternative',
        'items': [
            {
                'label': 'Bank Transfer (US)',
                'details': (
                    'Joseph Lucas Fonsil Davis\n'
                    'Community Federal Savings Bank\n'
                    'Account Number: 822001057565\n'
                    'ACH and Wire Routing: 026073150\n'
                    'Account Type: Checking\n'
                    'Swift/BIC: CMFGUS33\n'
                    '89-16 Jamaica Avenue, Woodhaven, NY 11421, United States'
                ),
            },
            {
                'label': 'PayPal',
                'details': 'paypal.me/joedavisartsnew',
            },
            {
                'label': 'Venmo',
                'details': '@joedavismusic',
            },
            {
                'label': 'Cash App',
                'details': '$joedavismusic',
            },
        ],
    },
])


def migrate():
    db = get_db()
    c = db.cursor()
    print(f'Database: {DATA_DIR}/ledger.db')

    # ------------------------------------------------------------------
    # 1. Create users table (idempotent)
    # ------------------------------------------------------------------
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            username             TEXT NOT NULL UNIQUE,
            password_hash        TEXT NOT NULL,
            email                TEXT NOT NULL,
            display_name         TEXT NOT NULL,
            title                TEXT,
            business_name        TEXT NOT NULL,
            business_website     TEXT,
            business_email       TEXT,
            business_phone       TEXT,
            address_line1        TEXT,
            address_line2        TEXT,
            address_country      TEXT,
            accent_color         TEXT NOT NULL DEFAULT '#DAB322',
            accent_color_dark    TEXT NOT NULL DEFAULT '#77600B',
            logo_filename        TEXT DEFAULT 'logo.png',
            logotype_filename    TEXT,
            doc_prefix_invoice   TEXT DEFAULT 'INV',
            doc_prefix_quote     TEXT DEFAULT 'QT',
            doc_prefix_receipt   TEXT DEFAULT 'RCP',
            social_links_json    TEXT DEFAULT '[]',
            payment_methods_json TEXT DEFAULT '[]',
            gmail_token          TEXT,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    print('✓ users table exists')

    # ------------------------------------------------------------------
    # 2. Insert Joe (skip if already present)
    # ------------------------------------------------------------------
    if not c.execute("SELECT id FROM users WHERE username='joe'").fetchone():
        # Migrate existing token.json into gmail_token
        token_path = os.path.join(DATA_DIR, 'token.json')
        gmail_token = None
        if os.path.exists(token_path):
            with open(token_path) as f:
                gmail_token = f.read().strip()
            print(f'  Reading Gmail token from {token_path}')
        else:
            print(f'  No token.json found at {token_path} — Joe will need to re-authorise Gmail.')

        c.execute(
            """INSERT INTO users (
                username, password_hash, email, display_name, title,
                business_name, business_website, business_email, business_phone,
                address_line1, address_line2, address_country,
                accent_color, accent_color_dark,
                logo_filename, logotype_filename,
                doc_prefix_invoice, doc_prefix_quote, doc_prefix_receipt,
                social_links_json, payment_methods_json, gmail_token
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                'joe',
                generate_password_hash(JOE_PASSWORD),
                'bookings@joedavisarts.com',
                'Joe Davis',
                'Musical Director & Multi-Instrumentalist',
                'Joe Davis Arts & Media',
                'www.joedavisarts.com',
                'bookings@joedavisarts.com',
                '+1 (876) 897-2446',
                '16 Liguanea Way',
                'Liguanea, Kingston',
                'Jamaica, W.I.',
                '#DAB322',
                '#77600B',
                'logo.png',
                'LogoType_Gold.png',
                'JDAMI',
                'JDAMQ',
                'JDAMR',
                JOE_SOCIAL_LINKS,
                JOE_PAYMENT_METHODS,
                gmail_token,
            ),
        )
        db.commit()
        print('✓ Inserted user: joe')
    else:
        print('- joe already exists, skipping insert')

    joe_id = c.execute("SELECT id FROM users WHERE username='joe'").fetchone()['id']

    # ------------------------------------------------------------------
    # 3. Insert Verlando (skip if already present)
    # ------------------------------------------------------------------
    if not c.execute("SELECT id FROM users WHERE username='aureum'").fetchone():
        c.execute(
            """INSERT INTO users (
                username, password_hash, email, display_name, title,
                business_name, business_website, business_email, business_phone,
                address_line1, address_line2, address_country,
                accent_color, accent_color_dark,
                logo_filename, logotype_filename,
                doc_prefix_invoice, doc_prefix_quote, doc_prefix_receipt,
                social_links_json, payment_methods_json, gmail_token
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                'aureum',
                generate_password_hash(VERLANDO_PASSWORD),
                'verlandosmallmusic@gmail.com',
                'Verlando Small',
                'Professional Saxophonist',
                'Aureum Luxe Entertainment',
                '',
                'info@aureumluxent.com',
                '876 506 1236',
                'Portmore, Jamaica',
                '',
                '',
                '#ffbb66',
                '#996c33',
                'aureum_luxe_logo.png',
                None,
                'ALEINV',
                'ALEQT',
                'ALERCP',
                VERLANDO_SOCIAL_LINKS,
                VERLANDO_PAYMENT_METHODS,
                None,  # Verlando authorises Gmail separately via /auth/google
            ),
        )
        db.commit()
        print('✓ Inserted user: aureum (Verlando)')
    else:
        print('- aureum already exists, skipping insert')

    # ------------------------------------------------------------------
    # 3b. Insert Chromatics (skip if already present)
    # ------------------------------------------------------------------
    if not c.execute("SELECT id FROM users WHERE username='chromatics'").fetchone():
        c.execute(
            """INSERT INTO users (
                username, password_hash, email, display_name, title,
                business_name, business_website, business_email, business_phone,
                address_line1, address_line2, address_country,
                accent_color, accent_color_dark,
                logo_filename, logotype_filename,
                doc_prefix_invoice, doc_prefix_quote, doc_prefix_receipt,
                social_links_json, payment_methods_json, gmail_token
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                'chromatics',
                generate_password_hash(CHROMATICS_PASSWORD),
                'contact@bychromatics.com',
                'Chromatics Creative Studios',
                '',
                'Chromatics Creative Studios',
                '',
                'contact@bychromatics.com',
                None,  # no phone on Chromatics docs
                '5 Balmoral Avenue',
                'Half Way Tree, Kingston 5',
                'St. Andrew, Jamaica',
                '#DAB322',
                '#77600B',
                'chromatics_logo.png',
                None,
                'CCSI',
                'CCSQ',
                'CCSR',
                CHROMATICS_SOCIAL_LINKS,
                CHROMATICS_PAYMENT_METHODS,
                None,  # Gmail auth handled separately
            ),
        )
        db.commit()
        print('✓ Inserted user: chromatics')
    else:
        print('- chromatics already exists, skipping insert')

    # 3c. Correct chromatics user fields (idempotent)
    c.execute("""UPDATE users SET
        email               = 'contact@bychromatics.com',
        display_name        = 'Chromatics Creative Studios',
        title               = '',
        business_name       = 'Chromatics Creative Studios',
        business_email      = 'contact@bychromatics.com',
        business_phone      = NULL,
        address_line1       = '5 Balmoral Avenue',
        address_line2       = 'Half Way Tree, Kingston 5',
        address_country     = 'St. Andrew, Jamaica',
        accent_color        = '#DAB322',
        accent_color_dark   = '#77600B'
    WHERE username = 'chromatics'""")
    db.commit()
    print('\u2713 Corrected chromatics user fields')

    # ------------------------------------------------------------------
    # 4. Add user_id columns to clients / documents / item_library
    # ------------------------------------------------------------------
    def _add_col(table, col, definition):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            print(f'✓ Added {table}.{col}')
        else:
            print(f'- {table}.{col} already exists')

    _add_col('clients',      'user_id', 'INTEGER REFERENCES users(id)')
    _add_col('documents',    'user_id', 'INTEGER REFERENCES users(id)')
    _add_col('item_library', 'user_id', 'INTEGER REFERENCES users(id)')

    # ------------------------------------------------------------------
    # 5. Assign all un-owned rows to Joe
    # ------------------------------------------------------------------
    for table in ('clients', 'documents', 'item_library'):
        result = c.execute(
            f"UPDATE {table} SET user_id=? WHERE user_id IS NULL", (joe_id,)
        )
        if result.rowcount:
            print(f'✓ Assigned {result.rowcount} orphaned {table} rows to joe (id={joe_id})')
    db.commit()

    # ------------------------------------------------------------------
    # 6. Rebuild item_library with per-user unique constraint if needed
    # ------------------------------------------------------------------
    # The old schema has UNIQUE(description); we need UNIQUE(description, user_id).
    # Check by inspecting the index list.
    indexes = [r[1] for r in c.execute(
        "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='item_library'"
    ).fetchall()]
    needs_rebuild = not any('user_id' in idx for idx in indexes)

    if needs_rebuild:
        print('Rebuilding item_library with per-user unique constraint...')
        c.executescript("""
            CREATE TABLE item_library_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER REFERENCES users(id),
                name          TEXT NOT NULL,
                description   TEXT NOT NULL,
                default_price REAL,
                currency      TEXT DEFAULT 'USD',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(description, user_id)
            );
            INSERT INTO item_library_new
                (id, user_id, name, description, default_price, currency, created_at)
            SELECT id, user_id, name, description, default_price, currency, created_at
            FROM item_library;
            DROP TABLE item_library;
            ALTER TABLE item_library_new RENAME TO item_library;
        """)
        db.commit()
        print('✓ item_library rebuilt')
    else:
        print('- item_library unique constraint already per-user')

    # ------------------------------------------------------------------
    # 7. Rebuild doc_counter with per-user schema if needed
    # ------------------------------------------------------------------
    doc_counter_cols = [r[1] for r in c.execute(
        "PRAGMA table_info(doc_counter)"
    ).fetchall()]

    if 'user_id' not in doc_counter_cols:
        print('Rebuilding doc_counter with per-user schema...')
        c.executescript("""
            CREATE TABLE doc_counter_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_type    TEXT NOT NULL,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                last_number INTEGER DEFAULT 0,
                UNIQUE(doc_type, user_id)
            );
        """)
        # Copy Joe's existing counters
        c.execute(
            f"INSERT INTO doc_counter_new (doc_type, user_id, last_number) "
            f"SELECT doc_type, {joe_id}, last_number FROM doc_counter"
        )
        c.executescript("""
            DROP TABLE doc_counter;
            ALTER TABLE doc_counter_new RENAME TO doc_counter;
        """)
        db.commit()
        print('✓ doc_counter rebuilt')
    else:
        print('- doc_counter already has user_id column')

    # ------------------------------------------------------------------
    # 8. Seed / enforce minimum counter values for Joe
    # ------------------------------------------------------------------
    COUNTER_MINS = {'invoice': 199, 'quote': 120, 'receipt': 120}
    for doc_type, min_val in COUNTER_MINS.items():
        c.execute(
            "INSERT OR IGNORE INTO doc_counter (doc_type, user_id, last_number) "
            "VALUES (?, ?, ?)",
            (doc_type, joe_id, min_val),
        )
        c.execute(
            "UPDATE doc_counter SET last_number=? "
            "WHERE doc_type=? AND user_id=? AND last_number < ?",
            (min_val, doc_type, joe_id, min_val),
        )
    db.commit()
    print('✓ doc_counter seeded for joe')

    # ------------------------------------------------------------------
    # 9. Seed / enforce doc_counter starting point for Chromatics
    # ------------------------------------------------------------------
    chromatics_row = c.execute("SELECT id FROM users WHERE username='chromatics'").fetchone()
    if chromatics_row:
        chromatics_id = chromatics_row['id']
        CHROMATICS_COUNTER_SEED = {'invoice': 1110, 'quote': 1110, 'receipt': 1110}
        for doc_type, seed_val in CHROMATICS_COUNTER_SEED.items():
            c.execute(
                "INSERT OR IGNORE INTO doc_counter (doc_type, user_id, last_number) "
                "VALUES (?, ?, ?)",
                (doc_type, chromatics_id, seed_val),
            )
            c.execute(
                "UPDATE doc_counter SET last_number=? "
                "WHERE doc_type=? AND user_id=? AND last_number < ?",
                (seed_val, doc_type, chromatics_id, seed_val),
            )
        db.commit()
        print('✓ doc_counter seeded for chromatics (starts at 1111)')
    else:
        print('! chromatics user not found — skipping doc_counter seed')

    db.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    migrate()
