"""Site search opens a signal, and the Wire list scrolls (18 Oct 2026).

Bug 1: every signal in search-index.json pointed at bare index.html, so
clicking a search result just reloaded the map. On pages in articles/ and
cases/ the same relative link went to articles/index.html, which 404s.
Now a signal with a pin links to index.html?signal=<first 8 of id> (the same
link the ticker uses), result links resolve against the site root, and on the
map page a click opens the drawer in place.

Bug 2: in the Wire panel's Top mode, #wire-list sat inside a plain block
(#wire-top), so it grew to the full list and the panel's overflow:hidden cut
off everything past the sixth entry with no way to scroll.

The optional browser test at the end runs only when Playwright and a browser
are installed (FORGE_UI_BASE=http://host:port pointing at a served dist/).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_LINK = re.compile(r"^index\.html\?signal=([0-9a-f]{8})$")


def _fresh_db(tmp_path, monkeypatch):
    import app as forge_app

    db_file = tmp_path / "database.db"
    monkeypatch.setattr(forge_app, "DB_PATH", db_file)
    forge_app.init_db()
    forge_app.migrate_db()
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def test_search_target_for_pins_articles_and_the_rest():
    from tools.publish import _signal_search_target as target

    pin = {"signal_id": "821f2edc-aaaa-bbbb", "lat": -34.0, "lng": 18.5,
           "article_slug": "some-analysis"}
    assert target(pin) == {"url": "index.html?signal=821f2edc", "signal": "821f2edc"}
    no_pin = {"signal_id": "abcdef01", "lat": None, "lng": None, "article_slug": "x-y"}
    assert target(no_pin) == {"url": "articles/x-y.html"}
    bare = {"signal_id": "abcdef01", "lat": None, "lng": None, "article_slug": None}
    assert target(bare) == {"url": "index.html"}


def test_every_checked_pin_gets_a_drawer_link(tmp_path, monkeypatch):
    """All checked pins, imported from the batches, link to their own drawer."""
    import tools.publish as pub
    from tools.checked_signals import import_checked, load_index

    conn = _fresh_db(tmp_path, monkeypatch)
    cs = load_index()
    import_checked(conn, cs, write=True)
    signals = [s for s in pub._fetch_signals(conn) if s.get("checked")]
    assert len(signals) == len(cs.active) >= 24
    seen = set()
    for s in signals:
        t = pub._signal_search_target(s)
        m = SIGNAL_LINK.match(t["url"])
        assert m, t
        assert m.group(1) == s["signal_id"][:8] == t["signal"]
        seen.add(m.group(1))
    assert len(seen) == len(signals)          # no two pins share a link


def test_built_search_index_matches_map_markers():
    """The committed dist/: every signal result opens a marker that is on the map."""
    docs = json.loads((ROOT / "dist/search-index.json").read_text(encoding="utf-8"))
    html = (ROOT / "dist/index.html").read_text(encoding="utf-8")
    raw = json.loads(re.search(r"var RAW = (\[.*?\]);\n", html, re.S).group(1))
    marker_ids = {str(m["signal_id"])[:8] for m in raw if m.get("lat") and m.get("lng")}
    sig_docs = [d for d in docs if d["kind"] == "signal"]
    assert sig_docs
    for d in sig_docs:
        assert d["url"] != "index.html", d["title"]
        m = SIGNAL_LINK.match(d["url"])
        assert m and m.group(1) in marker_ids and d.get("signal") == m.group(1), d


def test_search_box_script_opens_drawer_and_resolves_links():
    base = (ROOT / "publisher/templates/base.html").read_text(encoding="utf-8")
    assert "storeFields: ['title', 'kind', 'url', 'signal']" in base
    assert "new URL(r.url, siteRoot)" in base           # not raw relative hrefs
    assert "data-signal=" in base
    assert "window.forgeOpenSignal" in base and "e.preventDefault()" in base
    assert "+ r.url +" not in base
    assert "r.title.slice(0, 60) +" not in base        # titles are escaped now

    mp = (ROOT / "publisher/templates/map.html").read_text(encoding="utf-8")
    hook = re.search(r"window\.forgeOpenSignal = function \(sid, trigger\) \{(.*?)\};", mp, re.S)
    assert hook, "map must expose window.forgeOpenSignal"
    assert "idxForSignal(sid)" in hook.group(1) and "showSignal(idx" in hook.group(1)
    assert "return false" in hook.group(1) and "return true" in hook.group(1)


def test_static_subpages_use_the_fixed_search_script():
    """Pages kept in dist/ but not rebuilt from the database carry the fix too."""
    pages = list((ROOT / "dist/articles").glob("*.html")) + list((ROOT / "dist/cases").glob("*.html"))
    for page in pages:
        t = page.read_text(encoding="utf-8")
        if 'id="site-search"' not in t:
            continue
        assert "var indexUrl = '../search-index.json';" in t, page.name
        assert "new URL(r.url, siteRoot)" in t and "+ r.url +" not in t, page.name


def test_wire_list_can_scroll():
    mp = (ROOT / "publisher/templates/map.html").read_text(encoding="utf-8")
    body = re.search(r"\n  \.md-panel__body \{(.*?)\}", mp, re.S).group(1)
    assert "overflow-y: auto" in body and "min-height: 0" in body
    top = re.search(r"\n  #wire-top \{(.*?)\}", mp, re.S).group(1)
    for rule in ("display: flex", "flex-direction: column", "min-height: 0"):
        assert rule in top, rule
    assert '<div id="wire-top">' in mp and 'class="md-panel__body" id="wire-list"' in mp


@pytest.mark.skipif(not os.environ.get("FORGE_UI_BASE"), reason="set FORGE_UI_BASE to a served dist/")
def test_in_browser_search_click_and_wire_scroll():
    pw = pytest.importorskip("playwright.sync_api")
    base = os.environ["FORGE_UI_BASE"].rstrip("/")
    with pw.sync_playwright() as p:
        exe = os.environ.get("FORGE_UI_CHROME") or None
        browser = p.chromium.launch(executable_path=exe)
        page = browser.new_page(viewport={"width": 1280, "height": 620})
        page.goto(base + "/index.html", wait_until="networkidle")
        page.evaluate("window.__sameDoc = 1")

        page.type("#site-search", "Voorbrug", delay=20)
        page.wait_for_selector("#search-results a[data-signal]", state="visible")
        page.locator("#search-results a").first.click()
        page.wait_for_function("!document.getElementById('signal-drawer').hidden")
        assert page.evaluate("window.__sameDoc") == 1           # no reload
        assert page.inner_text("#sd-title").startswith("Voorbrug, Delft")
        assert "signal=" in page.url
        page.go_back()
        page.wait_for_function("document.getElementById('signal-drawer').hidden")

        page.evaluate("openPanel('wire')")
        info = page.evaluate("""() => { const l = document.getElementById('wire-list');
          l.scrollTop = l.scrollHeight; return {sh: l.scrollHeight, ch: l.clientHeight, top: l.scrollTop}; }""")
        assert info["sh"] > info["ch"] and info["top"] > 0
        browser.close()


# --- Sprint 5 (19 Oct 2026): Asanda Village area fix + search aliases -------

ASANDA = "0d2ac747"


def test_checked_search_aliases_are_read_and_cleaned():
    from tools.publish import _checked_fields

    meta = json.dumps({"checked": {"incident": "x",
                                   "search_aliases": ["Zola Village", " ", 7]}})
    assert _checked_fields(meta)["search_aliases"] == ["Zola Village", "7"]
    assert _checked_fields(json.dumps({"checked": {"incident": "x"}}))["search_aliases"] == []
    assert _checked_fields(None)["search_aliases"] == []


def test_asanda_pin_area_corrected_and_old_name_kept_as_alias():
    from tools.checked_signals import load_index

    pin = next(s for s in load_index().active if s["signal_id"].startswith(ASANDA))
    chk = pin["metadata"]["checked"]
    assert pin["title"].startswith("Asanda Village, Strand")
    assert "Zola Village" not in pin["title"]
    assert chk["search_aliases"] == ["Zola Village"]
    assert "Asanda Village" in chk["location"]
    urls = {s["url"] for s in chk["sources"]}
    assert "https://vocfm.co.za/man-stabbed-and-burnt-to-death-in-strand-cpf-condemns-mob-justice/" in urls
    assert len(urls) == 3
    prov = json.loads((ROOT / "docs/signals/provenance.json").read_text(encoding="utf-8"))
    entry = next(v for k, v in prov["pins"].items() if k.startswith(ASANDA))
    assert entry["independence"] == "partly_independent"
    assert len(entry["outlets"]) == 3
    # The alleged motive stays private: never on the public pin.
    public = json.dumps(pin) + json.dumps(entry)
    assert "caught stealing" not in public and "Lubobo" not in pin["content"]


def test_built_search_index_finds_asanda_by_either_name():
    docs = json.loads((ROOT / "dist/search-index.json").read_text(encoding="utf-8"))
    doc = next(d for d in docs if d.get("signal") == ASANDA)
    assert doc["title"].startswith("Asanda Village")
    assert doc["aliases"] == "Zola Village Western Cape"   # alias, then the region
    for page in ["index.html", "about.html"]:
        t = (ROOT / "dist" / page).read_text(encoding="utf-8")
        assert "fields: ['title', 'aliases']" in t, page
