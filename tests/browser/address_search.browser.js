// Real-browser check of the typed street-address search (Area check, Sprint C)
// on desktop, a narrow laptop and a phone.
//   FORGE_UI_BASE=http://localhost:8937/ node tests/browser/address_search.browser.js
// By default every Geoapify call is answered by a stub (fixtures in
// tests/js/fixtures/geoapify), so nothing is sent and the key is not used.
// FORGE_GEOCODER_REAL=1 also runs the fixed list of South African test
// addresses against the real service and prints the top matches.
// Needs the playwright package (NODE_PATH may point at it). Exits 1 on failure.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { chromium, devices } = require('playwright');
const BASE = (process.env.FORGE_UI_BASE || '').replace(/\/?$/, '/');
if (!process.env.FORGE_UI_BASE) { console.log('FORGE_UI_BASE not set: skipped'); process.exit(0); }
const REAL = process.env.FORGE_GEOCODER_REAL === '1';
const FX = n => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'js', 'fixtures', 'geoapify', n + '.json'), 'utf8')).data;
const FAIL_TEXT = /Search didn\u2019t respond\. Try again or place it on the map\./;
const PRIVACY = 'Typed addresses are sent to Geoapify, along with your IP address, to find the spot. FORGE Map never receives them.';

// The fixed list from the plan: Cape Town streets, a township street,
// Ipelegeng, a farm, Potchefstroom and others.
const ADDRESSES = [
  'Jakkalsvlei Avenue, Bonteheuwel',
  'Long Street, Cape Town',
  'Klipfontein Road, Athlone',
  'Mew Way, Khayelitsha',
  'Vuyani Street, Gugulethu',
  'Kgaka Street, Ipelegeng',
  'Kgaka Street, Schweizer-Reneke',
  'Doornpan farm, Ottosdal',
  'Doornpan',
  'Potchefstroom',
  'Kuzwayo Street, KwaZakhele',
  '12 Voortrekker Road, Parow',
  'asdfqwerty zzz'
];

let failures = 0;
function check(ok, what) { console.log((ok ? 'ok   ' : 'FAIL ') + what); if (!ok) failures++; }
const text = (p, sel) => p.evaluate(s => document.querySelector(s).textContent, sel);
const hidden = (p, sel) => p.evaluate(s => document.querySelector(s).hidden, sel);
const visible = (p, sel) => p.evaluate(s => {
  const e = document.querySelector(s); if (!e) return false;
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
}, sel);
const isOpen = p => p.evaluate(() => document.getElementById('panel-area').classList.contains('open'));
async function ensurePanel(p) { if (!(await isOpen(p))) { await p.click('#ac-pill'); await p.waitForTimeout(400); } }
async function search(p, q) {
  await p.fill('#ac-addr-q', q);
  await p.click('#ac-addr-go');
}

async function run(name, ctxOpts) {
  console.log(`\n== ${name}`);
  const browser = await chromium.launch();
  const ctx = await browser.newContext(ctxOpts);
  const p = await ctx.newPage();
  const errors = [], geo = [];
  p.on('pageerror', e => errors.push(e.message));
  p.on('request', r => { if (/geoapify\.com/.test(r.url())) geo.push(r.url()); });

  // stub: what the next Geoapify call gets
  let stub = { data: FX('jakkalsvlei') };
  await p.route(/api\.geoapify\.com/, async route => {
    const s = stub;
    if (s.real) return route.continue();
    if (s.abort) return route.abort('failed');
    if (s.delay) await new Promise(r => setTimeout(r, s.delay));
    try {
      await route.fulfill({ status: s.status || 200, contentType: 'application/json',
                            headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(s.data || {}) });
    } catch (e) { /* the page aborted it */ }
  });

  await p.goto(BASE, { waitUntil: 'load' });
  await p.waitForTimeout(1200);
  await p.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
  await p.reload({ waitUntil: 'load' }); await p.waitForTimeout(1000);
  await ensurePanel(p);
  check(!(await hidden(p, '#ac-addr')), 'street-address box shows');
  await p.evaluate(() => document.getElementById('ac-addr-privacy').scrollIntoView({ block: 'center' }));
  check(await visible(p, '#ac-addr-privacy'), 'privacy line is visible before Search');
  check((await text(p, '#ac-addr-privacy')).includes(PRIVACY), 'privacy line has the approved words');
  check(await p.evaluate(() => !!document.querySelector('#ac-addr-privacy a[href="https://www.geoapify.com/privacy-policy/"]')),
        'privacy line links to Geoapify\u2019s privacy policy');
  check(await visible(p, '#ac-addr-attrib') && /Powered by Geoapify/.test(await text(p, '#ac-addr-attrib')) &&
        /OpenStreetMap/.test(await text(p, '#ac-addr-attrib')), 'Powered by Geoapify and OpenStreetMap credit visible');

  await p.fill('#ac-addr-q', 'Jakkalsvlei Avenue, Bonteheu'); await p.waitForTimeout(600);
  check(geo.length === 0, 'typing alone sends nothing');
  check(!(await visible(p, '#ac-q')) || (await text(p, '#ac-q-help')).includes('Nothing typed here is sent'), 'place-list box says it stays on the device');

  // matches: none picked for you
  stub = { data: FX('jakkalsvlei') };
  await search(p, 'Jakkalsvlei Avenue, Bonteheuwel');
  await p.waitForSelector('#ac-addr-matches .ac-match');
  check(geo.length === 1 && /filter=countrycode(:|%3A)za/.test(geo[0]) && /limit=5/.test(geo[0]), 'one request on Search, South Africa only, limit 5');
  const ms = await p.$$eval('#ac-addr-matches .ac-match', els => els.map(e => e.innerText.replace(/\n/g, ' | ')));
  check(ms.length === 2 && ms.every(m => /street/.test(m) && /Bonteheuwel/.test(m)), `Jakkalsvlei shows its 2 street matches: ${JSON.stringify(ms)}`);
  check(await hidden(p, '#ac-set'), 'no centre set until a match is picked');
  await p.click('#ac-addr-matches .ac-match'); await p.waitForTimeout(1300);
  await ensurePanel(p);
  check((await text(p, '#ac-centre-name')) === 'Jakkalsvlei Avenue', 'picked street becomes the centre');
  check(/^A point on this street/.test(await text(p, '#ac-centre-kind')), 'street centre is labelled a point on the street');
  check(/within 2 km/.test(await text(p, '#ac-summary')), `count shows: "${await text(p, '#ac-summary')}"`);

  // whole suburb fallback
  stub = { data: FX('vuyani') };
  await search(p, 'Vuyani Street, Gugulethu');
  await p.waitForSelector('#ac-addr-matches .ac-match');
  const v = await text(p, '#ac-addr-matches .ac-match');
  check(/whole suburb/.test(v) && /Exact spot not found/.test(v), `suburb fallback is flagged: "${v}"`);
  await p.click('#ac-addr-matches .ac-match'); await p.waitForTimeout(1200); await ensurePanel(p);
  check((await text(p, '#ac-centre-name')) === 'Guguletu (whole suburb)', 'circle labelled as a whole suburb');

  // wrong-town matches are flagged
  stub = { data: FX('kgaka_ipelegeng') };
  await search(p, 'Kgaka Street, Ipelegeng');
  await p.waitForSelector('#ac-addr-matches .ac-match');
  const kg = await p.$$eval('#ac-addr-matches .ac-match', els => els.map(e => e.textContent));
  check(kg.length === 5 && kg.every(t => /Does not mention \u201cipelegeng\u201d/.test(t)), 'Kgaka matches elsewhere are flagged');

  // not found
  stub = { data: FX('garbage') };
  await search(p, 'asdfqwerty zzz');
  await p.waitForFunction(() => /No address found/.test(document.getElementById('ac-addr-status').textContent));
  check(/place it on the map/.test(await text(p, '#ac-addr-status')), 'not found offers placing on the map');

  // HTML in a label stays text
  stub = { data: { results: [{ result_type: 'building', lat: -33.95, lon: 18.55, country_code: 'za',
                               address_line1: '<img src=x onerror="window.__xss=1">Evil', address_line2: 'Cape Town' }] } };
  await search(p, 'evil street, cape town');
  await p.waitForSelector('#ac-addr-matches .ac-match');
  check(await p.evaluate(() => !document.querySelector('#ac-addr-matches img') && !window.__xss), 'service labels are inserted as text, not HTML');

  // errors and timeout
  stub = { status: 401, data: { statusCode: 401, error: 'Unauthorized', message: 'Not allowed' } };
  await search(p, 'Long Street, Cape Town');
  await p.waitForFunction(() => /respond/.test(document.getElementById('ac-addr-status').textContent));
  check(FAIL_TEXT.test(await text(p, '#ac-addr-status')), 'refused key shows the fallback');
  stub = { abort: true };
  await search(p, 'Long Street, Cape Town');
  await p.waitForFunction(() => /respond/.test(document.getElementById('ac-addr-status').textContent));
  check(FAIL_TEXT.test(await text(p, '#ac-addr-status')), 'network error shows the fallback');
  stub = { delay: 9000, data: FX('jakkalsvlei') };
  const t0 = Date.now();
  await search(p, 'Long Street, Cape Town');
  await p.waitForTimeout(300);
  check(!(await hidden(p, '#ac-addr-cancel')) && (await text(p, '#ac-addr-status')) === 'Searching\u2026', 'Searching\u2026 with a Cancel button');
  await p.waitForFunction(() => /respond/.test(document.getElementById('ac-addr-status').textContent), null, { timeout: 9000 });
  const took = Date.now() - t0;
  check(took >= 5500 && took < 8000 && FAIL_TEXT.test(await text(p, '#ac-addr-status')), `timeout after about 6 s shows the fallback (${took} ms)`);
  await p.waitForTimeout(3500);
  check(await p.$$eval('#ac-addr-matches .ac-match', els => els.length) === 0, 'the late answer is ignored');
  // cancel
  stub = { delay: 3000, data: FX('jakkalsvlei') };
  await search(p, 'Long Street, Cape Town'); await p.waitForTimeout(300);
  await p.click('#ac-addr-cancel'); await p.waitForTimeout(3500);
  check(/cancelled/.test(await text(p, '#ac-addr-status')) &&
        await p.$$eval('#ac-addr-matches .ac-match', els => els.length) === 0, 'Cancel stops the search');
  // place-list search and map placing still work
  await p.fill('#ac-q', 'Wesbank'); await p.press('#ac-q', 'Enter');
  await p.waitForSelector('#ac-matches .ac-match');
  check((await p.$$eval('#ac-matches .ac-match', els => els.length)) >= 3, 'built-in place list still works');
  await p.click('#ac-place-btn'); await p.waitForTimeout(500);
  await p.click('#ac-place-use'); await p.waitForTimeout(1200);
  check((await text(p, '#ac-centre-name')) === 'Your map point', 'Place on the map still works');
  // picked address can be remembered
  await ensurePanel(p);
  stub = { data: FX('jakkalsvlei') };
  await search(p, 'Jakkalsvlei Avenue, Bonteheuwel');
  await p.waitForSelector('#ac-addr-matches .ac-match');
  await p.click('#ac-addr-matches .ac-match'); await p.waitForTimeout(1200); await ensurePanel(p);
  await p.click('#ac-remember'); await p.waitForTimeout(100);
  await p.reload({ waitUntil: 'load' }); await p.waitForTimeout(1200);
  await ensurePanel(p);
  check((await text(p, '#ac-centre-name')) === 'Jakkalsvlei Avenue', 'a picked address is remembered after a reload');
  await p.click('#ac-forget');

  if (REAL && name.startsWith('desktop')) {
    console.log('\n-- real Geoapify answers (from ' + BASE + ')');
    stub = { real: true };
    for (const q of ADDRESSES) {
      await search(p, q);
      await p.waitForFunction(() => !/Searching/.test(document.getElementById('ac-addr-status').textContent), null, { timeout: 10000 });
      const st = await text(p, '#ac-addr-status');
      const rows = await p.$$eval('#ac-addr-matches .ac-match', els => els.map(e => e.innerText.replace(/\n/g, ' | ')));
      check(rows.length > 0 ? rows.length <= 5 : /No address found/.test(st), `${q}: ${rows.length ? rows.length + ' match(es)' : st}`);
      rows.forEach(r => console.log('       - ' + r));
    }
  }

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
