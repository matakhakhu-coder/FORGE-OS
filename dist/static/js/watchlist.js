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

  function renderWatchlistPage() {
    var container = document.getElementById('wl-list');
    if (!container) return;

    var wl = getWatchlist();
    var all = (wl.actors || []).concat(wl.signals || []);
    all.sort(function (a, b) { return (b.addedAt || '').localeCompare(a.addedAt || ''); });

    if (all.length === 0) {
      container.innerHTML = '<div class="wl-empty">No bookmarked items yet. Click the star on any signal or entity to add it here.</div>';
      return;
    }

    var html = '';
    all.forEach(function (item) {
      var date = item.addedAt ? new Date(item.addedAt).toLocaleDateString() : '';
      html += '<a href="' + (item.url || '#') + '" class="wl-item">' +
        '<span class="wl-item__star">★</span>' +
        '<div class="wl-item__body">' +
          '<div class="wl-item__title">' + (item.title || item.id) + '</div>' +
          '<div class="wl-item__meta">' + (item.type || '') + ' · saved ' + date + '</div>' +
        '</div>' +
      '</a>';
    });
    container.innerHTML = html;
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
  };
})();
