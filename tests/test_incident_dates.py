"""What each pin's incident date stands for (tools/incident_dates.py).

A report date or a later arrest/charge date must never be read as the day an
incident happened. Every active pin says which it is in
docs/signals/incident-dates.json, and publish stops when one does not.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import _publish_to  # noqa: E402
from tests.test_open_records import _imported  # noqa: E402
from tools import incident_dates as idates  # noqa: E402
from tools.checked_signals import load_index  # noqa: E402

SIGNALS = ROOT / "docs" / "signals"
BEITBRIDGE = "931b443f-6722-507f-be64-a47bb4ef899f"
AIRPORT_ARRESTS = "befc9ba6-05fd-5471-ac93-fc32257d498b"
REPORT_DATED = {
    "736aae9c-e6de-5bb2-acb1-4fb32bb23c99",   # Tzaneen burst: "The pin date is the report date"
    "09124894-bed2-5f4e-bd80-e3f7b162ff59",   # Seshego learners: "The pin date is the report date"
    "b5342d21-a1b8-5abf-8bd6-954123f57973",   # Newcastle: start only "about", date is the notice
    "34ab4d0c-f76b-5775-9f43-faf729c21de4",   # Musina: "start of the breakdown not stated"
    "b2ec397b-458f-55c5-9882-65064085e1be",   # Polokwane pump station: day not stated
}


# ── The committed file ──────────────────────────────────────────────────────────

def test_every_active_pin_has_a_valid_entry():
    cs = load_index()
    assert idates.problems(cs.active) == []


def test_no_entries_for_pins_that_do_not_exist():
    cs = load_index()
    known = {s["signal_id"] for s in cs.active} | {s["signal_id"] for s in cs.retracted}
    assert set(idates.load()) <= known


def test_the_classification_as_checked_on_24_october():
    entries = idates.load()
    report = {sid for sid, e in entries.items() if e["basis"] == "report"}
    later = {sid for sid, e in entries.items() if e["basis"] == "later_step"}
    assert report == REPORT_DATED
    assert later == {BEITBRIDGE, AIRPORT_ARRESTS}
    b = entries[BEITBRIDGE]
    assert (b["step"], b["earlier_incident"]) == ("charge", {"date": "2026-07-22", "precision": "day"})
    a = entries[AIRPORT_ARRESTS]
    assert (a["step"], a["earlier_incident"]) == ("arrest", {"date": "2025-04-17", "precision": "day"})


def test_earlier_dates_come_from_the_audited_quotes():
    """An earlier incident date is never guessed: its quote is one of the pin's
    word-for-word audited source quotes in fact-bases.json."""
    fact_bases = json.loads((SIGNALS / "fact-bases.json").read_text())["pins"]
    for sid, e in idates.load().items():
        if e["basis"] != "later_step":
            continue
        quotes = {(s["url"], s.get("quote")) for f in fact_bases[sid]["facts"] for s in f["sources"]}
        assert (e["evidence"]["url"], e["evidence"]["quote"]) in quotes, sid


# ── Validation ──────────────────────────────────────────────────────────────────

GOOD_LATER = {"basis": "later_step", "step": "charge", "why": "charged later",
              "earlier_incident": {"date": "2026-07", "precision": "month"},
              "evidence": {"source": "Paper", "url": "https://example.org/a", "quote": "in July"}}


@pytest.mark.parametrize("entry, start, ok", [
    ({"basis": "incident"}, "2026-10-01", True),
    ({"basis": "report", "why": "not stated"}, "2026-10-01", True),
    ({"basis": "report"}, "2026-10-01", False),                  # no why
    ({"basis": "occurred"}, "2026-10-01", False),                 # unknown basis
    (None, "2026-10-01", False),                                   # missing
    (GOOD_LATER, "2026-10-21", True),
    ({**GOOD_LATER, "step": "rumour"}, "2026-10-21", False),
    ({**GOOD_LATER, "earlier_incident": {"date": "2026-07", "precision": "day"}}, "2026-10-21", False),
    ({**GOOD_LATER, "earlier_incident": {"date": "2026-10", "precision": "month"}}, "2026-10-21", False),
    ({**GOOD_LATER, "evidence": {"url": "https://example.org/a"}}, "2026-10-21", False),   # no quote
    ({**GOOD_LATER, "evidence": {"quote": "in July"}}, "2026-10-21", False),               # no link
])
def test_entry_validation(entry, start, ok):
    assert (idates.entry_problem(entry, start) is None) is ok


# ── Open records and publish ────────────────────────────────────────────────────

def test_built_records_say_what_each_date_is(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    payload = json.loads((dist / "records.json").read_text())
    by_id = {r["signal_id"]: r for r in payload["records"]}
    assert by_id[BEITBRIDGE]["incident_date_basis"] == "later_step"
    assert by_id[BEITBRIDGE]["incident_start"].startswith("2026-10-21")   # the pin itself is unchanged
    assert {sid for sid, r in by_id.items() if r["incident_date_basis"] == "report"} == REPORT_DATED
    assert "incident_date_basis" in json.dumps(payload["fields"])
    csv_head = (dist / "records.csv").read_text().splitlines()[0]
    assert "incident_date_basis" in csv_head.split(",")
    html = (dist / "records.html").read_text()
    assert html.count("later step in an older case") == 2
    assert html.count("report date: when it happened is not stated") == len(REPORT_DATED)


def test_publish_refuses_a_pin_with_no_incident_date_entry(tmp_path, monkeypatch):
    signals = tmp_path / "signals"
    shutil.copytree(SIGNALS, signals)
    f = signals / "incident-dates.json"
    data = json.loads(f.read_text())
    del data["pins"][BEITBRIDGE]
    f.write_text(json.dumps(data))
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    with pytest.raises(SystemExit) as exc:
        _publish_to(tmp_path, monkeypatch, db_file, index=signals / "index.json")
    assert exc.value.code == 2
    assert not (tmp_path / "dist").exists()
