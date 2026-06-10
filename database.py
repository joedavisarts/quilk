import sqlite3
import os

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(__file__))
DB_PATH = os.path.join(DATA_DIR, 'ledger.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

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

        CREATE TABLE IF NOT EXISTS clients (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER REFERENCES users(id),
            company_name   TEXT,
            name           TEXT NOT NULL,
            email          TEXT,
            phone          TEXT,
            address_line1  TEXT,
            address_line2  TEXT,
            city           TEXT,
            country        TEXT,
            notes          TEXT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_templates (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            template_name       TEXT NOT NULL,
            service_description TEXT,
            unit_price          REAL,
            currency            TEXT DEFAULT 'USD',
            qty                 REAL DEFAULT 1,
            discount            REAL DEFAULT 0,
            tax_rate            REAL DEFAULT 0,
            notes               TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER REFERENCES users(id),
            doc_type            TEXT NOT NULL CHECK(doc_type IN ('invoice','quote','receipt')),
            doc_number          TEXT NOT NULL UNIQUE,
            client_id           INTEGER REFERENCES clients(id),
            date_issued         TEXT NOT NULL,
            currency            TEXT DEFAULT 'USD',
            line_items          TEXT NOT NULL DEFAULT '[]',
            subtotal            REAL DEFAULT 0,
            discount            REAL DEFAULT 0,
            tax_amount          REAL DEFAULT 0,
            paid_amount         REAL DEFAULT 0,
            amount_due          REAL DEFAULT 0,
            status              TEXT DEFAULT 'draft',
            notes               TEXT,
            pay_by_date         TEXT,
            source_document_id  INTEGER DEFAULT NULL REFERENCES documents(id),
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doc_counter (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type    TEXT NOT NULL,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            last_number INTEGER DEFAULT 0,
            UNIQUE(doc_type, user_id)
        );

        CREATE TABLE IF NOT EXISTS item_library (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER REFERENCES users(id),
            name          TEXT NOT NULL,
            description   TEXT NOT NULL,
            default_price REAL,
            currency      TEXT DEFAULT 'USD',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(description, user_id)
        );

        CREATE TABLE IF NOT EXISTS sent_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id          INTEGER REFERENCES documents(id),
            recipient_email TEXT,
            subject         TEXT,
            sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status          TEXT DEFAULT 'sent',
            error_message   TEXT
        );

        CREATE TABLE IF NOT EXISTS email_templates (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id),
            doc_type         TEXT NOT NULL,
            subject_template TEXT NOT NULL,
            body_template    TEXT NOT NULL,
            UNIQUE(user_id, doc_type)
        );
    """)

    # Safely add columns that may be missing on pre-migration installs
    def _add_col(table, col, definition):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

    _add_col('documents', 'pay_by_date', 'TEXT')
    _add_col('documents', 'source_document_id',
             'INTEGER DEFAULT NULL REFERENCES documents(id)')
    _add_col('clients', 'user_id', 'INTEGER REFERENCES users(id)')
    _add_col('documents', 'user_id', 'INTEGER REFERENCES users(id)')
    _add_col('item_library', 'user_id', 'INTEGER REFERENCES users(id)')

    c.execute("UPDATE documents SET status='pending' WHERE status IN ('draft', 'issued')")

    conn.commit()
    conn.close()


def next_doc_number(doc_type: str, user_id: int, prefix: str) -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO doc_counter (doc_type, user_id, last_number) VALUES (?, ?, 0)",
        (doc_type, user_id),
    )
    c.execute(
        "UPDATE doc_counter SET last_number = last_number + 1 "
        "WHERE doc_type = ? AND user_id = ?",
        (doc_type, user_id),
    )
    row = c.execute(
        "SELECT last_number FROM doc_counter WHERE doc_type = ? AND user_id = ?",
        (doc_type, user_id),
    ).fetchone()
    conn.commit()
    conn.close()
    return f"{prefix}{row['last_number']:03d}"
