(function () {
  'use strict';

  var WL_KEY = 'za_divergent_watchlist';

  // 18 Oct 2026: every old signal and entity was removed from the site, so
  // anything starred before this moment points at a page that no longer
  // exists. Those entries are dropped the next time the watchlist is read.
  var WL_RESET_AT = '2026-10-18T16:30:00.000Z';

  function getWatchlist() {
    var wl;
    try { wl = JSON.parse(localStorage.getItem(WL_KEY)) || { signals: [], actors: [] }; }
    catch (e) { return { signals: [], actors: [] }; }
    var changed = false;
    ['signals', 'actors'].forEach(function (type) {
      var list = wl[type] || [];
      var kept = list.filter(function (item) {
        return (item.addedAt || '') >= WL_RESET_AT;
      });
      if (kept.length !== list.length) changed = true;
      wl[type] = kept;
    });
    if (changed) {
      try { localStorage.setItem(WL_KEY, JSON.stringify(wl)); } catch (e) { /* ignore */ }
    }
    return wl;
  }

  function saveWatchlist(wl) {
    localStorage.setItem(WL_KEY, JSON.stringify(wl));
  }

  function isWatchlisted(type, id) {
    var list = getWatchlist()[type] || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return true;
    }
    return false;
  }

  function toggleItem(type, item) {
    var wl = getWatchlist();
    var list = wl[type] || [];
    var idx = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === item.id) { idx = i; break; }
    }
    if (idx > -1) {
      list.splice(idx, 1);
    } else {
      list.push({
        id: item.id,
        title: item.title,
        type: item.type || type,
        url: item.url,
        addedAt: new Date().toISOString()
      });
    }
    wl[type] = list;
    saveWatchlist(wl);
    return idx === -1;
  }

  function hydrateToggles() {
    document.querySelectorAll('[data-wl-id]').forEach(function (btn) {
      var type = btn.dataset.wlType || 'signals';
      var id = btn.dataset.wlId;
      var active = isWatchlisted(type, id);
      btn.classList.toggle('wl-active', active);
      btn.textContent = active ? '★' : '☆';

      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var added = toggleItem(type, {
          id: btn.dataset.wlId,
          title: btn.dataset.wlTitle || '',
          type: btn.dataset.wlType || 'signals',
          url: btn.dataset.wlUrl || '',
        });
        btn.classList.toggle('wl-active', added);
        btn.textContent = added ? '★' : '☆';
      });
    });
  }

  // Bookmarks come from localStorage and from names in the data, so nothing
  // from them is ever parsed as markup: every value goes in as plain text
  // (textContent), and a link only keeps a relative or http(s) address.
  function safeHref(url) {
    var raw = String(url == null ? '' : url).trim();
    if (!raw) return '#';
    // Browsers ignore tabs, newlines and control characters inside a scheme
    // ("java\tscript:"), so check the address with those removed.
    var bare = raw.replace(/[\u0000-\u0020\u007f]+/g, '');
    if (/^[a-z][a-z0-9+.\-]*:/i.test(bare) && !/^https?:/i.test(bare)) return '#';
    return raw;
  }

  // A saved pin is stored under its full signal id. Its link is rebuilt from
  // that id (the map's own ?signal= address), never taken from storage, and
  // only when the id looks like a signal id.
  var SIGNAL_ID_RE = /^[0-9a-f-]{8,36}$/i;
  function pinShortId(id) {
    var s = String(id == null ? '' : id).trim();
    return SIGNAL_ID_RE.test(s) ? s.slice(0, 8).toLowerCase() : '';
  }
  function pinUrl(id) {
    var sid = pinShortId(id);
    return sid ? 'index.html?signal=' + sid : '';
  }

  var TYPE_LABEL = { signals: 'Pin', actors: 'Entity' };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function renderWatchlistPage() {
    var container = document.getElementById('wl-list');
    if (!container) return;

    var wl = getWatchlist();
    var all = (wl.actors || []).concat(wl.signals || []);
    all.sort(function (a, b) { return String(b.addedAt || '').localeCompare(String(a.addedAt || '')); });

    container.textContent = '';

    if (all.length === 0) {
      container.appendChild(el('div', 'wl-empty',
        'No bookmarked items yet. Tap the star on any pin or entity to add it here.'));
      return;
    }

    all.forEach(function (item) {
      var date = item.addedAt ? new Date(item.addedAt).toLocaleDateString() : '';
      var link = el('a', 'wl-item');
      var sid = item.type === 'signals' ? pinShortId(item.id) : '';
      if (sid) {
        link.setAttribute('href', pinUrl(item.id));
        // The map opens this pin's drawer in place instead of reloading.
        link.setAttribute('data-wl-signal', sid);
      } else {
        link.setAttribute('href', safeHref(item.url));
      }
      link.appendChild(el('span', 'wl-item__star', '★'));
      var body = el('div', 'wl-item__body');
      body.appendChild(el('div', 'wl-item__title', item.title || item.id || ''));
      var typeLabel = Object.prototype.hasOwnProperty.call(TYPE_LABEL, item.type) ? TYPE_LABEL[item.type] : (item.type || '');
      body.appendChild(el('div', 'wl-item__meta', typeLabel + ' · saved ' + date));
      link.appendChild(body);
      container.appendChild(link);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    hydrateToggles();
    renderWatchlistPage();
  });

  window.ZADWatchlist = {
    getWatchlist: getWatchlist,
    isWatchlisted: isWatchlisted,
    toggleItem: toggleItem,
    renderWatchlistPage: renderWatchlistPage,
    safeHref: safeHref,
    pinUrl: pinUrl,
    pinShortId: pinShortId,
  };
})();
