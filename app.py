import base64
import json
import csv
import io
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders

from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, send_file, abort, flash, session)
from flask_login import (LoginManager, UserMixin, login_required,
                         current_user, login_user, logout_user)
from werkzeug.security import check_password_hash

from database import get_db, init_db, next_doc_number
from pdf_generator import generate_pdf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'jdam-ledger-secret-2026')

login_manager = LoginManager(app)
login_manager.login_view = 'login'

ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'assets')


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, row):
        self._data = dict(row)
        for k, v in self._data.items():
            setattr(self, k, v)

    def get_id(self):
        return str(self.id)

    def to_dict(self):
        return dict(self._data)


@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    db.close()
    return User(row) if row else None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.before_request
def require_login():
    public = {'login', 'static', 'oauth2callback'}
    if request.endpoint in public:
        return
    if not current_user.is_authenticated:
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        db.close()
        if row and check_password_hash(row['password_hash'], password):
            login_user(User(row))
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)


@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.template_filter('format_currency')
def format_currency_filter(value):
    try:
        return '{:,.2f}'.format(float(value))
    except (ValueError, TypeError):
        return '0.00'


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


def _compute_totals(line_items, discount_val, discount_type, paid_amount):
    subtotal = sum(item.get('qty', 1) * item.get('unit_price', 0)
                   for item in line_items)

    if discount_type == 'percent':
        discount = subtotal * (discount_val / 100)
    else:
        discount = discount_val

    after_discount = subtotal - discount
    tax_amount = 0
    amount_due = after_discount - paid_amount
    return subtotal, discount, tax_amount, amount_due


def _upsert_item_library(db, description, unit_price, currency, user_id):
    if not description or not description.strip():
        return
    desc = description.strip()
    db.execute(
        """INSERT INTO item_library (name, description, default_price, currency, user_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(description, user_id) DO UPDATE SET
             default_price=excluded.default_price,
             currency=excluded.currency""",
        (desc, desc, unit_price, currency, user_id),
    )


def _client_display_labels(db, user_id):
    """Return {client_id: display_label} with disambiguation for colliding base labels."""
    rows = _rows_to_list(db.execute(
        "SELECT id, company_name, name, email, phone, address_line1"
        " FROM clients WHERE user_id=?", (user_id,)
    ).fetchall())
    for r in rows:
        r['_base'] = ((r.get('company_name') or '').strip() or
                       (r.get('name') or '').strip() or '')
    groups = defaultdict(list)
    for r in rows:
        groups[r['_base'].lower()].append(r)
    result = {}
    for _, group in groups.items():
        if len(group) == 1:
            result[group[0]['id']] = group[0]['_base']
        else:
            for c in group:
                base = c['_base']
                contact = (c.get('name') or '').strip()
                if contact:
                    all_same = all(
                        (g.get('name') or '').strip().lower() == contact.lower()
                        for g in group
                    )
                    if not all_same:
                        result[c['id']] = f"{base} ({contact})"
                        continue
                for field in ('email', 'phone', 'address_line1'):
                    val = (c.get(field) or '').strip()
                    field_vals = {(g.get(field) or '').strip().lower() for g in group}
                    if len(field_vals) > 1 and val:
                        result[c['id']] = f"{base} ({val})"
                        break
                else:
                    result[c['id']] = f"{base} (#{c['id']})"
    return result


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.send']


def _load_google_client_config():
    from database import DATA_DIR
    raw = os.environ.get('GOOGLE_CREDENTIALS')
    if raw:
        return json.loads(raw)
    creds_path = os.path.join(DATA_DIR, 'credentials.json')
    if os.path.exists(creds_path):
        with open(creds_path) as f:
            return json.load(f)
    raise RuntimeError(
        'Google credentials not found. Set GOOGLE_CREDENTIALS env var or add credentials.json.'
    )


def _get_gmail_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            'Google API packages not installed. '
            'Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client'
        )

    token_json = current_user.gmail_token
    if not token_json:
        raise RuntimeError(
            'Gmail not authorised. Visit /auth/google to connect your account.'
        )

    creds = Credentials.from_authorized_user_info(json.loads(token_json), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            refreshed = creds.to_json()
            db = get_db()
            db.execute(
                "UPDATE users SET gmail_token=? WHERE id=?",
                (refreshed, current_user.id),
            )
            db.commit()
            db.close()
        else:
            raise RuntimeError(
                'Gmail not authorised. Visit /auth/google to connect your account.'
            )

    return build('gmail', 'v1', credentials=creds)


@app.route('/auth/google')
def auth_google():
    from google_auth_oauthlib.flow import Flow
    redirect_uri = os.environ.get(
        'OAUTH_REDIRECT_URI', url_for('oauth2callback', _external=True)
    )
    flow = Flow.from_client_config(
        _load_google_client_config(), scopes=GMAIL_SCOPES, redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session['oauth_state'] = state
    session['oauth_code_verifier'] = flow.code_verifier
    return redirect(auth_url)


@app.route('/oauth2callback')
def oauth2callback():
    if not current_user.is_authenticated:
        flash('Please log in before connecting Gmail.', 'error')
        return redirect(url_for('login'))

    from google_auth_oauthlib.flow import Flow
    redirect_uri = os.environ.get(
        'OAUTH_REDIRECT_URI', url_for('oauth2callback', _external=True)
    )
    flow = Flow.from_client_config(
        _load_google_client_config(),
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        state=session['oauth_state'],
    )
    flow.code_verifier = session.get('oauth_code_verifier')
    flow.fetch_token(authorization_response=request.url)

    db = get_db()
    db.execute(
        "UPDATE users SET gmail_token=? WHERE id=?",
        (flow.credentials.to_json(), current_user.id),
    )
    db.commit()
    db.close()

    flash('Gmail connected successfully.', 'success')
    return redirect(url_for('dashboard'))


# ---------------------------------------------------------------------------
# Email building
# ---------------------------------------------------------------------------

def _email_body_text(first_name, doc_number, doc_type, user):
    sign_off = '\n\nWarmest Regards,\n' + user['display_name']
    if user.get('title'):
        sign_off += '\n' + user['title']
    sign_off += '\n\n' + user['business_name']
    if user.get('business_website'):
        sign_off += '\n' + user['business_website']

    bodies = {
        'invoice': (
            f'Good day,\n\n'
            f'Please find your invoice attached, document number {doc_number}, for your records.\n\n'
            f'Kindly confirm receipt of this email, and please reply directly with any questions.'
            f'{sign_off}'
        ),
        'quote': (
            f'Good day,\n\n'
            f'Please find your quote attached, document number {doc_number}, for your records. '
            f'This quote is valid for two weeks from the date of issue.\n\n'
            f'Kindly confirm receipt of this email, and please reply directly with any questions.'
            f'{sign_off}'
        ),
        'receipt': (
            f'Good day,\n\n'
            f'Please find your receipt attached, document number {doc_number}, for your records.\n\n'
            f'It has been a pleasure. Kindly confirm receipt of this email, and feel free to reply directly with any questions.'
            f'{sign_off}'
        ),
    }
    return bodies[doc_type]


def build_html_email(first_name, doc_number, doc_type, logo_path, user, body_text=None):
    biz = user['business_name']
    accent = user['accent_color']
    accent_dark = user['accent_color_dark']

    preheaders = {
        'invoice': f'Please find your invoice from {biz} attached.',
        'quote':   f'Please find your quote from {biz} attached.',
        'receipt': f'Please find your receipt from {biz} attached.',
    }

    body_paras = {
        'invoice': (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;">'
            f'Please find your invoice attached, document number <strong>{doc_number}</strong>, for your records.</p>'
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;">'
            'Kindly confirm receipt of this email, and please reply directly with any questions.</p>'
        ),
        'quote': (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;">'
            f'Please find your quote attached, document number <strong>{doc_number}</strong>, for your records. '
            'This quote is valid for two weeks from the date of issue.</p>'
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;">'
            'Kindly confirm receipt of this email, and please reply directly with any questions.</p>'
        ),
        'receipt': (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;">'
            f'Please find your receipt attached, document number <strong>{doc_number}</strong>, for your records.</p>'
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0;">'
            'It has been a pleasure. Kindly confirm receipt of this email, and feel free to reply directly with any questions.</p>'
        ),
    }

    if body_text is not None:
        _ps = "font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 16px 0;"
        normalized = re.sub(r'\n{3,}', '\n\n', body_text.strip())
        body_html = ''.join(
            f'<p style="{_ps}">{p.strip().replace(chr(10), "<br/>")}</p>'
            for p in normalized.split('\n\n') if p.strip()
        )
        greeting_html = ''
        _so = '\n\nWarmest Regards,\n' + user['display_name']
        if user.get('title'):
            _so += '\n' + user['title']
        _so += '\n\n' + user['business_name']
        if user.get('business_website'):
            _so += '\n' + user['business_website']
        plain = body_text + _so
    else:
        body_html = body_paras[doc_type]
        greeting_html = f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 20px 0;">Good day,</p>'
        plain = _email_body_text(first_name, doc_number, doc_type, user)

    if logo_path and os.path.exists(logo_path):
        logo_html = f'<img src="cid:logo" height="48" alt="{biz}" style="display:block;width:auto;" />'
    else:
        logo_html = (
            f'<div style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;'
            f'color:{accent};font-size:14px;font-weight:bold;letter-spacing:0.1em;">'
            f'{biz.upper()}</div>'
        )

    # Social links
    social_links = json.loads(user.get('social_links_json') or '[]')
    if social_links:
        sep = '<span style="color:#333333;">&nbsp;&middot;&nbsp;</span>'
        parts = [
            f'<a href="{l["url"]}" style="color:#888888;text-decoration:none;">{l["label"]}</a>'
            for l in social_links
        ]
        social_html = (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;'
            f'font-size:11px;color:#888888;text-align:center;margin:0 0 14px 0;">'
            + sep.join(parts) + '</p>'
        )
    else:
        social_html = ''

    website = user.get('business_website', '')
    website_html = ''
    if website:
        href = website if website.startswith('http') else f'https://{website}'
        website_html = (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;'
            f'font-size:12px;text-align:center;margin:0 0 12px 0;">'
            f'<a href="{href}" style="color:{accent};text-decoration:none;letter-spacing:0.06em;">'
            f'{website}</a></p>'
        )

    title_line = ''
    if user.get('title'):
        title_line = (
            f'<p style="font-family:\'Nunito Sans\',\'Avenir\',Arial,sans-serif;'
            f'font-size:13px;color:{accent_dark};font-weight:600;letter-spacing:0.04em;margin:0;">'
            f'{user["title"]}</p>'
        )

    html = f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{biz}</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@300;400;600&amp;display=swap" rel="stylesheet" type="text/css" />
<!--[if mso]><style type="text/css">body,table,td{{font-family:Arial,sans-serif !important;}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f0f0f0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheaders[doc_type]}&nbsp;&#847;&nbsp;</div>
<table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#f0f0f0;">
  <tr><td align="center" style="padding:24px 12px;">
    <table border="0" cellpadding="0" cellspacing="0" width="600" style="max-width:600px;background-color:#ffffff;border-radius:4px;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
      <!-- Top bar -->
      <tr><td height="4" style="background-color:{accent};font-size:0;line-height:0;">&nbsp;</td></tr>
      <!-- Header -->
      <tr><td style="padding:32px 40px 0 40px;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td style="vertical-align:middle;width:60px;">{logo_html}</td>
            <td style="vertical-align:middle;text-align:right;padding-left:16px;">
              <span style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:13px;font-weight:700;color:{accent};letter-spacing:0.05em;">{biz.upper()}</span>
            </td>
          </tr>
        </table>
      </td></tr>
      <!-- Body -->
      <tr><td style="padding:36px 40px 8px 40px;">
        {greeting_html}{body_html}
      </td></tr>
      <!-- Sign-off -->
      <tr><td style="padding:24px 40px 32px 40px;">
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;line-height:26px;margin:0 0 4px 0;">Warmest Regards,</p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:16px;color:#0E0E0E;font-weight:700;line-height:26px;margin:0 0 4px 0;">{user['display_name']}</p>
        {title_line}
      </td></tr>
      <!-- Divider -->
      <tr><td style="padding:0 40px;">
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
          <tr><td height="1" style="background-color:#f0f0f0;font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
      </td></tr>
      <!-- Footer -->
      <tr><td style="background-color:#0E0E0E;padding:28px 40px;border-radius:0 0 4px 4px;">
        {website_html}
        {social_html}
        <table border="0" cellpadding="0" cellspacing="0" align="center" width="40%">
          <tr><td height="1" style="background-color:{accent_dark};font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:11px;color:#555555;text-align:center;margin:12px 0 4px 0;">This email was sent by {biz}.</p>
        <p style="font-family:'Nunito Sans','Avenir',Arial,sans-serif;font-size:11px;color:#555555;text-align:center;margin:0;">Any questions? Just reply directly.</p>
      </td></tr>
      <!-- Bottom bar -->
      <tr><td height="4" style="background-color:{accent_dark};font-size:0;line-height:0;">&nbsp;</td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

    return html, plain


def _build_mime_message(to_email, subject, html_body, plain_text,
                        pdf_bytes, pdf_filename, logo_path, user):
    msg = MIMEMultipart('related')
    msg['From'] = f"{user['business_name']} <{user['email']}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg['Reply-To'] = user.get('business_email') or user['email']
    msg['X-Mailer'] = f"Quilk - {user['business_name']}"

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


def _doc_subject(doc_type, user):
    biz = user['business_name']
    return {
        'invoice': f'Confidential: Invoice - {biz}',
        'quote':   f'Confidential: Quote - {biz}',
        'receipt': f'Important: Receipt - {biz}',
    }[doc_type]


def _is_overdue(doc):
    if doc.get('doc_type') == 'receipt' or doc.get('status') == 'paid':
        return False
    pay_by = doc.get('pay_by_date')
    if not pay_by:
        return False
    try:
        return date.fromisoformat(str(pay_by)) < date.today()
    except (ValueError, TypeError):
        return False


def _default_email_templates():
    return {
        'invoice': {
            'subject_template': 'Confidential: Invoice {DOCNUMBER} - {YOURBUSINESSNAME}',
            'body_template': (
                'Greetings,\n\n'
                'Please find your invoice attached, document number {DOCNUMBER}, for your records.\n\n'
                'Kindly confirm receipt of this email, and please reply directly with any questions.'
            ),
        },
        'quote': {
            'subject_template': 'Confidential: Quote {DOCNUMBER} - {YOURBUSINESSNAME}',
            'body_template': (
                'Greetings,\n\n'
                'Please find your quote attached, document number {DOCNUMBER}, for your records. '
                'This quote is valid for two weeks from the date of issue.\n\n'
                'Kindly confirm receipt of this email, and please reply directly with any questions.'
            ),
        },
        'receipt': {
            'subject_template': 'Important: Receipt {DOCNUMBER} - {YOURBUSINESSNAME}',
            'body_template': (
                'Greetings,\n\n'
                'Please find your receipt attached, document number {DOCNUMBER}, for your records.\n\n'
                'It has been a pleasure. Kindly confirm receipt of this email, and feel free to reply directly with any questions.'
            ),
        },
    }


def _default_reminder_templates():
    return {
        'invoice_reminder': {
            'subject_template': 'Reminder: Invoice {DOCNUMBER} is overdue — {YOURBUSINESSNAME}',
            'body_template': (
                'Greetings,\n\n'
                'This is a friendly reminder that invoice {DOCNUMBER} for {AMOUNT}, '
                'due on {DUEDATE}, is now overdue.\n\n'
                'Please arrange payment as soon as possible to avoid any disruption. '
                'If you have already made payment, kindly disregard this notice and '
                'reply to let us know.\n\n'
                'Thank you for your prompt attention to this matter.'
            ),
        },
        'quote_reminder': {
            'subject_template': 'Reminder: Quote {DOCNUMBER} is awaiting your response — {YOURBUSINESSNAME}',
            'body_template': (
                'Greetings,\n\n'
                'This is a gentle reminder regarding quote {DOCNUMBER} for {AMOUNT}, '
                'which is now past its review date of {DUEDATE}.\n\n'
                'Please let us know at your earliest convenience whether you wish to '
                'proceed, so we can arrange the next steps.\n\n'
                'Feel free to reply directly with any questions.'
            ),
        },
    }


def _resolve_placeholders(text, doc, client, user):
    display_name = client.get('company_name') or client.get('name') or 'Client'
    currency = doc.get('currency', 'USD')
    if doc.get('doc_type') == 'receipt':
        raw_amount = doc.get('paid_amount') or 0
    else:
        raw_amount = doc.get('amount_due') or 0
    amount_str = f"{currency} {raw_amount:,.2f}"
    due_date = doc.get('pay_by_date') or ''
    tokens = {
        'DOCNUMBER':          doc.get('doc_number', ''),
        'CLIENTNAME':         display_name,
        'AMOUNT':             amount_str,
        'DUEDATE':            due_date,
        'YOURBUSINESSNAME':   user.get('business_name', ''),
        'CLIENTBUSINESSNAME': client.get('company_name', ''),
    }
    for key, value in tokens.items():
        text = text.replace('{{###' + key + '###}}', str(value))  # legacy format
        text = text.replace('{' + key + '}', str(value))          # current format
    return text


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def dashboard():
    db = get_db()
    recent = _rows_to_list(
        db.execute(
            "SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
            " FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
            " WHERE d.user_id=? AND d.discarded=0 ORDER BY d.created_at DESC LIMIT 10",
            (current_user.id,),
        ).fetchall()
    )
    labels = _client_display_labels(db, current_user.id)
    for row in recent:
        if row.get('client_id'):
            row['client_name'] = labels.get(row['client_id'], row.get('client_name'))
        row['is_overdue'] = _is_overdue(row)
    counts = {}
    for dt in ('invoice', 'quote', 'receipt'):
        row = db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE doc_type=? AND user_id=? AND discarded=0",
            (dt, current_user.id),
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

        new_name = data.get('new_client_name', '').strip()
        new_company = data.get('new_client_company_name', '').strip()
        if new_name or new_company:
            cur = db.execute(
                "INSERT INTO clients (company_name,name,email,phone,address_line1,"
                "address_line2,city,country,notes,user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new_company or None, new_name, data.get('new_client_email'),
                 data.get('new_client_phone'), data.get('new_client_addr1'),
                 data.get('new_client_addr2'), data.get('new_client_city'),
                 data.get('new_client_country'), data.get('new_client_notes'),
                 current_user.id),
            )
            client_id = cur.lastrowid
            db.commit()

        if not client_id:
            db.close()
            return jsonify({'error': 'Client required'}), 400

        line_items = json.loads(data.get('line_items_json', '[]'))
        discount_val = float(data.get('discount_val', 0))
        discount_type = data.get('discount_type', 'flat')
        paid_amount = float(data.get('paid_amount', 0))
        currency = data.get('currency', 'USD')
        doc_date = data.get('date_issued') or date.today().isoformat()
        notes = data.get('notes', '')
        pay_by_date = data.get('pay_by_date') or None

        subtotal, discount, tax_amount, amount_due = _compute_totals(
            line_items, discount_val, discount_type, paid_amount
        )

        prefix = getattr(current_user, f'doc_prefix_{doc_type}')
        doc_number = next_doc_number(doc_type, current_user.id, prefix)
        existing_job_id = (data.get('existing_job_id') or '').strip() or None
        job_id = _ensure_job(db, current_user.id, existing_job_id)

        cur = db.execute(
            "INSERT INTO documents (doc_type,doc_number,client_id,date_issued,"
            "currency,line_items,subtotal,discount,tax_amount,paid_amount,"
            "amount_due,status,notes,pay_by_date,user_id,job_id) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_type, doc_number, client_id, doc_date, currency,
             json.dumps(line_items), subtotal, discount, tax_amount,
             paid_amount, amount_due, 'pending', notes, pay_by_date,
             current_user.id, job_id),
        )
        doc_id = cur.lastrowid
        db.commit()

        for item in line_items:
            _upsert_item_library(
                db, item.get('description', ''), item.get('unit_price', 0),
                currency, current_user.id,
            )
        db.commit()

        if data.get('save_template_name') and line_items:
            first = line_items[0]
            db.execute(
                "INSERT INTO client_templates (client_id,template_name,"
                "service_description,unit_price,currency,qty,discount,"
                "tax_rate,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                (client_id, data['save_template_name'],
                 first.get('description'), first.get('unit_price'),
                 currency, first.get('qty', 1), discount_val, tax_val, notes),
            )
            db.commit()

        db.close()
        return redirect(url_for('document_view', doc_id=doc_id))

    clients = _rows_to_list(
        db.execute(
            "SELECT id, name, company_name,"
            " COALESCE(NULLIF(company_name,''), name) AS display_name"
            " FROM clients WHERE user_id=?"
            " ORDER BY COALESCE(NULLIF(company_name,''), name)",
            (current_user.id,),
        ).fetchall()
    )
    labels = _client_display_labels(db, current_user.id)
    for c in clients:
        c['display_name'] = labels.get(c['id'], c['display_name'])
    db.close()
    today = date.today().isoformat()
    return render_template('new_document.html', doc_type=doc_type,
                           clients=clients, today=today,
                           prefill_job_id=request.args.get('job_id', ''))


# ---------------------------------------------------------------------------
# Client templates API
# ---------------------------------------------------------------------------

@app.route('/api/clients/<int:client_id>/templates')
def client_templates(client_id):
    db = get_db()
    owner = db.execute(
        "SELECT id FROM clients WHERE id=? AND user_id=?",
        (client_id, current_user.id),
    ).fetchone()
    if not owner:
        db.close()
        return jsonify([])
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
    _upsert_item_library(db, description, unit_price, currency, current_user.id)
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Document search API
# ---------------------------------------------------------------------------

@app.route('/api/documents/search')
def search_documents():
    q = request.args.get('q', '').strip()
    db = get_db()

    if q:
        rows = _rows_to_list(db.execute(
            "SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
            " FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
            " WHERE d.user_id=? AND d.discarded=0"
            " AND (d.doc_number LIKE ? OR c.name LIKE ? OR c.company_name LIKE ?)"
            " ORDER BY d.created_at DESC LIMIT 50",
            (current_user.id, f'%{q}%', f'%{q}%', f'%{q}%'),
        ).fetchall())
    else:
        rows = _rows_to_list(db.execute(
            "SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
            " FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
            " WHERE d.user_id=? AND d.discarded=0 ORDER BY d.created_at DESC LIMIT 50",
            (current_user.id,),
        ).fetchall())

    labels = _client_display_labels(db, current_user.id)
    for row in rows:
        row['line_items'] = _parse_line_items(row['line_items'])
        if row.get('client_id'):
            row['client_name'] = labels.get(row['client_id'], row.get('client_name'))

    db.close()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.route('/clients', methods=['GET', 'POST'])
def clients():
    db = get_db()

    if request.method == 'POST':
        d = request.form
        company = (d.get('company_name') or '').strip() or None
        name = (d.get('name') or '').strip() or None
        if not company and not name:
            db.close()
            return redirect(url_for('clients'))
        db.execute(
            "INSERT INTO clients (company_name,name,email,phone,address_line1,"
            "address_line2,city,country,notes,user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (company, name, d.get('email') or None, d.get('phone') or None,
             d.get('address_line1') or None, d.get('address_line2') or None,
             d.get('city') or None, d.get('country') or None,
             d.get('notes') or None, current_user.id),
        )
        db.commit()
        db.close()
        return redirect(url_for('clients'))

    rows = _rows_to_list(
        db.execute(
            "SELECT c.*, COUNT(d.id) AS doc_count,"
            " COALESCE(NULLIF(c.company_name,''), c.name) AS display_name"
            " FROM clients c LEFT JOIN documents d ON d.client_id = c.id"
            " WHERE c.user_id=? AND c.discarded=0 GROUP BY c.id"
            " ORDER BY COALESCE(NULLIF(c.company_name,''), c.name)",
            (current_user.id,),
        ).fetchall()
    )
    labels = _client_display_labels(db, current_user.id)
    for r in rows:
        r['display_name'] = labels.get(r['id'], r['display_name'])
    user_row = db.execute(
        "SELECT view_pref_clients FROM users WHERE id=?", (current_user.id,)
    ).fetchone()
    view_pref = (user_row['view_pref_clients'] if user_row else None) or 'list'
    db.close()
    return render_template('clients.html', clients=rows, view_pref=view_pref)


@app.route('/clients/import', methods=['POST'])
def import_clients():
    try:
        rows = json.loads(request.form.get('rows_json', '[]'))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid JSON'}), 400
    if not isinstance(rows, list):
        return jsonify({'error': 'rows_json must be a list'}), 400
    imported = 0
    skipped = 0
    db = get_db()
    try:
        for row in rows:
            company = (row.get('company_name') or '').strip() or None
            name = (row.get('name') or '').strip() or None
            if not company and not name:
                skipped += 1
                continue
            db.execute(
                "INSERT INTO clients (company_name,name,email,phone,address_line1,"
                "address_line2,city,country,notes,user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (company, name or '',
                 (row.get('email') or '').strip() or None,
                 (row.get('phone') or '').strip() or None,
                 (row.get('address_line1') or '').strip() or None,
                 (row.get('address_line2') or '').strip() or None,
                 (row.get('city') or '').strip() or None,
                 (row.get('country') or '').strip() or None,
                 (row.get('notes') or '').strip() or None,
                 current_user.id),
            )
            imported += 1
        db.commit()
    except Exception as exc:
        db.close()
        return jsonify({'error': str(exc)}), 500
    db.close()
    return jsonify({'imported': imported, 'skipped': skipped})


@app.route('/clients/<int:client_id>')
def client_detail(client_id):
    db = get_db()
    client = _row_to_dict(
        db.execute(
            "SELECT * FROM clients WHERE id=? AND user_id=?",
            (client_id, current_user.id),
        ).fetchone()
    )
    if not client:
        abort(404)
    docs = _rows_to_list(
        db.execute(
            "SELECT * FROM documents WHERE client_id=? AND user_id=? ORDER BY created_at DESC",
            (client_id, current_user.id),
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
        db.execute(
            "SELECT * FROM clients WHERE id=? AND user_id=?",
            (client_id, current_user.id),
        ).fetchone()
    )
    if not client:
        abort(404)

    if request.method == 'POST':
        d = request.form
        db.execute(
            "UPDATE clients SET company_name=?,name=?,email=?,phone=?,address_line1=?,"
            "address_line2=?,city=?,country=?,notes=? WHERE id=? AND user_id=?",
            (d.get('company_name') or None, d['name'], d.get('email'), d.get('phone'),
             d.get('address_line1'), d.get('address_line2'),
             d.get('city'), d.get('country'), d.get('notes'),
             client_id, current_user.id),
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
    owner = db.execute(
        "SELECT id FROM clients WHERE id=? AND user_id=?",
        (client_id, current_user.id),
    ).fetchone()
    if not owner:
        db.close()
        abort(403)
    db.execute(
        "DELETE FROM client_templates WHERE id=? AND client_id=?",
        (tmpl_id, client_id),
    )
    db.commit()
    db.close()
    return redirect(url_for('client_detail', client_id=client_id))


@app.route('/clients/bulk_discard', methods=['POST'])
@login_required
def bulk_discard_clients():
    client_ids = request.form.getlist('client_ids')
    if client_ids:
        now = datetime.utcnow()
        ph = ','.join('?' * len(client_ids))
        db = get_db()
        db.execute(
            f"UPDATE clients SET discarded=1, discarded_at=?"
            f" WHERE id IN ({ph}) AND user_id=? AND discarded=0",
            [now, *client_ids, current_user.id],
        )
        db.commit()
        db.close()
    return redirect(url_for('clients'))


@app.route('/clients/<int:client_id>/export')
def export_client(client_id):
    db = get_db()
    client = _row_to_dict(
        db.execute(
            "SELECT * FROM clients WHERE id=? AND user_id=?",
            (client_id, current_user.id),
        ).fetchone()
    )
    if not client:
        abort(404)
    docs = _rows_to_list(
        db.execute(
            "SELECT * FROM documents WHERE client_id=? AND user_id=? ORDER BY created_at DESC",
            (client_id, current_user.id),
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
# Jobs
# ---------------------------------------------------------------------------

def _doc_type_summary(type_counts):
    parts = []
    for dt in ('quote', 'invoice', 'receipt'):
        n = type_counts.get(dt, 0)
        if n:
            parts.append(f"{n} {dt if n == 1 else dt + 's'}")
    return ' · '.join(parts) if parts else '—'


@app.route('/jobs')
def jobs():
    db = get_db()

    # Get job metadata from jobs table (ordered newest first)
    job_meta_rows = _rows_to_list(db.execute(
        "SELECT j.id, j.job_id, j.job_number, j.job_title, j.created_at"
        " FROM jobs j WHERE j.user_id=? AND j.discarded=0 ORDER BY j.created_at DESC",
        (current_user.id,),
    ).fetchall())
    job_meta_map = {j['job_id']: j for j in job_meta_rows}

    # Get all active (non-discarded) documents grouped by job_id
    rows = _rows_to_list(db.execute(
        "SELECT d.id, d.job_id, d.doc_type, d.doc_number, d.status, d.voided,"
        " d.invoice_type, d.amount_due, d.paid_amount, d.subtotal, d.discount,"
        " d.tax_amount, d.source_document_id, d.created_at, d.pay_by_date,"
        " d.client_id, d.currency"
        " FROM documents d"
        " WHERE d.user_id=? AND d.job_id IS NOT NULL AND d.discarded=0"
        " ORDER BY d.created_at ASC",
        (current_user.id,),
    ).fetchall())

    labels = _client_display_labels(db, current_user.id)
    user_row = db.execute(
        "SELECT view_pref_jobs FROM users WHERE id=?", (current_user.id,)
    ).fetchone()
    view_pref = (user_row['view_pref_jobs'] if user_row else None) or 'list'
    db.close()

    jobs_map = defaultdict(list)
    for r in rows:
        jobs_map[r['job_id']].append(r)

    # Build a set of all job_ids that appear in either table
    all_job_ids = set(jobs_map.keys()) | set(job_meta_map.keys())

    job_list = []
    for job_id in all_job_ids:
        docs = jobs_map.get(job_id, [])
        meta = job_meta_map.get(job_id)

        if docs:
            anchor = next(
                (d for d in docs
                 if not d.get('source_document_id') and d['doc_type'] in ('quote', 'invoice')),
                docs[0],
            )
            client_id = anchor.get('client_id')
            client_name = labels.get(client_id) if client_id else '—'
            type_counts = defaultdict(int)
            for d in docs:
                type_counts[d['doc_type']] += 1
            has_overdue = any(_is_overdue(d) for d in docs if not d.get('voided'))
            has_voided = any(d.get('voided') for d in docs)
            overview = _job_overview(docs, anchor)
            latest = max(d['created_at'] for d in docs)
        else:
            client_id = None
            client_name = '—'
            type_counts = defaultdict(int)
            has_overdue = False
            has_voided = False
            overview = None
            latest = meta['created_at'] if meta else ''

        job_number = meta['job_number'] if meta else job_id[:8]
        job_title = meta['job_title'] if meta else None
        display_name = job_title or job_number

        job_list.append({
            'job_id': job_id,
            'job_number': job_number,
            'job_title': job_title,
            'display_name': display_name,
            'client_name': client_name,
            'client_id': client_id,
            'doc_count': len(docs),
            'type_summary': _doc_type_summary(dict(type_counts)),
            'has_overdue': has_overdue,
            'has_voided': has_voided,
            'overview': overview,
            'latest': latest,
        })

    job_list.sort(key=lambda j: j['latest'], reverse=True)
    return render_template('jobs.html', jobs=job_list, view_pref=view_pref)


@app.route('/jobs/<job_id>')
def job_detail(job_id):
    db = get_db()

    # Load job record from jobs table (may not exist for legacy jobs)
    job_row = _row_to_dict(db.execute(
        "SELECT * FROM jobs WHERE job_id=? AND user_id=?",
        (job_id, current_user.id),
    ).fetchone() or {})

    job_number = job_row.get('job_number') if job_row else None
    job_title = job_row.get('job_title') if job_row else None
    display_name = job_title or job_number or job_id[:8]

    docs = _rows_to_list(db.execute(
        "SELECT d.*,"
        " COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
        " FROM documents d LEFT JOIN clients c ON d.client_id=c.id"
        " WHERE d.job_id=? AND d.user_id=? AND d.discarded=0"
        " ORDER BY d.created_at ASC",
        (job_id, current_user.id),
    ).fetchall())

    # If no job record AND no documents, 404
    if not job_row and not docs:
        db.close()
        abort(404)

    for d in docs:
        d['is_overdue'] = _is_overdue(d)
        d['line_items'] = _parse_line_items(d.get('line_items', '[]'))

    anchor = None
    if docs:
        anchor = next(
            (d for d in docs
             if not d.get('source_document_id') and d['doc_type'] in ('quote', 'invoice')),
            docs[0],
        )

    client = None
    if anchor and anchor.get('client_id'):
        client = _row_to_dict(db.execute(
            "SELECT * FROM clients WHERE id=? AND user_id=?",
            (anchor['client_id'], current_user.id),
        ).fetchone())

    overview = _job_overview(docs, anchor) if anchor else None

    # Other jobs for reassignment dropdown
    other_jobs = _rows_to_list(db.execute(
        "SELECT job_id, job_number, job_title FROM jobs"
        " WHERE user_id=? AND job_id!=? ORDER BY job_number",
        (current_user.id, job_id),
    ).fetchall())

    db.close()
    return render_template('job_detail.html',
                           job_id=job_id,
                           job_meta=job_row,
                           job_number=job_number,
                           job_title=job_title,
                           display_name=display_name,
                           docs=docs,
                           client=client,
                           overview=overview,
                           anchor=anchor,
                           other_jobs=other_jobs)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.route('/documents')
def documents():
    db = get_db()
    doc_type = request.args.get('type', '')
    status = request.args.get('status', '')
    query = (
        "SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
        " FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
        " WHERE d.user_id=? AND d.discarded=0"
    )
    params = [current_user.id]
    if doc_type:
        query += " AND d.doc_type=?"
        params.append(doc_type)
    if status:
        query += " AND d.status=?"
        params.append(status)
    query += " ORDER BY d.created_at DESC"
    rows = _rows_to_list(db.execute(query, params).fetchall())
    labels = _client_display_labels(db, current_user.id)
    for row in rows:
        if row.get('client_id'):
            row['client_name'] = labels.get(row['client_id'], row.get('client_name'))
        row['is_overdue'] = _is_overdue(row)
    user_row = db.execute(
        "SELECT view_pref_documents FROM users WHERE id=?", (current_user.id,)
    ).fetchone()
    view_pref = (user_row['view_pref_documents'] if user_row else None) or 'list'
    db.close()
    return render_template('documents.html', documents=rows,
                           filter_type=doc_type, filter_status=status,
                           view_pref=view_pref)


def _job_capabilities(job_docs, anchor_id):
    """Return (can_generate_full, can_generate_balance) for the given job."""
    active_invoices = [
        d for d in job_docs
        if d['doc_type'] == 'invoice' and not d.get('voided')
        and d['id'] != anchor_id
    ]
    paid_deposits = [
        d for d in active_invoices
        if d.get('invoice_type') == 'deposit' and d['status'] == 'paid'
    ]
    has_paid_deposit = bool(paid_deposits)
    return not has_paid_deposit, has_paid_deposit


def _job_overview(job_docs, anchor):
    """Compute job money overview dict from the full job document list."""
    if not anchor:
        return None
    currency = anchor.get('currency', 'USD')
    project_total = (anchor['subtotal'] or 0) - (anchor['discount'] or 0)

    active = [d for d in job_docs if not d.get('voided')]
    voided = [d for d in job_docs if d.get('voided')]

    # Invoices that are children of the anchor (not the anchor itself)
    amount_billed = sum(
        (d['amount_due'] or 0) for d in active
        if d['doc_type'] == 'invoice' and d['id'] != anchor['id']
    )
    amount_paid = sum(
        (d['paid_amount'] or 0) for d in active
        if d['doc_type'] == 'receipt'
    )
    voided_received = sum(
        (d['paid_amount'] or 0) for d in voided
        if d['doc_type'] == 'receipt' and d.get('paid_amount')
    )
    return {
        'currency': currency,
        'project_total': project_total,
        'amount_billed': amount_billed,
        'amount_paid': amount_paid,
        'balance_outstanding': project_total - amount_paid,
        'voided_received': voided_received,
    }


def _next_job_number(db, user_id):
    """Increment job_counter for user and return formatted job_number."""
    user_row = db.execute("SELECT job_prefix FROM users WHERE id=?", (user_id,)).fetchone()
    prefix = (user_row['job_prefix'] if user_row and user_row['job_prefix'] else 'JOB')
    db.execute(
        "INSERT OR IGNORE INTO job_counter (user_id, last_number) VALUES (?, 1110)",
        (user_id,),
    )
    db.execute(
        "UPDATE job_counter SET last_number = last_number + 1 WHERE user_id=?",
        (user_id,),
    )
    row = db.execute(
        "SELECT last_number FROM job_counter WHERE user_id=?", (user_id,)
    ).fetchone()
    return f"{prefix}{row['last_number']}"


def _ensure_job(db, user_id, existing_job_id=None):
    """Return a valid job_id for user, creating a jobs record if needed."""
    if existing_job_id:
        row = db.execute(
            "SELECT job_id FROM jobs WHERE job_id=? AND user_id=?",
            (existing_job_id, user_id),
        ).fetchone()
        if row:
            return existing_job_id
    new_id = existing_job_id or str(uuid.uuid4())
    job_number = _next_job_number(db, user_id)
    db.execute(
        "INSERT OR IGNORE INTO jobs (job_id, user_id, job_number) VALUES (?, ?, ?)",
        (new_id, user_id, job_number),
    )
    return new_id


@app.route('/documents/<int:doc_id>')
def document_view(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (doc_id, current_user.id),
        ).fetchone()
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
            (doc_id,),
        ).fetchall()
    )
    parent_doc = _row_to_dict(
        db.execute(
            "SELECT id, doc_type, doc_number FROM documents WHERE id=?",
            (doc.get('source_document_id'),),
        ).fetchone()
    ) if doc.get('source_document_id') else None
    child_docs = _rows_to_list(
        db.execute(
            "SELECT id, doc_type, doc_number, status, voided FROM documents"
            " WHERE source_document_id=? AND user_id=? ORDER BY created_at",
            (doc_id, current_user.id),
        ).fetchall()
    )

    # Job family + capabilities
    job_family = []
    job_overview = None
    can_generate_full = True
    can_generate_balance = False
    is_anchor = (
        doc['doc_type'] == 'quote'
        or (doc['doc_type'] == 'invoice' and not doc.get('source_document_id'))
    )

    job_id = doc.get('job_id')
    if job_id:
        job_docs = _rows_to_list(db.execute(
            "SELECT d.id, d.doc_type, d.doc_number, d.status, d.voided,"
            " d.invoice_type, d.amount_due, d.paid_amount, d.source_document_id,"
            " d.subtotal, d.discount, d.tax_amount, d.job_id, d.created_at,"
            " d.pay_by_date,"
            " COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
            " FROM documents d LEFT JOIN clients c ON d.client_id=c.id"
            " WHERE d.job_id=? AND d.user_id=? AND d.discarded=0"
            " ORDER BY d.created_at",
            (job_id, current_user.id),
        ).fetchall())
        for jd in job_docs:
            jd['is_overdue'] = _is_overdue(jd)
        job_family = job_docs

        anchor = next(
            (d for d in job_docs if not d.get('source_document_id') and d['doc_type'] in ('quote', 'invoice')),
            None,
        )
        can_generate_full, can_generate_balance = _job_capabilities(job_docs, anchor['id'] if anchor else None)
        job_overview = _job_overview(job_docs, anchor)

    job_meta = None
    other_jobs = []
    if doc.get('job_id'):
        job_meta = _row_to_dict(db.execute(
            "SELECT * FROM jobs WHERE job_id=? AND user_id=?",
            (doc['job_id'], current_user.id),
        ).fetchone() or {})
        other_jobs = _rows_to_list(db.execute(
            "SELECT job_id, job_number, job_title FROM jobs"
            " WHERE user_id=? AND job_id!=? ORDER BY job_number",
            (current_user.id, doc['job_id']),
        ).fetchall())

    db.close()
    return render_template(
        'document_view.html',
        doc=doc,
        client=client,
        sent_logs=sent_logs,
        parent_doc=parent_doc,
        child_docs=child_docs,
        is_overdue=_is_overdue(doc),
        job_family=job_family,
        job_overview=job_overview,
        is_anchor=is_anchor,
        can_generate_full=can_generate_full,
        can_generate_balance=can_generate_balance,
        job_meta=job_meta,
        other_jobs=other_jobs,
    )


@app.route('/documents/<int:doc_id>/pdf')
def document_pdf(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (doc_id, current_user.id),
        ).fetchone()
    )
    if not doc:
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    ) or {}
    db.close()

    pdf_bytes = generate_pdf(doc, client, current_user.to_dict())
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    filename = f"{doc['doc_number']}.pdf"
    inline = request.args.get('download') != '1'
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=not inline, download_name=filename)


_VALID_STATUSES = {
    'invoice': {'pending', 'sent', 'paid'},
    'quote':   {'pending', 'sent', 'accepted'},
    'receipt': {'pending', 'sent'},
}


@app.route('/documents/<int:doc_id>/set_status', methods=['POST'])
def set_status(doc_id):
    status = request.form.get('status', '').strip()
    if not status:
        return redirect(url_for('document_view', doc_id=doc_id))
    db = get_db()
    row = _row_to_dict(db.execute(
        "SELECT doc_type FROM documents WHERE id=? AND user_id=?",
        (doc_id, current_user.id),
    ).fetchone())
    if row and status in _VALID_STATUSES.get(row['doc_type'], set()):
        db.execute(
            "UPDATE documents SET status=? WHERE id=? AND user_id=?",
            (status, doc_id, current_user.id),
        )
        db.commit()
    db.close()
    return redirect(url_for('document_view', doc_id=doc_id))


@app.route('/documents/<int:doc_id>/generate_invoice', methods=['POST'])
def generate_invoice(doc_id):
    """Generate a full, deposit, or balance invoice from an anchor document."""
    invoice_type = request.form.get('invoice_type', 'full')
    if invoice_type not in ('full', 'deposit', 'balance'):
        abort(400)

    db = get_db()

    # The anchor must be an accepted quote OR a standalone invoice owned by this user
    anchor = _row_to_dict(db.execute(
        "SELECT * FROM documents WHERE id=? AND user_id=?"
        " AND ((doc_type='quote' AND status='accepted')"
        "      OR (doc_type='invoice' AND source_document_id IS NULL))",
        (doc_id, current_user.id),
    ).fetchone())
    if not anchor:
        db.close()
        abort(400)

    job_id = anchor.get('job_id') or str(uuid.uuid4())

    # Load job docs for gating checks
    job_docs = _rows_to_list(db.execute(
        "SELECT id, doc_type, status, voided, invoice_type, amount_due"
        " FROM documents WHERE job_id=? AND user_id=?",
        (job_id, current_user.id),
    ).fetchall())

    can_full, can_balance = _job_capabilities(job_docs, doc_id)

    if invoice_type == 'full' and not can_full:
        db.close()
        flash('Full invoice is locked — a deposit invoice has already been paid.', 'error')
        return redirect(url_for('document_view', doc_id=doc_id))

    if invoice_type == 'balance' and not can_balance:
        db.close()
        flash('Balance invoice requires a paid deposit invoice first.', 'error')
        return redirect(url_for('document_view', doc_id=doc_id))

    # Compute amounts
    project_total = (anchor['subtotal'] or 0) - (anchor['discount'] or 0)

    deposit_amount_stored = None
    deposit_type_stored = None

    if invoice_type == 'full':
        amount_due = project_total
    elif invoice_type == 'deposit':
        try:
            deposit_val = float(request.form.get('deposit_val', 0))
        except (TypeError, ValueError):
            db.close()
            flash('Invalid deposit amount.', 'error')
            return redirect(url_for('document_view', doc_id=doc_id))
        deposit_type_stored = request.form.get('deposit_type', 'flat')
        if deposit_type_stored == 'percent':
            amount_due = project_total * deposit_val / 100
        else:
            amount_due = deposit_val
        deposit_amount_stored = amount_due
    else:  # balance
        paid_deposits = [
            d for d in job_docs
            if d['doc_type'] == 'invoice' and not d.get('voided')
            and d.get('invoice_type') == 'deposit' and d['status'] == 'paid'
        ]
        already_invoiced = sum(d['amount_due'] or 0 for d in paid_deposits)
        amount_due = project_total - already_invoiced

    pay_by_date = request.form.get('pay_by_date') or None

    doc_number = next_doc_number('invoice', current_user.id, current_user.doc_prefix_invoice)
    cur = db.execute(
        "INSERT INTO documents (doc_type, doc_number, client_id, date_issued, currency,"
        " line_items, subtotal, discount, tax_amount, paid_amount, amount_due, status,"
        " notes, source_document_id, user_id, job_id, invoice_type,"
        " deposit_amount, deposit_type, pay_by_date)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ('invoice', doc_number, anchor['client_id'], date.today().isoformat(),
         anchor['currency'], anchor['line_items'], anchor['subtotal'],
         anchor['discount'], anchor['tax_amount'], 0,
         amount_due, 'pending', anchor['notes'],
         doc_id, current_user.id, job_id, invoice_type,
         deposit_amount_stored, deposit_type_stored, pay_by_date),
    )
    invoice_id = cur.lastrowid
    db.commit()
    db.close()
    return redirect(url_for('document_view', doc_id=invoice_id))


@app.route('/documents/<int:doc_id>/create_receipt', methods=['POST'])
def generate_receipt_from_invoice(doc_id):
    db = get_db()
    invoice = _row_to_dict(
        db.execute(
            "SELECT * FROM documents WHERE id=? AND doc_type='invoice' AND user_id=?"
            " AND status='paid'",
            (doc_id, current_user.id),
        ).fetchone()
    )
    if not invoice:
        db.close()
        abort(400)
    doc_number = next_doc_number('receipt', current_user.id, current_user.doc_prefix_receipt)
    cur = db.execute(
        "INSERT INTO documents (doc_type,doc_number,client_id,date_issued,currency,"
        "line_items,subtotal,discount,tax_amount,paid_amount,amount_due,status,notes,"
        "source_document_id,user_id,job_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ('receipt', doc_number, invoice['client_id'], date.today().isoformat(),
         invoice['currency'], invoice['line_items'], invoice['subtotal'],
         invoice['discount'], invoice['tax_amount'], invoice['amount_due'],
         0, 'pending', invoice['notes'], doc_id, current_user.id,
         invoice.get('job_id')),
    )
    receipt_id = cur.lastrowid
    db.commit()
    db.close()
    return redirect(url_for('document_view', doc_id=receipt_id))


@app.route('/documents/<int:doc_id>/void', methods=['POST'])
def void_document(doc_id):
    db = get_db()
    doc = _row_to_dict(db.execute(
        "SELECT id, doc_type, voided, source_document_id FROM documents"
        " WHERE id=? AND user_id=?",
        (doc_id, current_user.id),
    ).fetchone())
    if not doc:
        db.close()
        abort(404)

    currently_voided = bool(doc.get('voided'))
    new_voided = 0 if currently_voided else 1
    void_reason = request.form.get('void_reason', '').strip() or None if new_voided else None

    db.execute(
        "UPDATE documents SET voided=?, void_reason=? WHERE id=? AND user_id=?",
        (new_voided, void_reason, doc_id, current_user.id),
    )

    # Cascade to linked receipt (child doc with doc_type='receipt')
    if doc['doc_type'] == 'invoice':
        db.execute(
            "UPDATE documents SET voided=?, void_reason=?"
            " WHERE source_document_id=? AND doc_type='receipt' AND user_id=?",
            (new_voided, void_reason, doc_id, current_user.id),
        )

    db.commit()
    db.close()
    return redirect(url_for('document_view', doc_id=doc_id))


@app.route('/documents/bulk_discard', methods=['POST'])
@login_required
def bulk_discard_documents():
    doc_ids = request.form.getlist('doc_ids')
    if doc_ids:
        now = datetime.utcnow()
        ph = ','.join('?' * len(doc_ids))
        db = get_db()
        db.execute(
            f"UPDATE documents SET discarded=1, discarded_at=?"
            f" WHERE id IN ({ph}) AND user_id=? AND discarded=0",
            [now, *doc_ids, current_user.id],
        )
        db.commit()
        db.close()
    return redirect(url_for('documents'))


@app.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete_document(doc_id):
    """Permanent hard-delete — only allowed on already-discarded documents."""
    db = get_db()
    row = db.execute(
        "SELECT id, discarded FROM documents WHERE id=? AND user_id=?",
        (doc_id, current_user.id),
    ).fetchone()
    if not row:
        db.close()
        abort(404)
    if not row['discarded']:
        db.close()
        abort(403)
    db.execute("DELETE FROM sent_log WHERE doc_id=?", (doc_id,))
    db.execute(
        "UPDATE documents SET source_document_id=NULL"
        " WHERE source_document_id=? AND user_id=?",
        (doc_id, current_user.id),
    )
    db.execute(
        "DELETE FROM documents WHERE id=? AND user_id=?", (doc_id, current_user.id)
    )
    db.commit()
    db.close()
    flash('Document permanently deleted.', 'success')
    return redirect(url_for('discarded_items'))


@app.route('/documents/export_csv')
def export_csv():
    db = get_db()
    rows = _rows_to_list(
        db.execute(
            "SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
            " FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
            " WHERE d.user_id=? ORDER BY d.created_at DESC",
            (current_user.id,),
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
        db.execute(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (doc_id, current_user.id),
        ).fetchone()
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
        paid_amount = float(data.get('paid_amount', 0))
        currency = data.get('currency', 'USD')
        doc_date = data.get('date_issued') or doc['date_issued']
        notes = data.get('notes', '')
        pay_by_date = data.get('pay_by_date') or None
        status = data.get('status', doc['status'])

        subtotal, discount, tax_amount, amount_due = _compute_totals(
            line_items, discount_val, discount_type, paid_amount
        )

        db.execute(
            "UPDATE documents SET client_id=?,date_issued=?,currency=?,line_items=?,"
            "subtotal=?,discount=?,tax_amount=?,paid_amount=?,amount_due=?,"
            "notes=?,pay_by_date=?,status=? WHERE id=? AND user_id=?",
            (client_id, doc_date, currency, json.dumps(line_items),
             subtotal, discount, tax_amount, paid_amount, amount_due,
             notes, pay_by_date, status, doc_id, current_user.id),
        )
        db.commit()
        db.close()

        flash('Document updated successfully.', 'success')
        return redirect(url_for('document_view', doc_id=doc_id))

    clients = _rows_to_list(
        db.execute(
            "SELECT id, name, company_name,"
            " COALESCE(NULLIF(company_name,''), name) AS display_name"
            " FROM clients WHERE user_id=?"
            " ORDER BY COALESCE(NULLIF(company_name,''), name)",
            (current_user.id,),
        ).fetchall()
    )
    labels = _client_display_labels(db, current_user.id)
    for c in clients:
        c['display_name'] = labels.get(c['id'], c['display_name'])
    db.close()
    return render_template('edit_document.html', doc=doc, clients=clients)


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

@app.route('/documents/<int:doc_id>/send', methods=['GET', 'POST'])
def send_document(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (doc_id, current_user.id),
        ).fetchone()
    )
    if not doc:
        db.close()
        abort(404)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    ) or {}
    tpl_row = _row_to_dict(
        db.execute(
            "SELECT subject_template, body_template FROM email_templates WHERE user_id=? AND doc_type=?",
            (current_user.id, doc['doc_type']),
        ).fetchone()
    )
    db.close()

    user_dict = current_user.to_dict()
    first_name = (client.get('name') or '').split()[0] if client.get('name') else 'there'
    defaults = _default_email_templates()[doc['doc_type']]
    subject_tpl = (tpl_row or {}).get('subject_template') or defaults['subject_template']
    body_tpl = (tpl_row or {}).get('body_template') or defaults['body_template']
    pdf_filename = _pdf_email_filename(doc['doc_number'])
    to_email = client.get('email', '')

    if request.method == 'GET':
        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject_tpl=subject_tpl, body_tpl=body_tpl,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               error=None)

    to_email = request.form.get('to_email', '').strip()
    subject_raw = request.form.get('subject', subject_tpl).strip()
    body_raw = request.form.get('body_text', body_tpl).strip()
    subject = _resolve_placeholders(subject_raw, doc, client, user_dict)
    body_text = _resolve_placeholders(body_raw, doc, client, user_dict)
    logo_path = os.path.join(ASSETS_DIR, current_user.logo_filename or 'logo.png')

    db = get_db()
    try:
        pdf_bytes = generate_pdf(doc, client, user_dict)
        html_body, plain_text = build_html_email(
            first_name, doc['doc_number'], doc['doc_type'], logo_path, user_dict,
            body_text=body_text,
        )
        msg = _build_mime_message(
            to_email, subject, html_body, plain_text,
            pdf_bytes, pdf_filename, logo_path, user_dict,
        )

        gmail = _get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(userId='me', body={'raw': raw}).execute()

        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status) VALUES (?,?,?,?)",
            (doc_id, to_email, subject, 'sent'),
        )
        db.execute(
            "UPDATE documents SET status='sent' WHERE id=? AND user_id=? AND status='pending'",
            (doc_id, current_user.id),
        )
        db.commit()
        db.close()

        flash(
            f'Email sent to {to_email} on {datetime.now().strftime("%B %d, %Y at %H:%M")}.',
            'success',
        )
        return redirect(url_for('document_view', doc_id=doc_id))

    except Exception as e:
        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status,error_message) VALUES (?,?,?,?,?)",
            (doc_id, to_email, subject_raw, 'failed', str(e)),
        )
        db.commit()
        db.close()

        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject_tpl=subject_raw, body_tpl=body_raw,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               error=str(e))


@app.route('/documents/<int:doc_id>/remind', methods=['GET', 'POST'])
def remind_document(doc_id):
    db = get_db()
    doc = _row_to_dict(
        db.execute(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (doc_id, current_user.id),
        ).fetchone()
    )
    if not doc:
        db.close()
        abort(404)
    if doc.get('doc_type') == 'receipt' or not _is_overdue(doc):
        db.close()
        abort(403)
    doc['line_items'] = _parse_line_items(doc['line_items'])
    client = _row_to_dict(
        db.execute("SELECT * FROM clients WHERE id=?",
                   (doc['client_id'],)).fetchone()
    ) or {}
    tpl_key = doc['doc_type'] + '_reminder'
    tpl_row = _row_to_dict(
        db.execute(
            "SELECT subject_template, body_template FROM email_templates WHERE user_id=? AND doc_type=?",
            (current_user.id, tpl_key),
        ).fetchone()
    )
    db.close()

    user_dict = current_user.to_dict()
    first_name = (client.get('name') or '').split()[0] if client.get('name') else 'there'
    defaults = _default_reminder_templates()[tpl_key]
    subject_tpl = (tpl_row or {}).get('subject_template') or defaults['subject_template']
    body_tpl = (tpl_row or {}).get('body_template') or defaults['body_template']
    pdf_filename = _pdf_email_filename(doc['doc_number'])
    to_email = client.get('email', '')

    if request.method == 'GET':
        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject_tpl=subject_tpl, body_tpl=body_tpl,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               is_reminder=True,
                               error=None)

    to_email = request.form.get('to_email', '').strip()
    subject_raw = request.form.get('subject', subject_tpl).strip()
    body_raw = request.form.get('body_text', body_tpl).strip()
    subject = _resolve_placeholders(subject_raw, doc, client, user_dict)
    body_text = _resolve_placeholders(body_raw, doc, client, user_dict)
    logo_path = os.path.join(ASSETS_DIR, current_user.logo_filename or 'logo.png')

    db = get_db()
    try:
        pdf_bytes = generate_pdf(doc, client, user_dict)
        html_body, plain_text = build_html_email(
            first_name, doc['doc_number'], doc['doc_type'], logo_path, user_dict,
            body_text=body_text,
        )
        msg = _build_mime_message(
            to_email, subject, html_body, plain_text,
            pdf_bytes, pdf_filename, logo_path, user_dict,
        )

        gmail = _get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(userId='me', body={'raw': raw}).execute()

        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status) VALUES (?,?,?,?)",
            (doc_id, to_email, subject, 'sent'),
        )
        db.commit()
        db.close()

        flash(
            f'Reminder sent to {to_email} on {datetime.now().strftime("%B %d, %Y at %H:%M")}.',
            'success',
        )
        return redirect(url_for('document_view', doc_id=doc_id))

    except Exception as e:
        db.execute(
            "INSERT INTO sent_log (doc_id,recipient_email,subject,status,error_message) VALUES (?,?,?,?,?)",
            (doc_id, to_email, subject_raw, 'failed', str(e)),
        )
        db.commit()
        db.close()

        return render_template('send_email.html',
                               doc=doc, client=client,
                               subject_tpl=subject_raw, body_tpl=body_raw,
                               pdf_filename=pdf_filename,
                               to_email=to_email,
                               is_reminder=True,
                               error=str(e))


@app.route('/documents/<int:doc_id>/save_email_template', methods=['POST'])
def save_email_template(doc_id):
    db = get_db()
    row = db.execute(
        "SELECT doc_type FROM documents WHERE id=? AND user_id=?",
        (doc_id, current_user.id),
    ).fetchone()
    if not row:
        db.close()
        return jsonify({'ok': False, 'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    subject_template = (data.get('subject_template') or '').strip()
    body_template = (data.get('body_template') or '').strip()
    if not subject_template or not body_template:
        db.close()
        return jsonify({'ok': False, 'error': 'Missing fields'}), 400

    tpl_key = row['doc_type'] + ('_reminder' if data.get('is_reminder') else '')
    db.execute(
        """INSERT INTO email_templates (user_id, doc_type, subject_template, body_template)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, doc_type) DO UPDATE SET
               subject_template = excluded.subject_template,
               body_template    = excluded.body_template""",
        (current_user.id, tpl_key, subject_template, body_template),
    )
    db.commit()
    db.close()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# View preference + export-selection APIs
# ---------------------------------------------------------------------------

@app.route('/api/view_pref', methods=['POST'])
@login_required
def api_view_pref():
    data = request.get_json(silent=True) or {}
    section = data.get('section')
    mode = data.get('mode')
    if section not in ('clients', 'documents', 'jobs') or mode not in ('list', 'grid'):
        return jsonify({'error': 'invalid'}), 400
    col = f'view_pref_{section}'
    db = get_db()
    db.execute(f"UPDATE users SET {col}=? WHERE id=?", (mode, current_user.id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/clients/export_selection_csv', methods=['POST'])
@login_required
def export_selection_clients_csv():
    client_ids = [int(x) for x in request.form.getlist('client_ids') if x.isdigit()]
    if not client_ids:
        return redirect(url_for('clients'))
    db = get_db()
    ph = ','.join('?' * len(client_ids))
    rows = _rows_to_list(db.execute(
        f"SELECT * FROM clients WHERE id IN ({ph}) AND user_id=?",
        [*client_ids, current_user.id],
    ).fetchall())
    db.close()
    fields = ['company_name', 'name', 'email', 'phone',
              'address_line1', 'address_line2', 'city', 'country', 'notes']
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k) or '' for k in fields})
    buf = io.BytesIO(output.getvalue().encode())
    buf.seek(0)
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name=f"clients_{date.today().isoformat()}.csv")


@app.route('/clients/export_selection', methods=['POST'])
@login_required
def export_selection_clients():
    client_ids = [int(x) for x in request.form.getlist('client_ids') if x.isdigit()]
    if not client_ids:
        return redirect(url_for('clients'))
    db = get_db()
    ph = ','.join('?' * len(client_ids))
    clients_rows = _rows_to_list(db.execute(
        f"SELECT * FROM clients WHERE id IN ({ph}) AND user_id=?",
        [*client_ids, current_user.id],
    ).fetchall())
    docs_rows = _rows_to_list(db.execute(
        f"SELECT * FROM documents WHERE client_id IN ({ph}) AND user_id=?",
        [*client_ids, current_user.id],
    ).fetchall())
    db.close()
    jobs_map = defaultdict(list)
    for d in docs_rows:
        if d.get('job_id'):
            jobs_map[d['job_id']].append(d['id'])
    payload = {
        'quilk_export': '1.0',
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'clients': clients_rows,
        'documents': docs_rows,
        'jobs': [{'job_id': jid, 'doc_ids': dids} for jid, dids in jobs_map.items()],
    }
    buf = io.BytesIO(json.dumps(payload, indent=2, default=str).encode())
    buf.seek(0)
    filename = f"quilk_clients_{date.today().isoformat()}.quilk"
    return send_file(buf, mimetype='application/json',
                     as_attachment=True, download_name=filename)


@app.route('/documents/export_selection_csv', methods=['POST'])
@login_required
def export_selection_documents_csv():
    doc_ids = [int(x) for x in request.form.getlist('doc_ids') if x.isdigit()]
    if not doc_ids:
        return redirect(url_for('documents'))
    db = get_db()
    ph = ','.join('?' * len(doc_ids))
    rows = _rows_to_list(db.execute(
        f"SELECT d.*, COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
        f" FROM documents d LEFT JOIN clients c ON d.client_id = c.id"
        f" WHERE d.id IN ({ph}) AND d.user_id=?",
        [*doc_ids, current_user.id],
    ).fetchall())
    db.close()
    fields = ['doc_number', 'doc_type', 'invoice_type', 'client_name',
              'date_issued', 'currency', 'subtotal', 'discount', 'tax_amount',
              'amount_due', 'paid_amount', 'status', 'pay_by_date', 'notes']
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k) or '' for k in fields})
    buf = io.BytesIO(output.getvalue().encode())
    buf.seek(0)
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name=f"documents_{date.today().isoformat()}.csv")


@app.route('/documents/export_selection', methods=['POST'])
@login_required
def export_selection_documents():
    doc_ids = [int(x) for x in request.form.getlist('doc_ids') if x.isdigit()]
    if not doc_ids:
        return redirect(url_for('documents'))
    db = get_db()
    ph = ','.join('?' * len(doc_ids))
    docs_rows = _rows_to_list(db.execute(
        f"SELECT * FROM documents WHERE id IN ({ph}) AND user_id=?",
        [*doc_ids, current_user.id],
    ).fetchall())
    client_ids = list({d['client_id'] for d in docs_rows if d.get('client_id')})
    clients_rows = []
    if client_ids:
        cph = ','.join('?' * len(client_ids))
        clients_rows = _rows_to_list(db.execute(
            f"SELECT * FROM clients WHERE id IN ({cph}) AND user_id=?",
            [*client_ids, current_user.id],
        ).fetchall())
    db.close()
    jobs_map = defaultdict(list)
    for d in docs_rows:
        if d.get('job_id'):
            jobs_map[d['job_id']].append(d['id'])
    payload = {
        'quilk_export': '1.0',
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'clients': clients_rows,
        'documents': docs_rows,
        'jobs': [{'job_id': jid, 'doc_ids': dids} for jid, dids in jobs_map.items()],
    }
    buf = io.BytesIO(json.dumps(payload, indent=2, default=str).encode())
    buf.seek(0)
    filename = f"quilk_documents_{date.today().isoformat()}.quilk"
    return send_file(buf, mimetype='application/json',
                     as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Job management routes
# ---------------------------------------------------------------------------

@app.route('/jobs/new', methods=['POST'])
@login_required
def new_job():
    db = get_db()
    job_id = str(uuid.uuid4())
    job_number = _next_job_number(db, current_user.id)
    db.execute(
        "INSERT INTO jobs (job_id, user_id, job_number) VALUES (?, ?, ?)",
        (job_id, current_user.id, job_number),
    )
    db.commit()
    db.close()
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/jobs/<job_id>/edit_title', methods=['POST'])
@login_required
def edit_job_title(job_id):
    title = (request.form.get('job_title') or '').strip() or None
    db = get_db()
    db.execute(
        "UPDATE jobs SET job_title=? WHERE job_id=? AND user_id=?",
        (title, job_id, current_user.id),
    )
    db.commit()
    db.close()
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/jobs/<job_id>/delete', methods=['POST'])
@login_required
def delete_job(job_id):
    """Permanent hard-delete — only allowed on already-discarded jobs."""
    db = get_db()
    job_row = db.execute(
        "SELECT job_id, discarded FROM jobs WHERE job_id=? AND user_id=?",
        (job_id, current_user.id),
    ).fetchone()
    if not job_row:
        db.close()
        abort(404)
    if not job_row['discarded']:
        db.close()
        abort(403)
    doc_ids = [r['id'] for r in db.execute(
        "SELECT id FROM documents WHERE job_id=? AND user_id=?",
        (job_id, current_user.id),
    ).fetchall()]
    if doc_ids:
        ph = ','.join('?' * len(doc_ids))
        db.execute(f"DELETE FROM sent_log WHERE doc_id IN ({ph})", doc_ids)
        db.execute(
            f"UPDATE documents SET source_document_id=NULL "
            f"WHERE source_document_id IN ({ph}) AND user_id=?",
            [*doc_ids, current_user.id],
        )
        db.execute(
            f"DELETE FROM documents WHERE id IN ({ph}) AND user_id=?",
            [*doc_ids, current_user.id],
        )
    db.execute("DELETE FROM jobs WHERE job_id=? AND user_id=?", (job_id, current_user.id))
    db.commit()
    db.close()
    flash('Job permanently deleted.', 'success')
    return redirect(url_for('discarded_items'))


@app.route('/jobs/bulk_discard', methods=['POST'])
@login_required
def bulk_discard_jobs():
    job_ids = request.form.getlist('job_ids')
    if not job_ids:
        return redirect(url_for('jobs'))
    now = datetime.utcnow()
    jph = ','.join('?' * len(job_ids))
    db = get_db()
    db.execute(
        f"UPDATE jobs SET discarded=1, discarded_at=?"
        f" WHERE job_id IN ({jph}) AND user_id=? AND discarded=0",
        [now, *job_ids, current_user.id],
    )
    db.execute(
        f"UPDATE documents SET discarded=1, discarded_at=?, discarded_with_job=1"
        f" WHERE job_id IN ({jph}) AND user_id=? AND discarded=0",
        [now, *job_ids, current_user.id],
    )
    db.commit()
    db.close()
    return redirect(url_for('jobs'))


@app.route('/documents/<int:doc_id>/reassign_job', methods=['POST'])
@login_required
def reassign_job(doc_id):
    target_job_id = (request.form.get('target_job_id') or '').strip()
    if not target_job_id:
        return redirect(url_for('document_view', doc_id=doc_id))
    db = get_db()
    target = db.execute(
        "SELECT job_id FROM jobs WHERE job_id=? AND user_id=?",
        (target_job_id, current_user.id),
    ).fetchone()
    if target:
        db.execute(
            "UPDATE documents SET job_id=? WHERE id=? AND user_id=?",
            (target_job_id, doc_id, current_user.id),
        )
        db.commit()
        flash('Document moved to new job.', 'success')
    db.close()
    return redirect(url_for('document_view', doc_id=doc_id))


@app.route('/jobs/export_selection', methods=['POST'])
@login_required
def export_selection_jobs():
    job_ids = request.form.getlist('job_ids')
    if not job_ids:
        return redirect(url_for('jobs'))
    db = get_db()
    jph = ','.join('?' * len(job_ids))
    jobs_rows = _rows_to_list(db.execute(
        f"SELECT * FROM jobs WHERE job_id IN ({jph}) AND user_id=?",
        [*job_ids, current_user.id],
    ).fetchall())
    docs_rows = _rows_to_list(db.execute(
        f"SELECT * FROM documents WHERE job_id IN ({jph}) AND user_id=?",
        [*job_ids, current_user.id],
    ).fetchall())
    client_ids = list({d['client_id'] for d in docs_rows if d.get('client_id')})
    clients_rows = []
    if client_ids:
        cph = ','.join('?' * len(client_ids))
        clients_rows = _rows_to_list(db.execute(
            f"SELECT * FROM clients WHERE id IN ({cph}) AND user_id=?",
            [*client_ids, current_user.id],
        ).fetchall())
    db.close()
    payload = {
        'quilk_export': '1.0',
        'exported_at': datetime.utcnow().isoformat() + 'Z',
        'jobs': [{'job_id': j['job_id'], 'job_number': j['job_number'], 'job_title': j.get('job_title')} for j in jobs_rows],
        'clients': clients_rows,
        'documents': docs_rows,
    }
    buf = io.BytesIO(json.dumps(payload, indent=2, default=str).encode())
    buf.seek(0)
    filename = f"quilk_jobs_{date.today().isoformat()}.quilk"
    return send_file(buf, mimetype='application/json', as_attachment=True, download_name=filename)


@app.route('/jobs/export_selection_csv', methods=['POST'])
@login_required
def export_selection_jobs_csv():
    job_ids = request.form.getlist('job_ids')
    if not job_ids:
        return redirect(url_for('jobs'))
    db = get_db()
    jph = ','.join('?' * len(job_ids))
    rows = _rows_to_list(db.execute(
        f"SELECT j.job_number, j.job_title, j.created_at,"
        f" COUNT(d.id) as doc_count,"
        f" COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
        f" FROM jobs j"
        f" LEFT JOIN documents d ON d.job_id = j.job_id"
        f" LEFT JOIN clients c ON d.client_id = c.id"
        f" WHERE j.job_id IN ({jph}) AND j.user_id=?"
        f" GROUP BY j.job_id",
        [*job_ids, current_user.id],
    ).fetchall())
    db.close()
    fields = ['job_number', 'job_title', 'client_name', 'doc_count', 'created_at']
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k) or '' for k in fields})
    buf = io.BytesIO(output.getvalue().encode())
    buf.seek(0)
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name=f"jobs_{date.today().isoformat()}.csv")


# ---------------------------------------------------------------------------
# Discard / Restore / Discarded page
# ---------------------------------------------------------------------------

@app.route('/documents/<int:doc_id>/discard', methods=['POST'])
@login_required
def discard_document(doc_id):
    db = get_db()
    row = db.execute(
        "SELECT id FROM documents WHERE id=? AND user_id=? AND discarded=0",
        (doc_id, current_user.id),
    ).fetchone()
    if not row:
        db.close()
        abort(404)
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    db.execute(
        "UPDATE documents SET discarded=1, discarded_at=? WHERE id=? AND user_id=?",
        (now, doc_id, current_user.id),
    )
    db.commit()
    db.close()
    flash(
        'Document discarded. You can find it under Discarded in the sidebar. '
        'To permanently delete, use Delete Permanently from the Discarded page.',
        'info',
    )
    return redirect(url_for('documents'))


@app.route('/jobs/<job_id>/discard', methods=['POST'])
@login_required
def discard_job(job_id):
    db = get_db()
    row = db.execute(
        "SELECT job_id FROM jobs WHERE job_id=? AND user_id=? AND discarded=0",
        (job_id, current_user.id),
    ).fetchone()
    if not row:
        db.close()
        abort(404)
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat()
    db.execute(
        "UPDATE jobs SET discarded=1, discarded_at=? WHERE job_id=? AND user_id=?",
        (now, job_id, current_user.id),
    )
    # Cascade: mark all active docs in this job as discarded-with-job
    db.execute(
        "UPDATE documents SET discarded=1, discarded_at=?, discarded_with_job=1"
        " WHERE job_id=? AND user_id=? AND discarded=0",
        (now, job_id, current_user.id),
    )
    db.commit()
    db.close()
    flash(
        'Job discarded (including its documents). You can find it under Discarded in the sidebar. '
        'To permanently delete, use Delete Permanently from the Discarded page.',
        'info',
    )
    return redirect(url_for('jobs'))


@app.route('/documents/<int:doc_id>/restore', methods=['POST'])
@login_required
def restore_document(doc_id):
    db = get_db()
    db.execute(
        "UPDATE documents SET discarded=0, discarded_at=NULL, discarded_with_job=0"
        " WHERE id=? AND user_id=?",
        (doc_id, current_user.id),
    )
    db.commit()
    db.close()
    flash('Document restored to the active list.', 'success')
    return redirect(url_for('discarded_items'))


@app.route('/jobs/<job_id>/restore', methods=['POST'])
@login_required
def restore_job(job_id):
    db = get_db()
    db.execute(
        "UPDATE jobs SET discarded=0, discarded_at=NULL WHERE job_id=? AND user_id=?",
        (job_id, current_user.id),
    )
    # Cascade-restore only docs that were discarded as part of this job discard
    db.execute(
        "UPDATE documents SET discarded=0, discarded_at=NULL, discarded_with_job=0"
        " WHERE job_id=? AND user_id=? AND discarded_with_job=1",
        (job_id, current_user.id),
    )
    db.commit()
    db.close()
    flash('Job and its documents restored to the active lists.', 'success')
    return redirect(url_for('discarded_items'))


@app.route('/discarded')
@login_required
def discarded_items():
    db = get_db()

    # Discarded jobs (with cascade-discarded doc count)
    discarded_jobs = _rows_to_list(db.execute(
        "SELECT j.job_id, j.job_number, j.job_title, j.discarded_at,"
        " COUNT(d.id) AS doc_count"
        " FROM jobs j"
        " LEFT JOIN documents d ON d.job_id=j.job_id AND d.discarded_with_job=1"
        " WHERE j.user_id=? AND j.discarded=1"
        " GROUP BY j.job_id ORDER BY j.discarded_at DESC",
        (current_user.id,),
    ).fetchall())

    # Discarded documents NOT cascade-discarded with a job (individually discarded)
    discarded_docs = _rows_to_list(db.execute(
        "SELECT d.id, d.doc_number, d.doc_type, d.invoice_type, d.status,"
        " d.discarded_at, d.job_id,"
        " COALESCE(NULLIF(c.company_name,''), c.name) AS client_name"
        " FROM documents d LEFT JOIN clients c ON d.client_id=c.id"
        " WHERE d.user_id=? AND d.discarded=1 AND d.discarded_with_job=0"
        " ORDER BY d.discarded_at DESC",
        (current_user.id,),
    ).fetchall())

    db.close()
    return render_template('discarded.html',
                           discarded_jobs=discarded_jobs,
                           discarded_docs=discarded_docs)


# ---------------------------------------------------------------------------

init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
