#!/usr/bin/env python3
"""
FORAGE — Forensic Artifact Processor (Phase 20)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Extracts forensic metadata from FORGE artifacts:

  Hashing (stdlib hashlib — zero dependencies)
    SHA-256  — primary integrity fingerprint, chain-of-custody anchor
    MD5      — legacy compatibility, fast duplicate pre-check

  EXIF Extraction (Pillow — already installed)
    GPS coordinates   — override or supplement analyst-entered lat/lng
    DateTimeOriginal  — when the photo was actually taken (not file date)
    Make / Model      — camera or device manufacturer and model
    Full tag dump     — all readable EXIF stored as JSON for future queries

  Duplicate Detection
    Any artifact sharing a SHA-256 with an existing artifact is recorded
    in the artifact_duplicates table.  Deduplication is advisory only —
    the analyst decides whether to delete or retain the duplicate.

Design
──────
• hash_file() and extract_exif() are importable standalone functions,
  used directly by the admin upload handler for real-time extraction.

• ForensicProcessor.run_batch() processes all existing artifacts that
  have no hash yet — safe to run on a live database.

• All writes are idempotent: UPDATE OR IGNORE / INSERT OR IGNORE.

• No paid APIs, no cloud, no extra installs beyond what's already present.

Usage
─────
    # Batch-process all un-hashed artifacts
    python forage/processors/forensic_processor.py

    # Process a single artifact by ID
    python forage/processors/forensic_processor.py --artifact-id 42

    # Dry run — show what would be extracted without writing
    python forage/processors/forensic_processor.py --dry-run

    # Re-process all (overwrite existing forensic data)
    python forage/processors/forensic_processor.py --reprocess

    # Override database path
    python forage/processors/forensic_processor.py --db /path/to/database.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

HASH_CHUNK  = 65_536   # 64 KB read chunks for hashing large files
BATCH_SIZE  = 25       # artifacts per DB commit cycle

# EXIF tags we care about (Pillow tag names)
_GPS_INFO_TAG   = "GPSInfo"
_EXIF_TAGS = {
    "Make":              "make",
    "Model":             "model",
    "DateTime":          "datetime",
    "DateTimeOriginal":  "datetime_original",
    "DateTimeDigitized": "datetime_digitized",
    "Software":          "software",
    "ImageWidth":        "width",
    "ImageLength":       "height",
    "Orientation":       "orientation",
    "Flash":             "flash",
    "FocalLength":       "focal_length",
    "ISOSpeedRatings":   "iso",
    "ExposureTime":      "exposure_time",
    "FNumber":           "f_number",
    "WhiteBalance":      "white_balance",
}

# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [forensic_processor] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [forensic_processor] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── Core extraction functions (importable by app.py admin handler) ────────────

def hash_file(path: Path) -> tuple[Optional[str], Optional[str]]:
    """
    Compute SHA-256 and MD5 of a file in streaming chunks.
    Returns (sha256_hex, md5_hex) or (None, None) on failure.
    Works on any file type, any size.
    """
    if not path.exists():
        return None, None
    try:
        sha256 = hashlib.sha256()
        md5    = hashlib.md5()
        with open(path, "rb") as fh:
            while chunk := fh.read(HASH_CHUNK):
                sha256.update(chunk)
                md5.update(chunk)
        return sha256.hexdigest(), md5.hexdigest()
    except Exception as exc:
        warn(f"Hashing failed for {path.name}: {exc}")
        return None, None


def extract_exif(path: Path) -> Optional[dict]:
    """
    Extract EXIF metadata from an image file using Pillow.
    Returns a dict of clean key→value pairs, or None if no EXIF present
    or the file is not an image.

    GPS coordinates are returned as decimal degrees in 'gps_lat' / 'gps_lng'.
    All other useful tags are included under human-readable keys.
    The raw full tag dict is included under 'raw' for completeness.
    """
    ext = path.suffix.lower().lstrip(".")
    if ext not in {"jpg", "jpeg", "png", "tiff", "tif", "webp", "heic"}:
        return None

    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
    except ImportError:
        warn("Pillow not available — EXIF extraction skipped")
        return None

    try:
        with Image.open(str(path)) as img:
            raw_exif = img._getexif()  # type: ignore[attr-defined]
            if not raw_exif:
                return None
    except Exception:
        return None

    # Build human-readable tag map
    tagged: dict = {}
    for tag_id, value in raw_exif.items():
        tag_name = TAGS.get(tag_id, str(tag_id))
        tagged[tag_name] = value

    result: dict = {}

    # Extract the tags we care about
    for pil_name, our_name in _EXIF_TAGS.items():
        val = tagged.get(pil_name)
        if val is not None:
            # IFDRational → float
            try:
                if hasattr(val, "numerator"):
                    val = float(val)
                elif isinstance(val, tuple) and len(val) == 2:
                    val = val[0] / val[1] if val[1] else None
            except Exception:
                val = str(val)
            result[our_name] = val

    # GPS decoding
    gps_info = tagged.get(_GPS_INFO_TAG)
    if gps_info and isinstance(gps_info, dict):
        gps_tagged = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}

        def _dms_to_decimal(dms, ref: str) -> Optional[float]:
            """Convert degrees/minutes/seconds tuple to decimal degrees."""
            try:
                d = float(dms[0])
                m = float(dms[1])
                s = float(dms[2])
                dd = d + m / 60.0 + s / 3600.0
                if ref in ("S", "W"):
                    dd = -dd
                return round(dd, 8)
            except Exception:
                return None

        lat = _dms_to_decimal(
            gps_tagged.get("GPSLatitude", ()),
            str(gps_tagged.get("GPSLatitudeRef", "N")),
        )
        lng = _dms_to_decimal(
            gps_tagged.get("GPSLongitude", ()),
            str(gps_tagged.get("GPSLongitudeRef", "E")),
        )
        if lat is not None:
            result["gps_lat"] = lat
        if lng is not None:
            result["gps_lng"] = lng

        alt = gps_tagged.get("GPSAltitude")
        if alt is not None:
            try:
                result["gps_altitude_m"] = round(float(alt), 2)
            except Exception:
                pass

    # Include sanitised raw dump (only serialisable values)
    raw_clean: dict = {}
    for k, v in tagged.items():
        if k == _GPS_INFO_TAG:
            continue
        try:
            json.dumps(v)   # test serialisability
            raw_clean[k] = v
        except (TypeError, ValueError):
            raw_clean[k] = str(v)
    result["raw"] = raw_clean

    return result if len(result) > 1 else None   # >1 because raw is always present


# ── ForensicProcessor ─────────────────────────────────────────────────────────

class ForensicProcessor:
    """
    Batch forensic processor for existing artifacts.
    Hashes every unprocessed file, extracts EXIF, detects duplicates.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()
        self._conn: Optional[sqlite3.Connection] = None

    def _conn_get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _open_db(self._db_path)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _base_dir(self) -> Path:
        return self._db_path.resolve().parent

    def _ensure_schema(self) -> None:
        """Add forensic columns if they don't exist (pre-migration safety)."""
        conn     = self._conn_get()
        existing = {r[1] for r in conn.execute("PRAGMA table_info(artifacts)")}
        for col, defn in [
            ("file_hash_sha256", "TEXT"),
            ("file_hash_md5",    "TEXT"),
            ("file_size_bytes",  "INTEGER"),
            ("exif_json",        "TEXT"),
            ("gps_lat",          "REAL"),
            ("gps_lng",          "REAL"),
            ("device_make",      "TEXT"),
            ("device_model",     "TEXT"),
            ("exif_datetime",    "TEXT"),
        ]:
            if col not in existing:
                log(f"Adding column artifacts.{col}…")
                conn.execute(f"ALTER TABLE artifacts ADD COLUMN {col} {defn}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS artifact_duplicates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id     INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
                duplicate_of_id INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
                hash_sha256     TEXT    NOT NULL,
                detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (artifact_id, duplicate_of_id)
            )
        """)
        conn.commit()

    def process_artifact(self, row: sqlite3.Row,
                         dry_run: bool = False) -> dict:
        """
        Process one artifact row. Returns a result dict.
        """
        aid       = row["artifact_id"]
        file_path = row["file_path"]
        result    = {"artifact_id": aid, "status": "skipped",
                     "hash": None, "exif": False, "duplicate_of": None}

        if not file_path:
            return result

        abs_path = self._base_dir() / file_path
        if not abs_path.exists():
            warn(f"File missing: {abs_path}")
            result["status"] = "missing"
            return result

        # ── Hash ──────────────────────────────────────────────────────────────
        sha256, md5 = hash_file(abs_path)
        if not sha256:
            result["status"] = "hash_failed"
            return result

        result["hash"] = sha256

        # ── EXIF ──────────────────────────────────────────────────────────────
        exif       = extract_exif(abs_path)
        gps_lat    = exif.get("gps_lat")    if exif else None
        gps_lng    = exif.get("gps_lng")    if exif else None
        make       = exif.get("make")       if exif else None
        model      = exif.get("model")      if exif else None
        exif_dt    = exif.get("datetime_original") if exif else None
        exif_json  = json.dumps(exif, ensure_ascii=False) if exif else None
        file_size  = abs_path.stat().st_size
        result["exif"] = exif is not None

        if dry_run:
            gps_str = f"GPS({gps_lat:.4f},{gps_lng:.4f})" if gps_lat else "no GPS"
            log(f"  [DRY] {aid}: {abs_path.name} | {sha256[:12]}… | "
                f"{file_size:,}B | {gps_str} | exif={result['exif']}")
            result["status"] = "dry_run"
            return result

        conn = self._conn_get()

        # ── Write forensic data ───────────────────────────────────────────────
        conn.execute("""
            UPDATE artifacts
            SET    file_hash_sha256 = ?,
                   file_hash_md5    = ?,
                   file_size_bytes  = ?,
                   exif_json        = ?,
                   gps_lat          = ?,
                   gps_lng          = ?,
                   device_make      = ?,
                   device_model     = ?,
                   exif_datetime    = ?
            WHERE  artifact_id = ?
        """, (sha256, md5, file_size, exif_json,
              gps_lat, gps_lng, make, model, exif_dt,
              aid))

        # Auto-promote GPS → lat/lng if analyst left it blank
        if gps_lat and gps_lng and not row["latitude"]:
            conn.execute(
                "UPDATE artifacts SET latitude=?, longitude=? WHERE artifact_id=?",
                (gps_lat, gps_lng, aid),
            )

        # ── Duplicate detection ───────────────────────────────────────────────
        existing = conn.execute(
            "SELECT artifact_id FROM artifacts "
            "WHERE file_hash_sha256 = ? AND artifact_id != ?",
            (sha256, aid),
        ).fetchall()

        for dup in existing:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO artifact_duplicates
                       (artifact_id, duplicate_of_id, hash_sha256)
                       VALUES (?, ?, ?)""",
                    (aid, dup["artifact_id"], sha256),
                )
                result["duplicate_of"] = dup["artifact_id"]
            except Exception:
                pass

        result["status"] = "done"
        return result

    def run_batch(self, reprocess: bool = False,
                  artifact_id: Optional[int] = None,
                  dry_run: bool = False) -> dict:

        self._ensure_schema()
        conn = self._conn_get()

        if artifact_id is not None:
            rows = conn.execute(
                "SELECT artifact_id, file_path, latitude FROM artifacts "
                "WHERE artifact_id = ?", (artifact_id,)
            ).fetchall()
        elif reprocess:
            rows = conn.execute(
                "SELECT artifact_id, file_path, latitude FROM artifacts "
                "WHERE file_path IS NOT NULL ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT artifact_id, file_path, latitude FROM artifacts "
                "WHERE file_path IS NOT NULL AND file_hash_sha256 IS NULL "
                "ORDER BY created_at DESC"
            ).fetchall()

        total    = len(rows)
        log(f"Artifacts to process: {total}")
        if total == 0:
            log("Nothing to do. Use --reprocess to re-hash all artifacts.")
            self.close()
            return {"processed": 0, "done": 0, "skipped": 0,
                    "duplicates": 0, "missing": 0}

        summary = {"processed": 0, "done": 0, "skipped": 0,
                   "duplicates": 0, "missing": 0}

        for i, row in enumerate(rows):
            result = self.process_artifact(row, dry_run=dry_run)
            summary["processed"] += 1
            status = result["status"]
            if status == "done":
                summary["done"] += 1
            elif status == "missing":
                summary["missing"] += 1
            else:
                summary["skipped"] += 1
            if result.get("duplicate_of"):
                summary["duplicates"] += 1

            if (i + 1) % BATCH_SIZE == 0 and not dry_run:
                conn.commit()
                log(f"  Progress {i+1}/{total} — "
                    f"{summary['done']} done, "
                    f"{summary['duplicates']} duplicates found")

        if not dry_run:
            conn.commit()

        log(f"Complete: {summary}")
        self.close()
        return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Forensic Processor — hash + EXIF extraction for artifacts"
    )
    parser.add_argument("--db", type=Path, default=None,
                        help="Override path to database.db")
    parser.add_argument("--artifact-id", type=int, default=None, dest="artifact_id",
                        help="Process a single artifact by ID")
    parser.add_argument("--reprocess", action="store_true",
                        help="Re-hash all artifacts, not just unprocessed ones")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be extracted without writing")
    args = parser.parse_args()

    resolved = _resolve_db(str(args.db) if args.db else None)
    log(f"Database : {resolved}")
    log(f"Reprocess: {args.reprocess} | Dry run: {args.dry_run}")

    try:
        fp = ForensicProcessor(db_path=resolved)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    summary = fp.run_batch(
        reprocess=args.reprocess,
        artifact_id=args.artifact_id,
        dry_run=args.dry_run,
    )
    sys.exit(0 if summary.get("missing", 0) == 0 else 1)