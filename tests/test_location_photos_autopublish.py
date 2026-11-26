"""Location photos in the unattended run (26 Nov 2026). docs/signals/LOCATION_PHOTOS.md

Every new pin gets photo research; no pin is ever blocked by it, and no photo
is ever approved by it."""
import json

import pytest

from tools import autopublish as apub
from tools import location_photos as lp

PIN = {"title": "Nasrec Road, Riverlea (approx.): road collapses", "lat": -26.2155,
       "lng": 27.98481, "location_note": "Nasrec Road between Main Reef Road and Dunlin Drive"}


def row(title, refusals=(), dist=900):
    return {"file_title": title, "page_url": "https://commons.wikimedia.org/wiki/" + title,
            "distance_m": dist, "licence": "CC BY-SA 3.0", "creator": "Someone",
            "auto_refusals": list(refusals)}


@pytest.fixture(autouse=True)
def cand_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "CANDIDATES_DIR", tmp_path / "candidates")
    return tmp_path / "candidates"


@pytest.mark.parametrize("text", [
    "10,000 Tanzanian shillings banknote, obverse",
    "Southern Blue Waxbill (Uraeginthus angolensis ssp. niassensis). Subspecies of bird.",
    "This is an image of food from", "The African Cousine 2",
    "Pallet jacks supplied by East Elite Lifting Equipment"])
def test_things_found_near_real_pins_that_are_not_places_are_off_topic(text):
    assert lp.OFF_TOPIC.search(text)


@pytest.mark.parametrize("text", ["The M70 (Soweto Highway) next to Soccer City in Nasrec",
                                  "Peter Mokaba Stadium, Polokwane"])
def test_places_are_not_off_topic(text):
    assert not lp.OFF_TOPIC.search(text)


def test_research_writes_candidates_and_names_the_nearest_usable(cand_dir):
    search = lambda lat, lng: [row("File:Banknote.jpg", ["off-topic"], 100), row("File:Soweto Highway-001.jpg")]
    written, lines = lp.research_new_pins({"84d9b687-x": PIN}, search)
    assert written == [cand_dir / "84d9b687.json"]
    data = json.loads(written[0].read_text())
    assert len(data["candidates"]) == 2 and "owner" in data["note"]
    assert "Soweto Highway" in lines[0] and "Banknote" not in lines[0]


def test_no_usable_photo_publishes_without_one():
    _, lines = lp.research_new_pins({"aaaaaaaa": PIN}, lambda lat, lng: [row("File:Bird.jpg", ["off-topic"])])
    assert "published without one" in lines[0]


def test_a_failed_lookup_never_raises():
    def boom(lat, lng):
        raise OSError("Commons down")
    written, lines = lp.research_new_pins({"aaaaaaaa": PIN}, boom)
    assert written == [] and "failed" in lines[0] and "without one" in lines[0]


def test_homes_and_schools_are_not_even_searched():
    called = []
    pin = dict(PIN, title="Madiba Park, Seshego: robbery", location_note="At the school")
    _, lines = lp.research_new_pins({"e79e7810": pin}, lambda *a: called.append(a) or [])
    assert called == [] and "school" in lines[0]


def test_research_never_approves_a_photo(cand_dir):
    before = lp.load_registry()
    lp.research_new_pins({"aaaaaaaa": PIN}, lambda lat, lng: [row("File:Soweto Highway-001.jpg")])
    assert lp.load_registry() == before


def test_autopublish_research_cannot_stop_a_run(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_SKIP_PHOTO_RESEARCH", raising=False)
    written, lines = apub._photo_research(["pin1"], tmp_path / "missing.db")
    assert written == [] and lines and "without photos" in lines[0]


def test_autopublish_commits_the_research_with_the_batch():
    assert "docs/photos/candidates" in apub.DATA_PATHS
    assert "5b. Location photos" in apub.__doc__


# --- Run-approved place photos (owner's choice, 26 Nov 2026) -----------------

def _run_approved(sid="84d9b687-ee31-5b74-8191-a647309559d0", auth="standing-test", **over):
    reg = lp.load_registry()
    base = next(p for p in reg["photos"] if p["id"] == "riverlea-m70-soweto-highway-2008")
    p = json.loads(json.dumps(base))
    p["signal_id"] = sid
    p["approval"] = {"by": "standing", "at": "2026-11-26", "authorization": auth}
    p["privacy"]["checked_by"] = "unattended-run"
    p["privacy"]["note"] = "Run looked at it: open highway, no faces, no readable plates, no homes."
    p.update(over)
    return p


def test_a_run_approved_place_photo_passes():
    assert lp.validate_photo(_run_approved()) == []


def test_a_run_cannot_approve_an_incident_photo():
    assert any("place photos only" in e for e in lp.validate_photo(_run_approved(kind="incident")))


def test_a_run_approval_needs_its_own_look_written_down():
    p = _run_approved()
    p["privacy"]["note"] = ""
    assert any("unattended-run" in e for e in lp.validate_photo(p))
    p = _run_approved()
    p["approval"]["authorization"] = ""
    assert any("authorization" in e for e in lp.validate_photo(p))


def test_run_approved_photo_for_another_pin_is_refused():
    reg = {"photos": [_run_approved(sid="someone-else")]}
    assert lp.standing_photo_problems(reg, "standing-test", ["84d9b687-ee31-5b74-8191-a647309559d0"])
    assert not lp.standing_photo_problems(reg, "standing-test", ["someone-else"])


def test_refused_photos_go_back_to_candidate(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text(json.dumps({"photos": [_run_approved()]}))
    lp.demote(path, {"riverlea-m70-soweto-highway-2008"})
    assert json.loads(path.read_text())["photos"][0]["status"] == "candidate"
