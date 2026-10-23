"""Priority Intelligence routing (23 Oct 2026).

PI takes organised, political and state-level crime; it must not swallow
street crime or infrastructure stories, and stories that are also a killing
or an infrastructure event follow FORGE_PI_MODE (standalone by default).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forage.collectors import pi_signals as pi  # noqa: E402
from forage.collectors import infra_signals as infra  # noqa: E402
from forage.collectors import civic_intel_collector as civic  # noqa: E402
from forage.collectors import google_news_collector as gnews  # noqa: E402
from forage.collectors import rss_collector as rss  # noqa: E402
from tools import backfill_streams as backfill  # noqa: E402


def _route(title, summary="", stream="GLOBAL", mode="standalone"):
    is_infra, meta = infra.infra_metadata(title, summary)
    if is_infra and meta.get("infra_role") != "context":
        stream = "INFRASTRUCTURE"
    return pi.route(stream, title, summary, infra_meta=meta if is_infra else None, pi_mode=mode)


@pytest.mark.parametrize("title,category", [
    ("Hawks arrest Tshwane official over R20 million tender fraud", "corruption_capture"),
    ("Construction mafia boss arrested in Durban for extortion", "organised_network"),
    ("Cash-in-transit robbers arrested in Limpopo", "organised_network"),
    ("Drug lab worth R50 million dismantled in Mpumalanga", "organised_network"),
    ("Zama zama kingpin appears in court in Klerksdorp", "illegal_mining"),
    ("Eskom says sabotage at Tutuka power station caused unit trip, Hawks investigating", "state_sabotage"),
    ("SIU proclamation to probe Sassa contracts signed by Ramaphosa", "corruption_capture"),
    ("Police officer arrested for bribery in Giyani", "corruption_capture"),
])
def test_pi_stories_reach_priority(title, category):
    stream, hit, meta = _route(title)
    assert stream == "PRIORITY" and hit
    assert meta["pi_category"] == category
    assert meta["pi_route"] == "standalone"


@pytest.mark.parametrize("title,stream", [
    ("Man stabbed to death at tavern in Seshego", "CRIME_INTEL"),
    ("Woman arrested for shoplifting in Polokwane", "CRIME_INTEL"),
    ("Two arrested for possession of drugs in Mankweng", "CRIME_INTEL"),
    ("Gang shooting leaves three dead in Hanover Park", "CRIME_INTEL"),
    ("Water outage in Tzaneen as pump fails", "INFRASTRUCTURE"),
    ("Man arrested for cable theft in Musina", "INFRASTRUCTURE"),
    ("Limpopo police launch manhunt for suspects involved in infrastructure theft", "INFRASTRUCTURE"),
])
def test_street_crime_and_infrastructure_stay_home(title, stream):
    start = "CRIME_INTEL" if stream == "CRIME_INTEL" else "GLOBAL"
    got, hit, meta = _route(title, stream=start)
    assert got == stream and not hit and meta == {}


@pytest.mark.parametrize("title,why", [
    ("Mexican cartel leader extradited to US", "not about South Africa"),
    # foreign stories Google News returned in the 23 Oct test collection
    ("Digital arrests to investment scams, Delhi Police busts syndicate behind Rs 73 cr "
     "cyber-extortion racket", "not about South Africa"),
    ("FSB Arrests Swede in Pskov Region Over Border Crossing and Sabotage Allegations - "
     "The Moscow Times", "not about South Africa"),
    ("Trio to be charged with aiding gym boss' alleged extortion bid - Gold Coast Bulletin",
     "not about South Africa"),
    ("Hawks beat Sharks in thrilling derby", "entertainment or sport"),
    ("Netflix series on the construction mafia gets a second season 2 trailer", "entertainment or sport"),
])
def test_foreign_sport_and_fiction_are_not_pi(title, why):
    c = pi.classify(title)
    assert not c["is_pi"] and why in c["reason"]


@pytest.mark.parametrize("title", [
    "Nine arrested for illegal mining - eNCA",
    "Extortion syndicate leader arrested in Umlazi",
    "Hawks arrest official over R2 million tender fraud",
])
def test_south_african_outlet_place_or_rand_is_enough(title):
    assert pi.classify(title)["is_pi"]


def test_direct_sa_feeds_need_no_place_marker():
    title = "Three arrested for illegal mining near the old shaft"
    assert not pi.classify(title)["is_pi"]
    assert pi.classify(title, sa_source=True)["is_pi"]


@pytest.mark.parametrize("title", [
    "Madlanga Commission and two others set for additional R516m funding - IOL",
    "JUSTICE MALALA: Why the Madlanga commission could face failure - Financial Mail",
    "Is Magashule the right card to lead Free State again amid corruption claims?",
])
def test_naming_a_body_opinion_and_questions_are_context(title):
    c = pi.classify(title)
    assert c["is_pi"] is False or c["role"] == "context"


def test_policy_talk_is_context_not_routed():
    stream, hit, meta = _route("ANC vows to fight corruption ahead of elections")
    assert stream == "GLOBAL" and not hit
    assert meta["pi_route"] == "context"


def test_crime_intelligence_alone_does_not_make_a_street_arrest_pi():
    stream, hit, _ = _route("Acting on crime intelligence, police arrest two for dagga in Seshego",
                            stream="CRIME_INTEL")
    assert stream == "CRIME_INTEL" and not hit


@pytest.mark.parametrize("title,home", [
    ("Ward councillor shot dead outside his home in Inanda", "CRIME_INTEL"),
    ("Taxi boss gunned down in Mthatha", "CRIME_INTEL"),
    ("Copper cable theft syndicate busted in Polokwane", "INFRASTRUCTURE"),
])
def test_overlaps_follow_the_mode(title, home):
    start = "CRIME_INTEL" if home == "CRIME_INTEL" else "GLOBAL"
    stream, hit, meta = _route(title, stream=start, mode="standalone")
    assert stream == home and hit
    assert meta["pi_route"] == "escalation_candidate" and meta["pi_home_stream"] == home
    stream, hit, meta = _route(title, stream=start, mode="escalate")
    assert stream == "PRIORITY" and hit
    assert meta["pi_route"] == "escalated" and meta["pi_home_stream"] == home


def test_mode_defaults_to_standalone(monkeypatch):
    monkeypatch.delenv("FORGE_PI_MODE", raising=False)
    assert pi.mode() == "standalone"
    monkeypatch.setenv("FORGE_PI_MODE", "escalate")
    assert pi.mode() == "escalate"
    monkeypatch.setenv("FORGE_PI_MODE", "nonsense")
    assert pi.mode() == "standalone"


def test_metadata_records_basis_and_no_analysis():
    _, _, meta = _route("Hawks arrest Tshwane official over R20 million tender fraud")
    assert meta["pi_owners"] == ["Hawks (DPCI)"]
    assert meta["pi_basis"] == "agency named: Hawks (DPCI)"
    assert meta["pi_kind"] == "arrest"
    assert set(meta) <= {"pi_category", "pi_category_label", "pi_owners", "pi_kind", "pi_basis",
                         "pi_matched", "pi_reason", "pi_mode", "pi_route", "pi_home_stream",
                         "location_candidate"}
    _, _, court = _route("Zama zama kingpin appears in court in Klerksdorp")
    assert court["pi_basis"] == "court step reported"
    assert court["location_candidate"]["place"] == "Klerksdorp"


def test_civic_collector_routes_pi_and_keeps_infra():
    src = {"source_key": "t", "stream": "CRIME_INTEL", "base_relevance": 1.5, "is_priority_default": 0}
    sig = civic.entry_to_signal({"title": "Hawks arrest Tshwane official over R20 million tender fraud",
                                 "summary": "", "link": "https://example.org/a"}, src)
    assert sig["stream"] == "PRIORITY" and sig["is_priority"] == 1
    assert sig["infra_meta"]["stream_reason"].startswith("pi: ")
    sig = civic.entry_to_signal({"title": "Man arrested for cable theft in Musina",
                                 "summary": "", "link": "https://example.org/b"}, src)
    assert sig["stream"] == "INFRASTRUCTURE"
    sig = civic.entry_to_signal({"title": "Man stabbed to death at tavern in Seshego",
                                 "summary": "", "link": "https://example.org/c"}, src)
    assert sig["stream"] == "CRIME_INTEL"


def test_civic_pi_sources_start_neutral():
    pis = [s for s in civic.SOURCES if s.get("pi_source")]
    assert len(pis) == 9
    assert all(s["stream"] == "GLOBAL" and s["is_priority_default"] == 0 for s in pis)


def test_gnews_story_words_decide_not_the_query():
    item = {"title": "Extortion syndicate leader arrested in Umlazi", "description": "",
            "query": "Umlazi", "pubdate": "Mon, 12 Oct 2026 08:00:00 GMT",
            "pubdate_parsed": "2026-10-12T08:00:00+00:00", "link": "https://example.org/x"}
    sig = gnews.build_signal(item, is_crime=False)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "PRIORITY" and sig["is_priority"] == 1
    assert meta["pi_category"] == "organised_network"
    item["title"] = "Man arrested for robbery in Umlazi"
    sig = gnews.build_signal(item, is_crime=True)
    assert sig["stream"] == "CRIME_INTEL"


def test_gnews_pi_preset_queries():
    q = pi.pi_queries()
    assert "Hawks arrest corruption" in q and len(q) == len(pi.PI_NATIONAL_TERMS)
    q = pi.pi_queries("Limpopo")
    assert q[0] == "Limpopo Hawks" and len(q) == len(pi.PI_NATIONAL_TERMS) + len(pi.PI_PROVINCE_TERMS)


def test_rss_and_backfill_agree_and_keep_the_old_priority_path():
    for t in ("Hawks arrest Tshwane official over R20 million tender fraud",
              "Man arrested for robbery in Soweto", "Flood alert for Mozambique",
              "Exclusive analysis of cyclone track"):
        assert rss.classify_stream(t, "") == backfill.classify_stream(t, "")
    assert rss.classify_stream("Hawks arrest Tshwane official over R20 million tender fraud", "") == "PRIORITY"
    assert rss.classify_stream("Man arrested for robbery in Soweto", "") == "CRIME_INTEL"
    # the pre-existing PRIORITY keyword path still runs ("as well", not instead)
    assert rss.classify_stream("Exclusive analysis of cyclone track", "") == "PRIORITY"


def test_pi_search_results_that_fail_pi_do_not_fill_the_killings_queue():
    src = next(s for s in civic.SOURCES if s["source_key"] == "pi_cit")
    sig = civic.entry_to_signal({"title": "Man arrested after shop robbery in Ohio - WLWT",
                                 "summary": "", "link": "https://example.org/o"}, src)
    assert sig["stream"] == "GLOBAL"
    sig = civic.entry_to_signal({"title": "Ward councillor shot dead outside his home in Inanda - IOL",
                                 "summary": "", "link": "https://example.org/p"}, src)
    assert sig["stream"] == "CRIME_INTEL"
    assert sig["infra_meta"]["pi_route"] == "escalation_candidate"


@pytest.mark.parametrize("title,sa_source", [
    ("Taiwan probes organised crime links to China", True),           # Daily Maverick world news
    ("DA files raps vs 5 more firms over P2.9 billion rice smuggling - Philstar.com", False),
])
def test_world_news_in_sa_outlets_and_the_philippine_da_are_not_pi(title, sa_source):
    assert not pi.classify(title, sa_source=sa_source)["is_pi"]


def test_death_toll_makes_a_killing_overlap():
    stream, hit, meta = _route("Katlehong shooting death toll rises as political killings task team investigates",
                               stream="CRIME_INTEL")
    assert stream == "CRIME_INTEL" and meta["pi_route"] == "escalation_candidate"


def test_foreign_headline_beats_sa_outlet_and_gazetteer():
    assert not pi.classify("Taiwan probes organised crime links to China - Daily Maverick")["is_pi"]
    assert not pi.classify("Plans for plaque to Belfast councillor shot dead by the UDA in 1981 on hold")["is_pi"]
    # Belfast in Mpumalanga is still South African
    assert pi.classify("Belfast man arrested for extortion of local shop owners")["is_pi"]


def test_accused_of_killing_is_a_killing_overlap():
    stream, _, meta = _route("Five accused of killing two Durban crime intelligence officers in court - IOL",
                             stream="CRIME_INTEL")
    assert stream == "CRIME_INTEL" and meta["pi_route"] == "escalation_candidate"


def test_rand_amount_keeps_a_foreign_national_story_south_african():
    assert pi.classify("How 28 RAF claims allegedly led to Zimbabwean lawyer's arrest over R5.2m fraud - News24")["is_pi"]


def test_protest_over_corruption_is_context():
    c = pi.classify("Tshiendeulu residents march over corruption and poor services",
                    "They will hand over a memorandum; the municipal manager appeared in court last month.",
                    sa_source=True)
    assert c["role"] == "context"
