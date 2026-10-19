// Real-browser check of Area check on desktop, a narrow laptop and a phone.
// Run against a served dist/ (or the live site):
//   FORGE_UI_BASE=http://127.0.0.1:8937/ node tests/browser/area_check.browser.js
// Needs the playwright package (NODE_PATH may point at it). Exits 1 on failure.
'use strict';
const { chromium, devices } = require('playwright');
const BASE = (process.env.FORGE_UI_BASE || '').replace(/\/?$/, '/');
if (!process.env.FORGE_UI_BASE) { console.log('FORGE_UI_BASE not set: skipped'); process.exit(0); }

const FAR_PIN = '335b265d-2edd-54e2-969e-31e38733ff3a';   // Mqanduli, far from Cape Town
let failures = 0;
function check(ok, what) { console.log((ok ? 'ok   ' : 'FAIL ') + what); if (!ok) failures++; }
function box(p, sel) {
  return p.evaluate(s => {
    const e = document.querySelector(s); if (!e || e.hidden) return null;
    const r = e.getBoundingClientRect();
    return r.width ? { x: r.x, y: r.y, r: r.right, b: r.bottom } : null;
  }, sel);
}
function overlap(a, b) { return !!(a && b && a.x < b.r && b.x < a.r && a.y < b.b && b.y < a.b); }
const isOpen = p => p.evaluate(() => document.getElementById('panel-area').classList.contains('open'));
async function ensurePanel(p) { if (!(await isOpen(p))) { await p.click('#ac-pill'); await p.waitForTimeout(400); } }
const text = (p, sel) => p.evaluate(s => document.querySelector(s).textContent, sel);
const hidden = (p, sel) => p.evaluate(s => document.querySelector(s).hidden, sel);
const pinCount = p => p.evaluate(() => document.querySelectorAll('.leaflet-marker-icon:not(:has(.ac-home-pin))').length);

async function run(name, ctxOpts) {
  console.log(`\n== ${name}`);
  const browser = await chromium.launch();
  const ctx = await browser.newContext(ctxOpts);
  const p = await ctx.newPage();
  const errors = [], hosts = new Set();
  p.on('pageerror', e => errors.push(e.message));
  p.on('request', r => hosts.add(new URL(r.url()).host));
  await p.goto(BASE, { waitUntil: 'load' });
  await p.waitForTimeout(1200);
  check(await hidden(p, '#ac-home'), 'home base hidden before a place is set');

  await p.click('#ac-pill'); await p.waitForTimeout(500);
  check(await isOpen(p), 'Area check panel opens');

  await p.fill('#ac-q', 'Wesbank'); await p.press('#ac-q', 'Enter');
  await p.waitForSelector('.ac-match');
  const wes = await p.$$eval('.ac-match', els => els.map(e => e.textContent));
  check(wes.length >= 3 && wes.length <= 5, `Wesbank lists ${wes.length} matches, none picked for you`);
  check(await hidden(p, '#ac-set'), 'no centre set until a match is picked');

  await p.fill('#ac-q', 'zzqxv'); await p.press('#ac-q', 'Enter'); await p.waitForTimeout(200);
  check(/place the centre on the map/.test(await text(p, '#ac-match-status')), 'no match offers placing on the map');

  await p.fill('#ac-q', 'Bonteheuwel'); await p.press('#ac-q', 'Enter');
  await p.waitForSelector('.ac-match'); await p.click('.ac-match'); await p.waitForTimeout(1300);
  const s2 = await text(p, '#ac-summary');
  check(/^1 reported killing within 2 km\. [1-9] more may be inside/.test(s2), `Bonteheuwel 2 km: "${s2}"`);
  await p.click('[data-radius="1000"]'); await p.waitForTimeout(900);
  const s1 = await text(p, '#ac-summary');
  check(/^0 reported killings within 1 km\. [1-9] more may be inside/.test(s1), `Bonteheuwel 1 km: "${s1}"`);
  await p.click('[data-radius="2000"]'); await p.waitForTimeout(900);
  check(!(await hidden(p, '#ac-home')), 'home base button shows once a place is set');
  const shown = await pinCount(p);
  check(shown >= 1 && shown <= 4, `only the area's pins are on the map (${shown})`);

  await p.click('[data-dates="7"]'); await p.waitForTimeout(300);
  check(/between/.test(await text(p, '#ac-summary')), 'date filter narrows the count');
  await p.click('[data-dates="all"]'); await p.waitForTimeout(300);

  // open a report from the list, then a far pin: it opens anyway, with a note
  await p.click('.ac-res'); await p.waitForTimeout(800);
  check(!(await hidden(p, '#signal-drawer')), 'report opens from the area list');
  check(await hidden(p, '#sd-outside'), 'no outside note for a pin inside');
  if (!(await isOpen(p))) {
    check(!(await hidden(p, '#ac-bar')), 'one-line bar shows while the panel is closed');
    const bar = await box(p, '#ac-bar');
    check(!overlap(bar, await box(p, '.md-controls')) && !overlap(bar, await box(p, '#signal-drawer')) &&
          !overlap(bar, await box(p, '.md-sidebar')), 'bar does not cover controls, drawer or pills');
  }
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  check(await hidden(p, '#signal-drawer'), 'Escape closes the drawer first');

  await p.goto(BASE + '?signal=' + FAR_PIN, { waitUntil: 'load' }); await p.waitForTimeout(1500);
  check(!(await hidden(p, '#signal-drawer')), 'a pin outside the circle still opens from a link');
  check(!(await hidden(p, '#sd-outside')), 'it says it is outside your area');
  await p.click('#sd-outside-all'); await p.waitForTimeout(400);
  check(await hidden(p, '#sd-outside'), 'Show all reports clears the note');
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  await p.click('#ac-home'); await p.waitForTimeout(1200);
  await ensurePanel(p);
  check(/within 2 km/.test(await text(p, '#ac-summary')), 'home base turns the area filter back on');

  // place a point on the map
  await p.click('#ac-place-btn'); await p.waitForTimeout(500);
  check(!overlap(await box(p, '#ac-cross'), await box(p, '#ac-placebar')), 'crosshair is not under the placing bar');
  await p.click('#ac-place-use'); await p.waitForTimeout(1200);
  check((await text(p, '#ac-centre-name')) === 'Your map point', 'map point becomes the centre');

  // remember, reload, forget
  await p.click('#ac-remember'); await p.waitForTimeout(100);
  const saved = await p.evaluate(() => JSON.parse(localStorage.getItem('zad.areaCheck.v1') || 'null'));
  check(saved && saved.radius === 2000 && String(saved.lat).split('.')[1].length <= 4, 'remembered on this device, rounded');
  await p.reload({ waitUntil: 'load' }); await p.waitForTimeout(1200);
  check(!(await hidden(p, '#ac-home')), 'remembered place is back after a reload');
  await ensurePanel(p);
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  check(!(await isOpen(p)) && (await p.evaluate(() => document.activeElement.id)) === 'ac-pill',
        'Escape closes the panel and focus returns to Area check');
  await ensurePanel(p); await p.click('#ac-forget'); await p.waitForTimeout(200);
  const after = await p.evaluate(() => [localStorage.getItem('zad.areaCheck.v1'), sessionStorage.getItem('zad.areaCheck.v1')]);
  check(!after[0] && !after[1] && await hidden(p, '#ac-home'), 'forget clears the place');

  await p.evaluate(() => localStorage.setItem('zad.areaCheck.v1', JSON.stringify({ v: 1, lat: 51.5, lng: -0.1, label: 'London', radius: 2000 })));
  await p.reload({ waitUntil: 'load' }); await p.waitForTimeout(1000);
  check(!(await p.evaluate(() => localStorage.getItem('zad.areaCheck.v1'))), 'a bad saved place is thrown away');

  const outside = [...hosts].filter(h => /nominatim|geoapify|googleapis\.com\/maps|mapbox|photon/.test(h));
  check(outside.length === 0, 'no outside geocoder contacted');
  check(errors.length === 0, 'no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await browser.close();
}

(async () => {
  await run('desktop 1366x820', { viewport: { width: 1366, height: 820 } });
  await run('laptop 1024x768', { viewport: { width: 1024, height: 768 } });
  const phone = { ...devices['iPhone 13'] }; delete phone.defaultBrowserType;
  await run('phone (iPhone 13 size)', phone);
  console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
