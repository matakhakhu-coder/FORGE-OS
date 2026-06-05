#!/usr/bin/env python3
from __future__ import annotations

"""
forage/collectors/saflii_collector.py
======================================
Phase P4 — SAFLII Court Record Collector

Queries the Southern African Legal Information Institute (SAFLII)
for court cases involving actors in active FORGE cases.

Architecture principle: SAFLII provides EXTERNAL SCHEMA AUTHORITY.
A SAFLII case number (e.g. [2024] ZAGPPHC 441) is an immutable,
verifiable external identifier. Relationships that carry a SAFLII
case number as provenance are structurally verified — you cannot
fabricate a case number that resolves on saflii.org.

This is the forcing function that makes the entity graph dense with
meaning rather than sparse with guesses.

Strategy:
  1. Load all actors linked to active cases (confidence >= 0.35)
  2. For each actor, query Google News RSS with site:saflii.org
  3. Parse RSS results → extract case numbers, court, year
  4. Write signals with source='saflii', stream='CRIME_INTEL'
  5. Store case_ref in metadata_json for downstream relationship use

Deduplication: external_id = "saflii:{sha1(url)[:16]}"
Rate limit: 1 request per 3 seconds (SAFLII is a public resource)
Max actors per run: 15

Usage:
    python forage/collectors/saflii_collector.py
    python forage/collectors/saflii_collector.py --dry-run
    python forage/collectors/saflii_collector.py --actor "Fadiel Adams"
"""

__manifest__ = {
    "id":          "saflii_collector",
    "name":        "SAFLII Court Record Collector",
    "description": "Queries SAFLII for court records involving actors in active cases. Provides external schema authority for entity relationship verification.",
    "icon":        "⚖",
    "entry":       "forage/collectors/saflii_collector.py",
    "args":        ["--dry-run", "--actor"],
    "job_key":     "saflii_collector",
    "version":     "1.0.0",
}

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("forge.saflii_collector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH     = Path(__file__).resolve().parent.parent.parent / "database.db"
REQ_DELAY   = 3.0    # seconds between requests
MAX_ACTORS  = 15     # actors per run
UA          = "FORGE-OSINT/1.1 (legal research; non-commercial)"

# Regex to extract SAFLII case references from text
# Matches patterns like: [2024] ZAGPPHC 441  |  2024 (45) SA 123 (GP)
CASE_REF_RE = re.compile(
    r'\[?\d{4}\]?\s+(?:ZA\w+|ZACC|ZAECB|ZAECG|ZAECPEHC|ZAECQBHC|ZALC|ZANWHC|ZANWM|ZAWCHC)\s+\d+'
    r'|\d{4}\s+\(\d+\)\s+(?:SA|BCLR|All SA)\s+\d+\s+\(\w+\)',
    re.I
)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en"


def _make_external_id(url: str) -> str:
    return "saflii:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def _fetch_rss(query: str) -> list[dict]:
    """Query Google News RSS for SAFLII records. Returns list of item dicts."""
    encoded = urllib.parse.quote_plus(f'site:saflii.org {query}')
    url = GOOGLE_NEWS_RSS.format(query=encoded)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            link  = item.findtext("link")  or ""
            desc  = item.findtext("description") or ""
            pub   = item.findtext("pubDate") or ""
            if "saflii" in link.lower() or "saflii" in title.lower():
                items.append({"title": title, "link": link, "desc": desc, "pub": pub})
        return items
    except Exception as exc:
        _log.debug("SAFLII RSS fetch failed for %r: %s", query, exc)
        return []


def _extract_case_ref(text: str) -> str | None:
    m = CASE_REF_RE.search(text)
    return m.group(0).strip() if m else None


def _write_signal(
    conn: sqlite3.Connection,
    actor_name: str,
    item: dict,
    case_ref: str | None,
    dry_run: bool,
) -> bool:
    ext_id = _make_external_id(item["link"])
    existing = conn.execute(
        "SELECT 1 FROM signals WHERE external_id = ?", (ext_id,)
    ).fetchone()
    if existing:
        return False

    import uuid
    sig_id   = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()
    metadata = {"actor_query": actor_name, "case_ref": case_ref, "source_url": item["link"]}
    content  = re.sub(r"<[^>]+>", " ", item["desc"])[:500]

    if not dry_run:
        conn.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, source, external_id, title, content,
                stream, status, source_type, timestamp, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                sig_id, "saflii", ext_id,
                item["title"][:300], content,
                "CRIME_INTEL", "raw", "live", now,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        conn.commit()
        _log.info("  [SAFLII] Stored: %s | ref=%s", item["title"][:60], case_ref)
    else:
        _log.info("  [DRY] Would store: %s | ref=%s", item["title"][:60], case_ref)
    return True


def run(actor_name: str | None = None, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    # Load actors from active cases
    if actor_name:
        actors = [{"name": actor_name}]
    else:
        actors = conn.execute(
            """SELECT DISTINCT a.name
               FROM actors a
               JOIN case_actors ca ON a.actor_id = ca.actor_id
               JOIN cases c ON ca.case_id = c.case_id
               WHERE c.status != 'closed'
                 AND a.confidence_score >= 0.35
                 AND a.type IN ('person','institution','paramilitary','government')
               ORDER BY a.confidence_score DESC
               LIMIT ?""",
            (MAX_ACTORS,),
        ).fetchall()

    stats = {"actors_queried": 0, "signals_found": 0, "signals_written": 0}

    for actor in actors:
        name = actor["name"]
        _log.info("Querying SAFLII for: %s", name)
        stats["actors_queried"] += 1

        items = _fetch_rss(name)
        stats["signals_found"] += len(items)

        for item in items[:5]:  # max 5 results per actor
            case_ref = _extract_case_ref(f"{item['title']} {item['desc']}")
            if _write_signal(conn, name, item, case_ref, dry_run):
                stats["signals_written"] += 1

        time.sleep(REQ_DELAY)

    conn.close()
    _log.info("SAFLII collection complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE SAFLII Collector")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--actor",   type=str, default=None, help="Query a specific actor name")
    args = parser.parse_args()
    print(run(actor_name=args.actor, dry_run=args.dry_run))
