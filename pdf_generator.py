import base64
import io
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates', 'pdf')
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'assets')


def _logo_base64() -> str:
    logo_path = os.path.join(ASSETS_DIR, 'logo.png')
    if not os.path.exists(logo_path):
        return ''
    with open(logo_path, 'rb') as f:
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


def generate_pdf(doc: dict, client: dict) -> bytes:
    doc_type = doc['doc_type']
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    env.filters['format_date'] = _format_date
    env.filters['format_currency'] = _format_currency

    template = env.get_template(f'{doc_type}.html')

    html_str = template.render(
        doc=doc,
        client=client,
        logo_b64=_logo_base64(),
    )

    pdf_bytes = io.BytesIO()
    HTML(string=html_str, base_url=ASSETS_DIR).write_pdf(pdf_bytes)
    return pdf_bytes.getvalue()
