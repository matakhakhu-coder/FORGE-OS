"""Batch 3 (23 Oct 2026): place estimates and suggested precincts for every stream.

forage/collectors/place_signals.py gives Crime & Security and Africa & World
signals the place estimate Infrastructure and PI already get, adds a
suggested precinct, and flags a place that is only where an arrest or court
step happened. These tests cover the gazetteer top-up, the KwaZulu-Natal
district words, the precinct check (against tools/coverage_areas.py), the
arrest/court rule, both collectors, and the Batch 3 bench cases (offline).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from forage.collectors import infra_signals as infra  # noqa: E402
from forage.collectors import place_signals as place  # noqa: E402
from forage.collectors import civic_intel_collector as civic  # noqa: E402
from forage.collectors import google_news_collector as gnews  # noqa: E402

TOPUP = json.loads((ROOT / "data/gazetteer/collector_places_topup.json").read_text())


# ── gazetteer top-up ─────────────────────────────────────────────────────────────

def test_topup_has_the_planned_places_each_sourced_and_estimates_labelled():
    names = {p["name"] for p in TOPUP["places"]}
    assert names == {"Matswale", "Neltox", "Mashawana", "Phiphidi", "Maungani", "Tshikota",
                     "Signal Hill", "Tshamutumbu", "Botlokwa"}
    for p in TOPUP["places"]:
        assert p["source"] and p["point_basis"], p["name"]
        assert isinstance(p["estimated"], bool)
        assert -35 < p["lat"] < -22 and 16 < p["lng"] < 33


def test_topup_is_not_read_by_publish():
    # The map's place list (dist/places-za.json) must not change in Batch 3.
    assert "collector_places_topup" not in (ROOT / "tools/publish.py").read_text()


def test_section_beats_its_parent_town_and_carries_the_estimate_label():
    loc = infra.locate("A liquor store owner was shot in Matswale Phase 12, Musina.", collector_topup=True)
    assert loc["place"] == "Matswale" and loc["within"] == "Musina"
    assert loc["point_estimated"] is True and "estimate" in loc["note"]


def test_new_villages_place_with_their_context():
    assert infra.locate("Man killed at Phiphidi near Thohoyandou", collector_topup=True)["place"] == "Phiphidi"
    assert infra.locate("Shooting in Tshikota, Louis Trichardt", collector_topup=True)["place"] == "Tshikota"
    assert infra.locate("Explosives found at Botlokwa, Limpopo", collector_topup=True)["place"] == "Botlokwa"
    assert infra.locate("Man dies after Neltox shooting near Musina", collector_topup=True)["place"] == "Neltox"


def test_signal_hill_needs_newcastle():
    assert infra.locate("No water in Signal Hill, Newcastle, for a week", collector_topup=True)["place"] == "Signal Hill"
    assert infra.locate("Upper Signal Hill residents in Newcastle without power", collector_topup=True)["place"] == "Signal Hill"
    loc = infra.locate("Signal Hill hikers rescued above Cape Town", collector_topup=True)
    assert loc is None or loc["place"] != "Signal Hill"


def test_kzn_district_words_only_for_crime_placement():
    # Held for the Infrastructure KZN run: province_mentioned is unchanged.
    kzn = "KwaZulu-Natal"
    assert infra.COLLECTOR_DISTRICT_WORDS["amajuba"] == kzn
    assert infra.COLLECTOR_DISTRICT_WORDS["kzn"] == kzn
    assert "amajuba" not in infra.DISTRICT_WORDS
    assert not infra.province_mentioned("Amajuba district outage", "Limpopo")


# ── precinct ─────────────────────────────────────────────────────────────────────

def test_precinct_check_agrees_with_coverage_areas():
    import coverage_areas as ca
    precincts = json.loads((ROOT / "data/gazetteer/saps_precincts.json").read_text())["precincts"]
    points = [(p["lat"], p["lng"]) for p in TOPUP["places"]] + [
        (-23.9045, 29.4689), (-22.3450, 30.0410), (-27.7569, 29.9318), (-22.9750, 30.4500),
        (-29.8587, 31.0218), (-33.9249, 18.4241)]
    for lat, lng in points:
        want = ""
        for pr in precincts:
            rings = pr.get("rings") or []
            if rings and ca.point_in_rings(lat, lng, rings):
                want = pr["name"]
                break
        assert place.holding_precinct(lat, lng) == want, (lat, lng)


def test_suggested_precinct_is_labelled_a_suggestion():
    loc = place.add_precinct({"place": "Musina", "lat": -22.3450, "lng": 30.0410})
    assert loc["precinct"] == "Musina" and loc["precinct_basis"].startswith("suggested")


def test_source_named_station_wins_over_the_estimated_point():
    loc = infra.locate("Two bodies found on Mashawana mountain near Thohoyandou", collector_topup=True)
    assert loc["place"] == "Mashawana"
    assert place.add_precinct(loc)["precinct"] == "Thohoyandou"


# ── arrest and court places ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Three police officers arrested for alleged corruption in Polokwane",
    "5 cops arrested at Polokwane airport",
    "Accused appeared in court in Polokwane on Monday",
    "Suliman Carrim faces Musina court over alleged vehicle theft",
    "Eleven suspected illegal miners due in Stilfontein Magistrate's Court",
    "Officers stationed at Polokwane were suspended",
])
def test_arrest_or_court_place_is_flagged_not_placed(text):
    loc, review = place.estimate(text)
    assert loc is None
    assert review["place_review"] == "arrest_or_court_place"
    assert review["arrest_place_candidate"]["place"]


@pytest.mark.parametrize("text,want", [
    ("Four arrested after suspected drug lab discovered at Soweto house", "Soweto"),
    ("Three people convicted for murder of IFP branch secretary in Weenen", "Weenen"),
    ("Man shot dead in Polokwane; suspect later arrested", "Polokwane"),
    ("Polokwane man arrested for stock theft", "Polokwane"),
])
def test_incident_places_are_still_placed(text, want):
    loc, review = place.estimate(text)
    assert loc and loc["place"] == want and review is None


def test_incident_place_wins_when_the_court_is_elsewhere():
    loc, review = place.estimate("Middelburg High Court freezes R178.6m in Standerton drug syndicate assets")
    assert loc["place"] == "Standerton"
    assert review["arrest_place_candidate"]["place"] == "Middelburg"


# ── add_place and the collectors ───────────────────────────────────────────────

def test_add_place_never_moves_an_existing_candidate():
    meta = {"location_candidate": {"place": "Seshego", "lat": -23.85, "lng": 29.38}}
    out = place.add_place("CRIME_INTEL", meta, "Man shot dead in Musina")
    assert out["location_candidate"]["place"] == "Seshego"


def test_add_place_only_for_crime_and_security():
    # Merged for Crime & Security only (23 Oct 2026); Africa & World held back.
    assert "location_candidate" not in place.add_place("INFRASTRUCTURE", {}, "Musina water outage")
    assert "location_candidate" not in place.add_place("PRIORITY", {}, "Musina councillor charged")
    assert "location_candidate" not in place.add_place("GLOBAL", {}, "Elephants roam near Musina")
    assert place.add_place("CRIME_INTEL", {}, "Man shot dead in Musina")["location_candidate"]["place"] == "Musina"


def test_other_streams_are_returned_untouched():
    # No precinct is added to an Infrastructure or PI candidate in this merge.
    meta = {"location_candidate": {"place": "Thohoyandou", "lat": -22.97, "lng": 30.47}}
    out = place.add_place("INFRASTRUCTURE", json.loads(json.dumps(meta)), "Thohoyandou water outage")
    assert out == meta


def test_topup_and_kzn_words_not_used_outside_crime():
    # Infrastructure and PI placement read the gazetteer as before Batch 3.
    assert infra.locate("Man dies after Neltox shooting near Musina")["place"] == "Musina"
    assert infra.locate("Man dies after Neltox shooting near Musina", collector_topup=True)["place"] == "Neltox"


def _item(title, query, desc=""):
    return {"title": title, "description": desc, "query": query,
            "pubdate": "Mon, 19 Oct 2026 08:00:00 GMT",
            "pubdate_parsed": "2026-10-19T08:00:00Z", "link": "https://example.org/x"}


def test_gnews_crime_signal_gets_a_place_and_precinct():
    sig = gnews.build_signal(_item("Musina businessman shot more than 20 times in store - Limpopo Mirror",
                                   "Musina murder"), is_crime=True)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "CRIME_INTEL"
    assert meta["location_candidate"]["place"] == "Musina"
    assert meta["location_candidate"]["precinct"] == "Musina"


def test_gnews_crime_arrest_place_is_flagged():
    sig = gnews.build_signal(_item("Three police officers arrested for alleged corruption in Polokwane - SABC News",
                                   "Polokwane murder"), is_crime=True)
    meta = json.loads(sig["metadata_json"])
    if sig["stream"] == "CRIME_INTEL":
        assert "location_candidate" not in meta
        assert meta["place_review"] == "arrest_or_court_place"


def test_civic_world_signal_not_placed_while_held():
    src = {"source_key": "t", "stream": "GLOBAL", "base_relevance": 1.0, "is_priority_default": 0,
           "fetch_mode": "rss_direct"}
    sig = civic.entry_to_signal({"title": "Musina residents warned as elephants roam near town centre",
                                 "summary": "", "link": "https://example.org/e"}, src)
    assert sig["stream"] == "GLOBAL"
    assert "location_candidate" not in (sig.get("infra_meta") or {})


# ── Batch 3 cases on the bench rows (offline) ──────────────────────────────────

@pytest.fixture(scope="module")
def bench():
    import collector_bench as b
    cases = {c["id"]: c for c in b.load_cases()["cases"]}
    _, rows = b.load_replay()
    return b, cases, rows


def _outs(bench, case_id):
    b, cases, rows = bench
    out = []
    with b.no_network():
        for r in b.case_rows(cases[case_id], rows):
            o = b.replay_outcome(r)
            assert not o.get("replay_error"), o
            out.append(o)
    return out


def test_bench_musina_murder_is_placed_with_a_precinct(bench):
    # The saved text says only "Musina businessman..."; Matswale is in the
    # article body, which the collectors don't read (Batch 6).
    outs = _outs(bench, "musina_matswale_murder")
    assert outs and all(o["place"] == "Musina" and o["precinct"] == "Musina" for o in outs)


def test_bench_carrim_pi_placement_unchanged(bench):
    # PI's court rule stays on batch3-places (it cost Carrim's only correct
    # Musina placement). The Crime & Security merge must not change PI.
    outs = _outs(bench, "musina_carrim")
    assert sum(1 for o in outs if o["place"] == "Musina") == 2


@pytest.mark.parametrize("case_id,want", [("lt_phiphidi_killing", "Phiphidi"),
                                          ("lt_madombidzha_death", "Madombidzha")])
def test_bench_crime_cases_now_placed(bench, case_id, want):
    outs = _outs(bench, case_id)
    assert outs and all(o["place"] == want and o["precinct"] for o in outs)


def test_springbok_team_is_not_the_town():
    loc, review = place.estimate("Springbok Women to face Ireland challenge for the first time in 20 years - Daily Maverick")
    assert loc is None and review is None
