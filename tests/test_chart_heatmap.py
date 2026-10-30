"""Interactive charts Batch 8: the town by incident date heat map.

Towns come from tools/record_towns.py (the coverage-area naming rule plus
the hand-kept fixes in docs/signals/record-towns.json), for the charts only;
Python owns the counting; a publish stops if a record has no town; tapping a
cell lists its records and filters the map to that town and day.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import ALL_PINS, _publish_to  # noqa: E402
from tests.test_open_records import _imported  # noqa: E402
from tests.test_record_charts import _chart_table, _mixed, _page, _rec, _section  # noqa: E402
from tools import record_charts as rc  # noqa: E402
from tools import record_towns as rt  # noqa: E402

JS = ROOT / "publisher" / "static" / "js" / "record-charts.js"
TPL = ROOT / "publisher" / "templates" / "map.html"
LIVE = ROOT / "dist" / "records.json"


def _towned(recs, towns=("Cape Town", "Musina", "Polokwane", "Giyani")):
    return [dict(r, town=towns[i % len(towns)]) for i, r in enumerate(recs)]


# ── Town labels on the 78 published records ─────────────────────────────────────

@pytest.fixture(scope="module")
def live():
    if not LIVE.exists():
        pytest.skip("no published records.json in dist/")
    recs = json.loads(LIVE.read_text(encoding="utf-8"))["records"]
    if len(recs) != 78:
        pytest.skip("dist/records.json is not the 78-record publish")
    return recs


def test_hand_fixes_take_effect_on_the_published_records(live):
    towned, problems, warnings = rt.towns_for_charts(live)
    assert problems == [] and warnings == []
    c = Counter(r["town"] for r in towned)
    assert sum(c.values()) == 78 and len(c) == 37   # + Bloemfontein (Bochabela, Phase 7, Wilgehof), 30 Oct
    # the approved fixes
    for gone in ("Westenburg", "Vuwani", "Mmabatho", "Groot-Brakrivier", "Somerset West"):
        assert gone not in c, gone
    assert c["Cape Town"] == 26 and c["Polokwane"] == 4 and c["Mahikeng"] == 2
    assert c["Thohoyandou"] == 1 and c["Great Brak River"] == 1
    # kept as the rule gives them
    for kept in ("Musina", "East London", "Hermanus", "Ilinge"):
        assert c[kept] >= 1, kept
    assert c["Musina"] == 4 and c["Gqeberha"] == 4


def test_every_override_names_a_published_record(live):
    ov = rt.load_overrides()
    ids = {r["signal_id"] for r in live}
    for sid in ov:
        assert any(i.startswith(sid) for i in ids), sid


def test_charts_get_towns_but_the_open_data_does_not(live):
    towned, _, _ = rt.towns_for_charts(live)
    assert all("town" not in r for r in live)
    assert all(r["town"] for r in towned)


# ── Publish check: no record without a town ──────────────────────────────────────

def test_check_towns_stops_when_a_record_has_no_town():
    recs = _towned([_rec("a0000001"), _rec("b0000002")])
    rt.check_towns(recs, [])                                   # passes
    recs[1]["town"] = ""
    with pytest.raises(SystemExit, match="no town"):
        rt.check_towns(recs, [])
    with pytest.raises(SystemExit, match="no town"):
        rt.check_towns(_towned([_rec("a0000001")]), ["a0000001: no town (no place or precinct within 25 km)"])


def test_publish_stops_if_a_record_has_no_town(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()

    def no_town(records, **kw):
        return [dict(r, town="") for r in records], ["x: no town"], []
    monkeypatch.setattr(rt, "towns_for_charts", no_town)
    with pytest.raises(SystemExit, match="no town"):
        _publish_to(tmp_path, monkeypatch, db_file)


# ── Counting (Python owns it) ─────────────────────────────────────────────────────

def test_heat_map_follows_the_incident_line_rules():
    recs = _towned(_mixed())
    charts = rc.chart_counts(recs)
    h, inc = _page(charts, "town")["data"], _page(charts, "incident")["data"]
    assert [d["date"] for d in h["days"]] == [d["date"] for d in inc["days"]]
    assert h["undated"] == inc["undated"] and h["later_step"] == inc["later_step"]
    per_day = [sum(r["days"][i] for r in h["rows"]) for i in range(len(h["days"]))]
    assert per_day == [d["count"] for d in inc["days"]]
    for r in h["rows"]:
        assert r["count"] == sum(r["days"]) + r["undated"] + r["later_step"]
    assert sum(r["count"] for r in h["rows"]) == len(recs) == rc.chart_total(_page(charts, "town"))
    assert h["max"] == max(v for r in h["rows"] for v in r["days"] + [r["undated"], r["later_step"]])
    # towns by records, then name
    counts = [r["count"] for r in h["rows"]]
    assert counts == sorted(counts, reverse=True)


def test_a_later_step_or_report_date_is_never_put_on_a_day():
    recs = [dict(_rec("a0000001", incident_date_basis="later_step", incident_start="2026-10-07"), town="Musina"),
            dict(_rec("b0000002", incident_date_basis="report", incident_start="2026-10-07"), town="Musina"),
            dict(_rec("c0000003", incident_start="2026-10-07"), town="Musina")]
    h = _page(rc.chart_counts(recs), "town")["data"]
    (row,) = h["rows"]
    assert (sum(row["days"]), row["undated"], row["later_step"]) == (1, 1, 1)


@pytest.mark.parametrize("on", [None, {"CRIME_INTEL"}, {"INFRASTRUCTURE", "PRIORITY"}])
def test_table_reproduces_the_heat_map_and_each_cell_list_at_every_step(on):
    recs = _towned(_mixed())
    t = rc.timeline_table(recs)
    ax = t["axes"]
    assert ax["town"] == [r["label"] for r in _page(rc.chart_counts(recs), "town")["data"]["rows"]]
    for k, st in enumerate(t["steps"]):
        c = rc.counts_from_table(t, k, on)["town"]
        if on is None:
            col = {c: i for i, c in enumerate(t["columns"])}
            live_ids = {row[col["id"]] for row in t["rows"] if row[col["step"]] <= k}
            cut = [r for r in recs if any(r["signal_id"].startswith(i) for i in live_ids)]
            assert len(cut) == len(live_ids)
            want = {r["label"]: r for r in _page(rc.chart_counts(cut), "town")["data"]["rows"]}
            assert sum(c["rows"]) == st["total"]
            for i, town in enumerate(ax["town"]):
                w = want.get(town)
                assert c["rows"][i] == (w["count"] if w else 0)
        for i, town in enumerate(ax["town"]):
            for j, d in enumerate(ax["incident_days"]):
                assert len(rc.ids_from_table(t, k, "town", f"{town}|{d}", on)) == c["cells"][i][j]
            assert len(rc.ids_from_table(t, k, "town", f"{town}|undated", on)) == c["undated"][i]
            assert len(rc.ids_from_table(t, k, "town", f"{town}|later_step", on)) == c["later_step"][i]
            assert len(rc.ids_from_table(t, k, "town", f"{town}|*", on)) == c["rows"][i]


# ── The built page ─────────────────────────────────────────────────────────────

@pytest.fixture
def built(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    return _publish_to(tmp_path, monkeypatch, db_file)


def test_build_prints_the_heat_map_table_as_the_no_script_fallback(built):
    html = (built / "index.html").read_text()
    payload = json.loads((built / "records.json").read_text())
    towned, problems, _ = rt.towns_for_charts(payload["records"])
    assert not problems
    h = _page(rc.chart_counts(towned), "town")["data"]
    sec = _section(html, "town")
    assert '<table class="gp-heat"' in sec and "gp-heat__btn" not in sec   # buttons come from the script
    assert sec.count('<tr>') == len(h["rows"]) + 1                          # header + one per town
    names = re.findall(r'<span class="gp-heat__name">([^<]+)</span>', sec)
    assert names == [r["label"] for r in h["rows"]]
    totals = [int(n) for n in re.findall(r'<span class="gp-heat__total">(\d+)</span>', sec)]
    assert totals == [r["count"] for r in h["rows"]] and sum(totals) == ALL_PINS
    shown = [int(n) for n in re.findall(r'<span class="gp-heat__n">(\d+)</span>', sec)]
    assert sum(shown) == ALL_PINS
    assert sec.count('class="gp-heat__cell') == len(h["rows"]) * (len(h["days"]) + 2)
    assert f"{ALL_PINS - h['undated'] - h['later_step']} on a day" in sec and "records" in sec.lower()
    t = _chart_table(html)
    assert t["axes"]["town"] == names


def test_town_page_is_last_with_its_switches_slider_and_list(built):
    html = (built / "index.html").read_text()
    assert re.findall(r'id="gp-tab-([a-z]+)"', html)[-1] == "town"
    sec = _section(html, "town")
    assert "data-gp-list" in sec and 'type="range"' in sec and "Where FORGE has checked so far" in sec


def test_script_filters_the_map_by_cell_and_keeps_the_rules():
    js = JS.read_text(encoding="utf-8")
    assert "M.setCell(" in js and "clearCell:" in js and "setupHeat();" in js
    tpl = TPL.read_text(encoding="utf-8")
    # the map side: the cell filter sits on top of the time filter, with a Clear button
    assert "setCell: function" in tpl and "C.clearCell" in tpl
    for bad in ("https://", "chart.js", "import ", "require("):
        assert bad not in js.lower(), bad
    # one tab stop on the grid; the arrow keys move
    assert "b.tabIndex = -1" in js and "ArrowDown" in js
    # no fixed pixel scroll trap: the table box does not stop the panel swipe
    css = tpl[tpl.index(".gp-heat-scroll {"):]
    assert "overscroll-behavior" not in css[:css.index("}")]
