"""Time-aware pins, Batch 3: a lighter map page that loads a pin's details
only when it is opened.

The page keeps a map list (tools/map_data.py MAP_FIELDS) in `var RAW`; each
pin's full record goes to dist/pins/<short id>.json, which the drawer fetches.

The build tests use the same route as tests/test_checked_signals.py: an empty
database, one import of docs/signals, tools/publish.py main() into a temp dist.

The browser tests run when FORGE_UI_BASE points at a served Batch 3 dist/:

    FORGE_UI_BASE=http://127.0.0.1:8765/ pytest tests/test_time_aware_lazy_details.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import map_data  # noqa: E402
import nothing_missing as nm  # noqa: E402

from tests.test_checked_signals import ALL_PINS, _fresh_db, _publish_to  # noqa: E402
from tests.test_publish_deploy import _git, repo  # noqa: E402,F401  (repo: fixture)

TEMPLATE = (ROOT / "publisher" / "templates" / "map.html").read_text(encoding="utf-8")
UI_BASE = os.environ.get("FORGE_UI_BASE")

# The heaviest fields: none of them may come back into the page.
DETAIL_ONLY = ("story", "summary", "source_links", "provenance", "fact_basis",
               "related", "media", "disagreements", "location_note", "search_aliases")


def _build(tmp_path, monkeypatch):
    from tools.checked_signals import run_import
    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.close()
    run_import(db_file, write=True, out=lambda m: None)
    return _publish_to(tmp_path, monkeypatch, db_file), db_file


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        dist, _ = _build(tmp_path_factory.mktemp("b3"), mp)
    return dist


# ── The build ───────────────────────────────────────────────────────────────────────

def test_page_carries_only_the_map_list(built):
    entries = map_data.read_map_list(built / "index.html")
    assert len(entries) == ALL_PINS
    for e in entries:
        assert set(e) == set(map_data.MAP_FIELDS)   # tojson sorts the keys
    html = (built / "index.html").read_text(encoding="utf-8")
    raw = map_data.RAW_RE.search(html).group(1)
    for field in DETAIL_ONLY:
        assert f'"{field}"' not in raw, field


def test_one_detail_file_per_pin_and_nothing_else(built):
    entries = map_data.read_map_list(built / "index.html")
    files = sorted(p.name for p in (built / map_data.PIN_DIR).iterdir())
    assert files == sorted(map_data.detail_name(e["signal_id"]) for e in entries)
    assert map_data.problems(built) == []


def test_detail_files_hold_the_full_record(built):
    """What the drawer used to read from the page is in the pin's file."""
    full = map_data.load_markers(built)
    assert len(full) == ALL_PINS
    for m in full:
        rec = map_data.read_detail(built, m["signal_id"])
        assert rec == {k: m[k] for k in rec}
        assert rec.get("story") or rec.get("summary"), m["signal_id"]
        assert rec.get("source_links"), m["signal_id"]
        assert "fact_basis" in rec and "provenance" in rec


def test_page_got_lighter(built):
    """Batch 1 measured about 6.6 KB of embedded data a pin; the map list must
    stay well under 1.5 KB a pin."""
    html = (built / "index.html").read_text(encoding="utf-8")
    raw = map_data.RAW_RE.search(html).group(1).encode()
    assert len(raw) / ALL_PINS < 1500, len(raw) / ALL_PINS
    full = json.dumps(map_data.load_markers(built), ensure_ascii=False).encode()
    assert len(raw) < len(full) / 4


def test_rebuild_clears_leftover_detail_files(tmp_path, monkeypatch):
    dist, db_file = _build(tmp_path, monkeypatch)
    stale = dist / map_data.PIN_DIR / "deadbeef.json"
    stale.write_text('{"signal_id": "deadbeef-0000"}')
    assert any("leftover" in p for p in map_data.problems(dist))
    _publish_to(tmp_path, monkeypatch, db_file)
    assert not stale.exists()
    assert map_data.problems(dist) == []


def test_publish_refuses_when_a_detail_file_is_missing(tmp_path, monkeypatch):
    import tools.publish as pub
    real = map_data.write_pin_details

    def drop_one(dist, markers):
        n = real(dist, markers)
        (Path(dist) / map_data.PIN_DIR / map_data.detail_name(markers[0]["signal_id"])).unlink()
        return n

    monkeypatch.setattr(map_data, "write_pin_details", drop_one)
    with pytest.raises(pub.CheckedGuardError) as exc:
        _build(tmp_path, monkeypatch)
    assert exc.value.code == 3


def test_short_id_clash_is_refused(tmp_path):
    a = {"signal_id": "abcdef12-0000-0000-0000-000000000001", "title": "a"}
    b = {"signal_id": "abcdef12-0000-0000-0000-000000000002", "title": "b"}
    with pytest.raises(map_data.ShortIdClash):
        map_data.write_pin_details(tmp_path, [a, b])


def test_deploy_stages_new_detail_files_despite_a_global_dist_ignore(repo):
    """dist/pins/ is new. This machine's global git ignore lists dist/, so the
    deploy must force-add new files there (it does, since the push fix)."""
    import tools.publish as pub
    w, _ = repo
    (w / "dist" / "pins").mkdir()
    (w / "dist" / "pins" / "abcdef12.json").write_text("{}")
    pub._stage_deploy_files(str(w))
    staged = _git(w, "diff", "--cached", "--name-only").split()
    assert "dist/pins/abcdef12.json" in staged


# ── The after-build check catches each break ──────────────────────────────────────────────

@pytest.fixture
def copy_of_built(built, tmp_path):
    import shutil
    out = tmp_path / "dist"
    shutil.copytree(built, out, ignore=shutil.ignore_patterns("static", "articles", "cases", "entities"))
    return out, map_data.read_map_list(out / "index.html")


def test_problems_missing_file(copy_of_built):
    dist, entries = copy_of_built
    (dist / map_data.PIN_DIR / map_data.detail_name(entries[3]["signal_id"])).unlink()
    assert any("no detail file" in p for p in map_data.problems(dist))


def test_problems_file_for_another_pin(copy_of_built):
    dist, entries = copy_of_built
    folder = dist / map_data.PIN_DIR
    other = (folder / map_data.detail_name(entries[1]["signal_id"])).read_text()
    (folder / map_data.detail_name(entries[0]["signal_id"])).write_text(other)
    assert any("for another pin" in p for p in map_data.problems(dist))


def test_problems_unreadable_file(copy_of_built):
    dist, entries = copy_of_built
    (dist / map_data.PIN_DIR / map_data.detail_name(entries[2]["signal_id"])).write_text("{oops")
    assert any("not readable" in p for p in map_data.problems(dist))


def test_problems_map_list_and_file_disagree(copy_of_built):
    dist, entries = copy_of_built
    path = dist / map_data.PIN_DIR / map_data.detail_name(entries[0]["signal_id"])
    rec = json.loads(path.read_text())
    rec["title"] = rec["title"] + " (old)"
    path.write_text(json.dumps(rec))
    assert any("title differs" in p for p in map_data.problems(dist))


def test_problems_detail_fields_back_in_the_page(copy_of_built):
    dist, entries = copy_of_built
    html = (dist / "index.html").read_text(encoding="utf-8")
    fat = [dict(e, story="x") for e in entries]
    html = map_data.RAW_RE.sub(lambda m: "var RAW = " + json.dumps(fat) + ";", html, count=1)
    (dist / "index.html").write_text(html, encoding="utf-8")
    assert any("carries detail fields: story" in p for p in map_data.problems(dist))


def test_nothing_missing_counts_a_pin_without_details_as_off_the_map(copy_of_built):
    dist, entries = copy_of_built
    gone = entries[5]["signal_id"][:8]
    (dist / map_data.PIN_DIR / f"{gone}.json").unlink()
    rep = nm.check(dist)
    bad = [p for p in rep["problems"] if p["short"] == gone]
    assert [p["route"] for p in bad] == ["map"]
    assert "details file" in bad[0]["why"]
    assert nm.check(dist)["problems"] == bad


def test_nothing_missing_passes_on_the_batch3_build(built):
    rep = nm.check(built)
    assert rep["problems"] == [], rep["problems"][:5]


# ── The page code ───────────────────────────────────────────────────────────────────

def _fn_body(name: str) -> str:
    start = TEMPLATE.index(f"function {name}(")
    nxt = TEMPLATE.find("\n  function ", start + 10)
    return TEMPLATE[start: nxt if nxt > 0 else start + 6000]


def test_code_that_runs_before_details_reads_only_map_fields():
    """Outside the drawer, the page's script reads a pin only through the map
    list: popup, pulse, Wire, ticker, Area check, Share, Watchlist. ('m' also
    names marker layers there, which have .layer.)"""
    script = TEMPLATE[TEMPLATE.index("var RAW = {{ markers"):]
    body = _fn_body("fillDrawer")
    script = script.replace(body, "")
    used = set(re.findall(r"\bm\.([a-z_]+)\b", script))
    allowed = set(map_data.MAP_FIELDS) | {"_idx", "layer"}
    assert used - allowed == set(), sorted(used - allowed)
    assert "fact_basis" not in script and "source_links" not in script


def test_drawer_fetches_details_on_open():
    body = _fn_body("openDrawer")
    assert "requestDetail(idx)" in body
    assert "PIN_DETAIL_DIR + encodeURIComponent(sid) + '.json'" in TEMPLATE


def test_drawer_shows_no_audit_stand_in_while_loading():
    """Before the details arrive the drawer stops after the dates, so it never
    says 'not yet audited' or 'No source links' for a pin that has them."""
    body = _fn_body("fillDrawer")
    stop = body.index("if (!detail) {")
    assert body.index("return;", stop) < body.index("not yet audited")
    assert body.index("return;", stop) < body.index("sd-src")


def test_preview_starts_the_fetch_early():
    """Opening a pin's preview fetches its details, so "View event" is quick."""
    assert "prefetchDetail(idx)" in _fn_body("onPinActivate")


def test_map_list_fields_cover_area_check_and_wire():
    """The page builds Area check's and the Wire's lists from RAW at load."""
    for field in ("precision", "uncertainty_m", "incident_at", "incident_end", "incident_event",
                  "published_at", "checked", "source", "region", "gravity_score"):
        assert field in map_data.MAP_FIELDS


# ── In the browser ────────────────────────────────────────────────────────────────────

browser = pytest.mark.skipif(not UI_BASE, reason="set FORGE_UI_BASE to run the browser checks")
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
         "device_scale_factor": 3}


def _first_sid(page) -> str:
    return page.evaluate("() => fetch('records.json').then(r => r.json())"
                         ".then(d => d.records[0].signal_id)")


def _no_tiles(ctx):
    ctx.route("**/*", lambda r: r.abort() if "tile.openstreetmap.org" in r.request.url else r.continue_())


@browser
@pytest.mark.parametrize("device", ["desktop", "phone"])
def test_browser_drawer_loads_details_on_open(device):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(**(PHONE if device == "phone" else {"viewport": {"width": 1366, "height": 900}}))
        _no_tiles(ctx)
        page = ctx.new_page()
        fetched = []
        page.on("request", lambda r: fetched.append(r.url) if "/pins/" in r.url else None)
        page.goto(UI_BASE, wait_until="load")
        assert fetched == []   # nothing fetched until a pin is opened
        sid = _first_sid(page)
        page.goto(UI_BASE.rstrip("/") + "/?signal=" + sid[:8], wait_until="load")
        page.wait_for_function("() => window.ZADPinDetails && window.ZADPinDetails.state() === 'ready'",
                               timeout=15000)
        assert any(u.endswith(f"/pins/{sid[:8]}.json") for u in fetched), fetched
        assert page.locator("#sd-story").text_content().strip()
        assert page.locator("#sd-src-wrap").is_visible()
        assert not page.locator("#sd-detail-status").is_visible()
        b.close()


@browser
def test_browser_failed_details_offer_a_retry():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        _no_tiles(ctx)
        page = ctx.new_page()
        page.goto(UI_BASE, wait_until="load")
        sid = _first_sid(page)
        page.route("**/pins/*.json", lambda r: r.abort())
        page.goto(UI_BASE.rstrip("/") + "/?signal=" + sid[:8], wait_until="load")
        page.wait_for_function("() => window.ZADPinDetails.state() === 'error'", timeout=15000)
        assert page.locator("#sd-title").text_content().strip()      # title still shows
        assert page.locator("#sd-detail-retry").is_visible()
        assert not page.locator("#sd-src-wrap").is_visible()
        assert not page.locator("#sd-fact-wrap").is_visible()        # no "not yet audited" stand-in
        page.unroute("**/pins/*.json")
        page.locator("#sd-detail-retry").click()
        page.wait_for_function("() => window.ZADPinDetails.state() === 'ready'", timeout=15000)
        assert page.locator("#sd-src-wrap").is_visible()
        b.close()
