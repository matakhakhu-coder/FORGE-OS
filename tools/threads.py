#!/usr/bin/env python3
"""Story threads: events a source has placed in the same named conflict.

Policy (owner decision, 11 Nov 2026, docs/threads/POLICY.md): a line on the
map only ever stands for something a source said. A thread is one file in
docs/threads/; each member pin needs its OWN source placing that event in the
thread's conflict by name, with who says so. Same suburb, same week, same
weapon or a pattern noticed by us never makes a member.

What reaches the page and records.json: only threads with status "approved"
that have at least MIN_DRAWN approved members with coordinates. Draft and
retired threads, and approved threads with fewer members, are checked here and
kept private. Members come out sorted by incident time; an arrow between two
neighbours is drawn only when their incident windows do not overlap.

Context entries (a ceasefire, a court order, a suspension) are dated lines in
the thread's list. They are never pins and never line ends.

Usage:
    python tools/threads.py check      # validate every thread against the checked pins
    python tools/threads.py list       # one line per thread: status, members, public or not
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
THREADS_DIR = ROOT / "docs" / "threads"
SCHEMA = "forge.thread"
VERSION = 1

STATUSES = ("draft", "approved", "retired")
MIN_DRAWN = 2          # fewer approved members with coordinates: nothing public
MAX_MEMBERS = 12       # pilot cap (scope, Performance)
CONTEXT_PRECISIONS = ("day", "month")
# Pins placed only this coarsely get a hollow line end and "approximate place".
APPROX_PRECISIONS = {"suburb", "village", "town", "farm", "precinct"}

CAPTION = ("Order in time. Sources place each event in this dispute. "
           "One did not cause the next.")
RECORDS_CAVEAT = ("Relationships as stated by the cited source or party. Not police findings. "
                  "Order in time does not mean one event caused another.")

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CLAIM_RE = re.compile(r"^(police|source|court|party: .{2,60})$")
_TOP = {"schema", "version", "thread_id", "title", "status", "summary", "basis",
        "members", "context", "excluded", "approved_by", "approved_at", "reviewed_at", "note"}
_MEMBER = {"signal_id", "claim_by", "basis", "approved_by", "approved_at", "note"}
_CONTEXT = {"date", "precision", "text", "basis", "note"}
_EXCLUDED = {"signal_id", "label", "reason"}
_BASIS = {"quote", "url", "outlet", "published", "archive", "read_at"}


class ThreadError(ValueError):
    pass


def _is_url(v) -> bool:
    return str(v or "").strip().lower().startswith(("https://", "http://"))


def _iso_day(v, where: str) -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        raise ThreadError(f"{where}: needs an ISO date (YYYY-MM-DD)") from None


def _basis(b, where: str) -> dict:
    """A basis is a quote AND a link: the words a line or entry rests on."""
    if not isinstance(b, dict):
        raise ThreadError(f"{where}: basis must be an object with quote and url")
    extra = set(b) - _BASIS
    if extra:
        raise ThreadError(f"{where}: unknown basis fields {sorted(extra)}")
    quote = str(b.get("quote") or "").strip()
    url = str(b.get("url") or "").strip()
    if not quote or not _is_url(url):
        raise ThreadError(f"{where}: basis needs both a quote and an http(s) url")
    if b.get("archive") and not _is_url(b["archive"]):
        raise ThreadError(f"{where}: basis.archive must be an http(s) link")
    if b.get("published"):
        _iso_day(b["published"], f"{where}.published")
    return b


def load_threads(threads_dir: pathlib.Path = THREADS_DIR) -> list[dict]:
    """Every docs/threads/*.json file, parsed (not yet validated)."""
    out = []
    if not threads_dir.is_dir():
        return out
    for p in sorted(threads_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ThreadError(f"{p.name}: not valid JSON ({exc})") from None
        if not isinstance(d, dict):
            raise ThreadError(f"{p.name}: must be an object")
        d["_file"] = p.name
        out.append(d)
    return out


def validate(threads: list[dict], pins: dict[str, dict], windows: dict[str, tuple | None]) -> None:
    """Raise ThreadError on the first problem.

    pins: full signal_id -> active checked pin (retracted pins left out).
    windows: full signal_id -> (earliest, latest) incident start, or None."""
    seen_ids: set[str] = set()
    for t in threads:
        where = t.get("_file", "thread")
        extra = set(t) - _TOP - {"_file"}
        if extra:
            raise ThreadError(f"{where}: unknown fields {sorted(extra)}")
        if t.get("schema") != SCHEMA or t.get("version") != VERSION:
            raise ThreadError(f"{where}: schema must be {SCHEMA!r} version {VERSION}")
        tid = str(t.get("thread_id") or "")
        if not _ID_RE.match(tid):
            raise ThreadError(f"{where}: thread_id must be lower-case words joined by hyphens")
        if tid in seen_ids:
            raise ThreadError(f"{where}: thread_id {tid} is used twice")
        seen_ids.add(tid)
        if where != f"{tid}.json" and where != "thread":
            raise ThreadError(f"{where}: file must be named {tid}.json")
        if not str(t.get("title") or "").strip() or len(t["title"]) > 120:
            raise ThreadError(f"{where}: title is required, at most 120 characters")
        status = t.get("status")
        if status not in STATUSES:
            raise ThreadError(f"{where}: status must be one of {list(STATUSES)}")
        basis = t.get("basis")
        if not isinstance(basis, list) or not basis:
            raise ThreadError(f"{where}: basis must list at least one source naming the conflict")
        for i, b in enumerate(basis):
            _basis(b, f"{where}: basis[{i}]")
        if status == "approved":
            if not str(t.get("approved_by") or "").strip():
                raise ThreadError(f"{where}: an approved thread needs approved_by")
            _iso_day(t.get("approved_at"), f"{where}: approved_at")

        members = t.get("members")
        if not isinstance(members, list):
            raise ThreadError(f"{where}: members must be a list")
        if len(members) > MAX_MEMBERS:
            raise ThreadError(f"{where}: at most {MAX_MEMBERS} members")
        in_thread: set[str] = set()
        for i, m in enumerate(members):
            mw = f"{where}: members[{i}]"
            if not isinstance(m, dict):
                raise ThreadError(f"{mw} is not an object")
            extra = set(m) - _MEMBER
            if extra:
                raise ThreadError(f"{mw}: unknown fields {sorted(extra)}")
            sid = str(m.get("signal_id") or "").lower()
            if not _UUID_RE.match(sid):
                raise ThreadError(f"{mw}: signal_id must be the full id of a checked pin")
            if sid not in pins:
                raise ThreadError(f"{mw}: {sid[:8]} is not an active checked pin "
                                  "(held, rejected or retracted pins cannot join a thread)")
            if sid in in_thread:
                raise ThreadError(f"{mw}: {sid[:8]} is listed twice")
            in_thread.add(sid)
            if not _CLAIM_RE.match(str(m.get("claim_by") or "")):
                raise ThreadError(f"{mw}: claim_by must be police, source, court or 'party: <name>'")
            _basis(m.get("basis"), f"{mw}")
            if m.get("approved_by") is not None or m.get("approved_at") is not None:
                if not str(m.get("approved_by") or "").strip():
                    raise ThreadError(f"{mw}: approved_at without approved_by")
                _iso_day(m.get("approved_at"), f"{mw}: approved_at")

        context = t.get("context") or []
        if not isinstance(context, list):
            raise ThreadError(f"{where}: context must be a list")
        for i, c in enumerate(context):
            cw = f"{where}: context[{i}]"
            if not isinstance(c, dict):
                raise ThreadError(f"{cw} is not an object")
            extra = set(c) - _CONTEXT
            if extra:
                raise ThreadError(f"{cw}: unknown fields {sorted(extra)} (context entries are not pins)")
            _iso_day(c.get("date"), cw)
            if c.get("precision", "day") not in CONTEXT_PRECISIONS:
                raise ThreadError(f"{cw}: precision must be one of {list(CONTEXT_PRECISIONS)}")
            if not str(c.get("text") or "").strip():
                raise ThreadError(f"{cw}: text is required")
            _basis(c.get("basis"), cw)

        excluded = t.get("excluded") or []
        if not isinstance(excluded, list):
            raise ThreadError(f"{where}: excluded must be a list")
        for i, x in enumerate(excluded):
            xw = f"{where}: excluded[{i}]"
            if not isinstance(x, dict) or set(x) - _EXCLUDED or not str(x.get("reason") or "").strip():
                raise ThreadError(f"{xw}: needs a reason (and only signal_id, label, reason)")
            xs = str(x.get("signal_id") or "").lower()
            if xs and xs in in_thread:
                raise ThreadError(f"{xw}: {xs[:8]} is both a member and excluded")


def _approved_members(t: dict) -> list[dict]:
    return [m for m in t.get("members") or [] if str(m.get("approved_by") or "").strip()]


def is_public(t: dict, published: dict[str, dict]) -> bool:
    """Approved, and at least MIN_DRAWN approved members on published pins
    with coordinates. Anything else never reaches the page."""
    if t.get("status") != "approved":
        return False
    n = 0
    for m in _approved_members(t):
        p = published.get(str(m["signal_id"]).lower())
        if p and p.get("lat") is not None and p.get("lng") is not None:
            n += 1
    return n >= MIN_DRAWN


def _parse_utc(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def overlap(a: tuple | None, b: tuple | None) -> bool:
    """True when two incident windows overlap, or either is unknown."""
    if not a or not b:
        return True
    return a[0] < b[1] and b[0] < a[1]


def order_members(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """rows: dicts with 'signal' and 'window' ((earliest, latest) or None).

    Returns (dated rows in incident order, undated rows, segments). Segments
    join neighbours in that order; 'arrow' is False when their windows
    overlap ("same period, order unknown"). Undated rows are never line ends."""
    dated = sorted((r for r in rows if r.get("window")), key=lambda r: (r["window"][0], r["signal"]))
    undated = [r for r in rows if not r.get("window")]
    segs = []
    for a, b in zip(dated, dated[1:]):
        segs.append({"from": a["signal"], "to": b["signal"],
                     "arrow": not overlap(a["window"], b["window"])})
    return dated, undated, segs


def public_threads(threads: list[dict], published: list[dict]) -> list[dict]:
    """The page / records.json form of every public thread. published: the
    publisher's pin dicts (signal_id, title, lat, lng, incident, incident_at,
    incident_end, precision, uncertainty_m)."""
    by_id = {str(s["signal_id"]).lower(): s for s in published}
    out = []
    for t in threads:
        if not is_public(t, by_id):
            continue
        rows = []
        for m in _approved_members(t):
            p = by_id.get(str(m["signal_id"]).lower())
            if not p or p.get("lat") is None or p.get("lng") is None:
                continue
            a, z = _parse_utc(p.get("incident_at")), _parse_utc(p.get("incident_end"))
            b = m["basis"]
            rows.append({
                "signal":   p["signal_id"][:8],
                "title":    p.get("title") or "",
                "lat":      p["lat"], "lng": p["lng"],
                "when":     p.get("incident") or "",
                "approximate": (p.get("precision") in APPROX_PRECISIONS),
                "claim_by": m["claim_by"],
                "quote":    b["quote"], "url": b["url"],
                "outlet":   b.get("outlet") or "",
                "window":   (a, z) if a and z else None,
            })
        dated, undated, segs = order_members(rows)
        for n, r in enumerate(dated, 1):
            r["n"] = n
        members = []
        for r in dated + undated:
            w = r.pop("window")
            r["incident_at"] = w[0].strftime("%Y-%m-%dT%H:%M:%SZ") if w else None
            r.setdefault("n", None)
            members.append(r)
        context = sorted(
            ({"date": c["date"], "precision": c.get("precision", "day"), "text": c["text"],
              "quote": c["basis"]["quote"], "url": c["basis"]["url"],
              "outlet": c["basis"].get("outlet") or ""} for c in t.get("context") or []),
            key=lambda c: c["date"])
        out.append({
            "id": t["thread_id"], "title": t["title"], "caption": CAPTION,
            "summary": t.get("summary") or "",
            "members": members, "segments": segs, "context": context,
            "approved_at": str(t.get("approved_at") or "")[:10],
        })
    return out


def check(threads_dir: pathlib.Path = THREADS_DIR, index_path=None) -> list[dict]:
    """Load and validate every thread against the checked batches."""
    sys.path.insert(0, str(ROOT))
    from tools import checked_signals as cs_mod
    cs = cs_mod.load_index(index_path or cs_mod.INDEX_PATH)
    pins = {s["signal_id"]: s for s in cs.active}
    windows = {sid: cs_mod.incident_window(s["metadata"]["checked"]) for sid, s in pins.items()}
    threads = load_threads(threads_dir)
    validate(threads, pins, windows)
    return threads


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "check"
    try:
        threads = check()
    except Exception as exc:  # noqa: BLE001 - CLI reports any refusal
        print(f"[threads] REFUSED: {exc}")
        return 2
    if cmd == "list":
        for t in threads:
            am = len(_approved_members(t))
            print(f"{t['thread_id']:40} {t['status']:9} members {len(t.get('members') or [])} "
                  f"(approved {am}), context {len(t.get('context') or [])}, "
                  f"public: {'yes' if t['status'] == 'approved' and am >= MIN_DRAWN else 'no'}")
    else:
        print(f"[threads] {len(threads)} thread(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
