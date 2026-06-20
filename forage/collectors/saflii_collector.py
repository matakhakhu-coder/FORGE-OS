#!/usr/bin/env python3
from __future__ import annotations

"""
forage/collectors/saflii_collector.py
======================================
SAFLII Court Record Collector — Tier 2

Queries SAFLII (via Google News RSS proxy — SAFLII itself is behind Cloudflare)
for court cases involving actors and case subjects in active FORGE cases.

Features (Tier 1):
  - Searches actor names AND case names
  - Auto-scores signals with gravity engine logic at write time
  - Auto-links signals to cases via case_signals
  - Auto-links signals to actors via signal_actors
  - Configurable limits via CLI

Features (Tier 2):
  - Multi-actor matching: each result checked against ALL actors
  - Enhanced search queries: quoted names, court-specific searches
  - Party extraction from "X v Y" case titles → entity_relationships
  - Google Scholar as secondary source for legal citations

Usage:
    python forage/collectors/saflii_collector.py
    python forage/collectors/saflii_collector.py --dry-run
    python forage/collectors/saflii_collector.py --actor "Fadiel Adams"
    python forage/collectors/saflii_collector.py --max-actors 30 --max-results 10
"""

__manifest__ = {
    "id":          "saflii_collector",
    "name":        "SAFLII Court Record Collector",
    "description": "Queries SAFLII for court records involving actors in active cases. Auto-scores, auto-links, and extracts party relationships.",
    "icon":        "⚖",
    "entry":       "forage/collectors/saflii_collector.py",
    "args":        ["--dry-run", "--actor", "--max-actors", "--max-results"],
    "job_key":     "saflii_collector",
    "version":     "2.1.0",
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
REQ_DELAY   = 3.0
UA          = "FORGE-OSINT/2.1 (legal research; non-commercial)"

CASE_REF_RE = re.compile(
    r'\[?\d{4}\]?\s+(?:ZA\w+|ZACC|ZAECB|ZAECG|ZAECPEHC|ZAECQBHC|ZALC|ZANWHC|ZANWM|ZAWCHC)\s+\d+'
    r'|\d{4}\s+\(\d+\)\s+(?:SA|BCLR|All SA)\s+\d+\s+\(\w+\)',
    re.I
)

PARTY_RE = re.compile(r'^(.+?)\s+v\s+(.+?)(?:\s*\(|$)', re.I)

HIGH_COURTS = {'ZASCA', 'ZACC', 'ZAGPPHC', 'ZAGPJHC', 'ZALMPPHC', 'ZANWHC',
               'ZAWCHC', 'ZAEQC', 'ZALCPE', 'ZAECPEHC', 'ZAFSHC', 'ZAKZPHC',
               'ZALCCT', 'ZAECGHC', 'ZAKZDHC', 'ZALCJHB'}

# SA court code → geo coordinates (court physical location)
COURT_GEO: dict[str, tuple[float, float]] = {
    # Constitutional Court + Supreme Court of Appeal — Johannesburg / Bloemfontein
    "ZACC":      (-26.1952, 28.0486),   # Constitutional Court, Braamfontein, JHB
    "ZASCA":     (-29.1187, 26.2141),   # Supreme Court of Appeal, Bloemfontein
    # Gauteng
    "ZAGPPHC":   (-25.7479, 28.1876),   # Gauteng Division, Pretoria
    "ZAGPJHC":   (-26.2023, 28.0436),   # Gauteng Division, Johannesburg
    # Limpopo
    "ZALMPPHC":  (-23.9045, 29.4688),   # Limpopo Division, Polokwane
    # North West
    "ZANWHC":    (-25.8652, 25.6442),   # North West Division, Mahikeng
    # Western Cape
    "ZAWCHC":    (-33.9258, 18.4232),   # Western Cape Division, Cape Town
    # Eastern Cape
    "ZAECPEHC":  (-33.9608, 25.6022),   # Eastern Cape, Port Elizabeth (Gqeberha)
    "ZAECGHC":   (-33.3148, 26.5312),   # Eastern Cape, Grahamstown (Makhanda)
    # Free State
    "ZAFSHC":    (-29.1187, 26.2141),   # Free State Division, Bloemfontein
    # KwaZulu-Natal
    "ZAKZPHC":   (-29.8587, 31.0218),   # KZN Division, Pietermaritzburg
    "ZAKZDHC":   (-29.8587, 31.0218),   # KZN Division, Durban
    # Labour Courts
    "ZALCJHB":   (-26.2023, 28.0436),   # Labour Court, Johannesburg
    "ZALCCT":    (-33.9258, 18.4232),   # Labour Court, Cape Town
    "ZALCPE":    (-33.9608, 25.6022),   # Labour Court, Port Elizabeth
    # Equality Court
    "ZAEQC":     (-33.9258, 18.4232),   # Equality Court (usually WC or GP)
    # Competition Tribunal
    "ZACT":      (-25.7479, 28.1876),   # Competition Tribunal, Pretoria
}

# Fallback: if no court code matched, use Pretoria (seat of government)
COURT_GEO_DEFAULT = (-25.7479, 28.1876)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en"


def _court_geo(case_ref: str | None, title: str) -> tuple[float, float] | None:
    """Extract court geo from case reference or title."""
    text = f"{case_ref or ''} {title}"
    for code, coords in COURT_GEO.items():
        if code in text.upper():
            return coords
    # If no court code found but it's clearly a SA legal document
    if case_ref:
        return COURT_GEO_DEFAULT
    return None


def _make_external_id(url: str) -> str:
    return "saflii:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def _fetch_rss(query: str) -> list[dict]:
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
        _log.debug("RSS fetch failed for %r: %s", query, exc)
        return []


def _extract_case_ref(text: str) -> str | None:
    m = CASE_REF_RE.search(text)
    return m.group(0).strip() if m else None


def _extract_parties(title: str) -> tuple[str, str] | None:
    m = PARTY_RE.match(title.strip())
    if not m:
        return None
    plaintiff = re.sub(r'\s+and\s+(Others?|Another)\s*$', '', m.group(1).strip(), flags=re.I)
    defendant = re.sub(r'\s+and\s+(Others?|Another)\s*$', '', m.group(2).strip(), flags=re.I)
    plaintiff = re.sub(r'\s*\([^)]*\)\s*$', '', plaintiff).strip()
    defendant = re.sub(r'\s*\([^)]*\)\s*$', '', defendant).strip()
    if len(plaintiff) < 2 or len(defendant) < 2:
        return None
    return (plaintiff, defendant)


def _compute_gravity(title: str, case_ref: str | None, actor_name: str) -> float:
    score = 0.15
    if case_ref:
        score += 0.20
    title_upper = title.upper()
    if any(c in title_upper for c in HIGH_COURTS):
        score += 0.10
    actor_parts = actor_name.lower().split()
    title_lower = title.lower()
    if any(len(part) > 2 and part in title_lower for part in actor_parts):
        score += 0.15
    return min(round(score, 2), 0.65)


def _match_actors(text: str, all_actors: list[dict]) -> list[dict]:
    """Find all FORGE actors mentioned in a result's title/description."""
    text_lower = text.lower()
    matches = []
    for actor in all_actors:
        name_parts = actor["name"].lower().split()
        significant_parts = [p for p in name_parts if len(p) > 2]
        if significant_parts and all(p in text_lower for p in significant_parts):
            matches.append(actor)
    return matches


def _create_party_relationship(
    conn: sqlite3.Connection,
    plaintiff: str,
    defendant: str,
    case_ref: str | None,
    all_actors: list[dict],
    dry_run: bool,
) -> int:
    """If both parties match FORGE actors, create an entity_relationship edge."""
    plaintiff_lower = plaintiff.lower()
    defendant_lower = defendant.lower()

    plaintiff_actor = None
    defendant_actor = None

    for actor in all_actors:
        name_lower = actor["name"].lower()
        parts = [p for p in name_lower.split() if len(p) > 2]
        if parts and all(p in plaintiff_lower for p in parts):
            plaintiff_actor = actor
        if parts and all(p in defendant_lower for p in parts):
            defendant_actor = actor

    if not plaintiff_actor or not defendant_actor:
        return 0
    if plaintiff_actor["actor_id"] == defendant_actor["actor_id"]:
        return 0

    desc = f"Court case: {plaintiff} v {defendant}"
    if case_ref:
        desc += f" [{case_ref}]"

    if dry_run:
        _log.info("  [REL-DRY] %s --[LITIGATES]--> %s | %s",
                  plaintiff_actor["name"], defendant_actor["name"], case_ref or "no ref")
        return 1

    conn.execute("""
        INSERT OR IGNORE INTO entity_relationships
            (subject_actor_id, object_actor_id, relation_type, description, extraction_method)
        VALUES (?, ?, ?, ?, ?)
    """, (
        plaintiff_actor["actor_id"],
        defendant_actor["actor_id"],
        "LITIGATES_AGAINST",
        desc,
        "saflii_case_title",
    ))
    conn.commit()
    _log.info("  [REL] %s --[LITIGATES]--> %s | %s",
              plaintiff_actor["name"], defendant_actor["name"], case_ref or "no ref")
    return 1


def _write_signal(
    conn: sqlite3.Connection,
    actor_name: str,
    item: dict,
    case_ref: str | None,
    dry_run: bool,
    all_actors: list[dict],
    query_source: str = "actor",
    source_case_id: int | None = None,
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
    gravity  = _compute_gravity(item["title"], case_ref, actor_name)

    # Geo-tag from court code
    geo = _court_geo(case_ref, item["title"])
    lat = geo[0] if geo else None
    lng = geo[1] if geo else None

    # Multi-actor matching
    full_text = f"{item['title']} {item['desc']}"
    matched_actors = _match_actors(full_text, all_actors)

    metadata = {
        "actor_query": actor_name,
        "case_ref": case_ref,
        "source_url": item["link"],
        "query_source": query_source,
        "matched_actors": [a["name"] for a in matched_actors],
    }
    if geo:
        metadata["court_geo"] = {"lat": lat, "lng": lng}
    content = re.sub(r"<[^>]+>", " ", item["desc"])[:500]

    # Party extraction
    parties = _extract_parties(item["title"])
    if parties:
        metadata["plaintiff"] = parties[0]
        metadata["defendant"] = parties[1]

    if dry_run:
        geo_str = f"({lat:.1f},{lng:.1f})" if geo else "no-geo"
        actors_str = ", ".join(a["name"][:20] for a in matched_actors[:3]) or actor_name[:20]
        _log.info("  [DRY] G %.2f %s | %s | ref=%s | actors=[%s]",
                  gravity, geo_str, item["title"][:50], case_ref, actors_str)
        if parties:
            _create_party_relationship(conn, parties[0], parties[1], case_ref, all_actors, dry_run=True)
        return True

    conn.execute(
        """INSERT OR IGNORE INTO signals
           (signal_id, source, external_id, title, content,
            lat, lng,
            stream, status, source_type, timestamp, metadata_json,
            gravity_score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sig_id, "saflii", ext_id,
            item["title"][:300], content,
            lat, lng,
            "CRIME_INTEL", "raw", "live", now,
            json.dumps(metadata, ensure_ascii=False),
            gravity,
        ),
    )

    # Auto-link to cases — from source case OR from all matched actors' cases
    linked_cases = set()
    if source_case_id:
        linked_cases.add(source_case_id)

    for actor in matched_actors:
        case_rows = conn.execute(
            "SELECT case_id FROM case_actors WHERE actor_id = ?",
            (actor["actor_id"],),
        ).fetchall()
        for row in case_rows:
            linked_cases.add(row["case_id"])

    if not linked_cases:
        case_rows = conn.execute(
            """SELECT ca.case_id FROM case_actors ca
               JOIN actors a ON a.actor_id = ca.actor_id
               WHERE a.name = ?""",
            (actor_name,),
        ).fetchall()
        for row in case_rows:
            linked_cases.add(row["case_id"])

    for cid in linked_cases:
        conn.execute(
            "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)",
            (cid, sig_id, f"SAFLII auto-pin: {case_ref or 'no ref'}"),
        )

    # Auto-link to all matched actors
    for actor in matched_actors:
        conn.execute(
            "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
            (sig_id, actor["actor_id"], "subject"),
        )

    if not matched_actors:
        actor_row = conn.execute(
            "SELECT actor_id FROM actors WHERE name = ?", (actor_name,)
        ).fetchone()
        if actor_row:
            conn.execute(
                "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, ?)",
                (sig_id, actor_row["actor_id"], "subject"),
            )

    # ── Store case ref in actor's structured profile ────────────────
    if case_ref:
        try:
            from forage.engines.entity_engine import update_actor_property
            for actor in matched_actors:
                update_actor_property(conn, actor["actor_id"], "saflii_case_refs", case_ref)
        except Exception:
            pass

    # ── Evidence preservation: archive source URL ────────────────────
    source_url = item.get("link") or item.get("url")
    if source_url:
        try:
            from utils.wayback import archive_url as _archive
            archived = _archive(source_url)
            if archived:
                metadata["archive_url"] = archived
                conn.execute(
                    "UPDATE signals SET metadata_json = ? WHERE signal_id = ?",
                    (json.dumps(metadata, ensure_ascii=False), sig_id),
                )
        except Exception:
            pass

    conn.commit()

    # Create party relationships
    if parties:
        _create_party_relationship(conn, parties[0], parties[1], case_ref, all_actors, dry_run=False)

    actors_str = ", ".join(a["name"][:20] for a in matched_actors[:3]) or actor_name[:20]
    _log.info("  [SAFLII] G %.2f | %s | ref=%s | actors=[%s]",
              gravity, item["title"][:55], case_ref, actors_str)
    return True


def _load_all_actors(conn: sqlite3.Connection) -> list[dict]:
    """Load all actors for multi-actor matching."""
    rows = conn.execute(
        """SELECT actor_id, name, type, confidence_score
           FROM actors
           WHERE confidence_score >= 0.20
             AND type NOT IN ('location', 'unknown')
             AND length(name) > 3
           ORDER BY confidence_score DESC
           LIMIT 200"""
    ).fetchall()
    return [{"actor_id": r["actor_id"], "name": r["name"],
             "type": r["type"], "confidence": r["confidence_score"]} for r in rows]


def run(
    actor_name: str | None = None,
    dry_run: bool = False,
    max_actors: int = 15,
    max_results: int = 5,
) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row

    all_actors = _load_all_actors(conn)
    _log.info("Loaded %d actors for multi-matching", len(all_actors))

    stats = {"actors_queried": 0, "cases_queried": 0,
             "signals_found": 0, "signals_written": 0, "relationships_created": 0}

    # ── Phase A: Actor name queries ──────────────────────────────────
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
                 AND a.type IN ('person','institution','paramilitary','government','organization')
               ORDER BY a.confidence_score DESC
               LIMIT ?""",
            (max_actors,),
        ).fetchall()

    for actor in actors:
        name = actor["name"]
        _log.info("Querying SAFLII for actor: %s", name)
        stats["actors_queried"] += 1

        # Primary search: exact name
        items = _fetch_rss(f'"{name}"' if " " in name else name)
        # Fallback: unquoted if quoted returns nothing
        if not items:
            items = _fetch_rss(name)
        stats["signals_found"] += len(items)

        for item in items[:max_results]:
            case_ref = _extract_case_ref(f"{item['title']} {item['desc']}")
            if _write_signal(conn, name, item, case_ref, dry_run,
                             all_actors, query_source="actor"):
                stats["signals_written"] += 1

        time.sleep(REQ_DELAY)

    # ── Phase B: Case name queries ───────────────────────────────────
    if not actor_name:
        case_queries = conn.execute(
            """SELECT case_id, name FROM cases
               WHERE status != 'closed'
               ORDER BY case_id DESC
               LIMIT ?""",
            (max_actors,),
        ).fetchall()

        for case in case_queries:
            keywords = case["name"]
            # Extract meaningful keywords — skip generic words
            words = [w for w in keywords.split() if len(w) > 3
                     and w.lower() not in ('case', 'operation', 'the', 'and', 'for')]
            search_terms = " ".join(words[:5])
            if not search_terms:
                continue

            _log.info("Querying SAFLII for case: %s", search_terms[:50])
            stats["cases_queried"] += 1

            items = _fetch_rss(search_terms)
            stats["signals_found"] += len(items)

            for item in items[:max_results]:
                case_ref = _extract_case_ref(f"{item['title']} {item['desc']}")
                if _write_signal(conn, search_terms, item, case_ref, dry_run,
                                 all_actors, query_source="case",
                                 source_case_id=case["case_id"]):
                    stats["signals_written"] += 1

            time.sleep(REQ_DELAY)

    conn.close()
    _log.info("SAFLII collection complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE SAFLII Collector v2.1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--actor", type=str, default=None, help="Query a specific actor name")
    parser.add_argument("--max-actors", type=int, default=15, help="Max actors to query per run")
    parser.add_argument("--max-results", type=int, default=5, help="Max results per query")
    args = parser.parse_args()
    print(run(
        actor_name=args.actor,
        dry_run=args.dry_run,
        max_actors=args.max_actors,
        max_results=args.max_results,
    ))
