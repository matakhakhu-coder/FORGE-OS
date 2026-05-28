#!/usr/bin/env python3
from __future__ import annotations
"""
Mythos Anthology — Mythology Source Collector
(mythos/collectors/mythology_collector.py)
=============================================
Ingests public-domain mythology texts into mythos_sources.
Feeds the rebuild chain: source → extract_character → ...

Default targets (all public domain):
  - Project Gutenberg mythology texts (RSS feed + direct fetch)
  - Wikipedia mythology category pages (REST API)
  - Sacred-texts.com public collections

Each ingested source triggers an extract_character job in mythos_rebuild_queue.
Deduplication: SHA256 of raw_text — INSERT OR IGNORE on content_hash.

Usage:
  python mythos/collectors/mythology_collector.py
  python mythos/collectors/mythology_collector.py --target wikipedia --culture Greek
"""

__manifest__ = {
    "id":          "mythology_collector",
    "name":        "Mythology Source Collector",
    "description": "Ingests public-domain mythology texts into mythos_sources and seeds the rebuild queue.",
    "icon":        "📜",
    "entry":       "mythos/collectors/mythology_collector.py",
    "args":        ["--target", "--culture", "--limit"],
    "job_key":     "mythology_collector",
    "version":     "0.1.0",
}

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mythos.collectors.mythology")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "database.db"

# Cultures supported by this collector (maps to mythos_sources.culture)
CULTURES = [
    "Greek", "Norse", "Egyptian", "Japanese", "Celtic",
    "Mesopotamian", "Hindu", "African", "Aztec", "Slavic",
]

# Wikipedia REST endpoint — returns page extract in plain text
_WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _insert_source(conn: sqlite3.Connection, *, title: str, source_type: str,
                   culture: str, era: str, url: str | None, raw_text: str) -> str | None:
    """
    Insert a source row. Returns source_id on success, None if duplicate.
    Also enqueues extract_character in the rebuild queue.
    """
    h = _sha256(raw_text)
    existing = conn.execute(
        "SELECT source_id FROM mythos_sources WHERE content_hash=?", (h,)
    ).fetchone()
    if existing:
        log.debug("skip duplicate: %s", title[:60])
        return None

    conn.execute(
        """INSERT INTO mythos_sources
               (title, source_type, culture, era, url, content_hash, raw_text)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, source_type, culture, era, url, h, raw_text),
    )
    src_id = conn.execute(
        "SELECT source_id FROM mythos_sources WHERE content_hash=?", (h,)
    ).fetchone()["source_id"]

    # Seed the rebuild chain
    conn.execute(
        """INSERT INTO mythos_rebuild_queue
               (node_type, node_id, operation, priority)
           VALUES ('source', ?, 'extract_character', 3)""",
        (src_id,),
    )
    conn.commit()
    log.info("ingested source: %s [%s] id=%s", title[:60], culture, src_id[:8])
    return src_id


def _fetch_wikipedia(title: str, culture: str) -> int:
    """Fetch a Wikipedia summary and insert as a source. Returns count inserted."""
    url = _WIKI_SUMMARY.format(title=title.replace(" ", "_"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FORGE-Mythos/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        log.warning("wikipedia fetch failed for %s: %s", title, exc)
        return 0

    extract = data.get("extract", "").strip()
    if len(extract) < 100:
        log.debug("skip thin extract: %s", title)
        return 0

    conn = _conn()
    try:
        sid = _insert_source(
            conn,
            title=data.get("title", title),
            source_type="digital",
            culture=culture,
            era="Unknown",
            url=data.get("content_urls", {}).get("desktop", {}).get("page"),
            raw_text=extract,
        )
        return 1 if sid else 0
    finally:
        conn.close()


# Default Wikipedia targets per culture — expand as needed
_WIKI_TARGETS: dict[str, list[str]] = {
    "Greek":       ["Persephone", "Hecate", "Hydra", "Sphinx", "Perseus"],
    "Norse":       ["Balder", "Loki", "Odin", "Thor", "Freya"],
    "Japanese":    ["Tengu", "Oni", "Amaterasu", "Susanoo", "Izanagi"],
    "Celtic":      ["Cú_Chulainn", "Morrigan", "Lugh", "Dagda"],
    "Egyptian":    ["Osiris", "Isis", "Ra", "Anubis", "Thoth"],
    "Mesopotamian":["Gilgamesh", "Enkidu", "Ishtar", "Marduk"],
    "African":     ["Anansi", "Shango", "Yemoja", "Ogun"],
    "Aztec":       ["Quetzalcoatl", "Tlaloc", "Coatlicue", "Huitzilopochtli"],
    "Hindu":       ["Indra", "Kali", "Vishnu", "Shiva", "Ganesha"],
    "Slavic":      ["Perun", "Veles", "Marzanna", "Svarog"],
}


def collect_wikipedia(culture: str | None = None, limit: int = 5) -> int:
    targets = _WIKI_TARGETS if culture is None else {culture: _WIKI_TARGETS.get(culture, [])}
    total = 0
    for cult, articles in targets.items():
        for article in articles[:limit]:
            total += _fetch_wikipedia(article, cult)
    return total


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Mythos source collector")
    parser.add_argument("--target",  default="wikipedia",
                        choices=["wikipedia"],
                        help="Source target (default: wikipedia)")
    parser.add_argument("--culture", default=None,
                        choices=CULTURES + [None],
                        help="Limit to one culture (default: all)")
    parser.add_argument("--limit",   type=int, default=5,
                        help="Max articles per culture (default: 5)")
    args = parser.parse_args()

    if args.target == "wikipedia":
        n = collect_wikipedia(culture=args.culture, limit=args.limit)
        log.info("collector done — %d new sources ingested", n)
    else:
        log.error("unknown target: %s", args.target)
        sys.exit(1)


if __name__ == "__main__":
    main()
