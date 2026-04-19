# -*- coding: utf-8 -*-
"""
FORGE -- Restricted Suppliers Cross-Check  (scripts/restricted_crosscheck.py)
==============================================================================
Phase 2 / Task 3: compare company names in the National Treasury Restricted
Suppliers list against the 813 actors in the FORGE actor registry.

Any match is flagged as HIGH_RISK:
  - actors.description updated with restricted-supplier warning
  - actors.is_priority set to 1  (surfaces in dashboard targeted actors)
  - A new signal written (stream='PROCUREMENT', is_priority=1) so the hit
    appears in the investigative feed

The Restricted Suppliers PDF:
    https://ocpo.treasury.gov.za/RestrictedSupplier/RestrictedSuppliersReport.pdf

Run:
    python scripts/restricted_crosscheck.py [--dry-run] [--db path/to/database.db]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "database.db"
MEDIA_DIR = BASE_DIR / "media" / "documents"

RESTRICTED_PDF_URL = (
    "https://ocpo.treasury.gov.za/RestrictedSupplier/RestrictedSuppliersReport.pdf"
)
LOCAL_CACHE = MEDIA_DIR / "NT_restricted_RestrictedSuppliersReport.pdf"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    try:
        print(f"[{_ts()}] [crosscheck] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{_ts()}] [crosscheck] {msg.encode('utf-8', errors='replace').decode('ascii', errors='replace')}", flush=True)


def _get_pdf_bytes() -> bytes:
    """Return PDF bytes — local cache first, then download."""
    if LOCAL_CACHE.exists():
        log(f"Using cached PDF: {LOCAL_CACHE.name}")
        return LOCAL_CACHE.read_bytes()

    log(f"Downloading Restricted Suppliers PDF from OCPO…")
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    import requests
    sess = requests.Session()
    sess.verify = False
    sess.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    r = sess.get(RESTRICTED_PDF_URL, timeout=(10, 60), stream=True)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(65536):
        buf.write(chunk)
    pdf_bytes = buf.getvalue()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CACHE.write_bytes(pdf_bytes)
    log(f"Saved to {LOCAL_CACHE.name} ({len(pdf_bytes)//1024} KB)")
    return pdf_bytes


def _extract_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF using pdfplumber."""
    import pdfplumber
    texts = []
    buf = io.BytesIO(pdf_bytes)
    pdf = None
    try:
        pdf = pdfplumber.open(buf)
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texts.append(t)
            page.close()
    finally:
        if pdf:
            pdf.close()
        buf.close()
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Name extraction from Restricted Suppliers report
# ---------------------------------------------------------------------------

# The NT Restricted Suppliers PDF contains rows like:
#   ACME TRADING (PTY) LTD   | 2019/123456/07 | Fraudulent documents | 2024-01-01
# We extract the company-name column.

_COMPANY_RE = re.compile(
    r"^([A-Z][A-Z0-9\s&()\-\.\']{3,80}"   # company name (UPPERCASE)
    r"(?:\s*(?:PTY|CC|LTD|NPC|INC|SOC|CORP|GROUP|TRUST|JV)\b"
    r"(?:\s*(?:LTD|LIMITED|PROPRIETARY))?)?)",
    re.MULTILINE,
)

# Registration number pattern — used to identify lines that are data rows
_REG_RE = re.compile(r"\b\d{4}/\d{6}/\d{2}\b")


def _parse_company_names(text: str) -> list[str]:
    """
    Extract supplier/company names from the restricted suppliers text.
    Strategy: collect lines that appear before a registration number,
    then supplement with the company-name regex.
    """
    names: list[str] = []
    seen: set[str] = set()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Lines containing a registration number are data rows —
        # the company name is on the same line before the reg number
        if _REG_RE.search(line):
            # Strip registration number and everything after
            company_part = _REG_RE.split(line)[0].strip()
            company_part = re.sub(r"[\|\t]+.*$", "", company_part).strip()
            if company_part and len(company_part) > 3:
                norm = company_part.upper()
                if norm not in seen:
                    seen.add(norm)
                    names.append(company_part)
            continue

        # Fallback: lines that match the company-name pattern
        m = _COMPANY_RE.match(line)
        if m:
            candidate = m.group(1).strip()
            # Skip header/footer lines
            if any(w in candidate.upper() for w in
                   ("SUPPLIER", "COMPANY", "ENTITY", "NAME", "REPORT",
                    "NATIONAL TREASURY", "PAGE", "DATE")):
                continue
            norm = candidate.upper()
            if len(norm) > 4 and norm not in seen:
                seen.add(norm)
                names.append(candidate)

    log(f"Extracted {len(names)} candidate company names from PDF")
    return names


# ---------------------------------------------------------------------------
# Actor matching
# ---------------------------------------------------------------------------

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = _NORM_RE.sub(" ", t)
    return " ".join(p for p in t.split() if p)


def _load_actors(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """
    Return {normalized_name: (actor_id, raw_name)} for actors that could
    plausibly appear on a restricted suppliers list (institutions, companies,
    persons — not news URLs or publication bylines).
    """
    # Exclude actors whose names look like URLs, bylines, or news artefacts
    _NOISE_FRAGMENTS = (
        ".co.za", ".gov.za", ".org.za", "news24", "timeslive",
        "groundup", "dailymaverick", "daily maverick", "amabhungane",
        "businessday", "business day", "iol.co", "pressreader",
    )
    index: dict[str, tuple[int, str]] = {}
    for row in conn.execute("SELECT actor_id, name FROM actors WHERE name IS NOT NULL"):
        raw_name = row[1]
        low = raw_name.lower()
        if any(frag in low for frag in _NOISE_FRAGMENTS):
            continue
        norm = _normalize(raw_name)
        if norm:
            index[norm] = (int(row[0]), raw_name)
    return index


# Generic words that appear in many company names — substring matches on these
# alone are meaningless.
_GENERIC_WORDS: frozenset[str] = frozenset({
    "construction", "trading", "services", "management", "consulting",
    "solutions", "technologies", "holdings", "enterprise", "enterprises",
    "group", "projects", "development", "investments", "finance",
    "resources", "supplies", "contractors", "engineering",
    "digital", "frontier", "global", "national", "south",
    "africa", "african",
})


def _match_actor(company_name: str,
                 actor_index: dict[str, tuple[int, str]]) -> list[tuple[int, str, str]]:
    """
    Try to match a company name against all actors.
    Returns list of (actor_id, actor_name, match_type).
    match_type: 'exact' | 'substring'

    Substring matching requires:
      - Match token ≥ 8 chars (raises previous 6-char floor)
      - Match token is not a standalone generic word
    """
    norm = _normalize(company_name)
    if not norm or len(norm) < 4:
        return []

    matches: list[tuple[int, str, str]] = []

    # 1. Exact normalized match
    if norm in actor_index:
        aid, raw = actor_index[norm]
        matches.append((aid, raw, "exact"))
        return matches  # exact wins — no need to check further

    # 2. Meaningful substring — min 8 chars, not a lone generic word
    norm_tokens = set(norm.split())
    for actor_norm, (aid, raw) in actor_index.items():
        if len(actor_norm) < 8:
            continue
        if actor_norm not in norm and norm not in actor_norm:
            continue
        # Determine the actual matching fragment
        if actor_norm in norm:
            fragment = actor_norm
        else:
            fragment = norm
        # Reject if the fragment collapses to a single generic token
        frag_tokens = set(fragment.split())
        if frag_tokens.issubset(_GENERIC_WORDS):
            continue
        matches.append((aid, raw, "substring"))

    return matches


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def _flag_actor_high_risk(conn: sqlite3.Connection,
                           actor_id: int,
                           actor_name: str,
                           company_name: str,
                           dry_run: bool) -> None:
    """Update actor as HIGH_RISK and write a priority signal."""
    warning = (
        f"[HIGH_RISK] Appears on National Treasury Restricted Suppliers list "
        f"as '{company_name}'. Cross-checked {datetime.now(timezone.utc).date()}."
    )

    if not dry_run:
        # Flag actor
        conn.execute(
            """UPDATE actors
               SET description = CASE
                   WHEN description IS NULL THEN ?
                   WHEN description NOT LIKE '%HIGH_RISK%' THEN description || ' | ' || ?
                   ELSE description
               END,
               confidence_score = COALESCE(confidence_score, 0.5)
               WHERE actor_id = ?""",
            (warning, warning, actor_id),
        )

        # Write a HIGH_RISK signal into the feed
        ext_id  = "restricted:" + hashlib.sha1(
            f"{actor_id}:{company_name}".encode()
        ).hexdigest()[:20]
        existing = conn.execute(
            "SELECT 1 FROM signals WHERE external_id=?", (ext_id,)
        ).fetchone()
        if not existing:
            sig_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO signals
                       (signal_id, source, external_id, title, content,
                        lat, lng, timestamp, status, stream,
                        relevance_score, is_priority, source_type)
                   VALUES (?,?,?,?,?,
                           -25.7479, 28.2293, ?, 'raw', 'PROCUREMENT',
                           2.0, 1, 'live')""",
                (
                    sig_id,
                    "restricted_crosscheck",
                    ext_id,
                    f"[HIGH_RISK] Restricted Supplier match: {actor_name}",
                    (f"Actor '{actor_name}' matched against National Treasury "
                     f"Restricted Suppliers list as '{company_name}'. "
                     f"This actor is barred from government procurement."),
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_crosscheck(db_path: Path = DB_PATH, dry_run: bool = False) -> dict:
    log("=== Restricted Suppliers Cross-Check ===")
    if dry_run:
        log("DRY RUN — no DB writes")

    # 1. Fetch PDF
    try:
        pdf_bytes = _get_pdf_bytes()
    except Exception as exc:
        log(f"ERROR fetching PDF: {exc}")
        return {"status": "error", "error": str(exc)}

    # 2. Extract text
    text = _extract_text(pdf_bytes)
    if not text.strip():
        log("ERROR: no extractable text (may be a scanned/image PDF)")
        return {"status": "error", "error": "no_text"}
    log(f"Extracted {len(text)} chars of text from PDF")

    # 3. Parse company names
    company_names = _parse_company_names(text)
    if not company_names:
        log("No company names parsed — check PDF format")
        return {"status": "done", "companies_found": 0, "matches": 0}

    # 4. Load actor index
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    actor_index = _load_actors(conn)
    log(f"Actor registry: {len(actor_index)} entries")

    # 5. Cross-check
    matches_found: list[dict] = []
    flagged_actors: set[int] = set()

    for company_name in company_names:
        hits = _match_actor(company_name, actor_index)
        for actor_id, actor_name, match_type in hits:
            if actor_id in flagged_actors:
                continue
            flagged_actors.add(actor_id)

            log(f"  [{match_type.upper()}] '{company_name}' → actor#{actor_id} '{actor_name}'")
            matches_found.append({
                "company_on_list": company_name,
                "actor_id":        actor_id,
                "actor_name":      actor_name,
                "match_type":      match_type,
            })
            _flag_actor_high_risk(conn, actor_id, actor_name, company_name, dry_run)

    if not dry_run and matches_found:
        conn.commit()
        log(f"Committed {len(matches_found)} HIGH_RISK flags to DB")

    conn.close()

    summary = {
        "status":          "dry_run" if dry_run else "done",
        "pdf_url":         RESTRICTED_PDF_URL,
        "companies_parsed": len(company_names),
        "actors_checked":  len(actor_index),
        "matches":         len(matches_found),
        "flagged":         matches_found,
        "computed_at":     datetime.now(timezone.utc).isoformat(),
    }
    log(f"Done: {len(matches_found)} actor(s) flagged HIGH_RISK from "
        f"{len(company_names)} restricted suppliers")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Restricted Suppliers Cross-Check"
    )
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and match but do not write to DB")
    args = parser.parse_args()

    db = args.db.resolve() if args.db else DB_PATH
    result = run_crosscheck(db_path=db, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("status") in ("done", "dry_run") else 1)
