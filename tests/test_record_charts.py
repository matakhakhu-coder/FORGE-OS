"""Graph panel pages and the fixed charts (tools/record_charts.py).

The charts count the same approved records as records.json, in the same
build, and the panel's Network page keeps its empty state instead of
inventing actors.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import ALL_PINS, _publish_to  # noqa: E402
from tests.test_open_records import _imported  # noqa: E402
from tools import record_charts as rc  # noqa: E402


def _rec(sid, **over):
    r = {"signal_id": sid, "incident_start": "2026-10-05", "incident_event": "occurred",
         "published_at": "2026-10-20T10:00Z", "stream": "Violent crime", "province": "Limpopo"}
    r.update(over)
    return r


def _page(charts, pid):
    return next(p for p in charts["pages"] if p["id"] == pid)


# ── Registry ────────────────────────────────────────────────────────────────────

def test_pages_are_fixed_and_in_swipe_order():
    assert [p["id"] for p in rc.CHART_PAGES] == ["incident", "added", "stream", "province"]
    # page 2 of the panel (first chart) is the incident-date line chart
    assert rc.CHART_PAGES[0]["kind"] == "line"
    assert all(p["kind"] in rc.RENDERERS for p in rc.CHART_PAGES)


def test_unknown_renderer_is_refused(monkeypatch):
    monkeypatch.setattr(rc, "CHART_PAGES", rc.CHART_PAGES + ({"id": "x", "kind": "pie"},))
    with pytest.raises(ValueError):
        rc._check_registry()


# ── Dates ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-10-05", "2026-10-05"),
    ("2026-10-05T23:30+02:00", "2026-10-05"),
    ("2026-10-05T22:30Z", "2026-10-06"),          # UTC late evening is the next SA day
    ("2026-10-05T21:59:00+00:00", "2026-10-05"),
    ("", None), (None, None), ("sometime", None), ("2026-13-01", None),
])
def test_sa_date(value, expected):
    d = rc.sa_date(value)
    assert (d.isoformat() if d else None) == expected


def test_incident_chart_uses_incident_date_never_publish_date():
    recs = [_rec("a", incident_start="2026-10-01", published_at="2026-10-22T10:00Z"),
            _rec("b", incident_start="2026-10-03", incident_event="found",
                 published_at="2026-10-22T10:00Z")]
    inc = _page(rc.chart_counts(recs), "incident")["data"]
    assert [(d["date"], d["count"]) for d in inc["days"]] == [
        ("2026-10-01", 1), ("2026-10-02", 0), ("2026-10-03", 1)]
    assert (inc["occurred"], inc["found"], inc["undated"]) == (1, 1, 0)
    assert "2026-10-22" not in {d["date"] for d in inc["days"]}


def test_missing_dates_are_counted_not_dropped_or_guessed():
    recs = [_rec("a"), _rec("b", incident_start="", published_at=""), _rec("c", incident_start=None)]
    charts = rc.chart_counts(recs)
    inc, added = _page(charts, "incident")["data"], _page(charts, "added")["data"]
    assert inc["undated"] == 2 and added["undated"] == 1
    for p in charts["pages"]:
        assert rc.chart_total(p) == 3, p["id"]


def test_report_dated_pins_are_not_put_on_the_incident_line():
    sid = next(iter(rc.REPORT_DATED))
    recs = [_rec("a", incident_start="2026-10-02"), _rec(sid, incident_start="2026-10-09")]
    charts = rc.chart_counts(recs)
    inc = _page(charts, "incident")["data"]
    assert [d["date"] for d in inc["days"]] == ["2026-10-02"]
    assert inc["undated"] == 1
    # it still counts everywhere else
    assert sum(d["count"] for d in _page(charts, "added")["data"]["days"]) == 2


def test_report_dated_list_matches_the_committed_records():
    payload = json.loads((ROOT / "dist" / "records.json").read_text())
    by_id = {r["signal_id"]: r for r in payload["records"]}
    for sid in rc.REPORT_DATED:
        assert sid in by_id, sid


def test_added_by_day_is_published_at_in_sa_time():
    recs = [_rec("a", published_at="2026-10-20T22:30Z"), _rec("b", published_at="2026-10-20T10:00Z")]
    added = _page(rc.chart_counts(recs), "added")["data"]
    assert [(d["date"], d["count"]) for d in added["days"]] == [("2026-10-20", 1), ("2026-10-21", 1)]


def test_duplicate_signal_id_is_refused():
    with pytest.raises(ValueError):
        rc.chart_counts([_rec("a"), _rec("a")])


def test_no_records_gives_empty_pages():
    charts = rc.chart_counts([])
    assert charts["total"] == 0
    for p in charts["pages"]:
        assert rc.chart_total(p) == 0
    assert _page(charts, "incident")["data"]["line"]["points"] == []


def test_line_points_stay_inside_the_drawing():
    recs = [_rec(str(i), incident_start=f"2026-10-{1 + i % 21:02d}") for i in range(40)]
    g = _page(rc.chart_counts(recs), "incident")["data"]["line"]
    assert len(g["points"]) == 21
    assert all(g["x0"] <= p["x"] <= g["x1"] and 0 <= p["y"] <= g["h"] for p in g["points"])
    assert 2 <= len(g["ticks"]) <= 6
    assert g["ticks"][0] is g["points"][0] and g["ticks"][-1] is g["points"][-1]


# ── The real build ───────────────────────────────────────────────────────────────

@pytest.fixture
def built(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    return _publish_to(tmp_path, monkeypatch, db_file)


def _section(html, pid):
    m = re.search(rf'<section class="gp-page[^"]*" id="gp-page-{pid}".*?</section>', html, re.S)
    assert m, pid
    return m.group(0)


def test_build_charts_match_records_json(built):
    html = (built / "index.html").read_text()
    payload = json.loads((built / "records.json").read_text())
    assert payload["count"] == ALL_PINS
    expected = rc.chart_counts(payload["records"])
    assert f"{ALL_PINS} approved records" in html

    # every chart page is in the panel, in order, after Network
    tabs = re.findall(r'id="gp-tab-([a-z]+)"', html)
    assert tabs == ["network", "incident", "added", "stream", "province"]

    # bars: the printed numbers are the counts from records.json
    for pid in ("stream", "province"):
        sec = _section(html, pid)
        shown = [int(n) for n in re.findall(r'<span class="gp-bar__n">(\d+)</span>', sec)]
        assert shown == [r["count"] for r in _page(expected, pid)["data"]["rows"]]
        assert sum(shown) == ALL_PINS
    sec = _section(html, "added")
    shown = [int(n) for n in re.findall(r'<span class="gp-col__n">(\d+)</span>', sec)]
    added = _page(expected, "added")["data"]
    assert sum(shown) + added["undated"] == ALL_PINS
    sec = _section(html, "incident")
    inc = _page(expected, "incident")["data"]
    assert sec.count('<circle class="gp-line__pt"') == len(inc["days"])
    assert f'{inc["occurred"]} occurred' in sec
    assert inc["occurred"] + inc["found"] + inc["undated"] == ALL_PINS
    assert inc["undated"] == len(rc.REPORT_DATED & {r["signal_id"] for r in payload["records"]})
    assert f'{inc["undated"]} date not stated' in sec


def test_build_charts_hold_no_record_text(built):
    html = (built / "index.html").read_text()
    payload = json.loads((built / "records.json").read_text())
    charts_html = "".join(_section(html, p["id"]) for p in rc.CHART_PAGES)
    for r in payload["records"]:
        assert r["title"] not in charts_html
        for url in filter(None, (r["source_urls"] or "").split(" | ")):
            assert url not in charts_html


def test_empty_network_keeps_its_empty_state(built):
    html = (built / "index.html").read_text()
    net = _section(html, "network")
    # nothing published as a case, so no network is drawn or loaded
    assert "0 actors &middot; 0 relationships" in html
    assert "No network to draw yet" in net
    assert 'id="ig-cy"' not in net
    assert "cytoscape.min.js" not in html
    assert 'data-gp-go="1"' in net


def test_network_page_kept_when_cases_are_published(tmp_path, monkeypatch):
    """With a node in the graph, page one is the Cytoscape network as before."""
    import tools.publish as pub
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    real = pub._build_graph_data if hasattr(pub, "_build_graph_data") else None
    if real is None:
        pytest.skip("graph loader name changed")
    monkeypatch.setattr(pub, "_build_graph_data", lambda *a, **k: {
        "nodes": [{"id": "n1", "name": "Test actor", "type": "person", "confidence": 0.9}],
        "edges": []})
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    html = (dist / "index.html").read_text()
    net = _section(html, "network")
    assert 'id="ig-cy"' in net and 'id="ig-search"' in html
    assert "cytoscape.min.js" in html
    assert "No network to draw yet" not in net


def test_publish_refuses_charts_that_disagree_with_records(tmp_path, monkeypatch):
    import tools.record_charts as mod
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    monkeypatch.setattr(mod, "chart_total", lambda page: -1)
    with pytest.raises(SystemExit):
        _publish_to(tmp_path, monkeypatch, db_file)
