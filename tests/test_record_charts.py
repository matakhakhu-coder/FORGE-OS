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
         "incident_date_basis": "incident", "published_at": "2026-10-20T10:00Z", "stream": "Violent crime", "province": "Limpopo"}
    r.update(over)
    return r


def _page(charts, pid):
    return next(p for p in charts["pages"] if p["id"] == pid)


# ── Registry ────────────────────────────────────────────────────────────────────

def test_pages_are_fixed_and_in_swipe_order():
    assert [p["id"] for p in rc.CHART_PAGES] == ["incident", "added", "stream", "province", "town"]
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
    recs = [_rec("a", incident_start="2026-10-02"),
            _rec("r", incident_start="2026-10-09", incident_date_basis="report")]
    charts = rc.chart_counts(recs)
    inc = _page(charts, "incident")["data"]
    assert [d["date"] for d in inc["days"]] == ["2026-10-02"]
    assert (inc["undated"], inc["later_step"]) == (1, 0)
    # it still counts everywhere else
    assert sum(d["count"] for d in _page(charts, "added")["data"]["days"]) == 2


def test_later_step_is_counted_apart_never_on_the_day_of_the_step():
    # a charge laid on 21 Oct in a case from July: not an incident on 21 Oct
    recs = [_rec("a", incident_start="2026-10-02"),
            _rec("c", incident_start="2026-10-21", incident_date_basis="later_step")]
    charts = rc.chart_counts(recs)
    inc = _page(charts, "incident")["data"]
    assert [d["date"] for d in inc["days"]] == ["2026-10-02"]
    assert (inc["undated"], inc["later_step"], inc["occurred"]) == (0, 1, 1)
    for p in charts["pages"]:
        assert rc.chart_total(p) == 2, p["id"]


@pytest.mark.parametrize("basis", [None, "", "guess"])
def test_unclassified_date_is_never_plotted(basis):
    r = _rec("u", incident_start="2026-10-09")
    r["incident_date_basis"] = basis
    inc = _page(rc.chart_counts([r]), "incident")["data"]
    assert inc["days"] == [] and inc["undated"] == 1


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
    assert tabs == ["network", "incident", "added", "stream", "province", "town"]

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
    assert inc["occurred"] + inc["found"] + inc["undated"] + inc["later_step"] == ALL_PINS
    bases = [r["incident_date_basis"] for r in payload["records"]]
    assert inc["undated"] == bases.count("report")
    assert inc["later_step"] == bases.count("later_step")
    assert f'{inc["undated"]} date not stated' in sec
    assert f'{inc["later_step"]} later step in an older case' in sec


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


# ── Timeline table for the slider (interactive charts Batch 1) ──────────────────

def test_timeline_one_step_per_publish_in_sa_time_order():
    recs = [_rec("a" * 8 + "1", published_at="2026-10-20T22:30Z"),     # 21 Oct 00:30 SAST
            _rec("b" * 8 + "1", published_at="2026-10-20T10:00Z"),
            _rec("c" * 8 + "1", published_at="2026-10-20T10:00Z")]
    t = rc.timeline_table(recs)
    assert [s["at"] for s in t["steps"]] == ["2026-10-20T12:00+02:00", "2026-10-21T00:30+02:00"]
    assert [s["label"] for s in t["steps"]] == ["20 Oct, 12:00 PM", "21 Oct, 12:30 AM"]
    assert [s["total"] for s in t["steps"]] == [2, 3]
    assert t["marks"] == [{"step": 0, "label": "20 Oct"}, {"step": 1, "label": "21"}]


def test_timeline_record_without_publish_time_joins_the_last_step():
    recs = [_rec("a1"), _rec("b1", published_at="")]
    t = rc.timeline_table(recs)
    assert t["steps"][-1]["label"] == "date added not recorded"
    assert t["steps"][-1]["total"] == 2 and len(t["rows"]) == 2


def test_timeline_table_reproduces_the_charts_at_every_step():
    recs = [_rec(f"s{i:07d}", incident_start=f"2026-10-{1 + i % 9:02d}",
                 incident_event="found" if i % 4 == 0 else "occurred",
                 incident_date_basis=("report" if i % 7 == 0 else "later_step" if i % 11 == 0 else "incident"),
                 published_at=f"2026-10-{18 + i % 4}T{8 + i % 3:02d}:00Z",
                 stream=("A", "B", "")[i % 3], province=("Limpopo", "Gauteng")[i % 2])
            for i in range(40)]
    t = rc.timeline_table(recs)
    by_step = {}
    for k, st in enumerate(t["steps"]):
        cut = [r for r in recs if rc._published_instant(r["published_at"]).isoformat(timespec="minutes") <= st["at"]]
        by_step[k] = cut
        got = rc.counts_from_table(t, k)
        assert got["total"] == st["total"] == len(cut)
        want = rc.chart_counts(cut)
        inc, wi = got["incident"], _page(want, "incident")["data"]
        assert (inc["occurred"], inc["found"], inc["undated"], inc["later_step"]) == (
            wi["occurred"], wi["found"], wi["undated"], wi["later_step"])
        wd = {d["date"]: d["count"] for d in wi["days"]}
        assert inc["days"] == [wd.get(d, 0) for d in t["axes"]["incident_days"]]
        for f in ("stream", "province"):
            wr = {x["label"]: x["count"] for x in _page(want, f)["data"]["rows"]}
            assert got[f] == [wr.get(lab, 0) for lab in t["axes"][f]]
            assert sum(got[f]) == len(cut)
        assert sum(inc["days"]) + inc["undated"] + inc["later_step"] == len(cut)
        assert sum(got["added"]["days"]) + got["added"]["undated"] == len(cut)


def _chart_table(html):
    m = re.search(r'<script type="application/json" id="gp-chart-data">(.*?)</script>', html, re.S)
    assert m, "chart table missing from the page"
    return json.loads(m.group(1))


def test_build_carries_the_timeline_table(built):
    html = (built / "index.html").read_text()
    payload = json.loads((built / "records.json").read_text())
    recs = payload["records"]
    t = _chart_table(html)
    col = {c: i for i, c in enumerate(t["columns"])}
    # one row per approved record, no duplicates
    ids = [r[col["id"]] for r in t["rows"]]
    assert len(ids) == len(set(ids)) == ALL_PINS == payload["count"]
    assert set(ids) == {r["short_id"] for r in recs}
    # steps: one per publish, in time order, in South African time
    ats = [s["at"] for s in t["steps"]]
    assert ats == sorted(ats) and len(ats) == len(set(ats))
    assert all(a.endswith("+02:00") for a in ats)
    assert len(t["steps"]) == len({r["published_at"] for r in recs})
    totals = [s["total"] for s in t["steps"]]
    assert totals == sorted(totals) and totals[-1] == ALL_PINS
    # the last step reproduces today's four charts exactly
    now = rc.counts_from_table(t, len(t["steps"]) - 1)
    want = rc.chart_counts(recs)
    wi = _page(want, "incident")["data"]
    assert now["incident"]["days"] == [d["count"] for d in wi["days"]]
    assert (now["incident"]["occurred"], now["incident"]["found"],
            now["incident"]["undated"], now["incident"]["later_step"]) == (wi["occurred"], wi["found"],
                                                                         wi["undated"], wi["later_step"])
    assert now["added"]["days"] == [d["count"] for d in _page(want, "added")["data"]["days"]]
    for f in ("stream", "province"):
        assert now[f] == [x["count"] for x in _page(want, f)["data"]["rows"]]
    # the printed charts are those counts too (no visible change)
    sec = _section(html, "stream")
    assert [int(n) for n in re.findall(r'<span class="gp-bar__n">(\d+)</span>', sec)] == now["stream"]


def test_build_today_is_53_occurred_23_found_5_undated_2_later_steps(built):
    t = _chart_table((built / "index.html").read_text())
    inc = rc.counts_from_table(t, len(t["steps"]) - 1)["incident"]
    assert (inc["occurred"], inc["found"], inc["undated"], inc["later_step"]) == (53, 23, 5, 2)   # + the Bloemfontein sweep: Bochabela and Wilgehof occurred, Phase 7 found; + the remaining-provinces sweep: Tembisa, Westonaria and Zamdela occurred, Meqheleng and Currieskamp found
    assert len(t["steps"]) == 20   # publication steps: the Durban sweep pins, then the Bloemfontein sweep pins, then the remaining-provinces sweep pins, 30 Oct


def test_build_timeline_table_holds_no_record_text(built):
    html = (built / "index.html").read_text()
    raw = re.search(r'id="gp-chart-data">(.*?)</script>', html, re.S).group(1)
    t = json.loads(raw)
    assert t["columns"] == ["id", "step", "inc", "kind", "added", "stream", "province", "town"]
    for r in json.loads((built / "records.json").read_text())["records"]:
        for text in (r["title"], r["location_described"], r["incident_described"], r["statement_from"],
                     r["outlets"], r["signal_id"]):
            if text and len(str(text)) > 3:
                assert str(text) not in raw, text
        for url in filter(None, (r["source_urls"] or "").split(" | ")):
            assert url not in raw
    assert "http" not in raw


def test_publish_refuses_a_timeline_that_disagrees_with_records(tmp_path, monkeypatch):
    import tools.record_charts as mod
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    real = mod.timeline_table

    def short(records, charts=None):
        t = real(records, charts)
        t["rows"] = t["rows"][:-1]
        return t
    monkeypatch.setattr(mod, "timeline_table", short)
    with pytest.raises(SystemExit, match="timeline"):
        _publish_to(tmp_path, monkeypatch, db_file)


# ── Slider markup and script (interactive charts Batch 2) ──────────────────────

JS = ROOT / "publisher" / "static" / "js" / "record-charts.js"


def test_one_hidden_slider_per_chart_page(built):
    html = (built / "index.html").read_text()
    t = _chart_table(html)
    for p in rc.CHART_PAGES:
        sec = _section(html, p["id"])
        assert sec.count("data-gp-time") == 1, p["id"]
        # printed hidden: without the script the page is today's charts, no slider
        assert re.search(r'<div class="gp-time" data-gp-time hidden>', sec)
        m = re.search(r'<input class="gp-time__range" id="gp-time-%s" type="range" min="0" max="(\d+)" step="1" value="(\d+)"' % p["id"], sec)
        assert m and int(m.group(1)) == int(m.group(2)) == len(t["steps"]) - 1   # opens at "now"
        assert f"(now) · {ALL_PINS} of {ALL_PINS} records" in sec
        # the slider sits between the chart and its caption
        assert sec.index("data-gp-time") < sec.index("gp-chart__caption")
    net = _section(html, "network")
    assert "data-gp-time" not in net


def test_chart_script_is_served_from_the_site_not_a_cdn(built):
    html = (built / "index.html").read_text()
    srcs = re.findall(r'<script[^>]*src="([^"]*record-charts[^"]*)"', html)
    assert srcs == ["static/js/record-charts.js"]
    assert (built / "static" / "js" / "record-charts.js").read_bytes() == JS.read_bytes()
    js = JS.read_text()
    # the only URL is the SVG namespace name (an identifier, never fetched)
    assert not re.search(r"https?://", js.replace("'http://www.w3.org/2000/svg'", "")), \
        "the chart script loads nothing from outside"
    assert "import " not in js and "require(" not in js
    # Chart.js and D3 are not on the page for the charts
    assert "chart.js" not in html.lower().replace("record-charts.js", "")
    assert "d3.min.js" not in html


def test_chart_script_follows_the_motion_rules():
    js = JS.read_text()
    assert "prefers-reduced-motion: reduce" in js
    assert "requestAnimationFrame" in js
    m = re.search(r"var DURATION = (\d+);", js)
    assert m and 250 <= int(m.group(1)) <= 400          # about a third of a second
    assert "plural(T.total, 'record')" in js            # labels say records, never incidents
    assert "incidents" not in js.lower()
    assert "Math.pow(1 - t, 3)" in js                   # ease-out, no overshoot term


def test_empty_timeline_when_there_are_no_records():
    charts = rc.chart_counts([])
    t = rc.timeline_table([], charts)
    assert t["steps"] == [] and t["rows"] == []


# ── The incident line moves (interactive charts Batch 3) ────────────────────────

def test_line_keeps_the_straight_fallback_and_the_script_draws_the_curve(built):
    html = (built / "index.html").read_text()
    sec = _section(html, "incident")
    # without the script: today's line, straight segments, as before
    assert sec.count('<polyline class="gp-line__path"') == 1
    assert sec.count('class="gp-line__pt"') == 28   # incident days 1-28 Oct since the Durban sweep pins (22, 25, 28 Oct)
    js = JS.read_text()
    assert "function monotonePath" in js and "curveMonotoneX" in js
    # fixed scale: the script uses today's highest day, never the step's
    assert "Math.max(AX.incident_max, 1)" in js
    t = _chart_table(html)
    assert t["axes"]["incident_max"] == _page(rc.chart_counts(json.loads((built / "records.json").read_text())["records"]),
                                             "incident")["data"]["max"]
    assert t["line"]["top"] == rc.LINE_PAD_TOP and t["line"]["bottom"] == rc.LINE_H - rc.LINE_PAD_BOTTOM


def test_line_summary_text_matches_the_printed_summary(built):
    # the script rebuilds the summary in the same words the page prints
    sec = _section((built / "index.html").read_text(), "incident")
    printed = re.search(r'<p class="gp-chart__summary">(.*?)</p>', sec, re.S).group(1).replace("&middot;", "\u00b7")
    js = JS.read_text()
    for phrase in (" occurred ", " found (date a body was found)", " date not stated (not on the line)",
                   " later step in an older case (not on the line)"):
        assert phrase.strip() in printed
        assert phrase.strip() in js


# ── Tap to see the records (Batch 4) and stream switches (Batch 5) ─────────────

from tools.records_export import STREAM_LABELS  # noqa: E402

_SL = list(STREAM_LABELS.values())


def _mixed(n=48):
    return [_rec(f"m{i:07d}", incident_start=f"2026-10-{1 + i % 9:02d}",
                 incident_event="found" if i % 4 == 0 else "occurred",
                 incident_date_basis=("report" if i % 7 == 0 else "later_step" if i % 11 == 0 else "incident"),
                 published_at=f"2026-10-{18 + i % 4}T{8 + i % 3:02d}:00Z" if i % 13 else "",
                 stream=_SL[i % 4], province=("Limpopo", "Gauteng", "Western Cape")[i % 3])
            for i in range(n)]


def test_timeline_table_names_the_four_map_streams_in_panel_order():
    t = rc.timeline_table(_mixed())
    assert t["streams"] == [{"key": k, "label": v} for k, v in STREAM_LABELS.items()]
    assert len(t["streams"]) == 4


def test_every_chart_page_says_where_its_stream_switches_sit():
    assert {p["id"]: p["stream_filter"] for p in rc.CHART_PAGES} == {
        "incident": "chips", "added": "chips", "stream": "bars", "province": "chips", "town": "chips"}


@pytest.mark.parametrize("bad", [{}, {"stream_filter": "slider"}, {"stream_filter": "bars"}])
def test_stream_filter_registry_is_checked(monkeypatch, bad):
    pages = [dict(p) for p in rc.CHART_PAGES]
    pages[0].pop("stream_filter")
    pages[0].update(bad)
    monkeypatch.setattr(rc, "CHART_PAGES", tuple(pages))
    with pytest.raises(ValueError, match="stream_filter|Streams bars"):
        rc._check_registry()


@pytest.mark.parametrize("on", [{"CRIME_INTEL"}, {"CRIME_INTEL", "GLOBAL"}, set(), set(STREAM_LABELS)])
def test_stream_filter_counts_only_the_streams_switched_on(on):
    recs = _mixed()
    t = rc.timeline_table(recs)
    labels = {STREAM_LABELS[k] for k in on}
    for k, st in enumerate(t["steps"]):
        got = rc.counts_from_table(t, k, on)
        cut = [r for r in recs if r["stream"] in labels
               and (r["published_at"] == "" and k == len(t["steps"]) - 1 or
                    r["published_at"] and rc._published_instant(r["published_at"]).isoformat(timespec="minutes") <= (st["at"] or "9"))]
        assert got["total"] == len(cut)
        want = rc.chart_counts(cut)
        assert got["incident"]["days"] == [
            next((d["count"] for d in _page(want, "incident")["data"]["days"] if d["date"] == day), 0)
            for day in t["axes"]["incident_days"]]
        assert sum(got["stream"]) == sum(got["province"]) == got["total"]
        for i, lab in enumerate(t["axes"]["stream"]):
            if lab not in labels:
                assert got["stream"][i] == 0


@pytest.mark.parametrize("on", [None, {"CRIME_INTEL"}, {"INFRASTRUCTURE", "PRIORITY"}])
def test_each_tap_list_holds_exactly_the_records_counted_there(on):
    t = rc.timeline_table(_mixed())
    ax = t["axes"]
    for k in range(len(t["steps"])):
        c = rc.counts_from_table(t, k, on)
        for i, d in enumerate(ax["incident_days"]):
            assert len(rc.ids_from_table(t, k, "incident", d, on)) == c["incident"]["days"][i]
        for key in ("undated", "later_step"):
            assert len(rc.ids_from_table(t, k, "incident", key, on)) == c["incident"][key]
        for i, d in enumerate(ax["added_days"]):
            assert len(rc.ids_from_table(t, k, "added", d, on)) == c["added"]["days"][i]
        assert len(rc.ids_from_table(t, k, "added", None, on)) == c["added"]["undated"]
        for pid in ("stream", "province"):
            for i, lab in enumerate(ax[pid]):
                assert len(rc.ids_from_table(t, k, pid, lab, on)) == c[pid][i]


def test_build_prints_stream_switches_and_list_slots_hidden(built):
    html = (built / "index.html").read_text()
    for pid in ("incident", "added", "stream", "province"):
        sec = _section(html, pid)
        assert re.search(r'<div class="gp-list" data-gp-list[^>]*hidden', sec), pid
        assert re.search(r'<p class="gp-streams-note" data-gp-streams-note hidden', sec), pid
        if pid == "stream":
            assert "data-gp-streams" not in sec.replace("data-gp-streams-note", "")
            continue
        chips = re.search(r'<div class="gp-streams" data-gp-streams[^>]*hidden>(.*?)</div>', sec, re.S)
        assert chips, pid
        assert re.findall(r'data-gp-stream="([A-Z_]+)"', chips.group(1)) == list(STREAM_LABELS)
        assert chips.group(1).count('aria-pressed="true"') == 4
        # switches sit under the slider, so the chart and slider stay where they were
        assert sec.index("data-gp-time") < sec.index("data-gp-streams") < sec.index("gp-chart__caption")


def test_map_and_charts_share_one_stream_state():
    tpl = (ROOT / "publisher" / "templates" / "map.html").read_text(encoding="utf-8")
    assert "window.ZADStreams" in tpl and "'forge-streams'" in tpl
    assert "setStreamVisible(toggle.dataset.streamToggle" in tpl
    assert "window.forgeSignalInfo" in tpl
    js = JS.read_text(encoding="utf-8")
    assert "ZADStreams.set(" in js and "'forge-streams'" in js
    assert "forgeSignalInfo" in js and "forgeOpenSignal" in js
    assert "Show on map" in js and "Showing " in js
    assert "incidents" not in js.lower()
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", js)   # no CDN; the SVG namespace is not a fetch
