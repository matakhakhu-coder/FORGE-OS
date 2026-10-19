"""Area check (address radar, Sprints A and B).

Sprint A: every active hand-checked pin has a precision entry in
docs/signals/pin-precision.json, kept apart from the pin files; the build
stops when one is missing; markers carry incident_end, incident_event,
precision and uncertainty_m.

Sprint B: the built-in place list (dist/places-za.json) holds the places the
pins sit in; the map page has the panel and loads no outside geocoder; the
plain-function tests in tests/js/area_check.test.js pass; and, when
FORGE_UI_BASE is set, a real browser runs tests/browser/area_check.browser.js.
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import publish                      # noqa: E402
from tools.checked_signals import load_index   # noqa: E402


def _active_ids():
    return {s["signal_id"] for s in load_index().active}


# ── Sprint A: precision ──────────────────────────────────────────────────────────────

def test_every_active_pin_has_a_valid_precision_entry():
    missing = [i[:8] for i in _active_ids() if publish._precision_for(i) is None]
    assert not missing, f"no precision entry: {missing}"


def test_precision_file_has_no_stale_or_unknown_entries():
    data = json.loads(publish.PRECISION_FILE.read_text(encoding="utf-8"))
    assert data["schema"] == "forge.pin_precision"
    assert set(data["levels"]) == set(publish.PRECISION_LEVELS)
    stale = set(data["pins"]) - _active_ids()
    assert not stale, f"precision entries for pins that are not active: {sorted(stale)}"


def test_precision_rejects_bad_entries(monkeypatch):
    monkeypatch.setattr(publish, "_PRECISION_CACHE", {
        "a": {"precision": "street", "uncertainty_m": 300},
        "b": {"precision": "house", "uncertainty_m": 300},
        "c": {"precision": "street", "uncertainty_m": 0},
        "d": {"precision": "street", "uncertainty_m": "lots"},
        "e": {"precision": "farm", "uncertainty_m": 99999},
    })
    assert publish._precision_for("a") == {"precision": "street", "uncertainty_m": 300}
    for k in ("b", "c", "d", "e", "missing"):
        assert publish._precision_for(k) is None


def test_build_stops_when_a_pin_has_no_precision(monkeypatch, capsys):
    ids = sorted(_active_ids())
    pins = {i: {"precision": "street", "uncertainty_m": 300} for i in ids[1:]}
    monkeypatch.setattr(publish, "_PRECISION_CACHE", pins)
    import tools.checked_signals as cs_mod
    monkeypatch.setattr(cs_mod, "verify", lambda conn, cs: [])   # skip the database part
    with pytest.raises(publish.CheckedGuardError):
        publish._checked_guard_before(None)
    out = capsys.readouterr().out
    assert "precision" in out and ids[0][:8] in out


def test_pin_files_are_untouched_by_precision():
    """Precision lives in its own file: no pin record carries it."""
    for s in load_index().active:
        chk = s["metadata"]["checked"]
        assert "uncertainty_m" not in chk and "precision" not in chk


def test_incident_end_follows_the_incident_window():
    day = {"incident_start": "2026-10-15", "incident_precision": "day"}
    assert publish._incident_at(day) == "2026-10-14T22:00:00Z"
    assert publish._incident_end(day) == "2026-10-15T22:00:00Z"
    assert publish._incident_end({"incident_start": None}) is None


def test_markers_carry_the_area_check_fields():
    dist = ROOT / "dist" / "index.html"
    if not dist.exists():
        pytest.skip("dist not built")
    html = dist.read_text(encoding="utf-8")
    for key in ('"incident_end"', '"incident_event"', '"precision"', '"uncertainty_m"'):
        assert key in html, key


# ── Sprint B: place list ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def places():
    return publish._build_places()


def _rows_named(places, name):
    return [r for r in places["rows"] if r[0] == name or name in (r[6] or [])]


def test_place_list_holds_the_places_the_plan_found_missing(places):
    for name in ("Bonteheuwel", "Asanda Village", "Zola Village", "Belgravia", "Langa",
                 "Port Elizabeth", "Mahikeng", "Bholotwa", "Soweto-on-Sea", "KwaDwesi"):
        assert _rows_named(places, name), name


def test_three_wesbanks_stay_apart(places):
    rows = _rows_named(places, "Wesbank")
    assert len(rows) >= 3
    assert len({(r[1], r[2]) for r in rows}) == len(rows)


def test_bonteheuwel_is_the_suburb_point_not_a_road(places):
    (row,) = [r for r in places["rows"] if r[0] == "Bonteheuwel"]
    assert abs(row[1] - -33.94917) < 0.001 and abs(row[2] - 18.55306) < 0.001


def test_place_rows_are_inside_south_africa_and_well_formed(places):
    lat0, lat1, lng0, lng1 = publish._ZA_BOX
    for r in places["rows"]:
        assert lat0 <= r[1] <= lat1 and lng0 <= r[2] <= lng1, r
        assert 0 <= r[3] < len(places["kinds"])
        assert 0 <= r[4] < len(places["provinces"])
        assert 0 <= r[5] < len(places["municipalities"])
    assert "GeoNames" in places["credit"]


def test_place_list_stays_small(places):
    raw = json.dumps(places, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(gzip.compress(raw, 9)) < 220_000


# ── Sprint B: page ────────────────────────────────────────────────────────────────

TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
JS = ROOT / "publisher" / "static" / "js" / "area-check.js"


def test_page_has_the_area_check_parts():
    html = TEMPLATE.read_text(encoding="utf-8")
    for needle in ('id="panel-area"', 'data-panel="area"', 'id="ac-home"', 'id="ac-bar"',
                   'id="sd-outside"', 'static/js/area-check.js', "No reports does not mean safe",
                   "placesUrl: 'places-za.json'"):
        assert needle in html, needle


PRIVACY_LINE = ("Typed addresses are sent to Geoapify, along with your IP address, to find the spot. "
                "ZA Divergent never receives them.")


def test_only_the_one_address_service_and_its_key():
    """Sprint C: Geoapify is the only outside geocoder, and its key sits once, in the settings block."""
    html = TEMPLATE.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    for bad in ("nominatim", "maps.googleapis", "api.mapbox", "photon.komoot", "api_key"):
        assert bad not in (html + js).lower(), bad
    assert "apikey" not in html.lower()
    keys = re.findall(r"\b[0-9a-f]{32}\b", js)
    assert len(keys) == 1, keys
    block = js[js.index("var GEOCODER = {"):js.index("};", js.index("var GEOCODER = {"))]
    assert keys[0] in block and "service: 'geoapify'" in block and "timeoutMs: 6000" in block
    assert js.count("api.geoapify.com") == 1


def test_address_box_has_privacy_line_and_attribution():
    html = TEMPLATE.read_text(encoding="utf-8")
    form = html[html.index('id="ac-addr"'):html.index("</form>", html.index('id="ac-addr"'))]
    assert PRIVACY_LINE in form
    assert 'href="https://www.geoapify.com/privacy-policy/"' in form
    assert "Powered by" in form and "https://www.geoapify.com/" in form
    assert "OpenStreetMap" in form and "https://www.openstreetmap.org/copyright" in form
    # the line sits above the results, before anything is searched
    assert form.index(PRIVACY_LINE) > form.index('id="ac-addr-go"')
    assert html.index('id="ac-addr"') < html.index('id="ac-addr-matches"') < html.index('id="ac-place-btn"')


def test_address_search_only_on_press():
    js = JS.read_text(encoding="utf-8")
    body = js[js.index("typed street address (Sprint C)"):js.index("placing a point on the map")]
    assert "addEventListener('submit'" in body
    for bad in ("'input'", "'keyup'", "'keydown'", "'change'"):
        assert bad not in body, bad


def test_external_link_gate_and_drawer_still_present():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "extGateOpen" in html and 'id="sd-body"' in html


def test_area_check_js_plain_functions():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "area_check.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


@pytest.mark.skipif(not os.environ.get("FORGE_UI_BASE"), reason="set FORGE_UI_BASE to run the browser test")
def test_area_check_in_a_real_browser():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "browser" / "area_check.browser.js")],
                       capture_output=True, text=True, timeout=600, env=os.environ.copy())
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
