/* ZA Divergent — Area check (address radar, Sprints A, B and C).
 *
 * A visitor picks a suburb or town from a built-in place list (searched on
 * their own device; nothing they type there is sent anywhere), types a street
 * address (Sprint C: sent to the address service in GEOCODER below, only when
 * they press Search), or places a point on the map, sets a radius and dates,
 * and sees the reports inside. A home-base button flies back to the place.
 *
 * The top half is plain functions (distance, inside / may-be-inside, date
 * overlap, place search, saved-place checks), tested in Node by
 * tests/js/area_check.test.js. mount() wires them to the map page.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ZADArea = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var RADII = [500, 1000, 2000, 5000];
  var DEFAULT_RADIUS = 2000;
  var STORE_KEY = 'zad.areaCheck.v1';
  var SA_OFFSET_MS = 2 * 3600 * 1000;       // South Africa: UTC+2, no daylight saving
  var ZA_BOX = { s: -35.2, n: -22.0, w: 16.3, e: 33.0 };

  /* ── Address service settings (Sprint C) ─────────────────────────────────────
   * The one place that names the service and its key. To switch services, change
   * `service` (and add an adapter to ADAPTERS below if it is new), then republish.
   * service: 'off' hides the street-address box and nothing is ever sent.
   * The key is a browser key: Geoapify only accepts it from the allowed origins
   * set in its dashboard (forge-os-alpha.vercel.app and localhost). */
  var GEOCODER = {
    service: 'geoapify',
    key: '49cb5c94ff9a424faa28fc6e532216f9',
    endpoint: 'https://api.geoapify.com/v1/geocode/search',
    timeoutMs: 6000,         // then: "Search didn't respond"
    limit: 5,                // never more than five matches, never auto-picked
    minConfidence: 0.2       // weaker guesses are dropped, not shown
  };

  /* ── Distance and inside / may-be-inside ────────────────────────────────── */
  // Same great-circle formula and earth radius as Leaflet's CRS.Earth.distance.
  function distanceM(lat1, lng1, lat2, lng2) {
    var rad = Math.PI / 180, R = 6371000;
    var la1 = lat1 * rad, la2 = lat2 * rad;
    var sinDLat = Math.sin((lat2 - lat1) * rad / 2);
    var sinDLon = Math.sin((lng2 - lng1) * rad / 2);
    var a = sinDLat * sinDLat + Math.cos(la1) * Math.cos(la2) * sinDLon * sinDLon;
    var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
  }

  // 'in' when the pin's whole uncertainty fits inside the circle, 'maybe' when
  // only part of it overlaps, 'out' otherwise. A pin with no precision entry
  // is treated as a point.
  function classify(distance, uncertainty, radius) {
    var u = (typeof uncertainty === 'number' && uncertainty > 0) ? uncertainty : 0;
    if (distance + u <= radius) return 'in';
    if (distance - u <= radius) return 'maybe';
    return 'out';
  }

  /* ── Dates (South African calendar days) ──────────────────────────────────── */
  function saDay(ms) { return new Date(ms + SA_OFFSET_MS).toISOString().slice(0, 10); }

  function addDays(day, n) {
    var t = Date.parse(day + 'T00:00:00Z');
    return new Date(t + n * 86400000).toISOString().slice(0, 10);
  }

  function validDay(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    var t = Date.parse(s + 'T00:00:00Z');
    return !isNaN(t) && new Date(t).toISOString().slice(0, 10) === s;
  }

  // First and last SA day the incident could have started on, or null.
  // incident_end is exclusive for day and part-of-day windows and equal to
  // incident_at for a stated time.
  function pinDays(pin) {
    var s = pin && pin.incident_at ? Date.parse(pin.incident_at) : NaN;
    if (isNaN(s)) return null;
    var e = pin.incident_end ? Date.parse(pin.incident_end) : NaN;
    if (isNaN(e) || e < s) e = s;
    return { start: saDay(s), end: saDay(Math.max(s, e - 1)) };
  }

  // range: null (all dates) or {from, to} inclusive SA days.
  function rangeFor(mode, today, from, to) {
    if (mode === '7') return { from: addDays(today, -6), to: today };
    if (mode === '30') return { from: addDays(today, -29), to: today };
    if (mode === 'custom' && validDay(from) && validDay(to)) {
      return from <= to ? { from: from, to: to } : { from: to, to: from };
    }
    return null;
  }

  // true, false, or 'unknown' (no readable date: shown and counted apart).
  function dateMatch(pin, range) {
    if (!range) return true;
    var d = pinDays(pin);
    if (!d) return 'unknown';
    return d.start <= range.to && d.end >= range.from;
  }

  /* ── Place search over the built-in list ─────────────────────────────────── */
  function norm(s) {
    s = String(s || '');
    if (s.normalize) s = s.normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
    return s.toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function words(s) {
    s = String(s || '');
    if (s.normalize) s = s.normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
    return s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  }

  // places: the parsed places-za.json. Returns up to `limit` matches, best
  // first: exact name, then name start, then a word start, then anywhere.
  // Text after a comma ("Wesbank, Cape Town") only helps rank the matches.
  function searchPlaces(places, query, limit) {
    limit = limit || 5;
    var parts = String(query || '').split(',');
    var q = norm(parts[0]);
    var ctx = words(parts.slice(1).join(' '));
    if (q.length < 2 || !places || !places.rows) return [];
    var out = [];
    var rows = places.rows;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var names = [r[0]].concat(r[6] || []);
      var best = 0, hitName = null;
      for (var k = 0; k < names.length; k++) {
        var n = norm(names[k]), sc = 0;
        if (n === q) sc = 100;
        else if (n.indexOf(q) === 0) sc = 80;
        else if (words(names[k]).some(function (w) { return w.indexOf(q) === 0; })) sc = 60;
        else if (q.length >= 4 && n.indexOf(q) !== -1) sc = 40;
        if (k > 0) sc -= 1;                       // the main name wins a tie
        if (sc > best) { best = sc; hitName = names[k]; }
      }
      if (!best) continue;
      var muni = (places.municipalities || [])[r[5]] || '';
      var prov = (places.provinces || [])[r[4]] || '';
      if (ctx.length) {
        var hay = words(muni + ' ' + prov);
        if (ctx.every(function (w) { return hay.some(function (h) { return h.indexOf(w) === 0; }); })) best += 10;
      }
      out.push({ score: best, order: i, name: r[0], alias: hitName !== r[0] ? hitName : null,
                 lat: r[1], lng: r[2], kind: (places.kinds || [])[r[3]] || 'place',
                 province: prov, municipality: muni });
    }
    out.sort(function (a, b) { return (b.score - a.score) || (a.order - b.order); });
    return out.slice(0, limit);
  }

  /* ── Saved place ────────────────────────────────────────────────────────── */
  function inZA(lat, lng) {
    return typeof lat === 'number' && typeof lng === 'number' && isFinite(lat) && isFinite(lng) &&
           lat >= ZA_BOX.s && lat <= ZA_BOX.n && lng >= ZA_BOX.w && lng <= ZA_BOX.e;
  }

  // A saved place is kept only when every part checks out; anything else
  // (old shape, a point outside South Africa, an unknown radius) is dropped.
  function parseSaved(text) {
    var o;
    try { o = JSON.parse(text); } catch (e) { return null; }
    if (!o || typeof o !== 'object' || o.v !== 1) return null;
    if (!inZA(o.lat, o.lng)) return null;
    if (RADII.indexOf(o.radius) === -1) return null;
    if (typeof o.label !== 'string' || !o.label || o.label.length > 80) return null;
    var kind = typeof o.kind === 'string' ? o.kind.slice(0, 60) : '';
    var dates = (o.dates === '7' || o.dates === '30' || o.dates === 'custom') ? o.dates : 'all';
    var from = validDay(o.from) ? o.from : null, to = validDay(o.to) ? o.to : null;
    if (dates === 'custom' && !(from && to)) dates = 'all';
    return { v: 1, lat: o.lat, lng: o.lng, label: o.label, kind: kind, radius: o.radius,
             dates: dates, from: from, to: to, on: o.on !== false };
  }

  // About 11 m: enough for a radius of 500 m and up, no house-level detail.
  function roundPoint(x) { return Math.round(x * 1e4) / 1e4; }

  function fmtRadius(r) { return r >= 1000 ? (r / 1000) + ' km' : r + ' m'; }
  function fmtDistance(m) { return m < 950 ? (Math.round(m / 10) * 10) + ' m' : (Math.round(m / 100) / 10) + ' km'; }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDay(day) {
    var p = day.split('-');
    return parseInt(p[2], 10) + ' ' + MONTHS[parseInt(p[1], 10) - 1];
  }
  // "6 Oct", "6–7 Oct", "31 Oct–1 Nov"
  function fmtSpan(d) {
    if (d.start === d.end) return fmtDay(d.start);
    var a = fmtDay(d.start).split(' '), b = fmtDay(d.end).split(' ');
    return a[1] === b[1] ? a[0] + '–' + b[0] + ' ' + b[1] : a.join(' ') + '–' + b.join(' ');
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  // The sentence under the radius and dates.
  function summaryText(c, radius, range) {
    var when = range ? ' between ' + fmtDay(range.from) + ' and ' + fmtDay(range.to) : '';
    var s = plural(c.inside, 'reported killing', 'reported killings') + ' within ' + fmtRadius(radius) + when + '.';
    if (c.maybe) s += ' ' + plural(c.maybe, 'more may be', 'more may be') + ' inside: ' +
                      (c.maybe === 1 ? 'its' : 'their') + ' location is approximate.';
    if (c.unknown) s += ' ' + plural(c.unknown, 'report has', 'reports have') + ' no known date and ' +
                        (c.unknown === 1 ? 'is' : 'are') + ' shown anyway.';
    if (!c.inside && !c.maybe && !c.unknown) s += ' That does not mean the area is safe.';
    return s;
  }

  /* ── Street-address search (Sprint C) ─────────────────────────────────────── */
  var LABEL_MAX = 70;

  // Text from the service, made safe for textContent and short: no control
  // characters, single spaces, cut at a word with an ellipsis.
  function cleanLabel(s, max) {
    max = max || LABEL_MAX;
    s = String(s == null ? '' : s).replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069]/g, ' ')
                                 .replace(/\s+/g, ' ').trim();
    if (s.length <= max) return s;
    var cut = s.slice(0, max - 1);
    var sp = cut.lastIndexOf(' ');
    if (sp > max * 0.6) cut = cut.slice(0, sp);
    return cut.replace(/[\s,;·-]+$/, '') + '…';
  }

  // How each kind of match is named, and whether it is a whole area.
  var KINDS = {
    building: { word: 'address', area: false },
    amenity: { word: 'place', area: false },
    street: { word: 'street', area: false },
    suburb: { word: 'suburb', area: 'suburb' },
    district: { word: 'district', area: 'suburb' },
    postcode: { word: 'postcode area', area: 'postcode area' },
    city: { word: 'town or city', area: 'town' },
    county: { word: 'municipality', area: 'municipality' }
  };
  // Too big to be a centre: a province or the whole country.
  var TOO_BIG = { state: 1, country: 1 };

  // "gugulethu" and "guguletu", "ipelegeng" and "ipelegeng": a shared start
  // of six letters (or the whole shorter word) counts as the same place name.
  function similarWord(w, h) {
    var n = 0, m = Math.min(w.length, h.length);
    while (n < m && w.charAt(n) === h.charAt(n)) n++;
    return n >= Math.min(6, w.length) && (n === m || n >= 6);
  }

  function queryContext(query) {
    var parts = String(query || '').split(',');
    return parts.length > 1 ? words(parts.slice(1).join(' ')).filter(function (w) { return w.length >= 3; }) : [];
  }

  var ADAPTERS = {
    geoapify: {
      url: function (query, cfg) {
        return cfg.endpoint + '?text=' + encodeURIComponent(query) +
          '&filter=countrycode:za&lang=en&format=json&limit=' + (cfg.limit || 5) +
          '&apiKey=' + encodeURIComponent(cfg.key);
      },
      // Geoapify's "json" format gives lat and lon by name; its GeoJSON gives
      // [lon, lat]. Both are read, so the order cannot be swapped by accident.
      rows: function (data) {
        if (data && Array.isArray(data.results)) {
          return data.results.map(function (r) { return { r: r, lat: r.lat, lng: r.lon }; });
        }
        if (data && Array.isArray(data.features)) {
          return data.features.map(function (f) {
            var c = f && f.geometry && f.geometry.coordinates;
            return { r: (f && f.properties) || {}, lat: c && c[1], lng: c && c[0] };
          });
        }
        return null;
      }
    }
  };

  function addressUrl(query, cfg) {
    cfg = cfg || GEOCODER;
    var a = ADAPTERS[cfg.service];
    return a ? a.url(String(query || '').trim(), cfg) : null;
  }

  // The service's answer -> up to `limit` matches the page can show, or null
  // when the answer is not one it understands (shown as an error).
  function parseAddressResults(data, query, cfg) {
    cfg = cfg || GEOCODER;
    var a = ADAPTERS[cfg.service];
    var rows = a ? a.rows(data) : null;
    if (!rows) return null;
    var ctx = queryContext(query);
    var out = [], seen = {};
    rows.forEach(function (row) {
      var r = row.r || {};
      var lat = typeof row.lat === 'string' ? parseFloat(row.lat) : row.lat;
      var lng = typeof row.lng === 'string' ? parseFloat(row.lng) : row.lng;
      if (!inZA(lat, lng)) return;                              // outside South Africa
      if (r.country_code && String(r.country_code).toLowerCase() !== 'za') return;
      var type = String(r.result_type || 'unknown');
      if (TOO_BIG[type]) return;
      var rank = r.rank || {};
      if (typeof rank.confidence === 'number' && rank.confidence < (cfg.minConfidence || 0)) return;
      var k = KINDS[type] || { word: 'place', area: false };
      var name = cleanLabel(r.address_line1 || r.name || r.formatted, 60);
      if (!name) return;
      var area = cleanLabel(r.address_line2 || '', 90);
      if (area === name) area = '';
      // Geoapify fell back to the suburb or town: the street was not found.
      var fellBack = /^match_by_(city|postcode|country|state)/.test(String(rank.match_type || ''));
      var key = (name + '|' + area).toLowerCase();
      if (seen[key]) return;
      seen[key] = 1;
      var hay = words(name + ' ' + area + ' ' + (r.formatted || ''));
      var missing = ctx.filter(function (w) { return !hay.some(function (h) { return similarWord(w, h); }); });
      out.push({ name: name, area: area, type: type, kind: k.word, whole: k.area || false,
                 fellBack: fellBack, missing: missing.length ? missing : null,
                 lat: lat, lng: lng });
    });
    return out.slice(0, cfg.limit || 5);
  }

  // The centre's name and the line under it once a typed match is picked.
  function addressPlace(m) {
    var label = m.whole ? cleanLabel(m.name, 55) + ' (whole ' + m.whole + ')' : cleanLabel(m.name, 70);
    var kind;
    if (m.whole) kind = (m.fellBack ? 'Exact spot not found. ' : '') + 'Circle starts at the ' + m.whole + '’s centre.';
    else if (m.type === 'street') kind = 'A point on this street. Long streets run further.';
    else if (m.type === 'building') kind = 'Address point found by Geoapify.';
    else kind = 'Map point found by Geoapify.';
    return { lat: roundPoint(m.lat), lng: roundPoint(m.lng), label: label, kind: kind };
  }

  // One search at a time. Each search gets a number; an answer that arrives
  // after a newer search started (or after cancel) is thrown away. Calls
  // done(err, matches, seq): err is 'timeout', 'http', 'network' or 'bad'.
  function makeAddressSearch(opts) {
    opts = opts || {};
    var cfg = opts.cfg || GEOCODER;
    var fetchFn = opts.fetch || (typeof fetch === 'function' ? function (u, o) { return fetch(u, o); } : null);
    var Abort = opts.AbortController || (typeof AbortController === 'function' ? AbortController : null);
    var setT = opts.setTimeout || setTimeout, clearT = opts.clearTimeout || clearTimeout;
    var seq = 0, live = null;
    function cancel() {
      seq++;
      if (live) { clearT(live.timer); if (live.ctl) try { live.ctl.abort(); } catch (e) {} live = null; }
    }
    function search(query, done) {
      cancel();
      var my = seq;
      var url = addressUrl(query, cfg);
      if (!url || !fetchFn) { done('bad', null, my); return my; }
      var ctl = Abort ? new Abort() : null;
      var finished = false;
      function finish(err, list) {
        if (finished || my !== seq) return;     // late or cancelled: ignored
        finished = true;
        if (live) clearT(live.timer);
        live = null;
        done(err, list, my);
      }
      live = { ctl: ctl, timer: setT(function () {
        finish('timeout', null);
        if (ctl) try { ctl.abort(); } catch (e) {}
      }, cfg.timeoutMs || 6000) };
      fetchFn(url, { signal: ctl ? ctl.signal : undefined, credentials: 'omit', referrerPolicy: 'strict-origin',
                     headers: { Accept: 'application/json' } })
        .then(function (res) {
          if (!res || !res.ok) { finish('http', null); return null; }
          return res.json().then(function (data) {
            var list = parseAddressResults(data, query, cfg);
            finish(list ? null : 'bad', list);
          });
        })
        .catch(function () { finish('network', null); });
      return my;
    }
    return { search: search, cancel: cancel, busy: function () { return !!live; } };
  }

  /* ── The page ───────────────────────────────────────────────────────────── */
  // bridge: what map.html hands over (map, L, pins, setPinFilter, refreshPins,
  // showSignal, openPanel, closePanel, activePanel, drawerIdx, isPhone,
  // placesUrl, now). Pins: [{idx, lat, lng, unc, precision, incident_at,
  // incident_end, event, title, published_at}].
  function mount(bridge) {
    var doc = document, L = bridge.L, map = bridge.map;
    var $ = function (id) { return doc.getElementById(id); };
    var el = {
      panel: $('panel-area'), pill: $('ac-pill'), close: $('ac-close'),
      form: $('ac-search'), q: $('ac-q'), matchStatus: $('ac-match-status'), matches: $('ac-matches'),
      placeBtn: $('ac-place-btn'), set: $('ac-set'), centreName: $('ac-centre-name'), centreKind: $('ac-centre-kind'),
      radius: $('ac-radius'), dates: $('ac-dates'), datesAll: $('ac-dates-all'), custom: $('ac-custom'),
      from: $('ac-from'), to: $('ac-to'), summary: $('ac-summary'), results: $('ac-results'),
      toggle: $('ac-toggle'), fit: $('ac-fit'), remember: $('ac-remember'), forget: $('ac-forget'),
      storageNote: $('ac-storage-note'), upto: $('ac-checked-upto'),
      bar: $('ac-bar'), barText: $('ac-bar-text'), barEdit: $('ac-bar-edit'), barAll: $('ac-bar-all'),
      cross: $('ac-cross'), placebar: $('ac-placebar'), placeUse: $('ac-place-use'), placeCancel: $('ac-place-cancel'),
      home: $('ac-home'), outside: $('sd-outside'), outsideAll: $('sd-outside-all'),
      addr: $('ac-addr'), addrQ: $('ac-addr-q'), addrGo: $('ac-addr-go'), addrStatus: $('ac-addr-status'),
      addrMatches: $('ac-addr-matches'), addrCancel: $('ac-addr-cancel')
    };
    if (!el.panel || !map || !L) return null;

    var pins = bridge.pins || [];
    var byIdx = {};
    pins.forEach(function (p) { byIdx[p.idx] = p; });

    var state = { place: null, radius: DEFAULT_RADIUS, dates: 'all', from: null, to: null,
                  on: false, remember: false };
    var classes = {};          // idx -> 'in' | 'maybe' | 'out'
    var places = null, placesLoading = null;
    var layer = L.layerGroup();   // circle, centre dot, fuzzy edges
    var placing = false;

    function today() { return saDay(bridge.now ? bridge.now() : Date.now()); }

    // Day the newest report was added: "Reports checked up to".
    var newest = pins.reduce(function (m, p) {
      var t = p.published_at ? Date.parse(p.published_at) : NaN;
      return isNaN(t) ? m : Math.max(m, t);
    }, 0);
    if (newest && el.upto) {
      var nd = saDay(newest), np = nd.split('-');
      el.upto.textContent = ', up to ' + fmtDay(nd) + ' ' + np[0];
    }
    var earliest = pins.reduce(function (m, p) { var d = pinDays(p); return d && (!m || d.start < m) ? d.start : m; }, null);
    if (earliest && el.datesAll) el.datesAll.textContent = 'All (since ' + fmtDay(earliest) + ')';

    /* storage: localStorage when remembered, sessionStorage otherwise */
    function store(kind) {
      try { var s = window[kind]; var t = '__zad_t'; s.setItem(t, '1'); s.removeItem(t); return s; }
      catch (e) { return null; }
    }
    var local = store('localStorage'), session = store('sessionStorage');
    function save() {
      if (!state.place) return;
      var o = { v: 1, lat: roundPoint(state.place.lat), lng: roundPoint(state.place.lng),
                label: state.place.label, kind: state.place.kind, radius: state.radius,
                dates: state.dates, from: state.from, to: state.to, on: state.on };
      var txt = JSON.stringify(o);
      try {
        if (state.remember && local) { local.setItem(STORE_KEY, txt); if (session) session.removeItem(STORE_KEY); }
        else { if (local) local.removeItem(STORE_KEY); if (session) session.setItem(STORE_KEY, txt); }
      } catch (e) { /* storage full or blocked: the place lasts for this page only */ }
    }
    function forgetStored() {
      try { if (local) local.removeItem(STORE_KEY); } catch (e) {}
      try { if (session) session.removeItem(STORE_KEY); } catch (e) {}
    }
    function loadSaved() {
      var txt = null, from = null;
      try { if (local) { txt = local.getItem(STORE_KEY); from = 'local'; } } catch (e) {}
      if (!txt) { try { if (session) { txt = session.getItem(STORE_KEY); from = 'session'; } } catch (e) {} }
      if (!txt) return null;
      var o = parseSaved(txt);
      if (!o) { forgetStored(); return null; }
      o.remember = from === 'local';
      return o;
    }
    if (!local && el.storageNote) { el.storageNote.hidden = false; el.remember.disabled = true; }

    /* the count */
    function compute() {
      classes = {};
      var c = { inside: 0, maybe: 0, unknown: 0, list: [] };
      if (!state.place) return c;
      var range = rangeFor(state.dates, today(), state.from, state.to);
      pins.forEach(function (p) {
        var d = distanceM(state.place.lat, state.place.lng, p.lat, p.lng);
        var k = classify(d, p.unc, state.radius);
        if (k !== 'out') {
          var dm = dateMatch(p, range);
          if (dm === false) k = 'out';
          else if (dm === 'unknown') c.unknown++;
        }
        classes[p.idx] = k;
        if (k === 'in') c.inside++;
        if (k === 'maybe') c.maybe++;
        if (k !== 'out') c.list.push({ p: p, k: k, d: d });
      });
      c.list.sort(function (a, b) { return (a.k === b.k ? 0 : a.k === 'in' ? -1 : 1) || a.d - b.d; });
      c.range = range;
      return c;
    }

    function pinState(idx) { return state.on && state.place ? (classes[idx] || 'out') : 'in'; }

    function drawLayer(c) {
      layer.clearLayers();
      if (!state.place) return;
      var circle = L.circle([state.place.lat, state.place.lng], {
        radius: state.radius, interactive: false, color: '#e8b04a', weight: 2,
        opacity: state.on ? 0.9 : 0.4, fillColor: '#e8b04a', fillOpacity: state.on ? 0.06 : 0.02,
        dashArray: state.on ? null : '6 6'
      });
      layer.addLayer(circle);
      layer.addLayer(L.marker([state.place.lat, state.place.lng], {
        interactive: false, keyboard: false,
        icon: L.divIcon({ className: '', html: '<div class="ac-home-pin"></div>', iconSize: [22, 22], iconAnchor: [11, 11] })
      }));
      if (state.on) {
        c.list.forEach(function (it) {
          if (it.k !== 'maybe' || !it.p.unc) return;
          layer.addLayer(L.circle([it.p.lat, it.p.lng], {
            radius: it.p.unc, interactive: false, color: '#c8943a', weight: 1, opacity: 0.5,
            dashArray: '3 5', fillOpacity: 0.03
          }));
        });
      }
      if (!map.hasLayer(layer)) layer.addTo(map);
    }

    function precisionWord(p) {
      if (!p.precision || p.precision === 'spot') return '';
      return { street: 'street only', area: 'area only', suburb: 'suburb only', village: 'village only',
               town: 'town only', farm: 'farm area only',
               precinct: 'estimate, police area only' }[p.precision] || 'approximate';
    }

    function render() {
      var c = compute();
      bridge.setPinFilter(state.on && state.place ? pinState : null);
      drawLayer(c);
      el.set.hidden = !state.place;
      el.home.hidden = !state.place;
      if (state.place) {
        el.centreName.textContent = state.place.label;
        el.centreKind.textContent = state.place.kind;
        Array.prototype.forEach.call(el.radius.querySelectorAll('[data-radius]'), function (b) {
          b.setAttribute('aria-pressed', String(+b.getAttribute('data-radius') === state.radius));
        });
        Array.prototype.forEach.call(el.dates.querySelectorAll('[data-dates]'), function (b) {
          b.setAttribute('aria-pressed', String(b.getAttribute('data-dates') === state.dates));
        });
        el.custom.hidden = state.dates !== 'custom';
        el.remember.checked = !!state.remember;
        el.toggle.textContent = state.on ? 'Show all reports' : 'Turn the area filter back on';
        el.summary.textContent = state.on ? summaryText(c, state.radius, c.range)
          : 'Showing all reports. Your place is kept: use Home base or the button below to filter again.';
        el.results.innerHTML = '';
        if (state.on) c.list.forEach(function (it) {
          var li = doc.createElement('li');
          var b = doc.createElement('button');
          b.type = 'button';
          b.className = 'ac-res' + (it.k === 'maybe' ? ' ac-res--maybe' : '');
          var t = doc.createElement('span'); t.className = 'ac-res__title'; t.textContent = it.p.title;
          var days = pinDays(it.p);
          var when = days ? (it.p.event === 'found' ? 'Found ' : '') + fmtSpan(days) : 'Date not known';
          var bits = [when, fmtDistance(it.d) + ' from centre'];
          var pw = precisionWord(it.p);
          if (it.k === 'maybe') bits.push('may be inside, ' + (pw || 'approximate'));
          else if (pw) bits.push(pw);
          var m = doc.createElement('span'); m.className = 'ac-res__meta'; m.textContent = bits.join(' · ');
          b.appendChild(t); b.appendChild(m);
          b.addEventListener('click', function () { bridge.showSignal(it.p.idx); });
          li.appendChild(b); el.results.appendChild(li);
        });
      }
      // the one-line bar while the panel is closed
      var showBar = !!(state.place && state.on && bridge.activePanel() !== 'area' && !placing);
      el.bar.hidden = !showBar;
      if (showBar) {
        el.barText.textContent = fmtRadius(state.radius) + ' around ' + state.place.label + ': ' +
          plural(c.inside, 'report', 'reports') + (c.maybe ? ' (+' + c.maybe + ' maybe)' : '');
      }
      updateOutside();
      save();
    }

    function updateOutside() {
      if (!el.outside) return;
      var idx = bridge.drawerIdx();
      el.outside.hidden = !(state.on && state.place && idx !== null && (classes[idx] || 'out') === 'out');
    }

    function fitCircle() {
      if (!state.place) return;
      var b = L.latLng(state.place.lat, state.place.lng).toBounds(state.radius * 2.2);
      var pad;
      if (bridge.isPhone()) {
        // keep the circle above the bottom sheet when the panel is open
        var bottom = 130;
        if (bridge.activePanel() === 'area') {
          var mr = map.getContainer().getBoundingClientRect(), pr = el.panel.getBoundingClientRect();
          if (pr.height) bottom = Math.min(Math.round(mr.height * 0.55), Math.max(bottom, Math.round(mr.bottom - pr.top) + 16));
        }
        pad = { paddingTopLeft: [16, 70], paddingBottomRight: [16, bottom] };
      } else {
        pad = { paddingTopLeft: [bridge.activePanel() === 'area' ? 560 : 170, 40], paddingBottomRight: [60, 40] };
      }
      map.flyToBounds(b, Object.assign({ duration: 0.6, maxZoom: 16 }, pad));
    }

    function setPlace(p) {
      state.place = p;
      state.on = true;
      render();
      fitCircle();
    }

    /* place list: fetched once, the first time the panel opens */
    function loadPlaces() {
      if (places) return Promise.resolve(places);
      if (placesLoading) return placesLoading;
      el.matchStatus.textContent = 'Loading the place list…';
      placesLoading = fetch(bridge.placesUrl, { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (d) { places = d; el.matchStatus.textContent = ''; return d; })
        .catch(function () {
          placesLoading = null;
          el.matchStatus.textContent = 'The place list did not load. Check your connection and try again, or place the centre on the map.';
          return null;
        });
      return placesLoading;
    }

    function showMatches(list, query) {
      el.matches.innerHTML = '';
      if (!list.length) {
        el.matchStatus.textContent = 'No place called “' + query + '” in the list. Try another spelling, or place the centre on the map.';
        return;
      }
      el.matchStatus.textContent = list.length === 1 ? '1 match. Pick it to set the centre.'
                                                    : list.length + ' matches. Pick the right one.';
      list.forEach(function (m) {
        var li = doc.createElement('li');
        var b = doc.createElement('button');
        b.type = 'button'; b.className = 'ac-match';
        var n = doc.createElement('span'); n.className = 'ac-match__name';
        n.textContent = m.name + (m.alias ? ' (' + m.alias + ')' : '');
        var meta = doc.createElement('span'); meta.className = 'ac-match__meta';
        meta.textContent = [m.kind, m.municipality, m.province].filter(Boolean).join(' · ');
        b.appendChild(n); b.appendChild(meta);
        b.addEventListener('click', function () {
          el.matches.innerHTML = '';
          el.matchStatus.textContent = '';
          cancelAddr(true);
          setPlace({ lat: m.lat, lng: m.lng, label: m.name,
                     kind: 'The ' + m.kind + '’s map point' + (m.municipality ? ', ' + m.municipality : '') + '. Not a street address.' });
          el.summary.focus();
        });
        li.appendChild(b); el.matches.appendChild(li);
      });
    }

    el.form.addEventListener('submit', function (e) {
      e.preventDefault();
      var query = el.q.value.trim();
      if (norm(query.split(',')[0]).length < 2) {
        el.matches.innerHTML = '';
        el.matchStatus.textContent = 'Type at least two letters of a suburb or town.';
        return;
      }
      loadPlaces().then(function (d) { if (d) showMatches(searchPlaces(d, query, 5), query); });
    });

    /* typed street address (Sprint C): sent only when Search is pressed */
    var cfg = bridge.geocoder || GEOCODER;
    var addrOn = !!(el.addr && el.addrQ && ADAPTERS[cfg.service] && cfg.key);
    var addrSearch = addrOn ? makeAddressSearch({ cfg: cfg, fetch: bridge.fetch }) : null;
    var ADDR_FAIL = 'Search didn\u2019t respond. Try again or place it on the map.';
    function addrBusy(on) {
      if (el.addrCancel) el.addrCancel.hidden = !on;
      if (el.addrGo) { el.addrGo.disabled = on; el.addrGo.textContent = on ? 'Searching\u2026' : 'Search'; }
    }
    function cancelAddr(clearStatus) {
      if (!addrSearch || !addrSearch.busy()) return;
      addrSearch.cancel(); addrBusy(false);
      if (clearStatus) el.addrStatus.textContent = '';
    }
    function showAddrMatches(list, query) {
      el.addrMatches.innerHTML = '';
      if (!list.length) {
        el.addrStatus.textContent = 'No address found for \u201c' + cleanLabel(query, 60) +
          '\u201d. Check the spelling, add the suburb or town, or place it on the map.';
        return;
      }
      el.addrStatus.textContent = (list.length === 1 ? '1 match' : list.length + ' matches') +
        '. Pick the right one, or place it on the map if none fit.';
      list.forEach(function (m) {
        var li = doc.createElement('li');
        var b = doc.createElement('button');
        b.type = 'button'; b.className = 'ac-match ac-match--addr';
        var n = doc.createElement('span'); n.className = 'ac-match__name'; n.textContent = m.name;
        var meta = doc.createElement('span'); meta.className = 'ac-match__meta';
        meta.textContent = [m.whole ? 'whole ' + m.whole : m.kind, m.area].filter(Boolean).join(' \u00b7 ');
        b.appendChild(n); b.appendChild(meta);
        var flags = [];
        if (m.fellBack) flags.push('Exact spot not found: this is the whole ' + (m.whole || 'area') + '.');
        if (m.missing) flags.push('Does not mention \u201c' + cleanLabel(m.missing.join(' '), 40) + '\u201d.');
        if (flags.length) {
          var f = doc.createElement('span'); f.className = 'ac-match__flag'; f.textContent = flags.join(' ');
          b.appendChild(f);
        }
        b.addEventListener('click', function () {
          el.addrMatches.innerHTML = '';
          el.addrStatus.textContent = '';
          el.matches.innerHTML = ''; el.matchStatus.textContent = '';
          setPlace(addressPlace(m));
          el.summary.focus();
        });
        li.appendChild(b); el.addrMatches.appendChild(li);
      });
    }
    if (addrOn) {
      el.addr.hidden = false;
      el.addr.addEventListener('submit', function (e) {
        e.preventDefault();
        var query = cleanLabel(el.addrQ.value, 120);
        el.addrMatches.innerHTML = '';
        if (norm(query).length < 3) {
          cancelAddr(false);
          el.addrStatus.textContent = 'Type a street and its suburb or town.';
          return;
        }
        el.addrStatus.textContent = 'Searching\u2026';
        addrBusy(true);
        addrSearch.search(query, function (err, list) {
          addrBusy(false);
          if (err) { el.addrStatus.textContent = ADDR_FAIL; return; }
          showAddrMatches(list, query);
        });
      });
      el.addrCancel.addEventListener('click', function () {
        cancelAddr(false);
        el.addrStatus.textContent = 'Search cancelled. Try again or place it on the map.';
        el.addrQ.focus();
      });
    }

    /* placing a point on the map */
    function startPlacing() {
      cancelAddr(true);
      placing = true;
      if (bridge.activePanel() === 'area') bridge.closePanel();
      el.cross.hidden = false; el.placebar.hidden = false; el.bar.hidden = true;
      if (map.getZoom() < 13) map.setZoom(13);
      el.placeUse.focus();
    }
    function stopPlacing(reopen) {
      placing = false;
      el.cross.hidden = true; el.placebar.hidden = true;
      if (reopen) bridge.openPanel('area');
      render();
    }
    el.placeBtn.addEventListener('click', startPlacing);
    el.placeCancel.addEventListener('click', function () { stopPlacing(true); });
    el.placeUse.addEventListener('click', function () {
      var size = map.getSize();
      var c = map.containerPointToLatLng([size.x / 2, size.y / 2]);
      if (!inZA(c.lat, c.lng)) {
        el.placebar.querySelector('.ac-placebar__text').textContent = 'That spot is outside South Africa. Move the map and try again.';
        return;
      }
      placing = false;
      el.cross.hidden = true; el.placebar.hidden = true;
      bridge.openPanel('area');
      setPlace({ lat: roundPoint(c.lat), lng: roundPoint(c.lng), label: 'Your map point',
                 kind: 'A spot you placed on the map (' + roundPoint(c.lat).toFixed(4) + ', ' + roundPoint(c.lng).toFixed(4) + ').' });
    });

    /* radius, dates, toggles */
    el.radius.addEventListener('click', function (e) {
      var b = e.target.closest('[data-radius]'); if (!b) return;
      state.radius = +b.getAttribute('data-radius'); state.on = true; render(); fitCircle();
    });
    el.dates.addEventListener('click', function (e) {
      var b = e.target.closest('[data-dates]'); if (!b) return;
      state.dates = b.getAttribute('data-dates'); state.on = true;
      if (state.dates === 'custom') {
        var t = today();
        if (!state.from) state.from = addDays(t, -13);
        if (!state.to) state.to = t;
        el.from.value = state.from; el.to.value = state.to;
      }
      render();
    });
    function onCustom() {
      if (validDay(el.from.value)) state.from = el.from.value;
      if (validDay(el.to.value)) state.to = el.to.value;
      render();
    }
    el.from.addEventListener('change', onCustom);
    el.to.addEventListener('change', onCustom);
    function showAll() { state.on = false; render(); }
    el.toggle.addEventListener('click', function () {
      if (state.on) showAll(); else { state.on = true; render(); fitCircle(); }
    });
    el.fit.addEventListener('click', fitCircle);
    el.remember.addEventListener('change', function () { state.remember = el.remember.checked; render(); });
    el.forget.addEventListener('click', function () {
      forgetStored();
      state.place = null; state.on = false; state.remember = false;
      layer.clearLayers();
      render();
      forgetStored();
      el.q.focus();
    });
    el.barAll.addEventListener('click', showAll);
    el.barEdit.addEventListener('click', function () { bridge.openPanel('area'); });
    if (el.outsideAll) el.outsideAll.addEventListener('click', showAll);
    el.home.addEventListener('click', function () {
      if (!state.place) return;
      state.on = true; render(); fitCircle();
    });
    el.close.addEventListener('click', function () { bridge.closePanel(); el.pill.focus(); });

    // Escape: the outside-link box and the drawer go first (they mark the
    // key handled); then placing, then this panel.
    doc.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (placing) { e.preventDefault(); stopPlacing(true); return; }
      if (bridge.activePanel() === 'area' && bridge.drawerIdx() === null) {
        e.preventDefault(); bridge.closePanel(); el.pill.focus();
      }
    });

    var saved = loadSaved();
    if (saved) {
      state.place = { lat: saved.lat, lng: saved.lng, label: saved.label, kind: saved.kind };
      state.radius = saved.radius; state.dates = saved.dates; state.from = saved.from; state.to = saved.to;
      state.on = saved.on; state.remember = saved.remember;
      if (saved.from) el.from.value = saved.from;
      if (saved.to) el.to.value = saved.to;
    }
    render();

    return {
      onPanel: function (name) {
        if (name !== 'area') cancelAddr(true);
        if (name === 'area') {
          loadPlaces();
          if (!state.place && !bridge.isPhone()) setTimeout(function () { el.q.focus(); }, 50);
        }
        render();
      },
      onDrawer: updateOutside,
      showAll: showAll,
      state: function () { return JSON.parse(JSON.stringify(state)); },
      classOf: function (idx) { return classes[idx] || null; }
    };
  }

  return {
    RADII: RADII, DEFAULT_RADIUS: DEFAULT_RADIUS, STORE_KEY: STORE_KEY,
    distanceM: distanceM, classify: classify, saDay: saDay, addDays: addDays, validDay: validDay,
    pinDays: pinDays, rangeFor: rangeFor, dateMatch: dateMatch, norm: norm,
    searchPlaces: searchPlaces, parseSaved: parseSaved, inZA: inZA, roundPoint: roundPoint,
    summaryText: summaryText, fmtRadius: fmtRadius, fmtSpan: fmtSpan, mount: mount,
    GEOCODER: GEOCODER, cleanLabel: cleanLabel, addressUrl: addressUrl,
    parseAddressResults: parseAddressResults, addressPlace: addressPlace, makeAddressSearch: makeAddressSearch
  };
});
