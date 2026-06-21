#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- US OFAC SDN Sanctions List Collector  (forage/collectors/ofac_collector.py)
====================================================================================

Downloads and parses the US Treasury OFAC Specially Designated Nationals (SDN)
list to trace sanctioned entities, proxies, and asset networks.

Collection strategy: Strategy A — bulk CSV download from Treasury.gov.

The SDN CSV is pipe-delimited with twelve fields per row:
  ent_num | SDN_Name | SDN_Type | Program | Title | Call_Sign |
  Vess_type | Tonnage | GRT | Vess_flag | Vess_owner | Remarks

Program filtering (SA OSINT relevance)
──────────────────────────────────────
  SDGT     Specially Designated Global Terrorists
  NARCO    Foreign Narcotics Kingpin
  CYBER2   Cyber-Related Sanctions
  GLOMAG   Global Magnitsky (corruption / human rights)

By default all programs are ingested.  Use --programs SDGT,GLOMAG to filter.

Caching
───────
  The SDN CSV is cached at data/sdn.csv and only re-downloaded when the
  cached file is older than 24 hours.  Use --force-download to bypass.

Environment variables
─────────────────────
  FORGE_DB        Path to FORGE database (default: auto-detect from repo root)

Dependencies:  stdlib only (urllib, csv, sqlite3)
"""

# ── Manifest (AST-parsed by Autodiscovery Registry at boot) ──────────────────
__manifest__ = {
    "id":          "ofac_sdn",
    "name":        "US OFAC Sanctions List",
    "description": "Ingests the Specially Designated Nationals (SDN) data feed to trace sanctioned entities, proxies, and asset networks.",
    "icon":        "🏦",
    "entry":       "forage/collectors/ofac_collector.py",
    "args":        [],
    "job_key":     "ofac_sdn",
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
import csv
import io
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Path setup ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Canonical source key -- MUST match manifest["id"] for auto-pin membrane query
SOURCE_ID = __manifest__["id"]

# SDN CSV download URL (pipe-delimited, US Treasury)
SDN_CSV_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"

# Local cache path
CACHE_DIR  = BASE_DIR / "data"
CACHE_FILE = CACHE_DIR / "sdn.csv"

# Cache TTL: 24 hours in seconds
CACHE_TTL_SECONDS = 24 * 60 * 60

# SDN CSV column names (pipe-delimited, 12 fields)
SDN_COLUMNS = [
    "ent_num", "SDN_Name", "SDN_Type", "Program", "Title",
    "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
    "Vess_owner", "Remarks",
]

# Programs of SA OSINT relevance (used as default filter when --programs is set)
SA_RELEVANT_PROGRAMS = {"SDGT", "NARCO", "CYBER2", "GLOMAG"}

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


# ── DB helpers ───────────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    """Resolve DB path: CLI arg > FORGE_DB env > repo-root default."""
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return BASE_DIR / "database.db"


# ── Download / cache logic ───────────────────────────────────────────────────

def _cache_is_fresh() -> bool:
    """Return True if the cached SDN CSV exists and is younger than 24 hours."""
    if not CACHE_FILE.exists():
        return False
    age = time.time() - CACHE_FILE.stat().st_mtime
    return age < CACHE_TTL_SECONDS


def _download_sdn_csv(force: bool = False) -> Path:
    """
    Download the SDN CSV from Treasury.gov, caching to data/sdn.csv.
    Respects the 24-hour cache TTL unless force=True.

    Returns the path to the cached CSV file.
    """
    if not force and _cache_is_fresh():
        print(f"[ofac] Using cached SDN CSV: {CACHE_FILE} "
              f"(age: {(time.time() - CACHE_FILE.stat().st_mtime) / 3600:.1f}h)")
        return CACHE_FILE

    print(f"[ofac] Downloading SDN CSV from {SDN_CSV_URL} ...")
    req = Request(
        SDN_CSV_URL,
        headers={
            "User-Agent": "FORGE-OSINT/1.0 OFAC-SDN-Collector",
            "Accept":     "text/csv, */*",
        },
    )
    try:
        with urlopen(req, timeout=60) as resp:
            raw_bytes = resp.read()
    except HTTPError as exc:
        raise RuntimeError(
            f"OFAC download failed: HTTP {exc.code} {exc.reason}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"OFAC download failed: {exc.reason}"
        ) from exc

    # Ensure cache directory exists
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Write with UTF-8 encoding; the SDN CSV is mostly ASCII with occasional
    # Latin-1 characters in names (handle gracefully)
    CACHE_FILE.write_bytes(raw_bytes)
    size_kb = len(raw_bytes) / 1024
    print(f"[ofac] Downloaded {size_kb:.0f} KB -> {CACHE_FILE}")
    return CACHE_FILE


# ── CSV parser ───────────────────────────────────────────────────────────────

def _parse_sdn_csv(csv_path: Path, programs_filter: set[str] | None = None) -> list[dict]:
    """
    Parse the pipe-delimited SDN CSV into a list of signal dicts.

    Filtering rules:
      - Skip rows where SDN_Type is 'vessel' unless Vess_flag suggests SA linkage
      - If programs_filter is provided, only include entries matching those programs
      - Skip malformed / empty rows

    Returns a list of dicts ready for DB insertion.
    """
    # Read with encoding fallback: the SDN CSV is nominally Latin-1 / CP1252
    # but sometimes includes UTF-8 characters
    try:
        text = csv_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = csv_path.read_text(encoding="latin-1", errors="replace")

    reader = csv.reader(io.StringIO(text))
    signals = []
    programs_seen: set[str] = set()
    skipped_vessel  = 0
    skipped_program = 0
    parse_errors    = 0

    # SA-linked vessel flags (for the vessel filter exception)
    sa_vessel_flags = {
        "south africa", "mozambique", "tanzania", "kenya",
        "nigeria", "angola", "namibia", "madagascar",
    }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for row_num, row in enumerate(reader, start=1):
        try:
            row = [f.strip() if f else "" for f in row]
            row = ["" if f == "-0-" else f for f in row]

            # SDN CSV rows have 12 comma-delimited fields; some rows have fewer
            # (the final "Remarks" field is often missing)
            if len(row) < 4:
                continue  # Not enough fields to be useful

            # Pad to 12 fields if needed
            while len(row) < 12:
                row.append("")

            ent_num   = row[0].strip()
            sdn_name  = row[1].strip()
            sdn_type  = row[2].strip().lower()   # individual, entity, vessel, aircraft
            program   = row[3].strip()
            title_col = row[4].strip()
            call_sign = row[5].strip()
            vess_type = row[6].strip()
            tonnage   = row[7].strip()
            grt       = row[8].strip()
            vess_flag = row[9].strip()
            vess_owner = row[10].strip()
            remarks   = row[11].strip()

            # Skip empty / header-like rows
            if not ent_num or not sdn_name:
                continue

            # Validate ent_num is numeric (skip any header row)
            try:
                int(ent_num)
            except ValueError:
                continue

            # Track programs seen
            programs_seen.add(program)

            # Vessel filter: skip vessel-only entries unless SA-linked
            if sdn_type == "vessel":
                flag_lower = vess_flag.lower()
                if not any(sa_flag in flag_lower for sa_flag in sa_vessel_flags):
                    skipped_vessel += 1
                    continue

            # Program filter (if specified)
            if programs_filter:
                # OFAC programs are space-separated in the CSV; check intersection
                row_programs = set(program.replace(";", " ").split())
                if not row_programs.intersection(programs_filter):
                    skipped_program += 1
                    continue

            # ── Build signal ─────────────────────────────────────────────
            external_id = f"ofac:{ent_num}"
            signal_id   = uuid.uuid4().hex

            # Title: "{SDN_Name} -- OFAC SDN ({Program})"
            sig_title = f"{sdn_name} — OFAC SDN ({program})"
            sig_title = _sanitize(sig_title)[:300]

            # Content: Remarks field (aliases, addresses, DOB, passport numbers)
            sig_content = _sanitize(remarks) if remarks else (
                f"OFAC SDN entry for {sdn_name}. "
                f"Type: {sdn_type}. Program: {program}."
            )

            # Metadata
            metadata = {
                "ent_num":    ent_num,
                "sdn_type":   sdn_type,
                "program":    program,
                "title":      title_col,
                "remarks":    remarks,
                "call_sign":  call_sign,
                "vess_type":  vess_type,
                "tonnage":    tonnage,
                "grt":        grt,
                "vess_flag":  vess_flag,
                "vess_owner": vess_owner,
            }

            signals.append({
                "signal_id":       signal_id,
                "source":          SOURCE_ID,
                "external_id":     external_id,
                "title":           sig_title,
                "content":         sig_content,
                "timestamp":       now,
                "stream":          "CRIME_INTEL",
                "relevance_score": 1.8,
                "is_priority":     0,
                "metadata_json":   json.dumps(metadata, ensure_ascii=False),
                "source_type":     "live",
            })

        except Exception as exc:
            parse_errors += 1
            if parse_errors <= 5:
                print(f"  [ofac] parse error row {row_num}: {exc}")

    print(f"[ofac] Parsed {len(signals)} entries from {row_num} rows")
    print(f"[ofac] Skipped: {skipped_vessel} vessel-only, "
          f"{skipped_program} filtered-program, {parse_errors} parse errors")
    print(f"[ofac] Programs seen: {sorted(programs_seen)}")

    return signals


# ── DB insertion ─────────────────────────────────────────────────────────────

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
                """
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     timestamp, status, stream,
                     relevance_score, is_priority, metadata_json, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            skipped += 1
            print(f"  [ofac] insert error ({sig.get('external_id')}): {exc}")

    return inserted, skipped


# ── Main runner ──────────────────────────────────────────────────────────────

def run(
    db_override: str | None = None,
    dry_run: bool = False,
    force_download: bool = False,
    programs: set[str] | None = None,
) -> None:
    """
    OFAC SDN collection cycle:
      1. Download / refresh SDN CSV (with 24h cache)
      2. Parse pipe-delimited rows
      3. Apply program + vessel filters
      4. INSERT OR IGNORE into signals table
      5. Report summary
    """
    db_path  = _resolve_db(db_override)
    start_ts = datetime.now(timezone.utc)

    print(f"[ofac] OFAC SDN Collector starting")
    print(f"[ofac] DB: {db_path}")
    if programs:
        print(f"[ofac] Program filter: {sorted(programs)}")
    if dry_run:
        print(f"[ofac] DRY RUN -- no DB writes")

    # ── Step 1: Download / cache ─────────────────────────────────────────
    try:
        csv_path = _download_sdn_csv(force=force_download)
    except RuntimeError as exc:
        print(f"[ofac] ABORT: {exc}")
        return

    # ── Step 2-3: Parse + filter ─────────────────────────────────────────
    signals = _parse_sdn_csv(csv_path, programs_filter=programs)

    if not signals:
        print("[ofac] No signals to insert after filtering.")
        return

    # ── Step 4: Dry-run sample or DB insert ──────────────────────────────
    if dry_run:
        print(f"\n[ofac] DRY RUN -- {len(signals)} signals would be inserted")
        print("[ofac] Sample entries:")
        for sig in signals[:5]:
            meta = json.loads(sig["metadata_json"])
            print(f"  {sig['external_id']:16s} | {meta.get('program','?'):12s} | "
                  f"{meta.get('sdn_type','?'):12s} | {sig['title'][:70]}")
        if len(signals) > 5:
            print(f"  ... and {len(signals) - 5} more")
        return

    # ── DB connect + insert ──────────────────────────────────────────────
    if not db_path.exists():
        print(f"[ofac] ABORT: database not found at {db_path}")
        print(f"[ofac] Run: python app.py --init-db")
        print(f"[ofac] Or set FORGE_DB=/path/to/database.db")
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

    # ── Step 5: Summary ──────────────────────────────────────────────────
    print(
        f"\n[ofac] Complete in {elapsed:.1f}s -- "
        f"+{inserted} new | ~{skipped} duplicates | "
        f"{len(signals)} total parsed"
    )

    # ── Pipeline telemetry ───────────────────────────────────────────────
    log_run(
        collector="ofac_sdn",
        new_signals=inserted,
        errors=0,
        runtime_seconds=elapsed,
        meta={
            "total_parsed": len(signals),
            "skipped":      skipped,
            "programs":     sorted(programs) if programs else "all",
        },
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE OFAC SDN Collector -- ingest US Treasury sanctions list"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and display sample entries without writing to DB",
    )
    parser.add_argument(
        "--force-download", action="store_true",
        help="Bypass 24h cache and re-download the SDN CSV",
    )
    parser.add_argument(
        "--programs", type=str, default=None,
        help="Comma-separated OFAC program codes to filter (e.g. SDGT,GLOMAG). "
             "Default: all programs",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Path to database.db (default: auto-detect via FORGE_DB or repo root)",
    )
    args = parser.parse_args()

    programs_filter = None
    if args.programs:
        programs_filter = {p.strip().upper() for p in args.programs.split(",")}

    try:
        run(
            db_override=args.db,
            dry_run=args.dry_run,
            force_download=args.force_download,
            programs=programs_filter,
        )
    except Exception as exc:
        print(f"[ofac] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
