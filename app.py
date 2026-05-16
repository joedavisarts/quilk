import base64
import json
import csv
import io
import os
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders

from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, send_file, abort, flash, session)

from database import get_db, init_db, next_doc_number
from pdf_generator import generate_pdf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'jdam-ledger-secret-2026')

# ---------------------------------------------------------------------------
# Auth config — override with AUTH_USERNAME / AUTH_PASSWORD env vars
# ---------------------------------------------------------------------------
AUTH_USERNAME = os.environ.get('AUTH_USERNAME', 'joe')
AUTH_PASSWORD = os.environ.get('AUTH_PASSWORD', 'jdam2026')


@app.before_request
def require_login():
    if request.endpoint in ('login', 'logout', 'static'):
        return
    if not session.get('logged_in'):
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if (request.form.get('username') == AUTH_USERNAME and
                request.form.get('password') == AUTH_PASSWORD):
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.template_filter('format_currency')
def format_currency_filter(value):
    try:
        return '{:,.2f}'.format(float(value))
    except (ValueError, TypeError):
        return '0.00'

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'assets')
LOGO_PATH = os.path.join(ASSETS_DIR, 'logo.png')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row):
    return dict(row) if row else None


def _rows_to_list(rows):
    return [dict(r) for r in rows]


def _parse_line_items(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return raw or []


def _compute_totals(line_items, discount_val, discount_type,
                    tax_val, tax_type, paid_amount):
    subtotal = sum(item.get('qty', 1) * item.get('unit_price', 0)
                   for item in line_items)

    if discount_type == 'percent':
        discount = subtotal * (discount_val / 100)
    else:
        discount = discount_val

    after_discount = subtotal - discount

    if tax_type == 'percent':
        tax_amount = after_discount * (tax_val / 100)
    else:
        tax_amount = tax_val

    amount_due = after_discount + tax_amount - paid_amount
    return subtotal, discount, tax_amount, amount_due


def _upsert_item_library(db, description, unit_price, currency):
    """Save or update an item in the item library by description."""
    if not description or not description.strip():
        return
    desc = description.strip()
    db.execute(
        """INSERT INTO item_library (name, description, default_price, currency)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(description) DO UPDATE SET
             default_price=excluded.default_price,
             currency=excluded.currency""",
        (desc, desc, unit_price, currency)
    )


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def _get_gmail_service():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError('Google API packages not installed. Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client')

    from database import DATA_DIR
    scopes = ['https://www.googleapis.com/auth/gmail.send']
    token_path = os.path.join(DATA_DIR, 'token.json')
    creds_path = os.path.join(DATA_DIR, 'credentials.json')

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise RuntimeError('credentials.json not found. Please add your Google OAuth credentials.')
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def _email_body_text(first_name, doc_number, doc_type):
    sign_off = (
        "\n\nWarmest Regards,\n"
        "Joe Davis\n"
        "Musical Director & Multi-Instrumentalist\n\n"
        "Joe Davis Arts & Media\n"
        "www.joedavisarts.com"
    )
    bodies = {
        'invoice': (
            f"Good day,\n\n"
            f"Please find your invoice attached, document number {doc_number}, for your records.\n\n"
            f"Kindly confirm receipt of this email, and please reply directly with any questions."
            f"{sign_off}"
        ),
        'quote': (
            f"Good day,\n\n"
            f"Please find your quote attached, document number {doc_number}, for your records. "
            f"This quote is valid for two weeks from the date of issue.\n\n"
            f"Kindly confirm receipt of this email, and please reply directly with any questions."
            f"{sign_off}"
        ),
        'receipt': (
            f"Good day,\n\n"
            f"Please find your receipt attached, document number {doc_number}, for your records.\n\n"
            f"It has been a pleasure. Kindly confirm receipt of this email, and feel free to reply directly with any questions."
            f"{sign_off}"
        ),
    }
    return bodies[doc_type]


def build_html_email(first_name, doc_number, doc_type, logo_path=None):
    """Build HTML + plain-text email body. Returns (html_str, plain_str)."""
    plain = _email_body_text(first_name, doc_number, doc_type)

    preheaders = {
        'invoice': f'Please find your invoice from Joe Davis Arts &amp; Media attached.',
        'quote': f'Please find your quote from Joe Davis Arts &amp; Media attached.',
        'receipt': f'Please find your receipt from Joe Davis Arts &amp; Media attached.',
    }

    body_paras = {
        'invoice': (
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;\">"
            f"Please find your invoice attached, document number <strong>{doc_number}</strong>, for your records.</p>"
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;\">"
            "Kindly confirm receipt of this email, and please reply directly with any questions.</p>"
        ),
        'quote': (
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;\">"
            f"Please find your quote attached, document number <strong>{doc_number}</strong>, for your records. "
            "This quote is valid for two weeks from the date of issue.</p>"
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;\">"
            "Kindly confirm receipt of this email, and please reply directly with any questions.</p>"
        ),
        'receipt': (
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;\">"
            f"Please find your receipt attached, document number <strong>{doc_number}</strong>, for your records.</p>"
            f"<p style=\"font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;\">"
            "It has been a pleasure. Kindly confirm receipt of this email, and feel free to reply directly with any questions.</p>"
        ),
    }

    if logo_path and os.path.exists(logo_path):
        logo_html = '<img src="cid:logo" width="48" height="48" alt="Joe Davis Arts &amp; Media" style="display:block;" />'
    else:
        logo_html = '<div style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;color:#DAB322;font-size:14px;font-weight:bold;letter-spacing:0.1em;">JOE DAVIS ARTS &amp; MEDIA</div>'

    html = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Joe Davis Arts &amp; Media</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600&amp;display=swap" rel="stylesheet" type="text/css" />
<!--[if mso]><style type="text/css">body,table,td{{font-family:Arial,sans-serif !important;}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f0f0f0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheaders[doc_type]}&nbsp;&#847;&nbsp;</div>
<table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#f0f0f0;">
  <tr><td align="center" style="padding:24px 12px;">
    <table border="0" cellpadding="0" cellspacing="0" width="600" style="max-width:600px;background-color:#ffffff;border-radius:4px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
      <!-- Top bar -->
      <tr><td height="4" style="background-color:#DAB322;font-size:0;line-height:0;">&nbsp;</td></tr>
      <!-- Header -->
      <tr><td style="padding:32px 40px 0 40px;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td style="vertical-align:middle;width:60px;">{logo_html}</td>
            <td style="vertical-align:middle;text-align:right;padding-left:16px;">
              <div style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;color:#77600B;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;font-weight:600;line-height:1.4;">JOE DAVIS ARTS &amp; MEDIA</div>
            </td>
          </tr>
        </table>
      </td></tr>
      <!-- Body -->
      <tr><td style="padding:36px 40px 8px 40px;">
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 20px 0;">Good day,</p>
        {body_paras[doc_type]}
      </td></tr>
      <!-- Sign-off -->
      <tr><td style="padding:24px 40px 32px 40px;">
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 4px 0;">Warmest Regards,</p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;font-weight:700;line-height:26px;margin:0 0 4px 0;">Joe Davis</p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:13px;color:#77600B;font-weight:600;letter-spacing:0.04em;margin:0;">Musical Director &amp; Multi-Instrumentalist</p>
      </td></tr>
      <!-- Divider -->
      <tr><td style="padding:0 40px;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr><td height="1" style="background-color:#f0f0f0;font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
      </td></tr>
      <!-- Footer -->
      <tr><td style="background-color:#0E0E0E;padding:28px 40px;border-radius:0 0 4px 4px;">
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:12px;text-align:center;margin:0 0 12px 0;">
          <a href="https://www.joedavisarts.com" style="color:#DAB322;text-decoration:none;letter-spacing:0.06em;">www.joedavisarts.com</a>
        </p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:11px;color:#888888;text-align:center;margin:0 0 14px 0;">
          <a href="https://www.instagram.com/joedavismusic" style="color:#888888;text-decoration:none;">Instagram</a>
          <span style="color:#333333;">&nbsp;&middot;&nbsp;</span>
          <a href="https://www.youtube.com/JoeDavisMusic" style="color:#888888;text-decoration:none;">YouTube</a>
          <span style="color:#333333;">&nbsp;&middot;&nbsp;</span>
          <a href="https://www.tiktok.com/@joedavismusic" style="color:#888888;text-decoration:none;">TikTok</a>
          <span style="color:#333333;">&nbsp;&middot;&nbsp;</span>
          <a href="https://jm.linkedin.com/in/joedavisarts" style="color:#888888;text-decoration:none;">LinkedIn</a>
        </p>
        <table border="0" cellpadding="0" cellspacing="0" align="center" width="40%">
          <tr><td height="1" style="background-color:#77600B;font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:11px;color:#555555;text-align:center;margin:12px 0 4px 0;">This email was sent by Joe Davis Arts &amp; Media.</p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:11px;color:#555555;text-align:center;margin:0;">Any questions? Just reply directly.</p>
      </td></tr>
      <!-- Bottom bar -->
      <tr><td height="4" style="background-color:#77600B;font-size:0;line-height:0;">&nbsp;</td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    return html, plain


def _build_mime_message(to_email, subject, html_body, plain_text,
                        pdf_bytes, pdf_filename, logo_path=None):
    msg = MIMEMultipart('related')
    msg['From'] = 'Joe Davis Arts & Media <bookings@joedavisarts.com>'
    msg['To'] = to_email
    msg['Subject'] = subject
    msg['Reply-To'] = 'bookings@joedavisarts.com'
    msg['X-Mailer'] = 'Ledger - Joe Davis Arts & Media'

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(plain_text, 'plain', 'utf-8'))
    alt.attach(MIMEText(html_body, 'html', 'utf-8'))
    msg.attach(alt)

    if logo_path and os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_data = f.read()
        logo_mime = MIMEImage(logo_data)
        logo_mime.add_header('Content-ID', '<logo>')
        logo_mime.add_header('Content-Disposition', 'inline', filename='logo.png')
        msg.attach(logo_mime)

    pdf_part = MIMEBase('application', 'pdf')
    pdf_part.set_payload(pdf_bytes)
    encoders.encode_base64(pdf_part)
    pdf_part.add_header('Content-Type', 'application/pdf', name=pdf_filename)
    pdf_part.add_header('Content-Disposition', 'attachment', filename=pdf_filename)
    msg.attach(pdf_part)

    return msg


def _pdf_email_filename(doc_number):
    return doc_number + '.pdf'


def _doc_subject(doc_type):
    return {
        'invoice': 'Confidential: Invoice - Joe Davis Arts & Media',
        'quote': 'Confidential: Quote - Joe Davis Arts & Media',
        'receipt': 'Important: Receipt - Joe Davis Arts & Media',
    }[doc_type]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def dashboard():
    db = get_db()
    recent = _rows_to_list(
        db.execute(
            "SELECT d.*, c.name AS client_name FROM documents d "
            "LEFT JOIN clients c ON d.client_id = c.id "
            "ORDER BY d.created_at DESC LIMIT 10"
        ).fetchall()
    )
    counts = {}
    for dt in ('invoice', 'quote', 'receipt'):
        row = db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE doc_type=?", (dt,)
        ).fetchone()
        counts[dt] = row['n']
    db.close()
    return render_template('dashboard.html', recent=recent, counts=counts)


# ---------------------------------------------------------------------------
# New Document
# ---------------------------------------------------------------------------

@app.route('/new/<doc_type>', methods=['GET', 'POST'])
def new_document(doc_type):
    if doc_type not in ('invoice', 'quote', 'receipt'):
        abort(404)

    db = get_db()

    if request.method == 'POST':
        data = request.form
        client_id = data.get('client_id') or None

        # Inline client creation
        if data.get('new_client_name'):
            cur = db.execute(
                "INSERT INTO clients (name,email,phone,address_line1,"
                "address_line2,city,country,notes) VALUES (?,?,?,?,?,?,?,?)",
                (data['new_client_name'], data.get('new_client_email'),
                 data.get('new_client_phone'), data.get('new_client_addr1'),
                 data.get('new_client_addr2'), data.get('new_client_city'),
                 data.get('new_client_country'), data.get('new_client_notes'))
            )
            client_id = cur.lastrowid
            db.commit()

        if not client_id:
            db.close()
            return jsonify({'error': 'Client required'}), 400

        line_items = json.loads(data.get('line_items_json', '[]'))
        discount_val = float(data.get('discount_val', 0))
        discount_type = data.get('discount_type', 'flat')
        tax_val = float(data.get('tax_val', 0))
        tax_type = data.get('tax_type', 'percent')
        paid_amount = float(data.get('paid_amount', 0))
        currency = data.get('currency', 'USD')
        doc_date = data.get('date_issued') or date.today().isoformat()
        notes = data.get('notes', '')
        pay_by_date = data.get('pay_by_date') or None

        subtotal, discount, tax_amount, amount_due = _compute_totals(
            line_items, discount_val, discount_type,
            tax_val, tax_type, paid_amount
        )

        doc_number = next_doc_number(doc_type)

        cur = db.execute(
            "INSERT INTO documents (doc_type,doc_number,client_id,date_issued,"
            "currency,line_items,subtotal,discount,tax_amount,paid_amount,"
            "amount_due,status,notes,pay_by_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_type, doc_number, client_id, doc_date, currency,
             json.dumps(line_items), subtotal, discount, tax_amount,
             paid_amount, amount_due, 'pending', notes, pay_by_date)
        )
        doc_id = cur.lastrowid
        db.commit()

        # Auto-save line items to item library
        for item in line_items:
            _upsert_item_library(
                db,
                item.get('description', ''),
                item.get('unit_price', 0),
                currency
            )
        db.commit()

        # Save template if requested
        if data.get('save_template_name') and line_items:
            first = line_items[0]
            db.execute(
                "INSERT INTO client_templates (client_id,template_name,"
                "service_description,unit_price,currency,qty,discount,"
                "tax_rate,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                (client_id, data['save_template_name'],
                 first.get('description'), first.get('unit_price'),
                 currency, first.get('qty', 1),
                 discount_val, tax_val, notes)
            )
            db.commit()

        db.close()
        return redirect(url_for('document_view', doc_id=doc_id))

    # GET
    clients = _rows_to_list(
        db.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    )
    db.close()
    today = date.today().isoformat()
    return render_template('new_document.html', doc_type=doc_type,
                           clients=clients, today=today)


# ---------------------------------------------------------------------------
# Client templates API
# ---------------------------------------------------------------------------

@app.route('/api/clients/<int:client_id>/templates')
def client_templates(client_id):
    db = get_db()
    rows = _rows_to_list(
        db.execute(
            "SELECT * FROM client_templates WHERE client_id=?", (client_id,)
        ).fetchall()
    )
    db.close()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Item library API
# ---------------------------------------------------------------------------

@app.route('/api/item-library/save', methods=['POST'])
def save_item_to_library():
    data = request.get_json(silent=True) or {}
    description = str(data.get('description', '')).strip()
    unit_price = float(data.get('unit_price', 0))
    currency = str(data.get('currency', 'USD'))

    if not description:
        return jsonify({'error': 'Description required'}), 400

    db = get_db()
    _upsert_item_library(db, description, unit_price, currency)
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Document search API (for Import Items modal)
# ---------------------------------------------------------------------------

@app.route('/api/documents/search')
def search_documents():
    q = request.args.get('q', '').strip()
    db = get_db()

    if q:
        rows = _rows_to_list(db.execute(
            "SELECT d.*, c.name AS client_name FROM documents d "
            "LEFT JOIN clients c ON d.client_id = c.id "
            "WHERE d.doc_number LIKE ? OR c.name LIKE ? "
            "ORDER BY d.created_at DESC LIMIT 50",
            (f'%{q}%', f'%{q}%')
        ).fetchall())
    else:
        rows = _rows_to_list(db.execute(
            "SELECT d.*, c.name AS client_name FROM documents d "
            "LEFT JOIN clients c ON d.client_id = c.id "
            "ORDER BY d.created_at DESC LIMIT 50"
        ).fetchall())

    for row in rows:
        row['line_items'] = _parse_line_items(row['line_items'])

    db.close()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.route('/clients')
def clients():
    db = get_db()
    rows = _rows_to_list(
        db.execute(
            "SELECT c.*, COUNT(d.id) AS doc_count "
            "FROM clients c LEFT JOIN documents d ON d.client_id = c.id "
            "GROUP BY c.id ORDER BY c.name"
        ).fetchall()
    )
    db.close()
    return render_template('clients.html', clients=rows)


@app.route('/clients/<int:client_id>')
def client_detail(client_id):
    db = get_db()
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    )
    if not client:
        abort(404)
    docs = _rows_to_list(
        db.execute(
            "SELECT * FROM documents WHERE client_id=? ORDER BY created_at DESC",
            (client_id,)
        ).fetchall()
    )
    templates = _rows_to_list(
        db.execute(
            "SELECT * FROM client_templates WHERE client_id=?", (client_id,)
        ).fetchall()
    )
    db.close()
    return render_template('client_detail.html', client=client,
                           docs=docs, templates=templates)


@app.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
def client_edit(client_id):
    db = get_db()
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    )
    if not client:
        abort(404)

    if request.method == 'POST':
        d = request.form
        db.execute(
            "UPDATE clients SET name=?,email=?,phone=?,address_line1=?,"
            "address_line2=?,city=?,country=?,notes=? WHERE id=?",
            (d['name'], d.get('email'), d.get('phone'),
             d.get('address_line1'), d.get('address_line2'),
             d.get('city'), d.get('country'), d.get('notes'), client_id)
        )
        db.commit()
        db.close()
        return redirect(url_for('client_detail', client_id=client_id))

    db.close()
    return render_template('client_edit.html', client=client)


@app.route('/clients/<int:client_id>/templates/<int:tmpl_id>/delete',
           methods=['POST'])
def delete_template(client_id, tmpl_id):
    db = get_db()
    db.execute("DELETE FROM client_templates WHERE id=? AND client_id=?",
               (tmpl_id, client_id))
    db.commit()
    db.close()
    return redirect(url_for('client_detail', client_id=client_id))


@app.route('/clients/<int:client_id>/export')
def export_client(client_id):
    db = get_db()
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    )
    if not client:
        abort(404)
    docs = _rows_to_list(
        db.execute(
            "SELECT * FROM documents WHERE client_id=? ORDER BY created_at DESC",
            (client_id,)
        ).fetchall()
    )
    for d in docs:
        d['line_items'] = _parse_line_items(d['line_items'])
    payload = json.dumps({'client': client, 'documents': docs}, indent=2)
    buf = io.BytesIO(payload.encode())
    buf.seek(0)
    filename = f"client_{client['name'].replace(' ','_')}_export.json"
    return send_file(buf, mimetype='application/json',
                     as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.route('/documents')
def documents():
    db = get_db()
    doc_type = request.args.get('type', '')
    status = request.args.get('status', '')
    query = ("SELECT d.*, c.name AS client_name FROM documents d "
             "LEFT JOIN clients c ON d.client_id = c.id WHERE 1=1")
    params = []
    if doc_type:
        query += " AND d.doc_type=?"
        params.append(doc_type)
    if status:
        query += " AND d.status=?"
        params.append(status)
    query += " ORDER BY d.created_at DESC"
    rows = _rows_to_list(db.execute(query, params).fetchall())
    db.close()
    return render_template('documents.html', documents=rows,
                           filter_type=doc_type, filter_status=status)


@app.route('/documents/<int:doc_id>')
def document_view(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    )
    if not doc:
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    )
    sent_logs = _rows_to_list(
        db.execute(
            "SELECT * FROM sent_log WHERE doc_id=? ORDER BY sent_at DESC",
            (doc_id,)
        ).fetchall()
    )
    # Check for linked documents
    linked_doc = _row_to_dict(
        db.execute(
            "SELECT id, doc_type FROM documents WHERE source_document_id=? LIMIT 1",
            (doc_id,)
        ).fetchone()
    )
    db.close()
    return render_template('document_view.html', doc=doc, client=client,
                           sent_logs=sent_logs, linked_doc=linked_doc)


@app.route('/documents/<int:doc_id>/pdf')
def document_pdf(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    )
    if not doc:
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    ) or {}
    db.close()

    pdf_bytes = generate_pdf(doc, client)
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    filename = f"{doc['doc_number']}.pdf"
    inline = request.args.get('download') != '1'
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=not inline, download_name=filename)


@app.route('/documents/<int:doc_id>/set_status', methods=['POST'])
def set_status(doc_id):
    status = request.form.get('status', '')
    if status:
        db = get_db()
        db.execute("UPDATE documents SET status=? WHERE id=?", (status, doc_id))
        db.commit()
        db.close()
    return redirect(url_for('document_view', doc_id=doc_id))


@app.route('/documents/<int:doc_id>/create_invoice', methods=['POST'])
def create_invoice_from_quote(doc_id):
    db = get_db()
    quote = _row_to_dict(
        db.execute("SELECT * FROM documents WHERE id=? AND doc_type='quote'", (doc_id,)).fetchone()
    )
    if not quote:
        db.close()
        abort(400)
    doc_number = next_doc_number('invoice')
    cur = db.execute(
        "INSERT INTO documents (doc_type,doc_number,client_id,date_issued,currency,"
        "line_items,subtotal,discount,tax_amount,paid_amount,amount_due,status,notes,source_document_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ('invoice', doc_number, quote['client_id'], date.today().isoformat(),
         quote['currency'], quote['line_items'], quote['subtotal'],
         quote['discount'], quote['tax_amount'], 0,
         quote['amount_due'], 'pending', quote['notes'], doc_id)
    )
    invoice_id = cur.lastrowid
    db.commit()
    db.close()
    return redirect(url_for('document_view', doc_id=invoice_id))


@app.route('/documents/bulk_delete', methods=['POST'])
def bulk_delete_documents():
    doc_ids = request.form.getlist('doc_ids')
    if doc_ids:
        ph = ','.join('?' * len(doc_ids))
        db = get_db()
        db.execute(f"DELETE FROM sent_log WHERE doc_id IN ({ph})", doc_ids)
        db.execute(f"UPDATE documents SET source_document_id=NULL WHERE source_document_id IN ({ph})", doc_ids)
        db.execute(f"DELETE FROM documents WHERE id IN ({ph})", doc_ids)
        db.commit()
        db.close()
    return redirect(url_for('documents'))


@app.route('/documents/<int:doc_id>/delete', methods=['POST'])
def delete_document(doc_id):
    db = get_db()
    db.execute("DELETE FROM sent_log WHERE doc_id=?", (doc_id,))
    db.execute("UPDATE documents SET source_document_id=NULL WHERE source_document_id=?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    db.close()
    return redirect(url_for('documents'))


@app.route('/documents/export_csv')
def export_csv():
    db = get_db()
    rows = _rows_to_list(
        db.execute(
            "SELECT d.*, c.name AS client_name FROM documents d "
            "LEFT JOIN clients c ON d.client_id = c.id "
            "ORDER BY d.created_at DESC"
        ).fetchall()
    )
    db.close()

    output = io.StringIO()
    fieldnames = ['doc_number', 'doc_type', 'client_name', 'date_issued',
                  'currency', 'subtotal', 'discount', 'tax_amount',
                  'paid_amount', 'amount_due', 'status', 'notes', 'created_at']
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

    buf = io.BytesIO(output.getvalue().encode())
    buf.seek(0)
    return send_file(buf, mimetype='text/csv',
                     as_attachment=True, download_name='documents_export.csv')


# ---------------------------------------------------------------------------
# Edit Document
# ---------------------------------------------------------------------------

@app.route('/documents/<int:doc_id>/edit', methods=['GET', 'POST'])
def edit_document(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    )
    if not doc:
        db.close()
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])

    if request.method == 'POST':
        data = request.form
        client_id = data.get('client_id') or doc['client_id']

        line_items = json.loads(data.get('line_items_json', '[]'))
        discount_val = float(data.get('discount_val', 0))
        discount_type = data.get('discount_type', 'flat')
        tax_val = float(data.get('tax_val', 0))
        tax_type = data.get('tax_type', 'percent')
        paid_amount = float(data.get('paid_amount', 0))
        currency = data.get('currency', 'USD')
        doc_date = data.get('date_issued') or doc['date_issued']
        notes = data.get('notes', '')
        pay_by_date = data.get('pay_by_date') or None
        status = data.get('status', doc['status'])

        subtotal, discount, tax_amount, amount_due = _compute_totals(
            line_items, discount_val, discount_type,
            tax_val, tax_type, paid_amount
        )

        db.execute(
            "UPDATE documents SET client_id=?,date_issued=?,currency=?,line_items=?,"
            "subtotal=?,discount=?,tax_amount=?,paid_amount=?,amount_due=?,"
            "notes=?,pay_by_date=?,status=? WHERE id=?",
            (client_id, doc_date, currency, json.dumps(line_items),
             subtotal, discount, tax_amount, paid_amount, amount_due,
             notes, pay_by_date, status, doc_id)
        )
        db.commit()
        db.close()

        flash('Document updated successfully.', 'success')
        return redirect(url_for('document_view', doc_id=doc_id))

    clients = _rows_to_list(
        db.execute("SELECT id, name FROM clients ORDER BY name").fetchall()
    )
    db.close()
    return render_template('edit_document.html', doc=doc, clients=clients)


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

@app.route('/documents/<int:doc_id>/send', methods=['GET', 'POST'])
def send_document(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    )
    if not doc:
        db.close()
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    ) or {}
    db.close()

    first_name = (client.get('name') or '').split()[0] if client.get('name') else 'there'
    subject = _doc_subject(doc['doc_type'])
    email_body = _email_body_text(first_name, doc['doc_number'], doc['doc_type'])
    pdf_filename = _pdf_email_filename(doc['doc_number'])
    to_email = client.get('email', '')

    if request.method == 'GET':
        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject=subject, email_body=email_body,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               error=None)

    # POST
    to_email = request.form.get('to_email', '').strip()
    subject = request.form.get('subject', subject).strip()

    db = get_db()
    try:
        pdf_bytes = generate_pdf(doc, client)
        html_body, plain_text = build_html_email(
            first_name, doc['doc_number'], doc['doc_type'], LOGO_PATH
        )
        msg = _build_mime_message(
            to_email, subject, html_body, plain_text,
            pdf_bytes, pdf_filename, LOGO_PATH
        )

        gmail = _get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(userId='me', body={'raw': raw}).execute()

        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status) VALUES (?,?,?,?)",
            (doc_id, to_email, subject, 'sent')
        )
        db.commit()
        db.close()

        flash(f'Email sent to {to_email} on {datetime.now().strftime("%B %d, %Y at %H:%M")}.', 'success')
        return redirect(url_for('document_view', doc_id=doc_id))

    except Exception as e:
        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status,error_message) VALUES (?,?,?,?,?)",
            (doc_id, to_email, subject, 'failed', str(e))
        )
        db.commit()
        db.close()

        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject=subject, email_body=email_body,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               error=str(e))


# ---------------------------------------------------------------------------

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
