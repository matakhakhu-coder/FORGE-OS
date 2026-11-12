"""Pin clocks: when a routine last checked each pin (docs/signals/pin-clocks.json).

Time-aware pins Batch 2 (24 Oct 2026). Every hand-checked pin keeps four
clocks, and each one has exactly one writer:

  incident_start           metadata.checked, set when the pin is approved.
                           The age clock. Never changes on an update.
  published_at             the pin, set when it first goes on the map.
                           Never changes on an update.
  last_material_update_at  metadata.checked, with update_note and
                           update_sources. Only an owner approval with
                           "material": true (pin-approval v2) may set or
                           change them, as a new pin version.
                           tools/approvals.py enforces that.
  last_checked_at          THIS file. When a routine (a sweep, a parked-case
                           review) last re-read the pin's sources. It needs no
                           approval, so it lives outside the pin files: writing
                           it never touches a fingerprint or an approval hash.

A routine check may change one thing only: a pin's entry here (its
last_checked_at, the record the check is written in, and optionally the
outcome no_new_information).
record_check() is the one way a routine writes it, and
routine_write_problems() refuses a routine's write that changes anything
else (a pin file, an approval record, another sidecar, another key here, or
a clock moved backwards).

File format:
    {"schema": "forge.pin_clocks", "version": 1, "about": "...",
     "pins": {"<signal_id>": {"last_checked_at": "2026-11-03T08:15:00+02:00",
                              "record": "Sprint 5 results, section 15c",
                              "outcome": "no_new_information"}}}

"record" names the private log the check is written in (required: no log,
no date). "outcome" is optional and has one allowed value; a check that
found a lead or a possible update leaves it out. The map's "Last reviewed"
date is last_review(): the later of this check and the pin's last material
update. See docs/signals/REVIEW_DATES.md.

A pin with no entry has not been re-checked by a routine since it was
approved, and its last_checked_at is blank in the open records. Nothing is
back-filled: an unrecorded check is left unknown, never invented.

Commands (from the repo root):
    python tools/pin_clocks.py verify                       check this file
    python tools/pin_clocks.py record SIGNAL_ID --at ISO --record "LOG" [--outcome no_new_information]
    python tools/pin_clocks.py check-routine [--base REF]   is the working tree's
                                                            change vs REF (default
                                                            HEAD) a valid routine write?
"""
from __future__ import annotations

import argparse
import json
import re
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIGNALS_DIR = ROOT / "docs" / "signals"
CLOCKS_NAME = "pin-clocks.json"
CLOCKS_FILE = SIGNALS_DIR / CLOCKS_NAME
SCHEMA = "forge.pin_clocks"
VERSION = 1
# Last reviewed dates (12 Nov 2026): every entry names the record the check
# is written in, and may say what the check found. See
# docs/signals/REVIEW_DATES.md for what counts as a review.
REQUIRED_KEYS = {"last_checked_at", "record"}
OPTIONAL_KEYS = {"outcome"}
ENTRY_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS
# The one outcome a check may state on the map. A check that found something
# (a lead, a possible update) states nothing: the date alone shows it ran,
# and any real development goes through an owner-approved material update.
OUTCOMES = {
    "no_new_information": "No new information was found in the sources checked.",
}
RECORD_MAX = 200
# Words a review must never use about a case. "No new information" is not
# "cold", "closed" or "resolved": FORGE cannot know any of that.
BANNED_REVIEW_WORDS = (
    "cold", "closed", "resolved", "solved", "dormant", "inactive", "no longer",
    "dead end", "dead-end", "abandoned", "gone quiet", "went quiet", "shelved",
    "concluded",
)
_BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BANNED_REVIEW_WORDS) + r")\b", re.I)
# A check stamped a little ahead of this machine's clock is tolerated.
FUTURE_SKEW = timedelta(minutes=10)

ABOUT = ("When a routine (a town sweep, a parked-case review) last re-read each "
         "hand-checked pin's sources. Kept apart from the pin files so a routine check "
         "never changes a fingerprint or needs an owner approval. A routine may change only "
         "a pin's entry here: last_checked_at, the record the check is written in, and "
         "optionally outcome (no_new_information); anything else is refused "
         "(tools/pin_clocks.py). Only a check with a written record goes here; none is "
         "back-filled. A pin with no entry has not been re-checked since it was approved. "
         "Material updates are not recorded here: they need an owner approval with material "
         "set. The map's 'Last reviewed' date is the later of last_checked_at and the last "
         "material update (docs/signals/REVIEW_DATES.md).")


class PinClockError(ValueError):
    """A routine write that must not happen."""


def _parse_dt(value) -> datetime | None:
    """A date-time with a UTC offset, or None."""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _incident_start(value) -> datetime | None:
    """The incident's start: a date-time with an offset, or a bare date read
    as the start of that day in South African time (UTC+2)."""
    dt = _parse_dt(value)
    if dt is not None or not isinstance(value, str):
        return dt
    try:
        d = datetime.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    return d.replace(tzinfo=timezone(timedelta(hours=2)))


def empty_file() -> dict:
    return {"schema": SCHEMA, "version": VERSION, "about": ABOUT, "pins": {}}


def read_file(path: pathlib.Path | None = None) -> dict:
    """The whole file. A missing file reads as empty (no pin re-checked yet).
    A broken file raises PinClockError."""
    p = pathlib.Path(path or CLOCKS_FILE)
    if not p.exists():
        return empty_file()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinClockError(f"{p.name}: could not be read ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("version") != VERSION \
            or not isinstance(data.get("pins"), dict):
        raise PinClockError(f"{p.name}: must be {{schema: {SCHEMA}, version: {VERSION}, pins: {{...}}}}")
    return data


def load(path: pathlib.Path | None = None) -> dict:
    """signal_id -> entry; {} when the file is missing or unreadable."""
    try:
        return read_file(path)["pins"]
    except PinClockError:
        return {}


def wording_problem(text) -> str | None:
    """Why `text` may not describe a review (it calls a case cold, closed,
    resolved and the like), or None."""
    m = _BANNED_RE.search(str(text or ""))
    return f"review wording may not say {m.group(1).lower()!r} about a case" if m else None


def entry_problem(entry, pin: dict | None = None, now: datetime | None = None) -> str | None:
    """Why one entry is invalid, or None."""
    if not isinstance(entry, dict):
        return "entry must be an object"
    extra = sorted(set(entry) - ENTRY_KEYS)
    if extra:
        return f"only last_checked_at, record and outcome may be recorded here (found {extra})"
    missing = sorted(REQUIRED_KEYS - set(entry))
    if missing:
        return f"{', '.join(missing)} is missing"
    when = _parse_dt(entry["last_checked_at"])
    if when is None:
        return "last_checked_at must be a date-time with a UTC offset"
    record = entry["record"]
    if not isinstance(record, str) or not record.strip() or "\n" in record \
            or len(record) > RECORD_MAX:
        return f"record must name the log the check is written in, one line of 1 to {RECORD_MAX} characters"
    why = wording_problem(record)
    if why:
        return f"record: {why}"
    if "outcome" in entry and entry["outcome"] not in OUTCOMES:
        return f"outcome must be one of {sorted(OUTCOMES)} (or left out)"
    now = now or datetime.now(timezone.utc)
    if when > now + FUTURE_SKEW:
        return "last_checked_at is in the future"
    if pin is not None:
        published = _parse_dt(pin.get("published_at"))
        if published is not None and when < published:
            return "last_checked_at is before the pin was published"
        chk = (pin.get("metadata") or {}).get("checked") if isinstance(pin.get("metadata"), dict) else None
        start = _incident_start((chk or {}).get("incident_start")) if isinstance(chk, dict) else None
        if start is not None and when < start:
            return "last_checked_at is before the incident"
    return None


def last_checked_at(entry, pin: dict | None = None) -> str:
    """The entry's value when valid, else ''."""
    return "" if entry_problem(entry, pin) else entry["last_checked_at"]


def check_outcome(entry, pin: dict | None = None) -> str:
    """The entry's outcome when the entry is valid, else ''."""
    return "" if entry_problem(entry, pin) else str(entry.get("outcome") or "")


def last_review(entry, pin: dict | None = None, material_update_at=None) -> dict:
    """The map's Last reviewed date for one pin.

    {"last_reviewed_at": the later of a valid last_checked_at and the last
     owner-approved material update (an update is itself a review of the
     sources), or None; "review_outcome": the check's outcome, only when that
     check is the latest review, else None}. Nothing is invented: no record,
    no date."""
    checked = last_checked_at(entry, pin)
    c_dt = _parse_dt(checked)
    u_dt = _parse_dt(material_update_at)
    if c_dt is None and u_dt is None:
        return {"last_reviewed_at": None, "review_outcome": None}
    if u_dt is not None and (c_dt is None or u_dt >= c_dt):
        return {"last_reviewed_at": str(material_update_at), "review_outcome": None}
    return {"last_reviewed_at": checked, "review_outcome": check_outcome(entry, pin) or None}


def problems(pins: list[dict], path: pathlib.Path | None = None,
             now: datetime | None = None) -> list[str]:
    """One line per problem in the file. Pins with no entry are fine."""
    try:
        entries = read_file(path)["pins"]
    except PinClockError as exc:
        return [str(exc)]
    by_id = {p["signal_id"]: p for p in pins}
    out = []
    for sid in sorted(entries):
        if sid not in by_id:
            out.append(f"{sid[:8]}: not a hand-checked pin in any batch")
            continue
        why = entry_problem(entries[sid], by_id[sid], now)
        if why:
            out.append(f"{sid[:8]}: {why}")
    return out


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def record_check(signal_id: str, checked_at: str, *, record: str, outcome: str | None = None,
                 path: pathlib.Path | None = None, index_path: pathlib.Path | None = None,
                 now: datetime | None = None) -> dict:
    """The routine writer: set one pin's last_checked_at, with the record the
    check is written in and, optionally, its outcome. Nothing else.

    Refuses an unknown pin, a malformed or future time, a time before the pin
    was published, and a time earlier than the one already recorded (clocks
    only move forward). Returns the entry written."""
    from tools.checked_signals import load_index, INDEX_PATH
    p = pathlib.Path(path or CLOCKS_FILE)
    cs = load_index(pathlib.Path(index_path or INDEX_PATH))
    by_id = {s["signal_id"]: s for s in cs.signals}
    if signal_id not in by_id:
        raise PinClockError(f"{signal_id}: not a hand-checked pin in any batch")
    entry = {"last_checked_at": checked_at, "record": record}
    if outcome:
        entry["outcome"] = outcome
    why = entry_problem(entry, by_id[signal_id], now)
    if why:
        raise PinClockError(f"{signal_id[:8]}: {why}")
    data = read_file(p)
    old = data["pins"].get(signal_id)
    if isinstance(old, dict) and _parse_dt(old.get("last_checked_at")) \
            and _parse_dt(checked_at) < _parse_dt(old["last_checked_at"]):
        raise PinClockError(f"{signal_id[:8]}: last_checked_at may only move forward "
                            f"({old['last_checked_at']} is already recorded)")
    data["pins"][signal_id] = entry
    data["pins"] = dict(sorted(data["pins"].items()))
    p.write_text(_dump(data), encoding="utf-8")
    return entry


def routine_write_problems(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Is the change from `before` to `after` a valid routine check?

    Both map a repo-relative path (under docs/signals or docs/approvals) to the
    file's text, None for a file that does not exist. A routine may only add or
    move forward last_checked_at values in docs/signals/pin-clocks.json."""
    clocks_rel = f"docs/signals/{CLOCKS_NAME}"
    out: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if rel == clocks_rel:
            continue
        if before.get(rel) != after.get(rel):
            out.append(f"{rel}: a routine check may change only last_checked_at (with its "
                       f"record and outcome) in {clocks_rel}; this file changed")
    old_text, new_text = before.get(clocks_rel), after.get(clocks_rel)
    if old_text == new_text:
        return out
    if new_text is None:
        return out + [f"{clocks_rel}: a routine check may not delete the clocks file"]
    try:
        old = json.loads(old_text) if old_text is not None else empty_file()
        new = json.loads(new_text)
    except ValueError as exc:
        return out + [f"{clocks_rel}: not valid JSON ({exc})"]
    if not isinstance(new, dict) or not isinstance(new.get("pins"), dict) \
            or not isinstance(old, dict) or not isinstance(old.get("pins"), dict):
        return out + [f"{clocks_rel}: must keep its pins object"]
    for k in sorted((set(old) | set(new)) - {"pins"}):
        if old_text is not None and old.get(k) != new.get(k):
            out.append(f"{clocks_rel}: a routine check may not change {k!r}")
    if old_text is None and (new.get("schema") != SCHEMA or new.get("version") != VERSION):
        out.append(f"{clocks_rel}: must be schema {SCHEMA} version {VERSION}")
    op, np_ = old["pins"], new["pins"]
    for sid in sorted(set(op) - set(np_)):
        out.append(f"{sid[:8]}: a routine check may not remove a pin's last_checked_at")
    for sid in sorted(np_):
        e = np_[sid]
        if not isinstance(e, dict) or (set(e) - ENTRY_KEYS) or (REQUIRED_KEYS - set(e)):
            out.append(f"{sid[:8]}: a routine check may record only last_checked_at, "
                       f"its record and an outcome")
            continue
        if e.get("outcome") is not None and e.get("outcome") not in OUTCOMES:
            out.append(f"{sid[:8]}: outcome must be one of {sorted(OUTCOMES)}")
            continue
        if wording_problem(e.get("record")):
            out.append(f"{sid[:8]}: record: {wording_problem(e.get('record'))}")
            continue
        if op.get(sid) == e:
            continue
        new_t = _parse_dt(e.get("last_checked_at"))
        if new_t is None:
            out.append(f"{sid[:8]}: last_checked_at must be a date-time with a UTC offset")
            continue
        old_t = _parse_dt((op.get(sid) or {}).get("last_checked_at")) if isinstance(op.get(sid), dict) else None
        if old_t is not None and new_t < old_t:
            out.append(f"{sid[:8]}: last_checked_at may only move forward")
    return out


def _snapshot_worktree(repo: pathlib.Path) -> dict[str, str]:
    out = {}
    for top in ("docs/signals", "docs/approvals"):
        base = repo / top
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    out[f.relative_to(repo).as_posix()] = f.read_text(encoding="utf-8")
    return out


def _snapshot_ref(repo: pathlib.Path, ref: str) -> dict[str, str]:
    names = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref, "--",
                            "docs/signals", "docs/approvals"],
                           capture_output=True, text=True, timeout=60, check=True).stdout.split()
    out = {}
    for n in names:
        out[n] = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{n}"],
                                capture_output=True, text=True, timeout=60, check=True).stdout
    return out


def check_routine(repo: pathlib.Path = ROOT, base: str = "HEAD") -> list[str]:
    """routine_write_problems() between git `base` and the working tree."""
    return routine_write_problems(_snapshot_ref(repo, base), _snapshot_worktree(repo))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="check docs/signals/pin-clocks.json")
    c = sub.add_parser("check-routine", help="is the working-tree change a valid routine write?")
    c.add_argument("--base", default="HEAD")
    r = sub.add_parser("record", help="record one written check of a pin's sources")
    r.add_argument("signal_id")
    r.add_argument("--at", required=True, help="when the check ran, ISO date-time with offset")
    r.add_argument("--record", required=True, help="the log the check is written in")
    r.add_argument("--outcome", choices=sorted(OUTCOMES))
    args = ap.parse_args(argv)
    if args.cmd == "record":
        try:
            e = record_check(args.signal_id, args.at, record=args.record, outcome=args.outcome)
        except PinClockError as exc:
            print(f"[pin-clocks] REFUSED: {exc}")
            return 1
        print(f"[pin-clocks] recorded {args.signal_id[:8]}: {e}")
        return 0
    if args.cmd == "verify":
        from tools.checked_signals import load_index
        cs = load_index()
        found = problems(cs.signals)
        n = len(load())
        for line in found:
            print(f"[pin-clocks] {line}")
        print(f"[pin-clocks] {n} of {len(cs.signals)} pin(s) have a last_checked_at; "
              f"{len(found)} problem(s)")
        return 1 if found else 0
    found = check_routine(ROOT, args.base)
    for line in found:
        print(f"[pin-clocks] REFUSED: {line}")
    if not found:
        print("[pin-clocks] OK: only review entries changed (or nothing did)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
