"""Regression tests for the infrastructure rebuild of the civic and Google News
collectors (23 Oct 2026). Cases are real headlines from the Limpopo
1-23 Oct 2026 benchmark. Offline: no network."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from forage.collectors import infra_signals as I
from forage.collectors import civic_intel_collector as civic
from forage.collectors import google_news_collector as gnews


@pytest.mark.parametrize("title,summary,etype,kind", [
    ("Tzaneen residents await repair of burst water pipe", "", "water_failure", "incident"),
    ("Water outage in Polokwane after pipeline leaks", "", "water_failure", "incident"),
    ("Planned water outage for Polokwane and Mankweng on Wednesday", "", "water_failure", "planned"),
    ("PIPE UNBLOCKED AT MHINGA ZONE 1 – WATER SUPPLY RESTORED", "", "water_failure", "restored"),
    ("Zimbabwean national arrested with illegal firearm and stolen copper in Groblersdal", "", "cable_theft", "arrest"),
    ("Man electrocuted in Booysens cable theft attempt", "", "cable_theft", "incident"),
    ("Cable thieves cause power outage in Mokopane", "", "cable_theft", "incident"),
    ("Limpopo police launch manhunt for suspects involved in infrastructure theft", "", "cable_theft", "incident"),
    ("Stasie Road potholes fixed, bridge section still a worry", "", "road_bridge", "incident"),
    ("Seshego Hospital substation tampering causes R500k damage", "", "substation_transformer", "incident"),
    ("LOW WATER SUPPLY IN MUSINA TOWNSHIP AND SURROUNDING AREAS",
     "Several boreholes along the Limpopo River are non-functional due to an electrical breakdown",
     "water_failure", "incident"),
])
def test_classify_infrastructure_events(title, summary, etype, kind):
    c = I.classify(title, summary)
    assert c["is_infra"] and c["event_type"] == etype and c["kind"] == kind


@pytest.mark.parametrize("title", [
    "Gugulethu granny recalls the day she was attacked",
    "Eskom welcomes Nersa decision on tariff",
    "Seshego residents hit with water damages claims",
    "No load shedding in over 500 days, says Eskom - South African Government",
    "Eskom launching enough solar and batteries to cover 6 stages of load shedding",
    "Court finds 'water tanker mafia' posts cause damage and erode public trust",
])
def test_not_infrastructure_events(title):
    ok, meta = I.infra_metadata(title, "")
    assert not ok or meta["infra_role"] == "context"


def test_procurement_and_handover_are_context_not_events():
    for t in ("R25m transformer deal: Hawks arrest official",
              "EXECUTIVE MAYOR HANDS OVER BOREHOLE AT NGHOMUNGHOMU VILLAGE"):
        ok, meta = I.infra_metadata(t, "")
        assert ok and meta["infra_role"] == "context"


def test_locate_limpopo_towns_and_province_fix():
    assert I.locate("Tzaneen residents await repair of burst water pipe")["place"] == "Tzaneen"
    g = I.locate("stolen copper in Groblersdal", province="Limpopo")
    assert g["place"] == "Groblersdal" and g["province"] == "Limpopo"
    assert g["precision"] == "town_centroid_estimate"
    # national copy does not land on a same-named hamlet
    assert I.locate("Hope for the George fire victims") is None


def test_window():
    now = datetime(2026, 10, 23, 11, tzinfo=timezone.utc)
    assert I.in_window("2026-10-01T08:00:00Z", 30, now)
    assert I.in_window("2026-10-01 08:00:00", 30, now)
    assert not I.in_window("2017-05-04 10:00:00", 30, now)
    assert I.in_window("2017-05-04 10:00:00", 0, now)


def _entry(title, summary="", link="https://example.org/a", when=None):
    when = when or datetime.now(timezone.utc) - timedelta(days=2)
    return {"title": title, "summary": summary, "link": link, "published_parsed": when.timetuple()}


def _source(**kw):
    s = {"source_key": "letaba_herald", "label": "Letaba Herald", "fetch_mode": "rss_direct",
         "url": "https://example.org/feed/", "stream": "GLOBAL", "province": "Limpopo",
         "base_relevance": 1.4, "is_priority_default": 0}
    s.update(kw)
    return s


def test_civic_routes_cable_theft_to_infrastructure_not_crime():
    sig = civic.entry_to_signal(_entry("Zimbabwean national arrested with illegal firearm and stolen copper in Groblersdal"), _source())
    assert sig["stream"] == "INFRASTRUCTURE"
    m = sig["infra_meta"]
    assert m["infra_event_type"] == "cable_theft" and m["infra_role"] == "arrest_only"
    assert m["location_candidate"]["place"] == "Groblersdal"
    assert sig["lat"] is None and sig["lng"] is None  # candidate only, not a map point
    assert sig["timestamp"].endswith("Z")


def test_civic_blanket_infrastructure_default_removed():
    src = [s for s in civic.SOURCES if s["source_key"] == "groundup"][0]
    sig = civic.entry_to_signal(_entry("Gugulethu granny recalls the day she was attacked"), src)
    assert sig["stream"] != "INFRASTRUCTURE"
    # a bare 'municipality' keyword is not an event either
    sig = civic.entry_to_signal(_entry("Municipality approves new budget"), _source())
    assert sig["stream"] != "INFRASTRUCTURE"


def test_civic_municipal_notice_area_fallback():
    sig = civic.entry_to_signal(_entry("PIPE UNBLOCKED AT MHINGA ZONE 1 – WATER SUPPLY RESTORED"),
                                _source(area_fallback="Vhembe District"))
    assert sig["stream"] == "INFRASTRUCTURE"
    loc = sig["infra_meta"]["location_candidate"]
    assert loc["place"] == "Vhembe District" and loc["precision"] == "issuing_municipality_area"


def test_civic_has_limpopo_local_and_municipal_sources():
    keys = {s["source_key"] for s in civic.SOURCES}
    assert {"review_online", "letaba_herald", "limpopo_chronicle",
            "vhembe_dm", "polokwane_municipality"} <= keys
    for s in civic.SOURCES:
        if s["source_key"] in ("eskom_news", "municipal_failures", "groundup"):
            assert s["stream"] != "INFRASTRUCTURE"


def test_gnews_build_signal_routes_infrastructure():
    item = {"title": "Tzaneen residents await repair of burst water pipe - Letaba Herald",
            "link": "https://news.google.com/x", "pubdate": "Mon, 19 Oct 2026 08:00:00 GMT",
            "pubdate_parsed": "2026-10-19T08:00:00Z", "description": "", "source_name": "Letaba Herald",
            "query": "Tzaneen water outage"}
    sig = gnews.build_signal(item, False, province="Limpopo")
    m = json.loads(sig["metadata_json"])
    assert sig["stream"] == "INFRASTRUCTURE" and m["infra_event_type"] == "water_failure"
    assert m["location_candidate"]["place"] == "Tzaneen" and sig["lat"] is None
    # old two-argument call (tools/stage_gnews_window.py) still works
    assert gnews.build_signal(item, False)["stream"] == "INFRASTRUCTURE"


def test_gnews_infra_queries_cover_province_towns():
    q = gnews.infra_queries("Limpopo")
    assert "Polokwane cable theft" in q and "Tzaneen water outage" in q
    assert any(x.startswith("Limpopo ") for x in q)
    assert not any("Johannesburg" in x for x in q)


def test_province_filter_drops_other_countries():
    assert not I.province_mentioned("Harare residents go without water for weeks", "Limpopo")
    assert I.province_mentioned("Water outage in Polokwane after pipeline leaks", "Limpopo")


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("create table signals (signal_id text, source_type text, stream text, metadata_json text, timestamp text)")
    return c


def test_arrest_links_to_earlier_theft_incident():
    c = _db()
    ok, inc = I.infra_metadata("Cable thieves cause power outage in Mokopane", "")
    c.execute("insert into signals values ('s1','live','INFRASTRUCTURE',?,?)",
              (json.dumps(inc), "2026-10-05T08:00:00Z"))
    ok, arr = I.infra_metadata("Two arrested for cable theft in Mokopane", "")
    assert arr["infra_role"] == "arrest_only"
    arr = I.link_supporting(c, arr, "2026-10-12T08:00:00Z")
    assert arr["infra_role"] == "supporting_evidence" and arr["supports_signal_id"] == "s1"


def test_separate_incidents_a_week_apart_stay_separate():
    c = _db()
    ok, a = I.infra_metadata("Tzaneen residents await repair of burst water pipe", "")
    c.execute("insert into signals values ('s1','live','INFRASTRUCTURE',?,?)", (json.dumps(a), "2026-10-05T08:00:00Z"))
    ok, b = I.infra_metadata("New burst water pipe leaves Tzaneen dry", "")
    b = I.link_supporting(c, b, "2026-10-14T08:00:00Z")
    assert b["infra_role"] == "event" and "supports_signal_id" not in b
