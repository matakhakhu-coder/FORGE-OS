/* ═══════════════════════════════════════════════════════════════════════════
   ZA-DIVERGENT — Analyst Workspace (localStorage-backed)
   Premium feature: custom evidence notes, entity auto-linking, JSON export
   ═══════════════════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  var STORAGE_PREFIX = 'zad_workspace_';
  var MAX_BYTES = 50000;

  function getSlug() {
    var path = window.location.pathname.replace(/\.html$/, '').replace(/^.*\//, '');
    return path || 'index';
  }

  function getStorageKey() {
    return STORAGE_PREFIX + getSlug();
  }

  function loadNotes() {
    try {
      var raw = localStorage.getItem(getStorageKey());
      return raw ? JSON.parse(raw) : [];
    } catch(e) { return []; }
  }

  function saveNotes(notes) {
    try {
      localStorage.setItem(getStorageKey(), JSON.stringify(notes));
    } catch(e) {}
  }

  function byteCount(s) {
    return new Blob([s]).size;
  }

  /* ── Entity auto-linker ──────────────────────────────────────────────── */
  var entityIndex = null;

  function loadEntityIndex() {
    if (entityIndex) return;
    var el = document.getElementById('entity-index-data');
    if (el) {
      try { entityIndex = JSON.parse(el.textContent); } catch(e) { entityIndex = []; }
    }
  }

  function autoLinkEntities(text) {
    loadEntityIndex();
    if (!entityIndex || !entityIndex.length) return text;
    var result = text;
    entityIndex.forEach(function(ent) {
      var re = new RegExp('\\b' + ent.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'gi');
      result = result.replace(re, '<a href="' + ent.url + '" class="entity-autolink">' + ent.name + '</a>');
    });
    return result;
  }

  /* ── Render notes in drawer ──────────────────────────────────────────── */
  function renderDrawer() {
    var container = document.getElementById('drawer-notes');
    if (!container) return;
    var notes = loadNotes();
    if (!notes.length) {
      container.innerHTML = '<p style="color:#3d5060;font-size:0.75rem;text-align:center;padding:2rem 0;">No workspace notes yet.</p>';
      return;
    }
    var html = '';
    notes.forEach(function(n, i) {
      var linkedBody = autoLinkEntities(n.body || '');
      html += '<div class="workspace-note-card">'
        + '<div class="workspace-note-card__title">' + (n.title || 'Untitled') + '</div>'
        + '<div class="workspace-note-card__body">' + linkedBody + '</div>'
        + (n.source ? '<div class="workspace-note-card__meta">Source: <a href="' + n.source + '" target="_blank" rel="noopener">' + n.source.substring(0,40) + '</a></div>' : '')
        + '<div class="workspace-note-card__meta">' + (n.type || 'note') + ' · ' + (n.created || '') + '</div>'
        + '<button onclick="window.__wsRemoveNote(' + i + ')" style="background:none;border:none;color:#c0392b;font-size:0.65rem;cursor:pointer;margin-top:4px;">Remove</button>'
        + '</div>';
    });
    container.innerHTML = html;

    var badge = document.getElementById('drawer-count');
    if (badge) badge.textContent = notes.length;
  }

  window.__wsRemoveNote = function(idx) {
    var notes = loadNotes();
    notes.splice(idx, 1);
    saveNotes(notes);
    renderDrawer();
  };

  /* ── Modal form submission ───────────────────────────────────────────── */
  function handleSubmit(e) {
    e.preventDefault();
    var title = document.getElementById('ws-title');
    var body = document.getElementById('ws-body');
    var source = document.getElementById('ws-source');
    var tags = document.getElementById('ws-tags');

    if (!title || !body) return;
    if (!title.value.trim()) { title.focus(); return; }

    var note = {
      type: document.querySelector('.workspace-tab.active') ? document.querySelector('.workspace-tab.active').textContent.trim().toLowerCase() : 'note',
      title: title.value.trim(),
      body: body.value.trim(),
      source: source ? source.value.trim() : '',
      tags: tags ? tags.value.split(',').map(function(t){return t.trim()}).filter(Boolean) : [],
      slug: getSlug(),
      created: new Date().toISOString(),
    };

    var notes = loadNotes();
    notes.push(note);
    saveNotes(notes);

    title.value = '';
    body.value = '';
    if (source) source.value = '';
    if (tags) tags.value = '';
    updateByteCounter();
    renderDrawer();

    var overlay = document.querySelector('.workspace-overlay');
    if (overlay) overlay.classList.remove('active');
  }

  /* ── Byte counter ────────────────────────────────────────────────────── */
  function updateByteCounter() {
    var body = document.getElementById('ws-body');
    var counter = document.getElementById('ws-bytes');
    if (!body || !counter) return;
    var bytes = byteCount(body.value);
    counter.textContent = bytes.toLocaleString() + ' / ' + MAX_BYTES.toLocaleString() + ' bytes';
    counter.style.color = bytes > MAX_BYTES ? '#c0392b' : '#3d5060';
  }

  /* ── Export workspace ────────────────────────────────────────────────── */
  window.__wsExport = function() {
    var notes = loadNotes();
    if (!notes.length) return;
    var payload = {
      export_version: '1.0',
      exported_at: new Date().toISOString(),
      slug: getSlug(),
      notes: notes,
    };
    var blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'workspace_' + getSlug() + '_' + Date.now() + '.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  /* ── Toggle drawer ───────────────────────────────────────────────────── */
  window.__wsToggleDrawer = function() {
    var drawer = document.querySelector('.workspace-drawer');
    if (drawer) drawer.classList.toggle('open');
  };

  /* ── Toggle modal ────────────────────────────────────────────────────── */
  window.__wsToggleModal = function() {
    var overlay = document.querySelector('.workspace-overlay');
    if (overlay) overlay.classList.toggle('active');
  };

  /* ── Tab switching ───────────────────────────────────────────────────── */
  window.__wsTabSwitch = function(btn) {
    document.querySelectorAll('.workspace-tab').forEach(function(t) { t.classList.remove('active'); });
    btn.classList.add('active');
  };

  /* ── Init ────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', function() {
    var form = document.getElementById('ws-form');
    if (form) form.addEventListener('submit', handleSubmit);

    var bodyEl = document.getElementById('ws-body');
    if (bodyEl) bodyEl.addEventListener('input', updateByteCounter);

    renderDrawer();
  });
})();
