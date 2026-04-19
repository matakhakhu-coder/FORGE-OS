# -*- coding: utf-8 -*-
"""
FORGE -- Dork Collector  (forage/collectors/dork_collector.py)
===============================================================
Actor-driven document hunting for high-gravity signals.

Triggers on actors linked to signals with gravity_score > 0.6.
For each qualifying actor, constructs targeted Google News RSS
queries to surface:
  - Government tenders and procurement documents
  - NPA / Hawks / SIU proceedings and arrests
  - Corruption and fraud coverage in South Africa

Uses Google News RSS (no API key required, no Selenium) -- same
pattern as civic_intel_collector. Results inserted as source='dork'.

Deduplication: external_id = "dork:{sha1(url)[:16]}"
Rate limit: 1 request per second, max 20 actors per run.

Author: FORGE Phase 42
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = BASE_DIR / "database.db"

GRAVITY_THRESHOLD  = 0.6   # Only hunt for actors tied to high-gravity signals
MAX_ACTORS_PER_RUN = 20    # Rate-limit: cap actors processed per run
REQUEST_DELAY      = 1.2   # Seconds between HTTP requests

# -- Query templates -- three per actor -----------------------------------
# Template 0: PDF document hunt on SA government portals
# Template 1: tender / procurement coverage
# Template 2: criminal / prosecution targeting NPA, Hawks, SIU
_QUERY_TEMPLATES = [
    '"{actor}" filetype:pdf site:gov.za tender OR procurement OR contract',
    '"{actor}" tender OR procurement OR contract "South Africa"',
    '"{actor}" NPA OR Hawks OR SIU OR corruption OR fraud "South Africa"',
]

# -- Optional dependencies ------------------------------------------------
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

try:
    import requests as _requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

_SESSION = None


def _get_session():
    global _SESSION
    if _SESSION is None and HAS_REQUESTS:
        _SESSION = _requests.Session()
        _SESSION.verify = False
        _SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; FORGE-OSINT/1.0; "
                "+https://github.com/matakhakhu-coder/FORGE)"
            ),
        })
    return _SESSION


def _safe_print(msg: str) -> None:
    """Print with UTF-8 fallback for Windows cp1252 terminals."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"))


def _fetch_google_news(query: str, timeout: int = 15) -> list:
    """Fetch Google News RSS for a query string. Returns feedparser entries."""
    if not HAS_FEEDPARSER or not HAS_REQUESTS:
        return []
    sess = _get_session()
    if not sess:
        return []
    encoded = _requests.utils.quote(query)
    url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl=en-ZA&gl=ZA&ceid=ZA:en"
    )
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return feed.entries or []
    except Exception as exc:
        _safe_print(f"  [dork] fetch failed for {query[:60]!r}: {exc}")
        return []


def _entry_to_signal(entry: dict, actor_name: str, query_idx: int) -> Optional[dict]:
    """Convert a feedparser entry to a FORGE dork signal dict."""
    title = (entry.get("title") or "").strip()
    if not title:
        return None

    content = (entry.get("summary") or "").strip()
    content = re.sub(r"<[^>]+>", " ", content).strip()
    content = re.sub(r"\s+", " ", content)[:2000]

    link   = entry.get("link") or entry.get("id") or title
    ext_id = "dork:{}".format(
        hashlib.sha1(link.encode("utf-8", errors="replace")).hexdigest()[:16]
    )

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

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          "dork",
        "external_id":     ext_id,
        "title":           title[:400],
        "content":         content,
        "lat":             -25.7479,   # Default: Pretoria/Gauteng
        "lng":             28.2293,
        "timestamp":       published,
        "status":          "raw",
        "stream":          "CRIME_INTEL",
        "relevance_score": 1.4,
        "is_priority":     1,
        "metadata_json":   json.dumps({
            "dork_actor":     actor_name,
            "dork_query_idx": query_idx,
            "source_url":     link,
        }),
    }


def _get_qualifying_actors(conn: sqlite3.Connection) -> list[dict]:
    """
    Return the Top actors by Global Influence Score from actor_network_metrics.
    Falls back to gravity-based selection if actor_network_metrics is empty.
    Capped at MAX_ACTORS_PER_RUN.
    """
    try:
        # Primary: top actors by graph engine influence score
        rows = conn.execute("""
            SELECT a.actor_id, a.name, m.influence_score
            FROM actors a
            JOIN actor_network_metrics m ON m.actor_id = a.actor_id
            WHERE a.name IS NOT NULL
              AND length(trim(a.name)) > 3
              AND m.influence_score > 0
            ORDER BY m.influence_score DESC
            LIMIT ?
        """, (MAX_ACTORS_PER_RUN,)).fetchall()

        if rows:
            return [{"actor_id": r["actor_id"], "name": r["name"],
                     "influence_score": r["influence_score"]} for r in rows]

        # Fallback: gravity-based selection if metrics table is empty
        rows = conn.execute("""
            SELECT DISTINCT a.actor_id, a.name, MAX(s.gravity_score) AS influence_score
            FROM actors a
            JOIN signal_actors sa ON sa.actor_id = a.actor_id
            JOIN signals s        ON s.signal_id  = sa.signal_id
            WHERE s.gravity_score > ?
              AND a.name IS NOT NULL
              AND length(trim(a.name)) > 3
            GROUP BY a.actor_id, a.name
            ORDER BY influence_score DESC
            LIMIT ?
        """, (GRAVITY_THRESHOLD, MAX_ACTORS_PER_RUN)).fetchall()
        return [{"actor_id": r["actor_id"], "name": r["name"],
                 "influence_score": r["influence_score"]} for r in rows]

    except Exception as exc:
        _safe_print(f"  [dork] actor query failed: {exc}")
        return []


class DorkCollector:
    """
    Class-based wrapper for the dork collector, compatible with
    mega_ingest.py's _run_engine() pattern.

    Usage:
        DorkCollector(db_path=DB_PATH).run()
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def run(self) -> dict:
        return _run_collection(self.db_path)


def _run_collection(db_path: Path = DB_PATH) -> dict:
    """
    Main collection loop.

    1. Identify actors linked to high-gravity signals (> 0.6)
    2. For each actor, fire _QUERY_TEMPLATES against Google News RSS
    3. Insert novel results as source='dork' signals
    """
    if not HAS_FEEDPARSER:
        _safe_print("[dork] feedparser required -- pip install feedparser")
        return {"status": "error", "error": "feedparser missing"}
    if not HAS_REQUESTS:
        _safe_print("[dork] requests required -- pip install requests")
        return {"status": "error", "error": "requests missing"}

    if not db_path.exists():
        _safe_print(f"[dork] DB not found: {db_path}")
        return {"status": "error", "error": "db not found"}

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    actors = _get_qualifying_actors(conn)
    if not actors:
        conn.close()
        _safe_print("[dork] No qualifying actors found (gravity_score threshold not met)")
        return {"status": "skipped", "actors_checked": 0, "inserted": 0}

    _safe_print(f"[dork] {len(actors)} qualifying actors -- hunting documents...")

    total_inserted = 0
    total_skipped  = 0
    total_errors   = 0

    for actor in actors:
        name = actor["name"]
        _safe_print(f"  [dork] Actor: {name!r} (influence={actor['influence_score']:.4f})")

        for idx, template in enumerate(_QUERY_TEMPLATES):
            query = template.format(actor=name)
            entries = _fetch_google_news(query)
            time.sleep(REQUEST_DELAY)

            for entry in entries:
                sig = _entry_to_signal(entry, name, idx)
                if sig is None:
                    continue
                try:
                    conn.execute("""
                        INSERT INTO signals
                            (signal_id, source, external_id, title, content,
                             lat, lng, timestamp, status,
                             stream, relevance_score, is_priority,
                             metadata_json, source_type)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'live')
                    """, (
                        sig["signal_id"], sig["source"], sig["external_id"],
                        sig["title"],     sig["content"],
                        sig["lat"],       sig["lng"],
                        sig["timestamp"], sig["status"],
                        sig["stream"],    sig["relevance_score"],
                        sig["is_priority"], sig["metadata_json"],
                    ))
                    total_inserted += 1
                except sqlite3.IntegrityError:
                    total_skipped += 1
                except Exception as exc:
                    _safe_print(f"    [dork] insert error: {exc}")
                    total_errors += 1

        conn.commit()

    conn.close()

    summary = {
        "collector":      "dork",
        "actors_hunted":  len(actors),
        "inserted":       total_inserted,
        "skipped":        total_skipped,
        "errors":         total_errors,
        "status":         "success" if total_errors == 0 else "partial",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }
    _safe_print(
        f"\n[dork] Done -- {total_inserted} new signals "
        f"| {total_skipped} known | {len(actors)} actors hunted"
    )
    return summary


# Keep the top-level run() alias for backwards compatibility
def run(db_path: Path = DB_PATH) -> dict:
    return _run_collection(db_path)


# -- Mega runner adapter --------------------------------------------------

async def async_main(**kwargs):
    try:
        result = _run_collection()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        _safe_print(f"[ERROR] async_main failed in dork_collector.py: {e}")


if __name__ == "__main__":
    import sys
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    print(json.dumps(run(db_path=db), indent=2))
