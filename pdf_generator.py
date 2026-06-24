import base64
import io
import json
import os
import re
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates', 'pdf')
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'assets')


def _logo_b64_for_file(filename: str) -> str:
    if not filename:
        return ''
    path = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(path):
        return ''
    with open(path, 'rb') as f:
        return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()


def _format_currency(value):
    try:
        return '{:,.2f}'.format(float(value))
    except (ValueError, TypeError):
        return '0.00'


def _format_date(value, fmt='%B %d, %Y'):
    if not value:
        return ''
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').strftime(fmt)
    except Exception:
        return str(value)


def _nl2br(s):
    if not s:
        return ''
    return str(s).replace('\n', '<br>')


_EMOJI_RE = re.compile(
    "[\U0001F1E0-\U0001F1FF"   # regional indicators (flags)
    "\U0001F300-\U0001F9FF"    # misc symbols, pictographs, emoticons, transport
    "☀-➿]+",         # misc symbols, dingbats
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub('', text or '').strip()


def _bank_rows(details_str):
    """Parse a payment details string into (label, value) tuples for bankrow rendering."""
    rows = []
    for line in (details_str or '').split('\n'):
        line = line.strip()
        if not line:
            continue
        if ': ' in line:
            label, _, value = line.partition(': ')
            rows.append((label, value))
        else:
            rows.append((None, line))
    return rows


def generate_pdf(doc: dict, client: dict, user: dict) -> bytes:
    doc_type = doc['doc_type']
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    env.filters['format_date'] = _format_date
    env.filters['format_currency'] = _format_currency
    env.filters['nl2br'] = _nl2br

    user_ctx = dict(user)
    payment_methods = json.loads(user.get('payment_methods_json') or '[]')
    for group in payment_methods:
        if 'group' in group:
            group['group'] = _strip_emoji(group['group'])
    user_ctx['payment_methods'] = payment_methods
    user_ctx['social_links'] = json.loads(user.get('social_links_json') or '[]')

    if user.get('username') == 'aureum':
        env.filters['bank_rows'] = _bank_rows
        template = env.get_template('aureum_doc.html')
        doc_titles = {'invoice': 'INVOICE', 'quote': 'QUOTE', 'receipt': 'RECEIPT'}
        html_str = template.render(
            doc=doc,
            client=client,
            user=user_ctx,
            doc_title=doc_titles.get(doc_type, doc_type.upper()),
            aureum_logo_b64=_logo_b64_for_file('aureum_luxe_logo.png'),
            vsm_logo_b64=_logo_b64_for_file('VSMLogoWhite.png'),
        )
    else:
        template = env.get_template(f'{doc_type}.html')
        html_str = template.render(
            doc=doc,
            client=client,
            logo_b64=_logo_b64_for_file(user.get('logo_filename')),
            logotype_b64=_logo_b64_for_file(user.get('logotype_filename')),
            user=user_ctx,
        )

    pdf_bytes = io.BytesIO()
    HTML(string=html_str, base_url=ASSETS_DIR).write_pdf(pdf_bytes)
    return pdf_bytes.getvalue()
