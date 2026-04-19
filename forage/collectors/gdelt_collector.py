#!/usr/bin/env python3
"""
FORGE — GDELT DOC API Collector  (forage/collectors/gdelt_collector.py)
═══════════════════════════════════════════════════════════════════════

REPLACEMENT for the unreliable GDELT event stream collector.

This version uses the GDELT Document API (DOC API) instead of the
GDELT Event CSV stream. The DOC API returns structured JSON of news
articles matching a keyword query — far less noisy than the raw event
stream, and allows precise South Africa targeting.

Key differences from the old gdelt_collector.py
────────────────────────────────────────────────
  OLD: Polling raw GDELT event CSV → high noise, broad geo, no query control
  NEW: GDELT DOC API JSON query   → keyword-targeted, SA-focused, article-level

GDELT DOC API reference:
  https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
  https://api.gdeltproject.org/api/v2/doc/doc

No API key required. Rate limit: ~1 request/second.

Usage
─────
  python forage/collectors/gdelt_collector.py
  python forage/collectors/gdelt_collector.py --dry-run
  python forage/collectors/gdelt_collector.py --query "Hawks arrest" --hours 6
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import random
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote_plus
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

log = logging.getLogger("forge.collectors.gdelt")

# ── Configuration ─────────────────────────────────────────────────────────────

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Targeted queries for South Africa OSINT
# Format: (query_string, stream, base_relevance)
# The DOC API supports full boolean: AND, OR, NOT, site: filters
SA_QUERIES: list[tuple[str, str, float]] = [
    # Crime / Law enforcement
    (
        '"South Africa" (Hawks OR NPA OR SAPS OR arrest OR conviction OR sentenced) '
        'NOT sport NOT cricket NOT rugby',
        "CRIME_INTEL", 1.4,
    ),
    (
        '"South Africa" (corruption OR "state capture" OR tender OR fraud) '
        'NOT sport',
        "CRIME_INTEL", 1.3,
    ),
    # Infrastructure / Services
    (
        '"South Africa" (Eskom OR loadshedding OR "load shedding" OR "power outage" '
        'OR "water outage" OR municipality)',
        "INFRASTRUCTURE", 1.2,
    ),
    # Political / Civil unrest
    (
        '"South Africa" (protest OR "civil unrest" OR strike OR shutdown OR riots) '
        'NOT student NOT campus',
        "GLOBAL", 1.0,
    ),
    # High-priority intelligence
    (
        '"South Africa" (investigation OR "leaked documents" OR "whistleblower" '
        'OR intelligence OR surveillance)',
        "PRIORITY", 1.3,
    ),
]

# Number of articles per query (DOC API max: 250)
MAX_ARTICLES_PER_QUERY = 75

# Look-back window for articles (GDELT DOC API supports: 15min to 3months)
DEFAULT_LOOKBACK_HOURS = 24

# Fixed credibility for GDELT DOC (secondary source aggregator)
SOURCE_CREDIBILITY = 0.60

# Keywords that boost is_priority flag
PRIORITY_KEYWORDS = {
    "Hawks", "NPA", "arrest", "raid", "shoot", "killed", "murder",
    "explosion", "bomb", "attack", "protest", "shutdown", "strike",
    "collapsed", "Eskom", "blackout", "loadshedding",
}


# ── Geo-Aware Leaky Bucket throttle ──────────────────────────────────────────

class GeoAwareLeakyBucket:
    """
    Synchronous rate-limiter with per-tier parameters.

    SA tier (*.co.za / *.gov.za / *.org.za / *.ac.za / known SA outlets):
        max 3 req/s — 333 ms min interval + 800–1200 ms random jitter.
    Global tier:
        max 5 req/s — 200 ms min interval + 200–500 ms random jitter.

    Rationale: SA government/news sites sit behind Cloudflare-style WAFs
    that are aggressive on burst traffic; the extra jitter mimics human
    browsing pacing and avoids the write-lock death spiral on 429s.
    """

    _SA_TLDS = frozenset([".co.za", ".gov.za", ".org.za", ".ac.za", ".net.za"])
    _SA_DOMAINS = frozenset([
        "news24.com", "businesslive.co.za", "businesstech.co.za",
        "iol.co.za", "dailymaverick.co.za", "groundup.org.za",
        "sabcnews.com", "ewn.co.za", "timeslive.co.za",
        "citizen.co.za", "politicsweb.co.za", "702.co.za",
        "capetalk.co.za", "dailysun.co.za", "sowetanlive.co.za",
    ])

    # (min_interval_ms, jitter_low_ms, jitter_high_ms)
    _SA_PARAMS     = (333, 800, 1200)
    _GLOBAL_PARAMS = (200, 200, 500)

    def __init__(self):
        self._last_ts: dict[str, float] = {}

    def _tier(self, domain: str) -> tuple[int, int, int]:
        d = (domain or "").lower()
        if any(d.endswith(t) for t in self._SA_TLDS):
            return self._SA_PARAMS
        if d in self._SA_DOMAINS:
            return self._SA_PARAMS
        return self._GLOBAL_PARAMS

    def drain(self, domain: str = "") -> None:
        """Block until the bucket allows the next request for this domain."""
        min_ms, jitter_lo, jitter_hi = self._tier(domain)
        now   = time.monotonic()
        last  = self._last_ts.get(domain, 0.0)
        gap   = (now - last) * 1000          # ms elapsed since last request
        wait  = (min_ms - gap) + random.uniform(jitter_lo, jitter_hi)
        if wait > 0:
            time.sleep(wait / 1000)
        self._last_ts[domain] = time.monotonic()

    async def async_drain(self, domain: str = "") -> None:
        """Async variant — yields to the event loop during the wait."""
        min_ms, jitter_lo, jitter_hi = self._tier(domain)
        now   = time.monotonic()
        last  = self._last_ts.get(domain, 0.0)
        gap   = (now - last) * 1000
        wait  = (min_ms - gap) + random.uniform(jitter_lo, jitter_hi)
        if wait > 0:
            await asyncio.sleep(wait / 1000)
        self._last_ts[domain] = time.monotonic()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2] / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── GDELT DOC API client ──────────────────────────────────────────────────────

class GDELTDocClient:

    def fetch_articles(
        self,
        query: str,
        max_records: int = MAX_ARTICLES_PER_QUERY,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    ) -> list[dict]:
        """
        Query the GDELT DOC API and return a list of article dicts.

        DOC API parameters used:
          query        keyword query string
          mode         ArtList (article metadata list, not full text)
          maxrecords   1-250
          timespan     e.g. "24h", "6h"
          sort         DateDesc
          format       json
        """
        timespan = f"{lookback_hours}h"

        params = {
            "query":      query,
            "mode":       "ArtList",
            "maxrecords": str(max_records),
            "timespan":   timespan,
            "sort":       "DateDesc",
            "format":     "json",
        }

        url = f"{GDELT_DOC_API}?{urlencode(params)}"
        log.debug(f"GDELT DOC fetch: {url[:120]}...")

        req = Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        try:
            with urlopen(req, timeout=20) as resp:
                raw = resp.read()
                data = json.loads(raw)
                return data.get("articles", [])
        except HTTPError as e:
            raise RuntimeError(f"GDELT HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            raise RuntimeError(f"GDELT network error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"GDELT JSON parse error: {e}") from e


# ── Signal builder ────────────────────────────────────────────────────────────

def _is_priority(title: str, snippet: str) -> int:
    combined = (title + " " + snippet).lower()
    return 1 if any(kw.lower() in combined for kw in PRIORITY_KEYWORDS) else 0


def _clean_text(raw: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    cleaned = re.sub(r"<[^>]+>", " ", raw or "")
    return " ".join(cleaned.split())[:500]


def _build_signal(article: dict, stream: str, base_relevance: float) -> Optional[dict]:
    """
    Map one GDELT DOC API article dict to a FORGE signals row.
    Returns None if the article lacks the minimum required fields.

    GDELT DOC API article fields:
      url, title, seendate, socialimage, domain, language,
      sourcecountry, sentiment (optional)
    """
    url     = article.get("url", "").strip()
    title   = _clean_text(article.get("title", ""))
    snippet = _clean_text(article.get("seendescription", ""))
    domain  = article.get("domain", "")
    lang    = article.get("language", "English")
    raw_ts  = article.get("seendate", "")     # "20240415T123000Z"

    if not url or not title:
        return None

    # Stable dedup key from URL hash
    ext_id    = f"gdelt-doc:{hashlib.sha1(url.encode()).hexdigest()[:16]}"
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ext_id))

    # Parse GDELT timestamp format: "20240415T123000Z"
    try:
        timestamp = (
            datetime.strptime(raw_ts, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except ValueError:
        timestamp = datetime.now(timezone.utc).isoformat()

    # GDELT DOC API provides sentiment in some modes (-1 to +1)
    try:
        sentiment = float(article.get("sentiment", -0.1))
    except (TypeError, ValueError):
        sentiment = -0.1   # slight negative default — OSINT skews negative

    # Severity: GDELT articles don't have fatality counts so we use
    # relevance as a proxy — higher base_relevance → higher urgency
    severity = round(min(base_relevance / 2.0, 0.75), 4)

    # Content = snippet or title fallback
    content = snippet if snippet else title

    metadata = {
        "url":            url,
        "domain":         domain,
        "language":       lang,
        "source_country": article.get("sourcecountry", ""),
        "social_image":   article.get("socialimage", ""),
        # Pre-computed gravity inputs
        "severity":           severity,
        "actor_importance":   0.3,      # unknown without NER — ingest will update
        "sentiment":          sentiment,
        "source_credibility": SOURCE_CREDIBILITY,
    }

    return {
        "signal_id":        signal_id,
        "source":           "gdelt",
        "external_id":      ext_id,
        "title":            title[:200],
        "content":          content,
        "lat":              None,   # GDELT DOC doesn't geo-code articles
        "lng":              None,
        "timestamp":        timestamp,
        "status":           "raw",
        "is_priority":      _is_priority(title, snippet),
        "stream":           stream,
        "source_type":      "live",
        "relevance_score":  round(base_relevance / 1.5, 4),
        "metadata_json":    json.dumps(metadata, ensure_ascii=False),
        # Gravity inputs for ingest.py score_signal()
        "severity":           severity,
        "actor_importance":   0.3,
        "sentiment":          sentiment,
        "source_credibility": SOURCE_CREDIBILITY,
        "frequency":          0.0,
    }


# ── DB write ──────────────────────────────────────────────────────────────────

def _insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    inserted = 0
    skipped  = 0
    for sig in signals:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority,
                     stream, source_type, relevance_score, metadata_json)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :is_priority,
                     :stream, :source_type, :relevance_score, :metadata_json)
            """, sig)
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            log.warning(f"Insert error {sig.get('external_id')}: {exc}")
            skipped += 1
    conn.commit()
    return inserted, skipped


def _log_run(db_path, status, records_in, records_out, duration_s, detail):
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "gdelt_collector", status, records_in, records_out,
            round(duration_s, 2), json.dumps(detail, ensure_ascii=False),
        ))
        conn.commit()
        conn.close()
    except Exception as exc:
        log.debug(f"pipeline_runs log failed: {exc}")


# ── Main collector ────────────────────────────────────────────────────────────

class GDELTDocCollector:

    def __init__(
        self,
        db_path: Optional[Path] = None,
        queries: Optional[list[tuple[str, str, float]]] = None,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    ):
        self.db_path       = db_path or _resolve_db()
        self.queries       = queries or SA_QUERIES
        self.lookback_hours = lookback_hours

    def run(self, dry_run: bool = False) -> dict:
        start    = time.monotonic()
        client   = GDELTDocClient()
        throttle = GeoAwareLeakyBucket()
        all_sigs: list[dict] = []
        errors  : list[str]  = []

        log.info(
            f"[gdelt_collector] Starting: {len(self.queries)} queries, "
            f"lookback={self.lookback_hours}h, dry_run={dry_run}"
        )

        # GDELT DOC API lives on gdeltproject.org — classified Global tier
        _GDELT_DOMAIN = "gdeltproject.org"

        for query_str, stream, base_rel in self.queries:
            try:
                # Geo-aware throttle: respects per-tier rate + jitter before
                # each request to avoid 429 write-lock death spirals
                throttle.drain(_GDELT_DOMAIN)

                log.info(f"[gdelt_collector] Query: {query_str[:60]}...")
                articles = client.fetch_articles(
                    query_str,
                    lookback_hours=self.lookback_hours,
                )
                built = 0
                for art in articles:
                    sig = _build_signal(art, stream, base_rel)
                    if sig:
                        all_sigs.append(sig)
                        built += 1
                log.info(
                    f"[gdelt_collector] → {len(articles)} articles, "
                    f"{built} signals built ({stream})"
                )
            except Exception as exc:
                msg = f"Query failed: {exc}"
                log.warning(f"[gdelt_collector] WARN {msg}")
                errors.append(msg)
                # P3-04: 429 Retry-After backoff — parse from HTTPError directly
                # (urllib raises HTTPError, not requests.Response, so check .code)
                backoff = 0
                if isinstance(exc, RuntimeError) and "GDELT HTTP 429" in str(exc):
                    backoff = 30   # conservative default when no Retry-After header
                elif hasattr(exc, "__cause__") and isinstance(exc.__cause__, HTTPError):
                    if exc.__cause__.code == 429:
                        try:
                            backoff = int(
                                exc.__cause__.headers.get("Retry-After", 30)
                            )
                        except (ValueError, TypeError):
                            backoff = 30
                backoff = max(0, min(backoff, 60))
                if backoff > 0:
                    log.warning(
                        f"[gdelt_collector] 429 rate-limited — "
                        f"backing off {backoff}s (Retry-After)"
                    )
                    time.sleep(backoff)

        total_fetched = len(all_sigs)
        inserted = 0
        skipped  = 0

        if dry_run:
            log.info(
                f"[gdelt_collector] DRY RUN — {total_fetched} signals not written"
            )
            for s in all_sigs[:3]:
                log.info(
                    f"  SAMPLE [{s['stream']}]: {s['title'][:80]}"
                )
        else:
            conn = _open_db(self.db_path)
            try:
                inserted, skipped = _insert_signals(conn, all_sigs)
            finally:
                conn.close()
            log.info(
                f"[gdelt_collector] Written: {inserted} new, {skipped} skipped"
            )

        duration = round(time.monotonic() - start, 2)
        result   = {
            "status":      "success" if not errors else "partial",
            "fetched":     total_fetched,
            "inserted":    inserted,
            "skipped":     skipped,
            "errors":      errors,
            "dry_run":     dry_run,
            "duration_s":  duration,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        if not dry_run:
            _log_run(
                self.db_path,
                status="success" if not errors else "error",
                records_in=total_fetched,
                records_out=inserted,
                duration_s=duration,
                detail=result,
            )

        return result


# ── Async wrapper ─────────────────────────────────────────────────────────────

async def collect(
    db_path: Optional[Path] = None,
    queries: Optional[list] = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> dict:
    """Async entry point for mega_ingest.run_all_collectors()."""
    loop = asyncio.get_event_loop()
    collector = GDELTDocCollector(
        db_path=db_path,
        queries=queries,
        lookback_hours=lookback_hours,
    )
    return await loop.run_in_executor(None, collector.run)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="FORGE GDELT DOC API Collector — SA-focused news signals"
    )
    parser.add_argument("--hours",   type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--query",   type=str, default=None,
                        help="Override with a single custom query string")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db",      type=str, default=None)
    args = parser.parse_args()

    queries = (
        [(args.query, "GLOBAL", 1.0)]
        if args.query
        else SA_QUERIES
    )

    collector = GDELTDocCollector(
        db_path=_resolve_db(args.db),
        queries=queries,
        lookback_hours=args.hours,
    )
    result = collector.run(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("success", "partial") else 1)
