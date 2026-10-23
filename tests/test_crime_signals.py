"""Batch 2 (23 Oct 2026): killings and attempted murders go to Crime & Security.

The shared classifier in forage/collectors/crime_signals.py reads the story,
not the search words. These tests cover each pattern, each exclusion, the
routing rules and both collectors, then check the Batch 2 acceptance cases on
the Batch 1 bench rows (offline).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from forage.collectors import crime_signals as crime  # noqa: E402
from forage.collectors import civic_intel_collector as civic  # noqa: E402
from forage.collectors import google_news_collector as gnews  # noqa: E402


def kind(title, summary=""):
    return crime.classify(title, summary)["kind"]


def exclusion(title, summary=""):
    return crime.classify(title, summary)["exclusion"]


# ── patterns ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Man shot dead outside tavern in Seshego",
    "Woman stabbed to death in Thohoyandou",
    "Taxi owner murdered in Giyani",
    "Two killed in an attack on a homestead near Elim",
    "Mob justice claims another life in Phiphidi - Limpopo Mirror",
    "Shopkeeper gunned down in Musina",
    "Two dead in tavern shooting in Mankweng",
])
def test_killing(title):
    assert kind(title) == "killing"


@pytest.mark.parametrize("title", [
    "Police probe attempted murder after man shot in Makhado",
    "Man wounded in shooting at Thohoyandou taxi rank",
    "Woman survived stabbing by ex-partner in Polokwane",
])
def test_attempted_murder(title):
    assert kind(title) == "attempted_murder"


def test_attempted_murder_is_never_read_as_a_killing():
    assert kind("Suspect sought for attempted murder in Seshego") == "attempted_murder"


@pytest.mark.parametrize("title", [
    "Family seeks answers after body found in Madombidzha shack",
    "Body of young man found in dam at Polokwane Golf Club - The Citizen",
    "Woman found dead near University of Limpopo campus fence - eNCA",
    "Chilling mystery of two bodies on Mashawana mountain",
])
def test_unresolved_death_is_cause_unknown_not_a_killing(title):
    assert kind(title) == "unresolved_death"
    _, hit, meta = crime.route("GLOBAL", title, "Limpopo police are investigating.")
    assert hit and meta["crime_cause_unknown"] is True and meta["crime_review"] is True
    assert meta["crime_kind"] != "killing"


def test_family_suspecting_foul_play_does_not_make_a_killing():
    assert kind("Family seeks answers after body found in Madombidzha shack",
                "Relatives suspect foul play and are calling for a police investigation.") == "unresolved_death"


@pytest.mark.parametrize("title", [
    "Man killed, 2 injured after vehicle chase at Polokwane Cricket Club - The Citizen",
    "1 dead, 2 injured in Polokwane Cricket Club parking lot incident - The Citizen",
    "3 deaths linked to Shandesh show as police probe - The Citizen",
    "Musina businessman shot more than 20 times in store - Limpopo Mirror",
    "Police investigate stabbing of Newcastle businessman - The Citizen",
])
def test_unclear_cases_go_to_review_not_killing(title):
    assert kind(title) == "review"
    _, hit, meta = crime.route("GLOBAL", title, "South Africa")
    assert hit and meta["crime_review"] is True and "crime_cause_unknown" not in meta


# ── exclusions ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Five killed in head-on collision on R524 near Makhado",
    "Police say 6 now dead after Saps vehicle crash on R524 - The Citizen",
    "R524 road in Limpopo closed following multi-vehicle collision - SABC News",
    "Three SAPS Members, Two Awaiting-Trial Prisoners And Civilian Killed In R524 Levubu Road Crash",
    "MEC pays tribute to officer killed in R524 crash",
])
def test_road_crash_is_not_a_killing(title):
    c = crime.classify(title)
    assert c["kind"] is None and c["exclusion"] == "road_crash"


@pytest.mark.parametrize("title", [
    "Hit-and-run driver kills pedestrian in Polokwane",
    "Driver arrested for culpable homicide after crash kills three",
    "Motorist shot dead after crash in Thohoyandou",
])
def test_crash_with_a_crime_is_not_excluded(title):
    assert exclusion(title) != "road_crash"


def test_drowning_without_foul_play_is_excluded():
    assert exclusion("Boy drowns in Nandoni Dam") == "drowning_no_foul_play"
    assert exclusion("Man drowned in river, police say no foul play suspected") == "drowning_no_foul_play"
    assert exclusion("Woman drowned after being pushed into dam, murder case opened") is None


@pytest.mark.parametrize("title", [
    "Henrico Oosthuizen murder case postponed to March 2027 - Tabloid Newspapers",
    "Man sentenced to life for murder of girlfriend in Seshego",
    "Accused in Tzaneen shooting appears in court",
])
def test_court_steps_are_not_new_killings_and_keep_their_stream(title):
    assert exclusion(title) == "court_step" and kind(title) is None
    for start in ("CRIME_INTEL", "GLOBAL"):
        stream, hit, meta = crime.route(start, title)
        assert stream == start and not hit and meta["crime_exclusion"] == "court_step"
    # Batch 6 owns the court-step flag the bench scores; Batch 2 must not set it.
    assert "court_step" not in meta


@pytest.mark.parametrize("title", [
    "Two dead in shack fire in Seshego",
    "Man killed by elephant in Kruger",
    "Lightning kills three herders near Giyani",
])
def test_accidents_and_disasters_are_not_killings(title):
    assert kind(title) is None and exclusion(title) == "accident_or_disaster"


@pytest.mark.parametrize("title", [
    "43 learners hospitalised with stomach complaints in Limpopo - SABC News",
    "43 Limpopo Learners Hospitalised After Stomach Illness Outbreak at Seshego School",
    "Netflix crime series shooting starts in Polokwane",
    "Hawks arrest Tshwane official over R20 million tender fraud",
])
def test_stories_without_violence_or_death_are_left_alone(title):
    assert crime.classify(title) == {"kind": None, "exclusion": None, "matched": None}
    assert crime.route("GLOBAL", title) == ("GLOBAL", False, {})


# ── routing ─────────────────────────────────────────────────────────────────

def test_routing_moves_in_keeps_moves_out_and_leaves_infra_and_pi():
    s, hit, m = crime.route("GLOBAL", "Man shot dead in Seshego")
    assert (s, hit, m["crime_route"]) == ("CRIME_INTEL", True, "moved_in")
    s, hit, m = crime.route("CRIME_INTEL", "Man shot dead in Seshego")
    assert (s, hit, m["crime_route"]) == ("CRIME_INTEL", True, "kept")
    s, hit, m = crime.route("CRIME_INTEL", "Five killed in head-on collision on R524 near Makhado")
    assert (s, hit, m["crime_route"]) == ("GLOBAL", False, "moved_out")
    assert crime.route("INFRASTRUCTURE", "Cable thief electrocuted and found dead") == ("INFRASTRUCTURE", False, {})
    assert crime.route("PRIORITY", "Councillor shot dead") == ("PRIORITY", False, {})


def test_foreign_killings_are_not_moved_into_crime():
    s, hit, m = crime.route("GLOBAL", "Gunman shot dead three people in Texas mall - CNN")
    assert s == "GLOBAL" and not hit and m["crime_route"] == "not_moved_not_south_african"


def test_metadata_keys_are_limited_to_the_crime_namespace():
    for title in ("Man shot dead in Seshego", "Body found in Polokwane veld",
                  "Five killed in head-on collision on R524 near Makhado"):
        _, _, m = crime.route("CRIME_INTEL", title)
        assert m and all(k.startswith("crime_") for k in m)


# ── collectors ──────────────────────────────────────────────────────────────

def _item(title, query, desc=""):
    return {"title": title, "description": desc, "query": query,
            "pubdate": "Mon, 19 Oct 2026 08:00:00 GMT",
            "pubdate_parsed": "2026-10-19T08:00:00Z", "link": "https://example.org/x"}


def test_gnews_story_decides_not_the_query():
    sig = gnews.build_signal(_item("Man killed, 2 injured after vehicle chase at Polokwane Cricket Club - The Citizen",
                                   "Polokwane hospital"), is_crime=False)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "CRIME_INTEL" and sig["is_priority"] == 1
    assert meta["crime_kind"] == "review" and meta["stream_reason"].startswith("crime: ")

    sig = gnews.build_signal(_item("Police say 6 now dead after Saps vehicle crash on R524 - The Citizen",
                                   "Thohoyandou police murder"), is_crime=True)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "GLOBAL" and sig["is_priority"] == 0
    assert meta["crime_exclusion"] == "road_crash" and meta["crime_route"] == "moved_out"


def test_gnews_pi_still_runs_after_crime():
    sig = gnews.build_signal(_item("Ward councillor shot dead outside his home in Inanda - IOL", "Inanda"),
                             is_crime=False)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "CRIME_INTEL"
    assert meta["crime_kind"] == "killing" and meta["pi_route"] == "escalation_candidate"


def test_civic_story_decides_not_the_source_default_or_keyword_rule():
    src = {"source_key": "t", "stream": "GLOBAL", "base_relevance": 1.0, "is_priority_default": 0,
           "fetch_mode": "rss_direct"}
    sig = civic.entry_to_signal({"title": "Body of a Man found floating at Polokwane Golf Course",
                                 "summary": "", "link": "https://example.org/g"}, src)
    assert sig["stream"] == "CRIME_INTEL" and sig["is_priority"] == 1
    assert sig["infra_meta"]["crime_kind"] == "unresolved_death"
    assert sig["infra_meta"]["crime_cause_unknown"] is True
    # "Makhado" is a CRIME_INTEL keyword rule; a crash is not a crime.
    sig = civic.entry_to_signal({"title": "Five killed in head-on collision on R524 near Makhado",
                                 "summary": "", "link": "https://example.org/h"}, src)
    assert sig["stream"] == "GLOBAL"
    assert sig["infra_meta"]["crime_exclusion"] == "road_crash"


# ── Batch 2 acceptance on the bench rows (offline) ──────────────────────────

@pytest.fixture(scope="module")
def bench():
    import collector_bench as b
    cases = {c["id"]: c for c in b.load_cases()["cases"]}
    _, rows = b.load_replay()
    return b, cases, rows


def _metas(bench, case_id):
    b, cases, rows = bench
    out = []
    with b.no_network():
        for r in b.case_rows(cases[case_id], rows):
            o = b.replay_outcome(r)
            assert not o.get("replay_error"), o
            out.append((o["stream"], r))
    return out


@pytest.mark.parametrize("case_id", ["plk_shandesh_deaths", "lt_univen_shooting", "musina_matswale_murder",
                                     "plk_golf_club_body", "plk_cricket_club_death", "lt_madombidzha_death"])
def test_bench_death_and_shooting_cases_reach_crime(bench, case_id):
    got = _metas(bench, case_id)
    assert got and all(s == "CRIME_INTEL" for s, _ in got)


def test_bench_flags(bench):
    for _, r in _metas(bench, "plk_golf_club_body"):
        assert crime.classify(r["title"], r["content"] or "")["kind"] == "unresolved_death"
    for _, r in _metas(bench, "plk_cricket_club_death"):
        assert crime.classify(r["title"], r["content"] or "")["kind"] == "review"


def test_bench_r524_is_not_counted_as_a_killing(bench):
    got = _metas(bench, "lt_r524_crash")
    assert got and all(s != "CRIME_INTEL" for s, _ in got)
    for _, r in got:
        assert crime.classify(r["title"], r["content"] or "")["kind"] != "killing"


def test_bench_newcastle_stabbing_reaches_crime(bench):
    b, _, rows = bench
    hit = [r for r in rows if r["title"].startswith("Police investigate stabbing of Newcastle businessman")]
    assert hit
    with b.no_network():
        assert all(b.replay_outcome(r)["stream"] == "CRIME_INTEL" for r in hit)


def test_bench_health_alerts_stay_where_they_were(bench):
    for stream, r in _metas(bench, "plk_seshego_school_illness"):
        assert stream == r["stream"]
