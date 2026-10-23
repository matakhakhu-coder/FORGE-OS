/* FORGE Graph panel: the timeline slider and moving charts.
 *
 * Plain JavaScript, no library, served from the site itself. The page prints
 * today's charts in plain HTML (the no-script fallback) and a compact table
 * from tools/record_charts.timeline_table() in #gp-chart-data: one step per
 * publish and one row per record. Python owns the counting rules; this file
 * only filters the rows to the slider's step and draws them.
 *
 * - One slider per chart page, one shared position (the single time value).
 * - Axes stay fixed at today's size: bars grow and shrink, nothing rescales.
 * - Motion: fast-then-gentle easing, no bounce, about a third of a second.
 *   A new step picks up from wherever the last movement had got to.
 * - Numbers never animate: counts and summaries show the step's whole number.
 * - prefers-reduced-motion: values jump with no movement.
 * - Draws at most once per screen refresh, and only while something moves.
 *
 * Tap to see the records (Batch 4): a bar, a day column, a day on the line
 * (the whole day column is the target) or the summary's "date not stated" /
 * "later step" counts open a short list under the chart of the records
 * counted there at the slider's position. Titles come from the map's own pin
 * data (window.forgeSignalInfo, matched by short ID); "Show on map" opens the
 * map's event drawer (window.forgeOpenSignal). The table never holds titles.
 *
 * Stream switches (Batch 5): one stream state shared with the map's Streams
 * panel (window.ZADStreams; a 'forge-streams' event when it changes), so the
 * map and the charts never disagree. A row of switches sits above the line,
 * Added and Provinces charts; on Streams, tapping a bar turns it on or off.
 */
(function () {
  'use strict';

  var dataEl = document.getElementById('gp-chart-data');
  var panel = document.getElementById('panel-graph');
  if (!dataEl || !panel) return;
  var T;
  try { T = JSON.parse(dataEl.textContent); } catch (e) { return; }
  if (!T || !T.steps || !T.steps.length || !T.rows) return;

  var DURATION = 320;                      // ms, about a third of a second
  var COL = {};
  T.columns.forEach(function (c, i) { COL[c] = i; });
  var LAST = T.steps.length - 1;
  var AX = T.axes;

  function ease(t) { return 1 - Math.pow(1 - t, 3); }   // fast, then gentle; never past 1
  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function dayLabel(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    return m ? (+m[3]) + ' ' + MONTHS[+m[2] - 1] : String(iso);
  }

  /* ── Streams: shared with the map (Batch 5) ── */
  var STREAMS = T.streams || [];
  var KEY_OF = {};                         // stream label -> map stream key
  STREAMS.forEach(function (s) { KEY_OF[s.label] = s.key; });
  var VIS = {};
  function readVisible() {
    var v = (window.ZADStreams && window.ZADStreams.visible) ? window.ZADStreams.visible() : null;
    STREAMS.forEach(function (s) { VIS[s.key] = v ? v[s.key] !== false : (VIS[s.key] !== false); });
  }
  readVisible();
  function rowOn(r) { var k = KEY_OF[r[COL.stream]]; return !k || VIS[k] !== false; }
  function streamsOn() { return STREAMS.filter(function (s) { return VIS[s.key] !== false; }); }

  /* ── Counting: filter the rows to one step (mirrors counts_from_table) ── */
  function counts(step) {
    var inc = {}, added = {}, stream = {}, province = {};
    AX.incident_days.forEach(function (d) { inc[d] = 0; });
    AX.added_days.forEach(function (d) { added[d] = 0; });
    var out = { total: 0, occurred: 0, found: 0, undated: 0, later_step: 0, added_undated: 0 };
    for (var i = 0; i < T.rows.length; i++) {
      var r = T.rows[i];
      if (r[COL.step] > step || !rowOn(r)) continue;
      out.total++;
      var k = r[COL.kind];
      if (k === 'later_step') out.later_step++;
      else if (k === 'undated') out.undated++;
      else { out[k]++; inc[r[COL.inc]] = (inc[r[COL.inc]] || 0) + 1; }
      if (r[COL.added] === null) out.added_undated++;
      else added[r[COL.added]] = (added[r[COL.added]] || 0) + 1;
      stream[r[COL.stream]] = (stream[r[COL.stream]] || 0) + 1;
      province[r[COL.province]] = (province[r[COL.province]] || 0) + 1;
    }
    out.incident = AX.incident_days.map(function (d) { return inc[d]; });
    out.added = AX.added_days.map(function (d) { return added[d]; });
    out.stream = AX.stream.map(function (l) { return stream[l] || 0; });
    out.province = AX.province.map(function (l) { return province[l] || 0; });
    return out;
  }

  /* ── Tweens: one shared clock, each value eases from where it is now ── */
  var tweens = [];
  var afterFrame = [];
  var t0 = 0, raf = 0;
  function tween(initial, apply) {
    var o = { cur: initial, from: initial, to: initial, apply: apply };
    tweens.push(o);
    return o;
  }
  function runFrameHooks() { for (var i = 0; i < afterFrame.length; i++) afterFrame[i](); }
  function startMotion() {
    var i;
    if (reducedMotion()) {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      for (i = 0; i < tweens.length; i++) { tweens[i].cur = tweens[i].from = tweens[i].to; tweens[i].apply(tweens[i].cur); }
      runFrameHooks();
      return;
    }
    for (i = 0; i < tweens.length; i++) tweens[i].from = tweens[i].cur;   // pick up mid-motion
    t0 = performance.now();
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function frame(now) {
    var t = Math.min(1, Math.max(0, (now - t0) / DURATION)), e = ease(t);
    for (var i = 0; i < tweens.length; i++) {
      var o = tweens[i];
      o.cur = t >= 1 ? o.to : o.from + (o.to - o.from) * e;
      o.apply(o.cur);
    }
    runFrameHooks();
    raf = t < 1 ? requestAnimationFrame(frame) : 0;
  }

  var NOW = counts(LAST);
  var renderers = [];       // each: function (c) setting targets and whole numbers
  var announcers = [];      // run when the slider stops (screen-reader labels)

  /* ── Bars (Streams, Provinces): grow along their length ── */
  function setupBars(pid, values, max) {
    var page = document.getElementById('gp-page-' + pid);
    if (!page) return;
    var items = page.querySelectorAll('.gp-bar');
    if (items.length !== values.length) return;
    var tw = [], nums = [], rows = [];
    var isSwitch = pid === 'stream' && STREAMS.length;
    var ol = page.querySelector('.gp-bars');
    if (ol) ol.classList.add('gp-bars--live');
    Array.prototype.forEach.call(items, function (li, i) {
      var fill = li.querySelector('.gp-bar__fill');
      nums.push(li.querySelector('.gp-bar__n'));
      tw.push(tween(values[i], function (v) { fill.style.width = (max ? v / max * 100 : 0).toFixed(2) + '%'; }));
      rows.push(enhanceBar(li, pid, AX[pid][i], isSwitch));
    });
    renderers.push(function (c) {
      var vals = c[pid];
      for (var i = 0; i < tw.length; i++) {
        tw[i].to = vals[i]; nums[i].textContent = vals[i];
        var r = rows[i];
        if (r.sw) {
          var on = VIS[r.key] !== false;
          r.li.classList.toggle('gp-bar--off', !on);
          r.state.textContent = on ? '' : 'off';
          r.sw.setAttribute('aria-checked', on ? 'true' : 'false');
          r.sw.setAttribute('aria-label', r.label + ': ' + (on ? plural(vals[i], 'record') + ', on' : 'off') + ' (charts and map)');
          r.count.setAttribute('aria-label', 'List the ' + plural(vals[i], 'record') + ' in ' + r.label);
        } else {
          r.btn.setAttribute('aria-label', r.label + ': ' + plural(vals[i], 'record') + '. List them');
        }
      }
    });
  }

  // Bars become buttons (Batch 4): the label, bar and count move, unchanged,
  // into a button that opens the list. On Streams (Batch 5) the label and bar
  // are a switch for that stream and the count is the list button.
  function enhanceBar(li, pid, label, isSwitch) {
    var lab = li.querySelector('.gp-bar__lab'), track = li.querySelector('.gp-bar__track'),
        n = li.querySelector('.gp-bar__n');
    var out = { li: li, label: label };
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'gp-hit gp-bar__btn';
    btn.appendChild(lab); btn.appendChild(track);
    li.innerHTML = '';
    if (isSwitch) {
      out.key = KEY_OF[label];
      li.className = 'gp-bar gp-bar--switch';
      btn.setAttribute('role', 'switch');
      out.state = document.createElement('span');
      out.state.className = 'gp-bar__state';
      lab.appendChild(out.state);
      var count = document.createElement('button');
      count.type = 'button';
      count.className = 'gp-hit gp-bar__count';
      count.appendChild(n);
      li.appendChild(btn); li.appendChild(count);
      out.sw = btn; out.count = count;
      if (out.key) btn.addEventListener('click', function () { setStream(out.key, VIS[out.key] === false); });
      else btn.disabled = true;
      count.addEventListener('click', function () { openList(pid, label, count); });
    } else {
      li.className = 'gp-bar gp-bar--live';
      btn.appendChild(n);
      li.appendChild(btn);
      out.btn = btn;
      btn.addEventListener('click', function () { openList(pid, label, btn); });
    }
    pickTargets.push({ pid: pid, key: label, el: out.count || btn });
    return out;
  }

  /* ── Columns (Added by day): grow up from the base ── */
  function setupColumns() {
    var page = document.getElementById('gp-page-added');
    if (!page) return;
    var items = page.querySelectorAll('.gp-col');
    if (items.length !== NOW.added.length) return;
    var max = AX.added_max, tw = [], nums = [], btns = [];
    Array.prototype.forEach.call(items, function (li, i) {
      var bar = li.querySelector('.gp-col__bar');
      nums.push(li.querySelector('.gp-col__n'));
      tw.push(tween(NOW.added[i], function (v) { bar.style.height = (max ? v / max * 100 : 0).toFixed(2) + '%'; }));
      // the column becomes a button (Batch 4); its parts move in unchanged
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'gp-hit gp-col__btn';
      while (li.firstChild) btn.appendChild(li.firstChild);
      li.appendChild(btn);
      btns.push(btn);
      var day = AX.added_days[i];
      btn.addEventListener('click', function () { openList('added', day, btn); });
      pickTargets.push({ pid: 'added', key: day, el: btn });
    });
    // "date added not recorded" is its own list; printed only when there are any
    var undatedEl = page.querySelector('.gp-chart__summary');
    if (!undatedEl) {
      undatedEl = document.createElement('p');
      undatedEl.className = 'gp-chart__summary';
      undatedEl.hidden = true;
      var scroll = page.querySelector('.gp-cols-scroll');
      if (scroll) scroll.parentNode.insertBefore(undatedEl, scroll); else undatedEl = null;
    }
    var undatedBtn = null;
    if (undatedEl) {
      undatedEl.textContent = '';
      undatedBtn = document.createElement('button');
      undatedBtn.type = 'button';
      undatedBtn.className = 'gp-hit';
      undatedEl.appendChild(undatedBtn);
      undatedBtn.addEventListener('click', function () { openList('added', null, undatedBtn); });
      pickTargets.push({ pid: 'added', key: null, el: undatedBtn });
    }
    renderers.push(function (c) {
      for (var i = 0; i < tw.length; i++) {
        tw[i].to = c.added[i]; nums[i].textContent = c.added[i];
        btns[i].setAttribute('aria-label', 'Added ' + dayLabel(AX.added_days[i]) + ': ' + plural(c.added[i], 'record') + '. List them');
      }
      if (undatedEl) {
        undatedEl.hidden = !c.added_undated;
        undatedBtn.textContent = plural(c.added_undated, 'record') + ': date added not recorded';
      }
    });
  }

  /* ── The incident line: reshapes like a wave (Batch 3) ──
     Each day point eases up or down on today's fixed scale; the curve is
     redrawn through the points on every frame. Monotone cubic (the same rule
     as D3's curveMonotoneX): between two days it never goes below the lower
     or above the higher of the two, so it can't dip under zero or invent a
     peak. The page prints straight segments for the no-script fallback. */
  function monotonePath(xs, ys) {
    var n = xs.length, i;
    if (!n) return '';
    if (n === 1) return 'M' + xs[0] + ',' + ys[0];
    var h = [], s = [], t = new Array(n);
    for (i = 0; i < n - 1; i++) { h[i] = xs[i + 1] - xs[i]; s[i] = h[i] ? (ys[i + 1] - ys[i]) / h[i] : 0; }
    function sign(v) { return v < 0 ? -1 : 1; }
    for (i = 1; i < n - 1; i++) {
      var p = (s[i - 1] * h[i] + s[i] * h[i - 1]) / (h[i - 1] + h[i]);
      t[i] = (sign(s[i - 1]) + sign(s[i])) * Math.min(Math.abs(s[i - 1]), Math.abs(s[i]), 0.5 * Math.abs(p)) || 0;
    }
    if (n === 2) { t[0] = t[1] = s[0]; }
    else {
      t[0] = (3 * s[0] - t[1]) / 2;
      t[n - 1] = (3 * s[n - 2] - t[n - 2]) / 2;
      // keep the end tangents inside the monotone limit
      if (sign(t[0]) !== sign(s[0]) || s[0] === 0) t[0] = 0; else if (Math.abs(t[0]) > 3 * Math.abs(s[0])) t[0] = 3 * s[0];
      if (sign(t[n - 1]) !== sign(s[n - 2]) || s[n - 2] === 0) t[n - 1] = 0; else if (Math.abs(t[n - 1]) > 3 * Math.abs(s[n - 2])) t[n - 1] = 3 * s[n - 2];
    }
    var d = 'M' + xs[0].toFixed(2) + ',' + ys[0].toFixed(2);
    for (i = 0; i < n - 1; i++) {
      var dx = h[i] / 3;
      d += 'C' + (xs[i] + dx).toFixed(2) + ',' + (ys[i] + dx * t[i]).toFixed(2) + ' ' +
           (xs[i + 1] - dx).toFixed(2) + ',' + (ys[i + 1] - dx * t[i + 1]).toFixed(2) + ' ' +
           xs[i + 1].toFixed(2) + ',' + ys[i + 1].toFixed(2);
    }
    return d;
  }

  var LINE = null;
  function setupLine() {
    var page = document.getElementById('gp-page-incident');
    if (!page) return;
    var svg = page.querySelector('.gp-line svg');
    var summary = page.querySelector('.gp-chart__summary');
    var title = (page.querySelector('.gp-chart__title') || {}).textContent || 'Records by incident date';
    if (!svg) return;
    var circles = svg.querySelectorAll('.gp-line__pt');
    if (circles.length !== NOW.incident.length) return;
    var top = T.line.top, bottom = T.line.bottom, max = Math.max(AX.incident_max, 1);
    function yOf(v) { return top + (bottom - top) * (1 - v / max); }
    var xs = [], labels = [], ys = [], tw = [], titles = [];
    Array.prototype.forEach.call(circles, function (c, i) {
      xs.push(parseFloat(c.getAttribute('cx')));
      var tt = c.querySelector('title');
      titles.push(tt);
      labels.push(tt ? tt.textContent.split(':')[0] : AX.incident_days[i]);
      ys.push(yOf(NOW.incident[i]));
      tw.push(tween(NOW.incident[i], function (v) { ys[i] = yOf(v); c.setAttribute('cy', ys[i].toFixed(2)); }));
    });
    var poly = svg.querySelector('polyline.gp-line__path');
    var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('class', 'gp-line__path');
    path.setAttribute('d', monotonePath(xs, ys));
    if (poly) { poly.parentNode.insertBefore(path, poly); poly.parentNode.removeChild(poly); }
    else svg.insertBefore(path, circles[0]);
    afterFrame.push(function () { path.setAttribute('d', monotonePath(xs, ys)); });

    /* Tap a day (Batch 4): the whole day column is the target, from halfway
       to the day before to halfway to the day after, the chart's full height.
       A highlight band marks the day whose records are listed. */
    var NS = 'http://www.w3.org/2000/svg';
    var vb = (svg.getAttribute('viewBox') || '0 0 320 200').split(/\s+/).map(Number);
    var band = document.createElementNS(NS, 'rect');
    band.setAttribute('class', 'gp-line__sel');
    band.setAttribute('y', top); band.setAttribute('height', Math.max(0, bottom - top));
    band.style.display = 'none';
    svg.insertBefore(band, svg.firstChild);
    var hits = document.createElementNS(NS, 'g');
    hits.setAttribute('class', 'gp-line__hits');
    var edges = xs.map(function (x, i) {
      var a = i ? (xs[i - 1] + x) / 2 : Math.max(vb[0], x - (xs[1] - x || 10) / 2);
      var b = i < xs.length - 1 ? (x + xs[i + 1]) / 2 : Math.min(vb[0] + vb[2], x + (x - xs[i - 1] || 10) / 2);
      return [a, b];
    });
    edges.forEach(function (e, i) {
      var r = document.createElementNS(NS, 'rect');
      r.setAttribute('class', 'gp-line__hit');
      r.setAttribute('x', e[0].toFixed(2)); r.setAttribute('width', (e[1] - e[0]).toFixed(2));
      r.setAttribute('y', vb[1]); r.setAttribute('height', vb[3]);
      r.setAttribute('data-day', AX.incident_days[i]);
      r.addEventListener('click', function () { openList('incident', AX.incident_days[i], fig); });
      hits.appendChild(r);
    });
    svg.appendChild(hits);
    var fig = svg.parentNode;
    fig.tabIndex = 0;
    fig.setAttribute('aria-label', 'Records by day. Enter lists a day\u2019s records; the left and right arrow keys change the day.');
    fig.addEventListener('keydown', function (e) {
      var days = AX.incident_days, st = lists.incident, at = (st && days.indexOf(st.key));
      if (at === undefined || at === null || at < 0) at = -1;
      var to = null;
      if (e.key === 'ArrowRight') to = at < 0 ? 0 : Math.min(days.length - 1, at + 1);
      else if (e.key === 'ArrowLeft') to = at < 0 ? days.length - 1 : Math.max(0, at - 1);
      else if (e.key === 'Home') to = 0;
      else if (e.key === 'End') to = days.length - 1;
      else if (e.key === 'Enter' || e.key === ' ') {
        if (at < 0) { var c = counts(current), best = 0; c.incident.forEach(function (v, i) { if (v > c.incident[best]) best = i; }); to = best; }
        else to = at;
      } else if (e.key === 'Escape' && st) { closeList('incident'); e.preventDefault(); return; }
      if (to === null) return;
      e.preventDefault(); e.stopPropagation();
      openList('incident', days[to], fig);
    });
    LINE_PICK = function (key) {
      var i = AX.incident_days.indexOf(key);
      if (i < 0) { band.style.display = 'none'; return; }
      band.setAttribute('x', edges[i][0].toFixed(2)); band.setAttribute('width', (edges[i][1] - edges[i][0]).toFixed(2));
      band.style.display = '';
    };

    // The summary's "date not stated" and "later step" counts open their own
    // lists. Its text reads exactly as the printed summary does.
    var sumBtns = {};
    function sumButton(key) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'gp-hit';
      b.addEventListener('click', function () { openList('incident', key, b); });
      pickTargets.push({ pid: 'incident', key: key, el: b });
      return b;
    }
    if (summary) { sumBtns.undated = sumButton('undated'); sumBtns.later_step = sumButton('later_step'); }

    renderers.push(function (c) {
      for (var i = 0; i < tw.length; i++) {
        tw[i].to = c.incident[i];
        if (titles[i]) titles[i].textContent = labels[i] + ': ' + plural(c.incident[i], 'record');
      }
      if (summary) {
        summary.textContent = c.occurred + ' occurred \u00b7 ' + c.found + ' found (date a body was found)';
        if (c.undated) {
          summary.appendChild(document.createTextNode(' \u00b7 '));
          sumBtns.undated.textContent = c.undated + ' date not stated (not on the line)';
          summary.appendChild(sumBtns.undated);
        }
        if (c.later_step) {
          summary.appendChild(document.createTextNode(' \u00b7 '));
          sumBtns.later_step.textContent = c.later_step + ' later step in an older case (not on the line)';
          summary.appendChild(sumBtns.later_step);
        }
      }
    });
    // screen readers: the chart's label follows the slider once it stops
    announcers.push(function (c) {
      svg.setAttribute('aria-label', title + ': ' + labels.map(function (l, i) { return l + ' ' + c.incident[i]; }).join(', '));
    });
    LINE = {
      path: path, xs: xs,
      ys: function () { return ys.slice(); },
      monotonePath: monotonePath
    };
  }

  /* ── Tap to see the records (Batch 4) ──
     One list per chart page, under the chart (after the slider). It shows the
     records counted at the slider's position in the streams switched on, and
     follows the slider and the switches while it is open. Mirrors
     tools/record_charts.ids_from_table. */
  var lists = {};           // page id -> { key, trigger }
  var pickTargets = [];     // { pid, key, el }: highlighted while their list is open
  var LINE_PICK = null;

  function rowMatches(r, pid, key) {
    if (pid === 'incident') {
      return (key === 'undated' || key === 'later_step') ? r[COL.kind] === key
        : ((r[COL.kind] === 'occurred' || r[COL.kind] === 'found') && r[COL.inc] === key);
    }
    if (pid === 'added') return r[COL.added] === key;
    return r[COL[pid]] === key;
  }
  function rowsFor(pid, key, step) {
    var out = [];
    for (var i = 0; i < T.rows.length; i++) {
      var r = T.rows[i];
      if (r[COL.step] <= step && rowOn(r) && rowMatches(r, pid, key)) out.push(r);
    }
    out.sort(function (a, b) {
      var x = a[COL.inc] || '', y = b[COL.inc] || '';
      return x < y ? -1 : x > y ? 1 : (a[COL.id] < b[COL.id] ? -1 : 1);
    });
    return out;
  }
  function listHeading(pid, key) {
    if (pid === 'incident') {
      if (key === 'undated') return 'Date not stated';
      if (key === 'later_step') return 'Later step in an older case';
      return dayLabel(key);
    }
    if (pid === 'added') return key === null ? 'Date added not recorded' : 'Added ' + dayLabel(key);
    return key;
  }
  function kindText(r) {
    var k = r[COL.kind];
    if (k === 'occurred') return 'Happened ' + dayLabel(r[COL.inc]);
    if (k === 'found') return 'Found ' + dayLabel(r[COL.inc]);
    if (k === 'later_step') return 'Later step in an older case';
    return 'Date not stated';
  }
  function dayKeys(pid, key) {
    if (pid === 'incident' && key !== 'undated' && key !== 'later_step') return AX.incident_days;
    if (pid === 'added' && key !== null) return AX.added_days;
    return null;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function renderList(pid) {
    var st = lists[pid];
    var page = document.getElementById('gp-page-' + pid);
    var box = page && page.querySelector('[data-gp-list]');
    if (!box) return;
    pickTargets.forEach(function (p) {
      if (p.pid === pid) p.el.classList.toggle('is-picked', !!st && p.key === st.key);
    });
    if (pid === 'incident' && LINE_PICK) LINE_PICK(st ? st.key : null);
    if (!st) { box.hidden = true; box.innerHTML = ''; return; }
    var rows = rowsFor(pid, st.key, current);
    var head = listHeading(pid, st.key);
    box.innerHTML = '';
    box.setAttribute('aria-label', head);
    var bar = el('div', 'gp-list__head');
    var days = dayKeys(pid, st.key), at = days ? days.indexOf(st.key) : -1;
    function stepBtn(sym, label, to) {
      var b = el('button', 'gp-hit gp-list__btn', sym);
      b.type = 'button';
      b.setAttribute('aria-label', label);
      if (to < 0 || to >= days.length) b.disabled = true;
      else b.addEventListener('click', function () { openList(pid, days[to], st.trigger, { keepFocus: b, dir: sym }); });
      return b;
    }
    if (days) bar.appendChild(stepBtn('\u2039', 'Previous day', at - 1));
    var h = el('h4', 'gp-list__title', head + ': ' + plural(rows.length, 'record'));
    bar.appendChild(h);
    if (days) bar.appendChild(stepBtn('\u203a', 'Next day', at + 1));
    var x = el('button', 'gp-hit gp-list__btn gp-list__close', '\u2715');
    x.type = 'button';
    x.setAttribute('aria-label', 'Close the list');
    x.addEventListener('click', function () { closeList(pid, true); });
    bar.appendChild(x);
    box.appendChild(bar);
    var stp = T.steps[current], on = streamsOn().length;
    var note = (stp.at ? 'On the map as of ' + stp.label + (current === LAST ? ' (now)' : '') : 'On the map now');
    if (STREAMS.length && on < STREAMS.length) note += ' \u00b7 ' + on + ' of ' + STREAMS.length + ' streams';
    box.appendChild(el('p', 'gp-list__note', note));
    if (!rows.length) {
      box.appendChild(el('p', 'gp-list__empty', 'No records here at this point.'));
    } else {
      var ul = el('ul', 'gp-list__items');
      rows.forEach(function (r) {
        var id = r[COL.id];
        var info = window.forgeSignalInfo ? window.forgeSignalInfo(id) : null;
        var li = el('li', 'gp-list__item');
        li.setAttribute('data-id', id);
        li.appendChild(el('span', 'gp-list__t', (info && info.title) || ('Record ' + id)));
        li.appendChild(el('span', 'gp-list__meta', [kindText(r), r[COL.province], r[COL.stream]].join(' \u00b7 ')));
        var a = el('a', 'gp-list__show', 'Show on map');
        a.href = '?signal=' + encodeURIComponent(id);
        a.addEventListener('click', function (e) {
          if (window.forgeOpenSignal && window.forgeOpenSignal(id, a)) e.preventDefault();
        });
        li.appendChild(a);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    box.hidden = false;
  }

  function openList(pid, key, trigger, opts) {
    lists[pid] = { key: key, trigger: trigger };
    renderList(pid);
    if (opts && opts.keepFocus) {
      // keep focus on the same previous / next button after the redraw
      var page = document.getElementById('gp-page-' + pid);
      var bs = page.querySelectorAll('.gp-list__head .gp-list__btn');
      var b = opts.dir === '\u2039' ? bs[0] : bs[1];
      if (b && !b.disabled) b.focus(); else { var x = page.querySelector('.gp-list__close'); if (x) x.focus(); }
    }
    revealList(pid);
  }
  function closeList(pid, refocus) {
    var st = lists[pid];
    delete lists[pid];
    renderList(pid);
    if (refocus && st && st.trigger && document.body.contains(st.trigger)) {
      try { st.trigger.focus({ preventScroll: true }); } catch (e) { st.trigger.focus(); }
    }
  }
  // Scroll the chart page (not the page track) so the list's top is in view.
  function revealList(pid) {
    var page = document.getElementById('gp-page-' + pid);
    var box = page && page.querySelector('[data-gp-list]');
    if (!box || box.hidden) return;
    var pr = page.getBoundingClientRect(), br = box.getBoundingClientRect();
    if (br.bottom > pr.bottom) page.scrollTop += Math.min(br.bottom - pr.bottom + 8, br.top - pr.top - 8);
  }
  panel.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var box = e.target.closest ? e.target.closest('[data-gp-list]') : null;
    if (!box) return;
    var page = box.closest('[data-chart]');
    if (page && lists[page.getAttribute('data-chart')]) { e.preventDefault(); closeList(page.getAttribute('data-chart'), true); }
  });

  setupLine();
  setupColumns();
  setupBars('stream', NOW.stream, AX.stream_max);
  setupBars('province', NOW.province, AX.province_max);

  /* ── The shared slider ── */
  var sliders = Array.prototype.slice.call(panel.querySelectorAll('[data-gp-time]'));
  var current = LAST;
  var announceTimer = null;

  function stepText(s, c) {
    var st = T.steps[s];
    var when = st.at ? 'Map as of ' + st.label + (s === LAST ? ' (now)' : '') : 'Map now, ' + st.label;
    var txt = when + ' \u00b7 ' + c.total + ' of ' + plural(T.total, 'record');
    var on = streamsOn().length;
    if (STREAMS.length && on < STREAMS.length) txt += ' (' + on + ' of ' + STREAMS.length + ' streams)';
    return txt;
  }

  function setStep(s, opts) {
    s = Math.max(0, Math.min(LAST, Math.round(+s)));
    var c = counts(s);
    var text = stepText(s, c);
    sliders.forEach(function (box) {
      var input = box.querySelector('input');
      if (+input.value !== s) input.value = s;
      input.setAttribute('aria-valuetext', text);
      box.querySelector('.gp-time__label').textContent = text;
    });
    var changed = s !== current;
    current = s;
    for (var i = 0; i < renderers.length; i++) renderers[i](c);
    for (var pid in lists) renderList(pid);
    if (changed || (opts && opts.force)) startMotion();
    clearTimeout(announceTimer);
    announceTimer = setTimeout(function () {
      for (var k = 0; k < announcers.length; k++) announcers[k](c);
    }, (opts && opts.settled) ? 0 : 350);
    if (changed) {
      try {
        document.dispatchEvent(new CustomEvent('gp-time', { detail: { step: s, at: T.steps[s].at, total: c.total } }));
      } catch (e) { /* old browsers: no event, charts still move */ }
    }
    return c;
  }

  sliders.forEach(function (box) {
    var input = box.querySelector('input');
    input.addEventListener('input', function () { setStep(input.value); });
    input.addEventListener('change', function () { setStep(input.value, { settled: true }); });
    box.hidden = false;
  });

  /* ── Stream switches (Batch 5) ──
     The chips and the Streams bars call the map's own setter, so the map's
     pins and Streams panel change too; the map tells us back with a
     'forge-streams' event and every chart recounts. */
  var chipGroups = Array.prototype.slice.call(panel.querySelectorAll('[data-gp-streams]'));
  var notes = Array.prototype.slice.call(panel.querySelectorAll('[data-gp-streams-note]'));
  function setStream(key, on) {
    if (window.ZADStreams && window.ZADStreams.set) window.ZADStreams.set(key, on);
    else { VIS[key] = !!on; streamsChanged(); }
  }
  function streamsChanged() {
    readVisible();
    var on = streamsOn();
    chipGroups.forEach(function (g) {
      Array.prototype.forEach.call(g.querySelectorAll('[data-gp-stream]'), function (b) {
        b.setAttribute('aria-pressed', VIS[b.getAttribute('data-gp-stream')] !== false ? 'true' : 'false');
      });
    });
    var text = on.length < STREAMS.length
      ? 'Showing ' + on.length + ' of ' + STREAMS.length + ' streams' +
        (on.length ? ' (' + on.map(function (s) { return s.label; }).join(', ') + ')' : '') + ', on the map too.'
      : '';
    notes.forEach(function (n) { n.textContent = text; n.hidden = !text; });
    setStep(current, { force: true, settled: true });
  }
  if (STREAMS.length) {
    chipGroups.forEach(function (g) {
      Array.prototype.forEach.call(g.querySelectorAll('[data-gp-stream]'), function (b) {
        var key = b.getAttribute('data-gp-stream');
        b.addEventListener('click', function () { setStream(key, VIS[key] === false); });
      });
      g.hidden = false;
    });
    document.addEventListener('forge-streams', streamsChanged);
    streamsChanged();
  }

  window.ZADRecordCharts = {
    table: T,
    counts: counts,
    setStep: setStep,
    step: function () { return current; },
    steps: T.steps.length,
    animating: function () { return !!raf; },
    line: LINE,
    openList: function (pid, key) { openList(pid, key, null); },
    closeList: closeList,
    ids: function (pid, key) { return rowsFor(pid, key, current).map(function (r) { return r[COL.id]; }); },
    streams: function () { return streamsOn().map(function (s) { return s.key; }); },
    setStream: setStream
  };
})();
