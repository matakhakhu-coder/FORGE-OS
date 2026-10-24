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
 *
 * Heat map (Batch 8): records by town and incident date. Each cell and town
 * name becomes a button: tapping one lists its records (as the other charts
 * do) and filters the map to that town and day (window.ZADMapTime.setCell,
 * with the map's Clear button to undo). The cell filter sits on top of the
 * slider's map filter and the stream switches; closing the list clears it.
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
  var TOWNS = AX.town || [], TI = {}, DI = {};
  TOWNS.forEach(function (t, i) { TI[t] = i; });
  AX.incident_days.forEach(function (d, i) { DI[d] = i; });
  function counts(step) {
    var inc = {}, added = {}, stream = {}, province = {};
    var tc = TOWNS.map(function () { return AX.incident_days.map(function () { return 0; }); });
    var tu = TOWNS.map(function () { return 0; }), tl = TOWNS.map(function () { return 0; });
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
      if (COL.town !== undefined && TI[r[COL.town]] !== undefined) {
        var ti = TI[r[COL.town]];
        if (k === 'later_step') tl[ti]++;
        else if (k === 'undated') tu[ti]++;
        else tc[ti][DI[r[COL.inc]]]++;
      }
    }
    out.incident = AX.incident_days.map(function (d) { return inc[d]; });
    out.added = AX.added_days.map(function (d) { return added[d]; });
    out.stream = AX.stream.map(function (l) { return stream[l] || 0; });
    out.province = AX.province.map(function (l) { return province[l] || 0; });
    out.town = { cells: tc, undated: tu, later_step: tl,
                 rows: tc.map(function (row, i) { return row.reduce(function (a, b) { return a + b; }, 0) + tu[i] + tl[i]; }) };
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
  /* ── Zoom into a week (Batch 7) ──
     Week buttons above the incident line and the Added columns: "All", then
     the weeks Python cut from the day axis (tools/record_charts.chart_weeks).
     A week only narrows what is drawn; the slider, the streams and every
     count work the same inside it, and the week note says how the records
     split between the week and the other days, so the totals add up. An axis
     of a week or less gets no buttons. */
  var WEEKS = {};           // page id -> { weeks, at (index or -1), pick }
  function weekBar(pid, weeks, before, onPick) {
    if (!weeks || weeks.length < 2 || !before) return null;
    var wrap = el('div', 'gp-weeks');
    var group = el('div', 'gp-weeks__btns');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Zoom into a week');
    var note = el('p', 'gp-weeks__note');
    note.hidden = true;
    var btns = [];
    var W = { weeks: weeks, at: -1, note: note };
    [{ label: 'All' }].concat(weeks).forEach(function (w, k) {
      var b = el('button', 'gp-weeks__btn', w.label);
      b.type = 'button';
      b.setAttribute('aria-pressed', k ? 'false' : 'true');
      b.setAttribute('data-week', String(k - 1));
      if (k) b.setAttribute('aria-label', 'Zoom into ' + w.label);
      b.addEventListener('click', function () { W.pick(k - 1); });
      group.appendChild(b);
      btns.push(b);
    });
    W.pick = function (k) {
      k = Math.max(-1, Math.min(weeks.length - 1, +k));
      W.at = k;
      btns.forEach(function (b, j) { b.setAttribute('aria-pressed', j === k + 1 ? 'true' : 'false'); });
      onPick(k < 0 ? null : weeks[k]);
      setStep(current, { force: true, settled: true });
    };
    wrap.appendChild(group);
    wrap.appendChild(note);
    before.parentNode.insertBefore(wrap, before);
    WEEKS[pid] = W;
    return W;
  }
  // "15 to 21 Oct: 22 records on the line · 43 on other days"
  function weekNote(W, perDay, what) {
    if (!W) return;
    if (W.at < 0) { W.note.hidden = true; return; }
    var w = W.weeks[W.at], inside = 0, every = 0;
    for (var i = 0; i < perDay.length; i++) {
      every += perDay[i];
      if (i >= w.from && i <= w.to) inside += perDay[i];
    }
    W.note.textContent = w.label + ': ' + plural(inside, 'record') + ' ' + what + ' \u00b7 ' + (every - inside) + ' on other days';
    W.note.hidden = false;
  }

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
    var colScroll = page.querySelector('.gp-cols-scroll');
    var addedWeek = weekBar('added', AX.added_weeks, colScroll, function (w) {
      Array.prototype.forEach.call(items, function (li, i) {
        li.hidden = !!w && (i < w.from || i > w.to);
      });
      var st = lists.added;
      if (w && st && st.key !== null && (AX.added_days.indexOf(st.key) < w.from || AX.added_days.indexOf(st.key) > w.to)) closeList('added');
    });
    renderers.push(function (c) {
      weekNote(addedWeek, c.added, 'added');
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
    // the days drawn: all of them, or one week (Batch 7)
    var view = [0, xs.length - 1];
    function drawPath() { path.setAttribute('d', monotonePath(xs.slice(view[0], view[1] + 1), ys.slice(view[0], view[1] + 1))); }
    afterFrame.push(drawPath);

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
    var edges = [], hitRects = [];
    function placeHits() {
      var a0 = view[0], b0 = view[1];
      for (var i = 0; i < xs.length; i++) {
        if (i < a0 || i > b0) { edges[i] = null; hitRects[i].style.display = 'none'; continue; }
        var x = xs[i];
        var a = i > a0 ? (xs[i - 1] + x) / 2 : Math.max(vb[0], x - ((i < b0 ? xs[i + 1] - x : 0) || 10) / 2);
        var b = i < b0 ? (x + xs[i + 1]) / 2 : Math.min(vb[0] + vb[2], x + ((i > a0 ? x - xs[i - 1] : 0) || 10) / 2);
        edges[i] = [a, b];
        hitRects[i].setAttribute('x', a.toFixed(2)); hitRects[i].setAttribute('width', (b - a).toFixed(2));
        hitRects[i].style.display = '';
      }
    }
    xs.forEach(function (x, i) {
      var r = document.createElementNS(NS, 'rect');
      r.setAttribute('class', 'gp-line__hit');
      r.setAttribute('y', vb[1]); r.setAttribute('height', vb[3]);
      r.setAttribute('data-day', AX.incident_days[i]);
      r.addEventListener('click', function () { openList('incident', AX.incident_days[i], fig); });
      hits.appendChild(r);
      hitRects.push(r);
    });
    placeHits();
    svg.appendChild(hits);

    // Week zoom (Batch 7): the week's days spread across the chart's width,
    // each day labelled under the line; the height keeps today's scale.
    var xs0 = xs.slice();
    var X0 = T.line.x0, X1 = T.line.x1;
    var xlabs = svg.querySelectorAll('.gp-line__xlab');
    var zoomLabs = document.createElementNS(NS, 'g');
    zoomLabs.setAttribute('class', 'gp-line__zoomlabs');
    svg.insertBefore(zoomLabs, hits);
    var lineWeek = weekBar('incident', AX.incident_weeks, svg.parentNode, function (w) {
      view = w ? [w.from, w.to] : [0, xs.length - 1];
      var a = view[0], b = view[1], zoomed = !!w;
      while (zoomLabs.firstChild) zoomLabs.removeChild(zoomLabs.firstChild);
      for (var i = 0; i < xs.length; i++) {
        var on = i >= a && i <= b;
        circles[i].style.display = on ? '' : 'none';
        if (!on) continue;
        xs[i] = !zoomed ? xs0[i] : (b > a ? X0 + (X1 - X0) * (i - a) / (b - a) : (X0 + X1) / 2);
        circles[i].setAttribute('cx', xs[i].toFixed(2));
        if (zoomed) {
          var t = document.createElementNS(NS, 'text');
          t.setAttribute('class', 'gp-line__xlab');
          t.setAttribute('x', xs[i].toFixed(2));
          t.setAttribute('y', (T.line.h - 8).toFixed(0));
          t.setAttribute('text-anchor', 'middle');
          var d = AX.incident_days[i];
          t.textContent = i === a ? dayLabel(d) : String(+d.slice(8, 10));
          zoomLabs.appendChild(t);
        }
      }
      Array.prototype.forEach.call(xlabs, function (t) { t.style.display = zoomed ? 'none' : ''; });
      placeHits();
      drawPath();
      var st = lists.incident;
      if (st && st.key !== 'undated' && st.key !== 'later_step') {
        var k = AX.incident_days.indexOf(st.key);
        if (k < a || k > b) closeList('incident');
        else if (LINE_PICK) LINE_PICK(st.key);
      }
    });
    var fig = svg.parentNode;
    fig.tabIndex = 0;
    fig.setAttribute('aria-label', 'Records by day. Enter lists a day\u2019s records; the left and right arrow keys change the day.');
    fig.addEventListener('keydown', function (e) {
      var days = AX.incident_days.slice(view[0], view[1] + 1), st = lists.incident, at = (st && days.indexOf(st.key));
      if (at === undefined || at === null || at < 0) at = -1;
      var to = null;
      if (e.key === 'ArrowRight') to = at < 0 ? 0 : Math.min(days.length - 1, at + 1);
      else if (e.key === 'ArrowLeft') to = at < 0 ? days.length - 1 : Math.max(0, at - 1);
      else if (e.key === 'Home') to = 0;
      else if (e.key === 'End') to = days.length - 1;
      else if (e.key === 'Enter' || e.key === ' ') {
        if (at < 0) { var c = counts(current).incident.slice(view[0], view[1] + 1), best = 0; c.forEach(function (v, i) { if (v > c[best]) best = i; }); to = best; }
        else to = at;
      } else if (e.key === 'Escape' && st) { closeList('incident'); e.preventDefault(); return; }
      if (to === null) return;
      e.preventDefault(); e.stopPropagation();
      openList('incident', days[to], fig);
    });
    LINE_PICK = function (key) {
      var i = AX.incident_days.indexOf(key);
      if (i < 0 || !edges[i]) { band.style.display = 'none'; return; }
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
      weekNote(lineWeek, c.incident, 'on the line');
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
      view: function () { return view.slice(); },
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
  var HEAT_PICK = null;

  /* ── The map follows a heat map cell (Batch 8) ──
     While a cell (or a town name) is picked, the map keeps only the pins of
     that town and day. The IDs sent are every record of the cell, before the
     slider and the streams: the map applies its own slider filter, stream
     switches and Area check on top, so what it shows is the list's records.
     The note's text says how many records the list holds now. */
  var cellKey = null;
  function cellIds(key) {
    var out = [];
    for (var i = 0; i < T.rows.length; i++) if (rowMatches(T.rows[i], 'town', key)) out.push(T.rows[i][COL.id]);
    return out;
  }
  function syncCell(st, rows) {
    var M = window.ZADMapTime;
    if (!M || !M.setCell) return;
    if (!st) {
      if (cellKey !== null) M.setCell(null);
      cellKey = null;
      return;
    }
    var text = 'Map: ' + listHeading('town', st.key) + ' \u00b7 ' + plural(rows.length, 'record');
    var on = streamsOn().length;
    if (STREAMS.length && on < STREAMS.length) text += ' (' + on + ' of ' + STREAMS.length + ' streams)';
    M.setCell({ ids: st.key !== cellKey ? cellIds(st.key) : null, text: text });
    cellKey = st.key;
  }

  function rowMatches(r, pid, key) {
    if (pid === 'incident') {
      return (key === 'undated' || key === 'later_step') ? r[COL.kind] === key
        : ((r[COL.kind] === 'occurred' || r[COL.kind] === 'found') && r[COL.inc] === key);
    }
    if (pid === 'added') return r[COL.added] === key;
    if (pid === 'town') {
      // key "Town|2026-10-16", "Town|undated", "Town|later_step" or "Town|*"
      var tk = townKey(key), k = r[COL.kind];
      if (r[COL.town] !== tk.town) return false;
      if (tk.day === '*') return true;
      if (tk.day === 'undated' || tk.day === 'later_step') return k === tk.day;
      return (k === 'occurred' || k === 'found') && r[COL.inc] === tk.day;
    }
    return r[COL[pid]] === key;
  }
  function townKey(key) {
    var s = String(key), i = s.lastIndexOf('|');
    return { town: s.slice(0, i), day: s.slice(i + 1) };
  }
  function townDayText(day) {
    if (day === '*') return 'every date';
    if (day === 'undated') return 'date not stated';
    if (day === 'later_step') return 'later step in an older case';
    return dayLabel(day);
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
    if (pid === 'town') { var tk = townKey(key); return tk.town + ', ' + townDayText(tk.day); }
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
    var days = null;
    if (pid === 'incident' && key !== 'undated' && key !== 'later_step') days = AX.incident_days;
    if (pid === 'added' && key !== null) days = AX.added_days;
    if (pid === 'town' && DI[townKey(key).day] !== undefined) days = AX.incident_days;
    // inside a week (Batch 7), previous and next day stay in the week
    var W = WEEKS[pid];
    if (days && W && W.at >= 0) days = days.slice(W.weeks[W.at].from, W.weeks[W.at].to + 1);
    // the heat map steps through the days of the same town
    if (days && pid === 'town') { var tw0 = townKey(key).town; days = days.map(function (d) { return tw0 + '|' + d; }); }
    return days;
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
    if (pid === 'town' && HEAT_PICK) HEAT_PICK(st ? st.key : null);
    var rows = st ? rowsFor(pid, st.key, current) : [];
    if (pid === 'town') syncCell(st, rows);
    if (!st) { box.hidden = true; box.innerHTML = ''; return; }
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

  /* ── Heat map: records by town and incident date (Batch 8) ──
     Rows are today's towns and columns the incident line's days, then
     "date not stated" and "later step" (both fixed, so nothing moves around
     while scrubbing). Each cell's shade eases to its new count on today's
     fixed scale; the number shows the step's whole count (blank for zero).
     Cells and town names are buttons with one tab stop: the arrow keys,
     Home and End move between them, Enter opens the list. */
  function setupHeat() {
    var page = document.getElementById('gp-page-town');
    if (!page || !TOWNS.length) return;
    var table = page.querySelector('.gp-heat');
    if (!table) return;
    var trs = table.querySelectorAll('tbody tr');
    var nd = AX.incident_days.length, ncol = nd + 2;
    if (trs.length !== TOWNS.length) return;
    var max = Math.max(AX.town_max || 0, 1);
    var summary = page.querySelector('.gp-chart__summary');
    var heads = table.querySelectorAll('thead th');          // corner, days, ?, L
    var grid = [];            // grid[row] = [townBtn, cell buttons...]
    var cellTw = [], nums = [], totals = [];
    function shade(v) { return v > 0 ? (0.18 + 0.82 * Math.min(1, v / max)) : 0; }
    function colKey(j) { return j < nd ? AX.incident_days[j] : (j === nd ? 'undated' : 'later_step'); }
    Array.prototype.forEach.call(trs, function (tr, i) {
      var town = TOWNS[i];
      var th = tr.querySelector('th');
      var tb = el('button', 'gp-hit gp-heat__townbtn');
      tb.type = 'button';
      while (th.firstChild) tb.appendChild(th.firstChild);
      th.appendChild(tb);
      totals.push(tb.querySelector('.gp-heat__total'));
      tb.addEventListener('click', function () { openList('town', town + '|*', tb); });
      pickTargets.push({ pid: 'town', key: town + '|*', el: tb });
      var row = [tb], rtw = [], rn = [];
      var tds = tr.querySelectorAll('td');
      Array.prototype.forEach.call(tds, function (td, j) {
        if (j >= ncol) return;
        var fill = td.querySelector('.gp-heat__fill'), n = td.querySelector('.gp-heat__n');
        var b = el('button', 'gp-hit gp-heat__btn');
        b.type = 'button';
        b.tabIndex = -1;
        if (n) b.appendChild(n);
        td.appendChild(b);
        var key = town + '|' + colKey(j);
        b.setAttribute('data-key', key);
        b.addEventListener('click', function () { openList('town', key, b); });
        pickTargets.push({ pid: 'town', key: key, el: b });
        row.push(b); rn.push(n);
        var init = j < nd ? NOW.town.cells[i][j] : (j === nd ? NOW.town.undated[i] : NOW.town.later_step[i]);
        rtw.push(tween(init, function (v) { if (fill) fill.style.opacity = shade(v).toFixed(3); }));
      });
      tb.tabIndex = -1;
      grid.push(row); cellTw.push(rtw); nums.push(rn);
    });
    if (grid[0]) grid[0][0].tabIndex = 0;

    // keyboard: one tab stop, arrows move (hidden week columns are skipped)
    var visCols = null;
    function colVisible(c) { return c === 0 || !visCols || visCols[c - 1]; }
    function focusCell(r, c) {
      grid.forEach(function (row) { row.forEach(function (b) { b.tabIndex = -1; }); });
      grid[r][c].tabIndex = 0;
      grid[r][c].focus();
    }
    table.addEventListener('keydown', function (e) {
      var t = e.target, r = -1, c = -1;
      for (var i = 0; i < grid.length && r < 0; i++) { var j = grid[i].indexOf(t); if (j >= 0) { r = i; c = j; } }
      if (r < 0) return;
      var nr = r, nc = c, step;
      if (e.key === 'ArrowDown') nr = Math.min(grid.length - 1, r + 1);
      else if (e.key === 'ArrowUp') nr = Math.max(0, r - 1);
      else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        step = e.key === 'ArrowRight' ? 1 : -1;
        nc = c + step;
        while (nc > 0 && nc <= ncol && !colVisible(nc)) nc += step;
        if (nc < 0 || nc > ncol) nc = c;
      } else if (e.key === 'Home') nc = 0;
      else if (e.key === 'End') { nc = ncol; while (nc > 0 && !colVisible(nc)) nc--; }
      else if (e.key === 'Escape' && lists.town) { closeList('town', true); e.preventDefault(); return; }
      else return;
      e.preventDefault(); e.stopPropagation();
      focusCell(nr, nc);
    });

    HEAT_PICK = function (key) {
      Array.prototype.forEach.call(trs, function (tr, i) {
        tr.classList.toggle('is-row-picked', !!key && townKey(key).town === TOWNS[i]);
      });
    };

    // Week zoom (Batch 7 buttons): only that week's day columns show, so
    // they widen; "?" and "L" stay.
    var heatWeek = weekBar('town', AX.incident_weeks, page.querySelector('.gp-heat-scroll'), function (w) {
      visCols = [];
      for (var j = 0; j < ncol; j++) visCols.push(j >= nd || !w || (j >= w.from && j <= w.to));
      Array.prototype.forEach.call(heads, function (h, k) { if (k > 0) h.hidden = !visCols[k - 1]; });
      Array.prototype.forEach.call(trs, function (tr) {
        Array.prototype.forEach.call(tr.querySelectorAll('td'), function (td, j) { if (j < ncol) td.hidden = !visCols[j]; });
      });
      var st = lists.town;
      if (w && st) {
        var di = DI[townKey(st.key).day];
        if (di !== undefined && (di < w.from || di > w.to)) closeList('town');
      }
    });

    renderers.push(function (c) {
      var T8 = c.town, perDay = AX.incident_days.map(function () { return 0; }), und = 0, lat = 0;
      for (var i = 0; i < grid.length; i++) {
        var town = TOWNS[i];
        totals[i].textContent = T8.rows[i];
        grid[i][0].setAttribute('aria-label', town + ': ' + plural(T8.rows[i], 'record') + '. List them and show them on the map');
        for (var j = 0; j < ncol; j++) {
          var v = j < nd ? T8.cells[i][j] : (j === nd ? T8.undated[i] : T8.later_step[i]);
          if (j < nd) perDay[j] += v;
          cellTw[i][j].to = v;
          if (nums[i][j]) nums[i][j].textContent = v ? String(v) : '';
          grid[i][j + 1].setAttribute('aria-label', town + ', ' + townDayText(colKey(j)) + ': ' + plural(v, 'record'));
        }
        und += T8.undated[i]; lat += T8.later_step[i];
      }
      weekNote(heatWeek, perDay, 'on a day');
      if (summary) {
        var dated = perDay.reduce(function (a, b) { return a + b; }, 0);
        var txt = dated + ' on a day (' + dayLabel(AX.incident_days[0]) + ' to ' + dayLabel(AX.incident_days[nd - 1]) + ')';
        if (und) txt += ' \u00b7 ' + und + ' date not stated';
        if (lat) txt += ' \u00b7 ' + lat + ' later step in an older case';
        txt += ' \u00b7 ' + plural(TOWNS.length, 'town');
        summary.textContent = txt;
      }
    });
    announcers.push(function (c) {
      table.setAttribute('aria-label', 'Records by town and incident date: ' +
        TOWNS.map(function (t, i) { return t + ' ' + c.town.rows[i]; }).join(', '));
    });
  }

  setupLine();
  setupColumns();
  setupBars('stream', NOW.stream, AX.stream_max);
  setupBars('province', NOW.province, AX.province_max);
  setupHeat();

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

  /* ── The map follows the slider (Batch 6) ──
     Off "now", the map keeps only the pins added by the slider's publish:
     the same rows the charts count (step <= s), before streams, because the
     map applies its own stream switches and Area check on top. It changes once
     per publish step, never per animation frame. At "now" the map is left
     exactly as it is without the charts. */
  var mapStep = LAST;
  function idsAtStep(s) {
    var out = [];
    for (var i = 0; i < T.rows.length; i++) {
      var st = T.rows[i][COL.step];
      if (st !== null && st <= s) out.push(T.rows[i][COL.id]);
    }
    return out;
  }
  function pushMap(s, c, changed) {
    var M = window.ZADMapTime;
    if (!M || !M.setTime) return;
    if (s === LAST) {
      if (mapStep !== LAST) M.setTime(null);
      mapStep = LAST;
      return;
    }
    var st = T.steps[s];
    var text = 'Map as of ' + st.label + ' (pins added by then) \u00b7 ' +
      c.total + ' of ' + plural(T.total, 'record');
    var on = streamsOn().length;
    if (STREAMS.length && on < STREAMS.length) text += ' (' + on + ' of ' + STREAMS.length + ' streams)';
    M.setTime({ ids: s !== mapStep ? idsAtStep(s) : null, text: text });
    mapStep = s;
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
    pushMap(s, c, changed);
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
    setStream: setStream,
    idsAtStep: idsAtStep,
    clearCell: function () { closeList('town'); },
    cell: function () { return cellKey; },
    cellIds: cellIds,
    pickWeek: function (pid, k) { if (WEEKS[pid]) WEEKS[pid].pick(k); },
    week: function (pid) { return WEEKS[pid] ? WEEKS[pid].at : null; }
  };
})();
