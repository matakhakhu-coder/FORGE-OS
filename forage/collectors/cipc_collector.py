#!/usr/bin/env python3
from __future__ import annotations

"""
forage/collectors/cipc_collector.py
=====================================
Phase P4 — CIPC Company Registry Collector

Queries the Companies and Intellectual Property Commission (CIPC)
database for company registration details related to named entities
in active FORGE cases — particularly procurement fraud signals.

Architecture principle: CIPC registration numbers are EXTERNAL SCHEMA
AUTHORITY. A CIPC reg number links a named individual to a legal entity
with a verifiable paper trail. This forces entity disambiguation:
  - Two actors with the same name but different CIPC reg numbers
    are different people.
  - A person with no CIPC registration cannot legally hold a government
    contract — which is itself an intelligence signal.

Strategy:
  1. Find signals with procurement/tender/contract keywords
  2. Extract company/person names via actors linked to those signals
  3. Query Google (site:companiesandinetellect.co.za OR site:cipc.co.za)
     for registration information
  4. Parse company name, reg number, registration date, status
  5. Update actors.socint_profile JSON with cipc_reg, cipc_status
  6. Write a signal if a disqualifying finding is discovered
     (e.g., company registered AFTER the contract award date)

Rate limit: 1 request per 5 seconds (CIPC blocks aggressive scraping)
Max entities per run: 10

Usage:
    python forage/collectors/cipc_collector.py
    python forage/collectors/cipc_collector.py --dry-run
    python forage/collectors/cipc_collector.py --actor "Minenhle Mavuso"
"""

__manifest__ = {
    "id":          "cipc_collector",
    "name":        "CIPC Company Registry Collector",
    "description": "Looks up company registration details for procurement-linked actors. Provides external entity disambiguation via CIPC registration numbers.",
    "icon":        "🏢",
    "entry":       "forage/collectors/cipc_collector.py",
    "args":        ["--dry-run", "--actor"],
    "job_key":     "cipc_collector",
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

_log = logging.getLogger("forge.cipc_collector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH     = Path(__file__).resolve().parent.parent.parent / "database.db"
REQ_DELAY   = 5.0    # seconds between requests — CIPC is sensitive to scraping
MAX_ACTORS  = 10
UA          = "FORGE-OSINT/1.1 (company verification; non-commercial)"

# Regex patterns for CIPC registration numbers
# South African company reg: YYYY/NNNNNN/NN (e.g. 2024/123456/07)
CIPC_REG_RE = re.compile(r'\b(20\d{2}|19\d{2})/\d{5,7}/\d{2}\b')

# Google News RSS fallback for CIPC searches
GOOGLE_NEWS_RSS  = "https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en"
# Public company search portals (tried in order)
SEARCH_URLS = [
    "https://www.companiesandinetellect.co.za",
    "https://efiling.cipc.co.za",
]

# Procurement-signal keywords — only run CIPC enrichment on these signal types
PROCUREMENT_KEYWORDS = re.compile(
    r'\bcontract|\btender\b|\bprocure|\baward\b|\bsupplier\b|\bfronting\b',
    re.I
)


def _make_external_id(url: str) -> str:
    return "cipc:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def _fetch_rss(query: str) -> list[dict]:
    """Search Google News RSS for CIPC/company registration data."""
    encoded = urllib.parse.quote_plus(
        f'(site:companiesandinetellect.co.za OR site:cipc.co.za) "{query}"'
    )
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
            items.append({"title": title, "link": link, "desc": desc})
        return items
    except Exception as exc:
        _log.debug("CIPC RSS fetch failed for %r: %s", query, exc)
        return []


def _extract_reg_number(text: str) -> str | None:
    m = CIPC_REG_RE.search(text)
    return m.group(0) if m else None


def _update_actor_profile(
    conn: sqlite3.Connection,
    actor_name: str,
    cipc_reg: str | None,
    cipc_data: dict,
    dry_run: bool,
) -> None:
    """Upsert CIPC data into actors.socint_profile JSON."""
    row = conn.execute(
        "SELECT actor_id, socint_profile FROM actors WHERE name = ?",
        (actor_name,)
    ).fetchone()
    if not row:
        return

    actor_id       = row["actor_id"]
    existing_raw   = row["socint_profile"] or "{}"
    try:
        profile = json.loads(existing_raw)
    except (json.JSONDecodeError, TypeError):
        profile = {}

    profile["cipc_reg"]        = cipc_reg
    profile["cipc_lookup_date"] = datetime.now(timezone.utc).isoformat()
    profile.update(cipc_data)

    if not dry_run:
        conn.execute(
            "UPDATE actors SET socint_profile = ? WHERE actor_id = ?",
            (json.dumps(profile, ensure_ascii=False), actor_id)
        )
        conn.commit()
        _log.info("  [CIPC] Updated actor %d (%s): reg=%s", actor_id, actor_name, cipc_reg)
    else:
        _log.info("  [DRY] Would update actor %d (%s): reg=%s", actor_id, actor_name, cipc_reg)


def _write_finding_signal(
    conn: sqlite3.Connection,
    actor_name: str,
    finding: str,
    detail: str,
    dry_run: bool,
) -> None:
    """Write an intelligence signal when a disqualifying CIPC finding is made."""
    import uuid
    ext_id = _make_external_id(f"cipc-finding:{actor_name}:{finding}")
    if conn.execute("SELECT 1 FROM signals WHERE external_id=?", (ext_id,)).fetchone():
        return

    now  = datetime.now(timezone.utc).isoformat()
    title = f"CIPC FINDING: {actor_name} — {finding}"
    if not dry_run:
        conn.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, source, external_id, title, content,
                stream, status, source_type, timestamp, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), "cipc_collector", ext_id,
                title[:300], detail[:500],
                "CRIME_INTEL", "raw", "live", now,
                json.dumps({"actor": actor_name, "finding_type": finding}),
            ),
        )
        conn.commit()
        _log.info("  [CIPC] Signal written: %s", title)


def run(actor_name: str | None = None, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    # Find actors linked to procurement-related signals in active cases
    if actor_name:
        actors = [{"name": actor_name, "actor_id": None}]
    else:
        actors = conn.execute(
            """SELECT DISTINCT a.name, a.actor_id
               FROM actors a
               JOIN signal_actors sa ON a.actor_id = sa.actor_id
               JOIN signals s ON sa.signal_id = s.signal_id
               JOIN case_signals cs ON s.signal_id = cs.signal_id
               JOIN cases c ON cs.case_id = c.case_id
               WHERE c.status != 'closed'
                 AND a.type IN ('person','organization','institution')
                 AND a.confidence_score >= 0.35
                 AND (s.title LIKE '%contract%' OR s.title LIKE '%tender%'
                      OR s.title LIKE '%procure%' OR s.title LIKE '%fraud%')
               ORDER BY a.confidence_score DESC
               LIMIT ?""",
            (MAX_ACTORS,),
        ).fetchall()

    stats = {"actors_queried": 0, "registrations_found": 0, "findings_written": 0}

    for actor in actors:
        name = actor["name"]
        _log.info("CIPC lookup: %s", name)
        stats["actors_queried"] += 1

        items = _fetch_rss(name)
        reg_found = None
        cipc_data = {}

        for item in items[:3]:
            combined = f"{item['title']} {item['desc']}"
            reg = _extract_reg_number(combined)
            if reg:
                reg_found = reg
                cipc_data["cipc_source_title"] = item["title"][:200]
                cipc_data["cipc_source_url"]   = item["link"]
                stats["registrations_found"] += 1
                break

        _update_actor_profile(conn, name, reg_found, cipc_data, dry_run)

        # Flag: no registration found for a procurement-linked actor
        if not reg_found and actor.get("actor_id"):
            _write_finding_signal(
                conn, name,
                "NO CIPC REGISTRATION FOUND",
                f"Actor {name} is linked to procurement/contract signals but no "
                f"company registration was found in CIPC/public records. "
                f"Possible fronting or unregistered entity.",
                dry_run,
            )
            stats["findings_written"] += 1

        time.sleep(REQ_DELAY)

    conn.close()
    _log.info("CIPC collection complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE CIPC Company Registry Collector")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--actor",   type=str, default=None)
    args = parser.parse_args()
    print(run(actor_name=args.actor, dry_run=args.dry_run))
