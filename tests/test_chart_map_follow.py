"""Interactive charts Batches 6 and 7: the map follows the slider, and the
week buttons on the incident line and Added charts.

Python owns what the map keeps at each publish step (ids_on_map) and where
the weeks fall (chart_weeks); the page script only applies them. The browser
side is checked by tests/browser/map_follow_check.py."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_record_charts import _chart_table, _rec, built  # noqa: E402,F401
from tools import record_charts as rc  # noqa: E402

JS = ROOT / "publisher" / "static" / "js" / "record-charts.js"
TPL = ROOT / "publisher" / "templates" / "map.html"


def _days(a, b):
    return [f"2026-10-{d:02d}" for d in range(a, b + 1)]


# ── Batch 6: the map follows the slider ──

def _three_publishes():
    return [
        _rec("aaaaaaaa-1", published_at="2026-10-18T17:28:00Z"),
        _rec("bbbbbbbb-1", published_at="2026-10-18T17:28:00Z", stream="Infrastructure"),
        _rec("cccccccc-1", published_at="2026-10-19T02:05:00Z"),
        _rec("dddddddd-1", published_at="2026-10-20T08:00:00Z"),
        _rec("eeeeeeee-1", published_at=None),
    ]


def test_map_keeps_the_pins_added_by_each_publish():
    t = rc.timeline_table(_three_publishes())
    # three publishes, then the record with no publish time joins the last step
    assert [s["total"] for s in t["steps"]] == [2, 3, 4, 5]
    assert rc.ids_on_map(t, 0) == ["aaaaaaaa", "bbbbbbbb"]
    assert rc.ids_on_map(t, 1) == ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
    # "now" is every record, the one with no publish time too
    assert len(rc.ids_on_map(t, 3)) == 5


def test_map_count_matches_the_chart_total_at_every_step():
    t = rc.timeline_table(_three_publishes())
    for s in range(len(t["steps"])):
        assert len(rc.ids_on_map(t, s)) == rc.counts_from_table(t, s)["total"] == t["steps"][s]["total"]


def test_map_follow_is_wired_both_ways():
    tpl = TPL.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    # map side: one hook, the note with Back to now, and the filters stacked
    assert "window.ZADMapTime" in tpl and 'id="gp-mapnote"' in tpl and ">Back to now</button>" in tpl
    assert "if (timeIdx && !timeIdx[ml.idx]) return 'out';" in tpl
    # streams first, then the open drawer's pin always shows, then slider and Area check
    body = tpl[tpl.index("function pinShowState(ml)"):]
    body = body[:body.index("\n  }")]
    assert body.index("streamVisible") < body.index("drawerIdx") < body.index("timeIdx") < body.index("pinFilter(")
    # chart side: once per publish step, never in the animation loop; null at now
    assert "pushMap(s, c, changed);" in js and "M.setTime(null)" in js
    loop = js[js.index("function startMotion"):]
    loop = loop[:loop.index("\n  }\n")]
    assert "pushMap" not in loop and "setTime" not in loop
    assert "(pins added by then)" in js


def test_map_note_hidden_in_the_build(built):
    html = (built / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<div class="gp-mapnote" id="gp-mapnote" hidden>', html)
    assert m, "the map note must ship hidden: at now the map is as it is today"


# ── Batch 7: week buttons ──

def test_weeks_count_from_the_axis_first_day():
    w = rc.chart_weeks(_days(1, 21))
    assert [x["label"] for x in w] == ["1 to 7 Oct", "8 to 14 Oct", "15 to 21 Oct"]
    assert [(x["from"], x["to"]) for x in w] == [(0, 6), (7, 13), (14, 20)]


def test_short_last_week_and_month_change():
    days = [f"2026-09-{d}" for d in (28, 29, 30)] + _days(1, 7)
    w = rc.chart_weeks(days)
    assert [x["label"] for x in w] == ["28 Sep to 4 Oct", "5 to 7 Oct"]
    assert rc.chart_weeks(_days(1, 8))[-1]["label"] == "8 Oct"


@pytest.mark.parametrize("n", [0, 1, 6, 7])
def test_a_week_or_less_gets_no_buttons(n):
    assert rc.chart_weeks(_days(1, n) if n else []) == []


def test_week_totals_add_up_to_the_records_on_the_map():
    recs = [_rec(f"{i:08d}-x", incident_start=f"2026-10-{1 + i % 21:02d}",
                 published_at=f"2026-10-{18 + i % 3}T10:00:00Z") for i in range(40)]
    recs.append(_rec("undated1-x", incident_start=None))
    t = rc.timeline_table(recs, rc.chart_counts(recs))
    weeks = t["axes"]["incident_weeks"]
    assert len(weeks) == 3
    for s in range(len(t["steps"])):
        c = rc.counts_from_table(t, s)
        per_week = [rc.week_split(t, s, "incident", w) for w in weeks]
        for ws in per_week:
            assert ws["in_week"] + ws["other_days"] == c["incident"]["occurred"] + c["incident"]["found"]
        assert sum(ws["in_week"] for ws in per_week) + c["incident"]["undated"] + c["incident"]["later_step"] == c["total"]


def test_build_carries_the_weeks(built):
    html = (built / "index.html").read_text(encoding="utf-8")
    t = _chart_table(html)
    assert t["axes"]["incident_weeks"] == rc.chart_weeks(t["axes"]["incident_days"])
    assert t["axes"]["added_weeks"] == rc.chart_weeks(t["axes"]["added_days"])


def test_week_buttons_only_on_the_line_and_added_and_tap_sized():
    js = JS.read_text(encoding="utf-8")
    assert js.count("weekBar('") == 2
    assert "weekBar('incident', AX.incident_weeks" in js and "weekBar('added', AX.added_weeks" in js
    tpl = TPL.read_text(encoding="utf-8")
    css = tpl[tpl.index(".gp-weeks__btn {"):]
    css = css[:css.index("}")]
    assert "min-height: 44px" in css
    # no pinch zoom: no gesture or wheel handling for the zoom
    assert "gesturestart" not in js and "'wheel'" not in js
