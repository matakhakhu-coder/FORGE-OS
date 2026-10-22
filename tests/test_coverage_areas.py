"""Coverage areas: the scheduled runs' search areas, worked out from the
published pins (tools/coverage_areas.py)."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import coverage_areas as ca
from tools.checked_signals import stamp_files, PATTERN_TEST_PROVINCE

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DIR = ROOT / "docs" / "signals"
SAST = timezone(timedelta(hours=2))


def _pin(sid, lat, lng, province, title="Somewhere: man shot dead"):
    return {"signal_id": sid, "title": title, "lat": lat, "lng": lng,
            "province": province, "place_names": ca.pin_place_names(title)}


@pytest.fixture
def tiny_gaz(tmp_path):
    """A two-town gazetteer: Alpha (precinct + places) and Beta, 200 km east."""
    places = {"fields": ["name", "lat", "lng", "feature_code", "population",
                         "province", "district", "municipality"],
              "rows": [["Alpha", -26.0, 26.0, "PPL", 20000, "North West", "D1", "M-Alpha"],
                       ["Alpha East", -26.0, 26.1, "PPLX", 0, "North West", "D1", "M-Alpha"],
                       ["Faraway", -26.0, 27.0, "PPL", 500, "North West", "D1", "M-Far"],
                       ["Beta", -26.0, 28.0, "PPL", 15000, "North West", "D2", "M-Beta"]]}
    sq = lambda lat, lng, d: [[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d],
                              [lng - d, lat + d], [lng - d, lat - d]]
    prec = {"precincts": [{"name": "Alpha", "station": [26.0, -26.0], "rings": [sq(-26.0, 26.0, 0.1)]},
                          {"name": "Beta", "station": [28.0, -26.0], "rings": [sq(-26.0, 28.0, 0.1)]},
                          {"name": "Pointonly", "station": [26.2, -26.0], "rings": []}]}
    pp, pr = tmp_path / "places.json", tmp_path / "prec.json"
    pp.write_text(json.dumps(places))
    pr.write_text(json.dumps(prec))
    return pp, pr


def _compute(pins, gaz, r=30.0):
    return ca.compute_areas(pins, r, places_path=gaz[0], precincts_path=gaz[1])


# ── Areas from pins ──────────────────────────────────────────────────────────────

def test_areas_name_the_precincts_and_places_inside_the_radius(tiny_gaz):
    res = _compute([_pin("a", -26.0, 26.01, "North West", "Alpha East (x St, approx.): man stabbed"),
                    _pin("b", -26.0, 28.0, "North West")], tiny_gaz)
    assert res["radius_km"] == 30.0 and len(res["areas"]) == 2
    alpha = next(a for a in res["areas"] if a["pins"][0]["signal_id"] == "a")
    names = [p["name"] for p in alpha["places"]]
    assert names == ["Alpha", "Alpha East"]          # Faraway (100 km) and Beta are out
    prec = {p["name"]: p for p in alpha["precincts"]}
    assert prec["Alpha"]["holds_pins"] == ["a"]
    assert "Pointonly" in prec and "Beta" not in prec   # a station-only precinct counts by its point
    assert alpha["name"].startswith("Alpha") and alpha["province"] == "North West"
    # Searches start with the precinct holding the pin, then the pin's own place.
    assert alpha["queries"][0].startswith('"Alpha" ')
    assert any(q.startswith('"Alpha East" ') for q in alpha["queries"])
    assert len(alpha["queries"]) <= ca.MAX_QUERIES_PER_AREA


def test_overlapping_circles_merge_and_distant_ones_do_not():
    # About 50 km apart: circles of 30 km overlap (50 < 60), circles of 20 km do not.
    a, b = _pin("a", -26.0, 26.0, "North West"), _pin("b", -26.0, 26.5, "North West")
    assert 45 < ca.haversine_km(a["lat"], a["lng"], b["lat"], b["lng"]) < 55
    assert len(ca.cluster_pins([a, b], 30)) == 1
    assert len(ca.cluster_pins([a, b], 20)) == 2
    # Chains merge: a-b and b-c overlap, so a, b and c are one area.
    c = _pin("c", -26.0, 27.0, "North West")
    assert [len(g) for g in ca.cluster_pins([a, b, c], 30)] == [3]


def test_radius_is_one_config_value(monkeypatch):
    assert ca.radius_km() == ca.COVERAGE_RADIUS_KM == 30.0
    monkeypatch.setenv("FORGE_COVERAGE_RADIUS_KM", "45")
    assert ca.radius_km() == 45.0
    assert ca.radius_km(12) == 12.0
    with pytest.raises(ValueError):
        ca.radius_km(0)


def test_pin_place_names_from_titles():
    assert ca.pin_place_names("Voorbrug, Delft (Camdeboo St, approx.): 2 men shot dead") == ["Voorbrug", "Delft"]
    assert ca.pin_place_names("Doornpan Farm near Ottosdal (farm area, approx.): x") == ["Doornpan Farm", "Ottosdal"]
    assert ca.pin_place_names("Farm near Worcester (approx.): x") == ["Worcester"]


# ── A new pin brings its area; held pins bring none ────────────────────────────────

def _copy_signals(tmp_path) -> Path:
    d = tmp_path / "signals"
    shutil.copytree(SIGNALS_DIR, d)
    return d / "index.json"


def _nw_batch(index: Path) -> tuple[Path, dict]:
    idx = json.loads(index.read_text())
    e = next(b for b in idx["batches"] if b["province"] == "North West")
    p = index.parent / e["file"]
    return p, json.loads(p.read_text())


def _save(index: Path, path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    idx = json.loads(index.read_text())
    for e in idx["batches"]:
        if e["file"] == path.name:
            e["signals"] = len(data["signals"])
    index.write_text(json.dumps(idx, indent=2))
    stamp_files(index)


def test_current_pins_make_their_areas_and_skip_held_places():
    pins = ca.published_pins()
    assert len(pins) == 44
    res = ca.compute_areas(pins)
    keys = {a["key"] for a in res["areas"]}
    assert keys == {"western-cape--cape-town", "western-cape--mossel-bay", "western-cape--worcester",
                    "north-west--mmabatho", "north-west--ottosdal", "north-west--schweizer-reneke",
                    "eastern-cape--gqeberha", "eastern-cape--willowvale", "eastern-cape--east-london",
                    "eastern-cape--ilinge"}
    assert sum(len(a["pins"]) for a in res["areas"]) == 44
    held = {h["place"] for h in ca.held_back()}
    assert any("Lebotloane" in h for h in held)
    pin_titles = " ".join(p["title"] for a in res["areas"] for p in a["pins"])
    assert "Lebotloane" not in pin_titles and "Dunoon" not in pin_titles
    # Lebotloane is far from every published pin, so no area reaches it.
    blob = json.dumps(res["areas"])
    assert "Lebotloane" not in blob and "Moretele" not in blob
    # The pins hold the precincts the police statements came from.
    holders = {pr["name"] for a in res["areas"] for pr in a["precincts"] if pr["holds_pins"]}
    assert {"Steenberg", "Ottosdal", "Ipelegeng", "Mmabatho", "Kwadwesi", "Duncan Village", "Bholothwa"} <= holders
    assert not any(a["pattern_test"] for a in res["areas"] if a["province"] == "Eastern Cape")


def test_a_newly_published_pin_adds_its_area_on_the_next_run(tmp_path):
    index = _copy_signals(tmp_path)
    out = tmp_path / "coverage"
    first = ca.write_areas(ca.compute_areas(ca.published_pins(index)), out, index, today="2026-10-19")
    assert len(first["areas"]) == 10 and len(first["new_since_last_run"]) == 10   # first run: all new

    path, data = _nw_batch(index)
    new = copy.deepcopy(data["signals"][0])
    new["signal_id"] = str(uuid.uuid4())
    new["external_id"] = "test-rustenburg"
    new["title"] = "Tlhabane, Rustenburg (approx.): man shot dead"
    new["lat"], new["lng"] = -25.6544, 27.2559
    data["signals"].append(new)
    _save(index, path, data)

    second = ca.write_areas(ca.compute_areas(ca.published_pins(index)), out, index, today="2026-10-22")
    # Computing again the same day (compute, then sweep) keeps the flag.
    again = ca.write_areas(ca.compute_areas(ca.published_pins(index)), out, index, today="2026-10-22")
    assert again["new_since_last_run"] == second["new_since_last_run"]
    assert len(second["areas"]) == 11
    added = [a for a in second["areas"] if a["key"] in second["new_since_last_run"]]
    assert len(added) == 1 and added[0]["pins"][0]["signal_id"] == new["signal_id"]
    assert added[0]["province"] == "North West" and not added[0]["pattern_test"]
    assert "Rustenburg" in {p["name"] for p in added[0]["precincts"]}
    assert (out / "areas-latest.md").read_text().count("NEW since last run") == 1


def test_held_back_and_retracted_pins_make_no_coverage(tmp_path):
    index = _copy_signals(tmp_path)
    path, data = _nw_batch(index)
    ip = next(s for s in data["signals"] if "Ipelegeng" in s["title"])
    data["retractions"].append({"signal_id": ip["signal_id"], "reason": "test",
                                "retracted_at": "2026-10-20T08:00:00Z"})
    # A held place, even with a point, is not a pin.
    data.setdefault("held_back", []).append({"place": "Brits (test)", "lat": -25.63, "lng": 27.78,
                                             "reason": "held"})
    _save(index, path, data)
    pins = ca.published_pins(index)
    assert ip["signal_id"] not in {p["signal_id"] for p in pins}
    keys = {a["key"] for a in ca.compute_areas(pins)["areas"]}
    assert "north-west--schweizer-reneke" not in keys
    assert not any("brits" in k for k in keys) and len(keys) == 9


# ── Western Cape scoring stays apart ───────────────────────────────────────────

def test_areas_never_cross_provinces_and_only_wc_is_pattern_test(tiny_gaz):
    wc = _pin("wc", -26.0, 26.0, PATTERN_TEST_PROVINCE)
    nw = _pin("nw", -26.0, 26.05, "North West")           # 5 km away, other province
    assert len(ca.cluster_pins([wc, nw], 30)) == 2
    res = _compute([wc, nw], tiny_gaz)
    flags = {a["pins"][0]["signal_id"]: a["pattern_test"] for a in res["areas"]}
    assert flags == {"wc": True, "nw": False}
    for a in res["areas"]:
        assert a["collected_by"] == ("locked Sprint 5 Western Cape pass" if a["pattern_test"] else "coverage sweep")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_sweep_skips_wc_areas_marks_nothing_for_scoring_and_never_writes_sprint5(tiny_gaz, tmp_path):
    res = _compute([_pin("wc", -26.0, 26.0, PATTERN_TEST_PROVINCE), _pin("nw", -26.0, 28.0, "North West")], tiny_gaz)
    calls = []

    def fake(q, start, end):
        calls.append(q)
        return [{"title": "Man shot dead in Beta", "outlet": "X", "link": "https://x.test/1",
                 "published_sast": "2026-10-19 10:00"}]

    s5 = ROOT / "private" / "sprint5"
    before = {str(p.relative_to(s5)): _sha(p) for p in s5.rglob("*") if p.is_file()} if s5.exists() else {}
    start = datetime(2026, 10, 19, tzinfo=SAST)
    rep = ca.sweep(res, start, start + timedelta(days=3), tmp_path / "run", 30, fake, known={})
    by = {a["pattern_test"]: a for a in rep["areas"]}
    assert by[True]["status"] == "skipped" and by[True]["items"] == []
    assert by[False]["status"] == "complete" and len(by[False]["items"]) == 1
    assert rep["feeds_scoring"] is False
    assert all(a["feeds_scoring"] is False for a in rep["areas"])
    assert all(i["feeds_scoring"] is False for a in rep["areas"] for i in a["items"])
    assert (tmp_path / "run" / "sweep.json").exists()
    with pytest.raises(ValueError):
        ca.sweep(res, start, start + timedelta(days=3), s5 / "coverage", 30, fake, known={})
    with pytest.raises(ValueError):
        ca.write_areas(res, ROOT / "docs" / "coverage")
    after = {str(p.relative_to(s5)): _sha(p) for p in s5.rglob("*") if p.is_file()} if s5.exists() else {}
    assert before == after


def test_sweep_marks_links_already_on_a_pin(tiny_gaz, tmp_path):
    res = _compute([_pin("nw", -26.0, 28.0, "North West")], tiny_gaz)
    fake = lambda q, s, e: [{"title": "t", "link": "https://x.test/old/"}, {"title": "u", "link": "https://x.test/new"}]
    start = datetime(2026, 10, 19, tzinfo=SAST)
    rep = ca.sweep(res, start, start + timedelta(days=1), tmp_path, 30, fake,
                   known={"https://x.test/old": "pin abc"})
    items = rep["areas"][0]["items"]
    assert [i["already_known"] for i in items] == ["pin abc", None]
    assert rep["areas"][0]["reading"] == "1 found"


def test_same_story_under_two_links_is_one_item(tiny_gaz, tmp_path):
    res = _compute([_pin("nw", -26.0, 28.0, "North West")], tiny_gaz)
    fake = lambda q, s, e: [
        {"title": "Two arrested after murders of 2 men in Beta - EWN", "link": "https://g.test/a"},
        {"title": "Two arrested after murders of 2 men in Beta - EWN", "link": "https://g.test/b"}]
    start = datetime(2026, 10, 19, tzinfo=SAST)
    known = {ca.title_key("Two arrested after murders of 2 men in Beta - EWN"): "earlier pass: x"}
    rep = ca.sweep(res, start, start + timedelta(days=1), tmp_path, 30, fake, known=known)
    items = rep["areas"][0]["items"]
    assert len(items) == 1 and items[0]["already_known"] == "earlier pass: x"
    assert rep["areas"][0]["reading"] == "none found (search complete)"


def test_known_links_include_current_pin_sources():
    known = ca.known_links()
    assert any(v.startswith("pin ") for v in known.values())
    assert len(known) >= 28


# ── Time limits ─────────────────────────────────────────────────────────────────────

def _hang_in_beta(q, start, end):
    if q.startswith('"Beta"'):
        time.sleep(60)
    return []


def test_a_slow_area_times_out_alone_and_reads_inconclusive(tiny_gaz, tmp_path):
    res = _compute([_pin("a", -26.0, 26.0, "North West"), _pin("b", -26.0, 28.0, "North West")], tiny_gaz)
    start = datetime(2026, 10, 19, tzinfo=SAST)
    t0 = time.monotonic()
    rep = ca.sweep(res, start, start + timedelta(days=1), tmp_path, 2, _hang_in_beta, known={})
    assert time.monotonic() - t0 < 20
    st = {a["key"]: a for a in rep["areas"]}
    beta = next(v for k, v in st.items() if "beta" in k)
    alpha = next(v for k, v in st.items() if "alpha" in k)
    assert beta["status"] == "timed_out" and "inconclusive" in beta["reading"]
    assert alpha["status"] == "complete" and alpha["reading"] == "none found (search complete)"
    assert rep["timed_out"] == [beta["key"]]
    assert "Timed out" in (tmp_path / "sweep-log.md").read_text()


def test_count_reading_only_trusts_complete_searches():
    assert ca.count_reading("complete", 0) == "none found (search complete)"
    for status in ("timed_out", "error", "partial"):
        assert ca.count_reading(status, 0).startswith("inconclusive")


def test_a_failing_query_does_not_end_the_area(tiny_gaz, tmp_path):
    res = _compute([_pin("a", -26.0, 26.0, "North West")], tiny_gaz)

    def flaky(q, s, e):
        if "polao" in q:
            raise TimeoutError("read timed out")
        return []

    start = datetime(2026, 10, 19, tzinfo=SAST)
    rep = ca.sweep(res, start, start + timedelta(days=1), tmp_path, 10, flaky, known={})
    a = rep["areas"][0]
    assert a["status"] == "partial" and a["query_errors"] and a["queries_done"] > 0
    assert a["reading"].startswith("inconclusive")
