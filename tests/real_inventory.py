"""Expected figures for tests that check the real data, read from the data itself.

Before 21 Nov 2026 every new pin meant hand-editing the pin count (85, 86, ...)
and the October-only assumptions in about sixteen test files. These helpers
count from the checked batches in docs/signals and from the approval ledger
instead, so a valid new pin needs no test edits, while the tests still compare
what the code builds (markers, records, months, areas) against an independent
count taken straight from the batch files.

Nothing here calls the code under test to decide what it should produce: the
counts come from the JSON on disk (active pins, their incident_start month,
the record files in the ledger).
"""
from __future__ import annotations

import json
import pathlib
from collections import Counter
from functools import lru_cache

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNALS = ROOT / "docs" / "signals"
LEDGER = ROOT / "docs" / "approvals" / "pins"


@lru_cache(maxsize=1)
def _batches() -> tuple[tuple[str, tuple[dict, ...], tuple[str, ...]], ...]:
    idx = json.loads((SIGNALS / "index.json").read_text(encoding="utf-8"))
    out = []
    for entry in idx["batches"]:
        data = json.loads((SIGNALS / entry["file"]).read_text(encoding="utf-8"))
        retracted = tuple(r["signal_id"] for r in data.get("retractions") or [])
        out.append((entry["file"], tuple(data["signals"]), retracted))
    return tuple(out)


def all_pins() -> list[dict]:
    """Every pin in every checked batch, retracted ones included."""
    return [s for _, sigs, _ in _batches() for s in sigs]


def active_pins() -> list[dict]:
    """Pins that are live on the map: in a batch and not retracted."""
    gone = {sid for _, _, r in _batches() for sid in r}
    return [s for s in all_pins() if s["signal_id"] not in gone]


def active_ids() -> set[str]:
    return {s["signal_id"] for s in active_pins()}


def active_count() -> int:
    return len(active_pins())


def incident_month(pin: dict) -> str:
    """YYYY-MM of the pin's incident_start, as written in its batch file.
    Hand-checked pins always carry incident_start (checked_signals requires it)."""
    return pin["metadata"]["checked"]["incident_start"][:7]


def ids_by_month() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for s in active_pins():
        out.setdefault(incident_month(s), set()).add(s["signal_id"])
    return out


def months() -> list[str]:
    return sorted(ids_by_month())


def count_in_month(month: str) -> int:
    return len(ids_by_month().get(month, set()))


def ids_from_month(month: str) -> set[str]:
    """Active pins whose incident month is this month or later."""
    return {sid for m, ids in ids_by_month().items() if m >= month for sid in ids}


def latest_incident_start() -> str:
    return max(s["metadata"]["checked"]["incident_start"][:10] for s in active_pins())


def stream_count(stream: str) -> int:
    """Active pins filed under this map stream (CRIME_INTEL, INFRASTRUCTURE, ...)."""
    return sum(s.get("stream") == stream for s in active_pins())


def batch_files() -> list[str]:
    return [f for f, _, _ in _batches()]


def ledger_record_files() -> list[pathlib.Path]:
    """Every approval record file (one folder per signal_id; schema and examples skipped)."""
    return sorted(p for p in LEDGER.glob("*/*.json")
                  if p.parent.name not in ("schema", "_examples"))


def ledger_record_count() -> int:
    return len(ledger_record_files())


def ledger_bases() -> Counter:
    return Counter(json.loads(p.read_text(encoding="utf-8"))["basis"] for p in ledger_record_files())
