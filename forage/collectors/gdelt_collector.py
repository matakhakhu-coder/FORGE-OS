"""
FORAGE — GDELT 2.0 Signal Collector
=====================================
Phase 15.5: Massive Signal Expansion

Polls the GDELT 2.0 Document API for relevant geopolitical signals and
ingests them into the FORGE signals table.

Keywords: crime, money laundering, syndicate, protest, investigation, arrest
Priority keywords: interpol, cartel, corruption, trafficking, sanctions

Usage:
    python forage/collectors/gdelt_collector.py
    python forage/collectors/gdelt_collector.py --limit 50
    python forage/collectors/gdelt_collector.py --dry-run

Schedule via cron (every 15 minutes):
    */15 * * * * cd /path/to/forge && python forage/collectors/gdelt_collector.py >> logs/gdelt.log 2>&1
"""

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = BASE_DIR / "database.db"

# GDELT 2.0 Document API endpoint
GDELT_API_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

# Core keyword set — broad capture net
QUERY_KEYWORDS = (
    'crime OR "money laundering" OR syndicate OR protest '
    'OR investigation OR arrest OR corruption OR cartel OR trafficking'
)

# Mode: ArtList returns article-level JSON (best for structured ingestion)
GDELT_PARAMS = {
    "query":      QUERY_KEYWORDS,
    "mode":       "ArtList",
    "maxrecords": "250",
    "format":     "json",
    "sort":       "DateDesc",
    # Restrict to the last 24 hours to avoid re-ingesting old articles
    "STARTDATETIME": "",   # filled dynamically if needed
}

# Priority trigger terms — any match → is_priority = 1
PRIORITY_TERMS = {
    "interpol", "cartel", "corruption", "trafficking",
    "sanction", "sanctions", "money laundering", "laundering",
    "organised crime", "organized crime", "syndicate",
    "drug lord", "narco", "bribery", "extortion",
    "terrorist", "terrorism",
}

SOURCE_NAME = "gdelt"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[GDELT %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gdelt_collector")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def migrate_source_column(conn: sqlite3.Connection) -> None:
    """
    Phase 15.5: ensure the signals table has a TEXT `source` column.
    This mirrors the migrate_db() logic in app.py so the collector can
    run standalone without the Flask app.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(signals)")}
    if "source" not in cols:
        log.info("Migration: adding 'source' column to signals table")
        conn.execute("ALTER TABLE signals ADD COLUMN source TEXT")
        conn.commit()

# ---------------------------------------------------------------------------
# GDELT API fetch
# ---------------------------------------------------------------------------

def _build_url(extra_params: dict | None = None) -> str:
    params = dict(GDELT_PARAMS)
    if extra_params:
        params.update(extra_params)
    # Strip empty values
    params = {k: v for k, v in params.items() if v}
    return GDELT_API_BASE + "?" + urllib.parse.urlencode(params)


def fetch_gdelt_articles(limit: int = 250) -> list[dict]:
    """
    Calls the GDELT 2.0 Doc API and returns a list of raw article dicts.
    Returns [] on any network or parse error.
    """
    url = _build_url({"maxrecords": str(min(limit, 250))})
    log.info("Fetching GDELT: %s", url[:120] + "…")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "FORGE-OS/1.0 GDELT Collector (research)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.error("GDELT HTTP error: %s", exc)
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("GDELT JSON parse error: %s", exc)
        log.debug("Raw response (first 500): %s", raw[:500])
        return []

    articles = data.get("articles") or []
    log.info("GDELT returned %d articles", len(articles))
    return articles

# ---------------------------------------------------------------------------
# Coordinate extraction
# ---------------------------------------------------------------------------

# GDELT article dict may carry location data in several fields.
# The Doc API ArtList mode includes `sourcecountry` and sometimes
# `socialimage` URL fragments with lat/lng.  We do best-effort extraction.

# Known country centroids (ISO2 → lat, lng) — covers most GDELT traffic.
# Expand as needed; this is not exhaustive.
COUNTRY_CENTROIDS: dict[str, tuple[float, float]] = {
    "US": (37.09, -95.71),  "GB": (55.38, -3.44),   "FR": (46.23, 2.21),
    "DE": (51.17, 10.45),   "RU": (61.52, 105.32),  "CN": (35.86, 104.20),
    "IN": (20.59, 78.96),   "BR": (14.24, -51.93),  "AU": (-25.27, 133.78),
    "MX": (23.63, -102.55), "ZA": (-30.56, 22.94),  "NG": (9.08, 8.68),
    "EG": (26.82, 30.80),   "KE": (-0.02, 37.91),   "PK": (30.38, 69.35),
    "AF": (33.94, 67.71),   "IQ": (33.22, 43.68),   "SY": (34.80, 38.99),
    "UA": (48.38, 31.17),   "TR": (38.96, 35.24),   "SA": (23.89, 45.08),
    "IR": (32.43, 53.69),   "CO": (4.57, -74.30),   "VE": (6.42, -66.59),
    "PE": (-9.19, -75.02),  "AR": (-38.42, -63.62), "CL": (-35.68, -71.54),
    "PH": (12.88, 121.77),  "ID": (-0.79, 113.92),  "MM": (21.91, 95.96),
    "TH": (15.87, 100.99),  "KP": (40.34, 127.51),  "VN": (14.06, 108.28),
    "GH": (7.95, -1.02),    "ET": (9.15, 40.49),    "SD": (12.86, 30.22),
    "LY": (26.34, 17.23),   "ML": (17.57, -3.99),   "SO": (5.15, 46.20),
    "CD": (-4.04, 21.76),   "CM": (3.85, 11.50),    "AO": (-11.20, 17.87),
    "ES": (40.46, -3.75),   "IT": (41.87, 12.57),   "PL": (51.92, 19.15),
    "NL": (52.13, 5.29),    "BE": (50.50, 4.47),    "SE": (60.13, 18.64),
    "NO": (60.47, 8.47),    "CH": (46.82, 8.23),    "AT": (47.52, 14.55),
    "JP": (36.20, 138.25),  "KR": (35.91, 127.77),  "CA": (56.13, -106.35),
    "IL": (31.05, 34.85),   "PS": (31.95, 35.23),   "LB": (33.85, 35.86),
    "JO": (30.59, 36.24),   "YE": (15.55, 48.52),   "HT": (18.97, -72.29),
    "GT": (15.78, -90.23),  "HN": (15.20, -86.24),  "SV": (13.79, -88.90),
    "NI": (12.87, -85.21),  "PA": (8.54, -80.78),   "CU": (21.52, -77.78),
    "DO": (18.74, -70.16),  "TZ": (-6.37, 34.89),   "MZ": (-18.67, 35.53),
    "ZW": (-19.02, 29.15),  "ZM": (-13.13, 27.85),  "MW": (-13.25, 34.30),
    "SN": (14.50, -14.45),  "MR": (21.01, -10.94),  "TN": (33.89, 9.54),
    "DZ": (28.03, 1.66),    "MA": (31.79, -7.09),   "LR": (6.43, -9.43),
    "SL": (8.46, -11.78),   "GN": (11.80, -15.18),  "CI": (7.54, -5.55),
    "BF": (12.36, -1.56),   "TD": (15.45, 18.73),   "NE": (17.61, 8.08),
}


def extract_coordinates(article: dict) -> tuple[float | None, float | None]:
    """
    Attempts to resolve lat/lng from article metadata.

    Priority order:
    1. `location` field (rare in ArtList but present in some extended records)
    2. `sourcecountry` → country centroid lookup
    3. None, None (coordinate-less signals are still stored, just not mappable)
    """
    # 1. Direct lat/lng fields (enriched records)
    lat = article.get("latitude") or article.get("lat")
    lng = article.get("longitude") or article.get("lng") or article.get("lon")
    try:
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    except (ValueError, TypeError):
        pass

    # 2. EventGeo fields (present when GDELT merges event data)
    action_geo = article.get("ActionGeo_Lat") or article.get("actiongeolat")
    action_lon = article.get("ActionGeo_Long") or article.get("actiongeolong")
    try:
        if action_geo and action_lon:
            return float(action_geo), float(action_lon)
    except (ValueError, TypeError):
        pass

    # 3. Country centroid
    country = (article.get("sourcecountry") or "").upper().strip()
    if country in COUNTRY_CENTROIDS:
        # Jitter slightly so multiple articles from same country don't stack
        import random
        base_lat, base_lng = COUNTRY_CENTROIDS[country]
        jitter = lambda v: v + random.uniform(-0.8, 0.8)
        return jitter(base_lat), jitter(base_lng)

    return None, None

# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def compute_priority(article: dict) -> int:
    """
    Returns 1 if any priority keyword appears in title or domain, else 0.
    Case-insensitive substring match.
    """
    haystack = " ".join([
        (article.get("title")  or ""),
        (article.get("domain") or ""),
        (article.get("url")    or ""),
    ]).lower()

    for term in PRIORITY_TERMS:
        if term in haystack:
            return 1
    return 0

# ---------------------------------------------------------------------------
# External ID + dedup
# ---------------------------------------------------------------------------

def make_external_id(article: dict) -> str:
    """
    Stable unique ID for a GDELT article.
    Uses the article URL (the most reliable unique field in GDELT).
    Falls back to a hash of title + date.
    """
    url = (article.get("url") or "").strip()
    if url:
        return "gdelt:" + hashlib.sha1(url.encode()).hexdigest()[:16]

    seed = (article.get("title") or "") + (article.get("seendate") or "")
    return "gdelt:" + hashlib.sha1(seed.encode()).hexdigest()[:16]


def make_signal_id(external_id: str) -> str:
    """UUID-ish primary key from the external ID."""
    h = hashlib.sha256(external_id.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

# ---------------------------------------------------------------------------
# Timestamp normalisation
# ---------------------------------------------------------------------------

def parse_gdelt_date(raw: str | None) -> str:
    """
    GDELT seendate format: YYYYMMDDTHHMMSSZ  or  YYYYMMDDHHMMSS
    Returns ISO 8601 datetime string or UTC now if unparseable.
    """
    if not raw:
        return datetime.now(timezone.utc).isoformat()

    raw = raw.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Core ingestion logic
# ---------------------------------------------------------------------------

def ingest_articles(
    articles: list[dict],
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Iterates over GDELT articles, resolves coordinates + priority, and
    upserts into the signals table.

    Returns a stats dict: { inserted, skipped, errors }
    """
    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    for article in articles:
        try:
            external_id = make_external_id(article)
            signal_id   = make_signal_id(external_id)

            # Dedup: skip if already stored
            existing = conn.execute(
                "SELECT 1 FROM signals WHERE external_id = ?", (external_id,)
            ).fetchone()
            if existing:
                stats["skipped"] += 1
                continue

            title    = (article.get("title") or "").strip()[:500]
            if not title:
                stats["skipped"] += 1
                continue

            content   = (article.get("domain") or article.get("url") or "")[:1000]
            timestamp = parse_gdelt_date(article.get("seendate"))
            lat, lng  = extract_coordinates(article)
            priority  = compute_priority(article)

            metadata  = {
                "url":           article.get("url"),
                "domain":        article.get("domain"),
                "language":      article.get("language"),
                "sourcecountry": article.get("sourcecountry"),
                "seendate":      article.get("seendate"),
                "socialimage":   article.get("socialimage"),
            }

            if dry_run:
                log.info(
                    "[DRY-RUN] Would insert: %-60s  lat=%s lng=%s priority=%d",
                    title[:60], lat, lng, priority,
                )
                stats["inserted"] += 1
                continue

            conn.execute(
                """
                INSERT INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, metadata_json, is_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'raw', ?, ?)
                """,
                (
                    signal_id,
                    SOURCE_NAME,
                    external_id,
                    title,
                    content,
                    lat,
                    lng,
                    timestamp,
                    json.dumps(metadata, ensure_ascii=False),
                    priority,
                ),
            )
            stats["inserted"] += 1

            if priority:
                log.info("⚑ PRIORITY: %s", title[:80])

        except sqlite3.IntegrityError:
            # Race condition — another process inserted this signal
            stats["skipped"] += 1
        except Exception as exc:
            log.warning("Error processing article: %s — %s", article.get("title", "?")[:60], exc)
            stats["errors"] += 1

    if not dry_run:
        conn.commit()

    return stats

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="FORAGE GDELT 2.0 Collector — Phase 15.5"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help="Maximum articles to fetch per run (max 250, GDELT API limit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse articles but do not write to the database",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override database path (default: autodetect from script location)",
    )
    args = parser.parse_args()

    global DB_PATH
    if args.db:
        DB_PATH = args.db.resolve()

    if not DB_PATH.exists() and not args.dry_run:
        log.error("Database not found at %s — run 'python app.py --init-db' first", DB_PATH)
        return 1

    log.info("=== GDELT Collector starting (limit=%d, dry_run=%s) ===", args.limit, args.dry_run)
    t0 = time.monotonic()

    # Fetch
    articles = fetch_gdelt_articles(limit=args.limit)
    if not articles:
        log.warning("No articles returned from GDELT — exiting")
        return 0

    if args.dry_run:
        conn = None
        stats = ingest_articles(articles, conn=None, dry_run=True)  # type: ignore[arg-type]
    else:
        conn = open_db()
        try:
            migrate_source_column(conn)
            stats = ingest_articles(articles, conn, dry_run=False)
        finally:
            conn.close()

    elapsed = time.monotonic() - t0
    log.info(
        "=== Done in %.1fs — inserted: %d  skipped: %d  errors: %d ===",
        elapsed, stats["inserted"], stats["skipped"], stats["errors"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())