'use strict';

// ── Theme toggle ──────────────────────────────────────────────────────────────
const toggle = document.getElementById('theme-toggle');
const savedTheme = localStorage.getItem('ledger-theme') || 'dark';
document.body.setAttribute('data-theme', savedTheme);
updateToggleIcon(savedTheme);

if (toggle) {
  toggle.addEventListener('click', () => {
    const current = document.body.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.body.setAttribute('data-theme', next);
    localStorage.setItem('ledger-theme', next);
    updateToggleIcon(next);
  });
}

function updateToggleIcon(theme) {
  const logo = document.getElementById('sidebar-logo');
  if (logo) {
    logo.src = theme === 'dark'
      ? '/static/assets/pnglogo.png'
      : '/static/assets/pnglogo.png';
  }
  if (!toggle) return;
  const sunSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
  const moonSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  toggle.innerHTML = theme === 'dark' ? sunSvg : moonSvg;
  toggle.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
}

// ── Sidebar toggle (mobile) ───────────────────────────────────────────────────
const sidebarToggleBtn = document.getElementById('sidebar-toggle');
const sidebarEl = document.getElementById('sidebar');
const sidebarBackdropEl = document.getElementById('sidebar-backdrop');

if (sidebarToggleBtn && sidebarEl) {
  sidebarToggleBtn.addEventListener('click', () => {
    sidebarEl.classList.toggle('mobile-expanded');
    if (sidebarBackdropEl) sidebarBackdropEl.classList.toggle('active');
  });
  if (sidebarBackdropEl) {
    sidebarBackdropEl.addEventListener('click', () => {
      sidebarEl.classList.remove('mobile-expanded');
      sidebarBackdropEl.classList.remove('active');
    });
  }
}

// ── Clickable table rows ──────────────────────────────────────────────────────
document.querySelectorAll('tr.clickable[data-href]').forEach(row => {
  row.addEventListener('click', e => {
    if (e.target.closest('a, button, form')) return;
    window.location.href = row.dataset.href;
  });
});

// ── New Document form ─────────────────────────────────────────────────────────
const lineItemsBody = document.getElementById('line-items-body');
if (lineItemsBody) {
  let lineItems = [];

  // ── Render ──────────────────────────────────────────────────────────────────
  function renderRows() {
    lineItemsBody.innerHTML = '';
    lineItems.forEach((item, i) => {
      const total = fmtAmt(item.qty * item.unit_price);
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><input type="text" placeholder="Description" value="${esc(item.description)}"
            data-i="${i}" data-f="description" /></td>
        <td><input type="number" min="0" step="0.01" placeholder="1" value="${item.qty}"
            data-i="${i}" data-f="qty" style="width:70px" /></td>
        <td><input type="number" min="0" step="0.01" placeholder="0.00" value="${item.unit_price}"
            data-i="${i}" data-f="unit_price" /></td>
        <td class="row-total">${total}</td>
        <td><button type="button" class="save-lib-btn" data-i="${i}" title="Save to Library">&#9733;</button></td>
        <td><button type="button" class="remove-line-btn" data-i="${i}" title="Remove">×</button></td>
      `;
      lineItemsBody.appendChild(tr);
    });
    syncHidden();
    recalc();
  }

  // ── Sync hidden input ────────────────────────────────────────────────────────
  function syncHidden() {
    document.getElementById('line_items_json').value = JSON.stringify(lineItems);
  }

  // ── Recalculate totals ───────────────────────────────────────────────────────
  function recalc() {
    const subtotal = lineItems.reduce((s, item) => s + item.qty * item.unit_price, 0);
    const discountVal = parseFloat(document.getElementById('discount_val').value) || 0;
    const discountType = document.getElementById('discount_type').value;
    const taxVal = parseFloat(document.getElementById('tax_val').value) || 0;
    const taxType = document.getElementById('tax_type').value;
    const paid = parseFloat(document.getElementById('paid_amount').value) || 0;

    const discount = discountType === 'percent' ? subtotal * (discountVal / 100) : discountVal;
    const afterDiscount = subtotal - discount;
    const tax = taxType === 'percent' ? afterDiscount * (taxVal / 100) : taxVal;
    const due = afterDiscount + tax - paid;

    document.getElementById('t-subtotal').textContent = fmtAmt(subtotal);
    document.getElementById('t-discount').textContent = fmtAmt(discount);
    document.getElementById('t-tax').textContent = fmtAmt(tax);
    document.getElementById('t-paid').textContent = fmtAmt(paid);
    document.getElementById('t-due').textContent = fmtAmt(due);
  }

  // ── Add line ─────────────────────────────────────────────────────────────────
  document.getElementById('add-line-btn').addEventListener('click', () => {
    lineItems.push({ description: '', qty: 1, unit_price: 0 });
    renderRows();
    const inputs = lineItemsBody.querySelectorAll('tr:last-child input');
    if (inputs[0]) inputs[0].focus();
  });

  // ── Delegate events on table body ────────────────────────────────────────────
  lineItemsBody.addEventListener('input', e => {
    const el = e.target;
    if (!el.dataset.i) return;
    const i = parseInt(el.dataset.i);
    const f = el.dataset.f;
    if (f === 'description') {
      lineItems[i].description = el.value;
    } else {
      lineItems[i][f] = parseFloat(el.value) || 0;
      const row = el.closest('tr');
      const total = fmtAmt(lineItems[i].qty * lineItems[i].unit_price);
      row.querySelector('.row-total').textContent = total;
    }
    syncHidden();
    recalc();
  });

  lineItemsBody.addEventListener('click', e => {
    // Remove button
    const removeBtn = e.target.closest('.remove-line-btn');
    if (removeBtn) {
      lineItems.splice(parseInt(removeBtn.dataset.i), 1);
      renderRows();
      return;
    }

    // Save to Library button
    const saveBtn = e.target.closest('.save-lib-btn');
    if (saveBtn) {
      const i = parseInt(saveBtn.dataset.i);
      const item = lineItems[i];
      if (!item.description.trim()) {
        alert('Please enter a description before saving to library.');
        return;
      }
      const currency = document.getElementById('currency-select')?.value || 'USD';
      fetch('/api/item-library/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: item.description,
          unit_price: item.unit_price,
          currency: currency
        })
      }).then(r => r.json()).then(() => {
        saveBtn.classList.add('saved');
        saveBtn.title = 'Saved!';
        setTimeout(() => {
          saveBtn.classList.remove('saved');
          saveBtn.title = 'Save to Library';
        }, 2000);
      });
      return;
    }
  });

  // ── Totals inputs ────────────────────────────────────────────────────────────
  ['discount_val', 'discount_type', 'tax_val', 'tax_type', 'paid_amount'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', recalc);
  });

  // ── Client select → load templates ──────────────────────────────────────────
  const clientSelect = document.getElementById('client_select');
  const templateRow = document.getElementById('template-row');
  const templateSelect = document.getElementById('template_select');

  if (clientSelect) {
    clientSelect.addEventListener('change', async () => {
      const clientId = clientSelect.value;
      templateRow.classList.add('hidden');
      templateSelect.innerHTML = '<option value="">— None —</option>';
      if (!clientId) return;

      const res = await fetch(`/api/clients/${clientId}/templates`);
      const templates = await res.json();
      if (templates.length === 0) return;

      templates.forEach(t => {
        const opt = document.createElement('option');
        opt.value = JSON.stringify(t);
        opt.textContent = t.template_name;
        templateSelect.appendChild(opt);
      });
      templateRow.classList.remove('hidden');
    });
  }

  document.getElementById('load-template-btn')?.addEventListener('click', () => {
    const val = templateSelect.value;
    if (!val) return;
    const t = JSON.parse(val);
    lineItems = [{
      description: t.service_description || '',
      qty: t.qty || 1,
      unit_price: t.unit_price || 0
    }];

    const currencyEl = document.getElementById('currency-select');
    if (currencyEl && t.currency) currencyEl.value = t.currency;

    document.getElementById('discount_val').value = t.discount || 0;
    document.getElementById('tax_val').value = t.tax_rate || 0;

    renderRows();
  });

  // ── Preview PDF ──────────────────────────────────────────────────────────────
  document.getElementById('preview-btn')?.addEventListener('click', () => {
    syncHidden();
    document.getElementById('doc-form').target = '_self';
    document.getElementById('doc-form').submit();
  });

  // ── Form submit: ensure line items are synced ────────────────────────────────
  document.getElementById('doc-form').addEventListener('submit', () => {
    syncHidden();
  });

  // Init: pre-populate from edit form data if available, otherwise one empty row
  if (typeof EDIT_LINE_ITEMS !== 'undefined' && EDIT_LINE_ITEMS.length) {
    lineItems = EDIT_LINE_ITEMS.map(item => ({
      description: item.description || '',
      qty: Number(item.qty) || 1,
      unit_price: Number(item.unit_price) || 0
    }));
  } else {
    lineItems = [{ description: '', qty: 1, unit_price: 0 }];
  }
  renderRows();

  // ── Import Items Modal ────────────────────────────────────────────────────────
  const modal = document.getElementById('import-modal');
  const modalBody = document.getElementById('modal-body');
  const searchInput = document.getElementById('modal-search-input');
  let allDocs = [];
  let searchTimer = null;

  function openModal() {
    modal.classList.remove('hidden');
    searchInput.value = '';
    loadDocs('');
    searchInput.focus();
  }

  function closeModal() {
    modal.classList.add('hidden');
  }

  document.getElementById('import-items-btn')?.addEventListener('click', openModal);
  document.getElementById('modal-close-btn')?.addEventListener('click', closeModal);
  document.getElementById('modal-cancel-btn')?.addEventListener('click', closeModal);
  modal?.addEventListener('click', e => {
    if (e.target === modal) closeModal();
  });

  searchInput?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadDocs(searchInput.value), 280);
  });

  async function loadDocs(q) {
    modalBody.innerHTML = '<div class="empty-state" style="padding:24px;text-align:center;">Loading…</div>';
    const res = await fetch(`/api/documents/search?q=${encodeURIComponent(q)}`);
    allDocs = await res.json();
    renderModalDocs();
  }

  function renderModalDocs() {
    if (!allDocs.length) {
      modalBody.innerHTML = '<div class="empty-state" style="padding:24px;text-align:center;">No documents found.</div>';
      return;
    }
    const badgeClass = { invoice: 'badge-invoice', quote: 'badge-quote', receipt: 'badge-receipt' };
    modalBody.innerHTML = allDocs.map((doc, di) => {
      const items = (doc.line_items || []).map((item, ii) => `
        <tr>
          <td><input type="checkbox" class="item-check" data-di="${di}" data-ii="${ii}" checked /></td>
          <td>${esc(item.description || '')}</td>
          <td style="text-align:center;">${item.qty}</td>
          <td style="text-align:right;">${fmtAmt(item.unit_price || 0)}</td>
        </tr>`).join('');
      return `
        <div class="modal-doc-row" data-di="${di}">
          <div class="modal-doc-header" data-di="${di}">
            <span class="badge ${badgeClass[doc.doc_type] || ''}">${doc.doc_type}</span>
            <div class="modal-doc-meta">
              <div class="modal-doc-num">${esc(doc.doc_number)}</div>
              <div class="modal-doc-client">${esc(doc.client_name || '—')}</div>
            </div>
            <div>
              <div class="modal-doc-date">${esc(doc.date_issued || '')}</div>
              <div class="modal-doc-total">${esc(doc.currency || '')} ${fmtAmt(doc.amount_due || 0)}</div>
            </div>
            <span class="modal-expand-icon">&#9658;</span>
          </div>
          <div class="modal-doc-items">
            <table class="modal-items-table">
              <thead><tr><th style="width:28px;"></th><th>Description</th><th style="width:50px;text-align:center;">Qty</th><th style="width:80px;text-align:right;">Price</th></tr></thead>
              <tbody>${items}</tbody>
            </table>
            <div class="modal-import-actions">
              <button type="button" class="btn btn-sm btn-gold import-all-btn" data-di="${di}">Import All Items</button>
              <button type="button" class="btn btn-sm btn-outline import-selected-btn" data-di="${di}">Import Selected</button>
            </div>
          </div>
        </div>`;
    }).join('');
  }

  modalBody?.addEventListener('click', e => {
    // Expand/collapse document row
    const header = e.target.closest('.modal-doc-header');
    if (header && !e.target.closest('button')) {
      const di = header.dataset.di;
      const row = modalBody.querySelector(`.modal-doc-row[data-di="${di}"]`);
      row.classList.toggle('expanded');
      return;
    }

    // Import all items
    const importAllBtn = e.target.closest('.import-all-btn');
    if (importAllBtn) {
      const di = parseInt(importAllBtn.dataset.di);
      importItems(allDocs[di].line_items);
      closeModal();
      return;
    }

    // Import selected items
    const importSelBtn = e.target.closest('.import-selected-btn');
    if (importSelBtn) {
      const di = parseInt(importSelBtn.dataset.di);
      const checks = modalBody.querySelectorAll(`.item-check[data-di="${di}"]`);
      const selected = [];
      checks.forEach(cb => {
        if (cb.checked) {
          const ii = parseInt(cb.dataset.ii);
          selected.push(allDocs[di].line_items[ii]);
        }
      });
      if (selected.length) importItems(selected);
      closeModal();
      return;
    }
  });

  function importItems(items) {
    const newItems = items.map(item => ({
      description: item.description || '',
      qty: item.qty || 1,
      unit_price: item.unit_price || 0
    }));
    // Replace empty trailing rows, then append
    lineItems = lineItems.filter(it => it.description.trim() || it.unit_price > 0);
    lineItems = lineItems.concat(newItems);
    if (!lineItems.length) lineItems = [{ description: '', qty: 1, unit_price: 0 }];
    renderRows();
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function fmtAmt(n) {
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── Prevent Enter from submitting document forms ──────────────────────────────
const docForm = document.getElementById('doc-form');
if (docForm) {
  docForm.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') {
      e.preventDefault();
    }
  });
}
