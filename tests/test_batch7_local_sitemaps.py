"""Batch 7: Limpopo Mirror and Zoutpansberger public sitemaps (headline, link
and date only) and the per-town local paper list.

No network: robots.txt and sitemaps are saved copies (trimmed) in
tests/fixtures/local_sitemaps, fetched on 23 Oct 2026.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from forage.collectors import civic_intel_collector as civic
from forage.collectors import local_sitemaps as ls
from forage.collectors import run_window as rw
from forage.collectors import town_sources as ts

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures", "local_sitemaps")
NOW = datetime(2026, 10, 23, 19, 0, tzinfo=timezone.utc)

FILES = {
    "https://www.limpopomirror.co.za/robots.txt": "limpopomirror_robots.txt",
    "https://www.zoutpansberger.co.za/robots.txt": "zoutpansberger_robots.txt",
    "https://www.limpopomirror.co.za/news-sitemap.xml": "limpopomirror_news-sitemap.xml",
    "https://www.limpopomirror.co.za/sitemap.xml": "limpopomirror_sitemap.xml",
    "https://www.zoutpansberger.co.za/sitemap-news.xml": "zoutpansberger_sitemap-news.xml",
    "https://www.zoutpansberger.co.za/sitemap.xml": "zoutpansberger_sitemap.xml",
}


class Resp:
    def __init__(self, text, code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = code


class FakeSession:
    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout,
                           "allow_redirects": allow_redirects})
        if url in self.overrides:
            return self.overrides[url]
        if url in FILES:
            with open(os.path.join(FIX, FILES[url]), encoding="utf-8") as fh:
                return Resp(fh.read())
        return Resp("", 404)


def _fetcher(session=None, sleeps=None):
    clock = iter(range(0, 10_000))
    return ls.SitemapFetcher(session=session or FakeSession(),
                             sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                             clock=lambda: float(next(clock)))


def _window():
    return rw.RunWindow.build(start="2026-10-01", now=NOW, use_env=False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORGE_LOCAL_SITEMAPS", raising=False)
    monkeypatch.delenv("FORGE_ARTICLE_READER", raising=False)
    monkeypatch.delenv("FORGE_WINDOW_START", raising=False)


# ── Per-town list and switches ───────────────────────────────────────────

def test_limpopo_towns_get_both_zout_news_papers():
    for town in ("Musina", "Louis Trichardt", "Thohoyandou", "Polokwane"):
        got = ts.sources_for_town(town)
        assert {p["key"] for p in got.readable} == {"limpopo_mirror", "zoutpansberger"}, town
        assert got.province == "Limpopo"


def test_makhado_is_an_alias_and_zoutpansberger_comes_first():
    got = ts.sources_for_town("makhado")
    assert got.town == "Louis Trichardt"
    assert [p["key"] for p in got.readable] == ["zoutpansberger", "limpopo_mirror"]


def test_newcastle_reads_nothing_and_says_why():
    got = ts.sources_for_town("Newcastle")
    assert got.readable == []
    reasons = dict(got.blocked)
    assert "written permission" in reasons["newcastillian"]
    assert "2.7.1" in reasons["northern_natal_news"]


def test_unknown_town_uses_province_default_or_nothing():
    assert {p["key"] for p in ts.sources_for_town("Vuwani", province="Limpopo").readable} == {
        "limpopo_mirror", "zoutpansberger"}
    assert ts.sources_for_town("Vuwani").readable == []


def test_publisher_switch_in_config_turns_both_papers_off():
    cfg = ts.load_config()
    cfg["publishers"]["zoutnet"]["enabled"] = False
    got = ts.sources_for_town("Musina", config=cfg)
    assert got.readable == []
    assert len(got.blocked) == 2


def test_env_switch_turns_everything_off(monkeypatch):
    monkeypatch.setenv("FORGE_LOCAL_SITEMAPS", "0")
    got = ts.sources_for_town("Musina")
    assert got.readable == []
    assert all("FORGE_LOCAL_SITEMAPS" in why for _, why in got.blocked)
    assert civic.local_sources("Musina") == []


def test_blocked_publishers_have_no_sitemaps_and_no_allowed_host():
    cfg = ts.load_config()
    for key in ("newcastillian", "northern_natal_news"):
        assert cfg["papers"][key]["sitemaps"] == []
        assert cfg["papers"][key]["host"] not in ls.ALLOWED_HOSTS
    # Even switched on by mistake, they stay unreadable.
    cfg["publishers"]["pixelfish"]["enabled"] = True
    cfg["publishers"]["caxton"]["enabled"] = True
    assert ts.sources_for_town("Newcastle", config=cfg).readable == []


def test_newcastillian_stays_blocked_in_reader_permissions():
    with open(os.path.join(HERE, "..", "data", "article_reader_permissions.json"), encoding="utf-8") as fh:
        perms = json.load(fh)
    assert "newcastillian.com" in json.dumps(perms)
    assert "newcastillian.com" not in json.dumps(perms.get("granted", {}))


# ── Fetcher: sitemaps only, polite ───────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://www.limpopomirror.co.za/article/musina-businessman-shot-more-than-20-times-in-store",
    "https://www.limpopomirror.co.za/api/articles",
    "https://www.zoutpansberger.co.za/sitemap.xml?page=2",
    "http://www.limpopomirror.co.za/sitemap.xml",
    "https://www.citizen.co.za/sitemap.xml",
    "https://newcastillian.com/sitemap.xml",
    "https://www.limpopomirror.co.za/search?q=murder",
])
def test_fetcher_refuses_anything_but_an_allowed_sitemap(url):
    sess = FakeSession()
    res = _fetcher(sess).fetch(url)
    assert res.status == "refused"
    assert sess.calls == []


def test_fetcher_checks_robots_first_identifies_itself_and_never_follows_redirects():
    sess = FakeSession()
    res = _fetcher(sess).fetch("https://www.limpopomirror.co.za/sitemap.xml")
    assert res.status == "ok" and res.listed_in_robots
    assert [c["url"] for c in sess.calls] == ["https://www.limpopomirror.co.za/robots.txt",
                                             "https://www.limpopomirror.co.za/sitemap.xml"]
    for c in sess.calls:
        assert c["headers"]["User-Agent"].startswith("FORGE-LocalSitemaps/")
        assert c["allow_redirects"] is False
        assert c["timeout"]


def test_robots_disallow_or_refusal_blocks_the_sitemap():
    sess = FakeSession({"https://www.limpopomirror.co.za/robots.txt": Resp("User-agent: *\nDisallow: /\n")})
    res = _fetcher(sess).fetch("https://www.limpopomirror.co.za/sitemap.xml")
    assert res.status == "blocked_robots"
    assert len(sess.calls) == 1
    sess = FakeSession({"https://www.zoutpansberger.co.za/robots.txt": Resp("no", 403)})
    assert _fetcher(sess).fetch("https://www.zoutpansberger.co.za/sitemap.xml").status == "blocked_robots"


def test_requests_to_one_host_are_spaced_ten_seconds_apart():
    sleeps = []
    f = _fetcher(FakeSession(), sleeps)
    f.fetch("https://www.limpopomirror.co.za/news-sitemap.xml")
    f.fetch("https://www.limpopomirror.co.za/sitemap.xml")
    # robots, news, main: two waits, each topping up to 10 s
    assert len(sleeps) == 2 and all(s == pytest.approx(9.0) for s in sleeps)


def test_redirect_error_and_oversize_are_not_ok():
    url = "https://www.limpopomirror.co.za/sitemap.xml"
    assert _fetcher(FakeSession({url: Resp("", 301)})).fetch(url).status == "error"
    assert _fetcher(FakeSession({url: Resp("", 500)})).fetch(url).status == "error"
    f = _fetcher(FakeSession({url: Resp("x" * 50)}))
    f.max_bytes = 10
    assert f.fetch(url).status == "too_large"


# ── Parsing and headlines ────────────────────────────────────────────────

def _paper(key):
    return {"key": key, **ts.load_config()["papers"][key]}


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def test_news_sitemap_gives_title_and_publication_time():
    items = ls.parse_sitemap(_read("limpopomirror_news-sitemap.xml"), _paper("limpopo_mirror"),
                             "https://www.limpopomirror.co.za/news-sitemap.xml")
    by = {i.slug: i for i in items}
    it = by["musina-businessman-shot-more-than-20-times-in-store"]
    assert it.title == "Musina businessman shot more than 20 times in store"
    assert it.headline_basis == "news_sitemap_title"
    assert it.date_basis == "news_sitemap_publication_date"
    assert it.when == datetime(2026, 10, 22, 7, 29, tzinfo=timezone.utc)


def test_main_sitemap_keeps_only_dated_article_links_and_rebuilds_headlines():
    items = ls.parse_sitemap(_read("limpopomirror_sitemap.xml"), _paper("limpopo_mirror"),
                             "https://www.limpopomirror.co.za/sitemap.xml")
    assert all("/article/" in i.url for i in items)
    assert len(items) == 6  # home and about pages dropped
    by = {i.slug: i for i in items}
    it = by["chilling-mystery-of-two-bodies-on-mashawana-mountain"]
    assert it.title == "Chilling mystery of two bodies on Mashawana mountain"
    assert it.headline_basis == "url_slug" and it.date_basis == "sitemap_lastmod"
    # a bare date is noon SAST, so the calendar date is the same everywhere
    assert it.when.strftime("%Y-%m-%d") == "2026-10-08"


def test_links_on_another_host_and_sitemap_indexes_are_ignored():
    xml = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           '<url><loc>https://evil.example/article/x-y</loc><lastmod>2026-10-10</lastmod></url></urlset>')
    assert ls.parse_sitemap(xml, _paper("limpopo_mirror"), "u") == []
    idx = _read("limpopomirror_robots.txt")  # not XML at all
    assert ls.parse_sitemap(idx, _paper("limpopo_mirror"), "u") == []
    sidx = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<sitemap><loc>https://www.zoutpansberger.co.za/sitemap.xml</loc></sitemap></sitemapindex>')
    assert ls.parse_sitemap(sidx, _paper("zoutpansberger"), "u") == []


def test_slug_headline_capitals():
    h = ls.slug_to_headline("thohoyandou-businessman-shot-dead-near-n1-carousel-plaza", "Limpopo")
    assert h == "Thohoyandou businessman shot dead near N1 carousel plaza"
    assert ls.slug_to_headline("r524-crash-claims-saps-member", "Limpopo") == "R524 crash claims SAPS member"
    assert ls.slug_to_headline("mob-justice-claims-another-life-in-phiphidi", "Limpopo").endswith("Phiphidi")
    # place names that are ordinary words stay lower case
    assert ls.slug_to_headline("beauty-queen-on-a-mission", "Limpopo") == "Beauty queen on a mission"


# ── Collecting leads ─────────────────────────────────────────────────────

def _collect(papers=None, window=None):
    papers = papers or ts.sources_for_town("Musina").readable
    rep = ls.CollectReport()
    entries = ls.collect(papers, window=window or _window(), fetcher=_fetcher(), now=NOW, report=rep)
    return entries, rep


def test_collect_windows_merges_shared_stories_and_records_provenance():
    entries, rep = _collect()
    assert rep.out_of_window == 1  # the 2019 article
    assert all(f["status"] == "ok" and f["listed_in_robots"] for f in rep.fetches)
    by = {e["_external_key"]: e for e in entries}
    neltox = by["zoutnet:residents-back-army-crackdown-but-demand-border-fence"]
    prov = neltox["_provenance"]
    assert prov["papers"] == ["Limpopo Mirror", "Zoutpansberger"]
    assert len(prov["links"]) == 2 and len(prov["sitemaps"]) == 2
    assert prov["source_kind"] == "public_sitemap" and prov["publisher"] == "Zout News"
    assert prov["stored"].startswith("headline, link and date only")
    assert prov["fetched_at"] and prov["fetcher"] == ls.USER_AGENT and "B7-LM-ZB-sitemap" in prov["basis"]
    assert "date_note" in prov  # lastmod, not a publication date
    assert neltox["summary"] == ""
    # in both sitemaps: the news sitemap's real title and time win
    musina = by["zoutnet:musina-businessman-shot-more-than-20-times-in-store"]
    assert musina["_provenance"]["headline_basis"] == "news_sitemap_title"
    assert musina["_provenance"]["date_basis"] == "news_sitemap_publication_date"
    cams = by["zoutnet:cameras-help-crack-down-on-vehicle-smuggling-at-border-posts"]
    assert cams["_provenance"]["papers"] == ["Limpopo Mirror", "Zoutpansberger"]


def test_collect_without_window_drops_old_main_sitemap_links():
    entries, rep = _collect(window=rw.RunWindow.build(now=NOW, use_env=False))
    assert not any("stadium-of-dreams" in e["link"] for e in entries)


# ── Through the Batch 2-6 pipeline ───────────────────────────────────────

def _signals():
    entries, _ = _collect()
    src = civic.local_sources("Musina")
    assert len(src) == 1 and src[0]["fetch_mode"] == "sitemap"
    return {e["_external_key"].split(":", 1)[1]: civic.entry_to_signal(e, src[0]) for e in entries}


def test_leads_go_through_crime_place_and_story_rules():
    sigs = _signals()
    mash = sigs["chilling-mystery-of-two-bodies-on-mashawana-mountain"]
    assert mash["stream"] == "CRIME_INTEL"
    assert mash["infra_meta"]["location_candidate"]["place"] == "Mashawana"
    musina = sigs["musina-businessman-shot-more-than-20-times-in-store"]
    assert musina["stream"] == "CRIME_INTEL"
    assert musina["infra_meta"]["location_candidate"]["place"] == "Musina"
    boom = sigs["two-arrested-with-explosives-after-uber-driver-raises-alarm"]
    assert boom["stream"] == "CRIME_INTEL"
    fest = sigs["lekompo-tshivenda-and-xigaza-music-festival-set-for-vleifontein"]
    assert fest["stream"] == "GLOBAL"


def test_district_name_alone_does_not_make_a_local_story_crime():
    # Live run 23 Oct: arts and election stories went to Crime & Security
    # only because they named Vhembe. Local papers skip that rule; VBS keeps it.
    src = civic.local_sources("Musina")[0]
    def sig(title, source):
        e = {"title": title, "link": "https://www.limpopomirror.co.za/article/x", "summary": "",
             "published_parsed": NOW.timetuple()}
        return civic.entry_to_signal(e, source)
    assert sig("Vhembe arts structures urged to demonstrate impact", src)["stream"] == "GLOBAL"
    assert sig("Makhado election lists reveal old hands", src)["stream"] == "GLOBAL"
    assert sig("VBS looters face new charges", src)["stream"] == "CRIME_INTEL"
    national = next(s for s in civic.SOURCES if s["source_key"] == "vhembe_dm")
    assert civic.classify_signal("Vhembe arts structures urged", "", "GLOBAL", 1.0, 0)[0] == "CRIME_INTEL"
    assert national["fetch_mode"] != "sitemap"


def test_every_lead_carries_provenance_and_no_text():
    for slug, sig in _signals().items():
        meta = sig["infra_meta"]
        assert meta["lead_only"] is True and meta["provenance"]["links"], slug
        assert sig["content"] == ""
        assert sig["source"] == "zoutnet_sitemaps"
        if not meta.get("location_candidate"):
            assert meta["lead_area"].startswith("Vhembe")


def test_external_id_is_the_same_whichever_paper_is_found_first():
    entries, _ = _collect()
    e = next(x for x in entries if "army-crackdown" in x["link"])
    src = civic.local_sources("Musina")[0]
    a = civic.entry_to_signal(e, src)["external_id"]
    e2 = dict(e, link=e["_provenance"]["links"][1])
    assert civic.entry_to_signal(e2, src)["external_id"] == a


# ── Full collector run on a throwaway database ───────────────────────────

SCHEMA = """
CREATE TABLE signals (signal_id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL, content TEXT, lat REAL, lng REAL, timestamp DATETIME NOT NULL, status TEXT NOT NULL,
  metadata_json TEXT, cluster_id TEXT, is_priority INTEGER NOT NULL DEFAULT 0, stream TEXT NOT NULL,
  relevance_score REAL NOT NULL DEFAULT 1.0, source_type TEXT NOT NULL DEFAULT 'live',
  duplicate_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE pipeline_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT NOT NULL, status TEXT NOT NULL,
  records_in INTEGER, records_out INTEGER, duration_s REAL, detail_json TEXT, run_at TEXT);
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "copy.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(civic, "WINDOW_START", "2026-10-01")
    real_fetcher = ls.SitemapFetcher
    monkeypatch.setattr(ls, "SitemapFetcher",
                        lambda *a, **k: real_fetcher(session=FakeSession(), sleep=lambda s: None))
    real_build = rw.RunWindow.build
    monkeypatch.setattr(civic._rw.RunWindow, "build",
                        classmethod(lambda cls, **k: real_build(**{**k, "now": NOW})))
    return path


def test_town_run_saves_leads_once_with_story_and_provenance(db, monkeypatch):
    monkeypatch.setenv("FORGE_ARTICLE_READER", "1")
    read_calls = []
    monkeypatch.setattr(civic._art, "read", lambda *a, **k: read_calls.append(a))
    out = civic.run(db_path=db, town="Musina", local_only=True)
    assert out["sources"] == 1 and out["total_new"] >= 8
    assert read_calls == []  # never reads an article, even with the reader on
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT title, content, metadata_json FROM signals").fetchall()
    assert len(rows) == out["total_new"]
    for title, content, mj in rows:
        meta = json.loads(mj)
        assert content == "" and meta["lead_only"] and meta["provenance"]["links"]
        assert "story_id" in meta or "story_role" in meta, title
    again = civic.run(db_path=db, town="Musina", local_only=True)
    assert again["total_new"] == 0


def test_plain_run_is_unchanged_and_reads_no_local_paper(db, monkeypatch):
    monkeypatch.setattr(civic, "SOURCES", [])
    seen = []
    monkeypatch.setattr(civic, "fetch_source", lambda s: seen.append(s) or [])
    out = civic.run(db_path=db)
    assert seen == [] and out["sources"] == 0
