#!/usr/bin/env python3
"""
FORGE — Sprint 4 evidence layer  (tools/evidence_layer.py)
=========================================================
Private structured coding, the source-independence check and the gated
Wayback backup for the hand-checked pins.

    python tools/evidence_layer.py code           # build private coding, load it into FORGE_DB
    python tools/evidence_layer.py independence   # counterintel on saved article text (private)
    python tools/evidence_layer.py proximity      # place + incident-time review list (private)
    python tools/evidence_layer.py check          # completeness check for every checked pin
    python tools/evidence_layer.py wayback        # dry run: lists the source links
    python tools/evidence_layer.py wayback --submit   # sends them to the Internet Archive

Private means: under private/ (git-ignored, never published) and in the
FORGE_DB database tables checked_coding / checked_independence /
checked_proximity, which publish.py never reads. Nothing here writes to the
signals table or to docs/signals.

Inputs (all under private/):
    article_text.json  saved text of every source article, keyed by URL
                       ({url: {title, author, date, sitename, text}})
    coding_seed.json   hand coding from the pattern-case experiment
                       ({id, dead, m, f, fr, wounded, method, setting, ctx, who,
                         arrests, station, spk, ...})

The Wayback step contacts web.archive.org and stores copies of the source
pages there. It only runs with --submit, and --submit refuses unless
FORGE_WAYBACK_APPROVED=1 is also set, so it cannot run by accident.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import checked_signals as cs  # noqa: E402
from forge_modules.counterintel.independence import assess  # noqa: E402

PRIVATE = Path(os.environ.get("FORGE_PRIVATE_DIR", ROOT / "private"))
PROVENANCE = ROOT / "docs" / "signals" / "provenance.json"
INDEPENDENCE_VALUES = {"single_report", "same_outlet", "same_statement",
                       "shared_wording", "partly_independent", "independent"}
NOT_STATED = None   # unknowns are stored as null, never guessed


def db_path() -> str:
    return os.environ.get("FORGE_DB") or str(ROOT / "database.db")


def _load(name: str):
    p = PRIVATE / name
    if not p.exists():
        raise SystemExit(f"[evidence] missing private input: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def load_provenance() -> dict:
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))["pins"]


def _int_or_none(v):
    return v if isinstance(v, int) else None


_ARREST_RX = re.compile(r"\b(arrested|arrest of|were arrested|was arrested|apprehended|taken into custody)\b", re.I)
_NO_ARREST_RX = re.compile(r"\bno (one has been |arrests? (have|has) (yet )?been )?(arrest|made)", re.I)


def first_arrest_report(sources: list[dict], texts: dict) -> str | None:
    """Date of the earliest source whose text reports an arrest."""
    dates = []
    for s in sources:
        a = texts.get(s["url"]) or {}
        t = a.get("text") or ""
        if _ARREST_RX.search(t) and a.get("date"):
            dates.append(a["date"][:10])
    return min(dates) if dates else None


_NO_MOTIVE = ("none given", "motive unknown", "motive under investigation", "unknown")


def motive(f: dict) -> dict:
    """Motive as the reports attribute it. 'None given' etc. is stored as null,
    with the reports' own words kept in as_reported."""
    raw = (f.get("ctx") or "").strip()
    first = re.split(r";(?![^()]*\))", raw)[0].strip()   # ';' outside brackets
    given = bool(raw) and first.lower() not in _NO_MOTIVE
    return {"motive": first if given else NOT_STATED,
            "attributed_by": (f.get("who") or NOT_STATED) if given else NOT_STATED,
            "as_reported": raw or NOT_STATED}


_STATION_RX = re.compile(
    r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:police station|SAPS|police (?:registered|had registered|are investigating|have registered|responded|were called|confirmed))")


def station_from_text(sources: list[dict], texts: dict) -> str | None:
    """Police station named in the article text, when exactly one is named."""
    found = set()
    for s in sources:
        for m in _STATION_RX.finditer((texts.get(s["url"]) or {}).get("text") or ""):
            name = m.group(1)
            if name.split()[0] not in {"The", "Western", "Provincial", "Southern", "Cape", "Metro", "Police", "Local"}:
                found.add(name)
    return found.pop() if len(found) == 1 else None


_NOT_A_NAME = re.compile(r"^(staff reporter|own correspondent|reporter|editor|the editor|news24|capeetc|"
                        r"people's post|daily voice|iol|ewn|sabc news|cape argus|cape times)$", re.I)


def reporter(author) -> str | None:
    """A byline that names a person; desk bylines and outlet names are null."""
    a = (author or "").strip()
    if not a or _NOT_A_NAME.match(a):
        return NOT_STATED
    return a.title() if a.isupper() else a


def build_coding() -> list[dict]:
    """One private record per active checked pin."""
    texts = _load("article_text.json")
    seed = {r["id"]: r for r in _load("coding_seed.json")}
    prov = load_provenance()
    checked = cs.load_index()
    province = cs.provinces(checked)
    out = []
    for s in checked.active:
        sid = s["signal_id"]
        chk = s["metadata"]["checked"]
        f = seed.get(sid[:8]) or {}
        p = prov.get(sid) or {}
        srcs = []
        for src in chk["sources"]:
            a = texts.get(src["url"]) or {}
            srcs.append({
                "url": src["url"], "outlet": src["name"],
                "reporter": reporter(a.get("author")),
                "byline_as_printed": a.get("author") or NOT_STATED,
                "report_date": (a.get("date") or "")[:10] or NOT_STATED,
                "text_saved": bool((a.get("text") or "").strip()),
            })
        arrests = f.get("arrests") if f.get("arrests") in ("yes", "none") else NOT_STATED
        out.append({
            "signal_id": sid,
            "title": s["title"],
            "province": province.get(sid),
            # Only Western Cape pins belong to the Sprint 5 pattern test.
            "in_pattern_test": province.get(sid) == cs.PATTERN_TEST_PROVINCE,
            "incident_start": chk["incident_start"],
            "incident_precision": chk["incident_precision"],
            "incident_event": chk["incident_event"],
            "incident_at_utc": cs.incident_sort_key(chk),
            "method": f.get("method") or NOT_STATED,
            "setting": f.get("setting") or NOT_STATED,
            "victims": {
                "dead": _int_or_none(f.get("dead")),
                "male": _int_or_none(f.get("m")),
                "female": _int_or_none(f.get("f")),
                "female_reported_unconfirmed": _int_or_none(f.get("fr")),
                "wounded": _int_or_none(f.get("wounded")),
            },
            "attributed_motive": motive(f),
            "arrests": {
                "status": arrests,   # yes / none / null = not stated
                # A hand-coded date wins: the text scan only reads English.
                "first_reported": (f.get("arrest_first_reported")
                                   or first_arrest_report(chk["sources"], texts)) if arrests == "yes" else None,
            },
            "reported_at": chk["reported_at"],
            "report_dates": sorted({x["report_date"] for x in srcs if x["report_date"]}),
            "police_station": f.get("station") or station_from_text(chk["sources"], texts),
            "police_station_basis": "hand coding" if f.get("station") else (
                "article text" if station_from_text(chk["sources"], texts) else "not stated"),
            "spokesperson": p.get("spokesperson") or [],
            "reporters": sorted({x["reporter"] for x in srcs if x["reporter"]}),
            "outlets": p.get("outlets") or sorted({x["outlet"] for x in srcs}),
            "sources": srcs,
            "coded_from": "pattern-case experiment hand coding + saved article text",
        })
    return out


def _store(table: str, rows: list[tuple[str, dict]]) -> None:
    conn = sqlite3.connect(db_path())
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (signal_id TEXT PRIMARY KEY, "
                     "data_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute(f"DELETE FROM {table}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(f"INSERT INTO {table} VALUES (?,?,?)",
                         [(sid, json.dumps(d, ensure_ascii=False), now) for sid, d in rows])
        conn.commit()
    finally:
        conn.close()


def cmd_code(a) -> int:
    rows = build_coding()
    (PRIVATE / "checked_coding.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.write:
        _store("checked_coding", [(r["signal_id"], r) for r in rows])
    unknown = {k: sum(1 for r in rows if r[k] is None) for k in ("method", "setting", "police_station")}
    unknown["motive"] = sum(1 for r in rows if r["attributed_motive"]["motive"] is None)
    unknown["arrests"] = sum(1 for r in rows if r["arrests"]["status"] is None)
    print(f"[evidence] coded {len(rows)} pins -> {PRIVATE/'checked_coding.json'}"
          + (f" and {db_path()}:checked_coding" if a.write else " (dry run: DB not written)"))
    print(f"[evidence] not stated in the reports: {unknown}")
    return 0


def cmd_independence(a) -> int:
    texts = _load("article_text.json")
    prov = load_provenance()
    results = []
    for s in cs.load_index().active:
        arts = [{"url": x["url"], "outlet": x["name"].split(" (")[0],
                 "author": (texts.get(x["url"]) or {}).get("author"),
                 "text": (texts.get(x["url"]) or {}).get("text") or ""}
                for x in s["metadata"]["checked"]["sources"]]
        r = assess(arts)
        r["signal_id"] = s["signal_id"]
        r["published_label"] = (prov.get(s["signal_id"]) or {}).get("independence")
        results.append(r)
        print(f"  {s['signal_id'][:8]}  machine={r['verdict']:<22} published={r['published_label']}")
    (PRIVATE / "independence.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if a.write:
        _store("checked_independence", [(r["signal_id"], r) for r in results])
    return 0


def cmd_proximity(a) -> int:
    from forage.engines.correlation_engine import CorrelationEngine
    r = CorrelationEngine(db_path=Path(db_path())).checked_proximity(dry_run=not a.write)
    for p in r["pairs"]:
        print(f"  {p[0][:8]} ~ {p[1][:8]}  {p[2]} km  {p[3]} h apart  ({p[4]})")
    return 0


def completeness_problems() -> list[str]:
    """Sprint 4 done-check for the public side: every active checked pin has a
    machine-readable incident date and a provenance entry."""
    probs = []
    prov = load_provenance()
    for s in cs.load_index().active:
        sid = s["signal_id"]
        if not cs.incident_sort_key(s["metadata"]["checked"]):
            probs.append(f"{sid[:8]}: no machine-readable incident date")
        p = prov.get(sid)
        if not p:
            probs.append(f"{sid[:8]}: no provenance entry in docs/signals/provenance.json")
            continue
        if p.get("independence") not in INDEPENDENCE_VALUES:
            probs.append(f"{sid[:8]}: provenance independence {p.get('independence')!r} is not a known value")
        for k in ("statement_from", "outlets", "note"):
            if not p.get(k):
                probs.append(f"{sid[:8]}: provenance {k} is empty")
    return probs


def cmd_check(a) -> int:
    probs = completeness_problems()
    for p in probs:
        print("  ✗", p)
    print(f"[evidence] {'OK' if not probs else str(len(probs)) + ' problem(s)'}")
    return 1 if probs else 0


def source_urls() -> list[str]:
    seen, out = set(), []
    for s in cs.load_index().active:
        for x in s["metadata"]["checked"]["sources"]:
            if x["url"] not in seen:
                seen.add(x["url"])
                out.append(x["url"])
    return out


def cmd_wayback(a) -> int:
    urls = source_urls()
    if not a.submit:
        for u in urls:
            print("  would archive:", u)
        print(f"[evidence] {len(urls)} source links. Dry run: nothing sent to the Internet Archive.")
        return 0
    if os.environ.get("FORGE_WAYBACK_APPROVED") != "1":
        print("[evidence] refused: --submit needs FORGE_WAYBACK_APPROVED=1 (the user's go-ahead).")
        return 2
    # Keep the earlier backup: links already saved are skipped, new results are
    # added to wayback.json rather than replacing it.
    path = PRIVATE / "wayback.json"
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    saved = {d["url"] for d in prior if d.get("ok")}
    todo = [u for u in urls if u not in saved]
    print(f"[evidence] {len(urls) - len(todo)} already archived, {len(todo)} to send")
    done = []
    for u in todo:
        try:
            req = urllib.request.Request("https://web.archive.org/save/" + u,
                                         headers={"User-Agent": "FORGE-OS source backup"})
            with urllib.request.urlopen(req, timeout=90) as r:
                loc = r.headers.get("Content-Location") or r.geturl()
            done.append({"url": u, "archive": loc, "ok": True})
        except Exception as exc:  # report, keep going
            done.append({"url": u, "error": str(exc), "ok": False})
        time.sleep(5)
    new_urls = {d["url"] for d in done}
    merged = [d for d in prior if d["url"] not in new_urls] + done
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"[evidence] archived {sum(d['ok'] for d in done)}/{len(todo)} new links")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("code", "independence", "proximity"):
        p = sub.add_parser(name)
        p.add_argument("--write", action="store_true", help="store results in FORGE_DB")
    sub.add_parser("check")
    w = sub.add_parser("wayback")
    w.add_argument("--submit", action="store_true")
    a = ap.parse_args(argv)
    return {"code": cmd_code, "independence": cmd_independence, "proximity": cmd_proximity,
            "check": cmd_check, "wayback": cmd_wayback}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
