"""Batch 6 step 3: article reader and court-step check wired into both
collectors, the story-level court-step flag, and the bench hooks. Offline."""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from forage.collectors import article_step as art  # noqa: E402
from forage.collectors import civic_intel_collector as civic  # noqa: E402
from forage.collectors import google_news_collector as gnews  # noqa: E402
from forage.collectors import run_window as rw  # noqa: E402


class FakeReader:
    def __init__(self, reading):
        self.reading = reading
        self.calls = []

    def read_signal(self, url, feed_date=None, shortlisted=False):
        self.calls.append((url, feed_date, shortlisted))
        return dict(self.reading, signal_url=url)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(art.ENV_READER, raising=False)
    monkeypatch.delenv(art.ENV_COURT_STEP, raising=False)
    prev = (art.READ_ARTICLES, art._READER)
    art.READ_ARTICLES = None
    art._READER = None
    yield
    art.READ_ARTICLES, art._READER = prev


def _item(title, query="Musina police", desc="", link="https://example.org/a"):
    return {"title": title, "description": desc, "query": query,
            "pubdate": "Mon, 19 Oct 2026 08:00:00 GMT",
            "pubdate_parsed": "2026-10-19T08:00:00Z", "link": link}


def _meta(sig):
    return json.loads(sig["metadata_json"] or "{}")


PLACED = {"location_candidate": {"place": "Musina", "province": "Limpopo"}}


# ── switches ────────────────────────────────────────────────────────────────────────

def test_reader_off_by_default_and_flag_or_env_turns_it_on(monkeypatch):
    assert art.reader_enabled() is False
    monkeypatch.setenv(art.ENV_READER, "1")
    assert art.reader_enabled() is True
    art.READ_ARTICLES = False
    assert art.reader_enabled() is False
    monkeypatch.delenv(art.ENV_READER)
    art.READ_ARTICLES = True
    assert art.reader_enabled() is True


def test_reader_off_means_no_read_attempt():
    fake = FakeReader({"status": "read"})
    art.set_reader(fake)
    assert art.read("https://example.org/a", "2026-10-19", "CRIME_INTEL", PLACED) is None
    assert fake.calls == []


def test_only_shortlisted_signals_are_read():
    art.READ_ARTICLES = True
    fake = FakeReader({"status": "read", "body_text": "x"})
    art.set_reader(fake)
    assert art.read("https://example.org/a", "2026-10-19", "CRIME_INTEL", {}) is None
    assert art.read("https://example.org/a", "2026-10-19", "CRIME_INTEL",
                    {**PLACED, "place_review": "foreign place"}) is None
    assert fake.calls == []
    assert art.read("https://example.org/a", "2026-10-19", "CRIME_INTEL", PLACED)["status"] == "read"
    assert fake.calls == [("https://example.org/a", "2026-10-19", True)]


def test_reader_error_never_blocks_a_signal():
    class Boom:
        def read_signal(self, *a, **k):
            raise RuntimeError("down")
    art.READ_ARTICLES = True
    art.set_reader(Boom())
    r = art.read("https://example.org/a", "2026-10-19", "CRIME_INTEL", PLACED)
    assert r["status"] == "error"
    assert art.article_metadata(r)["date_basis"].startswith("feed date")


# ── court-step check in both collectors ─────────────────────────────────────────

def test_gnews_court_headline_is_flagged_as_a_step_without_reader():
    sig = gnews.build_signal(_item("Henrico Oosthuizen murder case postponed to March 2027 - Tabloid Media",
                                   "Newcastle murder"), is_crime=True)
    m = _meta(sig)
    assert m["court_step"] is True and m["step_kind"] == "court" and m["new_event"] is False
    assert "article_status" not in m
    assert sig["timestamp"].startswith("2026-10-19")


def test_gnews_new_killing_is_not_a_step():
    sig = gnews.build_signal(_item("Ward councillor shot dead outside his home in Inanda - IOL", "Inanda"),
                             is_crime=False)
    m = _meta(sig)
    assert not m.get("court_step")
    assert sig["stream"] == "CRIME_INTEL"


def test_court_step_off_switch(monkeypatch):
    monkeypatch.setenv(art.ENV_COURT_STEP, "0")
    sig = gnews.build_signal(_item("Murder accused back in court in Musina"), is_crime=True)
    assert "court_step" not in _meta(sig)


def test_civic_court_headline_is_flagged():
    src = {"source_key": "t", "stream": "GLOBAL", "base_relevance": 1.0, "is_priority_default": 0,
           "fetch_mode": "rss_direct"}
    sig = civic.entry_to_signal({"title": "Murder accused granted bail in Musina Magistrate's Court",
                                 "summary": "", "link": "https://example.org/c"}, src)
    assert sig["infra_meta"]["court_step"] is True
    assert sig["infra_meta"]["step_kind"] == "court"


def test_hand_checked_conduct_date_makes_an_arrest_an_older_case_step():
    t = "Police probe firearm syndicate after 88 guns disappear in Polokwane"
    plain = _meta(gnews.build_signal(_item(t, "Polokwane police"), is_crime=True))
    assert not plain.get("court_step")
    fx = _meta(gnews.build_signal(_item(t, "Polokwane police"), is_crime=True,
                                  fixture={"conduct_date": "2026-05/2026-07"}))
    assert fx["court_step"] is True and fx["older_conduct"] is True


# ── article text and dates ──────────────────────────────────────────────────────

READ = {"status": "read", "article_url": "https://example.org/a", "published": "2026-10-19",
        "incident_date": "2026-10-16", "incident_basis": "'on Friday 16 October'",
        "body_text": "A shop owner was shot dead in his store in Matswale on Friday 16 October."}


def test_read_article_adds_dates_but_keeps_feed_timestamp():
    sig = gnews.build_signal(_item("Musina businessman shot in store"), is_crime=True, reading=READ)
    m = _meta(sig)
    assert m["article_status"] == "read"
    assert m["article_published"] == "2026-10-19" and m["incident_date"] == "2026-10-16"
    assert sig["timestamp"].startswith("2026-10-19")


def test_blocked_article_keeps_feed_date_and_says_why():
    r = {"status": "blocked", "reason": "permission_required: Caxton clause 2.7.1"}
    m = _meta(gnews.build_signal(_item("Two arrested with stolen quad bike in Newcastle"), is_crime=True,
                                 reading=r))
    assert m["article_status"] == "blocked"
    assert "Caxton" in m["article_reason"] and m["date_basis"].startswith("feed date")


def test_article_text_reaches_pi_rules():
    """The quad bike headline alone is Crime & Security; the (test) article's
    syndicate makes it Priority Intelligence once the collector reads it."""
    sys.path.insert(0, str(REPO / "tests"))
    from test_batch6_step2 import _page
    it = _item("Suspected quad bike thieves nabbed in Schuinshoogte - The Citizen", "Newcastle police")
    plain = gnews.build_signal(it, is_crime=True)
    assert plain["stream"] == "CRIME_INTEL" and "pi_category" not in _meta(plain)
    read = gnews.build_signal(it, is_crime=True, reading=_page("syndicate_story.html").to_dict())
    m = _meta(read)
    assert read["stream"] == "PRIORITY" and m["pi_category"] == "organised_network"
    assert m["pi_text_basis"] == "article" and m["article_status"] == "read"
    assert not m.get("court_step")  # arrested in the act: a new event


def test_before_window_drops_old_incident_but_not_a_court_step():
    w = rw.RunWindow.build(days=7, start="2026-10-01")
    assert w.start == date(2026, 10, 1)
    assert art.before_window({"incident_date": "2026-09-15"}, w) is True
    assert art.before_window({"incident_date": "2026-09-15", "court_step": True}, w) is False
    assert art.before_window({"incident_date": "2026-10-02"}, w) is False
    assert art.before_window({}, w) is False
    assert art.before_window({"incident_date": "2026-09-15"}, None) is False


# ── one story, one flag (database) ─────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (signal_id TEXT, external_id TEXT, title TEXT, timestamp TEXT, "
                 "stream TEXT, metadata_json TEXT, source_type TEXT)")
    return conn


def _save(conn, sid, ts, meta):
    conn.execute("INSERT INTO signals VALUES (?,?,?,?,?,?, 'live')",
                 (sid, sid, sid, ts, "CRIME_INTEL", json.dumps(meta)))


OLDER = {"story_key": "k1", "court_step": True, "step_kind": "arrest", "older_conduct": True,
         "new_event": False, "step_basis": "arrest step; signs: 'five-month investigation'"}


def test_headline_only_copy_gets_the_flag_from_its_story():
    conn = _db()
    _save(conn, "a", "2026-10-19T08:00:00", OLDER)
    meta = art.share_story_step(conn, "b", "2026-10-20T08:00:00", {"story_key": "k1", "step_kind": "arrest"})
    assert meta["court_step"] is True and meta["court_step_shared"] is True


def test_saved_copies_get_the_flag_when_a_later_record_shows_older_conduct():
    conn = _db()
    _save(conn, "a", "2026-10-19T08:00:00", {"story_key": "k1", "step_kind": "arrest"})
    _save(conn, "c", "2026-10-19T09:00:00", {"story_key": "k1"})
    meta = art.share_story_step(conn, "b", "2026-10-20T08:00:00", dict(OLDER))
    assert meta["court_step"] is True
    a = json.loads(conn.execute("SELECT metadata_json FROM signals WHERE signal_id='a'").fetchone()[0])
    c = json.loads(conn.execute("SELECT metadata_json FROM signals WHERE signal_id='c'").fetchone()[0])
    assert a["court_step"] is True and a["court_step_shared"] is True
    assert not c.get("court_step")  # no step word: a story's killing report is never re-labelled


def test_other_stories_and_off_switches_are_left_alone(monkeypatch):
    conn = _db()
    _save(conn, "a", "2026-10-19T08:00:00", {**OLDER, "story_key": "k2"})
    meta = art.share_story_step(conn, "b", "2026-10-20T08:00:00", {"story_key": "k1", "step_kind": "arrest"})
    assert not meta.get("court_step")
    _save(conn, "x", "2026-10-19T08:00:00", OLDER)
    monkeypatch.setenv("FORGE_STORY_MERGE", "0")
    meta = art.share_story_step(conn, "b", "2026-10-20T08:00:00", {"story_key": "k1", "step_kind": "arrest"})
    assert not meta.get("court_step")


def test_share_never_raises():
    class Bad:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no table")
    meta = art.share_story_step(Bad(), "b", "2026-10-20T08:00:00", {"story_key": "k1"})
    assert "court_step_error" in meta


# ── offline fixture reader keeps the outlet rules ─────────────────────────────────

def test_fixture_reader_blocks_caxton_and_headline_only_outlets():
    fr = art.FixtureReader()
    r = fr.read_signal("https://www.citizen.co.za/review-online/news/some-new-story/", "2026-10-19", True)
    assert r["status"] == "blocked"
    r = fr.read_signal("https://www.zoutnet.co.za/articles/news/99999/2026-10-08/some-story", "2026-10-08", True)
    assert r["status"] == "blocked"
    r = fr.read_signal("https://example.org/new", "2026-10-19", True)
    assert r["status"] == "not_in_fixture"
    assert fr.read_signal("https://example.org/new", "2026-10-19", False)["status"] == "not_shortlisted"


def test_fixture_reader_respects_a_withdrawn_permission():
    fr = art.FixtureReader(permissions={})
    assert all(fr.read_signal(u, None, True)["status"] != "read"
               for u, ln in fr.links.items() if "citizen.co.za" in u or "zoutnet" in u)


# ── bench ────────────────────────────────────────────────────────────────────────

def test_bench_story_share_flags_whole_story():
    import collector_bench as cb
    rows = [
        {"town": "p", "outcome": {"story_key": "s", "step_flag": True, "court_step": True,
                                  "step_kind": "arrest", "older_conduct": True, "step_basis": "x"}},
        {"town": "p", "outcome": {"story_key": "s", "step_flag": False, "court_step": False,
                                  "step_kind": "arrest"}},
        {"town": "p", "outcome": {"story_key": "s", "step_flag": False, "court_step": False}},
        {"town": "q", "outcome": {"story_key": "s", "step_flag": False, "court_step": False,
                                  "step_kind": "arrest"}},
    ]
    out = cb.share_story_steps(rows)
    assert out[1]["outcome"]["court_step"] is True
    assert out[2]["outcome"]["court_step"] is False
    assert out[3]["outcome"]["court_step"] is False  # another town's story


def test_bench_holds_or_improves_on_batch5():
    import collector_bench as cb
    card = cb.score(merge=cb.story_merge_rows)
    s = card["summary"]
    assert s["catch_pass"] >= 9 and s["keep_out_pass"] >= 7
    by = {r["id"]: r for r in card["cases"]}
    assert by["nc_oosthuizen_court"]["pass"] and by["nc_mlambo_court"]["pass"]
    assert by["plk_airport_officers"]["court_step_flagged"] == "all"
    assert card["articles"] == "offline fixtures" and card["hand_checked_dates"] is False
