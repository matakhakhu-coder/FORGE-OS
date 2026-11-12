"""Last reviewed dates (12 Nov 2026).

A pin's panel shows "Last reviewed [date]" only when a FORGE log records a
review of the case after publication (docs/signals/REVIEW_DATES.md). These
tests hold the rule:
  * an entry must name its log, may carry only the outcome
    no_new_information, and may not be in the future, before publication or
    before the incident;
  * review wording may never call a case cold, closed, resolved and the like;
  * the date shown is the later of the check and the last material update;
  * the real entries are exactly the 15 written in the two logs, and nothing
    else is back-filled;
  * the panel line, the detail files and records.json carry it, and the map
    list does not.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import checked_signals as cs  # noqa: E402
from tools import map_data  # noqa: E402
from tools import pin_clocks as pc  # noqa: E402
from tools import publish  # noqa: E402
from tools import records_export as rx  # noqa: E402

JS = ROOT / "publisher" / "static" / "js" / "forge-pins.js"
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
POLICY = ROOT / "docs" / "signals" / "REVIEW_DATES.md"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node is not installed")

PIN = {"published_at": "2026-10-18T17:28:00+02:00",
       "metadata": {"checked": {"incident_start": "2026-10-16"}}}
OK = {"last_checked_at": "2026-10-25T09:45:00+02:00", "record": "Sprint 5 results, section 15c"}

LOG_15C = "Sprint 5 results, section 15c: arrest follow-ups, 25 Oct 2026, 09:15-09:45 SAST"
LOG_1111 = "Connections run notes, 11 Nov 2026: live pins checked against news to 11 Nov"
# The 15 entries the two logs support, by short id: (log, outcome).
EXPECTED = {
    "19a71c43": (LOG_15C, "no_new_information"),   # Tafelsig burnt body: "No new report found."
    "2fdd9a56": (LOG_15C, "no_new_information"),   # D'Almeida: "No new court report found."
    "c2601d28": (LOG_15C, None),                   # Cafda, Ensor St: status restated
    "d09da7da": (LOG_15C, "no_new_information"),   # Great Brak: "no change found"
    "adf99019": (LOG_15C, "no_new_information"),   # Korvette Rd: "no arrest found since"
    "7aa4eea1": (LOG_15C, "no_new_information"),   # Bernstein Rd: "none found"
    "49773d55": (LOG_15C, "no_new_information"),   # Tafelsig barber: "found nothing beyond"
    "06219161": (LOG_15C, None),                   # Athlone: an unverified lead noted
    "0caa5a74": (LOG_15C, None),                   # Valhalla Park: status only
    "0c9f233c": (LOG_15C, None),                   # St Montague Village: status only
    "048440e0": (LOG_1111, None),                  # Steenberg: possible charges found
    "dbf90d51": (LOG_1111, None),                  # Epping: dispute reporting found
    "1d4bd77f": (LOG_1111, None),                  # Sasolburg: funeral reporting found
    "42598400": (LOG_1111, None),                  # Neltox: nearby reporting found
    "c9697dcd": (LOG_1111, None),                  # Durban CBD: arrests found (then updated)
}


# ── one entry ────────────────────────────────────────────────────────────────────

def test_a_valid_entry_passes():
    assert pc.entry_problem(OK, PIN) is None
    assert pc.entry_problem({**OK, "outcome": "no_new_information"}, PIN) is None


def test_a_review_in_the_future_is_refused():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert "future" in pc.entry_problem({**OK, "last_checked_at": future}, PIN)


def test_a_review_before_publication_is_refused():
    e = {**OK, "last_checked_at": "2026-10-18T09:00:00+02:00"}
    assert "before the pin was published" in pc.entry_problem(e, PIN)


def test_a_review_before_the_incident_is_refused():
    pin = {"metadata": {"checked": {"incident_start": "2026-10-16"}}}   # no publication date
    e = {**OK, "last_checked_at": "2026-10-15T09:00:00+02:00"}
    assert "before the incident" in pc.entry_problem(e, pin)


@pytest.mark.parametrize("record", [None, "", "   ", 7, "two\nlines", "x" * 201])
def test_a_review_without_a_written_record_is_refused(record):
    e = dict(OK)
    if record is None:
        e.pop("record")
    else:
        e["record"] = record
    assert "record" in pc.entry_problem(e, PIN)


@pytest.mark.parametrize("outcome", ["cold", "closed", "resolved", "nothing", "", None])
def test_only_one_outcome_is_allowed(outcome):
    assert "outcome" in pc.entry_problem({**OK, "outcome": outcome}, PIN)


@pytest.mark.parametrize("words", [
    "Case gone cold", "cold case review", "Closed after review", "Resolved", "case solved",
    "dormant file", "Inactive since October", "no longer followed", "dead end", "Abandoned",
    "went quiet", "shelved", "review concluded",
])
def test_wording_that_calls_a_case_cold_is_refused(words):
    assert "may not say" in pc.entry_problem({**OK, "record": f"Log 3: {words}"}, PIN)
    assert pc.wording_problem(words)


@pytest.mark.parametrize("words", ["Sprint 5 results, section 15c", "Connections run notes",
                                   "Town sweep, Durban, 30 Oct", "collection run"])
def test_ordinary_log_names_pass(words):
    assert pc.wording_problem(words) is None


def test_the_allowed_outcome_text_is_careful():
    text = pc.OUTCOMES["no_new_information"]
    assert text == "No new information was found in the sources checked."
    assert pc.wording_problem(text) is None


def test_unknown_keys_are_refused():
    assert "only last_checked_at, record and outcome" in pc.entry_problem({**OK, "status": "x"}, PIN)


# ── the date shown ───────────────────────────────────────────────────────────────

def test_last_review_is_the_later_of_check_and_update():
    e = {**OK, "outcome": "no_new_information"}
    assert pc.last_review(e, PIN) == {"last_reviewed_at": OK["last_checked_at"],
                                      "review_outcome": "no_new_information"}
    # a later material update is the latest review, and says nothing about "no new information"
    later = "2026-11-11T18:13:00+02:00"
    assert pc.last_review(e, PIN, later) == {"last_reviewed_at": later, "review_outcome": None}
    # an earlier update leaves the check as the latest review
    assert pc.last_review(e, PIN, "2026-10-20T08:00:00+02:00")["last_reviewed_at"] == OK["last_checked_at"]
    # an update alone is a review
    assert pc.last_review(None, PIN, later)["last_reviewed_at"] == later


def test_no_record_means_no_date():
    assert pc.last_review(None, PIN) == {"last_reviewed_at": None, "review_outcome": None}
    assert pc.last_review({"last_checked_at": "2026-10-25T09:45:00+02:00"}, PIN)["last_reviewed_at"] is None
    bad = {**OK, "record": "case went cold"}
    assert pc.last_review(bad, PIN)["last_reviewed_at"] is None


# ── the real entries ─────────────────────────────────────────────────────────────

def test_real_entries_are_exactly_the_logged_reviews():
    data = pc.read_file()
    got = {sid[:8]: (e["record"], e.get("outcome")) for sid, e in data["pins"].items()}
    assert got == EXPECTED
    assert pc.problems(cs.load_index().signals) == []


def test_real_entries_match_their_log_times():
    for sid, e in pc.read_file()["pins"].items():
        want = ("2026-10-25T09:45:00+02:00" if e["record"] == LOG_15C
                else "2026-11-11T16:44:00+02:00")
        assert e["last_checked_at"] == want, sid


def test_policy_doc_states_the_rule():
    text = POLICY.read_text()
    for phrase in ("What counts as a review", "Nothing is back-filled",
                   "no new information was found in the sources checked",
                   "shows no Last reviewed line", "before the pin was published"):
        assert phrase in text


# ── where it shows ───────────────────────────────────────────────────────────────

def test_publish_puts_the_date_in_the_detail_not_the_map_list():
    assert "last_reviewed_at" not in map_data.MAP_FIELDS
    sid = next(s for s in pc.read_file()["pins"] if s.startswith("19a71c43"))
    pin = next(p for p in cs.load_index().signals if p["signal_id"] == sid)
    got = publish._review_for(sid, pin["published_at"], pin["metadata"]["checked"])
    assert got == {"last_reviewed_at": "2026-10-25T09:45:00+02:00",
                   "review_outcome": "no_new_information"}
    assert publish._review_for("00000000-0000-4000-8000-000000000000", None, {}) == {
        "last_reviewed_at": None, "review_outcome": None}


def _conn_for(sids) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (signal_id TEXT, metadata_json TEXT, published_at TEXT, "
                 "status TEXT, lat REAL, lng REAL)")
    for sid in sids:
        conn.execute("INSERT INTO signals VALUES (?,?,?,?,?,?)",
                     (sid, json.dumps({"checked": {}}), "x", "published", 1, 1))
    return conn


def test_records_carry_the_review_columns():
    assert rx.RECORD_FIELDS[-2:] == ("last_reviewed_at", "review_outcome")
    pins = [p["signal_id"] for p in cs.load_index().signals]
    rows = {r["signal_id"][:8]: r for r in rx.build_records(_conn_for(pins), version="test")}
    dated = {k for k, r in rows.items() if r["last_reviewed_at"]}
    assert dated == set(EXPECTED)
    assert rows["19a71c43"]["review_outcome"] == pc.OUTCOMES["no_new_information"]
    assert rows["06219161"]["review_outcome"] == ""
    # Durban's material update (18:13 on 11 Nov) is later than the 16:44 check
    assert rows["c9697dcd"]["last_reviewed_at"] == rows["c9697dcd"]["last_material_update_at"]
    for k, r in rows.items():
        assert pc.wording_problem(r["review_outcome"]) is None


def test_panel_has_the_line_and_it_stays_small():
    html = TEMPLATE.read_text()
    assert 'id="sd-reviewed"' in html and ".sd-reviewed[hidden]" in html
    assert "FP.reviewedLine(m)" in html


@needs_node
def test_reviewed_line_wording():
    prog = ("const F = require(%s).ForgePins; const now = Date.parse('2026-11-12T12:00:00+02:00');\n"
            "console.log(JSON.stringify(["
            " F.reviewedLine({last_reviewed_at: '2026-10-25T09:45:00+02:00', review_outcome: 'no_new_information'}, now),"
            " F.reviewedLine({last_reviewed_at: '2026-11-11T16:44:00+02:00', review_outcome: null}, now),"
            " F.reviewedLine({last_reviewed_at: null}, now),"
            " F.reviewedLine({}, now),"
            " F.reviewedLine({last_reviewed_at: '2026-11-11T18:13:00+02:00', last_material_update_at: '2026-11-11T18:13:00+02:00'}, now),"
            " F.reviewedLine({last_reviewed_at: '2025-12-30T09:00:00+02:00', review_outcome: 'cold'}, now)"
            "]));" % json.dumps(str(JS)))
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got == [
        "Last reviewed 25 Oct: no new information was found in the sources checked.",
        "Last reviewed 11 Nov",
        "",
        "",
        "",                          # the Updated line already gives that day
        "Last reviewed 30 Dec 2025",  # an unknown outcome adds no words
    ]
    for line in got:
        assert pc.wording_problem(line) is None
