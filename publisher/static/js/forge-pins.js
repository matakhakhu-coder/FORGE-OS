/* FORGE Map — shared pin loader and the 30-day live cut (time-aware pins, Batch 5).
 *
 * One place that answers three questions for every page that shows pins:
 *
 *   1. What day is it in South Africa?  ForgePins.today()
 *   2. Is this pin on the live map?      ForgePins.isLive(pin)
 *      A pin is live while its incident day is today or one of the 29 days
 *      before it (the settled 30-day window). Its age counts from the
 *      incident date, worked out here in the browser from today's date, so
 *      a pin leaves the live map on time even when nothing is published.
 *   3. Where is a pin that is not on this page?  ForgePins.findPin(id) and
 *      ForgePins.loadMonths(range)
 *      The page carries only the live months (tools/map_archive.py). Older
 *      pins sit in one file per calendar month, listed in archive/index.json.
 *      The loader fetches the index and a month only when they are needed,
 *      keeps them for the visit, and never returns the same pin twice.
 *
 * The day rules mirror tools/map_archive.py exactly (incident_start, then
 * incident_at, then published_at; a time without an offset is South African
 * time; South Africa has no daylight saving), and a test runs both on the
 * same values. A pin with no readable date is never hidden: the build
 * refuses such a pin, and if one slipped through it would stay on the map.
 *
 * No dependencies, no DOM. Exposes window.ForgePins.
 */
(function (root) {
  'use strict';

  var SAST_OFFSET_MS = 2 * 60 * 60 * 1000;   // UTC+02:00 all year
  var DAY_MS = 24 * 60 * 60 * 1000;
  var LIVE_DAYS = 30;                         // today plus the 29 days before it
  var DATE_KEYS = ['incident_start', 'incident_at', 'published_at'];
  var MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                     'August', 'September', 'October', 'November', 'December'];

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  // 'YYYY-MM-DD' for the South African day an instant (ms since epoch) falls on.
  function dayOfInstant(ms) {
    var d = new Date(ms + SAST_OFFSET_MS);
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }

  function realDate(y, m, d) {
    if (m < 1 || m > 12 || d < 1) return false;
    var dt = new Date(Date.UTC(y, m - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  }

  var DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
  var DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2})(?::(\d{2})(?::(\d{2})(?:[.,](\d{1,6}))?)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/;

  // The South African calendar day of an ISO date or date-time, or null.
  function localDay(value) {
    if (typeof value !== 'string') return null;
    var v = value.trim();
    if (!v) return null;
    var m = DATE_ONLY.exec(v);
    if (m) return realDate(+m[1], +m[2], +m[3]) ? v : null;
    m = DATE_TIME.exec(v);
    if (!m) return null;
    var y = +m[1], mo = +m[2], d = +m[3];
    var hh = +m[4], mi = m[5] ? +m[5] : 0, ss = m[6] ? +m[6] : 0;
    if (!realDate(y, mo, d) || hh > 23 || mi > 59 || ss > 59) return null;
    var offsetMs = SAST_OFFSET_MS;               // no offset: South African time
    var tz = m[8];
    if (tz === 'Z') offsetMs = 0;
    else if (tz) {
      var sign = tz.charAt(0) === '-' ? -1 : 1;
      var digits = tz.slice(1).replace(':', '');
      var oh = +digits.slice(0, 2), om = digits.length > 2 ? +digits.slice(2, 4) : 0;
      if (oh > 23 || om > 59) return null;
      offsetMs = sign * (oh * 60 + om) * 60 * 1000;
    }
    var utcMs = Date.UTC(y, mo - 1, d, hh, mi, ss) - offsetMs;
    return dayOfInstant(utcMs);
  }

  // The day a pin's age counts from, as 'YYYY-MM-DD', or null.
  function incidentDay(pin) {
    if (!pin) return null;
    for (var i = 0; i < DATE_KEYS.length; i++) {
      var d = localDay(pin[DATE_KEYS[i]]);
      if (d) return d;
    }
    return null;
  }

  function dayNumber(day) {
    var m = DATE_ONLY.exec(day);
    return Date.UTC(+m[1], +m[2] - 1, +m[3]) / DAY_MS;
  }

  function nowMs(now) {
    if (now == null) return Date.now();
    if (now instanceof Date) return now.getTime();
    return +now;
  }

  // Today's South African date. now: optional ms or Date (tests pass one).
  function today(now) { return dayOfInstant(nowMs(now)); }

  // Whole days from the pin's incident day to today (0 = today), or null.
  function ageDays(pin, now) {
    var d = incidentDay(pin);
    if (!d) return null;
    return dayNumber(today(now)) - dayNumber(d);
  }

  // On the live map: incident today or in the 29 days before. A pin dated in
  // the future, or with no readable date, stays live.
  function isLive(pin, now) {
    var a = ageDays(pin, now);
    return a === null || a < LIVE_DAYS;
  }

  // The first day still live today: 'YYYY-MM-DD'.
  function liveFrom(now) {
    var n = dayNumber(today(now)) - (LIVE_DAYS - 1);
    return dayOfInstant(n * DAY_MS - SAST_OFFSET_MS + 12 * 60 * 60 * 1000);
  }

  /* ── Age ring, Updated mark and the dates line (Batch 7) ───────────────────
     A pin's age shows as a still ring that steps down through four bands
     across the live window (0 to 2 days, 3 to 7, 8 to 14, 15 to 29). Past
     the window (an archived pin a lookup has drawn) there is no ring. Age
     always counts from the incident day; an update never changes it.

     The Updated mark shows from the South African day of an approved
     material update (last_material_update_at) to 7 days after it: an update
     on 24 October shows through 31 October and is gone on 1 November. */

  var AGE_BANDS = [
    { band: 0, from: 0, to: 2, label: '0 to 2 days' },
    { band: 1, from: 3, to: 7, label: '3 to 7 days' },
    { band: 2, from: 8, to: 14, label: '8 to 14 days' },
    { band: 3, from: 15, to: 29, label: '15 to 29 days' }
  ];
  var UPDATED_DAYS = 7;
  var SHORT_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct',
                      'Nov', 'Dec'];

  // The age band of a pin today: one of AGE_BANDS, or null (past the live
  // window, or no readable date). A pin dated later than today counts as 0.
  function ageBand(pin, now) {
    var a = ageDays(pin, now);
    if (a === null || a >= LIVE_DAYS) return null;
    if (a < 0) a = 0;
    for (var i = 0; i < AGE_BANDS.length; i++) {
      if (a <= AGE_BANDS[i].to) return AGE_BANDS[i];
    }
    return null;
  }

  // The South African day of the pin's last approved material update, or null.
  function updatedDay(pin) {
    return pin ? localDay(pin.last_material_update_at) : null;
  }

  // Whole days since the last material update (0 = today), or null.
  function updatedDaysAgo(pin, now) {
    var d = updatedDay(pin);
    if (!d) return null;
    return dayNumber(today(now)) - dayNumber(d);
  }

  // The Updated mark shows on the update's day and the 7 days after it.
  function showUpdated(pin, now) {
    var n = updatedDaysAgo(pin, now);
    return n !== null && n <= UPDATED_DAYS;
  }

  // '12 Oct', or '12 Oct 2025' when the year is not this year.
  function shortDate(day, now) {
    var m = DATE_ONLY.exec(String(day || ''));
    if (!m) return '';
    var s = (+m[3]) + ' ' + SHORT_MONTHS[+m[2] - 1];
    return m[1] === today(now).slice(0, 4) ? s : s + ' ' + m[1];
  }

  // The day the incident happened: incident_start, then incident_at. Not
  // published_at: a pin with no incident date never says "Happened".
  function happenedDay(pin) {
    if (!pin) return null;
    return localDay(pin.incident_start) || localDay(pin.incident_at);
  }

  // "Happened 12 Oct, added 19 Oct, last updated 24 Oct" (parts it has).
  function datesLine(pin, now) {
    var parts = [];
    var h = happenedDay(pin), a = pin ? localDay(pin.published_at) : null, u = updatedDay(pin);
    if (h) parts.push('Happened ' + shortDate(h, now));
    if (a) parts.push('added ' + shortDate(a, now));
    if (u) parts.push('last updated ' + shortDate(u, now));
    var s = parts.join(', ');
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
  }

  // How old a pin is, in words: 'today', 'yesterday', '12 days ago', or ''.
  function ageWords(pin, now) {
    var a = ageDays(pin, now);
    if (a === null) return '';
    if (a <= 0) return 'today';
    if (a === 1) return 'yesterday';
    return a + ' days ago';
  }

  function monthOf(pin) {
    var d = incidentDay(pin);
    return d ? d.slice(0, 7) : null;
  }

  function monthLabel(key) {
    var m = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
    if (!m || +m[2] < 1 || +m[2] > 12) return '';
    return MONTH_NAMES[+m[2] - 1] + ' ' + m[1];
  }

  /* ── The month archive ─────────────────────────────────────────────────── */

  var base = '';          // where archive/ sits, relative to the page
  var indexPromise = null;
  var indexDoc = null;    // archive/index.json once loaded
  var monthPromises = {}; // month file -> promise of its pins
  var fetches = [];       // every archive URL fetched, for the checks

  function configure(opts) {
    opts = opts || {};
    if (typeof opts.base === 'string') base = opts.base;
  }

  function getJSON(url) {
    fetches.push(url);
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
      return r.json();
    });
  }

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = getJSON(base + 'archive/index.json').then(function (doc) {
        if (!doc || !Array.isArray(doc.months) || !doc.pins || typeof doc.pins !== 'object') {
          throw new Error('archive index is malformed');
        }
        indexDoc = doc;
        return doc;
      });
      // A failed fetch can be tried again later.
      indexPromise.catch(function () { indexPromise = null; });
    }
    return indexPromise;
  }

  function monthRow(doc, key) {
    for (var i = 0; i < doc.months.length; i++) {
      if (doc.months[i].month === key) return doc.months[i];
    }
    return null;
  }

  // The pins of one month ([] for a month the archive does not list).
  function loadMonth(key) {
    return loadIndex().then(function (doc) {
      var row = monthRow(doc, key);
      if (!row || !row.file) return [];
      var file = row.file;
      if (!monthPromises[file]) {
        monthPromises[file] = getJSON(base + file).then(function (m) {
          if (!m || m.month !== key || !Array.isArray(m.features)) {
            throw new Error('month file ' + file + ' is malformed');
          }
          return m.features.map(function (f) { return f && f.properties; })
            .filter(function (p) { return p && p.signal_id; });
        });
        monthPromises[file].catch(function () { delete monthPromises[file]; });
      }
      return monthPromises[file];
    });
  }

  function idOf(p) { return String((p && p.signal_id) || ''); }

  // One copy of each pin, first seen wins, in the order given.
  function dedupe() {
    var seen = {}, out = [];
    for (var a = 0; a < arguments.length; a++) {
      var list = arguments[a] || [];
      for (var i = 0; i < list.length; i++) {
        var id = idOf(list[i]);
        if (!id || seen[id]) continue;
        seen[id] = true;
        out.push(list[i]);
      }
    }
    return out;
  }

  function monthKeyOf(v) {
    var s = String(v || '');
    var m = /^(\d{4}-\d{2})(?:-\d{2})?$/.exec(s);
    return m ? m[1] : null;
  }

  // Which archive months a range touches. range: 'all'; an array of
  // 'YYYY-MM'; or { from, to } with 'YYYY-MM' or 'YYYY-MM-DD' ends (either
  // end may be left out). A day range loads every month it touches; the
  // caller still filters to the exact days.
  function monthsFor(doc, range) {
    var keys = doc.months.map(function (r) { return r.month; });
    if (range === 'all' || range == null) return keys;
    if (Array.isArray(range)) {
      var want = {};
      range.forEach(function (k) { var mk = monthKeyOf(k); if (mk) want[mk] = true; });
      return keys.filter(function (k) { return want[k]; });
    }
    var from = monthKeyOf(range.from), to = monthKeyOf(range.to);
    return keys.filter(function (k) {
      return (!from || k >= from) && (!to || k <= to);
    });
  }

  // Every pin in the months a range touches, each once.
  function loadMonths(range) {
    return loadIndex().then(function (doc) {
      var keys = monthsFor(doc, range);
      return Promise.all(keys.map(loadMonth)).then(function (lists) {
        return dedupe.apply(null, lists);
      });
    });
  }

  function cleanId(param) {
    var s = String(param || '').trim().toLowerCase();
    return /^[0-9a-f-]{8,}$/.test(s) ? s : '';
  }

  // Find one pin by full id or 8-character short id, through the index's
  // lookup and its month file. Resolves { pin, month } or null.
  function findPin(param) {
    var sid = cleanId(param);
    if (!sid) return Promise.resolve(null);
    return loadIndex().then(function (doc) {
      var month = doc.pins[sid.slice(0, 8)];
      if (!month) return null;
      return loadMonth(month).then(function (pins) {
        var exact = null, short = [];
        pins.forEach(function (p) {
          var id = idOf(p).toLowerCase();
          if (id === sid) exact = p;
          else if (id.slice(0, 8) === sid) short.push(p);
        });
        var hit = exact || (sid.length === 8 && short.length === 1 ? short[0] : null);
        return hit ? { pin: hit, month: month } : null;
      });
    });
  }

  // Synchronous answers once the index is in; null while it is not.
  function indexLoaded() { return !!indexDoc; }
  function knownMonth(param) {
    if (!indexDoc) return null;
    var sid = cleanId(param);
    return sid ? (indexDoc.pins[sid.slice(0, 8)] || '') : '';
  }

  root.ForgePins = {
    LIVE_DAYS: LIVE_DAYS,
    localDay: localDay,
    incidentDay: incidentDay,
    today: today,
    ageDays: ageDays,
    isLive: isLive,
    liveFrom: liveFrom,
    monthOf: monthOf,
    monthLabel: monthLabel,
    AGE_BANDS: AGE_BANDS,
    UPDATED_DAYS: UPDATED_DAYS,
    ageBand: ageBand,
    updatedDay: updatedDay,
    updatedDaysAgo: updatedDaysAgo,
    showUpdated: showUpdated,
    shortDate: shortDate,
    happenedDay: happenedDay,
    datesLine: datesLine,
    ageWords: ageWords,
    configure: configure,
    loadIndex: loadIndex,
    loadMonth: loadMonth,
    loadMonths: loadMonths,
    findPin: findPin,
    dedupe: dedupe,
    indexLoaded: indexLoaded,
    knownMonth: knownMonth,
    cleanId: cleanId,
    _fetches: function () { return fetches.slice(); }
  };
})(typeof window !== 'undefined' ? window : this);
