#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- SA FIC Targeted Financial Sanctions Collector
======================================================

Ingests the South African Financial Intelligence Centre (FIC) Targeted
Financial Sanctions (TFS) consolidated list -- the domestic implementation
of United Nations Security Council sanctions (resolutions 1267, 1373, 1988,
2253, and successor regimes).

Collection strategy
-------------------
  The FIC publishes the TFS consolidated list as XML.  Three endpoints are
  attempted in order:

    1. FIC website:  https://www.fic.gov.za/wp-content/uploads/Files/TFS/consolidated_list.xml
    2. FIC portal:   https://tfs.fic.gov.za/DownloadList  (may redirect)
    3. UN source:    https://scsanctions.un.org/resources/xml/en/consolidated.xml

  The XML follows the UN Security Council consolidated list schema:
    <CONSOLIDATED_LIST>
      <INDIVIDUALS><INDIVIDUAL>...</INDIVIDUAL></INDIVIDUALS>
      <ENTITIES><ENTITY>...</ENTITY></ENTITIES>
    </CONSOLIDATED_LIST>

Caching
-------
  The XML is cached at data/fic_tfs.xml with a 24-hour TTL.
  Use --force-download to bypass the cache.

Signal parameters
-----------------
  source          = "sanctions_sa_fic"
  stream          = "CRIME_INTEL"
  relevance_score = 2.0
  is_priority     = 1
  gravity_score   = 0.60   (hard baseline -- sanctions are always material)

Environment variables
---------------------
  FORGE_DB    Path to FORGE database (default: auto-detect from repo root)

Dependencies:  stdlib only (urllib, xml.etree, sqlite3)
"""

# ── Manifest (AST-parsed by Autodiscovery Registry at boot) ──────────────────
__manifest__ = {
    "id":          "sanctions_sa_fic",
    "name":        "SA FIC Sanctions Engine",
    "description": "Ingests the Financial Intelligence Centre (FIC) Targeted Financial Sanctions (TFS) registry to monitor domestic asset-freeze notices.",
    "icon":        "\U0001f6e1",
    "entry":       "forage/collectors/sanctions_sa_collector.py",
    "args":        [],
    "job_key":     "sanctions_sa_fic",
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Path setup ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Canonical source key -- MUST match manifest["id"] for auto-pin membrane query
SOURCE_ID = __manifest__["id"]

# ── Endpoint cascade ────────────────────────────────────────────────────────
TFS_ENDPOINTS = [
    (
        "FIC website",
        "https://www.fic.gov.za/wp-content/uploads/Files/TFS/consolidated_list.xml",
    ),
    (
        "FIC portal",
        "https://tfs.fic.gov.za/DownloadList",
    ),
    (
        "UN SCSC",
        "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    ),
]

# ── Local cache ──────────────────────────────────────────────────────────────
CACHE_DIR  = BASE_DIR / "data"
CACHE_FILE = CACHE_DIR / "fic_tfs.xml"

# Cache TTL: 24 hours in seconds
CACHE_TTL_SECONDS = 24 * 60 * 60

# ── Signal defaults ──────────────────────────────────────────────────────────
STREAM          = "CRIME_INTEL"
RELEVANCE_SCORE = 2.0
IS_PRIORITY     = 1
GRAVITY_SCORE   = 0.60

USER_AGENT = "FORGE-OSINT/1.0 SA-FIC-TFS-Collector"

# ── Sanitizer (Stable 1.1 compliance) ───────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t):  # noqa: E731
        return t

# ── Pipeline logger (path-safe, no hard coupling) ───────────────────────────
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


# ── DB helpers ───────────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    """Resolve DB path: CLI arg > FORGE_DB env > repo-root default."""
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return BASE_DIR / "database.db"


# ══════════════════════════════════════════════════════════════════════════════
# Download / Cache
# ══════════════════════════════════════════════════════════════════════════════

def _cache_is_fresh() -> bool:
    """Return True if the cached TFS XML exists and is younger than 24 hours."""
    if not CACHE_FILE.exists():
        return False
    age = time.time() - CACHE_FILE.stat().st_mtime
    return age < CACHE_TTL_SECONDS


def _download_tfs_xml(force: bool = False) -> Path:
    """
    Download the TFS consolidated list XML, trying endpoints in cascade.
    Caches to data/fic_tfs.xml with 24-hour TTL unless force=True.

    Returns the path to the cached XML file.
    """
    if not force and _cache_is_fresh():
        age_h = (time.time() - CACHE_FILE.stat().st_mtime) / 3600
        print(f"[fic-tfs] Using cached XML: {CACHE_FILE} (age: {age_h:.1f}h)")
        return CACHE_FILE

    last_error: Exception | None = None

    for label, url in TFS_ENDPOINTS:
        print(f"[fic-tfs] Trying {label}: {url} ...")
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/xml, text/xml, */*",
            },
        )
        try:
            with urlopen(req, timeout=60) as resp:
                raw_bytes = resp.read()
        except HTTPError as exc:
            print(f"[fic-tfs]   HTTP {exc.code} {exc.reason} -- skipping")
            last_error = exc
            continue
        except URLError as exc:
            print(f"[fic-tfs]   Network error: {exc.reason} -- skipping")
            last_error = exc
            continue
        except Exception as exc:
            print(f"[fic-tfs]   Unexpected error: {exc} -- skipping")
            last_error = exc
            continue

        # Validate we got XML (sanity check: look for an XML declaration or
        # root element start within the first 500 bytes)
        head = raw_bytes[:500]
        if b"<" not in head:
            print(f"[fic-tfs]   Response does not look like XML -- skipping")
            last_error = ValueError(f"Non-XML response from {label}")
            continue

        # Write to cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_bytes(raw_bytes)
        size_kb = len(raw_bytes) / 1024
        print(f"[fic-tfs] Downloaded {size_kb:.0f} KB from {label} -> {CACHE_FILE}")
        return CACHE_FILE

    raise RuntimeError(
        f"All TFS endpoints failed. Last error: {last_error}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# XML Parser
# ══════════════════════════════════════════════════════════════════════════════

def _el_text(parent: ET.Element, tag: str) -> str:
    """Extract text from a direct child element, or return empty string."""
    el = parent.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _el_values(parent: ET.Element, container_tag: str, value_tag: str = "VALUE") -> list[str]:
    """
    Extract all <VALUE> texts under a container element.
    e.g. <NATIONALITY><VALUE>SA</VALUE><VALUE>MZ</VALUE></NATIONALITY>
    """
    container = parent.find(container_tag)
    if container is None:
        return []
    return [
        (v.text or "").strip()
        for v in container.findall(value_tag)
        if v.text and v.text.strip()
    ]


def _parse_individual(ind: ET.Element) -> dict | None:
    """Parse a single <INDIVIDUAL> element into a signal dict."""
    dataid = _el_text(ind, "DATAID")
    if not dataid:
        return None

    # Name assembly: FIRST + SECOND + THIRD + FOURTH (some entries use all four)
    name_parts = []
    for tag in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"):
        part = _el_text(ind, tag)
        if part:
            name_parts.append(part)
    full_name = " ".join(name_parts) if name_parts else _el_text(ind, "NAME_ORIGINAL_SCRIPT")
    if not full_name:
        full_name = f"Individual DATAID {dataid}"

    list_type       = _el_text(ind, "UN_LIST_TYPE")
    reference_number = _el_text(ind, "REFERENCE_NUMBER")
    listed_on       = _el_text(ind, "LISTED_ON")
    comments        = _el_text(ind, "COMMENTS1")

    # Nationalities
    nationalities = _el_values(ind, "NATIONALITY")

    # Designations
    designations = _el_values(ind, "DESIGNATION")

    # Aliases
    aliases = []
    for alias_el in ind.findall("INDIVIDUAL_ALIAS"):
        alias_name = _el_text(alias_el, "ALIAS_NAME")
        alias_quality = _el_text(alias_el, "QUALITY")
        if alias_name:
            entry = alias_name
            if alias_quality:
                entry += f" ({alias_quality})"
            aliases.append(entry)

    # Dates of birth
    dobs = []
    for dob_el in ind.findall("INDIVIDUAL_DATE_OF_BIRTH"):
        dob_date = _el_text(dob_el, "DATE")
        dob_year = _el_text(dob_el, "YEAR")
        if dob_date:
            dobs.append(dob_date)
        elif dob_year:
            dobs.append(f"Year: {dob_year}")

    # Places of birth
    pobs = []
    for pob_el in ind.findall("INDIVIDUAL_PLACE_OF_BIRTH"):
        city = _el_text(pob_el, "CITY")
        country = _el_text(pob_el, "COUNTRY")
        parts = [p for p in (city, country) if p]
        if parts:
            pobs.append(", ".join(parts))

    # Documents (passports, IDs)
    documents = []
    for doc_el in ind.findall("INDIVIDUAL_DOCUMENT"):
        doc_type = _el_text(doc_el, "TYPE_OF_DOCUMENT")
        doc_num  = _el_text(doc_el, "NUMBER")
        doc_country = _el_text(doc_el, "ISSUING_COUNTRY")
        if doc_num:
            doc_str = f"{doc_type}: {doc_num}" if doc_type else doc_num
            if doc_country:
                doc_str += f" ({doc_country})"
            documents.append(doc_str)

    # ── Build content string ─────────────────────────────────────────────
    content_lines = []
    if designations:
        content_lines.append(f"Designation: {'; '.join(designations)}")
    if nationalities:
        content_lines.append(f"Nationality: {', '.join(nationalities)}")
    if dobs:
        content_lines.append(f"DOB: {', '.join(dobs)}")
    if pobs:
        content_lines.append(f"Place of birth: {'; '.join(pobs)}")
    if aliases:
        content_lines.append(f"Aliases: {'; '.join(aliases)}")
    if documents:
        content_lines.append(f"Documents: {'; '.join(documents)}")
    if comments:
        content_lines.append(f"Comments: {comments[:500]}")

    content = _sanitize("\n".join(content_lines)) if content_lines else (
        f"FIC TFS listed individual: {full_name}. List: {list_type}."
    )

    # ── Build title ──────────────────────────────────────────────────────
    title = f"{full_name} — UN/FIC TFS ({list_type})" if list_type else (
        f"{full_name} — UN/FIC TFS"
    )
    title = _sanitize(title)[:300]

    # ── Metadata ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    metadata = {
        "dataid":           dataid,
        "entry_type":       "individual",
        "reference_number": reference_number,
        "list_type":        list_type,
        "listed_on":        listed_on,
        "full_name":        full_name,
        "nationalities":    nationalities,
        "designations":     designations,
        "aliases":          [a.split(" (")[0] for a in aliases],
        "dobs":             dobs,
        "places_of_birth":  pobs,
        "documents":        documents,
    }

    return {
        "signal_id":       uuid.uuid4().hex,
        "source":          SOURCE_ID,
        "external_id":     f"fic_tfs:{dataid}",
        "title":           title,
        "content":         content,
        "timestamp":       now,
        "stream":          STREAM,
        "relevance_score": RELEVANCE_SCORE,
        "is_priority":     IS_PRIORITY,
        "gravity_score":   GRAVITY_SCORE,
        "metadata_json":   json.dumps(metadata, ensure_ascii=False),
        "source_type":     "live",
        "list_type":       list_type,
    }


def _parse_entity(ent: ET.Element) -> dict | None:
    """Parse a single <ENTITY> element into a signal dict."""
    dataid = _el_text(ent, "DATAID")
    if not dataid:
        return None

    # Entity name: FIRST_NAME is the primary field for entities
    entity_name = _el_text(ent, "FIRST_NAME")
    if not entity_name:
        # Some entities use a different structure
        entity_name = _el_text(ent, "NAME_ORIGINAL_SCRIPT")
    if not entity_name:
        entity_name = f"Entity DATAID {dataid}"

    list_type        = _el_text(ent, "UN_LIST_TYPE")
    reference_number = _el_text(ent, "REFERENCE_NUMBER")
    listed_on        = _el_text(ent, "LISTED_ON")
    comments         = _el_text(ent, "COMMENTS1")

    # Entity aliases
    aliases = []
    for alias_el in ent.findall("ENTITY_ALIAS"):
        alias_name = _el_text(alias_el, "ALIAS_NAME")
        alias_quality = _el_text(alias_el, "QUALITY")
        if alias_name:
            entry = alias_name
            if alias_quality:
                entry += f" ({alias_quality})"
            aliases.append(entry)

    # Entity addresses
    addresses = []
    for addr_el in ent.findall("ENTITY_ADDRESS"):
        city    = _el_text(addr_el, "CITY")
        country = _el_text(addr_el, "COUNTRY")
        street  = _el_text(addr_el, "STREET")
        parts = [p for p in (street, city, country) if p]
        if parts:
            addresses.append(", ".join(parts))

    # ── Build content string ─────────────────────────────────────────────
    content_lines = []
    if aliases:
        content_lines.append(f"Also known as: {'; '.join(aliases)}")
    if addresses:
        content_lines.append(f"Addresses: {'; '.join(addresses)}")
    if comments:
        content_lines.append(f"Comments: {comments[:500]}")

    content = _sanitize("\n".join(content_lines)) if content_lines else (
        f"FIC TFS listed entity: {entity_name}. List: {list_type}."
    )

    # ── Build title ──────────────────────────────────────────────────────
    title = f"{entity_name} — UN/FIC TFS ({list_type})" if list_type else (
        f"{entity_name} — UN/FIC TFS"
    )
    title = _sanitize(title)[:300]

    # ── Metadata ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    metadata = {
        "dataid":           dataid,
        "entry_type":       "entity",
        "reference_number": reference_number,
        "list_type":        list_type,
        "listed_on":        listed_on,
        "entity_name":      entity_name,
        "aliases":          [a.split(" (")[0] for a in aliases],
        "addresses":        addresses,
    }

    return {
        "signal_id":       uuid.uuid4().hex,
        "source":          SOURCE_ID,
        "external_id":     f"fic_tfs:{dataid}",
        "title":           title,
        "content":         content,
        "timestamp":       now,
        "stream":          STREAM,
        "relevance_score": RELEVANCE_SCORE,
        "is_priority":     IS_PRIORITY,
        "gravity_score":   GRAVITY_SCORE,
        "metadata_json":   json.dumps(metadata, ensure_ascii=False),
        "source_type":     "live",
        "list_type":       list_type,
    }


def _parse_tfs_xml(xml_path: Path) -> list[dict]:
    """
    Parse the TFS consolidated list XML into a list of signal dicts.

    Handles:
      - UTF-8 with BOM (strip BOM bytes before parsing)
      - Both <INDIVIDUALS> and <ENTITIES> sections
      - Per-entry try/except to isolate malformed records
    """
    raw_bytes = xml_path.read_bytes()

    # Strip UTF-8 BOM if present
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raw_bytes = raw_bytes[3:]

    # Parse XML
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        print(f"[fic-tfs] XML parse error: {exc}")
        # Attempt recovery: try decoding as latin-1 and re-encoding as utf-8
        try:
            text = raw_bytes.decode("latin-1")
            root = ET.fromstring(text.encode("utf-8"))
            print("[fic-tfs] Recovered via latin-1 re-encoding")
        except Exception as exc2:
            raise RuntimeError(
                f"Cannot parse TFS XML: {exc}; recovery also failed: {exc2}"
            ) from exc

    signals: list[dict] = []
    parse_errors = 0
    list_type_counts: dict[str, int] = {}

    # ── Parse individuals ────────────────────────────────────────────────
    individuals_section = root.find("INDIVIDUALS")
    ind_count = 0
    if individuals_section is not None:
        for ind_el in individuals_section.findall("INDIVIDUAL"):
            try:
                sig = _parse_individual(ind_el)
                if sig:
                    signals.append(sig)
                    ind_count += 1
                    lt = sig.get("list_type", "UNKNOWN")
                    list_type_counts[lt] = list_type_counts.get(lt, 0) + 1
            except Exception as exc:
                parse_errors += 1
                if parse_errors <= 5:
                    dataid = _el_text(ind_el, "DATAID")
                    print(f"  [fic-tfs] parse error individual {dataid}: {exc}")

    # ── Parse entities ───────────────────────────────────────────────────
    entities_section = root.find("ENTITIES")
    ent_count = 0
    if entities_section is not None:
        for ent_el in entities_section.findall("ENTITY"):
            try:
                sig = _parse_entity(ent_el)
                if sig:
                    signals.append(sig)
                    ent_count += 1
                    lt = sig.get("list_type", "UNKNOWN")
                    list_type_counts[lt] = list_type_counts.get(lt, 0) + 1
            except Exception as exc:
                parse_errors += 1
                if parse_errors <= 5:
                    dataid = _el_text(ent_el, "DATAID")
                    print(f"  [fic-tfs] parse error entity {dataid}: {exc}")

    print(f"[fic-tfs] Parsed {len(signals)} entries: "
          f"{ind_count} individuals, {ent_count} entities "
          f"({parse_errors} parse errors)")
    print(f"[fic-tfs] List types: {dict(sorted(list_type_counts.items()))}")

    return signals


# ══════════════════════════════════════════════════════════════════════════════
# DB Insertion
# ══════════════════════════════════════════════════════════════════════════════

INSERT_SQL = """
    INSERT OR IGNORE INTO signals
        (signal_id, source, external_id, title, content,
         timestamp, status, stream,
         relevance_score, is_priority, metadata_json,
         source_type, gravity_score)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """
    Insert signals via INSERT OR IGNORE on external_id.
    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    for sig in signals:
        try:
            conn.execute(
                INSERT_SQL,
                (
                    sig["signal_id"],
                    sig["source"],
                    sig["external_id"],
                    sig["title"],
                    sig["content"],
                    sig["timestamp"],
                    "raw",
                    sig["stream"],
                    sig["relevance_score"],
                    sig["is_priority"],
                    sig["metadata_json"],
                    sig["source_type"],
                    sig["gravity_score"],
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            skipped += 1
            print(f"  [fic-tfs] insert error ({sig.get('external_id')}): {exc}")

    return inserted, skipped


# ══════════════════════════════════════════════════════════════════════════════
# Main Runner
# ══════════════════════════════════════════════════════════════════════════════

def run(
    db_override: str | None = None,
    dry_run: bool = False,
    force_download: bool = False,
) -> None:
    """
    FIC TFS collection cycle:
      1. Download / refresh consolidated list XML (with 24h cache)
      2. Parse <INDIVIDUALS> and <ENTITIES> sections
      3. INSERT OR IGNORE into signals table
      4. Report summary with breakdown by list type
    """
    db_path  = _resolve_db(db_override)
    start_ts = datetime.now(timezone.utc)

    print("[fic-tfs] SA FIC Sanctions Engine starting")
    print(f"[fic-tfs] DB: {db_path}")
    if dry_run:
        print("[fic-tfs] DRY RUN -- no DB writes")

    # ── Step 1: Download / cache ─────────────────────────────────────────
    try:
        xml_path = _download_tfs_xml(force=force_download)
    except RuntimeError as exc:
        print(f"[fic-tfs] ABORT: {exc}")
        return

    # ── Step 2: Parse ────────────────────────────────────────────────────
    signals = _parse_tfs_xml(xml_path)

    if not signals:
        print("[fic-tfs] No entries parsed from TFS XML.")
        return

    # ── Step 3: Dry-run sample or DB insert ──────────────────────────────
    if dry_run:
        print(f"\n[fic-tfs] DRY RUN -- {len(signals)} signals would be inserted")
        print("[fic-tfs] Sample entries:")
        for sig in signals[:8]:
            meta = json.loads(sig["metadata_json"])
            entry_type = meta.get("entry_type", "?")
            list_type  = meta.get("list_type", "?")
            print(f"  {sig['external_id']:20s} | {entry_type:12s} | "
                  f"{list_type:20s} | {sig['title'][:60]}")
        if len(signals) > 8:
            print(f"  ... and {len(signals) - 8} more")
        return

    # ── DB connect + insert ──────────────────────────────────────────────
    if not db_path.exists():
        print(f"[fic-tfs] ABORT: database not found at {db_path}")
        print("[fic-tfs] Run: python app.py --init-db")
        print("[fic-tfs] Or set FORGE_DB=/path/to/database.db")
        return

    conn = sqlite3.connect(str(db_path), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row

        inserted, skipped = _insert_signals(conn, signals)
        conn.commit()
    finally:
        conn.close()

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()

    # ── Step 4: Summary ──────────────────────────────────────────────────
    # Breakdown by list type
    type_inserted: dict[str, int] = {}
    for sig in signals:
        lt = sig.get("list_type", "UNKNOWN")
        type_inserted[lt] = type_inserted.get(lt, 0) + 1

    print(f"\n[fic-tfs] Complete in {elapsed:.1f}s -- "
          f"+{inserted} new | ~{skipped} duplicates | "
          f"{len(signals)} total parsed")
    print("[fic-tfs] By list type:")
    for lt, count in sorted(type_inserted.items()):
        print(f"  {lt}: {count}")

    # ── Pipeline telemetry ───────────────────────────────────────────────
    log_run(
        collector="sanctions_sa_fic",
        new_signals=inserted,
        errors=0,
        runtime_seconds=elapsed,
        meta={
            "total_parsed": len(signals),
            "skipped":      skipped,
            "by_list_type": type_inserted,
        },
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE SA FIC TFS Collector -- ingest Financial Intelligence Centre sanctions list"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and display sample entries without writing to DB",
    )
    parser.add_argument(
        "--force-download", action="store_true",
        help="Bypass 24h cache and re-download the TFS XML",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Path to database.db (default: auto-detect via FORGE_DB or repo root)",
    )
    args = parser.parse_args()

    try:
        run(
            db_override=args.db,
            dry_run=args.dry_run,
            force_download=args.force_download,
        )
    except Exception as exc:
        print(f"[fic-tfs] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
