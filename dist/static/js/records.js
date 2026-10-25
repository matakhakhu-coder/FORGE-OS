/* ═══════════════════════════════════════════════════════════════════════════
   ZA-DIVERGENT — Records (records.html)
   Filters and sorts the table of approved map pins, and downloads the rows
   being shown as a CSV. Everything here is open: no account, no tier.
   The table is rendered by the publisher; this only shows/hides and orders
   rows. The CSV uses the same columns and escaping as tools/records_export.py.
   The Incident from and to dates live in the page address
   (records.html?from=YYYY-MM-DD&to=YYYY-MM-DD), so a date range can be shared
   and cited as a link; dates are South African calendar days.
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


  /* ── South African days ───────────────────────────────────────────────────
     Records filter by the South African calendar day an incident started on,
     the same rule as the map (static/js/forge-pins.js): a time without an
     offset is South African time, and South Africa is UTC+2 all year. */
  var SAST_OFFSET_MS = 2 * 60 * 60 * 1000;
  var DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
  var STAMP_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2})(?::(\d{2})(?::(\d{2})(?:[.,]\d{1,6})?)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/;
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                'August', 'September', 'October', 'November', 'December'];
  var MIN_YEAR = 1900, MAX_YEAR = 2100;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function realDate(y, m, d) {
    if (m < 1 || m > 12 || d < 1) return false;
    var dt = new Date(Date.UTC(y, m - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  }

  /* 'YYYY-MM-DD': the South African day an incident_start falls on ('' if unreadable). */
  function saDay(value) {
    var v = String(value === null || value === undefined ? '' : value).trim();
    var m = DAY_RE.exec(v);
    if (m) return realDate(+m[1], +m[2], +m[3]) ? v : '';
    m = STAMP_RE.exec(v);
    if (!m || !realDate(+m[1], +m[2], +m[3])) return v.slice(0, 10);
    var tz = m[7], off = SAST_OFFSET_MS;
    if (tz === 'Z') off = 0;
    else if (tz) {
      var t = tz.replace(':', '');
      off = (t.charAt(0) === '-' ? -1 : 1) * ((+t.slice(1, 3)) * 60 + (t.length > 3 ? +t.slice(3, 5) : 0)) * 60000;
    }
    var ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], m[5] ? +m[5] : 0, m[6] ? +m[6] : 0) - off;
    var d = new Date(ms + SAST_OFFSET_MS);
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }

  /* Is this a real calendar day the filter can use? */
  function validDay(v) {
    var m = DAY_RE.exec(String(v || ''));
    return !!m && +m[1] >= MIN_YEAR && +m[1] <= MAX_YEAR && realDate(+m[1], +m[2], +m[3]);
  }

  /* '1 October 2026' */
  function dayLabel(v) {
    var m = DAY_RE.exec(String(v || ''));
    return m ? (+m[3]) + ' ' + MONTHS[+m[2] - 1] + ' ' + m[1] : String(v);
  }

  function clip(v) { v = String(v); return v.length > 24 ? v.slice(0, 24) + '…' : v; }

  /* The date range a page address asks for.
     search: location.search (or any query string).
     Returns {from, to, notes}: from/to are 'YYYY-MM-DD' or ''; notes are
     plain sentences for anything that was ignored or changed. A date that is
     not a real day is ignored; dates the wrong way round are swapped. */
  function parseRange(search) {
    var out = { from: '', to: '', notes: [] };
    var params;
    try { params = new URLSearchParams(String(search || '')); } catch (e) { return out; }
    [['from', 'from'], ['to', 'to']].forEach(function (pair) {
      var key = pair[0];
      if (!params.has(key)) return;
      var raw = String(params.get(key) || '').trim();
      if (!raw) return;
      if (validDay(raw)) out[key] = raw;
      else out.notes.push('The “' + key + '” date in this link (' + clip(raw) +
        ') is not a real date in the form YYYY-MM-DD, so it was ignored.');
    });
    if (out.from && out.to && out.from > out.to) {
      var t = out.from; out.from = out.to; out.to = t;
      out.notes.push('The dates in this link were the wrong way round, so they were swapped: ' +
        dayLabel(out.from) + ' to ' + dayLabel(out.to) + '.');
    }
    return out;
  }

  /* The page address for this range, keeping every other part of the address
     (?stream= and anything else) as it was. An empty date leaves the address. */
  function rangeSearch(search, from, to) {
    var params;
    try { params = new URLSearchParams(String(search || '')); } catch (e) { params = new URLSearchParams(''); }
    params.delete('from'); params.delete('to');
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    var q = params.toString();
    return q ? '?' + q : '';
  }

  /* A sentence about a range against the records' own span, or ''.
     span: {first, last} South African days of the earliest and latest incident. */
  function rangeNote(from, to, span) {
    if (from && to && from > to) {
      return 'The “from” date is after the “to” date, so no records can match. Swap them to see that range.';
    }
    if (!span || !span.first) return '';
    var spanText = 'Records run from ' + dayLabel(span.first) + ' to ' + dayLabel(span.last) + '.';
    if ((to && to < span.first) || (from && from > span.last)) {
      return 'No records fall in this range. ' + spanText;
    }
    return '';
  }

  /* Does one record pass the filters? f: {q, stream, province, from, to, precision, independence, basis} */
  function matches(r, f) {
    var day = saDay(r.incident_start);
    if (f.stream && r.stream !== f.stream) return false;
    if (f.province && r.province !== f.province) return false;
    if (f.precision && r.map_precision !== f.precision) return false;
    if (f.independence && r.reporting_independence !== f.independence) return false;
    if (f.basis && r.location_basis !== f.basis) return false;
    if (f.from && day < f.from) return false;
    if (f.to && day > f.to) return false;
    if (f.q) {
      var hay = [r.title, r.stream, r.province, r.location_described, r.incident_described,
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

  var api = { safeCell: safeCell, toCsv: toCsv, matches: matches, SORTS: SORTS, saDay: saDay,
              validDay: validDay, dayLabel: dayLabel, parseRange: parseRange, rangeSearch: rangeSearch,
              rangeNote: rangeNote };
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
    var tabs = Array.prototype.slice.call(document.querySelectorAll('#rec-streams [data-stream]'));
    var streamSel = form.elements.stream;

    var noteEl = document.getElementById('rec-range-note');
    var span = { first: '', last: '' };
    data.records.forEach(function (r) {
      var d = saDay(r.incident_start);
      if (!d) return;
      if (!span.first || d < span.first) span.first = d;
      if (!span.last || d > span.last) span.last = d;
    });
    var linkNotes = [];          // what the page address asked for that could not be used
    var lastWrite = -Infinity;   // when the address last changed, for one Back step per edit
    var BURST_MS = 1200;
    var hist = root.history && typeof root.history.pushState === 'function' ? root.history : null;

    function read() {
      var v = function (n) { var el = form.elements[n]; return el ? String(el.value || '').trim() : ''; };
      var f = { q: v('q'), stream: v('stream'), province: v('province'), from: v('from'), to: v('to'),
                precision: v('precision'), independence: v('independence'), basis: v('basis'),
                sort: v('sort') || 'incident_desc', bad: [] };
      // A browser without a date picker shows a text box: half-typed dates are not used.
      ['from', 'to'].forEach(function (k) {
        if (f[k] && !validDay(f[k])) { f.bad.push(k); f[k] = ''; }
      });
      return f;
    }

    function setRange(range) {
      if (form.elements.from) form.elements.from.value = range.from;
      if (form.elements.to) form.elements.to.value = range.to;
    }

    /* Put the range in the page address. One editing burst is one Back step:
       the first change after a pause adds a history entry, quick follow-ups
       (typing a date part by part) replace it. */
    function writeAddress(f, how) {
      if (!hist) return;
      var cur;
      try { cur = new URLSearchParams(root.location.search); } catch (e) { return; }
      if (cur.get('from') === (f.from || null) && cur.get('to') === (f.to || null)) return;
      var next = rangeSearch(root.location.search, f.from, f.to);
      var url = root.location.pathname + next + root.location.hash;
      // performance.now: a steady clock that a changed system date can't jump.
      var now = root.performance && root.performance.now ? root.performance.now() : Date.now();
      var mode = how || (now - lastWrite < BURST_MS ? 'replace' : 'push');
      try {
        if (mode === 'push') hist.pushState({ recRange: true }, '', url);
        else hist.replaceState(hist.state, '', url);
      } catch (e) { /* file:// or a locked-down browser: the filter still works */ }
      lastWrite = now;
    }

    function showNotes(f) {
      if (!noteEl) return;
      var notes = linkNotes.slice();
      f.bad.forEach(function (k) {
        notes.push('The “' + k + '” date is not a full date yet (YYYY-MM-DD), so it is not used.');
      });
      var extra = rangeNote(f.from, f.to, span);
      if (extra) notes.push(extra);
      var text = notes.join(' ');
      if (noteEl.textContent !== text) noteEl.textContent = text;
      noteEl.hidden = !text;
    }

    function rangeWords(f) {
      if (f.from && f.to) return 'incidents from ' + dayLabel(f.from) + ' to ' + dayLabel(f.to);
      if (f.from) return 'incidents from ' + dayLabel(f.from) + ' on';
      if (f.to) return 'incidents up to ' + dayLabel(f.to);
      return '';
    }

    function apply(opts) {
      opts = opts || {};
      var f = read();
      if (opts.dateEdit) linkNotes = [];
      if (!opts.noWrite) writeAddress(f, opts.how);
      showNotes(f);
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
      var rw = rangeWords(f);
      countEl.textContent = 'Showing ' + shown.length + ' of ' + data.records.length + ' records' + (rw ? ', ' + rw : '');
      var active = ['q', 'stream', 'province', 'from', 'to', 'precision', 'independence', 'basis']
        .filter(function (k) { return f[k]; }).length;
      tabs.forEach(function (t) {
        t.setAttribute('aria-pressed', String(t.getAttribute('data-stream') === f.stream));
      });
      var sum = box && box.querySelector('summary');
      if (sum) sum.firstChild.nodeValue = active ? 'Filter and sort (' + active + ' on)' : 'Filter and sort';
      emptyEl.hidden = shown.length !== 0;
      dlBtn.disabled = shown.length === 0;
      dlBtn.textContent = shown.length === data.records.length
        ? 'Download these rows (CSV)'
        : 'Download these ' + shown.length + ' rows (CSV)';
    }

    function onEdit(e) {
      var n = e && e.target && e.target.name;
      apply({ dateEdit: n === 'from' || n === 'to' });
    }
    form.addEventListener('input', onEdit);
    form.addEventListener('change', onEdit);
    form.addEventListener('submit', function (e) { e.preventDefault(); apply(); });
    form.addEventListener('reset', function () {
      setTimeout(function () { linkNotes = []; apply({ how: 'push' }); }, 0);
    });

    // records.html?from=2026-10-01&to=2026-10-15 opens on that range. Back
    // and Forward move between ranges without reloading the page.
    var start = parseRange(root.location.search);
    setRange(start);
    linkNotes = start.notes;
    root.addEventListener('popstate', function () {
      var r = parseRange(root.location.search);
      setRange(r);
      linkNotes = r.notes;
      lastWrite = -Infinity;
      apply({ noWrite: true });
    });

    // Stream tabs: one stream at a time, kept in step with the Stream filter.
    // records.html?stream=Infrastructure opens on that stream.
    tabs.forEach(function (t) {
      t.addEventListener('click', function () {
        if (streamSel) streamSel.value = t.getAttribute('data-stream');
        apply();
      });
    });
    try {
      var want = new URLSearchParams(root.location.search).get('stream');
      if (want && streamSel) {
        for (var i = 0; i < streamSel.options.length; i++) {
          if (streamSel.options[i].value.toLowerCase() === want.toLowerCase()) streamSel.value = streamSel.options[i].value;
        }
      }
    } catch (e) { /* old browsers: start on all streams */ }

    dlBtn.addEventListener('click', function () {
      var csv = toCsv(fields, shown);
      var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'forge-map-records-' + (data.data_version || 'data').slice(0, 7) + '-filtered.csv';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
    });

    // Filters start open on wide screens and folded on phones, so the
    // records are the first thing a phone shows after the caveats.
    if (box && root.matchMedia && root.matchMedia('(min-width: 761px)').matches) box.open = true;

    // A range from the address opens the filters, so the dates in use are on
    // screen on a phone too.
    if (box && (start.from || start.to || start.notes.length)) box.open = true;

    document.documentElement.classList.add('rec-js');
    // Tidy the address once: an ignored or swapped date is shown as it is used.
    apply({ how: 'replace' });
    lastWrite = -Infinity;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof window !== 'undefined' ? window : this);
