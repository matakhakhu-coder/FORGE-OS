// Pin bookmarks: a pin is saved in the Watchlist under its full signal id and
// comes back as a link to its own ?signal= address. Entity bookmarks unchanged.
// Run: node --test tests/js/pin_bookmarks.test.js   (also run by tests/test_pin_bookmarks.py)
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'publisher', 'static', 'js', 'watchlist.js'), 'utf8');

function makeEl(tag) {
  const node = {
    tagName: tag.toUpperCase(), className: '', attrs: {}, children: [], _text: '',
    dataset: {}, listeners: {},
    classList: {
      _s: new Set(),
      toggle(c, on) { if (on === undefined ? !this._s.has(c) : on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  };
  Object.defineProperty(node, 'textContent', {
    get() { return this._text + this.children.map((c) => c.textContent).join(''); },
    set(v) { this._text = String(v); this.children = []; },
  });
  for (const prop of ['innerHTML', 'outerHTML']) {
    Object.defineProperty(node, prop, {
      get() { throw new Error(prop + ' read'); },
      set() { throw new Error(prop + ' used'); },
    });
  }
  return node;
}

// One "page load": runs watchlist.js against a shared localStorage object, so a
// second load with the same store is a reload of the page.
function load(store, toggles = []) {
  const container = makeEl('div');
  let ready = null;
  const ctx = {
    window: {},
    document: {
      getElementById: (id) => (id === 'wl-list' ? container : null),
      createElement: makeEl,
      querySelectorAll: (sel) => (sel === '[data-wl-id]' ? toggles : []),
      addEventListener(t, fn) { if (t === 'DOMContentLoaded') ready = fn; },
    },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    JSON, Date, String, Object,
  };
  vm.runInNewContext(SRC, ctx);
  if (ready) ready();
  return { api: ctx.window.ZADWatchlist, container };
}

function walk(node, out = []) {
  out.push(node);
  node.children.forEach((c) => walk(c, out));
  return out;
}

const PIN_ID = 'e0fa3712-5b1c-4c2e-9a77-1d2f3a4b5c6d';
function pinBookmark(api, id = PIN_ID, title = 'Firethorne shooting') {
  return { id, title, type: 'signals', url: api.pinUrl(id) };
}

test('a pin is added under its full signal id with its ?signal= address', () => {
  const store = {};
  const { api } = load(store);
  assert.equal(api.isWatchlisted('signals', PIN_ID), false);
  assert.equal(api.toggleItem('signals', pinBookmark(api)), true);
  const saved = JSON.parse(store.za_divergent_watchlist);
  assert.equal(saved.signals.length, 1);
  assert.equal(saved.signals[0].id, PIN_ID);             // full id, not a map index
  assert.equal(saved.signals[0].type, 'signals');
  assert.equal(saved.signals[0].url, 'index.html?signal=e0fa3712');
  assert.equal(saved.signals[0].title, 'Firethorne shooting');
  assert.match(saved.signals[0].addedAt, /^\d{4}-\d\d-\d\dT/);
  assert.deepEqual(saved.actors, []);
});

test('the star removes the pin again', () => {
  const store = {};
  const { api } = load(store);
  api.toggleItem('signals', pinBookmark(api));
  assert.equal(api.toggleItem('signals', pinBookmark(api)), false);
  assert.equal(api.isWatchlisted('signals', PIN_ID), false);
  assert.deepEqual(JSON.parse(store.za_divergent_watchlist).signals, []);
});

test('a saved pin is still starred after a reload', () => {
  const store = {};
  load(store).api.toggleItem('signals', pinBookmark(load(store).api));
  const reloaded = load(store).api;
  assert.equal(reloaded.isWatchlisted('signals', PIN_ID), true);
  assert.equal(reloaded.isWatchlisted('signals', 'e0fa3712'), false);   // matched on the full id
  assert.equal(reloaded.isWatchlisted('actors', PIN_ID), false);
});

test('the Watchlist lists a saved pin as a link that reopens it', () => {
  const store = {};
  const { api } = load(store);
  api.toggleItem('signals', pinBookmark(api));
  const { api: page, container } = load(store);
  page.renderWatchlistPage();
  const link = walk(container).find((n) => n.tagName === 'A');
  assert.equal(link.getAttribute('href'), 'index.html?signal=e0fa3712');
  assert.equal(link.getAttribute('data-wl-signal'), 'e0fa3712');
  assert.equal(walk(container).find((n) => n.className === 'wl-item__title').textContent, 'Firethorne shooting');
  assert.match(walk(container).find((n) => n.className === 'wl-item__meta').textContent, /^Pin · saved /);
});

test('a pin link is rebuilt from its id, never taken from storage', () => {
  const store = { za_divergent_watchlist: JSON.stringify({ actors: [], signals: [
    { id: PIN_ID, title: '<img src=x onerror=alert(1)>', type: 'signals', url: 'javascript:alert(1)', addedAt: '2026-10-22T15:00:00.000Z' },
    { id: 'not a signal" onclick="x', title: 'Bad id', type: 'signals', url: 'javascript:alert(2)', addedAt: '2026-10-22T14:00:00.000Z' },
  ] }) };
  const { api, container } = load(store);
  api.renderWatchlistPage();
  const links = walk(container).filter((n) => n.tagName === 'A');
  assert.equal(links[0].getAttribute('href'), 'index.html?signal=e0fa3712');
  // A bad id gets no reopen hook, and its stored link is still made safe.
  assert.equal(links[1].getAttribute('href'), '#');
  assert.equal(links[1].getAttribute('data-wl-signal'), null);
  assert.ok(!walk(container).some((n) => ['IMG', 'SCRIPT'].includes(n.tagName)));
  assert.equal(api.pinUrl('javascript:alert(1)'), '');
  assert.equal(api.pinUrl(''), '');
  assert.equal(api.pinShortId('E0FA3712-5B1C'), 'e0fa3712');
});

test('entities still save, list and link as before, beside pins', () => {
  const store = {};
  const btn = makeEl('button');
  btn.dataset = { wlId: 'actor-1', wlType: 'actors', wlTitle: 'SAPS Western Cape', wlUrl: 'entities/saps-wc.html' };
  load(store, [btn]);
  assert.equal(btn.textContent, '☆');
  btn.listeners.click[0]({ preventDefault() {}, stopPropagation() {} });
  assert.equal(btn.textContent, '★');
  assert.ok(btn.classList.contains('wl-active'));

  const { api } = load(store);
  api.toggleItem('signals', pinBookmark(api));
  const btn2 = makeEl('button');
  btn2.dataset = { ...btn.dataset };
  const { api: page, container } = load(store, [btn2]);
  assert.equal(btn2.textContent, '★');                     // entity star restored on reload
  assert.equal(page.isWatchlisted('actors', 'actor-1'), true);
  page.renderWatchlistPage();
  const items = walk(container).filter((n) => n.tagName === 'A');
  assert.equal(items.length, 2);
  const entity = items.find((n) => n.getAttribute('href') === 'entities/saps-wc.html');
  assert.ok(entity);
  assert.equal(entity.getAttribute('data-wl-signal'), null);
  assert.match(entity.textContent, /SAPS Western Cape.*Entity · saved /);
  const saved = JSON.parse(store.za_divergent_watchlist);
  assert.equal(saved.actors.length, 1);
  assert.equal(saved.signals.length, 1);
});

test('the empty message names pins and entities', () => {
  const { api, container } = load({});
  api.renderWatchlistPage();
  assert.equal(container.textContent,
    'No bookmarked items yet. Tap the star on any pin or entity to add it here.');
});
