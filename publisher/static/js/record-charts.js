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

  /* ── Counting: filter the rows to one step (mirrors counts_from_table) ── */
  function counts(step) {
    var inc = {}, added = {}, stream = {}, province = {};
    AX.incident_days.forEach(function (d) { inc[d] = 0; });
    AX.added_days.forEach(function (d) { added[d] = 0; });
    var out = { total: 0, occurred: 0, found: 0, undated: 0, later_step: 0, added_undated: 0 };
    for (var i = 0; i < T.rows.length; i++) {
      var r = T.rows[i];
      if (r[COL.step] > step) continue;
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

  /* ── Bars (Streams, Provinces): grow along their length ── */
  function setupBars(pid, values, max) {
    var page = document.getElementById('gp-page-' + pid);
    if (!page) return;
    var items = page.querySelectorAll('.gp-bar');
    if (items.length !== values.length) return;
    var tw = [], nums = [];
    Array.prototype.forEach.call(items, function (li, i) {
      var fill = li.querySelector('.gp-bar__fill');
      nums.push(li.querySelector('.gp-bar__n'));
      tw.push(tween(values[i], function (v) { fill.style.width = (max ? v / max * 100 : 0).toFixed(2) + '%'; }));
    });
    renderers.push(function (c) {
      var vals = c[pid];
      for (var i = 0; i < tw.length; i++) { tw[i].to = vals[i]; nums[i].textContent = vals[i]; }
    });
  }

  /* ── Columns (Added by day): grow up from the base ── */
  function setupColumns() {
    var page = document.getElementById('gp-page-added');
    if (!page) return;
    var items = page.querySelectorAll('.gp-col');
    if (items.length !== NOW.added.length) return;
    var max = AX.added_max, tw = [], nums = [];
    Array.prototype.forEach.call(items, function (li, i) {
      var bar = li.querySelector('.gp-col__bar');
      nums.push(li.querySelector('.gp-col__n'));
      tw.push(tween(NOW.added[i], function (v) { bar.style.height = (max ? v / max * 100 : 0).toFixed(2) + '%'; }));
    });
    var undatedEl = page.querySelector('.gp-chart__summary');
    renderers.push(function (c) {
      for (var i = 0; i < tw.length; i++) { tw[i].to = c.added[i]; nums[i].textContent = c.added[i]; }
      if (undatedEl) {
        undatedEl.hidden = !c.added_undated;
        undatedEl.textContent = plural(c.added_undated, 'record') + ': date added not recorded';
      }
    });
  }

  setupColumns();
  setupBars('stream', NOW.stream, AX.stream_max);
  setupBars('province', NOW.province, AX.province_max);

  /* ── The shared slider ── */
  var sliders = Array.prototype.slice.call(panel.querySelectorAll('[data-gp-time]'));
  var current = LAST;
  var announceTimer = null;
  var announcers = [];      // run when the slider stops (screen-reader labels)

  function stepText(s, c) {
    var st = T.steps[s];
    var when = st.at ? 'Map as of ' + st.label + (s === LAST ? ' (now)' : '') : 'Map now, ' + st.label;
    return when + ' \u00b7 ' + c.total + ' of ' + plural(T.total, 'record');
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

  window.ZADRecordCharts = {
    table: T,
    counts: counts,
    setStep: setStep,
    step: function () { return current; },
    steps: T.steps.length,
    animating: function () { return !!raf; },
    _registerLine: function (render, announce, hook) {
      if (render) renderers.push(render);
      if (announce) announcers.push(announce);
      if (hook) afterFrame.push(hook);
    },
    _tween: tween
  };
})();
