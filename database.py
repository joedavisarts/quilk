import sqlite3
import os

# DATA_DIR can be pointed at a Railway persistent volume (e.g. /data)
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
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            address_line1 TEXT,
            address_line2 TEXT,
            city TEXT,
            country TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            template_name TEXT NOT NULL,
            service_description TEXT,
            unit_price REAL,
            currency TEXT DEFAULT 'USD',
            qty REAL DEFAULT 1,
            discount REAL DEFAULT 0,
            tax_rate REAL DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL CHECK(doc_type IN ('invoice','quote','receipt')),
            doc_number TEXT NOT NULL UNIQUE,
            client_id INTEGER REFERENCES clients(id),
            date_issued TEXT NOT NULL,
            currency TEXT DEFAULT 'USD',
            line_items TEXT NOT NULL DEFAULT '[]',
            subtotal REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            tax_amount REAL DEFAULT 0,
            paid_amount REAL DEFAULT 0,
            amount_due REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doc_counter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL UNIQUE,
            last_number INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS item_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL UNIQUE,
            default_price REAL,
            currency TEXT DEFAULT 'USD',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER REFERENCES documents(id),
            recipient_email TEXT,
            subject TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'sent',
            error_message TEXT
        );
    """)

    # Add columns to documents if they don't exist
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(documents)").fetchall()]
    if 'pay_by_date' not in existing_cols:
        c.execute("ALTER TABLE documents ADD COLUMN pay_by_date TEXT")
    if 'source_document_id' not in existing_cols:
        c.execute("ALTER TABLE documents ADD COLUMN source_document_id INTEGER DEFAULT NULL REFERENCES documents(id)")
    # Migrate legacy statuses
    c.execute("UPDATE documents SET status='pending' WHERE status IN ('draft', 'issued')")

    # Seed doc_counter with minimum starting values (Section 7)
    counter_mins = {'invoice': 199, 'quote': 120, 'receipt': 120}
    for doc_type, min_val in counter_mins.items():
        existing = c.execute(
            "SELECT last_number FROM doc_counter WHERE doc_type=?", (doc_type,)
        ).fetchone()
        if existing is None:
            c.execute(
                "INSERT INTO doc_counter (doc_type, last_number) VALUES (?, ?)",
                (doc_type, min_val)
            )
        elif existing['last_number'] < min_val:
            c.execute(
                "UPDATE doc_counter SET last_number=? WHERE doc_type=?",
                (min_val, doc_type)
            )

    conn.commit()
    conn.close()


def next_doc_number(doc_type: str) -> str:
    prefixes = {'invoice': 'JDAMI', 'quote': 'JDAMQ', 'receipt': 'JDAMR'}
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE doc_counter SET last_number = last_number + 1 WHERE doc_type = ?",
        (doc_type,)
    )
    row = c.execute(
        "SELECT last_number FROM doc_counter WHERE doc_type = ?", (doc_type,)
    ).fetchone()
    conn.commit()
    conn.close()
    return f"{prefixes[doc_type]}{row['last_number']:03d}"
