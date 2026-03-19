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
  CRIME_INTEL     — investigative journalism, SAPS, Hawks, NPA, corruption
  INFRASTRUCTURE  — Eskom, water, municipal, civic decay

All civic intel signals start at relevance_score=1.5.
Hawks signals are is_priority=1 by default.

Phase 32 compliance: writes heartbeat to pipeline_runs via pipeline_logger.

Dependencies:
  pip install feedparser requests beautifulsoup4 --break-system-packages

Author: FORGE Phase 31 / 32.5
"""

import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = BASE_DIR / "database.db"

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
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 0,
    },
    {
        "source_key":          "oxpeckers",
        "label":               "Oxpeckers Investigations",
        "fetch_mode":          "rss_direct",
        "url":                 "https://oxpeckers.org/feed/",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.4,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
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
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 0,
    },
    {
        "source_key":          "groundup",
        "label":               "GroundUp (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:groundup.org.za&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "INFRASTRUCTURE",
        "base_relevance":      1.3,
        "default_lat":         -33.9249,
        "default_lng":         18.4241,
        "is_priority_default": 0,
    },
    {
        "source_key":          "dailymaverick_corruption",
        "label":               "Daily Maverick — Corruption (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:dailymaverick.co.za+corruption+OR+VBS+OR+tender&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 1,
    },
    {
        "source_key":          "news24_crime",
        "label":               "News24 — Crime & Courts (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:news24.com+crime+OR+court+OR+arrest+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.3,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 0,
    },
    {
        "source_key":          "timeslive_corruption",
        "label":               "TimesLive — Corruption (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:timeslive.co.za+corruption+OR+Hawks+OR+NPA&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.3,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 0,
    },
    {
        "source_key":          "eskom_news",
        "label":               "Eskom (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=Eskom+loadshedding+OR+%22load+shedding%22+OR+%22power+outage%22+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "INFRASTRUCTURE",
        "base_relevance":      1.3,
        "default_lat":         -26.2041,
        "default_lng":         28.0473,
        "is_priority_default": 0,
    },
    {
        "source_key":          "municipal_infrastructure",
        "label":               "Municipal Infrastructure (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=south+africa+municipality+%22water+outage%22+OR+%22sewage%22+OR+%22road+collapse%22+OR+%22infrastructure%22&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "INFRASTRUCTURE",
        "base_relevance":      1.3,
        "default_lat":         -29.0,
        "default_lng":         25.5,
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
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 0,
    },
    {
        "source_key":          "hawks_media",
        "label":               "Hawks (DPCI) Media (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22Hawks+DPCI%22+OR+%22Directorate+for+Priority+Crime%22+arrest+OR+charge+OR+raid+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 1,
    },
    {
        "source_key":          "npa_media",
        "label":               "NPA Media (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:npa.gov.za+OR+%22NPA%22+prosecution+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "default_lat":         -25.7479,
        "default_lng":         28.2293,
        "is_priority_default": 1,
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


def classify_signal(title: str, content: str, base_stream: str,
                    base_relevance: float, base_priority: int) -> tuple:
    text      = f"{title} {content}"
    stream    = base_stream
    relevance = base_relevance
    priority  = base_priority
    for pattern, stream_override, prio_boost, rel_boost in _KEYWORD_PATTERNS:
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
            return feed.entries
        if feed.bozo:
            print(f"  [warn] feed bozo: {feed.bozo_exception}")
        return []
    except Exception as exc:
        print(f"  [error] feedparser: {exc}")
        return []


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
    else:
        print(f"  [warn] unknown fetch_mode: {mode}")
        return []


# ── Signal builder ─────────────────────────────────────────────────────────

def entry_to_signal(entry: dict, source: dict) -> Optional[dict]:
    """
    Convert a feedparser entry (or scrape dict) to a FORGE signal dict.
    Returns None if no usable title.
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
    published = None
    if entry.get("published_parsed"):
        try:
            published = datetime(
                *entry["published_parsed"][:6], tzinfo=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if not published:
        published = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Stable external ID
    link     = entry.get("link") or entry.get("id") or title
    ext_id   = "{}:{}".format(
        source["source_key"],
        hashlib.sha1(link.encode("utf-8", errors="replace")).hexdigest()[:16]
    )

    stream, relevance, priority = classify_signal(
        title, content,
        source["stream"],
        source["base_relevance"],
        source["is_priority_default"],
    )

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          source["source_key"],
        "external_id":     ext_id,
        "title":           title[:400],
        "content":         content,
        "lat":             source.get("default_lat"),
        "lng":             source.get("default_lng"),
        "timestamp":       published,
        "status":          "raw",
        "stream":          stream,
        "relevance_score": relevance,
        "is_priority":     priority,
    }


# ── DB helpers ─────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── Main run loop ──────────────────────────────────────────────────────────

def run(db_path: Path = DB_PATH) -> dict:
    """
    Iterate all SOURCES, fetch via appropriate strategy,
    deduplicate by external_id, insert new signals.
    Returns summary dict and writes heartbeat to pipeline_runs.
    """
    if not HAS_FEEDPARSER:
        print("[civic_intel] feedparser required — pip install feedparser --break-system-packages")
    if not HAS_REQUESTS:
        print("[civic_intel] requests required — pip install requests --break-system-packages")
    if not HAS_BS4:
        print("[civic_intel] beautifulsoup4 required for scraping — pip install beautifulsoup4 --break-system-packages")

    conn = _open_db(db_path)

    total_new     = 0
    total_skipped = 0
    total_errors  = 0
    per_source    = {}

    for source in SOURCES:
        key   = source["source_key"]
        label = source["label"]
        mode  = source.get("fetch_mode", "rss_direct")
        url_display = source.get("url") or source.get("scrape_url") or "?"
        print(f"[{label}] [{mode}] {url_display}")

        entries = fetch_source(source)
        new_s = skipped_s = 0

        for entry in entries:
            sig = entry_to_signal(entry, source)
            if sig is None:
                continue

            if conn.execute(
                "SELECT 1 FROM signals WHERE external_id=?",
                (sig["external_id"],)
            ).fetchone():
                skipped_s += 1
                continue

            try:
                conn.execute("""
                    INSERT INTO signals
                        (signal_id, source, external_id, title, content,
                         lat, lng, timestamp, status,
                         stream, relevance_score, is_priority, source_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'live')
                """, (
                    sig["signal_id"], sig["source"], sig["external_id"],
                    sig["title"],     sig["content"],
                    sig["lat"],       sig["lng"],
                    sig["timestamp"], sig["status"],
                    sig["stream"],    sig["relevance_score"], sig["is_priority"],
                ))
                new_s += 1
            except sqlite3.IntegrityError:
                skipped_s += 1
            except Exception as exc:
                print(f"  [error] insert {sig['external_id']}: {exc}")
                total_errors += 1

        conn.commit()
        per_source[key] = {"new": new_s, "skipped": skipped_s, "mode": mode}
        total_new     += new_s
        total_skipped += skipped_s

        if new_s > 0:
            print(f"  ✓ {new_s} new  ({skipped_s} known)")
        else:
            print(f"  · {skipped_s} known, nothing new")

    conn.close()

    # ── Summary + partial_success status ──────────────────────────────────
    sources_ok = sum(
        1 for v in per_source.values()
        if (v["new"] + v["skipped"]) > 0
    )
    if total_errors > 0 and sources_ok == 0:
        run_status = "error"
    elif sources_ok < len(SOURCES):
        run_status = "partial_success"
    else:
        run_status = "success"

    summary = {
        "collector":      "civic_intel",
        "sources":        len(SOURCES),
        "sources_ok":     sources_ok,
        "sources_failed": len(SOURCES) - sources_ok,
        "total_new":      total_new,
        "total_skipped":  total_skipped,
        "total_errors":   total_errors,
        "per_source":     per_source,
        "run_status":     run_status,
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }

    print(f"\n[civic_intel] Done — {total_new} new · "
          f"{sources_ok}/{len(SOURCES)} sources active · status: {run_status}")

    log_run(
        db_path,
        "civic_intel_collector",
        "success" if run_status in ("success", "partial_success") else "error",
        records_in=total_new + total_skipped,
        records_out=total_new,
        detail=summary,
    )
    return summary


if __name__ == "__main__":
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    print(json.dumps(run(db_path=db), indent=2))

# --- MEGA RUNNER ADAPTER ---
import asyncio as _asyncio

async def async_main(**kwargs):
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in civic_intel_collector.py: {e}")