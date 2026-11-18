"""FORGE Journal (18 Nov 2026): written field notes tied to a place on the map.

The map is the index; the journal holds the writing. docs/journal/entries.json
holds the entries the owner has decided to publish, and nothing else: the
repository is committed and pushed, so drafts never go in it. Every entry must
pass every rule below or the build stops before anything is written to dist/.

This is the frontend foundation only. There is no upload, sign-in, location
sharing or photo attachment yet; an entry reaches the site when the owner adds
it to entries.json and runs tools/publish.py. Policy: docs/journal/JOURNAL.md.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
ENTRIES_FILE = REPO / "docs" / "journal" / "entries.json"

REQUIRED_TEXT = ("id", "title", "published", "author", "place", "summary")
ALLOWED_KEYS = set(REQUIRED_TEXT) | {"body", "pin", "sources"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PIN_RE = re.compile(r"^[0-9a-f]{8}$")
MAX_SUMMARY = 280


class JournalError(ValueError):
    """entries.json is unreadable or an entry breaks a rule."""


def load_entries(path: pathlib.Path = ENTRIES_FILE) -> list[dict]:
    """The raw entries in the file; a missing file means no entries."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JournalError(f"{path.name} is not valid JSON: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise JournalError(f"{path.name} must be an object with an 'entries' list")
    return entries


def _parse_date(value: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def validate(entries: list, pin_ids: set[str] | None = None,
             today: _dt.date | None = None) -> list[str]:
    """Every rule an entry breaks, as plain lines. Empty means all pass.

    pin_ids: the 8-character ids of pins live on the map. When given, an
    entry's pin must be one of them, so a journal link never opens nothing.
    """
    today = today or _dt.date.today()
    problems: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"entry {i + 1}"
        if not isinstance(e, dict):
            problems.append(f"{where}: must be an object")
            continue
        if isinstance(e.get("id"), str) and e["id"]:
            where = f"entry {e['id']!r}"
        if e.get("example") or e.get("sample"):
            problems.append(f"{where}: example entries are for previews and never publish")
        extra = sorted(set(e) - ALLOWED_KEYS - {"example", "sample"})
        if extra:
            problems.append(f"{where}: unknown field(s) {', '.join(extra)}")
        for key in REQUIRED_TEXT:
            val = e.get(key)
            if not isinstance(val, str) or not val.strip():
                problems.append(f"{where}: '{key}' is required text")
        eid = e.get("id")
        if isinstance(eid, str) and eid:
            if not ID_RE.match(eid):
                problems.append(f"{where}: id must be lower-case words joined by hyphens")
            if eid in seen:
                problems.append(f"{where}: id is used twice")
            seen.add(eid)
        pub = e.get("published")
        if isinstance(pub, str) and pub:
            d = _parse_date(pub)
            if d is None:
                problems.append(f"{where}: 'published' must be a date like 2026-11-18")
            elif d > today:
                problems.append(f"{where}: 'published' is in the future")
        summary = e.get("summary")
        if isinstance(summary, str) and len(summary) > MAX_SUMMARY:
            problems.append(f"{where}: summary is over {MAX_SUMMARY} characters")
        body = e.get("body")
        if not isinstance(body, list) or not body or not all(
                isinstance(p, str) and p.strip() for p in body):
            problems.append(f"{where}: 'body' must be a list of paragraphs")
        pin = e.get("pin")
        if pin is not None:
            if not isinstance(pin, str) or not PIN_RE.match(pin):
                problems.append(f"{where}: 'pin' must be a pin's 8-character id")
            elif pin_ids is not None and pin not in pin_ids:
                problems.append(f"{where}: pin {pin} is not on the live map")
        sources = e.get("sources", [])
        if not isinstance(sources, list):
            problems.append(f"{where}: 'sources' must be a list")
        else:
            for s in sources:
                ok = (isinstance(s, dict) and isinstance(s.get("label"), str) and s["label"].strip()
                      and isinstance(s.get("url"), str) and s["url"].startswith("https://"))
                if not ok:
                    problems.append(f"{where}: each source needs a label and an https:// url")
                    break
    return problems


def _fmt(d: _dt.date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def published_entries(entries: list, pin_ids: set[str] | None = None,
                      today: _dt.date | None = None) -> list[dict]:
    """The entries the page shows, newest first. Raises JournalError if any
    entry breaks a rule: a bad entry stops the build, it is never dropped."""
    problems = validate(entries, pin_ids, today)
    if problems:
        raise JournalError("journal entries refused:\n  " + "\n  ".join(problems))
    out = []
    for e in entries:
        d = _dt.date.fromisoformat(e["published"])
        out.append({
            "id": e["id"],
            "title": e["title"].strip(),
            "published": e["published"],
            "published_fmt": _fmt(d),
            "author": e["author"].strip(),
            "place": e["place"].strip(),
            "summary": e["summary"].strip(),
            "body": [p.strip() for p in e["body"]],
            "pin_url": f"index.html?signal={e['pin']}" if e.get("pin") else None,
            "sources": [{"label": s["label"].strip(), "url": s["url"]} for s in e.get("sources", [])],
        })
    out.sort(key=lambda x: (x["published"], x["id"]), reverse=True)
    return out
