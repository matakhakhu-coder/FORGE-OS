#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — PMG Parliamentary Oversight Collector
══════════════════════════════════════════════

Ingests committee meeting records from the Parliamentary Monitoring Group
(PMG) public API. Targets high-value oversight committees whose proceedings
directly feed SA OSINT analysis: SCOPA, Justice, Police, Intelligence,
Public Enterprises, Home Affairs, Finance, CoGTA, Defence, Trade, Energy.

Data source: api.pmg.org.za (JSON REST, public, no auth required)
Rate limit: 1 request per 2 seconds (courteous — no documented limit)
Volume: ~34,669 meetings total; ~50 new per week

Content strategy:
  signal.content = first 3,000 chars of transcript (captures chair opening,
                   agenda, and first substantive discussion — sufficient for
                   NER actor extraction and gravity scoring)
  metadata_json  = structured metadata only (committee, URL, date, chair,
                   full_text_url for on-demand deep fetch)

Stream routing:
  CRIME_INTEL     — SCOPA (42), Justice (38), Police (86), Intelligence (84)
  INFRASTRUCTURE  — Public Enterprises (73), Electricity/Energy (3), CoGTA (65)
  GLOBAL          — Finance (24), Home Affairs (110), Defence (87), Trade (98)

Gravity scoring:
  Base: 0.25 (official institutional record)
  +0.10 if security/justice/oversight portfolio (CRIME_INTEL stream)
  +0.15 if a FORGE actor name appears in the meeting text
  Cap: 0.55

Usage:
  python forage/collectors/pmg_collector.py
  python forage/collectors/pmg_collector.py --days 7 --dry-run
  python forage/collectors/pmg_collector.py --max-pages 3 --committees 42,38,86
"""

__manifest__ = {
    "id":          "pmg_parliamentary",
    "name":        "Parliamentary Oversight Tracker",
    "description": "Ingests PMG committee transcripts, briefing summaries, and institutional oversight records.",
    "icon":        "🏛",
    "entry":       "forage/collectors/pmg_collector.py",
    "args":        [],
    "job_key":     "pmg_parliamentary",
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
from datetime import datetime, timedelta, timezone
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

# ── HTML stripping ───────────────────────────────────────────────────────────
_HTML_TAG_RE = re.compile(r"<[^>]{0,2000}>", re.DOTALL)
_WHITESPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

API_BASE = "https://api.pmg.org.za"
REQUEST_DELAY = 2.0  # seconds between API calls (courteous)
CONTENT_CAP = 3000   # chars stored in signal.content

# Priority committee matrix
PRIORITY_COMMITTEES: dict[int, dict] = {
    # CRIME_INTEL tier
    42:  {"name": "SCOPA",                    "stream": "CRIME_INTEL"},
    38:  {"name": "Justice",                  "stream": "CRIME_INTEL"},
    86:  {"name": "Police",                   "stream": "CRIME_INTEL"},
    84:  {"name": "Intelligence",             "stream": "CRIME_INTEL"},
    # INFRASTRUCTURE tier
    73:  {"name": "Public Enterprises",       "stream": "INFRASTRUCTURE"},
    3:   {"name": "Electricity and Energy",   "stream": "INFRASTRUCTURE"},
    65:  {"name": "CoGTA",                    "stream": "INFRASTRUCTURE"},
    # GLOBAL tier
    24:  {"name": "Finance",                  "stream": "GLOBAL"},
    110: {"name": "Home Affairs",             "stream": "GLOBAL"},
    87:  {"name": "Defence",                  "stream": "GLOBAL"},
    98:  {"name": "Trade",                    "stream": "GLOBAL"},
}

CRIME_INTEL_IDS = {42, 38, 86, 84}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════════════════════
# API Fetching
# ══════════════════════════════════════════════════════════════════════════════

def _api_get(endpoint: str, params: dict | None = None) -> dict | None:
    url = f"{API_BASE}{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 403:
            print(f"[pmg] API returned 403 for {url} — possible rate limit or geo-block")
        elif e.code == 404:
            print(f"[pmg] API returned 404 for {url}")
        else:
            print(f"[pmg] HTTP {e.code} for {url}: {e.reason}")
        return None
    except URLError as e:
        print(f"[pmg] Network error: {e.reason}")
        return None
    except Exception as e:
        print(f"[pmg] Unexpected error fetching {url}: {e}")
        return None


def fetch_meetings(committee_ids: list[int], max_pages: int = 5,
                   since_date: str | None = None) -> list[dict]:
    """
    Fetch committee meetings from the PMG API.
    Returns raw meeting dicts from the API response.
    """
    meetings: list[dict] = []

    for page in range(max_pages):
        params = {"page": str(page)}
        data = _api_get("/committee-meeting/", params)
        if not data:
            break

        results = data.get("results", [])
        if not results:
            break

        for meeting in results:
            cid = meeting.get("committee_id")
            if cid not in committee_ids:
                continue

            meeting_date = (meeting.get("date") or "")[:10]
            if since_date and meeting_date < since_date:
                continue

            meetings.append(meeting)

        # Check if there are more pages
        if not data.get("next"):
            break

        time.sleep(REQUEST_DELAY)
        print(f"[pmg] Page {page + 1} fetched — {len(meetings)} relevant meetings so far")

    return meetings


# ══════════════════════════════════════════════════════════════════════════════
# Signal Construction
# ══════════════════════════════════════════════════════════════════════════════

def _compute_gravity(committee_id: int, content: str, actor_names: set[str]) -> float:
    """
    Gravity scoring per expansion plan:
      Base: 0.25 (official institutional record)
      +0.10 if security/justice/oversight portfolio
      +0.15 if a FORGE actor name appears in the text
      Cap: 0.55
    """
    score = 0.25

    if committee_id in CRIME_INTEL_IDS:
        score += 0.10

    if actor_names and content:
        content_lower = content.lower()
        for actor in actor_names:
            if actor.lower() in content_lower:
                score += 0.15
                break

    return min(score, 0.55)


def _get_actor_names(conn: sqlite3.Connection) -> set[str]:
    """Load all actor names from the database for cross-referencing."""
    try:
        rows = conn.execute(
            "SELECT name FROM actors WHERE length(name) > 3"
        ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def build_signal(meeting: dict, actor_names: set[str]) -> dict:
    """Convert a PMG API meeting record into a FORGE signal dict."""
    meeting_id = meeting.get("id")
    committee = meeting.get("committee", {})
    committee_id = meeting.get("committee_id", 0)
    committee_name = committee.get("name", "") if isinstance(committee, dict) else ""
    title = meeting.get("title", "")
    date_str = (meeting.get("date") or "")[:10]
    body_raw = meeting.get("body") or ""
    summary_raw = meeting.get("summary") or ""

    # Determine stream from committee
    config = PRIORITY_COMMITTEES.get(committee_id, {})
    stream = config.get("stream", "GLOBAL")

    # Strip HTML from body and summary
    body_text = _strip_html(body_raw)
    summary_text = _strip_html(summary_raw)

    # Build content: prefer body (full transcript), fall back to summary
    raw_content = body_text if body_text else summary_text
    content = sanitize_text(raw_content[:CONTENT_CAP]) if raw_content else ""

    # Build signal title
    signal_title = f"[{committee_name}] {title}" if committee_name else title
    signal_title = sanitize_text(signal_title)[:200]

    # Compute gravity
    gravity = _compute_gravity(committee_id, raw_content[:5000], actor_names)

    # Structured metadata (no full transcript — use full_text_url for on-demand)
    metadata = {
        "meeting_id": meeting_id,
        "committee_id": committee_id,
        "committee_name": committee_name,
        "meeting_date": date_str,
        "chairperson": meeting.get("chairperson"),
        "meeting_url": f"https://pmg.org.za/committee-meeting/{meeting_id}/",
        "full_text_url": f"{API_BASE}/committee-meeting/{meeting_id}/",
        "attendance_url": meeting.get("attendance_url"),
        "has_body": bool(body_raw),
        "has_summary": bool(summary_raw),
        "body_length": len(body_text),
        "stream_reason": config.get("name", "unclassified"),
    }

    # Relevance score: higher for CRIME_INTEL committees
    relevance = 1.8 if stream == "CRIME_INTEL" else 1.4

    return {
        "signal_id": str(uuid.uuid4()),
        "source": SOURCE_ID,
        "external_id": f"pmg:{meeting_id}",
        "title": signal_title,
        "content": content,
        "lat": -33.9249,   # Parliament, Cape Town
        "lng": 18.4241,
        "timestamp": f"{date_str}T00:00:00Z" if date_str else datetime.now(timezone.utc).isoformat(),
        "status": "raw",
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "stream": stream,
        "relevance_score": relevance,
        "source_type": "live",
        "is_priority": 1 if stream == "CRIME_INTEL" else 0,
        "gravity_score": gravity,
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
        print(f"[pmg] DRY RUN — {len(signals)} signals would be inserted")
        for s in signals[:5]:
            print(f"  {s['external_id']:20s} | {s['stream']:15s} | {s['title'][:70]}")
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
                    print(f"[pmg] Insert error for {sig.get('external_id', '?')}: {e}")

        conn.commit()
    finally:
        conn.close()

    if errors:
        print(f"[pmg] {errors} insert errors encountered")
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def run_collector(days: int = 14, max_pages: int = 5,
                  committee_ids: list[int] | None = None,
                  dry_run: bool = False) -> dict:
    """
    Main collection function.
    Returns a summary dict for pipeline telemetry.
    """
    start_time = time.time()
    print(f"[pmg] Parliamentary Oversight Tracker starting")
    print(f"[pmg] DB: {DB_PATH}")
    print(f"[pmg] Days: {days} | Max pages: {max_pages} | Dry run: {dry_run}")

    # Resolve committee filter
    target_ids = committee_ids or list(PRIORITY_COMMITTEES.keys())
    print(f"[pmg] Targeting {len(target_ids)} committees: {target_ids}")

    # Calculate date filter
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    print(f"[pmg] Since: {since}")

    # Fetch meetings from API
    print(f"[pmg] Fetching meetings from PMG API...")
    meetings = fetch_meetings(target_ids, max_pages=max_pages, since_date=since)
    print(f"[pmg] Found {len(meetings)} relevant meetings")

    if not meetings:
        print("[pmg] No meetings to process")
        duration = time.time() - start_time
        log_run(SOURCE_ID, "success", 0, 0, duration)
        return {"status": "done", "fetched": 0, "inserted": 0, "duration_s": duration}

    # Load actor names for cross-referencing (only in write mode)
    actor_names: set[str] = set()
    if not dry_run:
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=60)
            try:
                actor_names = _get_actor_names(conn)
            finally:
                conn.close()
            print(f"[pmg] Loaded {len(actor_names)} actor names for cross-reference")
        except Exception as e:
            print(f"[pmg] Actor name load failed (non-fatal): {e}")

    # Build signals
    signals = []
    build_errors = 0
    for meeting in meetings:
        try:
            sig = build_signal(meeting, actor_names)
            signals.append(sig)
        except Exception as e:
            build_errors += 1
            if build_errors <= 3:
                print(f"[pmg] Signal build error for meeting {meeting.get('id', '?')}: {e}")

    print(f"[pmg] Built {len(signals)} signals ({build_errors} build errors)")

    # Stream distribution
    stream_counts: dict[str, int] = {}
    for s in signals:
        stream_counts[s["stream"]] = stream_counts.get(s["stream"], 0) + 1
    for stream, count in sorted(stream_counts.items()):
        print(f"[pmg]   {stream}: {count}")

    # Persist
    inserted = persist_signals(signals, dry_run=dry_run)
    duration = round(time.time() - start_time, 2)

    if not dry_run:
        print(f"[pmg] Inserted {inserted} new signals ({len(signals) - inserted} duplicates)")
    print(f"[pmg] Done in {duration}s")

    log_run(SOURCE_ID, "success", len(meetings), inserted, duration)

    return {
        "status": "done",
        "fetched": len(meetings),
        "built": len(signals),
        "inserted": inserted,
        "build_errors": build_errors,
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
        description="FORGE PMG Parliamentary Oversight Collector"
    )
    parser.add_argument("--days", type=int, default=14,
                        help="Fetch meetings from the last N days (default: 14)")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="Maximum API pages to fetch (default: 5, 50 per page)")
    parser.add_argument("--committees", type=str, default="",
                        help="Comma-separated committee IDs to target (default: all priority)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse without writing to database")

    args = parser.parse_args()

    committee_ids = None
    if args.committees:
        try:
            committee_ids = [int(c.strip()) for c in args.committees.split(",")]
        except ValueError:
            print("[pmg] ERROR: --committees must be comma-separated integers")
            sys.exit(1)

    run_collector(
        days=args.days,
        max_pages=args.max_pages,
        committee_ids=committee_ids,
        dry_run=args.dry_run,
    )
