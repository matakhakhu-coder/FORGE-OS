#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- INTERPOL Red Notice Collector
======================================
Ingests global Red Notices from the INTERPOL public API, filtering for
regional cross-matches relevant to the South African OSINT context.

API endpoint
────────────
  https://ws-public.interpol.int/notices/v1/red

CRITICAL: This API is GeoIP-blocked from South Africa (returns 403).
The collector uses a dual-path approach:
  1. Primary: attempt direct API access with pagination
  2. Fallback: if 403/blocked, log a clear message and exit gracefully

Filter strategy
───────────────
  Default query filters by nationality (--nationality, default ZA).
  API response shape per notice:
    {"forename": "JOHN", "name": "DOE", "date_of_birth": "1980/01/15",
     "nationalities": ["ZA"], "entity_id": "2024/12345",
     "_links": {"self": {"href": "..."}, "images": {"href": "..."},
                "thumbnail": {"href": "..."}}}

Signal mapping
──────────────
  source           = "interpol_red_notices"
  external_id      = "interpol:<entity_id>"
  stream           = CRIME_INTEL
  relevance_score  = 2.0 (high authority)
  is_priority      = 1
  source_type      = "live"

Environment variables
─────────────────────
  FORGE_DB   Path to FORGE database (default: auto-detect)

Usage
─────
  python forage/collectors/interpol_collector.py
  python forage/collectors/interpol_collector.py --dry-run
  python forage/collectors/interpol_collector.py --nationality ZA --max-pages 3
  python forage/collectors/interpol_collector.py --nationality MZ --dry-run
"""

__manifest__ = {
    "id":          "interpol_red_notices",
    "name":        "INTERPOL Notices Monitor",
    "description": "Ingests global Red Notices from the INTERPOL public API, filtering for regional cross-matches.",
    "icon":        "\U0001f310",
    "entry":       "forage/collectors/interpol_collector.py",
    "args":        [],
    "job_key":     "interpol_red_notices",
    "version":     "1.0.0",
}

# ── Windows CP1252 safety -- reconfigure stdout to UTF-8 before any print() ──
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Standard library ─────────────────────────────────────────────────────────
import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Canonical source key -- MUST match manifest["id"] for auto-pin membrane query
SOURCE_ID = __manifest__["id"]

# ── Refinery (Stable 1.1) ────────────────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t):  # noqa: E731
        return t

# ── Pipeline logger (path-safe, no hard coupling) ────────────────────────────
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu
    _lp = BASE_DIR / "forage" / "utils" / "pipeline_logger.py"
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_lp))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass

log_run = _log_run_safe

# ── Optional dependencies ────────────────────────────────────────────────────
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[interpol] WARNING: requests not installed. "
          "Run: pip install requests --break-system-packages")

# ── Constants ────────────────────────────────────────────────────────────────

API_BASE_URL    = "https://ws-public.interpol.int/notices/v1/red"
RESULTS_PER_PAGE = 20
REQUEST_DELAY_S  = 3      # polite delay between API requests
DEFAULT_NATIONALITY = "ZA"
DEFAULT_MAX_PAGES   = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# GeoIP block status codes that indicate regional blocking
GEOIP_BLOCK_CODES = {403, 451}

# ── DB helpers ───────────────────────────────────────────────────────────────

def _resolve_db() -> Path:
    """Resolve database path from env or default location."""
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return BASE_DIR / "database.db"


# ── API client ───────────────────────────────────────────────────────────────

def _build_api_url(nationality: str, page: int) -> str:
    """Build the INTERPOL Red Notice API URL with query parameters."""
    return (
        f"{API_BASE_URL}"
        f"?nationality={nationality}"
        f"&resultPerPage={RESULTS_PER_PAGE}"
        f"&page={page}"
    )


def _fetch_page(
    session: requests.Session,
    nationality: str,
    page: int,
) -> tuple[dict | None, str | None]:
    """
    Fetch one page of Red Notice results from the INTERPOL API.

    Returns:
        (data_dict, None)        on success
        (None, error_message)    on failure

    The error_message distinguishes between GeoIP blocks (which should
    halt the entire run) and transient failures (which can be skipped).
    """
    url = _build_api_url(nationality, page)
    try:
        resp = session.get(url, timeout=30)

        # GeoIP block detection
        if resp.status_code in GEOIP_BLOCK_CODES:
            return None, (
                f"GEOIP BLOCKED (HTTP {resp.status_code}): "
                f"The INTERPOL public API is blocked from this IP/region. "
                f"This is expected from South African IP addresses. "
                f"A VPN or proxy with a non-ZA exit node is required."
            )

        resp.raise_for_status()
        return resp.json(), None

    except requests.exceptions.HTTPError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc}"
    except requests.exceptions.ConnectionError as exc:
        return None, f"Connection error: {exc}"
    except requests.exceptions.Timeout:
        return None, f"Request timed out after 30s"
    except requests.exceptions.JSONDecodeError:
        return None, f"Invalid JSON response from API"
    except Exception as exc:
        return None, f"Unexpected error: {exc}"


# ── Notice → signal mapping ─────────────────────────────────────────────────

def _notice_to_signal(notice: dict) -> dict | None:
    """
    Map one INTERPOL Red Notice to a FORGE signals row dict.

    Returns None if the notice is missing essential fields.
    """
    entity_id = notice.get("entity_id", "")
    if not entity_id:
        return None

    forename    = (notice.get("forename") or "").strip()
    surname     = (notice.get("name") or "").strip()
    dob         = (notice.get("date_of_birth") or "").strip()
    nationalities = notice.get("nationalities") or []

    # Names
    if not surname:
        return None  # surname is the minimum viable field

    full_name = f"{forename} {surname}".strip() if forename else surname

    # Links
    links       = notice.get("_links") or {}
    self_link   = (links.get("self") or {}).get("href", "")
    thumb_link  = (links.get("thumbnail") or {}).get("href", "")
    images_link = (links.get("images") or {}).get("href", "")

    # Build the public notice URL from entity_id
    # Format: https://www.interpol.int/en/How-we-work/Notices/View-Red-Notices#2024-12345
    notice_url = self_link or f"https://ws-public.interpol.int/notices/v1/red/{entity_id}"

    # Signal fields
    signal_id   = uuid.uuid4().hex
    external_id = f"interpol:{entity_id}"
    title       = _sanitize(f"{full_name} — INTERPOL Red Notice")[:300]
    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    content = _sanitize(
        f"INTERPOL Red Notice for {full_name}. "
        f"Entity ID: {entity_id}. "
        f"Date of birth: {dob or 'unknown'}. "
        f"Nationalities: {', '.join(nationalities) if nationalities else 'unknown'}."
    )

    metadata = {
        "entity_id":     entity_id,
        "forename":      forename,
        "surname":       surname,
        "date_of_birth": dob,
        "nationalities": nationalities,
        "notice_url":    notice_url,
        "thumbnail_url": thumb_link,
        "images_url":    images_link,
        "sub_source":    "interpol_red_notices",
    }

    return {
        "signal_id":       signal_id,
        "source":          SOURCE_ID,
        "external_id":     external_id,
        "title":           title,
        "content":         content,
        "lat":             None,
        "lng":             None,
        "timestamp":       now,
        "stream":          "CRIME_INTEL",
        "relevance_score": 2.0,
        "is_priority":     1,
        "metadata_json":   json.dumps(metadata, ensure_ascii=False),
    }


# ── Main runner ──────────────────────────────────────────────────────────────

def run(
    nationality: str = DEFAULT_NATIONALITY,
    max_pages: int = DEFAULT_MAX_PAGES,
    dry_run: bool = False,
) -> None:
    """
    INTERPOL Red Notice collection cycle:
      1. Open HTTP session with realistic headers
      2. Paginate through Red Notice API (filtered by nationality)
      3. Map each notice to a FORGE signal
      4. INSERT OR IGNORE into signals table (dedup on external_id)
      5. Report results
    """
    if not HAS_REQUESTS:
        print("[interpol] ABORT: requests library not available.")
        return

    print(f"[interpol] INTERPOL Red Notice collector starting")
    print(f"[interpol] nationality={nationality}  max_pages={max_pages}  dry_run={dry_run}")
    start_ts = datetime.now(timezone.utc)

    # ── Resolve DB path ──────────────────────────────────────────────────────
    db_path = _resolve_db()
    if not dry_run and not db_path.exists():
        print(f"[interpol] ABORT: database not found at {db_path}")
        print(f"[interpol] Run: python app.py --init-db")
        return

    # ── HTTP session ─────────────────────────────────────────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept":     "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # ── Collection pass ──────────────────────────────────────────────────────
    all_signals: list[dict] = []
    total_notices = 0
    parse_errors  = 0
    geoip_blocked = False

    for page in range(1, max_pages + 1):
        print(f"  [interpol] Fetching page {page}/{max_pages} "
              f"(nationality={nationality}) ...")

        data, error = _fetch_page(session, nationality, page)

        if error:
            if "GEOIP BLOCKED" in error:
                print(f"  [interpol] {error}")
                geoip_blocked = True
                break
            else:
                print(f"  [interpol] Page {page} error: {error}")
                # Transient failure — try next page
                if page < max_pages:
                    time.sleep(REQUEST_DELAY_S)
                continue

        if not data:
            print(f"  [interpol] Page {page}: empty response")
            break

        # Extract notices from the embedded response
        embedded = data.get("_embedded") or {}
        notices  = embedded.get("notices") or []
        total_api = data.get("total", 0)

        if not notices:
            print(f"  [interpol] Page {page}: no notices returned (total={total_api})")
            break

        print(f"  [interpol] Page {page}: {len(notices)} notices "
              f"(API total: {total_api})")
        total_notices += len(notices)

        for notice in notices:
            try:
                sig = _notice_to_signal(notice)
                if sig:
                    all_signals.append(sig)
                else:
                    parse_errors += 1
            except Exception as exc:
                print(f"  [interpol] Notice parse error: {exc}")
                parse_errors += 1

        # Check if we've exhausted all available notices
        if total_notices >= total_api:
            print(f"  [interpol] All {total_api} notices fetched")
            break

        # Polite delay between API requests
        if page < max_pages:
            time.sleep(REQUEST_DELAY_S)

    # ── Summary of collection phase ──────────────────────────────────────────
    print(f"  [interpol] Collection complete: "
          f"{len(all_signals)} signals from {total_notices} notices "
          f"({parse_errors} parse errors)")

    if geoip_blocked and not all_signals:
        print("[interpol] No data collected due to GeoIP block. "
              "Use a VPN with a non-ZA exit node.")
        elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
        log_run(
            collector="interpol_red_notices",
            new_signals=0,
            errors=1,
            runtime_seconds=elapsed,
            meta={"geoip_blocked": True, "nationality": nationality},
        )
        return

    if not all_signals:
        print("[interpol] No signals to write.")
        return

    # ── Dry run ──────────────────────────────────────────────────────────────
    if dry_run:
        print(f"[interpol] DRY RUN — {len(all_signals)} signals not written to DB")
        for sig in all_signals[:5]:
            print(f"  SAMPLE: {sig['external_id']} | {sig['title'][:80]}")
        if len(all_signals) > 5:
            print(f"  ... and {len(all_signals) - 5} more")
        return

    # ── DB insert ────────────────────────────────────────────────────────────
    inserted = 0
    skipped  = 0
    db_errors = 0

    conn = sqlite3.connect(str(db_path), timeout=60)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        for sig in all_signals:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO signals
                        (signal_id, source, external_id, title, content,
                         lat, lng, timestamp, status, stream,
                         relevance_score, is_priority, metadata_json, source_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'live')
                    """,
                    (
                        sig["signal_id"],
                        sig["source"],
                        sig["external_id"],
                        sig["title"],
                        sig["content"],
                        sig["lat"],
                        sig["lng"],
                        sig["timestamp"],
                        "raw",
                        sig["stream"],
                        sig["relevance_score"],
                        sig["is_priority"],
                        sig["metadata_json"],
                    ),
                )
                # Check if the row was actually inserted (not a duplicate)
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
                else:
                    skipped += 1
            except sqlite3.Error as exc:
                print(f"  [interpol] DB insert error: {exc}")
                db_errors += 1

        conn.commit()
    finally:
        conn.close()

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(
        f"[interpol] Complete in {elapsed:.1f}s — "
        f"+{inserted} new | ~{skipped} duplicates | "
        f"!{db_errors} errors | "
        f"nationality={nationality}"
    )

    # ── Pipeline telemetry ───────────────────────────────────────────────────
    log_run(
        collector="interpol_red_notices",
        new_signals=inserted,
        errors=db_errors + parse_errors,
        runtime_seconds=elapsed,
        meta={
            "nationality":   nationality,
            "total_notices":  total_notices,
            "skipped":        skipped,
            "parse_errors":   parse_errors,
            "geoip_blocked":  geoip_blocked,
            "pages_fetched":  min(max_pages, (total_notices // RESULTS_PER_PAGE) + 1),
        },
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE INTERPOL Red Notice Collector — "
                    "ingest Red Notices from the INTERPOL public API"
    )
    parser.add_argument(
        "--nationality", type=str, default=DEFAULT_NATIONALITY,
        help=f"ISO 3166-1 alpha-2 nationality filter (default: {DEFAULT_NATIONALITY})"
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"Maximum API pages to fetch (default: {DEFAULT_MAX_PAGES})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse notices but do not write to DB"
    )
    args = parser.parse_args()

    run(
        nationality=args.nationality,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    )
