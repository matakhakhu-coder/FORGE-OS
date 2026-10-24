/* Header interactions reuse existing panel/search/settings handlers. */
(function () {
  'use strict';
  if (!document.body.classList.contains('signal-map-page')) return;
  var header = document.querySelector('.site-header');
  var inner = document.querySelector('.site-header__inner');
  var nav = document.querySelector('.site-nav');
  var menu = document.querySelector('.nav-toggle');
  var sidebar = document.querySelector('.md-sidebar');
  var search = document.querySelector('.site-search');
  var input = document.getElementById('site-search');
  function trigger(id, text, label, controls, cls) {
    var b = document.createElement('button');
    b.id = id; b.type = 'button'; b.textContent = text;
    b.className = 'map-header-control ' + cls;
    b.setAttribute('aria-label', label); b.setAttribute('aria-expanded', 'false');
    b.setAttribute('aria-controls', controls);
    inner.insertBefore(b, menu); return b;
  }
  sidebar.id = 'map-tools-menu'; sidebar.setAttribute('aria-label', 'Map tools');
  search.id = 'map-search-box'; nav.id = 'map-general-menu';
  var tools = trigger('map-tools-toggle', 'Tools', 'Tools', sidebar.id, 'map-tools-trigger');
  var searchButton = trigger('map-search-toggle', '⌕', 'Search', search.id, 'map-search-trigger');
  menu.removeAttribute('onclick'); menu.setAttribute('aria-controls', nav.id);
  var extras = document.createElement('div'); extras.className = 'map-menu-extras';
  ['.live-badge', '#data-age', '#settings-toggle'].forEach(function (s) {
    var el = document.querySelector(s); if (el) extras.appendChild(el);
  });
  var support = document.querySelector('.site-footer .membership-btn');
  if (support) extras.appendChild(support);
  nav.appendChild(extras);
  document.querySelector('.site-logo').setAttribute('aria-label', 'FORGE Map home');
  input.setAttribute('aria-label', 'Search signals, analysis and entities');
  var settings = document.getElementById('settings-toggle');
  if (settings) settings.setAttribute('aria-label', 'Settings');
  function closeAll() {
    tools.setAttribute('aria-expanded', 'false'); sidebar.classList.remove('map-tools-open');
    menu.setAttribute('aria-expanded', 'false'); nav.classList.remove('site-nav--open');
    searchButton.setAttribute('aria-expanded', 'false'); search.classList.remove('map-search-open');
    var results = document.getElementById('search-results'); if (results) results.style.display = 'none';
  }
  function toggle(button, element, cls) {
    var wasOpen = button.getAttribute('aria-expanded') === 'true'; closeAll();
    if (!wasOpen) { button.setAttribute('aria-expanded', 'true'); element.classList.add(cls); }
    return !wasOpen;
  }
  tools.addEventListener('click', function () { toggle(tools, sidebar, 'map-tools-open'); });
  menu.addEventListener('click', function () { toggle(menu, nav, 'site-nav--open'); });
  searchButton.addEventListener('click', function () { if (toggle(searchButton, search, 'map-search-open')) input.focus(); });
  sidebar.addEventListener('click', function (e) {
    if (e.target.closest('[data-panel]') && window.matchMedia('(max-width: 768px)').matches) { closeAll(); tools.focus(); }
  });
  nav.addEventListener('click', function (e) { if (e.target.closest('a, #settings-toggle')) closeAll(); });
  document.addEventListener('click', function (e) { if (!header.contains(e.target) && !sidebar.contains(e.target)) closeAll(); });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var active = [tools, menu, searchButton].find(function (b) { return b.getAttribute('aria-expanded') === 'true'; });
    if (active) { closeAll(); active.focus(); }
  });
  function measure() { document.body.style.setProperty('--map-header', header.getBoundingClientRect().height + 'px'); }
  new ResizeObserver(measure).observe(header); measure();
  window.matchMedia('(max-width: 768px)').addEventListener('change', closeAll);
})();
