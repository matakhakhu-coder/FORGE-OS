"""
FORGE — Civic Intelligence Collector  (Phase 31 / 32.5)
=========================================================
Hybrid OSINT collector for South African investigative and civic sources.

Fetch strategies per source type:
  RSS_DIRECT   — feedparser via requests raw fetch (verify=False for .gov.za)
  GOOGLE_NEWS  — Google News RSS proxy: news.google.com/rss/search?q=site:X
  HTML_SCRAPE  — BeautifulSoup scraper for sites with no RSS (SAPS, Hawks, NPA)

All HTTP is done through a single requests.Session with:
  - verify=False  (bypasses SA government SSL certificate failures)
  - urllib3 InsecureRequestWarning suppressed
  - 20s timeout
  - FORGE User-Agent

Raw bytes from requests are passed directly into feedparser.parse() or
BeautifulSoup — feedparser never makes its own HTTP calls.

Stream classification:
  PRIORITY        — organised, political and state-level crime the Hawks,
                    Crime Intelligence, NICOC or SSA would own (pi_signals)
  CRIME_INTEL     — street crime, SAPS, courts, investigative journalism
  INFRASTRUCTURE  — Eskom, water, municipal, civic decay

All civic intel signals start at relevance_score=1.5.
Hawks signals are is_priority=1 by default.

Phase 32 compliance: writes heartbeat to pipeline_runs via pipeline_logger.

Dependencies:
  pip install feedparser requests beautifulsoup4 --break-system-packages

Author: FORGE Phase 31 / 32.5
"""

__manifest__ = {
    "id":          "civic_intel_collector",
    "name":        "Civic Intelligence Collector",
    "description": "Hybrid OSINT collector for South African investigative sources. Pulls from 12 RSS, Google News, and HTML-scrape targets including amaBhungane, Daily Maverick, Hawks, NPA, and SAPS.",
    "icon":        "🏛",
    "entry":       "forage/collectors/civic_intel_collector.py",
    "args":        [],
    "job_key":     "civic_intel_collector",
    "version":     "1.0.0",
}

import hashlib
import json
import os
import time
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from forage.collectors import infra_signals as _infra
    from forage.collectors import pi_signals as _pi
    from forage.collectors import crime_signals as _crime
    from forage.collectors import place_signals as _place
    from forage.collectors import story_merge as _story
    from forage.collectors import run_window as _rw
    from forage.collectors import article_step as _art
    from forage.collectors import town_sources as _towns
    from forage.collectors import local_sitemaps as _ls
except ImportError:  # run as a script from the collectors directory
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from forage.collectors import infra_signals as _infra
    from forage.collectors import pi_signals as _pi
    from forage.collectors import crime_signals as _crime
    from forage.collectors import place_signals as _place
    from forage.collectors import story_merge as _story
    from forage.collectors import run_window as _rw
    from forage.collectors import article_step as _art
    from forage.collectors import town_sources as _towns
    from forage.collectors import local_sitemaps as _ls

# ── Path setup ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# FORGE_DB / FORGE_ROLE handling lives in core.db.paths (this collector used to
# ignore FORGE_DB and always write to the checkout's database.db).
import sys as _forge_sys
_FORGE_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _FORGE_REPO_ROOT not in _forge_sys.path:
    _forge_sys.path.insert(0, _FORGE_REPO_ROOT)
from core.db.paths import resolve_db_path as _resolve_db_path, connect_db as _connect_db  # noqa: E402
DB_PATH  = _resolve_db_path(BASE_DIR / "database.db")


def _forge_db_arg(db: Path) -> Path:
    """A --db argument: resolved as before by default; checked, never resolved, in staging."""
    from core.db.paths import checked_path, is_staging
    return checked_path(db) if is_staging() else db.resolve()

# ── Phase 32: path-safe pipeline logger ───────────────────────────────────
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu, sys as _sys
    _lp = Path(__file__).resolve().parent.parent.parent / "forage" / "utils" / "pipeline_logger.py"
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_lp))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass
log_run = _log_run_safe

# ── Refinery (Stable 1.1) ─────────────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t): return t  # noqa: E731

# ── Optional dependencies ──────────────────────────────────────────────────
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    print("[civic_intel] WARN: feedparser not installed — pip install feedparser --break-system-packages")

try:
    import requests as _requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[civic_intel] WARN: requests not installed — pip install requests --break-system-packages")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("[civic_intel] WARN: beautifulsoup4 not installed — pip install beautifulsoup4 --break-system-packages")

# ── Shared requests session ────────────────────────────────────────────────
# Single session used for ALL HTTP — verify=False bypasses SA gov SSL issues.
_SESSION = None

def _get_session():
    global _SESSION
    if _SESSION is None and HAS_REQUESTS:
        _SESSION = _requests.Session()
        _SESSION.verify  = False
        _SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; FORGE-OSINT/1.0; "
                "+https://github.com/matakhakhu-coder/FORGE)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
    return _SESSION

# ── Source registry ────────────────────────────────────────────────────────
# fetch_mode options:
#   "rss_direct"   — requests raw fetch → feedparser.parse(bytes)
#   "google_news"  — Google News RSS proxy → feedparser.parse(bytes)
#   "html_scrape"  — requests → BeautifulSoup → manual signal construction
#
# google_query: used when fetch_mode="google_news"
# scrape_url:   landing page URL for html_scrape mode
# scrape_selector: CSS selector for article links on the scrape page

SOURCES = [
    # ── Working RSS feeds (direct) ─────────────────────────────────────────
    {
        "source_key":          "amabhungane",
        "label":               "amaBhungane",
        "fetch_mode":          "rss_direct",
        "url":                 "https://amabhungane.org/feed/",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
    {
        "source_key":          "oxpeckers",
        "label":               "Oxpeckers Investigations",
        "fetch_mode":          "rss_direct",
        "url":                 "https://oxpeckers.org/feed/",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
    },

    # ── Google News proxy (replaces dead native RSS) ───────────────────────
    {
        "source_key":          "dailymaverick",
        "label":               "Daily Maverick (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:dailymaverick.co.za+investigat&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
    {
        "source_key":          "groundup",
        "label":               "GroundUp (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:groundup.org.za&hl=en-ZA&gl=ZA&ceid=ZA:en",
        # Was INFRASTRUCTURE for every GroundUp story (assaults, court cases).
        "stream":              "GLOBAL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },
    {
        "source_key":          "dailymaverick_corruption",
        "label":               "Daily Maverick — Corruption (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:dailymaverick.co.za+corruption+OR+VBS+OR+tender&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 1,
    },
    {
        "source_key":          "news24_crime",
        "label":               "News24 — Crime & Courts (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:news24.com+crime+OR+court+OR+arrest+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },
    {
        "source_key":          "timeslive_corruption",
        "label":               "TimesLive — Corruption (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:timeslive.co.za+corruption+OR+Hawks+OR+NPA&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },
    {
        "source_key":          "eskom_news",
        "label":               "Eskom (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=Eskom+loadshedding+OR+%22load+shedding%22+OR+%22power+outage%22+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        # 23 Oct 2026 benchmark: a blanket INFRASTRUCTURE default put Eskom
        # corporate and tariff news in the stream. Stories now reach
        # INFRASTRUCTURE only when infra_signals.classify() finds an event.
        "stream":              "GLOBAL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },
    {
        "source_key":          "municipal_infrastructure",
        "label":               "Municipal Infrastructure (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=south+africa+municipality+%22water+outage%22+OR+%22sewage%22+OR+%22road+collapse%22+OR+%22infrastructure%22&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },

    # ── HTML scrapers (gov sites with no RSS) ──────────────────────────────
    {
        "source_key":          "saps_media",
        "label":               "SAPS Media Releases (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:saps.gov.za+newsroom&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
    {
        "source_key":          "hawks_media",
        "label":               "Hawks (DPCI) Media (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22Hawks+DPCI%22+OR+%22Directorate+for+Priority+Crime%22+arrest+OR+charge+OR+raid+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 1,
    },
    {
        "source_key":          "npa_media",
        "label":               "NPA Media (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:npa.gov.za+OR+%22NPA%22+prosecution+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 1,
    },

    # ── Priority Intelligence searches (added 23 Oct 2026) ─────────────────
    # Base stream GLOBAL: pi_signals.route() decides PRIORITY from the story
    # text, so a search hit alone never lands in PI. when:30d makes Google
    # News return recent stories; without it most hits were months old.
    {
        "source_key":          "pi_siu",
        "label":               "SIU investigations (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22Special+Investigating+Unit%22+proclamation+OR+investigation+OR+tribunal+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_idac",
        "label":               "NPA Investigating Directorate (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22Investigating+Directorate%22+OR+IDAC+NPA+corruption+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_extortion",
        "label":               "Extortion and construction mafias (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22construction+mafia%22+OR+%22extortion+syndicate%22+OR+%22extortion+racket%22+South+Africa+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_political_violence",
        "label":               "Political killings (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=councillor+OR+whistleblower+OR+%22taxi+boss%22+%22shot+dead%22+OR+assassinated+OR+%22political+killings%22+South+Africa+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_sabotage",
        "label":               "Sabotage of Eskom and Transnet (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=sabotage+South+Africa+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_illegal_mining",
        "label":               "Illegal mining syndicates (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22illegal+mining%22+syndicate+OR+%22zama+zama%22+OR+%22Vala+Umgodi%22+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_cit",
        "label":               "Cash-in-transit gangs (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22cash-in-transit%22+heist+OR+gang+OR+suspects+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_trafficking",
        "label":               "Cross-border syndicates (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=syndicate+drugs+OR+trafficking+OR+smuggling+OR+firearms+arrested+South+Africa+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },
    {
        "source_key":          "pi_crime_intelligence",
        "label":               "SAPS Crime Intelligence (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22Crime+Intelligence%22+SAPS+when:30d&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "GLOBAL",
        "base_relevance":      1.4,
        "is_priority_default": 0,
        "pi_source":           True,
    },

    # ── Stable 1.2.1: ACLED replacement — SA security & conflict coverage ─
    {
        "source_key":          "defenceweb",
        "label":               "defenceWeb — SA Security & Defence",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.defenceweb.co.za/feed/",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
    {
        "source_key":          "citizen_news",
        "label":               "The Citizen — SA News & Crime",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.citizen.co.za/news/feed/",
        "stream":              "GLOBAL",
        "base_relevance":      1.3,
        "is_priority_default": 0,
    },

    # ── Provincial local outlets and municipal notices (added 23 Oct 2026) ─
    # The manual standard search found 10 of 11 Limpopo infrastructure
    # incidents in these feeds and in municipal notices; the national feeds
    # above found 1. "province" lets infra_signals.locate() prefer places in
    # that province; "pages" reads older feed pages (WordPress ?paged=N) so
    # a run reaches back across the window; "area_fallback" is the area a
    # district municipality's own notice covers when no town is named.
    # Zoutpansberger and Limpopo Mirror have no readable feed (JS sites);
    # citizen.co.za/bosvelder/feed/ and /mokopane-bulletin/feed/ redirect to
    # an unrelated article page, so those two titles are not wired in.
    {
        "source_key":          "review_online",
        "label":               "Review Online (Polokwane)",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.citizen.co.za/review-online/feed/",
        "stream":              "GLOBAL",
        "province":            "Limpopo",
        "pages":               10,
        "base_relevance":      1.4,
        "is_priority_default": 0,
    },
    {
        "source_key":          "letaba_herald",
        "label":               "Letaba Herald (Tzaneen)",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.citizen.co.za/letaba-herald/feed/",
        "stream":              "GLOBAL",
        "province":            "Limpopo",
        "pages":               10,
        "base_relevance":      1.4,
        "is_priority_default": 0,
    },
    {
        "source_key":          "limpopo_chronicle",
        "label":               "Limpopo Chronicle",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.limpopochronicle.co.za/feed/",
        "stream":              "GLOBAL",
        "province":            "Limpopo",
        "pages":               10,
        "base_relevance":      1.4,
        "is_priority_default": 0,
    },
    {
        "source_key":          "vhembe_dm",
        "label":               "Vhembe District Municipality notices",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.vhembe.gov.za/feed/",
        "stream":              "GLOBAL",
        "province":            "Limpopo",
        "pages":               3,
        "area_fallback":       "Vhembe District",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
    {
        "source_key":          "polokwane_municipality",
        "label":               "Polokwane Municipality notices",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.polokwane.gov.za/feed/",
        "stream":              "GLOBAL",
        "province":            "Limpopo",
        "pages":               3,
        "area_fallback":       "Polokwane",
        "base_relevance":      1.5,
        "is_priority_default": 0,
    },
]

# ── Keyword classification rules ───────────────────────────────────────────
# Format: (pattern, stream_override, priority_boost, relevance_boost)
KEYWORD_RULES = [
    (r"\b(VBS|Vhembe|Makhado)\b",                                     "CRIME_INTEL", True,  0.0),
    (r"\b(corrupt(ion|ed)?|brib(ery|e)|kickback|tender\s+fraud)\b",   "CRIME_INTEL", True,  0.0),
    (r"\b(state\s+capture|Zondo|Gupta|Magashule|Ace)\b",              "CRIME_INTEL", True,  0.0),
    (r"\b(money\s+laundering|POCA|PRECCA|irregular\s+expenditure)\b", "CRIME_INTEL", True,  0.0),
    (r"\b(Hawks|DPCI|Scorpions|NPA|SIU)\b",                           "CRIME_INTEL", True,  0.0),
    (r"\b(arrest(ed)?|charge[sd]?|indict(ed|ment)|convict)\b",        "CRIME_INTEL", False, 0.1),
    (r"\b(loadshed|load[\s.]shed|stage\s+[1-8]|Eskom)\b",            "INFRASTRUCTURE", False, 0.1),
    (r"\b(water[\s.]outage|water[\s.]cut|pipe[\s.]burst|sewage)\b",   "INFRASTRUCTURE", False, 0.1),
    (r"\b(pothole|road[\s.]clos|bridge[\s.]fail)\b",                  "INFRASTRUCTURE", False, 0.0),
    (r"\b(municipality|local[\s.]govern|ward[\s.]council|MFMA)\b",    "INFRASTRUCTURE", False, 0.0),
]

_KEYWORD_PATTERNS = [
    (re.compile(pat, re.IGNORECASE), stream, prio, boost)
    for pat, stream, prio, boost in KEYWORD_RULES
]


# Batch 7: the first rule treats any mention of Vhembe or Makhado as a
# Crime & Security priority story. That suits national feeds, where those
# names are rare, but every story in a Vhembe local paper names its own
# district. For local papers only VBS keeps that rule; the crime rules decide.
_AREA_RULE = _KEYWORD_PATTERNS[0][0]
_LOCAL_PAPER_AREA_RULE = re.compile(r"\bVBS\b", re.IGNORECASE)


def classify_signal(title: str, content: str, base_stream: str,
                    base_relevance: float, base_priority: int,
                    local_paper: bool = False) -> tuple:
    text      = f"{title} {content}"
    stream    = base_stream
    relevance = base_relevance
    priority  = base_priority
    for pattern, stream_override, prio_boost, rel_boost in _KEYWORD_PATTERNS:
        if local_paper and pattern is _AREA_RULE:
            pattern = _LOCAL_PAPER_AREA_RULE
        if pattern.search(text):
            stream    = stream_override
            relevance = min(relevance + rel_boost, 1.5)
            if prio_boost:
                priority = 1
    return stream, round(relevance, 3), priority


# ── Fetch helpers ──────────────────────────────────────────────────────────

def _raw_get(url: str, timeout: int = 20) -> Optional[bytes]:
    """
    Fetch URL bytes using the shared session (verify=False).
    Returns None on any error.
    """
    sess = _get_session()
    if not sess:
        return None
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  [warn] HTTP fetch failed: {url} — {exc}")
        return None


def fetch_rss_direct(source: dict) -> list:
    """
    Strategy: requests raw fetch → feedparser.parse(bytes).
    Bypasses feedparser's internal urllib entirely.
    """
    if not HAS_FEEDPARSER:
        return []
    raw = _raw_get(source["url"])
    if raw is None:
        return []
    try:
        feed = feedparser.parse(raw)
        if feed.entries:
            entries = list(feed.entries)
            for page in range(2, int(source.get("pages", 1)) + 1):
                if not entries or not _older_than_window(entries[-1]):
                    more = _raw_get(f"{source['url']}?paged={page}")
                    if more is None:  # one retry: busy local sites time out
                        time.sleep(2)
                        more = _raw_get(f"{source['url']}?paged={page}")
                    if more is None:
                        print(f"  [WARN] {source['label']}: page {page} unreachable; "
                              f"older items in the window are missing")
                        break
                    got = feedparser.parse(more).entries
                    if not got:
                        break
                    entries.extend(got)
                else:
                    break
            return entries
        if feed.bozo:
            print(f"  [warn] feed bozo: {feed.bozo_exception}")
        return []
    except Exception as exc:
        print(f"  [error] feedparser: {exc}")
        return []


# Publication window: items older than this are not collected (the 23 Oct
# 2026 baseline kept GroundUp and Oxpeckers items back to 2017). Override
# with --days or FORGE_CIVIC_WINDOW_DAYS; 0 disables the window.
WINDOW_DAYS = int(os.environ.get("FORGE_CIVIC_WINDOW_DAYS", "30") or 0)
# Batch 6 step 2 (G12): a fixed start date ("1 October" or YYYY-MM-DD, SAST)
# instead of counting back from the run start. Set with --from or
# FORGE_WINDOW_START; when set it replaces WINDOW_DAYS. Unset by default.
WINDOW_START: Optional[str] = os.environ.get("FORGE_WINDOW_START") or None


def run_window() -> "_rw.RunWindow":
    return _rw.RunWindow.build(days=WINDOW_DAYS, start=WINDOW_START, use_env=False)


def _entry_ts(entry: dict) -> Optional[str]:
    if entry.get("published_parsed"):
        try:
            return _infra.iso_utc(datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc))
        except Exception:
            return None
    if entry.get("published"):
        return entry.get("published")
    return None


def _older_than_window(entry: dict) -> bool:
    ts = _entry_ts(entry)
    w = run_window()
    return bool(ts and w.mode != "none" and not w.contains(ts))


def fetch_google_news(source: dict) -> list:
    """
    Strategy: Google News RSS proxy → feedparser.parse(bytes).
    Google News returns clean RSS so no SSL or XML issues.
    """
    if not HAS_FEEDPARSER:
        return []
    raw = _raw_get(source["url"])
    if raw is None:
        return []
    try:
        feed = feedparser.parse(raw)
        return feed.entries or []
    except Exception as exc:
        print(f"  [error] google_news feedparser: {exc}")
        return []


def fetch_html_scrape(source: dict) -> list:
    """
    Strategy: requests → BeautifulSoup → manual entry dicts.
    Used for gov sites (SAPS, Hawks, NPA) with no RSS.
    Returns a list of dicts with the same shape as feedparser entries
    so entry_to_signal() can process them identically.
    """
    if not HAS_BS4:
        print(f"  [skip] beautifulsoup4 not installed")
        return []

    raw = _raw_get(source["scrape_url"])
    if raw is None:
        return []

    try:
        soup = BeautifulSoup(raw, "html.parser")
        pattern = re.compile(source["scrape_link_pattern"])
        base    = source.get("scrape_base_url", "")

        entries = []
        seen_hrefs = set()

        for a in soup.find_all("a", href=True):
            href  = a["href"].strip()
            title = a.get_text(separator=" ", strip=True)

            # Skip empty titles, navigation links, duplicates
            if not title or len(title) < 10 or href in seen_hrefs:
                continue
            if not pattern.search(href):
                continue

            seen_hrefs.add(href)

            # Build absolute URL
            if href.startswith("http"):
                full_url = href
            else:
                full_url = base + href

            # Look for a nearby date string in the parent element
            parent_text = ""
            parent = a.find_parent(["li", "div", "article", "td", "tr"])
            if parent:
                parent_text = parent.get_text(separator=" ", strip=True)

            entries.append({
                "title":            title,
                "link":             full_url,
                "id":               full_url,
                "summary":          parent_text[:500] if parent_text != title else "",
                "published_parsed": None,  # no structured date from scrape
            })

            if len(entries) >= 30:  # cap per source
                break

        print(f"  [scrape] found {len(entries)} links at {source['scrape_url']}")
        return entries

    except Exception as exc:
        print(f"  [error] scrape {source['scrape_url']}: {exc}")
        return []


def fetch_source(source: dict) -> list:
    """Route to the correct fetch strategy based on source fetch_mode."""
    mode = source.get("fetch_mode", "rss_direct")
    if mode == "rss_direct":
        return fetch_rss_direct(source)
    elif mode == "google_news":
        return fetch_google_news(source)
    elif mode == "html_scrape":
        return fetch_html_scrape(source)
    elif mode == "sitemap":
        return fetch_local_sitemaps(source)
    else:
        print(f"  [warn] unknown fetch_mode: {mode}")
        return []


# ── Batch 7: local paper sitemaps (headline, link and date only) ─────────
#
# Not in SOURCES: a run reads local papers only when it names a town
# (--town) or asks for local papers alone (--local-only), so scheduled runs
# are unchanged. Which papers a town gets, and the per-publisher off switch,
# live in data/town_sources.json; FORGE_LOCAL_SITEMAPS=0 turns them all off.

def local_sources(town: Optional[str] = None, province: Optional[str] = None) -> list:
    """Civic source dicts for a town's readable local papers (all readable
    papers when town is None). One source per publisher and province, so a
    story both Zout News papers carry becomes one lead."""
    if town:
        ts = _towns.sources_for_town(town, province=province)
        for key, why in ts.blocked:
            print(f"[civic_intel] {ts.town}: not reading {key}: {why}")
        papers = ts.readable
    else:
        papers = _towns.all_readable_papers()
    groups: dict = {}
    for paper in papers:
        groups.setdefault((paper.get("publisher"), paper.get("province")), []).append(paper)
    out = []
    for (publisher, prov), group in groups.items():
        out.append({
            "source_key":          f"{publisher}_sitemaps",
            "label":               "Local papers (public sitemaps): " + ", ".join(p["name"] for p in group),
            "fetch_mode":          "sitemap",
            "papers":              group,
            "stream":              "GLOBAL",
            "province":            prov,
            "base_relevance":      1.4,
            "is_priority_default": 0,
            "lead_only":           True,
        })
    return out


def fetch_local_sitemaps(source: dict) -> list:
    report = _ls.CollectReport()
    entries = _ls.collect(source.get("papers") or [], window=run_window(), report=report)
    print(f"  [local_sitemaps] {report.items_seen} article links, {report.out_of_window} outside the window, "
          f"{report.leads} leads")
    return entries


# ── Signal builder ─────────────────────────────────────────────────────────

def entry_to_signal(entry: dict, source: dict, reading: Optional[dict] = None,
                    fixture: Optional[dict] = None, window_start=None) -> Optional[dict]:
    """
    Convert a feedparser entry (or scrape dict) to a FORGE signal dict.
    Returns None if no usable title.

    Batch 6 step 3: ``reading`` is the article reader's result (a dict);
    when it was read, the PI rules also read the article text. Every signal
    gets the court-step check (metadata only). ``fixture`` is for the bench.
    """
    title = (entry.get("title") or "").strip()
    if not title:
        return None

    # Content
    content = (entry.get("summary") or "").strip()
    if not content and entry.get("content"):
        try:
            content = entry["content"][0].get("value", "").strip()
        except (IndexError, AttributeError):
            pass
    content = re.sub(r"<[^>]+>", " ", content).strip()
    content = re.sub(r"\s+", " ", content)[:2000]

    # Timestamp
    # ISO UTC with Z, the signals table's dominant format (the baseline wrote
    # 'YYYY-MM-DD HH:MM:SS' here and ISO in google_news_collector).
    published = None
    if entry.get("published_parsed"):
        try:
            published = _infra.iso_utc(datetime(
                *entry["published_parsed"][:6], tzinfo=timezone.utc
            ))
        except Exception:
            pass
    if not published:
        published = _infra.iso_utc(datetime.now(timezone.utc))

    # Stable external ID
    link     = entry.get("link") or entry.get("id") or title
    # Batch 7: a sitemap lead keys on the article's link name, so the same
    # story on both Zout News sites (or found again next run) is one record.
    ext_key  = entry.get("_external_key") or link
    ext_id   = "{}:{}".format(
        source["source_key"],
        hashlib.sha1(ext_key.encode("utf-8", errors="replace")).hexdigest()[:16]
    )

    stream, relevance, priority = classify_signal(
        title, content,
        source["stream"],
        source["base_relevance"],
        source["is_priority_default"],
        local_paper=(source.get("fetch_mode") == "sitemap"),
    )
    # PI searches pull in foreign and unrelated crime news; the keyword rules
    # must not file it under CRIME_INTEL. Only PI routing (below) moves these.
    if source.get("pi_source"):
        stream, priority = source["stream"], source["is_priority_default"]

    # Infrastructure events win over the keyword rules: cable theft with an
    # arrest is an infrastructure event (the arrest is evidence), not crime.
    is_infra, infra_meta = _infra.infra_metadata(title, content, province=source.get("province"))
    if is_infra and infra_meta.get("infra_role") != "context":
        stream = "INFRASTRUCTURE"
        priority = 1 if infra_meta.get("infra_kind") in ("incident", "arrest") and infra_meta.get("location_candidate") else priority
    elif is_infra:
        # projects, policy and procurement stories about infrastructure are
        # context: keep the rule-based stream, but never INFRASTRUCTURE by
        # default alone
        if stream == "INFRASTRUCTURE":
            stream = "GLOBAL"
    elif stream == "INFRASTRUCTURE":
        # a keyword rule (e.g. bare "municipality" or "Eskom") is not an event
        stream = "GLOBAL"
        infra_meta = {"stream_reason": "infra keyword without an event; not routed to INFRASTRUCTURE"}
    # Crime & Security (Batch 2, 23 Oct 2026): killings, attempted murders and
    # unresolved deaths go by the story's words, whatever the source's default
    # stream or the keyword rules said. Road crashes, drownings and accidents
    # a keyword rule put in CRIME_INTEL move out.
    stream, crime_hit, crime_meta = _crime.route(stream, title, content, province=source.get("province"),
                                                 sa_source=source.get("fetch_mode") != "google_news")
    if crime_hit:
        priority = 1
    if crime_meta:
        infra_meta = {**(infra_meta or {}), **crime_meta}
        if crime_meta.get("crime_route") in ("moved_in", "kept", "moved_out"):
            infra_meta["stream_reason"] = "crime: " + crime_meta["crime_reason"]
    # Priority Intelligence (23 Oct 2026): organised, political and
    # state-level stories go to PRIORITY. Overlaps with a killing or an
    # infrastructure event follow FORGE_PI_MODE (see pi_signals).
    stream, pi_hit, pi_meta = _pi.route(stream, title, content, province=source.get("province"),
                                        infra_meta=infra_meta if is_infra else None,
                                        sa_source=source.get("fetch_mode") != "google_news",
                                        article_text=reading)
    if pi_hit:
        priority = 1
    if pi_meta:
        infra_meta = {**(infra_meta or {}), **pi_meta}
        if stream == _pi.STREAM:
            infra_meta["stream_reason"] = "pi: " + pi_meta["pi_reason"]
    if is_infra and "location_candidate" not in infra_meta and source.get("area_fallback"):
        infra_meta["location_candidate"] = {
            "place": source["area_fallback"], "province": source.get("province", ""),
            "lat": None, "lng": None, "precision": "issuing_municipality_area",
            "note": "The notice names no town the gazetteer knows; area of the issuing municipality."}
    # Batch 3 (23 Oct 2026): Crime & Security and Africa & World get the same
    # place estimate as Infrastructure and PI; every placed signal gets a
    # suggested precinct. Existing candidates are not moved.
    _placed = _place.add_place(stream, dict(infra_meta or {}), title, content, province=source.get("province"))
    if _placed:
        infra_meta = _placed
    # Batch 6 step 3: article dates and the court-step check (metadata only).
    infra_meta = _art.annotate(dict(infra_meta or {}), title, content, reading,
                               feed_date=published[:10], fixture=fixture, window_start=window_start)
    # Batch 7: provenance on every sitemap lead. It is a lead, not a report:
    # headline, link and date only, so a person opens the article to check.
    if entry.get("_provenance"):
        infra_meta = dict(infra_meta or {})
        infra_meta["provenance"] = entry["_provenance"]
        infra_meta["lead_only"] = True
        if not infra_meta.get("location_candidate") and entry["_provenance"].get("coverage_area"):
            infra_meta["lead_area"] = entry["_provenance"]["coverage_area"]

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          source["source_key"],
        "external_id":     ext_id,
        "title":           _sanitize(title)[:400],
        "content":         _sanitize(content),
        # Sprint 1 (2026-07-23): sources no longer carry a default_lat/lng —
        # a shared per-source coordinate was being stamped onto every
        # article regardless of content, producing false 0km-apart
        # correlations. NULL here is correct: these articles have no real
        # per-item location.
        "lat":             source.get("default_lat"),
        "lng":             source.get("default_lng"),
        "timestamp":       published,
        "status":          "raw",
        "stream":          stream,
        "relevance_score": relevance,
        "is_priority":     priority,
        "url":             link if link != title else None,
        "infra_meta":      infra_meta,
    }


# ── Title-based deduplication ──────────────────────────────────────────────
#
# Three-tier system:
#   similarity >= 0.90  → BLOCK: increment duplicate_count on existing signal
#   similarity  0.70–0.89 → ALLOW but tag: metadata_json["near_duplicate"] = true
#   similarity  < 0.70  → ALLOW: unique signal, insert normally
#
# Performance: titles from the last 24h are loaded ONCE before the main loop
# into a list of (signal_id, norm_title) tuples — no per-signal DB queries.

_STOPWORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","was","are","were","be","been","has","have","had",
    "will","would","could","should","this","that","it","its","as","up",
    "out","about","south","africa","african","new","also","after","says",
    "said","news","media","releases","newsroom","featured","inside","untitled",
}


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, remove stopwords, collapse whitespace."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    tokens = [t for t in title.split() if t not in _STOPWORDS and len(t) > 1]
    return " ".join(tokens)


def _title_similarity(norm_a: str, norm_b: str) -> float:
    """
    Token overlap similarity: |intersection| / |union| (Jaccard).
    Fast, no external dependencies, handles word reordering.
    Returns 0.0–1.0.
    """
    if not norm_a or not norm_b:
        return 0.0
    set_a = set(norm_a.split())
    set_b = set(norm_b.split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union        = len(set_a | set_b)
    return intersection / union if union else 0.0


def _ensure_dedup_index(conn: sqlite3.Connection) -> None:
    """Create the title+timestamp index once per run if it doesn't exist."""
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_title_time "
            "ON signals(title, timestamp)"
        )
        conn.commit()
    except Exception:
        pass


def _load_title_cache(conn: sqlite3.Connection,
                      window_hours: int = 24) -> list:
    """
    Load (signal_id, normalised_title) for all signals ingested in the
    last window_hours. Called once before the main loop — O(1) per signal.
    Returns list of (signal_id, norm_title) tuples.
    """
    try:
        rows = conn.execute(
            "SELECT signal_id, title FROM signals "
            "WHERE timestamp >= datetime('now', ?) "
            "  AND title IS NOT NULL",
            (f"-{window_hours} hours",)
        ).fetchall()
        return [(r["signal_id"], _normalize_title(r["title"])) for r in rows]
    except Exception:
        return []


def _check_dedup(sig: dict, title_cache: list) -> tuple:
    """
    Check a candidate signal against the title cache.

    Returns (action, matched_signal_id, similarity) where action is:
      'block'        — similarity >= 0.90, do not insert
      'near_dup'     — similarity 0.70–0.89, insert with tag
      'allow'        — similarity < 0.70, insert normally
    """
    if not sig.get("title"):
        return ("allow", None, 0.0)

    # Very short normalised titles (≤2 tokens) skip similarity —
    # they're too ambiguous to dedup reliably (e.g. "untitled")
    norm_new = _normalize_title(sig["title"])
    if len(norm_new.split()) <= 2:
        return ("allow", None, 0.0)

    best_sim = 0.0
    best_sid = None
    for (sid, norm_existing) in title_cache:
        sim = _title_similarity(norm_new, norm_existing)
        if sim > best_sim:
            best_sim = sim
            best_sid = sid
            if sim >= 0.90:
                break  # early exit — no need to keep scanning

    if best_sim >= 0.90:
        return ("block", best_sid, best_sim)
    if best_sim >= 0.70:
        return ("near_dup", best_sid, best_sim)
    return ("allow", None, best_sim)


def _increment_duplicate_count(conn: sqlite3.Connection,
                                signal_id: str) -> None:
    """
    Increment duplicate_count in the existing signal's metadata_json.
    Creates the key if it doesn't exist yet.
    """
    try:
        row = conn.execute(
            "SELECT metadata_json FROM signals WHERE signal_id=?",
            (signal_id,)
        ).fetchone()
        if not row:
            return
        meta = {}
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
            except Exception:
                pass
        meta["duplicate_count"] = meta.get("duplicate_count", 0) + 1
        conn.execute(
            "UPDATE signals SET metadata_json=? WHERE signal_id=?",
            (json.dumps(meta), signal_id)
        )
    except Exception:
        pass


# ── DB helpers ─────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python app.py --init-db"
        )
    conn = _connect_db(path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── Main run loop ──────────────────────────────────────────────────────────

def _source_hosts(source: dict) -> set:
    from urllib.parse import urlparse
    hosts = set()
    for k in ("url", "scrape_url"):
        if source.get(k):
            hosts.add((urlparse(source[k]).hostname or "").lower())
    for p in source.get("papers") or []:
        if p.get("host"):
            hosts.add(str(p["host"]).lower())
    hosts.discard("")
    return hosts


def run(db_path: Path = DB_PATH, town: Optional[str] = None, local_only: bool = False,
        province: Optional[str] = None, exclude_hosts: Optional[dict] = None,
        keep_signal=None) -> dict:
    """
    Iterate all SOURCES, fetch via appropriate strategy,
    deduplicate by external_id, insert new signals.
    Returns summary dict and writes heartbeat to pipeline_runs.

    Phase 34 addition: title-based deduplication layer.
      - Blocks near-identical signals (similarity >= 0.90)
      - Tags near-duplicates (similarity 0.70–0.89)
      - Loads title cache once per run — no per-signal DB queries

    Batch 8 (town mode only; both default to off, so a plain run is
    unchanged):
      - exclude_hosts {host: why}: sources on these hosts are not fetched.
      - keep_signal(sig, source) -> bool: a signal it rejects is not saved
        (counted as off_area in the summary).
    """
    if not HAS_FEEDPARSER:
        print("[civic_intel] feedparser required — pip install feedparser --break-system-packages")
    if not HAS_REQUESTS:
        print("[civic_intel] requests required — pip install requests --break-system-packages")
    if not HAS_BS4:
        print("[civic_intel] beautifulsoup4 required for scraping — pip install beautifulsoup4 --break-system-packages")

    conn = _open_db(db_path)

    # ── Dedup setup: index + title cache (loaded once, used per-signal) ───
    _ensure_dedup_index(conn)
    title_cache = _load_title_cache(conn, window_hours=24)
    print(f"[civic_intel] Dedup cache: {len(title_cache)} titles from last 24h")
    window = run_window()
    print(f"[civic_intel] Publication window: {window.describe()}")

    total_new      = 0
    total_skipped  = 0
    total_errors   = 0
    total_blocked  = 0
    total_near_dup = 0
    per_source     = {}

    # Batch 7: a named town adds its local papers; --local-only reads only
    # local papers. Neither: SOURCES exactly as before.
    sources = ([] if local_only else list(SOURCES))
    if town or local_only:
        sources += local_sources(town, province=province)
    excluded_sources = []
    if exclude_hosts:
        kept_sources = []
        for source in sources:
            hit = sorted(h for h in _source_hosts(source) if h in exclude_hosts)
            if hit:
                excluded_sources.append({"source_key": source["source_key"], "host": hit[0],
                                         "why": exclude_hosts[hit[0]]})
                print(f"[civic_intel] not reading {source['source_key']} ({hit[0]}): "
                      f"publisher '{exclude_hosts[hit[0]]}' is switched off in data/town_sources.json")
                continue
            kept_sources.append(source)
        sources = kept_sources
    total_off_area = 0

    for source in sources:
        key   = source["source_key"]
        label = source["label"]
        mode  = source.get("fetch_mode", "rss_direct")
        url_display = (source.get("url") or source.get("scrape_url")
                       or ", ".join(p.get("host", "") for p in source.get("papers") or []) or "?")
        print(f"[{label}] [{mode}] {url_display}")

        entries = fetch_source(source)
        new_s = skipped_s = blocked_s = near_dup_s = stale_s = 0

        for entry in entries:
            sig = entry_to_signal(entry, source, window_start=window.start)
            if sig is None:
                continue
            if window.mode != "none" and not window.contains(sig["timestamp"]):
                stale_s += 1
                continue
            if keep_signal is not None and not keep_signal(sig, source):
                total_off_area += 1
                continue
            # Batch 6 step 3: read the article of a shortlisted signal (off
            # unless --read-articles / FORGE_ARTICLE_READER=1). Skipped for a
            # link already saved, so a rerun doesn't fetch again.
            if _art.reader_enabled() and mode != "sitemap" and sig.get("url") and not conn.execute(
                    "SELECT 1 FROM signals WHERE external_id=?", (sig["external_id"],)).fetchone():
                reading = _art.read(sig["url"], sig["timestamp"][:10], sig["stream"], sig.get("infra_meta") or {})
                if reading is not None:
                    sig2 = entry_to_signal(entry, source, reading=reading, window_start=window.start)
                    if sig2 is not None:
                        sig = sig2
                    if _art.before_window(sig.get("infra_meta") or {}, window):
                        stale_s += 1
                        continue

            # ── Tier 0: external_id exact dedup (existing behaviour) ──────
            if conn.execute(
                "SELECT 1 FROM signals WHERE external_id=?",
                (sig["external_id"],)
            ).fetchone():
                skipped_s += 1
                continue

            # ── Tier 1–2: title similarity dedup ─────────────────────────
            action, matched_sid, sim = _check_dedup(sig, title_cache)

            if action == "block":
                # Do not insert — increment counter on matched signal
                _increment_duplicate_count(conn, matched_sid)
                conn.commit()
                blocked_s += 1
                print(
                    f"  [dedup:block]    {sig['title'][:60]!r} "
                    f"sim={sim:.2f} → blocked, counter↑ on {matched_sid[:8]}"
                )
                continue

            # Build metadata_json — tag near-duplicates and store source URL
            meta = {}
            if sig.get("url"):
                meta["url"] = sig["url"]
            if sig.get("infra_meta"):
                meta.update(sig["infra_meta"])
                if sig["stream"] == "INFRASTRUCTURE":
                    meta = _infra.link_supporting(conn, meta, sig["timestamp"])
            if action == "near_dup":
                meta["near_duplicate"] = True
                meta["near_dup_sim"]   = round(sim, 3)
                meta["near_dup_ref"]   = matched_sid
                near_dup_s += 1
                print(
                    f"  [dedup:near_dup] {sig['title'][:60]!r} "
                    f"sim={sim:.2f} → inserting with tag"
                )

            # Batch 5: one story, one record. Links this record to an earlier
            # one telling the same story (any stream, either collector).
            meta = _story.safe_link_story(conn, sig["signal_id"], sig["title"], sig["stream"],
                                          sig["timestamp"], meta)
            # Batch 6 step 3: one story, one court-step flag.
            meta = _art.share_story_step(conn, sig["signal_id"], sig["timestamp"], meta)
            meta_json = json.dumps(meta, ensure_ascii=False) if meta else None

            try:
                conn.execute("""
                    INSERT INTO signals
                        (signal_id, source, external_id, title, content,
                         lat, lng, timestamp, status,
                         stream, relevance_score, is_priority,
                         metadata_json, source_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'live')
                """, (
                    sig["signal_id"], sig["source"], sig["external_id"],
                    sig["title"],     sig["content"],
                    sig["lat"],       sig["lng"],
                    sig["timestamp"], sig["status"],
                    sig["stream"],    sig["relevance_score"], sig["is_priority"],
                    meta_json,
                ))
                new_s += 1
                # Add to cache so subsequent signals in this run can match it
                title_cache.append(
                    (sig["signal_id"], _normalize_title(sig["title"]))
                )
            except sqlite3.IntegrityError:
                skipped_s += 1
            except Exception as exc:
                print(f"  [error] insert {sig['external_id']}: {exc}")
                total_errors += 1

        conn.commit()
        per_source[key] = {
            "new":      new_s,
            "skipped":  skipped_s,
            "blocked":  blocked_s,
            "near_dup": near_dup_s,
            "stale":    stale_s,
            "fetched":  len(entries),
            "mode":     mode,
        }
        total_new      += new_s
        total_skipped  += skipped_s
        total_blocked  += blocked_s
        total_near_dup += near_dup_s

        if new_s > 0 or blocked_s > 0:
            parts = [f"✓ {new_s} new"]
            if blocked_s  > 0: parts.append(f"⊘ {blocked_s} blocked")
            if near_dup_s > 0: parts.append(f"≈ {near_dup_s} near-dup")
            parts.append(f"({skipped_s} known)")
            print(f"  {' · '.join(parts)}")
        else:
            print(f"  · {skipped_s} known, nothing new")

    conn.close()

    # ── Summary + partial_success status ──────────────────────────────────
    sources_ok = sum(
        1 for v in per_source.values()
        if (v["new"] + v["skipped"] + v.get("stale", 0) + v.get("blocked", 0)) > 0
    )
    if total_errors > 0 and sources_ok == 0:
        run_status = "error"
    elif sources_ok < len(sources):
        run_status = "partial_success"
    else:
        run_status = "success"

    summary = {
        "collector":       "civic_intel",
        "town":            town,
        "local_only":      local_only,
        "sources":         len(sources),
        "sources_ok":      sources_ok,
        "sources_failed":  len(sources) - sources_ok,
        "total_new":       total_new,
        "total_skipped":   total_skipped,
        "total_blocked":   total_blocked,
        "total_near_dup":  total_near_dup,
        "total_errors":    total_errors,
        "per_source":      per_source,
        "run_status":      run_status,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    if exclude_hosts is not None:
        summary["excluded_sources"] = excluded_sources
    if keep_signal is not None:
        summary["off_area"] = total_off_area

    dedup_line = ""
    if total_blocked > 0 or total_near_dup > 0:
        dedup_line = f" · ⊘ {total_blocked} blocked · ≈ {total_near_dup} near-dup"

    print(f"\n[civic_intel] Done — {total_new} new{dedup_line} · "
          f"{sources_ok}/{len(sources)} sources active · status: {run_status}")

    log_run(
        db_path,
        "civic_intel_collector",
        "success" if run_status in ("success", "partial_success") else "error",
        records_in=total_new + total_skipped + total_blocked,
        records_out=total_new,
        detail=summary,
    )
    return summary


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="FORGE Civic Intelligence Collector (SA)")
    _parser.add_argument("--db", type=Path, default=None, help="Path to database.db")
    _parser.add_argument("--dry-run", action="store_true", help="Fetch and display without DB writes")
    _parser.add_argument("--days", type=int, default=None,
                         help=f"Publication window in days (default {WINDOW_DAYS}; 0 = no window)")
    _parser.add_argument("--from", dest="window_start", default=None,
                         help="Fixed window start, SAST (YYYY-MM-DD or '1 October'); "
                              "replaces --days when given")
    _parser.add_argument("--read-articles", action="store_true",
                         help="Batch 6: read the article of each shortlisted signal "
                              "(off by default; outlet permissions apply)")
    _parser.add_argument("--town", default=None,
                         help="Batch 7: also read this town's local papers (data/town_sources.json)")
    _parser.add_argument("--province", default=None,
                         help="Province for a town not in data/town_sources.json")
    _parser.add_argument("--local-only", action="store_true",
                         help="Batch 7: read only local papers (the --town list, or all readable papers)")
    _args = _parser.parse_args()
    if _args.read_articles:
        _art.READ_ARTICLES = True
    if _args.days is not None:
        WINDOW_DAYS = _args.days
    if _args.window_start:
        _rw.parse_start(_args.window_start)  # fail fast on a bad date
        WINDOW_START = _args.window_start
    db = _forge_db_arg(_args.db) if _args.db else DB_PATH
    if _args.dry_run:
        print("[civic_intel] DRY RUN — would connect to:", db)
        print("[civic_intel] Dry run complete (no writes)")
    else:
        print(json.dumps(run(db_path=db, town=_args.town, local_only=_args.local_only,
                             province=_args.province), indent=2))

# --- MEGA RUNNER ADAPTER ---
import asyncio as _asyncio

async def async_main(**kwargs):
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in civic_intel_collector.py: {e}")