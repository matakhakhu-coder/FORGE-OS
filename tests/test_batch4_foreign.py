"""Batch 4: foreign stories and look-alike place names stay off the map.

Cases are taken from the 23 Oct Newcastle, Musina and Louis Trichardt sweep
rows (data/bench).
"""
import pytest

from forage.collectors import google_news_collector as gnews
from forage.collectors import infra_signals as infra
from forage.collectors import pi_signals as pi


# ── look-alike names ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Man charged after Windale stabbing in Newcastle NSW - 9News",
    "Woman (50s) rushed to hospital after serious Newcastle crash - Galway Beo",
    "Unai Emery hails Manzambi as Aston Villa win at Newcastle - County Times",
    "Stockton Bridge pothole repairs in Newcastle delayed again",
    "Belfast councillor linked to UDA, court hears - The Irish News",
    "Charlestown Square shooting: police appeal for witnesses in NSW",
])
def test_foreign_namesake_not_placed(text):
    loc = infra.locate(text)
    assert loc is None or loc["place"] not in ("Newcastle", "Belfast", "Charlestown")


@pytest.mark.parametrize("text,place", [
    ("Over R1 Million in Suspected Counterfeit Goods Seized in Newcastle CBD - Newcastillian News", "Newcastle"),
    ("No Water, But the Bill Still Arrives: What Newcastle Residents Can Actually Do - Newcastillian News", "Newcastle"),
    ("Newcastle, KwaZulu-Natal: police hunt gunmen after tavern shooting", "Newcastle"),
    ("Taxi violence in Madadeni and Newcastle leaves three dead", "Newcastle"),
    ("Rescued baby owl returns to the wild in Newcastle - The Citizen", "Newcastle"),
])
def test_sa_namesake_still_placed(text, place):
    loc = infra.locate(text)
    assert loc and loc["place"] == place and loc["province"] == "KwaZulu-Natal"


def test_hard_sign_beats_foreign_word():
    # Rands and the province outweigh a stray "British".
    loc = infra.locate("British tourist robbed of R20 000 in Newcastle, KZN")
    assert loc and loc["place"] == "Newcastle"


def test_soft_sign_loses_to_foreign_word():
    # "Hawks" is an SA word, but this is the Australian NBL.
    assert infra.locate("Sydney demolish Hawks in NBL as Newcastle fans watch") is None


def test_sa_check_no_longer_passes_foreign_newcastle():
    assert not pi.is_sa("Two arrests as Newcastle University buildings cordoned off - BBC")
    assert pi.is_sa("Over R1 Million in Suspected Counterfeit Goods Seized in Newcastle CBD - Newcastillian News")


def test_non_namesake_places_unchanged():
    loc = infra.locate("Musina businessman shot more than 20 times in store")
    assert loc and loc["place"] == "Musina"


# ── foreign local outlets ───────────────────────────────────────────────────

@pytest.mark.parametrize("title,src", [
    ("Detectives renew appeal over death of man found at Elermore Vale - Newcastle Weekly", ""),
    ("Banks and courts affected by disruption", "Belfast Telegraph"),
    ("Newcastle city centre cordoned off - The Northern Echo", ""),
    ("Kings import shines as Sydney demolish Hawks in NBL - Newcastle Herald", ""),
])
def test_foreign_outlets_blocked(title, src):
    assert gnews.blocked_outlet({"title": title, "source_name": src})


@pytest.mark.parametrize("title,src", [
    ("Rescued baby owl returns to the wild in Newcastle - The Citizen", ""),
    ("Musina businessman shot more than 20 times in store", "Limpopo Mirror"),
    ("Belfast water outage enters third day", "Middelburg Observer"),
])
def test_sa_outlets_not_blocked(title, src):
    assert not gnews.blocked_outlet({"title": title, "source_name": src})


# ── town keyword runs ───────────────────────────────────────────────────────

def _item(title, query):
    return {"title": title, "description": "", "query": query}


@pytest.mark.parametrize("title,query", [
    ("Durban taxi drivers protest over new ranks - IOL", "Musina protest"),
    ("Tunisians protest against pollution in southern city - SABC News", "Musina protest"),
    ("Man charged after Windale stabbing in Newcastle - 9News", "Newcastle murder"),
    ("Newcastle City Council revokes Late Night Levy - Institute of Licensing", "Newcastle water"),
])
def test_town_run_drops_other_places(title, query):
    assert not gnews.keep_for_town(_item(title, query))


@pytest.mark.parametrize("title,query", [
    # Real cases from the sweeps whose headlines name no place.
    ("Student shot dead at Phiphidi Waterfall", "Thohoyandou murder"),
    ("Municipality announces power cut for tomorrow - The Citizen", "Newcastle power outage"),
    ("Boy (12) dies in quad bike accident in Schuinshoogte", "Newcastle accident"),
    # Border-town story naming a neighbour country: kept.
    ("Suliman Carrim faces additional charges after Malawi-bound cars intercepted at Beitbridge - News24",
     "Musina fraud"),
    # Names the town, or a place near it.
    ("Musina businessman shot more than 20 times in store", "Musina murder"),
    ("Crash on Levubu Road in Vhembe claims two lives", "Makhado police murder"),
    ("Over R1 Million in Suspected Counterfeit Goods Seized in Newcastle CBD - Newcastillian News",
     "Newcastle crime"),
])
def test_town_run_keeps_local_and_placeless(title, query):
    assert gnews.keep_for_town(_item(title, query))


def test_query_without_town_keeps_everything():
    assert gnews.keep_for_town(_item("Tunisians protest against pollution", "load shedding protest"))


# ── border towns ──────────────────────────────────────────────────────────────

@pytest.fixture
def with_beitbridge(monkeypatch):
    idx = dict(infra._places())
    idx["beitbridge"] = [{"name": "Beitbridge", "province": "Limpopo", "lat": -22.218, "lng": 30.0,
                          "population": 0, "feature_code": "PPLA"}]
    monkeypatch.setattr(infra, "_places", lambda: idx)


def test_beitbridge_zimbabwe_side_not_placed(with_beitbridge):
    assert infra.locate("ZRP arrest smugglers in Beitbridge town, Zimbabwe") is None


def test_beitbridge_sa_side_placed(with_beitbridge):
    loc = infra.locate("SANDF soldiers intercept stolen cars at Beitbridge border post")
    assert loc and loc["place"] == "Beitbridge"


def test_foreign_national_in_sa_story_does_not_block_namesake():
    # Ermelo (Mpumalanga) shares a name with a Dutch town; a Nigerian suspect
    # is not a sign the story is Dutch.
    loc = infra.locate("Hawks raid Ermelo brothel, arrest Nigerian national with 'expired asylum documents'")
    assert loc and loc["place"] == "Ermelo"
