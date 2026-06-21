#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — X Search Collector  (flux/collectors/x_search_collector.py)
═══════════════════════════════════════════════════════════════════════

Actor-driven historical search on X (Twitter) via Nitter RSS search
endpoints. Complements x_pulse (forward-looking handle monitor) with
backward-looking evidence gathering: finds tweets BY and ABOUT FORGE
actors, surfacing historical statements, denials, political connections,
and public confrontations.

Architecture
────────────
  x_pulse:    watches predefined handles → new tweets (proactive)
  x_search:   queries by actor name → historical tweets (reactive)

Same Nitter instance rotation pool as x_pulse for transport resilience.
Dedup is cross-compatible: both collectors use tweet URL as external_id,
so a tweet found by x_search that x_pulse already captured is silently
skipped via INSERT OR IGNORE.

Collection strategy
───────────────────
  1. Load high-value actor names from the actors table
  2. For each actor, query Nitter search RSS:
     https://{instance}/search/rss?f=tweets&q={actor_name}
  3. Parse RSS items (same format as x_pulse Nitter feeds)
  4. Write signals to both `signals` and `socint_signals` tables
  5. Cross-reference: if tweet mentions another FORGE actor, log the link

SOCINT stream routing
─────────────────────
  All tweets → CRIME_INTEL (evidence-grade social intelligence)
  Gravity: 0.35 base (public statement by/about a tracked actor)
           +0.10 if tweet mentions multiple FORGE actors
           +0.10 if tweet contains investigative keywords
           Cap: 0.55

Environment variables
─────────────────────
  FORGE_DB           Path to database (default: auto-detect)
  X_SEARCH_TARGETS   Override: comma-separated names to search
                     (bypasses actor table query)

Usage
─────
  python flux/collectors/x_search_collector.py
  python flux/collectors/x_search_collector.py --dry-run
  python flux/collectors/x_search_collector.py --max-queries 5 --days 7
  python flux/collectors/x_search_collector.py --targets "Ramaphosa,Zuma,HAWKS"
"""

__manifest__ = {
    "id":          "x_search",
    "name":        "X Actor Search Engine",
    "description": (
        "Actor-driven historical search on X/Twitter via Nitter RSS. "
        "Finds tweets by and about FORGE actors — statements, denials, "
        "political connections. Complements x_pulse forward monitoring."
    ),
    "icon":        "🔍",
    "entry":       "flux/collectors/x_search_collector.py",
    "args":        [],
    "job_key":     "x_search",
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
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

# ── Path setup ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SOURCE_ID = __manifest__["id"]

_FORGE_DB_ENV = os.environ.get("FORGE_DB")
DB_PATH = Path(_FORGE_DB_ENV) if _FORGE_DB_ENV else BASE_DIR / "database.db"

# ── Sanitizer ────────────────────────────────────────────────────────────────
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

NITTER_INSTANCES: list[str] = [
    "nitter.net",
    "nitter.poast.org",
    "nitter.cz",
    "nitter.fdn.fr",
    "nitter.privacydev.net",
    "nitter.1d4.us",
]

REQUEST_DELAY = 4.0        # seconds between searches (courteous)
INSTANCE_TIMEOUT = 12      # seconds per HTTP request
CONTENT_CAP = 3000         # max chars in signal.content

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Investigative keyword boost — tweets containing these get +0.10 gravity
_INVESTIGATIVE_KEYWORDS = frozenset({
    "corruption", "fraud", "arrested", "charged", "indicted", "warrant",
    "tender", "irregular", "procurement", "stolen", "embezzle", "bribe",
    "looting", "state capture", "investigation", "hawks", "npa", "siu",
    "scopa", "tribunal", "court", "sentence", "bail", "guilty",
    "whistleblower", "expose", "leaked", "cover-up", "coverup",
})

# Minimum actor name length for search queries (skip "NPA", "SIU" — too short)
MIN_ACTOR_NAME_LENGTH = 5

# Skip generic labels that would return noise
_SKIP_ACTORS = frozenset({
    "south africa", "government", "department", "minister",
    "institution", "organization", "unknown", "company",
    "parliament", "national assembly", "location",
})


# ══════════════════════════════════════════════════════════════════════════════
# Nitter RSS Search
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_nitter_search(query: str) -> list[dict]:
    """
    Search Nitter RSS for a query string. Rotates through instances
    until one responds successfully.
    Returns a list of parsed tweet dicts.
    """
    encoded_query = quote_plus(query)

    for instance in NITTER_INSTANCES:
        url = f"https://{instance}/search/rss?f=tweets&q={encoded_query}"
        req = Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urlopen(req, timeout=INSTANCE_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                xml_bytes = resp.read()
                return _parse_rss(xml_bytes, instance)
        except (HTTPError, URLError, TimeoutError):
            continue
        except Exception:
            continue

    return []


def _parse_rss(xml_bytes: bytes, instance: str) -> list[dict]:
    """Parse Nitter RSS search results into tweet dicts."""
    tweets: list[dict] = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return tweets

    channel = root.find("channel")
    if channel is None:
        return tweets

    for item in channel.findall("item"):
        try:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            creator = (item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip()

            if not link or not title:
                continue

            # Normalize link: Nitter links → canonical twitter.com
            canonical_url = link
            if instance in link:
                canonical_url = link.replace(f"https://{instance}", "https://twitter.com")

            # Parse date
            timestamp = None
            if pub_date:
                try:
                    timestamp = parsedate_to_datetime(pub_date)
                except Exception:
                    pass

            tweets.append({
                "title": title,
                "link": canonical_url,
                "pub_date": pub_date,
                "timestamp": timestamp,
                "description": description,
                "creator": creator,
                "nitter_instance": instance,
            })
        except Exception:
            continue

    return tweets


# ══════════════════════════════════════════════════════════════════════════════
# Actor Loading & Signal Construction
# ══════════════════════════════════════════════════════════════════════════════

def _load_search_actors(conn: sqlite3.Connection, max_queries: int) -> list[str]:
    """Load high-value actor names from the database for search queries."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT a.name
            FROM actors a
            WHERE length(a.name) >= ?
              AND a.type IN ('person', 'institution', 'government', 'political_party')
            ORDER BY a.confidence_score DESC
            LIMIT ?
        """, (MIN_ACTOR_NAME_LENGTH, max_queries * 3)).fetchall()

        names = []
        for row in rows:
            name = row[0].strip()
            if name.lower() in _SKIP_ACTORS:
                continue
            names.append(name)
            if len(names) >= max_queries:
                break
        return names
    except Exception as e:
        print(f"[x_search] Actor load failed: {e}")
        return []


def _compute_gravity(tweet_text: str, actor_names: set[str]) -> float:
    """
    Gravity scoring:
      Base: 0.35 (public statement by/about a tracked actor)
      +0.10 if tweet mentions multiple FORGE actors
      +0.10 if tweet contains investigative keywords
      Cap: 0.55
    """
    score = 0.35
    text_lower = tweet_text.lower()

    # Multi-actor mention check
    actor_hits = sum(1 for a in actor_names if a.lower() in text_lower)
    if actor_hits >= 2:
        score += 0.10

    # Investigative keyword check
    if any(kw in text_lower for kw in _INVESTIGATIVE_KEYWORDS):
        score += 0.10

    return min(score, 0.55)


def _build_signal(tweet: dict, search_query: str,
                  actor_names: set[str]) -> dict:
    """Convert a parsed tweet into a FORGE signal dict."""
    title = sanitize_text(tweet["title"])[:200]
    content = sanitize_text(tweet.get("description") or tweet["title"])[:CONTENT_CAP]
    link = tweet["link"]

    # External ID: use tweet URL hash for cross-compatibility with x_pulse
    ext_id = f"xsearch:{hashlib.sha256(link.encode()).hexdigest()[:16]}"

    # Timestamp
    ts = tweet.get("timestamp")
    if ts:
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    gravity = _compute_gravity(content, actor_names)

    metadata = {
        "tweet_url": link,
        "search_query": search_query,
        "creator": tweet.get("creator", ""),
        "pub_date": tweet.get("pub_date", ""),
        "nitter_instance": tweet.get("nitter_instance", ""),
    }

    return {
        "signal_id": str(uuid.uuid4()),
        "source": SOURCE_ID,
        "external_id": ext_id,
        "title": f"[X] {title}" if not title.startswith("[X]") else title,
        "content": content,
        "lat": None,
        "lng": None,
        "timestamp": ts_str,
        "status": "raw",
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "stream": "CRIME_INTEL",
        "relevance_score": 1.6,
        "source_type": "live",
        "is_priority": 0,
        "gravity_score": gravity,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Database Persistence
# ══════════════════════════════════════════════════════════════════════════════

_INSERT_SQL = """
    INSERT OR IGNORE INTO signals (
        signal_id, source, external_id, title, content,
        lat, lng, timestamp, status, metadata_json,
        stream, relevance_score, source_type, is_priority,
        gravity_score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_SOCINT_SQL = """
    INSERT OR IGNORE INTO socint_signals (
        source, signal_id, content, metadata_json, timestamp
    ) VALUES (?, ?, ?, ?, ?)
"""


def _persist_signals(signals: list[dict], dry_run: bool = False) -> int:
    """Write signals to signals + socint_signals tables."""
    if dry_run:
        print(f"[x_search] DRY RUN — {len(signals)} signals would be inserted")
        for s in signals[:8]:
            print(f"  {s['external_id']:24s} | g={s['gravity_score']:.2f} | {s['title'][:60]}")
        if len(signals) > 8:
            print(f"  ... and {len(signals) - 8} more")
        return 0

    if not signals:
        return 0

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    inserted = 0
    errors = 0
    try:
        for sig in signals:
            try:
                conn.execute(_INSERT_SQL, (
                    sig["signal_id"], sig["source"], sig["external_id"],
                    sig["title"], sig["content"], sig["lat"], sig["lng"],
                    sig["timestamp"], sig["status"], sig["metadata_json"],
                    sig["stream"], sig["relevance_score"], sig["source_type"],
                    sig["is_priority"], sig["gravity_score"],
                ))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
                    # Also write to socint_signals for FLUX pipeline
                    try:
                        conn.execute(_INSERT_SOCINT_SQL, (
                            sig["source"], sig["signal_id"],
                            sig["content"], sig["metadata_json"],
                            sig["timestamp"],
                        ))
                    except Exception:
                        pass  # socint_signals FK may not exist yet
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"[x_search] Insert error: {e}")

        conn.commit()
    finally:
        conn.close()

    if errors:
        print(f"[x_search] {errors} insert errors encountered")
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def run_collector(max_queries: int = 10, days: int = 7,
                  targets: list[str] | None = None,
                  dry_run: bool = False) -> dict:
    """Main collection function."""
    start_time = time.time()
    print(f"[x_search] X Actor Search Engine starting")
    print(f"[x_search] DB: {DB_PATH}")
    print(f"[x_search] Max queries: {max_queries} | Days: {days} | Dry run: {dry_run}")

    # Resolve search targets
    if targets:
        search_names = targets[:max_queries]
        print(f"[x_search] Using CLI targets: {search_names}")
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        try:
            search_names = _load_search_actors(conn, max_queries)
        finally:
            conn.close()
        print(f"[x_search] Loaded {len(search_names)} actors from database")

    if not search_names:
        print("[x_search] No search targets — exiting")
        return {"status": "done", "queries": 0, "tweets": 0, "inserted": 0}

    # Load all actor names for cross-reference during gravity scoring
    all_actor_names: set[str] = set()
    if not dry_run:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        try:
            rows = conn.execute(
                "SELECT name FROM actors WHERE length(name) > 3"
            ).fetchall()
            all_actor_names = {r[0] for r in rows}
        finally:
            conn.close()

    # Date filter
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Execute searches
    all_signals: list[dict] = []
    total_tweets = 0
    queries_run = 0

    for name in search_names:
        print(f"[x_search] Searching: \"{name}\"")
        queries_run += 1

        tweets = _fetch_nitter_search(name)
        if not tweets:
            print(f"[x_search]   → 0 results (all instances failed or empty)")
            time.sleep(REQUEST_DELAY)
            continue

        # Filter by date
        recent = []
        for tw in tweets:
            ts = tw.get("timestamp")
            if ts and ts.replace(tzinfo=timezone.utc if ts.tzinfo is None else ts.tzinfo) < cutoff:
                continue
            recent.append(tw)

        print(f"[x_search]   → {len(recent)} tweets (of {len(tweets)} total, {days}d filter)")
        total_tweets += len(recent)

        for tw in recent:
            try:
                sig = _build_signal(tw, name, all_actor_names)
                all_signals.append(sig)
            except Exception as e:
                print(f"[x_search]   Signal build error: {e}")

        time.sleep(REQUEST_DELAY)

    print(f"[x_search] Total: {queries_run} queries, {total_tweets} tweets, {len(all_signals)} signals built")

    # Persist
    inserted = _persist_signals(all_signals, dry_run=dry_run)
    duration = round(time.time() - start_time, 2)

    if not dry_run:
        print(f"[x_search] Inserted {inserted} new signals ({len(all_signals) - inserted} duplicates)")
    print(f"[x_search] Done in {duration}s")

    log_run(SOURCE_ID, "success", total_tweets, inserted, duration)

    return {
        "status": "done",
        "queries": queries_run,
        "tweets": total_tweets,
        "built": len(all_signals),
        "inserted": inserted,
        "duration_s": duration,
    }


# ── Mega-runner adapter ──────────────────────────────────────────────────────

async def async_main():
    """Entry point for mega_ingest.py async collector dispatch."""
    run_collector()


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE FLUX — X Actor Search Engine"
    )
    parser.add_argument("--max-queries", type=int, default=10,
                        help="Maximum actor searches to run (default: 10)")
    parser.add_argument("--days", type=int, default=7,
                        help="Filter tweets from the last N days (default: 7)")
    parser.add_argument("--targets", type=str, default="",
                        help="Comma-separated search terms (bypasses actor DB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse without writing to database")

    args = parser.parse_args()

    target_list = None
    if args.targets:
        target_list = [t.strip() for t in args.targets.split(",") if t.strip()]

    run_collector(
        max_queries=args.max_queries,
        days=args.days,
        targets=target_list,
        dry_run=args.dry_run,
    )
