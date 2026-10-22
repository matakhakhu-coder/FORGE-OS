"""Pin bookmarks (22 Oct 2026): the event drawer has a Watchlist star.

A pin is saved in the visitor's own browser under its full signal id, listed
in the Watchlist beside entities, and its row reopens the pin's drawer. Runs the
Node tests in tests/js/pin_bookmarks.test.js and checks the map template wiring.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "publisher" / "templates" / "map.html"


def test_pin_bookmark_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "pin_bookmarks.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_drawer_has_a_watchlist_star():
    html = MAP.read_text(encoding="utf-8")
    head = html.split('<div class="sd-head">', 1)[1].split("</div>", 1)[0]
    btn = re.search(r'<button [^>]*id="sd-star"[^>]*>', head).group(0)
    assert 'type="button"' in btn and 'aria-pressed="false"' in btn
    assert 'id="sd-star-status"' in head and 'aria-live="polite"' in head
    # The star sits before Share and Close.
    assert head.index('id="sd-star"') < head.index('id="sd-share"') < head.index('id="sd-close"')


def test_star_uses_stable_ids_and_follows_the_open_pin():
    html = MAP.read_text(encoding="utf-8")
    # Saved under the full signal id and the ?signal= address, not the map index.
    assert "var id = String(m.signal_id || '');" in html
    assert "WL.pinUrl(id)" in html
    assert "type: 'signals'" in html
    assert "toggleItem('signals', drawerIdx" not in html
    # Every drawer fill (pin click, Wire, ticker, Back/Forward, shared link,
    # Watchlist) sets the star from storage.
    fill = html.split("function fillDrawer(m) {", 1)[1][:200]
    assert "syncStar(m, false);" in fill
    assert "WL.isWatchlisted('signals', bm.id)" in html
    # The star's state is set as text and attributes only.
    star = html.split("var starBtn = document.getElementById('sd-star');", 1)[1].split("function fillDrawer", 1)[0]
    assert "innerHTML" not in star
    assert "window.ZADWatchlist.toggleItem('signals', bm)" in star


def test_watchlist_row_reopens_the_pin_in_place():
    html = MAP.read_text(encoding="utf-8")
    block = html.split("var wlListEl = document.getElementById('wl-list');", 1)[1][:500]
    assert "closest('a[data-wl-signal]')" in block
    assert "window.forgeOpenSignal(a.getAttribute('data-wl-signal'), a)" in block
    assert "e.preventDefault()" in block
    # forgeOpenSignal is defined before the handler can run.
    assert html.index("window.forgeOpenSignal = function") < html.index("var wlListEl")


def test_entity_stars_unchanged():
    html = MAP.read_text(encoding="utf-8")
    assert 'data-wl-type="actors"' in html and 'data-wl-url="entities/{{ a.slug }}.html"' in html
