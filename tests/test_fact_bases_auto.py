"""Unattended 'What each fact rests on' entries: validation, the quote check and the
autopublish gate (owner's request 26 Nov 2026: new pins must not go live with the
section empty)."""
from __future__ import annotations

import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import fact_bases as fb  # noqa: E402
from tools import publish  # noqa: E402

NASREC = "84d9b687-ee31-5b74-8191-a647309559d0"      # same JMPD statement, approximate point
DOBSONVILLE = "96877bd0-09c7-5452-a666-7faab3c90663"  # two outlets, estimated point
TODAY = ["087aa074-be9c-5b86-a824-456443391d30", NASREC, DOBSONVILLE,
         "e79e7810-f8c5-5417-9823-ec3dbbf3198d"]


def _live():
    return json.loads(fb.FACT_BASES.read_text(encoding="utf-8"))["pins"]


def _check(sid, entry):
    return fb.validate_entry(sid, entry, fb.load_batch_pins().get(sid), fb._provenance().get(sid))


def test_todays_pins_have_valid_entries_that_the_map_shows():
    live = _live()
    assert fb.gate_problems(TODAY, live) == []
    for sid in TODAY:
        shown = publish._fact_basis_for(sid)
        assert shown["status"] == "audited" and len(shown["facts"]) == 6


def test_gate_refuses_a_pin_with_no_entry():
    live = _live()
    live.pop(NASREC)
    assert any("no fact-basis entry" in e for e in fb.gate_problems([NASREC], live))


def test_five_facts_would_be_hidden_so_refused():
    e = copy.deepcopy(_live()[NASREC])
    e["facts"].pop()
    assert any("exactly 6" in x for x in _check(NASREC, e))


def test_a_source_not_published_with_the_pin_is_refused():
    e = copy.deepcopy(_live()[NASREC])
    e["facts"][0]["sources"][0]["url"] = "https://example.com/other"
    assert any("not one of the pin's published sources" in x for x in _check(NASREC, e))


def test_same_statement_cannot_be_called_independent():
    e = copy.deepcopy(_live()[NASREC])
    e["facts"][0]["basis"] = "Independent origins identified"
    assert any("rest on one statement" in x for x in _check(NASREC, e))


def test_two_reports_needs_two_sources():
    e = copy.deepcopy(_live()[DOBSONVILLE])
    e["facts"][0]["sources"] = e["facts"][0]["sources"][:1]
    assert any("cites one source" in x for x in _check(DOBSONVILLE, e))


def test_approximate_point_must_be_said_in_place():
    e = copy.deepcopy(_live()[DOBSONVILLE])
    place = next(f for f in e["facts"] if f["label"] == "Place")
    place["claim"], place["detail"] = "Snake Park, Soweto.", None
    assert any("approximate or estimated" in x for x in _check(DOBSONVILLE, e))


def test_a_fact_without_a_source_must_say_not_reported():
    e = copy.deepcopy(_live()[NASREC])
    e["facts"][4]["sources"] = []
    assert any("no source" in x for x in _check(NASREC, e))
    e["facts"][4]["basis"] = fb.NOT_REPORTED
    assert not any("no source" in x for x in _check(NASREC, e))


def test_quotes_must_be_word_for_word():
    entries = {NASREC: copy.deepcopy(_live()[NASREC])}
    page = " ".join(s["quote"] for f in entries[NASREC]["facts"] for s in f["sources"])
    assert fb.quote_problems(entries, [page.replace("'", "\u2019")]) == []
    entries[NASREC]["facts"][0]["sources"][0]["quote"] = "a sentence the page never said"
    assert fb.quote_problems(entries, [page])


def test_add_writes_nothing_when_any_entry_fails(tmp_path, monkeypatch):
    good = copy.deepcopy(_live()[NASREC])
    f = tmp_path / "fact-bases.json"
    f.write_text(json.dumps({"schema": "forge.fact_bases", "pins": {}}))
    monkeypatch.setattr(fb, "FACT_BASES", f)
    bad = copy.deepcopy(good)
    bad["facts"] = []
    assert fb.add({NASREC: good, DOBSONVILLE: bad}, "standing-x")
    assert json.loads(f.read_text())["pins"] == {}
    assert fb.add({NASREC: good}, "standing-x") == []
    written = json.loads(f.read_text())["pins"][NASREC]
    assert written["approval"] == {"by": "standing", "authorization": "standing-x", "checked_by": "unattended-run"}


def test_panel_shows_the_audit_date_of_the_entry():
    html = (ROOT / "publisher" / "templates" / "map.html").read_text(encoding="utf-8")
    assert "fb.audited_on" in html
