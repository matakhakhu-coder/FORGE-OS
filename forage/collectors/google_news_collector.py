#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — Generalized News Monitor (Google News RSS)
═══════════════════════════════════════════════════

Dynamically generates Google News RSS search queries from FORGE's actor
database, providing continuous broad-spectrum media monitoring across
South African and regional open-source intelligence domains.

Collection strategy:
  1. Load actor names from the `actors` table (filter: length > 4, skip
     generic labels). Accepts --keywords CLI override.
  2. Build localized Google News RSS URL per query term:
     https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en
  3. Parse RSS XML using stdlib xml.etree.ElementTree
  4. Apply 3-second courteous delay between fetches
  5. Deduplicate via sha256(title + pubdate) → external_id

Stream routing:
  CRIME_INTEL  — if query actor matches a crime/security keyword context
  GLOBAL       — all other news articles

Gravity scoring:
  Base: 0.30 (news article, unverified provenance)

Stable 1.1/1.2 compliance:
  source = manifest["id"] = "google_news_monitor"
  Zero new DB tables. All columns present in Stable 1.1 schema.

Usage:
  python forage/collectors/google_news_collector.py
  python forage/collectors/google_news_collector.py --dry-run
  python forage/collectors/google_news_collector.py --keywords "Zuma,Eskom,HAWKS"
  python forage/collectors/google_news_collector.py --max-queries 5 --days 3
"""

__manifest__ = {
    "id":          "google_news_monitor",
    "name":        "Generalized News Monitor",
    "description": "Polls localized RSS keyword configurations to trace emergent regional alerts, corporate naming events, and actor network mentions.",
    "icon":        "📰",
    "entry":       "forage/collectors/google_news_collector.py",
    "args":        [],
    "job_key":     "google_news_monitor",
    "version":     "1.0.0",
}

# ── Windows CP1252 safety ────────────────────────────────────────────────────
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Standard library ─────────────────────────────────────────────────────────
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Path setup ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SOURCE_ID = __manifest__["id"]

_FORGE_DB_ENV = os.environ.get("FORGE_DB")
DB_PATH = Path(_FORGE_DB_ENV) if _FORGE_DB_ENV else BASE_DIR / "database.db"

# ── Sanitizer (Stable 1.1 compliance) ────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text
except ImportError:
    def sanitize_text(t):
        return re.sub(r"<[^>]{0,500}>", " ", t or "").strip()

# ── Pipeline logger ──────────────────────────────────────────────────────────
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*_a, **_kw):
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

GNEWS_RSS_BASE = "https://news.google.com/rss/search"
REQUEST_DELAY = 3.0      # seconds between RSS fetches (courteous)
CONTENT_CAP = 3000       # chars stored in signal.content
DEFAULT_MAX_QUERIES = 10
DEFAULT_DAYS = 7

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Generic labels to skip when loading actor names from DB
_GENERIC_LABELS = frozenset({
    "unknown", "other", "unnamed", "n/a", "none", "test",
    "person", "institution", "organization", "government",
    "media", "movement", "location", "political party",
    "south africa", "south african",
})

# Crime/security context keywords — if an actor query term appears alongside
# these in the DB or matches these patterns, route to CRIME_INTEL stream
_CRIME_KEYWORDS = frozenset({
    "police", "crime", "criminal", "corrupt", "fraud", "murder",
    "arrest", "prosecut", "npa", "hawks", "saps", "siu", "gang",
    "terror", "extremis", "smuggl", "traffick", "launder", "theft",
    "scopa", "intelligence", "security", "prison", "incarcerat",
    "syndicate", "cartel", "heist", "extort", "brib", "embezzl",
})

# ── HTML stripping ───────────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(r"<[^>]{0,2000}>", re.DOTALL)
_WHITESPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_html(raw: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ══════════════════════════════════════════════════════════════════════════════
# Actor Loading
# ══════════════════════════════════════════════════════════════════════════════

def _load_actor_queries(max_queries: int) -> list[str]:
    """
    Load actor names from the database for query generation.
    Filters: length > 4, not in generic labels set.
    Returns up to max_queries actor names sorted by most recently created.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT name FROM actors
            WHERE length(name) > 4
            ORDER BY created_at DESC
            """
        ).fetchall()
    except Exception as e:
        print(f"[gnews] Actor query failed: {e}")
        rows = []
    finally:
        conn.close()

    queries = []
    for row in rows:
        name = row[0].strip()
        if name.lower() in _GENERIC_LABELS:
            continue
        if len(name) <= 4:
            continue
        queries.append(name)
        if len(queries) >= max_queries:
            break

    return queries


def _is_crime_context(query: str) -> bool:
    """
    Determine if a query term is in a crime/security context.
    Checks if any crime keyword appears as a substring of the query.
    """
    q_lower = query.lower()
    for kw in _CRIME_KEYWORDS:
        if kw in q_lower:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# RSS Fetching & Parsing
# ══════════════════════════════════════════════════════════════════════════════

def _build_rss_url(query: str) -> str:
    """Build a Google News RSS search URL localized to ZA."""
    encoded = urllib.parse.quote(query)
    return f"{GNEWS_RSS_BASE}?q={encoded}&hl=en-ZA&gl=ZA&ceid=ZA:en"


def _fetch_rss(url: str) -> bytes | None:
    """Fetch RSS XML content from a URL. Returns raw bytes or None on error."""
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })

    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as e:
        if e.code == 429:
            print(f"[gnews] Rate limited (429) — backing off")
        elif e.code == 403:
            print(f"[gnews] Access denied (403) for URL")
        else:
            print(f"[gnews] HTTP {e.code}: {e.reason}")
        return None
    except URLError as e:
        print(f"[gnews] Network error: {e.reason}")
        return None
    except Exception as e:
        print(f"[gnews] Fetch error: {e}")
        return None


def _parse_pubdate(raw: str) -> str:
    """Parse RFC 2822 / ISO pubDate strings to ISO format."""
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Try RFC 2822 (standard RSS format)
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    # Try ISO variants
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rss_items(xml_bytes: bytes, query: str, days: int) -> list[dict]:
    """
    Parse Google News RSS XML into a list of raw item dicts.
    Filters items to those published within the last N days.
    """
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[gnews] XML parse error for query '{query}': {e}")
        return []

    # Google News RSS structure: <rss><channel><item>...</item></channel></rss>
    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        try:
            title_el = item.find("title")
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")
            desc_el = item.find("description")
            source_el = item.find("source")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
            description = (desc_el.text or "").strip() if desc_el is not None else ""
            source_name = (source_el.text or "").strip() if source_el is not None else ""

            if not title:
                continue

            # Parse and filter by date
            parsed_ts = _parse_pubdate(pubdate)
            try:
                item_dt = datetime.strptime(parsed_ts[:19], "%Y-%m-%dT%H:%M:%S")
                item_dt = item_dt.replace(tzinfo=timezone.utc)
                if item_dt < cutoff:
                    continue
            except ValueError:
                pass  # If we can't parse the date, include the item anyway

            items.append({
                "title": title,
                "link": link,
                "pubdate": pubdate,
                "pubdate_parsed": parsed_ts,
                "description": description,
                "source_name": source_name,
                "query": query,
            })
        except Exception:
            continue

    return items


# ══════════════════════════════════════════════════════════════════════════════
# Signal Construction
# ══════════════════════════════════════════════════════════════════════════════

def _build_external_id(title: str, pubdate: str) -> str:
    """Generate a stable external_id from title + pubdate."""
    raw = (title + pubdate).encode("utf-8")
    return "gnews:" + hashlib.sha256(raw).hexdigest()[:16]


def build_signal(item: dict, is_crime: bool) -> dict:
    """Convert a parsed RSS item into a FORGE signal dict."""
    title_raw = item["title"]
    description_raw = item.get("description", "")
    source_name = item.get("source_name", "")
    query = item.get("query", "")

    # Stream routing
    stream = "CRIME_INTEL" if is_crime else "GLOBAL"

    # Clean content
    title_clean = sanitize_text(_strip_html(title_raw))[:300]
    desc_clean = _strip_html(description_raw)
    content_raw = f"{title_clean}\n\n{desc_clean}".strip() if desc_clean else title_clean
    content = sanitize_text(content_raw)[:CONTENT_CAP]

    # External ID
    external_id = _build_external_id(title_raw, item.get("pubdate", ""))

    # Metadata
    metadata = {
        "query": query,
        "source_outlet": source_name,
        "link": item.get("link", ""),
        "stream_reason": "crime_context_match" if is_crime else "general_media",
        "pubdate_raw": item.get("pubdate", ""),
    }

    return {
        "signal_id": str(uuid.uuid4()),
        "source": SOURCE_ID,
        "external_id": external_id,
        "title": title_clean,
        "content": content,
        "lat": None,
        "lng": None,
        "timestamp": item.get("pubdate_parsed", datetime.now(timezone.utc).isoformat()),
        "status": "raw",
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "stream": stream,
        "relevance_score": 1.5,
        "source_type": "live",
        "is_priority": 1 if is_crime else 0,
        "gravity_score": 0.30,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Database Persistence
# ══════════════════════════════════════════════════════════════════════════════

INSERT_SQL = """
    INSERT OR IGNORE INTO signals (
        signal_id, source, external_id, title, content,
        lat, lng, timestamp, status, metadata_json,
        stream, relevance_score, source_type, is_priority,
        gravity_score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def persist_signals(signals: list[dict], dry_run: bool = False) -> int:
    """Write signals to the database. Returns count of new insertions."""
    if dry_run:
        print(f"[gnews] DRY RUN — {len(signals)} signals would be inserted")
        for s in signals[:5]:
            print(f"  {s['external_id']:28s} | {s['stream']:12s} | {s['title'][:60]}")
        if len(signals) > 5:
            print(f"  ... and {len(signals) - 5} more")
        return 0

    if not signals:
        return 0

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    inserted = 0
    errors = 0
    try:
        for sig in signals:
            try:
                conn.execute(INSERT_SQL, (
                    sig["signal_id"],
                    sig["source"],
                    sig["external_id"],
                    sig["title"],
                    sig["content"],
                    sig["lat"],
                    sig["lng"],
                    sig["timestamp"],
                    sig["status"],
                    sig["metadata_json"],
                    sig["stream"],
                    sig["relevance_score"],
                    sig["source_type"],
                    sig["is_priority"],
                    sig["gravity_score"],
                ))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"[gnews] Insert error for {sig.get('external_id', '?')}: {e}")

        conn.commit()
    finally:
        conn.close()

    if errors:
        print(f"[gnews] {errors} insert errors encountered")
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def run_collector(days: int = DEFAULT_DAYS, max_queries: int = DEFAULT_MAX_QUERIES,
                  keywords: list[str] | None = None,
                  dry_run: bool = False) -> dict:
    """
    Main collection function.
    Returns a summary dict for pipeline telemetry.
    """
    start_time = time.time()
    print(f"[gnews] Generalized News Monitor starting")
    print(f"[gnews] DB: {DB_PATH}")
    print(f"[gnews] Days: {days} | Max queries: {max_queries} | Dry run: {dry_run}")

    # Resolve query list
    if keywords:
        queries = keywords[:max_queries]
        print(f"[gnews] Using {len(queries)} CLI keyword(s): {queries}")
    else:
        queries = _load_actor_queries(max_queries)
        print(f"[gnews] Loaded {len(queries)} actor queries from database")

    if not queries:
        print("[gnews] No queries to process — exiting")
        duration = time.time() - start_time
        log_run(SOURCE_ID, "success", 0, 0, duration)
        return {"status": "done", "fetched": 0, "inserted": 0, "duration_s": duration}

    # Collect from Google News RSS
    all_signals: list[dict] = []
    total_articles = 0

    for i, query in enumerate(queries):
        print(f"[gnews] [{i+1}/{len(queries)}] Searching for: {query}")

        url = _build_rss_url(query)
        xml_bytes = _fetch_rss(url)

        if xml_bytes is None:
            print(f"[gnews]   Fetch failed — skipping")
            if i < len(queries) - 1:
                time.sleep(REQUEST_DELAY)
            continue

        items = _parse_rss_items(xml_bytes, query, days)
        print(f"[gnews]   Found {len(items)} articles")
        total_articles += len(items)

        # Determine crime context for the entire query
        is_crime = _is_crime_context(query)

        # Build signals from items
        for item in items:
            try:
                sig = build_signal(item, is_crime)
                all_signals.append(sig)
            except Exception as e:
                print(f"[gnews]   Signal build error: {e}")

        # Courteous delay between fetches
        if i < len(queries) - 1:
            time.sleep(REQUEST_DELAY)

    print(f"[gnews] Total articles found: {total_articles}")
    print(f"[gnews] Signals built: {len(all_signals)}")

    # Stream distribution
    stream_counts: dict[str, int] = {}
    for s in all_signals:
        stream_counts[s["stream"]] = stream_counts.get(s["stream"], 0) + 1
    for stream, count in sorted(stream_counts.items()):
        print(f"[gnews]   {stream}: {count}")

    # Persist
    inserted = persist_signals(all_signals, dry_run=dry_run)
    duration = round(time.time() - start_time, 2)

    if not dry_run:
        print(f"[gnews] Inserted {inserted} new signals ({len(all_signals) - inserted} duplicates)")
    print(f"[gnews] Done in {duration}s")

    log_run(SOURCE_ID, "success", total_articles, inserted, duration)

    return {
        "status": "done",
        "queries": len(queries),
        "fetched": total_articles,
        "built": len(all_signals),
        "inserted": inserted,
        "duration_s": duration,
        "stream_counts": stream_counts,
    }


# ── Mega-runner adapter ──────────────────────────────────────────────────────

async def async_main():
    """Entry point for mega_ingest.py async collector dispatch."""
    run_collector()


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Generalized News Monitor (Google News RSS)"
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Fetch articles from the last N days (default: {DEFAULT_DAYS})")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES,
                        help=f"Maximum number of actor queries to search (default: {DEFAULT_MAX_QUERIES})")
    parser.add_argument("--keywords", type=str, default="",
                        help="Comma-separated keywords to search (overrides actor DB query)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse without writing to database")

    args = parser.parse_args()

    kw_list = None
    if args.keywords:
        kw_list = [k.strip() for k in args.keywords.split(",") if k.strip()]

    run_collector(
        days=args.days,
        max_queries=args.max_queries,
        keywords=kw_list,
        dry_run=args.dry_run,
    )
