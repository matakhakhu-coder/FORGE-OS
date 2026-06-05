#!/usr/bin/env python3
from __future__ import annotations

"""
forage/processors/content_enricher.py
======================================
Phase P2 — Content Enrichment Worker

Drains the enrichment_queue table. For each queued signal:
  1. Fetches the article URL (stored in external_id / enrichment_queue.url)
  2. Strips HTML to plain text using stdlib html.parser
  3. Updates signals.content with the enriched text (capped at 2000 chars)
  4. Re-runs gravity scoring on the enriched signal
  5. Marks the queue entry as 'done' or 'failed'

Rate limiting:  1 request / second per domain (token bucket, in-memory)
Retry policy:   max 1 attempt per signal. Failed signals are marked 'failed'
                and excluded from future runs unless manually reset.
Constraint:     This worker NEVER runs inline with ingest_signal().
                It runs as a standalone batch job or scheduled worker.
                A failure here cannot affect the ingest pipeline.

Usage:
    python forage/processors/content_enricher.py
    python forage/processors/content_enricher.py --limit 50
    python forage/processors/content_enricher.py --dry-run
"""

__manifest__ = {
    "id":          "content_enricher",
    "name":        "Content Enricher",
    "description": "Fetches full article text for stub-length RSS signals and re-scores gravity.",
    "icon":        "◎",
    "entry":       "forage/processors/content_enricher.py",
    "args":        ["--limit", "--dry-run"],
    "job_key":     "content_enricher",
    "version":     "1.0.0",
}

import argparse
import html
import logging
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

_log = logging.getLogger("forge.content_enricher")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH   = Path(__file__).resolve().parent.parent.parent / "database.db"
BATCH_CAP = 200         # max signals per run
REQ_DELAY = 1.0         # seconds between requests to the same domain
CONTENT_CAP = 2000      # max chars to store in signals.content
MIN_BODY    = 150       # minimum extracted chars to count as successful fetch
TIMEOUT     = 10        # HTTP request timeout (seconds)

# User-agent: identifies as FORGE research tool
UA = "FORGE-OSINT/1.1 (research; non-commercial; +https://github.com/forge-osint)"

# ── HTML → plaintext stripper ─────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside",
                 "noscript", "figure", "figcaption", "button", "form",
                 "iframe", "svg", "meta", "link"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        raw = " ".join(self._parts)
        # Collapse whitespace
        raw = re.sub(r"\s{2,}", " ", raw)
        # Unescape HTML entities
        raw = html.unescape(raw)
        return raw.strip()


def _strip_html(markup: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(markup)
    except Exception:
        pass
    return p.get_text()


# ── Domain-level rate limiter (in-memory token bucket) ───────────────────────

_last_request: dict[str, float] = {}

def _rate_wait(domain: str) -> None:
    now = time.monotonic()
    last = _last_request.get(domain, 0.0)
    wait = REQ_DELAY - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request[domain] = time.monotonic()


# ── robots.txt cache ──────────────────────────────────────────────────────────

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

def _can_fetch(url: str) -> bool:
    parsed = urlparse(url)
    base   = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots.txt is unreachable, assume allowed
            rp.allow_all = True
        _robots_cache[base] = rp
    return _robots_cache[base].can_fetch(UA, url)


# ── HTTP fetch ────────────────────────────────────────────────────────────────

def _fetch(url: str) -> str | None:
    """Fetch URL, return raw HTML or None on failure."""
    domain = urlparse(url).netloc
    if not _can_fetch(url):
        _log.debug("robots.txt disallows: %s", url)
        return None
    _rate_wait(domain)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "text/html" not in ct and "text/plain" not in ct:
                return None
            raw = resp.read(512_000)   # cap at 500 KB
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].split(";")[0].strip()
            return raw.decode(charset, errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, Exception) as exc:
        _log.debug("Fetch failed %s: %s", url, exc)
        return None


# ── Gravity re-score ──────────────────────────────────────────────────────────

def _rescore(signal_id: str, new_content: str, conn: sqlite3.Connection) -> float | None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from forage.engines.gravity_engine import score_signal as _score
        row = conn.execute(
            "SELECT signal_id, source, title, stream, lat, lng, metadata_json"
            " FROM signals WHERE signal_id = ?",
            (signal_id,)
        ).fetchone()
        if not row:
            return None
        sig = dict(row)
        sig["content"] = new_content
        result = _score(sig)
        new_g = result.get("gravity_score")
        if new_g is not None:
            conn.execute(
                "UPDATE signals SET gravity_score = ? WHERE signal_id = ?",
                (round(float(new_g), 6), signal_id)
            )
        return new_g
    except Exception as exc:
        _log.warning("Rescore failed %s: %s", signal_id, exc)
        return None


# ── Main worker ───────────────────────────────────────────────────────────────

def run(limit: int = BATCH_CAP, dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT signal_id, url, source FROM enrichment_queue"
        " WHERE status = 'pending'"
        " ORDER BY queued_at ASC LIMIT ?",
        (limit,)
    ).fetchall()

    stats = {"attempted": 0, "enriched": 0, "failed": 0, "skipped": 0}

    for row in rows:
        sig_id = row["signal_id"]
        url    = row["url"]
        source = row["source"]

        if not url:
            conn.execute(
                "UPDATE enrichment_queue SET status='failed', attempted_at=?, error=?"
                " WHERE signal_id=?",
                (now, "no_url", sig_id)
            )
            stats["failed"] += 1
            continue

        stats["attempted"] += 1
        _log.info("[%d/%d] Fetching %s...", stats["attempted"], len(rows), url[:80])

        if dry_run:
            _log.info("  DRY RUN — skipping HTTP")
            stats["skipped"] += 1
            continue

        html_raw = _fetch(url)
        if not html_raw:
            conn.execute(
                "UPDATE enrichment_queue SET status='failed', attempted_at=?, error=?"
                " WHERE signal_id=?",
                (now, "fetch_failed", sig_id)
            )
            conn.commit()
            stats["failed"] += 1
            continue

        text = _strip_html(html_raw)
        if len(text) < MIN_BODY:
            conn.execute(
                "UPDATE enrichment_queue SET status='failed', attempted_at=?, error=?"
                " WHERE signal_id=?",
                (now, "body_too_short", sig_id)
            )
            conn.commit()
            stats["failed"] += 1
            continue

        capped = text[:CONTENT_CAP]

        # Update signal content
        conn.execute(
            "UPDATE signals SET content = ? WHERE signal_id = ?",
            (capped, sig_id)
        )

        # Re-score gravity on enriched content
        new_gravity = _rescore(sig_id, capped, conn)

        conn.execute(
            "UPDATE enrichment_queue SET status='done', attempted_at=? WHERE signal_id=?",
            (now, sig_id)
        )
        conn.commit()
        stats["enriched"] += 1
        _log.info("  Enriched [%s] new_gravity=%s", sig_id[:12], new_gravity)

    conn.close()
    _log.info("Content enrichment complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE Content Enricher")
    parser.add_argument("--limit",   type=int,  default=BATCH_CAP, help="Max signals per run")
    parser.add_argument("--dry-run", action="store_true",          help="Fetch URLs but don't write")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
