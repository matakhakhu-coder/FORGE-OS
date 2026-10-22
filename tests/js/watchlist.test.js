// Watchlist rendering: bookmarked names and links are plain text, never markup.
// Run: node --test tests/js/watchlist.test.js   (also run by tests/test_watchlist.py)
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'publisher', 'static', 'js', 'watchlist.js'), 'utf8');

// A tiny DOM: elements keep text and children as data. Any use of innerHTML,
// outerHTML or insertAdjacentHTML fails the test, because that is how a name
// would be parsed as markup.
function makeEl(tag) {
  const node = {
    tagName: tag.toUpperCase(), className: '', attrs: {}, children: [], _text: '',
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    insertAdjacentHTML() { throw new Error('insertAdjacentHTML used'); },
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

function load(stored) {
  const container = makeEl('div');
  const store = { za_divergent_watchlist: JSON.stringify(stored) };
  const ctx = {
    window: {},
    document: {
      getElementById: (id) => (id === 'wl-list' ? container : null),
      createElement: makeEl,
      querySelectorAll: () => [],
      addEventListener() {},
    },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    JSON, Date, String,
  };
  vm.runInNewContext(SRC, ctx);
  return { api: ctx.window.ZADWatchlist, container };
}

function walk(node, out = []) {
  out.push(node);
  node.children.forEach((c) => walk(c, out));
  return out;
}

const NOW = '2026-10-22T15:00:00.000Z';
const EVIL = '<img src=x onerror="alert(1)"><script>alert(2)</script>';

test('a name with markup is shown as text and makes no extra elements', () => {
  const { api, container } = load({
    actors: [{ id: 'a1', title: EVIL, type: '<b>actors</b>', url: 'entities/a1.html', addedAt: NOW }],
    signals: [],
  });
  api.renderWatchlistPage();
  const nodes = walk(container);
  const tags = nodes.map((n) => n.tagName).sort();
  assert.deepEqual(tags, ['A', 'DIV', 'DIV', 'DIV', 'DIV', 'SPAN']);
  assert.ok(!nodes.some((n) => ['IMG', 'SCRIPT', 'B'].includes(n.tagName)));
  const title = nodes.find((n) => n.className === 'wl-item__title');
  assert.equal(title.textContent, EVIL);
  const meta = nodes.find((n) => n.className === 'wl-item__meta');
  assert.ok(meta.textContent.startsWith('<b>actors</b> · saved '));
  const link = nodes.find((n) => n.tagName === 'A');
  assert.equal(link.getAttribute('href'), 'entities/a1.html');
  assert.equal(link.className, 'wl-item');
});

test('a link cannot break out of its href or run script', () => {
  const { api, container } = load({
    actors: [
      { id: 'q', title: 'Quote', url: 'x.html" onmouseover="alert(1)', addedAt: NOW },
      { id: 'j', title: 'JS', url: 'javascript:alert(1)', addedAt: NOW },
      { id: 't', title: 'Tab', url: ' java\tscript:alert(1)', addedAt: NOW },
      { id: 'd', title: 'Data', url: 'DATA:text/html,<script>alert(1)</script>', addedAt: NOW },
      { id: 'v', title: 'VBS', url: 'vbscript:msgbox(1)', addedAt: NOW },
    ],
    signals: [],
  });
  api.renderWatchlistPage();
  const hrefs = walk(container).filter((n) => n.tagName === 'A').map((n) => n.getAttribute('href'));
  // The quote stays inside the attribute value: setAttribute cannot add attributes.
  assert.ok(hrefs.includes('x.html" onmouseover="alert(1)'));
  assert.equal(hrefs.filter((h) => h === '#').length, 4);
  for (const n of walk(container)) {
    assert.deepEqual(Object.keys(n.attrs).filter((k) => k !== 'href'), []);
  }
});

test('safe links are kept as they are', () => {
  const { api } = load({ actors: [], signals: [] });
  assert.equal(api.safeHref('entities/cape-town.html'), 'entities/cape-town.html');
  assert.equal(api.safeHref('../index.html#s=abc'), '../index.html#s=abc');
  assert.equal(api.safeHref('https://example.org/a'), 'https://example.org/a');
  assert.equal(api.safeHref(''), '#');
  assert.equal(api.safeHref(undefined), '#');
});

test('normal bookmarks still list newest first with title, type and date', () => {
  const { api, container } = load({
    actors: [{ id: 'old', title: 'Older', type: 'actors', url: 'entities/old.html', addedAt: '2026-10-19T10:00:00.000Z' }],
    signals: [{ id: 'new', title: '', type: 'signals', url: 'index.html', addedAt: NOW }],
  });
  api.renderWatchlistPage();
  const titles = walk(container).filter((n) => n.className === 'wl-item__title').map((n) => n.textContent);
  assert.deepEqual(titles, ['new', 'Older']);   // an empty title falls back to the id
  const stars = walk(container).filter((n) => n.className === 'wl-item__star').map((n) => n.textContent);
  assert.deepEqual(stars, ['★', '★']);
});

test('the empty list shows the empty message as text', () => {
  const { api, container } = load({ actors: [], signals: [] });
  api.renderWatchlistPage();
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].className, 'wl-empty');
  assert.match(container.textContent, /^No bookmarked items yet\./);
});

test('the script never builds markup from strings', () => {
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(SRC));
});
