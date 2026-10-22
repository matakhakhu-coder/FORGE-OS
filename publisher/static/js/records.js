/* ═══════════════════════════════════════════════════════════════════════════
   ZA-DIVERGENT — Records (records.html)
   Filters and sorts the table of approved map pins, and downloads the rows
   being shown as a CSV. Everything here is open: no account, no tier.
   The table is rendered by the publisher; this only shows/hides and orders
   rows. The CSV uses the same columns and escaping as tools/records_export.py.
   ═══════════════════════════════════════════════════════════════════════════ */
(function (root) {
  'use strict';

  var FORMULA_START = ['=', '+', '-', '@', '\t', '\r'];
  var NUMBER_RE = /^-?\d+(\.\d+)?$/;

  /* A cell a spreadsheet will not run as a formula (numbers stay as they are). */
  function safeCell(value) {
    if (value === null || value === undefined) return '';
    var text = String(value);
    if (text && FORMULA_START.indexOf(text.charAt(0)) !== -1 && !NUMBER_RE.test(text)) {
      return "'" + text;
    }
    return text;
  }

  /* RFC 4180 quoting, as Python's csv.QUOTE_MINIMAL does it. */
  function quote(text) {
    return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  /* CSV text (with a UTF-8 BOM and CRLF line ends) for these records. */
  function toCsv(fields, records) {
    var lines = [fields.map(quote).join(',')];
    records.forEach(function (r) {
      lines.push(fields.map(function (f) { return quote(safeCell(r[f])); }).join(','));
    });
    return '\ufeff' + lines.join('\r\n') + '\r\n';
  }

  /* Does one record pass the filters? f: {q, province, from, to, precision, independence, basis} */
  function matches(r, f) {
    var day = String(r.incident_start || '').slice(0, 10);
    if (f.province && r.province !== f.province) return false;
    if (f.precision && r.map_precision !== f.precision) return false;
    if (f.independence && r.reporting_independence !== f.independence) return false;
    if (f.basis && r.location_basis !== f.basis) return false;
    if (f.from && day < f.from) return false;
    if (f.to && day > f.to) return false;
    if (f.q) {
      var hay = [r.title, r.province, r.location_described, r.incident_described,
                 r.outlets, r.statement_from, r.short_id].join(' ').toLowerCase();
      var words = String(f.q).toLowerCase().split(/\s+/).filter(Boolean);
      for (var i = 0; i < words.length; i++) if (hay.indexOf(words[i]) === -1) return false;
    }
    return true;
  }

  var SORTS = {
    incident_desc: function (a, b) { return cmp(b.incident_start, a.incident_start) || cmp(b.signal_id, a.signal_id); },
    incident_asc: function (a, b) { return cmp(a.incident_start, b.incident_start) || cmp(a.signal_id, b.signal_id); },
    published_desc: function (a, b) { return cmp(b.published_at, a.published_at) || cmp(b.signal_id, a.signal_id); }
  };
  function cmp(a, b) { a = String(a || ''); b = String(b || ''); return a < b ? -1 : a > b ? 1 : 0; }

  var api = { safeCell: safeCell, toCsv: toCsv, matches: matches, SORTS: SORTS };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  root.ZDRecords = api;

  /* ── Page wiring ──────────────────────────────────────────────────────── */
  function init() {
    var dataEl = document.getElementById('records-data');
    var form = document.getElementById('rec-filters');
    var body = document.getElementById('rec-rows');
    if (!dataEl || !form || !body) return;
    var data = JSON.parse(dataEl.textContent);
    var fields = data.fields.map(function (f) { return f.name; });
    var rows = Array.prototype.slice.call(body.querySelectorAll('tr[data-id]'));
    var countEl = document.getElementById('rec-count');
    var emptyEl = document.getElementById('rec-empty');
    var dlBtn = document.getElementById('rec-download-filtered');
    var shown = data.records.slice();
    var box = document.getElementById('rec-filter-box');

    function read() {
      var v = function (n) { var el = form.elements[n]; return el ? String(el.value || '').trim() : ''; };
      return { q: v('q'), province: v('province'), from: v('from'), to: v('to'),
               precision: v('precision'), independence: v('independence'), basis: v('basis'),
               sort: v('sort') || 'incident_desc' };
    }

    function apply() {
      var f = read();
      var order = data.records.slice().sort(SORTS[f.sort] || SORTS.incident_desc);
      shown = order.filter(function (r) { return matches(r, f); });
      var keep = {};
      shown.forEach(function (r) { keep[r.signal_id] = true; });
      var rowById = {};
      rows.forEach(function (tr) { rowById[tr.getAttribute('data-id')] = tr; });
      order.forEach(function (r) {
        var tr = rowById[r.signal_id];
        if (!tr) return;
        tr.hidden = !keep[r.signal_id];
        body.appendChild(tr);
      });
      countEl.textContent = 'Showing ' + shown.length + ' of ' + data.records.length + ' records';
      var active = ['q', 'province', 'from', 'to', 'precision', 'independence', 'basis']
        .filter(function (k) { return f[k]; }).length;
      var sum = box && box.querySelector('summary');
      if (sum) sum.firstChild.nodeValue = active ? 'Filter and sort (' + active + ' on)' : 'Filter and sort';
      emptyEl.hidden = shown.length !== 0;
      dlBtn.disabled = shown.length === 0;
      dlBtn.textContent = shown.length === data.records.length
        ? 'Download these rows (CSV)'
        : 'Download these ' + shown.length + ' rows (CSV)';
    }

    form.addEventListener('input', apply);
    form.addEventListener('change', apply);
    form.addEventListener('submit', function (e) { e.preventDefault(); apply(); });
    form.addEventListener('reset', function () { setTimeout(apply, 0); });

    dlBtn.addEventListener('click', function () {
      var csv = toCsv(fields, shown);
      var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'za-divergent-records-' + (data.data_version || 'data').slice(0, 7) + '-filtered.csv';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
    });

    // Filters start open on wide screens and folded on phones, so the
    // records are the first thing a phone shows after the caveats.
    if (box && root.matchMedia && root.matchMedia('(min-width: 761px)').matches) box.open = true;

    document.documentElement.classList.add('rec-js');
    apply();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window !== 'undefined' ? window : this);
