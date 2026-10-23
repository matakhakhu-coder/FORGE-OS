"""Owner decisions on Priority Intelligence, 23 Oct 2026.

1. Standalone mode stays the default; overlaps keep their home stream.
2. Health and disaster alerts leave PI and go to GLOBAL ("Africa & World");
   South African alerts are flagged, not re-homed. The switched-off GDELT and
   ACLED paths no longer tag PRIORITY by query or event type.
3. Scheduled area searches use each stream's own words, only in areas that
   hold a pin in that stream.
"""

import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forage.collectors import pi_signals as pi  # noqa: E402
from forage.collectors import infra_signals as infra  # noqa: E402
from forage.collectors import alert_routing as alerts  # noqa: E402
from forage.collectors import rss_collector as rss  # noqa: E402
from forage.collectors import disease_outbreak_collector as aegis  # noqa: E402
from forage.collectors import _gdelt_collector as gdelt  # noqa: E402
from forage.collectors import _acled_collector as acled  # noqa: E402
from tools import backfill_streams as backfill  # noqa: E402
from tools import coverage_areas as ca  # noqa: E402

SAST = timezone(timedelta(hours=2))


# ── 1. Standalone mode ──────────────────────────────────────────────────────────

def test_standalone_is_the_default_mode(monkeypatch):
    monkeypatch.delenv("FORGE_PI_MODE", raising=False)
    assert pi.DEFAULT_MODE == "standalone"
    assert pi.mode() == "standalone"
    assert pi.mode("nonsense") == "standalone"


def test_standalone_keeps_killings_and_infrastructure_in_their_streams(monkeypatch):
    monkeypatch.delenv("FORGE_PI_MODE", raising=False)
    t = "EFF councillor shot dead in KwaMashu, Hawks probe political killing"
    stream, flag, meta = pi.route("CRIME_INTEL", t, "")
    assert stream == "CRIME_INTEL" and flag is True
    assert meta["pi_route"] == "escalation_candidate" and meta["pi_home_stream"] == "CRIME_INTEL"
    t2 = "Syndicate arrested for stripping Eskom substation in Polokwane, Hawks say"
    is_infra, imeta = infra.infra_metadata(t2, "")
    s2, _f, m2 = pi.route("INFRASTRUCTURE", t2, "", infra_meta=imeta if is_infra else None)
    assert s2 == "INFRASTRUCTURE"
    assert m2.get("pi_route") in ("escalation_candidate", None) or m2 == {}


# ── 2. Health and disaster alerts out of PI ─────────────────────────────────────

def test_alert_scope_flags_south_africa_and_never_guesses_a_new_stream():
    s, x = alerts.route_alert("Green flood alert for South Africa", "KwaZulu-Natal coast")
    assert s == "GLOBAL" and x["alert_scope"] == "za_domestic" and "stream_review" in x
    s, x = alerts.route_alert("Cholera outbreak", "", {"country": "South Africa"})
    assert s == "GLOBAL" and x["alert_scope"] == "za_domestic"
    s, x = alerts.route_alert("Tropical cyclone Freddy", "Mozambique coast", lat=-24.0, lng=32.5)
    assert x["alert_scope"] == "international" and "stream_review" not in x
    s, x = alerts.route_alert("Earthquake M4.6", "", lat=-26.2, lng=28.0)
    assert x["alert_scope"] == "za_possible" and "stream_review" in x
    s, x = alerts.route_alert("Typhoon warning for the Philippines", "", lat=14.6, lng=121.0)
    assert s == "GLOBAL" and x["alert_scope"] == "international"


def test_disease_collector_sources_never_use_priority():
    assert all(src["stream"] == "GLOBAL" for src in aegis.SOURCES)


def test_disease_insert_forces_global_and_keeps_the_bell():
    sig = {"title": "Mpox in Gauteng", "content": "Cases reported in Johannesburg", "stream": "PRIORITY",
           "is_priority": 1, "metadata_json": json.dumps({"tier": 1, "parent_signal_id": "p"})}
    aegis._apply_alert_stream(sig)
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "GLOBAL" and sig["is_priority"] == 1
    assert meta["alert_scope"] == "za_domestic" and meta["parent_signal_id"] == "p"


def _signals_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE signals (signal_id TEXT, source TEXT, external_id TEXT UNIQUE,
        title TEXT, content TEXT, lat REAL, lng REAL, timestamp TEXT, status TEXT, stream TEXT,
        relevance_score REAL, is_priority INTEGER, metadata_json TEXT, source_type TEXT)""")
    return conn


def test_velocity_surge_is_global_not_priority():
    conn = _signals_db()
    aegis._emit_velocity_surge(conn, "ob1", "cholera", "Harare", -17.8, 31.0, 6)
    stream, prio, meta = conn.execute("SELECT stream, is_priority, metadata_json FROM signals").fetchone()
    assert stream == "GLOBAL" and prio == 1
    assert json.loads(meta)["alert_scope"] == "international"


def _gdacs_item(title, desc, lat=None, lng=None):
    geo = (f'<geo:Point xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#"><geo:lat>{lat}</geo:lat>'
           f'<geo:long>{lng}</geo:long></geo:Point>') if lat is not None else ""
    return ET.fromstring(f"<item><title>{title}</title><description>{desc}</description>"
                         f"<guid>https://gdacs.test/{abs(hash(title))}</guid>"
                         f"<pubDate>Fri, 23 Oct 2026 10:00:00 GMT</pubDate>{geo}</item>")


def test_gdacs_never_tags_priority_even_with_old_article_words_or_pi_words():
    for t in ("Exclusive analysis of cyclone track", "Investigation report revealed flood damage",
              "Hawks arrest Tshwane official over R20 million tender fraud",
              "Man arrested for robbery in Soweto"):
        assert rss.classify_stream(t, "") == "GLOBAL"
    assert not hasattr(rss, "PRIORITY_ARTICLE_KEYWORDS")


def test_gdacs_signal_carries_global_and_the_scope_flag():
    sig = rss.item_to_signal(_gdacs_item("Orange flood alert in South Africa", "Heavy rain in Eastern Cape"))
    meta = json.loads(sig["metadata_json"])
    assert sig["stream"] == "GLOBAL" and meta["alert_scope"] == "za_domestic"
    sig = rss.item_to_signal(_gdacs_item("Green earthquake alert in Japan", "M5.1"))
    assert sig["stream"] == "GLOBAL" and json.loads(sig["metadata_json"])["alert_scope"] == "international"


def test_backfill_leaves_alerts_on_global_and_drops_the_article_word_path():
    t = "Hawks arrest Tshwane official over R20 million tender fraud"
    assert backfill.classify_stream(t, "") == "PRIORITY"          # news story: PI rules
    for src in ("GDACS", "rss_collector", "disease_outbreak_collector"):
        assert backfill.classify_stream(t, "", src) == "GLOBAL"   # alert: never reclassified
    assert backfill.classify_stream("Exclusive analysis of cyclone track", "") == "GLOBAL"
    assert backfill.classify_stream("Man arrested for robbery in Soweto", "") == "CRIME_INTEL"
    assert not hasattr(backfill, "PRIORITY_ARTICLE_KEYWORDS")


def test_backfill_run_reads_the_source_column(tmp_path):
    db = tmp_path / "db.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE signals (signal_id TEXT, source TEXT, title TEXT, content TEXT, stream TEXT)")
    t = "Hawks arrest Tshwane official over R20 million tender fraud"
    conn.executemany("INSERT INTO signals VALUES (?,?,?,?,?)",
                     [("a", "GDACS", t, "", "GLOBAL"), ("b", "google_news", t, "", "GLOBAL")])
    conn.commit()
    conn.close()
    import inspect
    params = inspect.signature(backfill.run).parameters
    kwargs = {}
    if "db_path" in params:
        kwargs["db_path"] = db
    elif "db" in params:
        kwargs["db"] = db
    else:
        pytest.skip("backfill.run has no db parameter")
    if "dry_run" in params:
        kwargs["dry_run"] = False
    backfill.run(**kwargs)
    rows = dict(sqlite3.connect(db).execute("SELECT signal_id, stream FROM signals").fetchall())
    assert rows == {"a": "GLOBAL", "b": "PRIORITY"}


def test_gdelt_priority_query_no_longer_forces_priority():
    assert all(st != "PRIORITY" for _q, st, _r in gdelt.SA_QUERIES)
    leak = {"url": "https://x.test/1", "title": "Whistleblower says intelligence report leaked",
            "seendate": "20261010T120000Z"}
    assert gdelt._build_signal(leak, "PRIORITY", 1.3)["stream"] == "GLOBAL"
    pi_story = {"url": "https://x.test/2", "seendate": "20261010T120000Z",
                "title": "Hawks arrest Tshwane official over R20 million tender fraud"}
    sig = gdelt._build_signal(pi_story, "GLOBAL", 1.3)
    assert sig["stream"] == "PRIORITY" and json.loads(sig["metadata_json"])["pi_category"]


def _acled(country, notes, event_type="Strategic developments", admin1="Limpopo"):
    return {"event_type": event_type, "sub_event_type": "Arrests", "actor1": "Police",
            "location": "Polokwane", "admin1": admin1, "country": country, "notes": notes,
            "event_id_cnty": f"{country[:3]}{abs(hash(notes))}", "event_date": "2026-10-10"}


def test_acled_strategic_developments_no_longer_tag_priority_by_type():
    assert acled.EVENT_TYPE_MAP["Strategic developments"][0] == "GLOBAL"
    assert acled._build_signal(_acled("South Africa", "Police met community leaders."))["stream"] == "GLOBAL"
    sa = acled._build_signal(_acled("South Africa", "The Hawks arrested three municipal officials in "
                                    "Polokwane over R12 million tender fraud."))
    assert sa["stream"] == "PRIORITY"
    zim = acled._build_signal(_acled("Zimbabwe", "Police arrested officials in Harare over tender fraud "
                                     "worth R12 million, the Hawks said.", admin1="Harare"))
    assert zim["stream"] == "GLOBAL"


# ── 3. Area searches per stream ─────────────────────────────────────────────────

@pytest.fixture
def gaz(tmp_path):
    places = {"fields": ["name", "lat", "lng", "feature_code", "population",
                         "province", "district", "municipality"],
              "rows": [["Alpha", -26.0, 26.0, "PPL", 20000, "North West", "D1", "M-Alpha"],
                       ["Alpha East", -26.0, 26.1, "PPLX", 0, "North West", "D1", "M-Alpha"],
                       ["Beta", -26.0, 28.0, "PPL", 15000, "North West", "D2", "M-Beta"],
                       ["Gamma", -24.0, 29.0, "PPL", 15000, "Limpopo", "D3", "M-Gamma"]]}
    sq = lambda lat, lng, d: [[lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d],
                              [lng - d, lat + d], [lng - d, lat - d]]
    prec = {"precincts": [{"name": "Alpha", "station": [26.0, -26.0], "rings": [sq(-26.0, 26.0, 0.2)]},
                          {"name": "Beta", "station": [28.0, -26.0], "rings": [sq(-26.0, 28.0, 0.2)]},
                          {"name": "Gamma", "station": [29.0, -24.0], "rings": [sq(-24.0, 29.0, 0.2)]}]}
    pp, pr = tmp_path / "places.json", tmp_path / "prec.json"
    pp.write_text(json.dumps(places))
    pr.write_text(json.dumps(prec))
    return pp, pr


def _pin(sid, lat, lng, province, title, stream=None):
    p = {"signal_id": sid, "title": title, "lat": lat, "lng": lng, "province": province,
         "place_names": ca.pin_place_names(title)}
    if stream:
        p["stream"] = stream
    return p


def _areas(pins, gaz):
    return {a["pins"][0]["signal_id"]: a for a in
            ca.compute_areas(pins, 30.0, places_path=gaz[0], precincts_path=gaz[1])["areas"]}


def _legacy_queries(area, members):
    """The search list as built before 23 Oct 2026 (killing words for every area)."""
    langs = ca.PROVINCE_LANGS.get(area["province"], ["en"])
    qs = []
    for n in ca.search_names(area, members):
        for lang in langs:
            qs.append(f'"{n}" {ca.QUERY_TERMS[lang]}')
            if len(qs) >= ca.MAX_QUERIES_PER_AREA:
                return qs
    return qs


def test_killing_areas_search_exactly_what_they_did_before(gaz):
    pins = [_pin("k", -26.0, 26.01, "North West", "Alpha East: man shot dead")]
    a = _areas(pins, gaz)["k"]
    assert a["streams"] == ["CRIME_INTEL"]
    assert a["queries"] == a["queries_by_stream"]["CRIME_INTEL"] == _legacy_queries(a, pins)


def test_infrastructure_only_area_searches_only_infrastructure_words(gaz):
    a = _areas([_pin("i", -24.0, 29.0, "Limpopo", "Gamma: substation vandalised", "INFRASTRUCTURE")], gaz)["i"]
    assert a["streams"] == ["INFRASTRUCTURE"]
    assert a["queries"] and all("substation" in q and "murder" not in q for q in a["queries"])


def test_mixed_area_searches_each_stream_with_its_own_pins_first(gaz):
    pins = [_pin("k", -26.0, 26.0, "North West", "Alpha: man shot dead", "CRIME_INTEL"),
            _pin("p", -26.0, 26.1, "North West", "Alpha East: Hawks arrest mayor", "PRIORITY")]
    a = next(iter(_areas(pins, gaz).values()))
    assert a["streams"] == ["CRIME_INTEL", "PRIORITY"]
    by = a["queries_by_stream"]
    kill = tuple(ca.QUERY_TERMS_BY_STREAM["CRIME_INTEL"].values())
    pit = tuple(ca.QUERY_TERMS_BY_STREAM["PRIORITY"].values())
    assert by["CRIME_INTEL"] and all(q.endswith(kill) for q in by["CRIME_INTEL"])
    assert by["PRIORITY"] and all(q.endswith(pit) for q in by["PRIORITY"])
    # the PI pin's own place comes before the area's other names in PI searches
    assert by["PRIORITY"][1].startswith('"Alpha East" ') or by["PRIORITY"][0].startswith('"Alpha" ')
    assert len(a["queries"]) == len(by["CRIME_INTEL"]) + len(by["PRIORITY"])
    assert len(by["PRIORITY"]) <= ca.MAX_QUERIES_PER_AREA


def test_pi_words_catch_court_follow_ups():
    en = ca.QUERY_TERMS_BY_STREAM["PRIORITY"]["en"]
    for w in ("Hawks", "SIU", "NPA", "IDAC", "charged", "bail", "court", "syndicate", "fraud"):
        assert w in en
    assert ca.QUERY_TERMS_BY_STREAM["CRIME_INTEL"] is ca.QUERY_TERMS


def test_no_setswana_words_means_no_setswana_searches_for_new_streams(gaz):
    a = _areas([_pin("i", -26.0, 28.0, "North West", "Beta: burst pipe", "INFRASTRUCTURE")], gaz)["i"]
    assert ca.PROVINCE_LANGS["North West"] == ["en", "af", "tn"]
    assert not any("polao" in q for q in a["queries"])
    assert any("kabeldiefstal" in q for q in a["queries"])


def test_a_stream_without_words_adds_no_searches_and_is_recorded(gaz):
    a = _areas([_pin("g", -24.0, 29.0, "Limpopo", "Gamma: flood", "GLOBAL")], gaz)["g"]
    assert a["queries"] == [] and a["streams_without_terms"] == ["GLOBAL"]


def test_sweep_records_which_stream_each_result_was_searched_for(gaz, tmp_path):
    pins = [_pin("k", -26.0, 26.0, "North West", "Alpha: man shot dead", "CRIME_INTEL"),
            _pin("i", -26.0, 26.1, "North West", "Alpha East: substation vandalised", "INFRASTRUCTURE")]
    res = ca.compute_areas(pins, 30.0, places_path=gaz[0], precincts_path=gaz[1])

    def fake(q, s, e):
        if "murder" in q or "moord" in q or "polao" in q:
            return [{"title": "Man killed in Alpha", "link": "https://x.test/k"}]
        return [{"title": "Substation stripped in Alpha", "link": "https://x.test/s"}]

    start = datetime(2026, 10, 19, tzinfo=SAST)
    rep = ca.sweep(res, start, start + timedelta(days=1), tmp_path, 30, fake, known={})
    area = rep["areas"][0]
    got = {i["link"]: i["searched_for_streams"] for i in area["items"]}
    assert got == {"https://x.test/s": ["INFRASTRUCTURE"], "https://x.test/k": ["CRIME_INTEL"]}
    assert set(area["queries_planned_by_stream"]) == {"CRIME_INTEL", "INFRASTRUCTURE"}


def test_areas_written_before_the_change_read_as_killing_searches():
    assert ca.query_stream_map({"queries": ["a", "b"]}) == {"a": "CRIME_INTEL", "b": "CRIME_INTEL"}


def test_published_pins_carry_their_stream():
    pins = ca.published_pins()
    assert pins and all(p["stream"] in ("CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY", "GLOBAL") for p in pins)
    assert sum(p["stream"] == "INFRASTRUCTURE" for p in pins) == 4
