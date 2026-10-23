"""Offline tests for the Batch 6 article reader (forage/collectors/article_reader.py).

Nothing here touches the network: pages come from saved HTML fixtures and
every fetch goes through a fake session.
"""
import base64
import json
import os
import socket
from datetime import date, datetime, timedelta, timezone

import pytest

from forage.collectors import article_reader as ar

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "article_reader")
REPO = os.path.abspath(os.path.join(HERE, ".."))


def _html(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*a, **k):
        raise AssertionError("network use in an offline test")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


class FakeResp:
    def __init__(self, url, status=200, text="", ctype="text/html; charset=utf-8"):
        self.url = url
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": ctype}


class FakeSession:
    """Maps URL -> FakeResp (or callable). Records every request."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True, stream=False):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        r = self.routes.get(url)
        if r is None:
            return FakeResp(url, 404, "")
        return r(url) if callable(r) else r


def _fetcher(session, **kw):
    kw.setdefault("min_interval_s", 0)
    kw.setdefault("permissions", {})
    kw.setdefault("sleep", lambda s: None)
    return ar.PoliteFetcher(session=session, **kw)


ROBOTS_OPEN = "User-agent: *\nAllow: /\n"


# -- parsing: published date --------------------------------------------------

def test_published_from_json_ld_graph():
    r = ar.parse_article(_html("wp_jsonld_comments.html"), "https://example.co.za/a")
    assert r.published.startswith("2026-10-21T12:36:34")
    assert r.published_source == "json-ld datePublished"
    assert r.title == "Goods worth over R1 million seized in town centre"


def test_published_from_meta_tag():
    r = ar.parse_article(_html("meta_date_incident.html"))
    assert r.published.startswith("2026-10-08")
    assert r.published_source == "meta article:published_time"


def test_published_from_parsely_and_time_element():
    assert ar.parse_article(_html("court_story.html")).published_source == "meta parsely-pub-date"
    r = ar.parse_article(_html("weekday_story.html"))
    assert r.published.startswith("2026-10-17")
    assert r.published_source == "time element"


def test_no_published_date_falls_back_to_feed_date():
    r = ar.parse_article(_html("no_date_page.html"), feed_date="2026-10-10")
    assert r.published is None
    assert r.incident_date is None
    assert r.effective_date == "2026-10-10"


# -- parsing: body text --------------------------------------------------------

def test_body_drops_comments_related_nav_and_sidebar():
    r = ar.parse_article(_html("wp_jsonld_comments.html"))
    body = r.body_text
    assert "counterfeit and illicit goods" in body
    assert "did not say whether anyone had been arrested" in body
    # reader comments (the G11 problem: 'fraudulent' in a comment)
    assert "fraudulent" not in body.lower()
    assert "paid off" not in body
    # related stories, sidebar, nav, share and newsletter blocks
    assert "tavern" not in body
    assert "water outage" not in body
    assert "Taxi violence" not in body
    assert "Home" not in body.split()
    assert "WhatsApp" not in body
    assert "newsletter" not in body.lower()
    assert "Copyright" not in body


def test_body_drops_more_stories_and_comment_blocks():
    assert "Truck overturns" not in ar.parse_article(_html("meta_date_incident.html")).body_text
    assert "Well done" not in ar.parse_article(_html("syndicate_story.html")).body_text
    assert "inquiry case" not in ar.parse_article(_html("court_story.html")).body_text


def test_byline_date_is_not_read_as_the_incident():
    r = ar.parse_article(_html("weekday_story.html"))
    assert "Staff Reporter" not in r.body_text
    assert r.incident_date == "2026-10-16"


# -- incident dates -------------------------------------------------------------

def test_incident_date_a_month_before_the_feed_date():
    # The NRCS pattern: published 8 Oct, incident 15 Sep.
    r = ar.parse_article(_html("meta_date_incident.html"), feed_date="2026-10-08")
    assert r.incident_date == "2026-09-15"
    assert "15 September" in r.incident_basis
    assert r.effective_date == "2026-09-15"


def test_weekday_and_date_resolved_against_published():
    r = ar.parse_article(_html("weekday_story.html"))
    m = [x for x in r.date_mentions if x["date"] == "2026-10-16"][0]
    assert m["weekday_ok"] is True
    assert m["kind"] == "past"


def test_syndicate_arrest_date():
    r = ar.parse_article(_html("syndicate_story.html"))
    assert r.incident_date == "2026-09-30"


def test_court_story_separates_past_event_from_next_court_date():
    r = ar.parse_article(_html("court_story.html"))
    assert r.incident_date == "2026-10-21"
    future = [m for m in r.date_mentions if m["kind"] == "future"]
    assert [m["date"] for m in future] == ["2026-11-30"]


@pytest.mark.parametrize("text,published,expected", [
    ("He was shot on Friday 16 October.", date(2026, 10, 17), "2026-10-16"),
    ("He was shot on October 16.", date(2026, 10, 17), "2026-10-16"),
    ("The body was found on Friday.", date(2026, 10, 19), "2026-10-16"),
    ("The body was found last Monday.", date(2026, 10, 19), "2026-10-12"),
    ("The man was stabbed yesterday.", date(2026, 10, 19), "2026-10-18"),
    ("The house was robbed on 28 December.", date(2026, 1, 5), "2025-12-28"),
    ("Goods were seized on 3rd of September 2026.", date(2026, 10, 1), "2026-09-03"),
])
def test_incident_date_patterns(text, published, expected):
    mentions = ar.extract_date_mentions(text, published)
    assert ar.pick_incident_date(mentions, published)[0] == expected


def test_wrong_weekday_is_not_used():
    # 16 October 2026 is a Friday, not a Tuesday.
    mentions = ar.extract_date_mentions("He was shot on Tuesday 16 October 2026.", date(2026, 10, 20))
    assert mentions[0].weekday_ok is False
    assert ar.pick_incident_date(mentions, date(2026, 10, 20))[0] is None


def test_postponed_date_is_future_not_incident():
    text = "The accused was arrested on 2 October. The case was postponed to 3 March."
    mentions = ar.extract_date_mentions(text, date(2026, 10, 6))
    kinds = {m.date: m.kind for m in mentions}
    assert kinds == {"2026-10-02": "past", "2027-03-03": "future"}
    assert ar.pick_incident_date(mentions, date(2026, 10, 6))[0] == "2026-10-02"


def test_month_only_mentions_are_kept_for_later_steps():
    text = "He allegedly posed as an inspector between May and July and was arrested in September."
    mentions = ar.extract_date_mentions(text, date(2026, 10, 1))
    assert {"2026-05", "2026-09"} <= {m.date for m in mentions}
    assert all(m.precision == "month" for m in mentions)


def test_no_date_in_text():
    mentions = ar.extract_date_mentions("Residents met to discuss the water supply.", date(2026, 10, 1))
    assert ar.pick_incident_date(mentions, date(2026, 10, 1)) == (None, "no dated incident in the text")


# -- outlet policy (Batch 7 findings) ----------------------------------------------

@pytest.mark.parametrize("url,policy", [
    ("https://www.citizen.co.za/review-online/news-headlines/local-news/2026/10/01/x/", ar.POLICY_NEEDS_PERMISSION),
    ("https://www.citizen.co.za/northern-natal-news/news-headlines/2026/10/05/x/", ar.POLICY_NEEDS_PERMISSION),
    ("https://newcastillian.com/2026/10/21/x/", ar.POLICY_NEEDS_PERMISSION),
    ("https://www.limpopomirror.co.za/article/x", ar.POLICY_LEAD_ONLY),
    ("https://www.zoutpansberger.co.za/articles/x", ar.POLICY_LEAD_ONLY),
    ("https://www.zoutnet.co.za/articles/news/1/x", ar.POLICY_LEAD_ONLY),
    ("https://tabloidmedia.co.za/some-story/", ar.POLICY_OPEN),
])
def test_outlet_policy(url, policy):
    assert ar.outlet_policy(url, permissions={})[0] == policy


def test_restricted_outlets_are_never_requested():
    s = FakeSession()
    f = _fetcher(s)
    for url in ("https://www.citizen.co.za/review-online/a/", "https://newcastillian.com/a/",
                "https://www.limpopomirror.co.za/article/a"):
        assert f.fetch(url).status == "blocked_policy"
    assert s.calls == []  # not even robots.txt


def test_written_permission_opens_an_outlet():
    perms = {"citizen.co.za": {"granted_by": "Caxton (editor)", "evidence": "email 2026-11-01"}}
    assert ar.outlet_policy("https://www.citizen.co.za/review-online/a/", perms)[0] == ar.POLICY_OPEN
    # lead-only outlets stay lead-only even with an entry
    perms["limpopomirror.co.za"] = {"granted_by": "x", "evidence": "y"}
    assert ar.outlet_policy("https://www.limpopomirror.co.za/article/a", perms)[0] == ar.POLICY_LEAD_ONLY


def test_permission_file_ignores_entries_without_evidence(tmp_path):
    p = tmp_path / "perm.json"
    p.write_text(json.dumps({"permissions": {
        "citizen.co.za": {"granted_by": "someone"},
        "newcastillian.com": {"granted_by": "Pixelfish", "evidence": "signed letter"}}}))
    assert set(ar.load_permissions(str(p))) == {"newcastillian.com"}


def test_committed_permission_file_grants_nothing():
    assert ar.load_permissions() == {}


def test_no_database_key_or_paid_service_in_the_reader():
    src = open(ar.__file__, encoding="utf-8").read().lower()
    for word in ("supabase.co", "apikey", "api_key", "x-api-key", "authorization", "scraperapi", "zenrows"):
        assert word not in src


# -- polite fetching --------------------------------------------------------------

def test_fetch_identifies_itself_and_sets_a_timeout():
    url = "https://open.example/story"
    s = FakeSession({"https://open.example/robots.txt": FakeResp("r", text=ROBOTS_OPEN),
                     url: FakeResp(url, text=_html("weekday_story.html"))})
    fr = _fetcher(s, timeout_s=12).fetch(url)
    assert fr.status == "ok"
    assert all(c["headers"]["User-Agent"].startswith("FORGE-ArticleReader/") for c in s.calls)
    assert all(c["timeout"] == 12 for c in s.calls)


def test_robots_disallow_is_obeyed():
    s = FakeSession({"https://open.example/robots.txt": FakeResp("r", text="User-agent: *\nDisallow: /private/\n"),
                     "https://open.example/private/a": FakeResp("x", text="<p>secret</p>")})
    fr = _fetcher(s).fetch("https://open.example/private/a")
    assert fr.status == "blocked_robots"
    assert [c["url"] for c in s.calls] == ["https://open.example/robots.txt"]


def test_robots_unreachable_means_no_fetch():
    def boom(url):
        raise TimeoutError("slow")
    s = FakeSession({"https://open.example/robots.txt": boom})
    assert _fetcher(s).fetch("https://open.example/a").status == "blocked_robots"


def test_missing_robots_file_allows():
    url = "https://open.example/a"
    s = FakeSession({url: FakeResp(url, text=_html("weekday_story.html"))})
    assert _fetcher(s).fetch(url).status == "ok"


def test_requests_to_one_host_are_spaced_out():
    t = {"now": 100.0}
    slept = []

    def sleep(s):
        slept.append(round(s, 3))
        t["now"] += s

    url1, url2 = "https://open.example/1", "https://open.example/2"
    s = FakeSession({"https://open.example/robots.txt": FakeResp("r", text=ROBOTS_OPEN),
                     url1: FakeResp(url1, text="<p>one one one one one one one one one</p>"),
                     url2: FakeResp(url2, text="<p>two two two two two two two two two</p>")})
    f = ar.PoliteFetcher(session=s, min_interval_s=10, permissions={}, sleep=sleep, clock=lambda: t["now"])
    f.fetch(url1)
    f.fetch(url2)
    # robots, page 1, page 2: two waits of the full interval (no time passes in fakes)
    assert slept == [10.0, 10.0]


def test_redirect_onto_restricted_outlet_is_blocked():
    url = "https://short.example/x"
    s = FakeSession({"https://short.example/robots.txt": FakeResp("r", text=ROBOTS_OPEN),
                     url: FakeResp("https://www.citizen.co.za/review-online/a/", text="<p>x</p>")})
    assert _fetcher(s).fetch(url).status == "blocked_policy"


def test_non_html_and_oversized_pages_are_refused():
    s = FakeSession({"https://open.example/robots.txt": FakeResp("r", text=ROBOTS_OPEN),
                     "https://open.example/a.pdf": FakeResp("https://open.example/a.pdf", text="%PDF", ctype="application/pdf"),
                     "https://open.example/big": FakeResp("https://open.example/big", text="x" * 5000)})
    f = _fetcher(s, max_bytes=1000)
    assert f.fetch("https://open.example/a.pdf").status == "not_html"
    assert f.fetch("https://open.example/big").status == "too_large"


# -- Google News links ----------------------------------------------------------------

def _old_style_link(target):
    raw = b"\x08\x13\x22" + bytes([len(target)]) + target.encode() + b"\xd2\x01\x00"
    return "https://news.google.com/rss/articles/" + base64.urlsafe_b64encode(raw).decode().rstrip("=") + "?oc=5"


def test_old_style_google_news_id_is_decoded_offline():
    link = _old_style_link("https://open.example/news/story-1")
    assert ar.decode_google_news_id(link) == "https://open.example/news/story-1"
    res = _fetcher(FakeSession()).resolve(link)
    assert (res.status, res.url) == ("decoded", "https://open.example/news/story-1")


def test_opaque_google_news_link_is_flagged_feed_date_only():
    # Real link shape from the replay: an AU_yqL... token that only resolves in a browser.
    link = ("https://news.google.com/rss/articles/CBMiigFBVV95cUxQSFBQbldDM0tCNDAyUWJ1cmhRaUd3Z0ItaFJSZ2Nf"
            "cWhLdDNXMWZib0xGem1fQjl1TGxMandZOG4zWlVPQmJmVDh6QXFBSnJjaTZpaHZwSC0wZXdOeVNqamZsZDZxNEIwWWZr"
            "N3hiZVFFc1IyejV4ZFhpN2cwaERMQTgtMHAyaVhSemc?oc=5")
    assert ar.decode_google_news_id(link) is None
    s = FakeSession({link: FakeResp(link + "&hl=en-US", text="<html>js redirect</html>")})
    res = _fetcher(s).resolve(link)
    assert res.status == "feed_date_only" and res.url is None


def test_google_news_http_redirect_to_article_is_followed():
    link = "https://news.google.com/rss/articles/opaque?oc=5"
    s = FakeSession({link: FakeResp("https://open.example/story", text="<p>x</p>")})
    res = _fetcher(s).resolve(link)
    assert (res.status, res.url) == ("redirected", "https://open.example/story")


# -- the reader end to end, with cache ---------------------------------------------------

def test_reader_only_reads_shortlisted_signals(tmp_path):
    s = FakeSession()
    reader = ar.ArticleReader(_fetcher(s), ar.ArticleCache(str(tmp_path)))
    r = reader.read_signal("https://open.example/a", "2026-10-08", shortlisted=False)
    assert r.status == "not_shortlisted" and s.calls == []
    assert r.effective_date == "2026-10-08"


def test_reader_reads_caches_and_reuses(tmp_path):
    url = "https://open.example/regulator"
    s = FakeSession({"https://open.example/robots.txt": FakeResp("r", text=ROBOTS_OPEN),
                     url: FakeResp(url, text=_html("meta_date_incident.html"))})
    reader = ar.ArticleReader(_fetcher(s), ar.ArticleCache(str(tmp_path)))
    first = reader.read_signal(url, "2026-10-08", shortlisted=True)
    assert first.status == "read" and first.incident_date == "2026-09-15" and not first.from_cache
    n = len(s.calls)
    again = reader.read_signal(url, "2026-10-08", shortlisted=True)
    assert again.from_cache and again.incident_date == "2026-09-15"
    assert len(s.calls) == n


def test_cache_entries_expire(tmp_path):
    cache = ar.ArticleCache(str(tmp_path), ttl_days=14)
    old = ar.ArticleReading("u", "read", fetched_at=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat())
    cache.put("u", old)
    assert cache.get("u") is None
    assert cache.purge_expired() == 1
    assert os.listdir(tmp_path) == []


def test_blocked_signal_keeps_feed_date_and_says_why(tmp_path):
    reader = ar.ArticleReader(_fetcher(FakeSession()), ar.ArticleCache(str(tmp_path)))
    r = reader.read_signal("https://newcastillian.com/2026/10/21/x/", "2026-10-21", shortlisted=True)
    assert r.status == "blocked"
    assert "written permission" in r.reason
    assert r.effective_date == "2026-10-21"
    assert os.listdir(tmp_path) == []  # nothing cached for a blocked outlet


def test_cache_dir_is_git_ignored():
    gi = open(os.path.join(REPO, ".gitignore"), encoding="utf-8").read()
    assert "data/cache/article_reader/" in gi
    assert "data/bench/article_fixtures_local/" in gi


# -- bench-case fixtures --------------------------------------------------------------------

FIXTURES = os.path.join(REPO, "data", "bench", "article_fixtures.json")
TARGETS = {"lt_nrcs_feed_date", "nc_p1_counterfeit", "nc_p2_quad_bike", "plk_88_firearms",
           "plk_airport_officers", "nc_oosthuizen_court", "nc_mlambo_court", "musina_carrim"}


@pytest.fixture(scope="module")
def bench_fixtures():
    with open(FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


def test_bench_fixtures_cover_every_batch6_case(bench_fixtures):
    assert set(bench_fixtures["cases"]) == TARGETS
    for cid, c in bench_fixtures["cases"].items():
        assert c["links"], cid
        assert c["manual_facts"] or any(l["status"] == "read" for l in c["links"]), cid


def test_bench_fixtures_hold_no_article_text(bench_fixtures):
    for c in bench_fixtures["cases"].values():
        for link in c["links"]:
            assert "body_text" not in link and "context" not in json.dumps(link["date_mentions"])


def test_bench_fixtures_respect_outlet_policy(bench_fixtures):
    for c in bench_fixtures["cases"].values():
        for link in c["links"]:
            policy = ar.outlet_policy(link["link"], permissions={})[0]
            if not ar.is_google_news(link["link"]) and policy != ar.POLICY_OPEN:
                assert link["status"] == "blocked", link["link"]
            if link["status"] == "feed_date_only":
                assert link["article_url"] is None


def test_oosthuizen_fixture_is_a_court_step_on_an_older_case(bench_fixtures):
    read = [l for l in bench_fixtures["cases"]["nc_oosthuizen_court"]["links"] if l["status"] == "read"]
    assert read, "Oosthuizen article should have been read"
    r = read[0]
    assert r["published"].startswith("2026-10-06")
    assert r["incident_date"] == "2025-12"
    assert any(m["kind"] == "future" and m["date"].startswith("2027-03") for m in r["date_mentions"])


def test_local_saved_pages_reparse_offline(bench_fixtures):
    local = os.path.join(REPO, "data", "bench", "article_fixtures_local")
    pages = [f for f in os.listdir(local)] if os.path.isdir(local) else []
    if not pages:
        pytest.skip("saved pages are local only and not on this machine")
    for c in bench_fixtures["cases"].values():
        for link in c["links"]:
            if link["status"] != "read" or "saved locally as" not in link["reason"]:
                continue
            name = link["reason"].split("saved locally as ")[1]
            r = ar.parse_article(open(os.path.join(local, name), encoding="utf-8").read(), link["link"])
            assert r.published == link["published"]
            assert r.incident_date == link["incident_date"]
