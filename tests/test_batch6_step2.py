"""Offline tests for Batch 6 step 2: court and arrest steps on older cases
(court_step.py, G8), the fixed start date for runs (run_window.py, G12) and
the Priority Intelligence rules on article text with comments left out
(pi_signals.py, G2/G11).

No network: pages are the synthetic fixtures in tests/fixtures/article_reader
and the hand-checked dates in data/bench/article_fixtures.json.
"""
import json
import os
import socket
from datetime import date, datetime, timezone

import pytest

from forage.collectors import article_reader as ar
from forage.collectors import court_step as cs
from forage.collectors import pi_signals as pi
from forage.collectors import run_window as rw

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "article_reader")
REPO = os.path.abspath(os.path.join(HERE, ".."))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*a, **k):
        raise AssertionError("network use in an offline test")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


def _page(name, url="https://example.test/story"):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return ar.parse_article(fh.read(), url=url)


def _bench_facts(case):
    with open(os.path.join(REPO, "data", "bench", "article_fixtures.json"), encoding="utf-8") as fh:
        return json.load(fh)["cases"][case]["manual_facts"]


# ── Court and arrest steps (G8) ──────────────────────────────────────────────

def test_airport_officers_arrest_is_a_step_on_2025_conduct():
    a = cs.assess_reading("Five police officers arrested at the Polokwane International Airport - SABC News",
                          "", None, fixture=_bench_facts("plk_airport_officers"))
    assert a["court_step"] and a["older_conduct"] and a["new_event"] is False
    assert a["conduct_date"] == "2025-04-17"
    assert a["step_date"] == "2026-10-19"


def test_airport_officers_summary_alone_shows_older_conduct():
    a = cs.assess("5 cops arrested at Polokwane airport",
                  "Five police officers were arrested after a five month investigation into alleged corruption.",
                  step_date="2026-10-19")
    assert a["court_step"] and a["older_conduct"]
    assert "five month investigation" in a["step_basis"]


def test_headline_only_copy_takes_the_flag_from_its_story():
    rows = [
        cs.assess("Five police officers arrested at the Polokwane International Airport - SABC News", "",
                  step_date="2026-10-19"),
        cs.assess("5 cops arrested at Polokwane airport",
                  "arrested after a five month investigation", step_date="2026-10-19"),
        cs.assess("Polokwane airport upgrades runway lights", "", step_date="2026-10-19"),
    ]
    assert rows[0]["court_step"] is False
    shared = cs.story_flags(rows)
    assert shared[0]["court_step"] and shared[0]["new_event"] is False
    assert "shared from another record" in shared[0]["step_basis"]
    assert shared[2]["court_step"] is False          # not an arrest, left alone
    assert rows[0]["court_step"] is False            # inputs unchanged


def test_follow_up_arrest_is_a_step():
    a = cs.assess("2 more Polokwane airport cops still being sought after arrests - The Citizen", "",
                  step_date="2026-10-21")
    assert a["court_step"] and a["new_event"] is False


def test_oosthuizen_like_court_story_reads_conduct_from_article():
    r = _page("older_court_story.html")
    a = cs.assess_reading("Murder case postponed to March 2027 - Tabloid Newspapers", "", r)
    assert a["court_step"] and a["step_kind"] == "court" and a["older_conduct"]
    assert a["conduct_date"] == "2025-12"            # month precision kept
    assert a["step_date"] == "2026-10-06"
    assert "2027" not in a["conduct_date"]            # next court date is never conduct


def test_court_headline_is_a_step_even_without_dates():
    a = cs.assess("Henrico Oosthuizen murder case postponed to March 2027 - Tabloid Newspapers", "",
                  step_date="2026-10-06")
    assert a["court_step"] and a["new_event"] is False
    assert a["older_conduct"] is False and a["conduct_date"] is None


def test_older_arrest_story_reads_conduct_date_from_article_dates():
    r = _page("older_arrest_story.html")
    a = cs.assess_reading("Officers arrested over missing detainees", "", r)
    assert a["court_step"] and a["step_kind"] == "arrest"
    assert a["conduct_date"] == "2025-04-17"


def test_mlambo_trial_on_2023_case():
    a = cs.assess_reading("Mlambo trial ends as judge seeks clarity from police - The Citizen", "", None,
                          fixture=_bench_facts("nc_mlambo_court"))
    assert a["court_step"] and a["older_conduct"] and a["conduct_date"] == "2023"


def test_88_firearms_investigation_on_may_to_july_conduct():
    a = cs.assess_reading("Police probe firearm syndicate after 88 guns disappear - The Citizen", "", None,
                          fixture=_bench_facts("plk_88_firearms"))
    assert a["court_step"] and a["older_conduct"]
    assert a["conduct_date"] == "2026-05/2026-07"


def test_88_firearms_without_checked_dates_is_not_flagged():
    a = cs.assess("Police probe firearm syndicate after 88 guns disappear - The Citizen", "",
                  step_date="2026-10-01")
    assert a["court_step"] is False and a["new_event"] is True
    assert "investigation is treated as the event" in a["step_basis"]


def test_quad_bike_arrest_in_the_act_is_the_event():
    r = _page("syndicate_story.html")
    a = cs.assess_reading("Suspected quad bike thieves nabbed in Schuinshoogte - The Citizen", "", r,
                          fixture=_bench_facts("nc_p2_quad_bike"))
    assert a["court_step"] is False and a["new_event"] is True
    assert cs.metadata(a) == {}


def test_carrim_court_step_adds_no_place_or_stream():
    a = cs.assess_reading("Sulliman Carrim in Musina court over attempted theft of Absa vehicles", "", None,
                          fixture=_bench_facts("musina_carrim"))
    assert a["court_step"] and a["older_conduct"] is False
    meta = cs.metadata(a)
    assert meta["court_step"] is True and meta["new_event"] is False
    assert not any(k in meta for k in ("location_candidate", "stream", "place", "court_place"))


def test_recent_conduct_date_is_not_older():
    a = cs.assess("Two arrested for robbery", "", step_date="2026-10-20", known_conduct_date="2026-10-12")
    assert a["court_step"] is False and a["older_conduct"] is False


def test_conduct_before_a_fixed_run_start_counts_as_older():
    a = cs.assess("Two arrested for robbery", "", step_date="2026-10-20", known_conduct_date="2026-09-28",
                  window_start="2026-10-01")
    assert a["court_step"] and a["older_conduct"]


def test_year_words_and_noise():
    assert cs.assess("Suspect arrested for 2024 murder of shop owner", "", step_date="2026-10-20")["court_step"]
    assert cs.assess("Man arrested over killing that happened last year", "", step_date="2026-10-20")["conduct_date"] == "2025"
    # An Act's year is not conduct, and a tennis court is not a court.
    assert cs.assess("Three arrested under the Firearms Control Act of 2000", "",
                     step_date="2026-10-20")["court_step"] is False
    assert cs.assess("Tennis court lights stolen at school", "", step_date="2026-10-20")["court_step"] is False


def test_plain_event_is_not_a_step():
    a = cs.assess("Musina businessman shot more than 20 times in store", "", step_date="2026-10-16")
    assert a["court_step"] is False and a["step_kind"] is None and cs.metadata(a) == {}


def test_a_step_is_never_a_new_event():
    heads = ["Accused granted bail", "Case postponed", "Man sentenced to life", "Suspects appear in court",
             "Two arrested for 2023 robbery", "Further arrests in tender case"]
    for h in heads:
        a = cs.assess(h, "", step_date="2026-10-20")
        assert a["court_step"] and a["new_event"] is False, h


def test_blocked_reading_contributes_no_article_evidence():
    blocked = {"status": "blocked", "body_text": "killed in December 2025",
               "date_mentions": [{"date": "2025-12", "kind": "past", "phrase": "in December 2025"}],
               "feed_date": "2026-10-06"}
    a = cs.assess_reading("Accused arrested", "", blocked)
    assert a["court_step"] is False and a["step_date"] == "2026-10-06"


# ── Fixed start date (G12) ───────────────────────────────────────────────────

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)      # 8 PM in South Africa


@pytest.mark.parametrize("text", ["1 October", "from 1 October", "2026-10-01", "October 1", "1 Oct 2026",
                                  "1st October", "since 1 oct"])
def test_parse_start_forms(text):
    assert rw.parse_start(text, now=NOW) == date(2026, 10, 1)


def test_parse_start_without_year_takes_the_last_one_passed():
    assert rw.parse_start("1 December", now=NOW) == date(2025, 12, 1)


@pytest.mark.parametrize("bad", ["2026-11-30", "31 September 2026", "next week", ""])
def test_parse_start_refuses(bad):
    with pytest.raises(ValueError):
        rw.parse_start(bad, now=NOW)


def test_88_firearms_falls_inside_a_1_october_window_but_not_22_days():
    ts = "2026-10-01T07:00:00Z"      # the Google News date of the 88 firearms story
    assert rw.RunWindow.build(days=22, now=NOW, use_env=False).contains(ts) is False
    w = rw.RunWindow.build(days=22, start="1 October", now=NOW, use_env=False)
    assert w.mode == "fixed_start" and w.contains(ts)
    assert w.cutoff() == datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)   # midnight SAST
    assert not w.contains("2026-09-30T21:59:00Z")
    assert w.contains("2026-09-30T22:00:00Z")
    assert not w.contains(None)
    assert w.query_days() == 24


def test_window_from_environment(monkeypatch):
    monkeypatch.setenv(rw.ENV_START, "2026-10-01")
    assert rw.RunWindow.build(days=7, now=NOW).start == date(2026, 10, 1)
    assert rw.RunWindow.build(days=7, now=NOW, use_env=False).start is None


def test_no_window_keeps_everything():
    w = rw.RunWindow.build(now=NOW, use_env=False)
    assert w.mode == "none" and w.contains("1999-01-01T00:00:00Z") and w.cutoff() is None


def test_gnews_rss_parse_uses_a_fixed_cutoff():
    from forage.collectors import google_news_collector as gn
    xml = (b'<?xml version="1.0"?><rss><channel><item><title>Police probe firearm syndicate after 88 guns '
           b'disappear - The Citizen</title><link>https://news.google.com/rss/articles/x</link>'
           b'<pubDate>Thu, 01 Oct 2026 07:00:00 GMT</pubDate><source url="https://www.citizen.co.za">'
           b'The Citizen</source></item></channel></rss>')
    kept = gn._parse_rss_items(xml, "q", 22, cutoff=datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc))
    assert len(kept) == 1
    dropped = gn._parse_rss_items(xml, "q", 22, cutoff=datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc))
    assert dropped == []


def test_civic_window_start_replaces_days(monkeypatch):
    from forage.collectors import civic_intel_collector as civic
    monkeypatch.setattr(civic, "WINDOW_DAYS", 1)
    monkeypatch.setattr(civic, "WINDOW_START", None)
    assert civic.run_window().mode == "count_back"
    monkeypatch.setattr(civic, "WINDOW_START", "2026-10-01")
    w = civic.run_window()
    assert w.mode == "fixed_start" and w.start == date(2026, 10, 1)


# ── PI rules on article text, comments left out (G2, G11) ────────────────────

QUAD = "Suspected quad bike thieves nabbed in Schuinshoogte - The Citizen"
P1 = "Over R1 Million in Suspected Counterfeit Goods Seized in Newcastle CBD - Newcastillian News"


def test_article_text_finds_a_category_the_headline_lacks():
    assert pi.classify(QUAD, "")["is_pi"] is False
    c = pi.classify(QUAD, "", article_text=_page("syndicate_story.html"))
    assert c["is_pi"] and c["category"] == "organised_network" and c["role"] == "signal"
    assert c["text_basis"] == "article" and c["matched_in_article"] == ["organised_network"]


def test_reader_comment_does_not_make_counterfeit_story_corruption():
    r = _page("counterfeit_comment_story.html")
    assert "fraudulent" not in r.body_text and "corrupt" not in r.body_text
    c = pi.classify(P1, "", article_text=r)
    assert c["is_pi"] is False and c["text_basis"] == "article"
    # The comment's words would have done it, which is why they stay out.
    leaked = r.body_text + " This fraudulent stuff is everywhere and the corrupt officials let it in."
    assert pi.classify(P1, "", article_text=leaked)["category"] == "corruption_capture"


def test_comment_guard_on_plain_text():
    text = ("Police seized goods from shops with a crime intelligence team.\n\n2 Comments\n"
            "This fraudulent stuff and corrupt officials.")
    assert "fraudulent" not in pi.article_text_for_pi(text)
    assert pi.classify(P1, "", article_text=text)["is_pi"] is False
    assert "corrupt" not in pi.article_text_for_pi("Story text. Leave a Reply corrupt officials")


def test_unread_article_falls_back_to_headline_and_summary():
    for status in ("blocked", "feed_date_only", "not_shortlisted", "error"):
        reading = {"status": status, "body_text": "a drug syndicate was involved"}
        c = pi.classify(QUAD, "", article_text=reading)
        assert c["is_pi"] is False and c["text_basis"] == "headline_summary"


def test_without_article_text_results_are_unchanged():
    cases = [("Hawks arrest three for tender fraud in Polokwane", ""),
             ("Councillor shot dead in Umlazi", ""),
             ("Minister says corruption must end", ""),
             ("Drug syndicate bust in Gold Coast", "")]
    for t, s in cases:
        a = pi.classify(t, s)
        b = pi.classify(t, s, article_text=None)
        assert a == b and a["text_basis"] == "headline_summary"
    stream, prio, meta = pi.route("CRIME_INTEL", "Hawks arrest three for tender fraud in Polokwane")
    assert "pi_text_basis" not in meta


def test_article_never_overrides_a_foreign_headline():
    body = "A syndicate was arrested by the Hawks in Johannesburg with R2 million in cash."
    c = pi.classify("Drug syndicate bust on Australia's Gold Coast", "", article_text=body)
    assert c["is_pi"] is False and c["reason"] == "not about South Africa"


def test_article_can_place_an_unplaced_headline_in_south_africa():
    body = "Members of the syndicate were arrested in Soweto with goods worth R2 million."
    assert pi.classify("Syndicate members arrested", "")["is_pi"] is False
    assert pi.classify("Syndicate members arrested", "", article_text=body)["is_pi"] is True


def test_route_marks_article_basis_and_keeps_killing_overlap_on_headline():
    body = ("Two suspected syndicate members were arrested in Soweto. In an unrelated case last year, "
            "a guard was shot dead nearby.")
    stream, prio, meta = pi.route("CRIME_INTEL", QUAD, "", article_text=body)
    assert meta["pi_text_basis"] == "article" and meta["pi_matched_in_article"] == ["organised_network"]
    assert meta["pi_route"] == "standalone" and stream == pi.STREAM


def test_kind_can_come_from_the_article_lead():
    body = "Hawks officers arrested the director of a construction firm in Polokwane on Tuesday."
    c = pi.classify("Tender fraud scandal deepens at municipality", "", article_text=body)
    assert c["is_pi"] and c["kind"] == "arrest"
