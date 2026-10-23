"""Local paper public sitemaps: headline, link and date only (Batch 7).

Reads the Limpopo Mirror and Zoutpansberger public sitemaps, the ones each
site lists in its own robots.txt, and turns each article URL into a lead:
the headline, the link and a date. Nothing else.

Rules this module keeps (Batch 7 terms check, 23 Oct 2026, register entry
B7-LM-ZB-sitemap):
  * Only sitemap files are fetched. ``SitemapFetcher`` refuses any URL that
    isn't a sitemap path on an allowed host, so no article page, search page,
    archive page or data endpoint can be requested through it. There is no
    code path that uses the sites' database key.
  * No article text is stored. A record's content is empty; its headline is
    the news sitemap's title, or, for the main sitemap (which has no titles),
    a headline rebuilt from the article's own link. Which one is recorded.
  * Polite: an identified user agent, robots.txt checked before every sitemap,
    one request per host at a time at least 10 seconds apart, a timeout, a size
    cap and no redirects followed.
  * Each record carries its provenance: publisher, paper, every link it was
    found at, the sitemap it came from, how the headline and date were
    obtained, when it was fetched and the legal basis.
  * Switchable: data/town_sources.json (publisher ``enabled``) and
    FORGE_LOCAL_SITEMAPS=0. See town_sources.py.

The two papers share stories: the same article often sits on both sites under
the same link name. Those become one lead with both links.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from urllib import robotparser
from urllib.parse import urlparse

try:
    from defusedxml import ElementTree as ET  # in requirements.txt
except Exception:  # pragma: no cover
    from xml.etree import ElementTree as ET  # type: ignore

USER_AGENT = (
    "FORGE-LocalSitemaps/0.1 (+https://forge-os-alpha.vercel.app/about; "
    "reads public sitemaps only: headline, link and date)"
)

# Hosts this module may contact at all. Adding a host is a code change on
# purpose: Caxton's sites and the Newcastillian need written permission first.
ALLOWED_HOSTS = frozenset({
    "www.limpopomirror.co.za", "limpopomirror.co.za",
    "www.zoutpansberger.co.za", "zoutpansberger.co.za",
})
SITEMAP_PATH = re.compile(r"^/(news-sitemap|sitemap-news|sitemap|sitemap-index)\.xml$")
ARTICLE_PATH = re.compile(r"^/article/([a-z0-9][a-z0-9-]*[a-z0-9])/?$")

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}
SAST = timezone(timedelta(hours=2))

# Without a run window the main sitemap (28,000+ links) is cut to this many
# days back by its last-changed date.
DEFAULT_MAIN_DAYS = 30

BASIS = ("Batch 7 terms check 23 Oct 2026 (B7-LM-ZB-sitemap): public sitemap listed in robots.txt; "
         "headline, link and date only; no article text; no database key")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_allowed_sitemap(url: str) -> tuple[bool, str]:
    p = urlparse(url or "")
    if p.scheme != "https":
        return False, "not https"
    if (p.hostname or "").lower() not in ALLOWED_HOSTS:
        return False, "host not on the sitemap allowlist"
    if p.query or p.fragment or not SITEMAP_PATH.match(p.path or ""):
        return False, "not a sitemap path; only sitemap files are fetched"
    return True, ""


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

@dataclass
class SitemapFetch:
    url: str
    status: str  # ok | refused | blocked_robots | error | too_large
    text: Optional[str] = None
    http_status: Optional[int] = None
    listed_in_robots: bool = False
    reason: str = ""
    fetched_at: str = ""


class SitemapFetcher:
    """Fetches sitemap files only, one host at a time, spaced out.

    ``session`` is anything with a requests-style ``get``; tests pass a fake.
    """

    def __init__(self, session=None, min_interval_s: float = 10.0, timeout_s: float = 30.0,
                 max_bytes: int = 20_000_000, user_agent: str = USER_AGENT,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if session is None:
            import requests  # local import keeps tests network-free
            session = requests.Session()
        self.session = session
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, Optional[tuple]] = {}
        self._lock = threading.Lock()
        self.requests_made: list[str] = []

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            last = self._last_hit.get(host)
            now = self._clock()
            if last is not None and now - last < self.min_interval_s:
                self._sleep(self.min_interval_s - (now - last))
            self._last_hit[host] = self._clock()

    def _get(self, url: str):
        self._wait_turn(_host(url))
        self.requests_made.append(url)
        return self.session.get(url, headers={"User-Agent": self.user_agent,
                                              "Accept": "application/xml,text/xml;q=0.9,text/plain;q=0.5"},
                                timeout=self.timeout_s, allow_redirects=False)

    def _robots_for(self, url: str):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            try:
                resp = self._get(base + "/robots.txt")
                code = getattr(resp, "status_code", 0) or 0
                if code in (401, 403) or 300 <= code < 400:
                    self._robots[base] = None  # refused or moved: treat as disallow
                else:
                    rp = robotparser.RobotFileParser()
                    text = (getattr(resp, "text", "") or "") if code < 400 else ""
                    rp.parse(text.splitlines())
                    listed = {ln.split(":", 1)[1].strip() for ln in text.splitlines()
                              if ln.lower().startswith("sitemap:")}
                    self._robots[base] = (rp, listed)
            except Exception:
                self._robots[base] = None
        return self._robots[base]

    def fetch(self, url: str) -> SitemapFetch:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok, why = is_allowed_sitemap(url)
        if not ok:
            return SitemapFetch(url, "refused", reason=why, fetched_at=now)
        robots = self._robots_for(url)
        if robots is None:
            return SitemapFetch(url, "blocked_robots", reason="robots.txt refused or unreachable", fetched_at=now)
        rp, listed = robots
        if not rp.can_fetch(self.user_agent, url):
            return SitemapFetch(url, "blocked_robots", reason="robots.txt disallows this path", fetched_at=now)
        try:
            resp = self._get(url)
        except Exception as exc:
            return SitemapFetch(url, "error", reason=f"{type(exc).__name__}: {exc}"[:200],
                                listed_in_robots=url in listed, fetched_at=now)
        code = getattr(resp, "status_code", None)
        if code is None or code >= 300:
            return SitemapFetch(url, "error", http_status=code, reason=f"HTTP {code}",
                                listed_in_robots=url in listed, fetched_at=now)
        body = getattr(resp, "content", b"") or b""
        if len(body) > self.max_bytes:
            return SitemapFetch(url, "too_large", http_status=code, reason=f"{len(body)} bytes",
                                listed_in_robots=url in listed, fetched_at=now)
        text = getattr(resp, "text", None)
        if text is None:
            text = body.decode("utf-8", "replace")
        return SitemapFetch(url, "ok", text=text, http_status=code,
                            listed_in_robots=url in listed, fetched_at=now)


# ---------------------------------------------------------------------------
# Headlines rebuilt from a link (main sitemap has no titles)
# ---------------------------------------------------------------------------

# Gazetteer place names that are also ordinary English words; left lower case
# so a rebuilt headline doesn't invent a place ("beauty", "mission").
_COMMON_WORDS = frozenset("""
beauty mission london harmony mara finale stable pile settlers jack shirley rita singapore bend
store mountain plaza river hill park new west east north south bridge dam valley farm school church
village town city station camp mine bush view rest grace hope glory victory paradise eden canaan
bethel goshen jerusalem zion
""".split())
_ACRONYMS = {
    "saps": "SAPS", "sandf": "SANDF", "anc": "ANC", "npa": "NPA", "eff": "EFF", "da": "DA",
    "sanral": "SANRAL", "bma": "BMA", "siu": "SIU", "ipid": "IPID", "tvet": "TVET",
    "univen": "UNIVEN", "mec": "MEC", "sars": "SARS", "nrcs": "NRCS", "sassa": "SASSA",
    "eskom": "Eskom", "hawks": "Hawks", "absa": "Absa",
}
_ROAD = re.compile(r"^(n|r)(\d{1,3})$")
_RAND = re.compile(r"^r(\d+)$")
_place_keys_cache: dict[str, frozenset] = {}


def _place_keys(province: Optional[str]) -> frozenset:
    key = province or ""
    if key not in _place_keys_cache:
        try:
            from forage.collectors import infra_signals as _inf
            idx = _inf._places_collector() or {}
            _place_keys_cache[key] = frozenset(
                k for k, v in idx.items()
                if " " not in k and (not province or any(p.get("province") == province for p in v)))
        except Exception:
            _place_keys_cache[key] = frozenset()
    return _place_keys_cache[key]


def slug_to_headline(slug: str, province: Optional[str] = None) -> str:
    """'chilling-mystery-of-two-bodies-on-mashawana-mountain' ->
    'Chilling mystery of two bodies on Mashawana mountain'.

    Capitals come back only for gazetteer places in the paper's province (not
    ordinary English words), road numbers, rand amounts and a few acronyms,
    so the place and crime rules can read the headline. Apostrophes can't
    come back ("carrims")."""
    words = [w for w in (slug or "").strip("/").split("-") if w]
    places = _place_keys(province)
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _ACRONYMS:
            out.append(_ACRONYMS[lw])
        elif _ROAD.match(lw) and (lw.startswith("n") or int(_ROAD.match(lw).group(2)) >= 21):
            out.append(lw.upper())  # N1, R524 (R10 or less reads as a rand amount)
        elif _RAND.match(lw):
            out.append("R" + lw[1:])
        elif lw in places and lw not in _COMMON_WORDS and len(lw) >= 4:
            out.append(lw.capitalize())
        elif i == 0:
            out.append(lw.capitalize())
        else:
            out.append(lw)
    text = " ".join(out)
    # "r50 000" style amounts: the slug splits on the thousands space
    return re.sub(r"\b(R\d+) (\d{3})\b", r"\1 \2", text)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class SitemapItem:
    url: str
    slug: str
    title: str
    headline_basis: str   # news_sitemap_title | url_slug
    when: datetime        # UTC
    date_basis: str       # news_sitemap_publication_date | sitemap_lastmod
    paper: str
    sitemap: str


def _parse_when(value: str) -> Optional[tuple[datetime, bool]]:
    """(UTC datetime, had_time). A bare date is taken as noon SAST so its
    calendar date is the same in UTC and South African time."""
    v = (value or "").strip()
    if not v:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if m:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, tzinfo=SAST)
        return d.astimezone(timezone.utc), False
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc), True


def parse_sitemap(xml_text: str, paper: dict, sitemap_url: str) -> list[SitemapItem]:
    """Article entries from one urlset. A sitemap index returns [] (its
    children are configured directly, so nothing is followed blindly)."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except Exception:
        return []
    if not root.tag.endswith("urlset"):
        return []
    host = (paper.get("host") or "").lower()
    out = []
    for u in root.findall("sm:url", NS):
        loc = (u.findtext("sm:loc", default="", namespaces=NS) or "").strip()
        p = urlparse(loc)
        if (p.hostname or "").lower() != host:
            continue
        m = ARTICLE_PATH.match(p.path or "")
        if not m:
            continue
        slug = m.group(1)
        news = u.find("news:news", NS)
        title = basis = None
        when = None
        date_basis = None
        if news is not None:
            t = (news.findtext("news:title", default="", namespaces=NS) or "").strip()
            if t:
                title, basis = re.sub(r"\s+", " ", t), "news_sitemap_title"
            w = _parse_when(news.findtext("news:publication_date", default="", namespaces=NS) or "")
            if w:
                when, date_basis = w[0], "news_sitemap_publication_date"
        if when is None:
            w = _parse_when(u.findtext("sm:lastmod", default="", namespaces=NS) or "")
            if w:
                when, date_basis = w[0], "sitemap_lastmod"
        if when is None:
            continue  # no date at all: can't be windowed, so not a lead
        if not title:
            title, basis = slug_to_headline(slug, paper.get("province")), "url_slug"
        out.append(SitemapItem(loc, slug, title, basis, when, date_basis,
                               paper.get("name", ""), sitemap_url))
    return out


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

_HEADLINE_RANK = {"news_sitemap_title": 0, "url_slug": 1}
_DATE_RANK = {"news_sitemap_publication_date": 0, "sitemap_lastmod": 1}


def merge_items(items: Iterable[SitemapItem]) -> list[dict]:
    """One lead per article link name across both papers and both sitemaps."""
    by_slug: dict[str, dict] = {}
    for it in items:
        lead = by_slug.get(it.slug)
        if lead is None:
            lead = by_slug[it.slug] = {"slug": it.slug, "links": [], "papers": [], "sitemaps": [],
                                       "title": it.title, "headline_basis": it.headline_basis,
                                       "when": it.when, "date_basis": it.date_basis}
        else:
            if _HEADLINE_RANK[it.headline_basis] < _HEADLINE_RANK[lead["headline_basis"]]:
                lead["title"], lead["headline_basis"] = it.title, it.headline_basis
            if (_DATE_RANK[it.date_basis], it.when) < (_DATE_RANK[lead["date_basis"]], lead["when"]):
                lead["when"], lead["date_basis"] = it.when, it.date_basis
        for k, v in (("links", it.url), ("papers", it.paper), ("sitemaps", it.sitemap)):
            if v not in lead[k]:
                lead[k].append(v)
    return list(by_slug.values())


def lead_to_entry(lead: dict, publisher: str, fetched_at: str, coverage: dict) -> dict:
    """A feedparser-shaped entry for civic_intel_collector.entry_to_signal."""
    when = lead["when"]
    papers = lead["papers"]
    provenance = {
        "source_kind": "public_sitemap",
        "publisher": publisher,
        "papers": papers,
        "links": lead["links"],
        "sitemaps": lead["sitemaps"],
        "headline_basis": lead["headline_basis"],
        "date_basis": lead["date_basis"],
        "fetched_at": fetched_at,
        "fetcher": USER_AGENT,
        "stored": "headline, link and date only; no article text",
        "basis": BASIS,
        "coverage_area": "; ".join(coverage[p] for p in papers if coverage.get(p)),
    }
    if lead["date_basis"] == "sitemap_lastmod":
        provenance["date_note"] = ("The main sitemap only gives the date the page last changed. "
                                   "The story was published on or before it.")
    return {
        "title": lead["title"],
        "link": lead["links"][0],
        "id": lead["links"][0],
        "summary": "",
        "published_parsed": when.timetuple(),
        "_external_key": "zoutnet:" + lead["slug"],
        "_provenance": provenance,
    }


@dataclass
class CollectReport:
    fetches: list = field(default_factory=list)   # SitemapFetch without text
    items_seen: int = 0
    leads: int = 0
    out_of_window: int = 0


def collect(papers: list, window=None, fetcher: Optional[SitemapFetcher] = None,
            now: Optional[datetime] = None, report: Optional[CollectReport] = None) -> list[dict]:
    """Fetch each paper's sitemaps and return feedparser-shaped lead entries.

    ``papers`` are dicts from town_sources (with ``key``, ``name``, ``host``,
    ``province``, ``sitemaps``). ``window`` is a run_window.RunWindow; with
    none, main-sitemap items older than DEFAULT_MAIN_DAYS are dropped.
    """
    fetcher = fetcher or SitemapFetcher()
    report = report if report is not None else CollectReport()
    now = now or datetime.now(timezone.utc)
    items: list[SitemapItem] = []
    publisher = "Zout News"
    coverage = {}
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for paper in papers:
        coverage[paper.get("name", "")] = paper.get("coverage_area", "")
        for sm in paper.get("sitemaps") or []:
            res = fetcher.fetch(sm["url"])
            report.fetches.append({"url": res.url, "status": res.status, "http": res.http_status,
                                   "listed_in_robots": res.listed_in_robots, "reason": res.reason})
            if res.status != "ok":
                print(f"  [local_sitemaps] {sm['url']}: {res.status} ({res.reason})")
                continue
            fetched_at = res.fetched_at or fetched_at
            got = parse_sitemap(res.text or "", paper, sm["url"])
            report.items_seen += len(got)
            items.extend(got)
    cutoff = now - timedelta(days=DEFAULT_MAIN_DAYS)
    kept = []
    for it in items:
        ts = it.when.strftime("%Y-%m-%dT%H:%M:%SZ")
        if window is not None and getattr(window, "mode", "none") != "none":
            if not window.contains(ts):
                report.out_of_window += 1
                continue
        elif it.date_basis == "sitemap_lastmod" and it.when < cutoff:
            report.out_of_window += 1
            continue
        kept.append(it)
    leads = merge_items(kept)
    report.leads = len(leads)
    return [lead_to_entry(lead, publisher, fetched_at, coverage) for lead in leads]
