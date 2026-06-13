'use strict';

// Custom date picker — replaces all input[type="date"] on desktop.
// On touch-primary devices (phones/tablets), leaves native pickers in place.

(function () {

  if (window.matchMedia('(pointer: coarse)').matches) return;

  var MONTHS = [
    'January','February','March','April','May','June',
    'July','August','September','October','November','December'
  ];
  var DOW = ['Su','Mo','Tu','We','Th','Fr','Sa'];

  var activePicker = null;

  // ── Global listeners ──────────────────────────────────────────────────────────

  document.addEventListener('pointerdown', function (e) {
    if (!activePicker) return;
    if (!activePicker.wrap.contains(e.target) && !activePicker.pop.contains(e.target)) {
      activePicker.close();
    }
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && activePicker) activePicker.close();
  });

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function parseISO(str) {
    if (!str || !/^\d{4}-\d{2}-\d{2}$/.test(str)) return null;
    var p = str.split('-');
    return { y: +p[0], m: +p[1], d: +p[2] };
  }

  function toISO(y, m, d) {
    return y + '-' + pad(m) + '-' + pad(d);
  }

  function toDisplay(y, m, d) {
    return pad(d) + ' ' + MONTHS[m - 1] + ' ' + y;
  }

  function pad(n) {
    return n < 10 ? '0' + n : '' + n;
  }

  // ── Build one picker ──────────────────────────────────────────────────────────

  function buildPicker(input) {
    var today = new Date();
    var todayY = today.getFullYear();
    var todayM = today.getMonth() + 1;
    var todayD = today.getDate();

    var selected = parseISO(input.value);
    var view = selected
      ? { y: selected.y, m: selected.m }
      : { y: todayY, m: todayM };

    // ── DOM: wrapper around original input ────────────────────────────────────

    var wrap = document.createElement('div');
    wrap.className = 'qpicker';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.classList.add('qpicker-real');   // visually hidden; submits with form

    // ── DOM: visible trigger ──────────────────────────────────────────────────

    var trigger = document.createElement('div');
    trigger.className = 'qpicker-trigger form-control';
    trigger.tabIndex = 0;
    trigger.setAttribute('role', 'button');
    trigger.setAttribute('aria-haspopup', 'true');
    trigger.setAttribute('aria-expanded', 'false');

    var valEl = document.createElement('span');
    valEl.className = 'qpicker-val';

    var iconSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    iconSvg.setAttribute('width', '15');
    iconSvg.setAttribute('height', '15');
    iconSvg.setAttribute('viewBox', '0 0 24 24');
    iconSvg.setAttribute('fill', 'currentColor');
    iconSvg.className = 'qpicker-icon';
    iconSvg.innerHTML = '<path d="M19 3h-1V1h-2v2H8V1H6v2H5C3.9 3 3 3.9 3 5v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V8h14v11zm-7-7h5v5h-5z"/>';

    trigger.appendChild(valEl);
    trigger.appendChild(iconSvg);
    wrap.insertBefore(trigger, input);

    // ── DOM: popup (appended to body to escape overflow:hidden parents) ────────

    var pop = document.createElement('div');
    pop.className = 'qpicker-pop';
    pop.setAttribute('role', 'dialog');
    document.body.appendChild(pop);

    var navRow = document.createElement('div');
    navRow.className = 'qpicker-nav';

    var prevBtn = document.createElement('button');
    prevBtn.type = 'button';
    prevBtn.className = 'qpicker-arrow';
    prevBtn.setAttribute('aria-label', 'Previous month');
    prevBtn.innerHTML = '&#8249;';

    var headingBtn = document.createElement('button');
    headingBtn.type = 'button';
    headingBtn.className = 'qpicker-heading';

    var nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'qpicker-arrow';
    nextBtn.setAttribute('aria-label', 'Next month');
    nextBtn.innerHTML = '&#8250;';

    navRow.appendChild(prevBtn);
    navRow.appendChild(headingBtn);
    navRow.appendChild(nextBtn);

    var grid = document.createElement('div');
    grid.className = 'qpicker-grid';

    pop.appendChild(navRow);
    pop.appendChild(grid);

    // ── Render grid ───────────────────────────────────────────────────────────

    function renderGrid() {
      headingBtn.textContent = MONTHS[view.m - 1] + ' ' + view.y;
      grid.innerHTML = '';

      DOW.forEach(function (label) {
        var hd = document.createElement('span');
        hd.className = 'qpicker-dow';
        hd.textContent = label;
        grid.appendChild(hd);
      });

      var firstDow = new Date(view.y, view.m - 1, 1).getDay();
      var daysInMonth = new Date(view.y, view.m, 0).getDate();

      for (var i = 0; i < firstDow; i++) {
        var blank = document.createElement('span');
        blank.className = 'qpicker-blank';
        grid.appendChild(blank);
      }

      for (var d = 1; d <= daysInMonth; d++) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'qpicker-day';
        btn.textContent = d;

        if (d === todayD && view.m === todayM && view.y === todayY) {
          btn.classList.add('is-today');
        }
        if (selected && d === selected.d && view.m === selected.m && view.y === selected.y) {
          btn.classList.add('is-selected');
        }

        (function (day) {
          btn.addEventListener('click', function () { selectDay(day); });
        }(d));

        grid.appendChild(btn);
      }
    }

    function updateTrigger() {
      if (selected) {
        valEl.textContent = toDisplay(selected.y, selected.m, selected.d);
        valEl.classList.remove('is-placeholder');
      } else {
        valEl.textContent = 'Select date';
        valEl.classList.add('is-placeholder');
      }
    }

    function selectDay(d) {
      selected = { y: view.y, m: view.m, d: d };
      input.value = toISO(selected.y, selected.m, selected.d);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      updateTrigger();
      self.close();
    }

    // ── Position popup ────────────────────────────────────────────────────────

    function positionPop() {
      var rect = trigger.getBoundingClientRect();
      var popWidth = Math.max(rect.width, 272);
      var spaceBelow = window.innerHeight - rect.bottom;
      var spaceRight = window.innerWidth - rect.left;

      pop.style.width = popWidth + 'px';

      // Horizontal: align to left of trigger, shift left if it would overflow right
      var left = rect.left;
      if (left + popWidth > window.innerWidth - 8) {
        left = window.innerWidth - popWidth - 8;
      }
      pop.style.left = Math.max(8, left) + 'px';

      // Vertical: below or above
      if (spaceBelow < 300 && rect.top > 300) {
        pop.style.top = 'auto';
        pop.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
      } else {
        pop.style.top = (rect.bottom + 4) + 'px';
        pop.style.bottom = 'auto';
      }
    }

    // ── Open / close ──────────────────────────────────────────────────────────

    function open() {
      if (activePicker && activePicker !== self) activePicker.close();

      if (selected) { view.y = selected.y; view.m = selected.m; }
      else { view.y = todayY; view.m = todayM; }

      renderGrid();
      pop.classList.add('is-open');
      trigger.setAttribute('aria-expanded', 'true');
      activePicker = self;

      positionPop();
    }

    function close() {
      pop.classList.remove('is-open');
      trigger.setAttribute('aria-expanded', 'false');
      if (activePicker === self) activePicker = null;
    }

    var self = { wrap: wrap, pop: pop, close: close };

    // ── Events ────────────────────────────────────────────────────────────────

    trigger.addEventListener('click', function () {
      pop.classList.contains('is-open') ? close() : open();
    });

    trigger.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        pop.classList.contains('is-open') ? close() : open();
      }
    });

    prevBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      view.m--;
      if (view.m < 1) { view.m = 12; view.y--; }
      renderGrid();
    });

    nextBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      view.m++;
      if (view.m > 12) { view.m = 1; view.y++; }
      renderGrid();
    });

    updateTrigger();
  }

  // ── Init ──────────────────────────────────────────────────────────────────────

  document.querySelectorAll('input[type="date"]').forEach(buildPicker);

}());
