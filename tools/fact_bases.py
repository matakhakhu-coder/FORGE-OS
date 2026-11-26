""""What each fact rests on" for new pins: check entries, add them, gate a run.

The panel section is drawn from docs/signals/fact-bases.json (publish.py
_fact_basis_for -> m.fact_basis -> map.html). A pin with no entry, or with an
entry that is not "audited" with exactly six facts, shows "not yet audited" and
an empty list.

An unattended sweep writes one entry per new pin into a JSON file, from the
source pages it read for the pin (see docs/signals/FACT_BASES.md), then:

    python tools/fact_bases.py quotes --entries new.json --texts <saved page text> ...
    python tools/fact_bases.py add --entries new.json --authorization standing-<id>

`quotes` checks every quote word for word against the page text the sweep
saved (outside the repo). `add` validates each entry against its pin's checked
batch and provenance and writes it into fact-bases.json. tools/autopublish.py
refuses a run while any of its pins has no valid entry (gate_problems), so a
pin cannot go live with the section empty.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNALS = ROOT / "docs" / "signals"
FACT_BASES = SIGNALS / "fact-bases.json"
PROVENANCE = SIGNALS / "provenance.json"

FACT_COUNT = 6   # publish._fact_basis_for shows an entry only with exactly six facts
# Bases that say more than one independent account stands behind a fact.
INDEPENDENT_BASES = {"Independent origins identified", "Two reports"}
NOT_REPORTED = "Not reported in the saved sources"
APPROX_WORDS = ("approximate", "approx.", "estimated")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def load_batch_pins() -> dict[str, dict]:
    """signal_id -> {'checked': ..., 'batch': file name, 'review_status': ...} from every checked batch."""
    out = {}
    for f in sorted(SIGNALS.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("schema") != "forge.checked_signals.batch":
            continue
        for s in data.get("signals") or []:
            out[s["signal_id"]] = {"checked": (s.get("metadata") or {}).get("checked") or {},
                                   "batch": f.name, "review_status": data.get("review_status") or ""}
    return out


def _provenance() -> dict:
    try:
        return json.loads(PROVENANCE.read_text(encoding="utf-8")).get("pins") or {}
    except (OSError, ValueError):
        return {}


def validate_entry(sid: str, entry: dict, pin: dict | None, prov: dict | None = None) -> list[str]:
    """Problems with one pin's entry; an empty list means it can show on the map."""
    if not isinstance(entry, dict):
        return [f"{sid[:8]}: no fact-basis entry"]
    if pin is None:
        return [f"{sid[:8]}: not in any checked batch"]
    errs = []
    say = lambda m: errs.append(f"{sid[:8]}: {m}")
    if entry.get("status") != "audited":
        say("status must be 'audited'")
    if not re.fullmatch(r"\d{1,2} [A-Z][a-z]+ \d{4}", str(entry.get("audited_on") or "")):
        say("audited_on must be the audit date, e.g. '26 November 2026' (the panel shows it)")
    if not isinstance(entry.get("police_party"), bool):
        say("police_party must be true or false")
    facts = entry.get("facts") or []
    if len(facts) != FACT_COUNT:
        say(f"needs exactly {FACT_COUNT} facts (has {len(facts)}); the map hides any other number")
    allowed = {s.get("url") for s in pin["checked"].get("sources") or [] if s.get("url")}
    same_statement = (prov or {}).get("independence") == "same_statement"
    labels = set()
    for i, f in enumerate(facts, 1):
        for k in ("label", "claim", "basis"):
            if not str(f.get(k) or "").strip():
                say(f"fact {i}: {k} is empty")
        labels.add(f.get("label"))
        srcs = f.get("sources") or []
        if not srcs and f.get("basis") != NOT_REPORTED:
            say(f"fact {i} ({f.get('label')}): no source; use basis '{NOT_REPORTED}' if none says it")
        for s in srcs:
            if s.get("url") not in allowed:
                say(f"fact {i}: source {s.get('url')} is not one of the pin's published sources")
            if not str(s.get("quote") or "").strip() or not str(s.get("name") or "").strip():
                say(f"fact {i}: every source needs a name and a word-for-word quote")
        if f.get("basis") in INDEPENDENT_BASES:
            if len({s.get("url") for s in srcs}) < 2:
                say(f"fact {i}: basis '{f['basis']}' claims more than one account but cites one source")
            if same_statement:
                say(f"fact {i}: basis '{f['basis']}' but the pin's reports rest on one statement")
        if not isinstance(f.get("conflicts", []), list):
            say(f"fact {i}: conflicts must be a list")
    if "Place" not in labels:
        say("needs a 'Place' fact")
    loc = str(pin["checked"].get("location") or "").upper()
    if loc.startswith(("APPROX", "ESTIMATED")):
        place = next((f for f in facts if f.get("label") == "Place"), {})
        text = (str(place.get("claim") or "") + " " + str(place.get("detail") or "")).lower()
        if not any(w in text for w in APPROX_WORDS):
            say("the map point is approximate or estimated; the Place fact must say so")
    if same_statement and not any(w in str(f.get("detail") or "").lower() for f in facts for w in ("same statement", "one statement")) \
            and not any(str(f.get("basis") or "").endswith("account only") for f in facts):
        say("the reports rest on one statement; say 'same statement' in a fact's detail")
    return errs


def gate_problems(pins: list[str], fact_bases: dict | None = None) -> list[str]:
    """Used by tools/autopublish.py before anything is built: every pin needs a valid entry."""
    if fact_bases is None:
        fact_bases = json.loads(FACT_BASES.read_text(encoding="utf-8")).get("pins") or {}
    batch, prov = load_batch_pins(), _provenance()
    errs = []
    for sid in pins:
        errs += validate_entry(sid, fact_bases.get(sid), batch.get(sid), prov.get(sid))
    return errs


def quote_problems(entries: dict, texts: list[str]) -> list[str]:
    """Every quote must appear word for word (spacing, curly quotes and dashes aside) in a saved page."""
    hay = [_norm(t) for t in texts]
    bad = []
    for sid, e in entries.items():
        for f in e.get("facts") or []:
            for s in (f.get("sources") or []) + (f.get("conflicts") or []):
                q = _norm(s.get("quote") or "")
                if not q or not any(q in h for h in hay):
                    bad.append(f"{sid[:8]} {f.get('label')}: quote not found word for word: {s.get('quote')!r}")
    return bad


def add(entries: dict, authorization: str) -> list[str]:
    """Validate and write entries into fact-bases.json. Writes nothing if any entry fails."""
    batch, prov = load_batch_pins(), _provenance()
    errs = []
    for sid, e in entries.items():
        errs += validate_entry(sid, e, batch.get(sid), prov.get(sid))
    if errs:
        return errs
    data = json.loads(FACT_BASES.read_text(encoding="utf-8"))
    for sid, e in entries.items():
        e = dict(e)
        e["approval"] = {"by": "standing", "authorization": authorization, "checked_by": "unattended-run"}
        data["pins"][sid] = e
    FACT_BASES.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quotes")
    q.add_argument("--entries", required=True)
    q.add_argument("--texts", nargs="+", required=True)
    a = sub.add_parser("add")
    a.add_argument("--entries", required=True)
    a.add_argument("--authorization", required=True)
    c = sub.add_parser("check")
    c.add_argument("--pins", nargs="+", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "check":
        errs = gate_problems(args.pins)
    else:
        entries = json.loads(pathlib.Path(args.entries).read_text(encoding="utf-8"))
        if args.cmd == "quotes":
            errs = quote_problems(entries, [pathlib.Path(t).read_text(encoding="utf-8") for t in args.texts])
        else:
            errs = add(entries, args.authorization)
    for e in errs:
        print(f"[fact-bases] {e}")
    print(f"[fact-bases] {'REFUSED' if errs else 'ok'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
