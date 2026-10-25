"""Page snippets for earlier_months_check.py (Batch 9)."""

SHOWN_JS = "() => window.ZADMapTime.shown()"
VIEW_JS = "() => window.ZADMonth.view()"

STILL_JS = r"""() => {
  const icons = [...document.querySelectorAll('#map .leaflet-marker-icon')];
  const vis = sel => icons.flatMap(i => [...i.querySelectorAll(sel)])
    .filter(e => getComputedStyle(e).display !== 'none').length;
  const anims = document.getAnimations().filter(a => !(window.CSSTransition && a instanceof CSSTransition) && a.effect && a.effect.target && a.effect.target.closest
    && a.effect.target.closest('.leaflet-marker-icon, .marker-cluster')).length;
  const dot = document.querySelector('#map .leaflet-marker-icon .sig-dot');
  const cl = document.querySelector('#map .marker-cluster div');
  const tiles = document.querySelector('#map .leaflet-tile-pane');
  const frame = getComputedStyle(document.querySelector('.md-shell'), '::after');
  return { rings: vis('.sig-ring'), pulses: vis('.sig-pulse'), newDots: vis('.mc-new'), anims,
    dotBg: dot ? getComputedStyle(dot).backgroundColor : null,
    clusterBg: cl ? getComputedStyle(cl).backgroundColor : null,
    tileFilter: tiles ? getComputedStyle(tiles).filter : null,
    frame: frame.borderTopStyle };
}"""

BOX_JS = r"""sel => { const e = document.querySelector(sel); if (!e || e.hidden) return null;
  const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; }"""

LIST_JS = r"""() => [...document.querySelectorAll('#mv-pop-list .mv-pop__item')].map(a => ({
  text: a.innerText.replace(/\s+/g, ' ').trim(), href: a.getAttribute('href'),
  current: a.getAttribute('aria-current') === 'true' }))"""
