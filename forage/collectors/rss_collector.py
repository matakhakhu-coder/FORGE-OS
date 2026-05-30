#!/usr/bin/env python3
"""
FORAGE — RSS News Signal Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingests disaster and crisis alerts from the GDACS (Global Disaster Alert and
Coordination System) public RSS feed and stores them in the FORGE signals table.

Feed:   GDACS Combined RSS Feed
URL:    https://www.gdacs.org/xml/rss.xml
Docs:   https://www.gdacs.org/xml/gdacs_rss_documentation.pdf

Additional feed options (set FORAGE_RSS_URL env var to override):
  All events:    https://www.gdacs.org/xml/rss.xml
  Earthquakes:   https://www.gdacs.org/xml/rss_eq.xml
  Tropical:      https://www.gdacs.org/xml/rss_tc.xml
  Floods:        https://www.gdacs.org/xml/rss_fl.xml
  Volcanoes:     https://www.gdacs.org/xml/rss_vo.xml
  Drought:       https://www.gdacs.org/xml/rss_dr.xml

Design decisions
────────────────
• Idempotent: the RSS <guid> or <link> element is used as external_id.
  INSERT OR IGNORE ensures re-running never creates duplicates.

• Priority detection: a configurable keyword list is checked against the
  combined title+description text (case-insensitive).  Matches set
  is_priority=1 so the topbar bell fires.  Default keywords cover the
  threat categories most likely to require immediate analyst attention.

• Coordinates: GDACS items optionally carry <geo:lat>/<geo:long> or
  <georss:point> tags.  Both formats are parsed; if neither is present
  the lat/lng columns remain NULL.

• metadata_json: the full dict of all RSS item fields is stored verbatim
  so no information is discarded.

• The script is zero-dependency — it uses only Python stdlib (urllib,
  xml.etree.ElementTree) so it runs without pip install.

Usage
─────
    # Run once manually
    python forage/collectors/rss_collector.py

    # Override feed URL (e.g. earthquakes only)
    FORAGE_RSS_URL=https://www.gdacs.org/xml/rss_eq.xml \\
        python forage/collectors/rss_collector.py

    # Schedule with cron (every 15 minutes)
    */15 * * * * cd /path/to/FORGE && python forage/collectors/rss_collector.py >> logs/forage_rss.log 2>&1

    # Override database path
    FORGE_DB=/custom/path/database.db python forage/collectors/rss_collector.py

Priority keywords (override with FORAGE_PRIORITY_KEYWORDS env var,
comma-separated):
    Nuclear, Tsunami, Explosion, Crisis, Chemical, Biological,
    Radiological, Attack, Mass Casualty, Red Alert
"""

__manifest__ = {
    "id":          "rss_collector",
    "name":        "GDACS RSS Collector",
    "description": "Ingests global disaster and crisis alerts from the GDACS combined RSS feed. Flags nuclear, tsunami, explosion, and mass casualty events as priority signals.",
    "icon":        "📻",
    "entry":       "forage/collectors/rss_collector.py",
    "args":        [],
    "job_key":     "rss_collector",
    "version":     "1.0.0",
}

import json
import time
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
# Phase 32: path-safe pipeline logger import
def _log_run_safe(*args, **kwargs):
    """Inline log_run that works whether called as a module or direct script."""
    import sys as _sys, importlib.util as _ilu
    from pathlib import Path as _P
    _logger_path = _P(__file__).resolve().parent.parent.parent / "forage" / "utils" / "pipeline_logger.py"
    if str(_logger_path.parent.parent) not in _sys.path:
        _sys.path.insert(0, str(_logger_path.parent.parent))
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_logger_path))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass  # logging must never crash the pipeline
log_run = _log_run_safe
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

# ── Feed configuration ────────────────────────────────────────────────────────

DEFAULT_FEED_URL = "https://www.gdacs.org/xml/rss.xml"
FEED_URL         = os.environ.get("FORAGE_RSS_URL", DEFAULT_FEED_URL)
SOURCE_TAG       = "GDACS"
TIMEOUT_SEC      = 25

# ── Priority keywords ─────────────────────────────────────────────────────────

_DEFAULT_KEYWORDS = [
    "nuclear", "tsunami", "explosion", "crisis", "chemical",
    "biological", "radiological", "attack", "mass casualty", "red alert",
    "level red", "extreme", "catastrophic", "major disaster",
]

def _load_keywords() -> list[str]:
    env = os.environ.get("FORAGE_PRIORITY_KEYWORDS", "")
    if env.strip():
        return [k.strip().lower() for k in env.split(",") if k.strip()]
    return _DEFAULT_KEYWORDS

PRIORITY_KEYWORDS = _load_keywords()

# ── Phase 27: Signal Stream classification ───────────────────────────────────
# Evaluated in order: CRIME_INTEL → INFRASTRUCTURE → PRIORITY → GLOBAL

CRIME_KEYWORDS = [
    "arrest", "murder", "robbery", "drug bust", "trafficking",
    "gang", "kidnapping", "smuggling", "police raid", "crime",
    "shooting", "homicide", "carjacking", "heist", "extortion",
    "organised crime", "organized crime", "syndicate", "narco",
    "interpol", "fugitive", "warrant", "conviction", "sentenced",
]

INFRASTRUCTURE_KEYWORDS = [
    "power outage", "load shedding", "loadshedding", "blackout",
    "water outage", "water supply", "pipe burst", "sewage",
    "road closure", "bridge failure", "transport disruption",
    "telecom failure", "network outage", "internet outage",
    "eskom", "infrastructure", "supply chain",
    "port congestion", "fuel shortage", "gas leak",
]

PRIORITY_ARTICLE_KEYWORDS = [
    "analysis", "investigation", "exclusive", "intelligence briefing",
    "threat assessment", "special report", "revealed", "leaked",
    "deep dive", "exposed", "classified",
]


def classify_stream(title: str, content: str) -> str:
    """
    Classify a signal into a curated stream via keyword matching.
    Evaluation order: CRIME_INTEL -> INFRASTRUCTURE -> PRIORITY -> GLOBAL
    """
    combined = (title + " " + (content or "")).lower()
    for kw in CRIME_KEYWORDS:
        if kw in combined:
            return "CRIME_INTEL"
    for kw in INFRASTRUCTURE_KEYWORDS:
        if kw in combined:
            return "INFRASTRUCTURE"
    for kw in PRIORITY_ARTICLE_KEYWORDS:
        if kw in combined:
            return "PRIORITY"
    return "GLOBAL"


# ── XML namespace map ─────────────────────────────────────────────────────────
# GDACS uses several optional namespaces; we register them all so XPath
# find() calls work without brittle string concatenation.

NS = {
    "geo":     "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "georss":  "http://www.georss.org/georss",
    "gdacs":   "http://www.gdacs.org",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# ── Database path resolution ──────────────────────────────────────────────────

def _resolve_db() -> Path:
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    here    = Path(__file__).resolve()
    db_path = here.parent.parent.parent / "database.db"
    return db_path

DB_PATH = _resolve_db()

# ── Logging helpers ───────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [rss_collector] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [rss_collector] WARN  {msg}", file=sys.stderr, flush=True)
def err(msg: str)  -> None: print(f"[{_ts()}] [rss_collector] ERROR {msg}", file=sys.stderr, flush=True)

# ── RSS fetch ─────────────────────────────────────────────────────────────────

def fetch_feed(url: str = FEED_URL) -> ET.Element:
    """Download and parse the RSS XML.  Returns the root Element."""
    headers = {
        "User-Agent": "FORGE-OS/1.0 FORAGE-RSSCollector (+local)",
        "Accept":     "application/rss+xml, application/xml, text/xml",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} fetching RSS feed: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error fetching RSS feed: {exc.reason}") from exc

    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse RSS XML: {exc}") from exc

# ── RSS item → signal record ──────────────────────────────────────────────────

def _find_text(elem: ET.Element, *paths: str) -> str | None:
    """Try each XPath in turn, return first non-empty text found."""
    for path in paths:
        try:
            node = elem.find(path, NS)
        except Exception:
            node = None
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None

def _parse_coords(item: ET.Element) -> tuple[float | None, float | None]:
    """
    Extract coordinates from:
      <geo:lat>/<geo:long>  — two separate elements
      <georss:point>lat lng — space-separated pair
    Returns (lat, lng) or (None, None).
    """
    lat = _find_text(item, "geo:lat")
    lng = _find_text(item, "geo:long")
    if lat and lng:
        try:
            return float(lat), float(lng)
        except ValueError:
            pass

    point = _find_text(item, "georss:point")
    if point:
        parts = point.split()
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass

    return None, None

def _parse_timestamp(item: ET.Element) -> str:
    """Return ISO-ish datetime string from <pubDate> or now()."""
    pub = _find_text(item, "pubDate")
    if pub:
        try:
            dt = parsedate_to_datetime(pub)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _build_external_id(item: ET.Element) -> str | None:
    """
    GDACS <guid> is a stable URL like
    https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1234567
    Use it verbatim.  Fall back to <link> if <guid> is absent.
    """
    return _find_text(item, "guid") or _find_text(item, "link")

def _collect_metadata(item: ET.Element) -> dict:
    """Gather all useful fields into a flat dict for metadata_json."""
    meta: dict = {}

    simple_tags = ["title", "link", "guid", "pubDate", "description",
                   "author", "category"]
    for tag in simple_tags:
        val = _find_text(item, tag)
        if val:
            meta[tag] = val

    # GDACS-specific extensions
    gdacs_tags = [
        ("gdacs:eventtype",   "eventtype"),
        ("gdacs:eventid",     "eventid"),
        ("gdacs:alertlevel",  "alertlevel"),
        ("gdacs:alertscore",  "alertscore"),
        ("gdacs:severity",    "severity"),
        ("gdacs:population",  "population"),
        ("gdacs:country",     "country"),
        ("gdacs:iso3",        "iso3"),
        ("gdacs:fromdate",    "fromdate"),
        ("gdacs:todate",      "todate"),
        ("gdacs:iscurrent",   "iscurrent"),
        ("gdacs:version",     "version"),
        ("gdacs:cap",         "cap_url"),
    ]
    for xpath, key in gdacs_tags:
        val = _find_text(item, xpath)
        if val:
            meta[key] = val

    # Coordinates
    lat, lng = _parse_coords(item)
    if lat is not None:
        meta["lat"] = lat
        meta["lng"] = lng

    return meta

def _is_priority(title: str, description: str) -> int:
    """Return 1 if any priority keyword matches the combined text."""
    combined = (title + " " + description).lower()
    for kw in PRIORITY_KEYWORDS:
        if kw in combined:
            return 1
    return 0

def _strip_html(text: str) -> str:
    """Remove HTML tags from RSS description text."""
    return re.sub(r"<[^>]+>", " ", text).strip()
    text = re.sub(r"\s{2,}", " ", text)
    return text

def item_to_signal(item: ET.Element) -> dict | None:
    """
    Convert a single RSS <item> into a FORGE signal dict.
    Returns None if the item is too malformed to be useful.
    """
    try:
        external_id = _build_external_id(item)
        if not external_id:
            warn("Item has no guid or link — skipping")
            return None

        title_raw = _find_text(item, "title") or "Untitled GDACS alert"
        title     = _strip_html(title_raw)[:255]

        desc_raw  = (_find_text(item, "description") or
                     _find_text(item, "content:encoded") or "")
        content   = _strip_html(desc_raw)[:1000]

        lat, lng  = _parse_coords(item)
        ts        = _parse_timestamp(item)
        meta      = _collect_metadata(item)
        priority  = _is_priority(title, content)

        stream = classify_stream(title, content)

        return {
            "signal_id":     str(uuid.uuid4()),
            "source":        SOURCE_TAG,
            "external_id":   external_id,
            "title":         title,
            "content":       content,
            "lat":           lat,
            "lng":           lng,
            "timestamp":     ts,
            "status":        "raw",
            "metadata_json": json.dumps(meta, ensure_ascii=False),
            "is_priority":   priority,
            "stream":        stream,
        }
    except Exception as exc:
        warn(f"Skipping malformed item: {exc}")
        return None

# ── Database operations ───────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run:  python app.py --init-db\n"
            "Or set FORGE_DB=/path/to/database.db"
        )
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def _ensure_signals_table(conn: sqlite3.Connection) -> None:
    """
    Create / migrate the signals table so the collector works stand-alone,
    before `python app.py --migrate` has been run.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            signal_id     TEXT    PRIMARY KEY,
            source        TEXT    NOT NULL,
            external_id   TEXT    NOT NULL UNIQUE,
            title         TEXT    NOT NULL,
            content       TEXT,
            lat           REAL,
            lng           REAL,
            timestamp     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status        TEXT    NOT NULL DEFAULT 'raw'
                          CHECK(status IN ('raw','reviewed','promoted','dismissed')),
            metadata_json TEXT,
            cluster_id    TEXT,
            is_priority   INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Idempotent column additions for databases that predate Phase 14
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    for col, defn in [("cluster_id", "TEXT"), ("is_priority", "INTEGER NOT NULL DEFAULT 0")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {defn}")
    conn.commit()

def insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """
    Bulk-insert via INSERT OR IGNORE — duplicate external_ids are silently
    skipped.  Returns (inserted_count, skipped_count).
    """
    inserted = skipped = 0

    # Phase 27: ensure stream column exists on pre-migration databases
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "stream" not in existing_cols:
        try:
            conn.execute(
                "ALTER TABLE signals ADD COLUMN "
                "stream TEXT NOT NULL DEFAULT 'GLOBAL'"
            )
            conn.commit()
        except Exception:
            pass

    for sig in signals:
        row = {**sig, "stream": sig.get("stream", "GLOBAL")}
        cur = conn.execute("""
            INSERT OR IGNORE INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, metadata_json,
                 is_priority, stream)
            VALUES
                (:signal_id, :source, :external_id, :title, :content,
                 :lat, :lng, :timestamp, :status, :metadata_json,
                 :is_priority, :stream)
        """, row)
        if cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped

# ── Main entry point ──────────────────────────────────────────────────────────

def run() -> int:
    """Execute one collection cycle.  Returns exit code (0 = success, 1 = error)."""
    _t0 = time.monotonic()
    log(f"Starting RSS collection — feed: {FEED_URL}")
    log(f"Priority keywords ({len(PRIORITY_KEYWORDS)}): {', '.join(PRIORITY_KEYWORDS)}")
    log(f"Database: {DB_PATH}")

    try:
        root = fetch_feed()
    except RuntimeError as exc:
        err(str(exc))
        return 1

    # RSS structure: <rss><channel><item>…</item></channel></rss>
    channel = root.find("channel")
    if channel is None:
        err("RSS root has no <channel> element — unexpected feed format")
        return 1

    items = channel.findall("item")
    log(f"Feed contains {len(items)} items")

    if not items:
        log("No items in feed — nothing to do.")
        return 0

    signals = [s for i in items if (s := item_to_signal(i)) is not None]
    priority_new = sum(1 for s in signals if s["is_priority"] == 1)
    log(f"Mapped {len(signals)}/{len(items)} items → signals "
        f"({priority_new} flagged as priority)")

    try:
        conn = _open_db(DB_PATH)
    except FileNotFoundError as exc:
        err(str(exc))
        return 1

    try:
        _ensure_signals_table(conn)
        inserted, skipped = insert_signals(conn, signals)
    finally:
        conn.close()

    log(f"Done — inserted: {inserted}, skipped (duplicate): {skipped}")
    if inserted > 0 and priority_new > 0:
        log(f"⚠  PRIORITY SIGNALS INSERTED — check Signal Monitor at /signals")
    log_run(DB_PATH, "rss_collector", "success",
            records_in=len(signals), records_out=inserted,
            duration_s=time.monotonic() - _t0)
    return 0

if __name__ == "__main__":
    sys.exit(run())

# --- MEGA RUNNER ADAPTER ---
import asyncio as _asyncio

async def async_main(**kwargs):
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in rss_collector.py: {e}")