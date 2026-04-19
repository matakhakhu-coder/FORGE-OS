"""
forage/utils/forensics.py — Forensic Provenance Utilities
==========================================================

Court-ready exhibit provenance for FORGE intelligence artifacts.

Dual-Signature Hash
───────────────────
Format:  ISO-8601 | DEV_HASH | CASE_HASH

  ISO-8601   — UTC timestamp at time of provenance capture
  DEV_HASH   — SHA-256 of the raw device/source payload (first 16 hex chars)
  CASE_HASH  — SHA-256 of (case_id + signal_id + timestamp) (first 16 hex chars)

The separator is ` | ` (space-pipe-space) to remain human-readable while
surviving copy-paste into court document templates.

Usage
─────
  from forage.utils.forensics import sign_exhibit, verify_exhibit, ExhibitStamp

  stamp = sign_exhibit(
      raw_payload=b"<raw bytes of the document>",
      case_id=42,
      signal_id="gdelt-doc:abc123",
      artifact_id=1001,
  )
  print(stamp.signature)
  # "2026-04-13T14:22:01Z | a3f8c2d1e9b04f12 | 7e1c9a3b2f84d051"

  ok = verify_exhibit(stamp.signature, raw_payload=b"...", case_id=42, ...)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union


# ── Signature format ──────────────────────────────────────────────────────────

_SIG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"   # ISO-8601 UTC
    r" \| ([0-9a-f]{16})"                            # DEV_HASH
    r" \| ([0-9a-f]{16})$"                           # CASE_HASH
)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ExhibitStamp:
    """
    Immutable forensic provenance stamp for a single exhibit.

    Attributes
    ----------
    signature    : str   — "ISO | DEV_HASH | CASE_HASH"
    captured_at  : str   — ISO-8601 UTC timestamp
    dev_hash     : str   — SHA-256[:16] of raw device payload
    case_hash    : str   — SHA-256[:16] of case+signal+artifact identity
    artifact_id  : int   — FORGE artifact_id
    case_id      : int   — FORGE case_id
    signal_id    : str   — FORGE signal_id (may be empty string)
    """
    signature:   str
    captured_at: str
    dev_hash:    str
    case_hash:   str
    artifact_id: int
    case_id:     int
    signal_id:   str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ChainOfCustody:
    """
    Ordered log of provenance events for a single artifact.

    Each entry records WHO handled the artifact (component name), WHEN,
    and WHAT state it was in (status label).
    """
    artifact_id: int
    entries: list[dict]

    def add(self, component: str, status: str, note: str = "") -> None:
        self.entries.append({
            "component": component,
            "status":    status,
            "note":      note,
            "ts":        _now_iso(),
        })

    def to_json(self) -> str:
        return json.dumps({"artifact_id": self.artifact_id,
                           "chain": self.entries}, ensure_ascii=False)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dev_hash(raw_payload: Union[bytes, str, Path]) -> str:
    """SHA-256 of raw device payload, first 16 hex chars."""
    h = hashlib.sha256()
    if isinstance(raw_payload, Path):
        with open(raw_payload, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    elif isinstance(raw_payload, str):
        h.update(raw_payload.encode("utf-8", errors="replace"))
    else:
        h.update(raw_payload)
    return h.hexdigest()[:16]


def _case_hash(case_id: int, signal_id: str, artifact_id: int,
               captured_at: str) -> str:
    """SHA-256 of the case identity tuple, first 16 hex chars."""
    blob = f"{case_id}:{signal_id}:{artifact_id}:{captured_at}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── Public API ────────────────────────────────────────────────────────────────

def sign_exhibit(
    raw_payload: Union[bytes, str, Path],
    case_id: int,
    artifact_id: int,
    signal_id: str = "",
) -> ExhibitStamp:
    """
    Create a forensic provenance stamp for an exhibit.

    Parameters
    ----------
    raw_payload  : bytes | str | Path — the raw source material being stamped.
                   For PDFs: pass the bytes or file path BEFORE any processing.
                   For signals: pass the raw JSON string from the source API.
    case_id      : int  — FORGE case_id this exhibit belongs to.
    artifact_id  : int  — FORGE artifact_id.
    signal_id    : str  — FORGE signal_id (optional, empty string if N/A).

    Returns
    -------
    ExhibitStamp with a dual-signature string and component hashes.
    """
    captured_at = _now_iso()
    dh = _dev_hash(raw_payload)
    ch = _case_hash(case_id, signal_id, artifact_id, captured_at)
    signature = f"{captured_at} | {dh} | {ch}"

    return ExhibitStamp(
        signature=signature,
        captured_at=captured_at,
        dev_hash=dh,
        case_hash=ch,
        artifact_id=artifact_id,
        case_id=case_id,
        signal_id=signal_id,
    )


def verify_exhibit(
    signature: str,
    raw_payload: Union[bytes, str, Path],
    case_id: int,
    artifact_id: int,
    signal_id: str = "",
) -> tuple[bool, str]:
    """
    Verify a previously issued exhibit stamp.

    Returns
    -------
    (True, "OK") if the stamp is valid.
    (False, reason_string) if any component fails to match.
    """
    m = _SIG_PATTERN.match(signature or "")
    if not m:
        return False, "Signature format invalid"

    captured_at, stored_dh, stored_ch = m.group(1), m.group(2), m.group(3)

    # Re-derive hashes using stored timestamp (not now)
    actual_dh = _dev_hash(raw_payload)
    actual_ch = _case_hash(case_id, signal_id, artifact_id, captured_at)

    if actual_dh != stored_dh:
        return False, f"DEV_HASH mismatch: stored={stored_dh} actual={actual_dh}"
    if actual_ch != stored_ch:
        return False, f"CASE_HASH mismatch: stored={stored_ch} actual={actual_ch}"

    return True, "OK"


def parse_signature(signature: str) -> Optional[dict]:
    """
    Parse a signature string into its components without verification.
    Returns None if the format is invalid.
    """
    m = _SIG_PATTERN.match(signature or "")
    if not m:
        return None
    return {
        "captured_at": m.group(1),
        "dev_hash":    m.group(2),
        "case_hash":   m.group(3),
    }


def new_custody_log(artifact_id: int) -> ChainOfCustody:
    """Create a fresh chain-of-custody log for an artifact."""
    return ChainOfCustody(artifact_id=artifact_id, entries=[])
