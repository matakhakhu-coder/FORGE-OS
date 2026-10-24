"""Time-aware pins, Batch 4: monthly archive files.

Every pin gets a permanent home in its incident month (South African time),
in a content-hashed GeoJSON file under dist/archive/, with an index of months,
a short pin-to-month lookup (its search list was dropped in Batch 6). The page's map list is the
live set: this month's and last month's pins. The 200-pin limit is gone.
Nothing reads the month files in the browser yet (Batch 5's loader does).

The build tests use the same route as tests/test_checked_signals.py: an empty
database, one import of docs/signals, tools/publish.py main() into a temp dist.
A build "on" another day replaces map_archive.build_today.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402

from tests.test_checked_signals import (ALL_PINS, EPPING, _copy_signals, _edit_batch,  # noqa: E402
                                        _fresh_db, _publish_to)
from tests.test_publish_deploy import _git, repo  # noqa: E402,F401  (repo: fixture)


def _build(tmp_path, monkeypatch, today: date | None = None):
    from tools.checked_signals import run_import
    if today is not None:
        monkeypatch.setattr(ma, "build_today", lambda: today)
    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.close()
    run_import(db_file, write=True, out=lambda m: None)
    return _publish_to(tmp_path, monkeypatch, db_file), db_file


@pytest.fixture(scope="module")
def built_oct(tmp_path_factory):
    """Built on 24 October 2026, the day Batch 4 was written."""
    with pytest.MonkeyPatch.context() as mp:
        dist, _ = _build(tmp_path_factory.mktemp("b4oct"), mp, date(2026, 10, 24))
    return dist


@pytest.fixture(scope="module")
def built_dec(tmp_path_factory):
    """Built on 15 December 2026: October is no longer a live month."""
    with pytest.MonkeyPatch.context() as mp:
        dist, _ = _build(tmp_path_factory.mktemp("b4dec"), mp, date(2026, 12, 15))
    return dist


def _record_ids(dist: Path) -> list[str]:
    data = json.loads((dist / "records.json").read_text(encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) else data
    return [r["signal_id"] for r in recs]


def _month_ids(dist: Path) -> list[str]:
    return [e["signal_id"] for es in ma.month_entries(dist).values() for e in es]


def _active_ids() -> set[str]:
    from tools.checked_signals import load_index
    return {s["signal_id"] for s in load_index().active}


# ── Months in South African time ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("incident_start, month", [
    ("2026-10-31", "2026-10"),                     # date only: that local day
    ("2026-11-01", "2026-11"),
    ("2026-10-31T21:59:59Z", "2026-10"),           # 23:59:59 in South Africa
    ("2026-10-31T22:00:00Z", "2026-11"),           # midnight in South Africa
    ("2026-10-31T23:30:00Z", "2026-11"),
    ("2026-11-01T00:30:00+02:00", "2026-11"),
    ("2026-10-31T23:59:00+02:00", "2026-10"),
    ("2026-10-31T23:59:00", "2026-10"),            # no offset: read as South African time
    ("2026-12-31T22:15:00Z", "2027-01"),           # year boundary
])
def test_month_boundary_is_south_african_time(incident_start, month):
    assert ma.month_of({"signal_id": "x", "incident_start": incident_start}) == month


def test_month_uses_incident_start_not_the_utc_incident_at():
    # A date-only incident on 1 November has incident_at 31 Oct 22:00 UTC.
    e = {"signal_id": "x", "incident_start": "2026-11-01", "incident_at": "2026-10-31T22:00:00Z",
         "published_at": "2026-11-03T08:00:00Z"}
    assert ma.month_of(e) == "2026-11"


def test_month_falls_back_for_a_pin_without_incident_start():
    assert ma.month_of({"signal_id": "x", "published_at": "2026-09-30T23:00:00Z"}) == "2026-10"
    with pytest.raises(ma.ArchiveError):
        ma.month_of({"signal_id": "abcdef12-0", "title": "no date"})


def test_live_months_are_last_month_and_this_month():
    assert ma.live_months(date(2026, 10, 24)) == ("2026-09", "2026-10")
    assert ma.live_months(date(2026, 11, 1)) == ("2026-10", "2026-11")
    assert ma.live_months(date(2027, 1, 5)) == ("2026-12", "2027-01")


def test_the_30_day_window_always_fits_in_the_live_months():
    d = date(2026, 1, 1)
    while d < date(2029, 1, 1):
        live = set(ma.live_months(d))
        for back in range(ma.WINDOW_DAYS + 1):
            assert ma.month_key(d - timedelta(days=back)) in live, d
        assert len(live) == 2 or (len(live) == 3 and d.month == 3 and d.day <= 2), d
        d += timedelta(days=1)


def test_early_march_reaches_back_into_january():
    assert ma.live_months(date(2027, 3, 1)) == ("2027-01", "2027-02", "2027-03")
    assert ma.live_months(date(2027, 3, 3)) == ("2027-02", "2027-03")


# ── Writing the files (made-up pins) ────────────────────────────────────────────────────────────────────────

def _pin(n: int, incident_start: str, **kw) -> dict:
    return {"signal_id": f"{n:08x}-0000-4000-8000-000000000000", "title": f"pin {n}",
            "lat": -33.9 - n / 1000, "lng": 18.5 + n / 1000, "stream": "crime",
            "incident_start": incident_start, "published_at": "2026-12-01T08:00:00Z", **kw}


PINS = [_pin(1, "2026-09-30"), _pin(2, "2026-10-01"), _pin(3, "2026-10-31T22:30:00Z"),
        _pin(4, "2026-11-15"), _pin(5, "2026-12-02"), _pin(6, "2026-08-09")]


def _write(tmp_path, pins, today=date(2026, 12, 10), coverage=None):
    (tmp_path / "index.html").write_text("<script>\nvar RAW = []; \n</script>")
    live, idx = ma.write_archive(tmp_path, pins, today, coverage or {})
    html = "<script>\nvar RAW = " + json.dumps(map_data.map_list(live)) + ";\n</script>"
    (tmp_path / "index.html").write_text(html)
    return live, idx


def test_each_pin_lands_in_exactly_one_month(tmp_path):
    live, idx = _write(tmp_path, PINS)
    months = ma.month_entries(tmp_path)
    assert {k: sorted(e["title"] for e in v) for k, v in months.items()} == {
        "2026-08": ["pin 6"], "2026-09": ["pin 1"], "2026-10": ["pin 2"],
        "2026-11": ["pin 3", "pin 4"], "2026-12": ["pin 5"]}
    assert sorted(p["title"] for p in live) == ["pin 3", "pin 4", "pin 5"]
    assert idx["live_months"] == ["2026-11", "2026-12"] and idx["count"] == 6
    assert [r["closed"] for r in idx["months"]] == [True, True, True, True, False]
    assert idx["pins"]["00000003"] == "2026-11"
    assert sorted(idx["pins"]) == sorted(f"{n:08x}" for n in range(1, 7))
    assert "search" not in idx       # Batch 6: site search covers every pin
    assert ma.problems(tmp_path, expected_ids={p["signal_id"] for p in PINS}) == []


def test_month_files_are_geojson_named_by_their_hash(tmp_path):
    _, idx = _write(tmp_path, PINS)
    for row in idx["months"]:
        body = (tmp_path / row["file"]).read_bytes()
        assert row["file"] == f"archive/{row['month']}.{ma.content_hash(body)}.json"
        doc = json.loads(body)
        assert doc["type"] == "FeatureCollection" and doc["count"] == row["count"]
        for f in doc["features"]:
            assert f["geometry"] == {"type": "Point", "coordinates": [f["properties"]["lng"], f["properties"]["lat"]]}
            assert set(f["properties"]) == set(map_data.MAP_FIELDS)


def test_a_change_renames_only_its_own_month(tmp_path):
    _, before = _write(tmp_path, PINS)
    old = {r["month"]: r["file"] for r in before["months"]}
    corrected = [dict(p, title="pin 4, corrected") if p["title"] == "pin 4" else p for p in PINS]
    _, after = _write(tmp_path, corrected)
    new = {r["month"]: r["file"] for r in after["months"]}
    assert new["2026-11"] != old["2026-11"]
    assert {k: v for k, v in new.items() if k != "2026-11"} == {k: v for k, v in old.items() if k != "2026-11"}
    assert not (tmp_path / old["2026-11"]).exists()          # no stale copy left behind
    assert ma.problems(tmp_path) == []


def test_a_late_pin_for_a_closed_month_goes_into_that_month(tmp_path):
    _, before = _write(tmp_path, PINS)
    _, after = _write(tmp_path, PINS + [_pin(7, "2026-09-12")])
    rows = {r["month"]: r for r in after["months"]}
    assert rows["2026-09"]["count"] == 2 and after["pins"]["00000007"] == "2026-09"
    assert rows["2026-09"]["file"] != {r["month"]: r for r in before["months"]}["2026-09"]["file"]


def test_the_build_day_does_not_change_a_month_file(tmp_path):
    _, nov = _write(tmp_path, PINS, today=date(2026, 11, 20))
    _, mar = _write(tmp_path, PINS, today=date(2027, 3, 1))
    assert [r["file"] for r in nov["months"]] == [r["file"] for r in mar["months"]]


def test_coverage_note_written_or_default(tmp_path):
    _, idx = _write(tmp_path, PINS, coverage={"2026-10": "October was patchy."})
    rows = {r["month"]: r for r in idx["months"]}
    assert rows["2026-10"]["coverage_note"] == "October was patchy."
    assert rows["2026-10"]["coverage_note_source"] == "written"
    assert rows["2026-11"]["coverage_note"] == ma.DEFAULT_COVERAGE_NOTE
    assert rows["2026-11"]["coverage_note_source"] == "default"
    _, again = _write(tmp_path, PINS, coverage={"2026-10": "October was patchy; reworded."})
    assert {r["month"]: r for r in again["months"]}["2026-10"]["file"] != rows["2026-10"]["file"]


def test_the_repo_coverage_file_has_a_note_for_october():
    notes = ma.load_coverage()
    assert "2026-10" in notes and "not a crime rate" in notes["2026-10"].lower()


# ── The after-build check catches each break ──────────────────────────────────────────────────────────────────

def _rewrite_month(tmp_path, idx, month, fn, rename=True):
    """Edit a month file in place; with rename, give it the right hash name
    again (so only the edit itself is wrong)."""
    row = next(r for r in idx["months"] if r["month"] == month)
    path = tmp_path / row["file"]
    doc = json.loads(path.read_bytes())
    fn(doc)
    body = ma._dump(doc)
    path.unlink()
    name = f"{month}.{ma.content_hash(body)}.json" if rename else path.name
    (tmp_path / "archive" / name).write_bytes(body)
    row["file"] = f"archive/{name}"
    (tmp_path / "archive" / "index.json").write_bytes(ma._dump(idx))


def test_problems_pin_in_two_months(tmp_path):
    _, idx = _write(tmp_path, PINS)
    extra = ma.month_entries(tmp_path)["2026-09"][0]
    _rewrite_month(tmp_path, idx, "2026-10", lambda d: (d["features"].append(ma._feature(extra)),
                                                     d.update(count=d["count"] + 1)))
    idx["months"][2]["count"] += 1
    (tmp_path / "archive" / "index.json").write_bytes(ma._dump(idx))
    probs = ma.problems(tmp_path)
    assert any("in two months" in p for p in probs)


def test_problems_pin_filed_in_the_wrong_month(tmp_path):
    _, idx = _write(tmp_path, PINS)
    _rewrite_month(tmp_path, idx, "2026-10", lambda d: d["features"][0]["properties"].update(
        incident_start="2026-09-29"))
    assert any("incident month is 2026-09" in p for p in ma.problems(tmp_path))


def test_problems_file_changed_without_a_new_name(tmp_path):
    _, idx = _write(tmp_path, PINS)
    _rewrite_month(tmp_path, idx, "2026-08", lambda d: d["features"][0]["properties"].update(title="edited"),
                   rename=False)
    assert any("does not match the hash" in p for p in ma.problems(tmp_path))


def test_problems_missing_and_leftover_files(tmp_path):
    _, idx = _write(tmp_path, PINS)
    (tmp_path / idx["months"][0]["file"]).unlink()
    (tmp_path / "archive" / "2025-01.0123456789.json").write_text("{}")
    probs = ma.problems(tmp_path)
    assert any("is missing" in p for p in probs)
    assert any("leftover file" in p and "2025-01" in p for p in probs)


def test_problems_live_list_disagrees_with_the_months(tmp_path):
    live, idx = _write(tmp_path, PINS)
    old = map_data.map_entry(next(p for p in PINS if p["title"] == "pin 6"))
    html = "<script>\nvar RAW = " + json.dumps(map_data.map_list(live[1:]) + [old]) + ";\n</script>"
    (tmp_path / "index.html").write_text(html)
    probs = ma.problems(tmp_path)
    assert any("belongs on the live list but is missing" in p for p in probs)
    assert any("its month 2026-08 is not live" in p for p in probs)


def test_problems_expected_set(tmp_path):
    _write(tmp_path, PINS)
    want = {p["signal_id"] for p in PINS[1:]} | {"feedface-0000"}
    probs = ma.problems(tmp_path, expected_ids=want)
    assert any("not in any month file: feedface" in p for p in probs)
    assert any("not an active pin: 00000001" in p for p in probs)


# ── The real build ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("which", ["built_oct", "built_dec"])
def test_live_list_plus_months_hold_exactly_the_records(which, request):
    dist = request.getfixturevalue(which)
    records = _record_ids(dist)
    months = _month_ids(dist)
    live = [e["signal_id"] for e in map_data.read_map_list(dist / "index.html")]
    assert len(records) == ALL_PINS == len(set(records))
    assert len(months) == len(set(months)) == ALL_PINS             # each pin in exactly one month
    assert len(live) == len(set(live))
    assert set(live) | set(months) == set(records) == _active_ids()  # no gaps, nothing extra
    assert set(live) <= set(months)
    assert ma.problems(dist, expected_ids=_active_ids()) == []
    assert map_data.problems(dist) == []


def test_october_build_keeps_every_pin_live(built_oct):
    """Today all 72 pins are October incidents, so the page is unchanged."""
    idx = ma.read_index(built_oct)
    assert idx["live_months"] == ["2026-09", "2026-10"]
    assert [r["month"] for r in idx["months"]] == ["2026-10"] and idx["months"][0]["closed"] is False
    assert len(map_data.read_map_list(built_oct / "index.html")) == ALL_PINS
    assert idx["months"][0]["coverage_note_source"] == "written"


def test_december_build_moves_october_off_the_live_list(built_dec):
    idx = ma.read_index(built_dec)
    assert idx["live_months"] == ["2026-11", "2026-12"]
    assert map_data.read_map_list(built_dec / "index.html") == []
    assert idx["months"][0]["month"] == "2026-10" and idx["months"][0]["closed"] is True
    # Archived pins keep their detail files, for Batch 5's ?signal= route.
    assert len(list((built_dec / map_data.PIN_DIR).glob("*.json"))) == ALL_PINS


def test_month_file_is_the_same_whichever_day_it_is_built(built_oct, built_dec):
    a, b = ma.read_index(built_oct), ma.read_index(built_dec)
    assert a["months"][0]["file"] == b["months"][0]["file"]
    assert (built_oct / a["months"][0]["file"]).read_bytes() == (built_dec / b["months"][0]["file"]).read_bytes()


def test_a_december_build_passes_the_nothing_missing_check(built_dec):
    """Batch 4 alone failed this (October only in its month file); Batch 5's
    loader reached archived pins by map, link, search and Watchlist, and
    Batch 6's Area check loads month files too, so a December build now
    passes every route. Batches 4 to 9 still go live together."""
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "nothing_missing.py"), "--dist", str(built_dec)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:]
    assert "MISSING" not in r.stdout and "PASS" in r.stdout


def test_october_build_passes_the_nothing_missing_check(built_oct):
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "nothing_missing.py"), "--dist", str(built_oct)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:]


def test_page_is_unchanged_from_batch3_today(built_oct):
    """The page carries the same map list it did in Batch 3 while every pin
    is live. (Batch 4 also had no template change; Batch 5 adds the shared
    loader, which reaches archive/ from static/js/forge-pins.js, so the page
    only gains that script tag.)"""
    html = (built_oct / "index.html").read_text(encoding="utf-8")
    assert 'src="static/js/forge-pins.js"' in html
    entries = map_data.read_map_list(built_oct / "index.html")
    assert all(set(e) == set(map_data.MAP_FIELDS) for e in entries)


def test_retraction_shows_up_in_its_month_after_a_rebuild(tmp_path, monkeypatch):
    from tools.checked_signals import load_index, import_checked
    monkeypatch.setattr(ma, "build_today", lambda: date(2026, 12, 15))
    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    import_checked(conn, load_index(), write=True)
    conn.commit()
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    before = ma.read_index(dist)
    assert before["pins"][EPPING[:8]] == "2026-10"
    old_file = before["months"][0]["file"]

    index = _copy_signals(tmp_path)
    _edit_batch(index, 0, lambda d: d["retractions"].append(
        {"signal_id": EPPING, "reason": "Test: not a murder after all.", "retracted_at": "2026-12-01T08:00:00Z"}))
    cs = load_index(index)
    import_checked(conn, cs, write=True)
    conn.commit()
    conn.close()
    dist = _publish_to(tmp_path, monkeypatch, db_file, index=index)
    after = ma.read_index(dist)
    row = after["months"][0]
    assert row["month"] == "2026-10" and row["count"] == ALL_PINS - 1
    assert row["file"] != old_file and not (dist / old_file).exists()
    assert EPPING[:8] not in after["pins"] and "search" not in after
    assert EPPING not in _month_ids(dist)
    assert not (dist / map_data.PIN_DIR / map_data.detail_name(EPPING)).exists()
    assert ma.problems(dist, expected_ids={s["signal_id"] for s in cs.active}) == []


def test_publish_refuses_when_a_pin_misses_its_month(tmp_path, monkeypatch):
    import tools.publish as pub
    real = ma.write_archive

    def drop_one(dist, markers, today=None, coverage=None):
        live, idx = real(dist, markers, today, coverage)
        row = idx["months"][0]
        path = Path(dist) / row["file"]
        doc = json.loads(path.read_bytes())
        doc["features"] = doc["features"][1:]
        path.write_bytes(ma._dump(doc))
        return live, idx

    monkeypatch.setattr(ma, "write_archive", drop_one)
    with pytest.raises(pub.CheckedGuardError) as exc:
        _build(tmp_path, monkeypatch)
    assert exc.value.code == 3


def test_the_200_pin_limit_is_gone(tmp_path, monkeypatch):
    import tools.publish as pub
    conn, _ = _fresh_db(tmp_path, monkeypatch)
    info = list(conn.execute("PRAGMA table_info(signals)"))
    cols = {r[1] for r in info}
    required = {r[1]: r[2] for r in info if r[3] and r[4] is None and not r[5]}  # NOT NULL, no default
    for n in range(260):
        row = {"signal_id": f"{n:08x}-0000-4000-8000-000000000000", "title": f"made-up {n}",
               "content": "", "stream": "CRIME_INTEL", "source": "test", "lat": -26.0, "lng": 28.0,
               "external_id": f"made-up-{n}",
               "published_at": f"2026-10-{1 + n % 28:02d}T08:00:00Z", "metadata_json": "{}"}
        for k, typ in required.items():
            row.setdefault(k, 0 if "INT" in typ.upper() or "REAL" in typ.upper() else f"x{n}")
        row = {k: v for k, v in row.items() if k in cols}
        conn.execute(f"INSERT INTO signals ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     tuple(row.values()))
    conn.commit()
    assert len(pub._fetch_signals(conn)) == 260


def test_deploy_stages_new_archive_files_despite_a_global_dist_ignore(repo):
    import tools.publish as pub
    w, _ = repo
    (w / "dist" / "archive").mkdir()
    (w / "dist" / "archive" / "index.json").write_text("{}")
    (w / "dist" / "archive" / "2026-10.0123456789.json").write_text("{}")
    pub._stage_deploy_files(str(w))
    staged = _git(w, "diff", "--cached", "--name-only").split()
    assert "dist/archive/index.json" in staged and "dist/archive/2026-10.0123456789.json" in staged


def test_month_files_are_cached_for_a_year_and_the_index_is_not():
    """Hashed month files never change under one name, so they may be cached
    for a year. archive/index.json is not hashed and keeps the default."""
    import re
    routes = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]
    hdr = [r for r in routes if "archive" in r.get("src", "")]
    assert len(hdr) == 1 and hdr[0].get("continue") is True
    assert "immutable" in hdr[0]["headers"]["Cache-Control"] and "31536000" in hdr[0]["headers"]["Cache-Control"]
    rx = re.compile("^" + hdr[0]["src"] + "$")
    assert rx.match("/archive/2026-10.6dc72d7cd3.json")
    assert not rx.match("/archive/index.json")
    # It sits before the catch-all that serves dist/.
    assert routes.index(hdr[0]) < [i for i, r in enumerate(routes) if r.get("src") == "/(.*)"][0]
