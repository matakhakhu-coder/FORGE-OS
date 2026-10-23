"""What each pin's incident date stands for (docs/signals/incident-dates.json).

Every hand-checked pin has an incident_start. Most of the time that is when
the incident happened or began (or, with incident_event "found", when a body
or a fault was found). Two kinds of pin are different, and the pin files have
no field that says so:

  report      the sources never say when it happened, so incident_start holds
              the date of the report or notice. The incident date is unknown.
  later_step  incident_start is a later step in an older case: an arrest, a
              charge, a court appearance or a finding. The incident itself
              happened earlier, on the date in "earlier_incident", which must
              come from a quoted source (never guessed).

The answer for each pin lives in this sidecar file, like pin-precision.json
and provenance.json, so the pin files, their fingerprints and the owner
approvals in docs/approvals/pins stay exactly as they are. Every active pin
needs an entry: tools/publish.py refuses to build when one is missing or
invalid, so a new pin can never default to "the date it happened" by
accident.

Entry formats:
  {"basis": "incident"}
  {"basis": "report", "why": "..."}
  {"basis": "later_step", "step": "charge",
   "earlier_incident": {"date": "2026-07-22", "precision": "day"},
   "evidence": {"source": "...", "url": "https://...", "quote": "..."},
   "why": "..."}
  earlier_incident.date is YYYY-MM-DD with precision "day", or YYYY-MM with
  precision "month" when the source gives only the month. It must be before
  the pin's incident_start.

Check one file from the repo root:
    python tools/incident_dates.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INCIDENT_DATES_FILE = ROOT / "docs" / "signals" / "incident-dates.json"

BASES = ("incident", "report", "later_step")
STEPS = ("arrest", "charge", "court", "finding")
EARLIER_PRECISIONS = {"day": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
                      "month": re.compile(r"^\d{4}-\d{2}$")}


def load(path: pathlib.Path | None = None) -> dict:
    """signal_id -> entry. A missing or unreadable file gives {} (so every
    pin is then reported as missing, never silently treated as dated)."""
    p = pathlib.Path(path or INCIDENT_DATES_FILE)
    try:
        pins = json.loads(p.read_text(encoding="utf-8")).get("pins")
    except (OSError, ValueError, AttributeError):
        return {}
    return pins if isinstance(pins, dict) else {}


def entry_problem(entry, incident_start: str | None = None) -> str | None:
    """Why an entry is invalid, or None when it is fine."""
    if not isinstance(entry, dict):
        return "no entry"
    basis = entry.get("basis")
    if basis not in BASES:
        return f"basis must be one of {', '.join(BASES)}"
    if basis == "incident":
        return None
    if not str(entry.get("why") or "").strip():
        return f"a {basis} entry needs a 'why'"
    if basis == "report":
        return None
    if entry.get("step") not in STEPS:
        return f"step must be one of {', '.join(STEPS)}"
    earlier = entry.get("earlier_incident")
    if not isinstance(earlier, dict):
        return "a later_step entry needs earlier_incident"
    prec, day = earlier.get("precision"), str(earlier.get("date") or "")
    if prec not in EARLIER_PRECISIONS or not EARLIER_PRECISIONS[prec].match(day):
        return "earlier_incident needs a date of YYYY-MM-DD (day) or YYYY-MM (month)"
    if incident_start and day >= str(incident_start)[: len(day)]:
        return "earlier_incident must be before the pin's incident_start"
    ev = entry.get("evidence")
    if not isinstance(ev, dict) or not str(ev.get("quote") or "").strip() \
            or not str(ev.get("url") or "").lower().startswith(("https://", "http://")):
        return "a later_step entry needs evidence with a quote and a source link"
    return None


def basis_for(entry, incident_start: str | None = None) -> str | None:
    """The entry's basis when it is valid, else None."""
    return None if entry_problem(entry, incident_start) else entry["basis"]


def problems(pins: list[dict], path: pathlib.Path | None = None) -> list[str]:
    """One line per active pin whose entry is missing or invalid."""
    entries = load(path)
    out = []
    for s in pins:
        sid = s["signal_id"]
        start = ((s.get("metadata") or {}).get("checked") or {}).get("incident_start")
        why = entry_problem(entries.get(sid), start)
        if why:
            out.append(f"{sid[:8]}: {why}")
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from tools.checked_signals import load_index
    cs = load_index()
    found = problems(cs.active)
    for line in found:
        print(line)
    counts: dict[str, int] = {}
    entries = load()
    for s in cs.active:
        b = (entries.get(s["signal_id"]) or {}).get("basis", "missing")
        counts[b] = counts.get(b, 0) + 1
    print(f"{len(cs.active)} active pins: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
