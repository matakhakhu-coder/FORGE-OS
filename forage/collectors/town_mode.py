"""
Town mode: the town sweep's collector step in one command (Batch 8, G10).

    python -m forage.collectors.town_mode --town Musina --db /path/copy.db --from 2026-10-01

Until now each town sweep typed its Google News searches by hand and ran
Civic Intelligence separately. Town mode builds the searches from the town:

  * the town name times the crime, Priority Intelligence and infrastructure
    words the four 23 Oct sweeps typed (data/town_mode.json),
  * the town's places from the gazetteer (sections and villages the
    collector top-up lists for it, and nearby sections) plus the per-town
    extras the gazetteer can't supply (a border post, the town's old name),
  * every search with Google News' recent-only limit (when:Nd).

It then

  * runs Google News with the Batch 4 area check pinned to the town, so a
    search with no recognisable town ("Beitbridge border") is checked
    against the town instead of keeping everything,
  * runs Civic Intelligence with the town's local papers (Batch 7), leaves
    out every feed on a switched-off publisher's hosts (Caxton: The Citizen,
    Review Online, Letaba Herald), and keeps only what is about the town
    (the town's own local-paper leads are always kept),
  * writes a run report (JSON and markdown) and, for a bench town, scores
    the run against the town's bench cases with the Batch 1 scorer.

Nothing here changes a plain or scheduled collector run, publishes a pin or
touches an approval gate: town mode writes only to the database it is given.
Article text stays off unless the run asks for it (--read-articles), as
before. --plan prints the searches and sources and fetches nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO / "data" / "town_mode.json"
TOPUP_FILE = REPO / "data" / "gazetteer" / "collector_places_topup.json"


def load_config(path: Path = CONFIG_FILE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _word_rx(words: list[str]):
    words = [w for w in words if w]
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b", re.I)


# ── the plan: which searches and sources a town gets ─────────────────────────────────

def gazetteer_places(town: dict, limit: int = 6) -> list[dict]:
    """The town's places to search by name, from the gazetteer only:
    collector top-up sections, villages and police areas whose parent is the
    town or that sit in its municipality (nearest first), then GeoNames
    sections (PPLX) within 15 km. Each entry says where it came from."""
    from forage.collectors import infra_signals as _infra

    out, seen = [], {_norm(town["name"])}
    muni = _norm(town.get("municipality") or "")
    try:
        topup = json.loads(TOPUP_FILE.read_text(encoding="utf-8")).get("places", [])
    except FileNotFoundError:
        topup = []
    cands = []
    for p in topup:
        if p.get("context_required"):
            continue  # a look-alike name that needs context words; too loose to search alone
        if _norm(p.get("parent", "")) == _norm(town["name"]) or (muni and _norm(p.get("municipality", "")) == muni):
            if p.get("lat") is None:
                continue
            d = _infra._km(town, p)
            if d <= 120:
                cands.append((d, p["name"], "collector gazetteer top-up"))
    for rows in _infra._places().values():
        for p in rows:
            if p.get("lat") is None or str(p.get("feature_code")) != "PPLX":
                continue
            d = _infra._km(town, p)
            if d <= 15:
                cands.append((d, p["name"], "GeoNames section"))
    for d, name, src in sorted(cands):
        if _norm(name) in seen:
            continue
        seen.add(_norm(name))
        out.append({"name": name, "km": round(d, 1), "source": src})
        if len(out) >= limit:
            break
    return out


def build_plan(town_name: str, days: int = 23, config: Optional[dict] = None) -> dict:
    """Everything a town run will do, before it fetches anything."""
    from forage.collectors import infra_signals as _infra
    from forage.collectors import google_news_collector as _gn
    from forage.collectors import town_sources as _towns

    cfg = config or load_config()
    entry_name, entry = None, {}
    for name, e in (cfg.get("towns") or {}).items():
        if _norm(name) == _norm(town_name):
            entry_name, entry = name, e
    place = _infra.query_town(town_name)
    if not place:
        raise ValueError(f"town mode: {town_name!r} is not a town in the gazetteer")
    name = entry_name or place["name"]
    province = entry.get("province") or place.get("province")

    words = cfg["words"]
    searches: list[dict] = []

    def add(q: str, why: str):
        q = _gn.recent(q, days)
        if q and q.lower() not in {s["query"].lower() for s in searches}:
            searches.append({"query": q, "why": why})

    for group in ("crime", "priority", "infrastructure", "other"):
        for w in words.get(group, []):
            add(f"{name} {w}", group)
    places = gazetteer_places(place, limit=int(cfg.get("place_word_limit", 6)))
    for p in places:
        add(p["name"], f"place ({p['source']}, {p['km']} km)")
    for q in entry.get("extra_searches", []):
        add(q, "town extra (data/town_mode.json)")

    ts = None
    try:
        ts = _towns.sources_for_town(name, province=province)
    except Exception:
        ts = None
    blocked_hosts = _towns.blocked_hosts()
    return {
        "town": name,
        "province": province,
        "gazetteer_point": {k: place.get(k) for k in ("name", "lat", "lng", "municipality", "source")},
        "days": days,
        "searches": searches,
        "places_searched": places,
        "area_words": entry.get("area_words", [name]),
        "foreign_words": entry.get("foreign_words", []),
        "area_km": int(cfg.get("area_km", 60)),
        "local_papers": [p["name"] for p in (ts.readable if ts else [])],
        "local_papers_not_read": [{"paper": k, "why": why} for k, why in (ts.blocked if ts else [])],
        "excluded_hosts": blocked_hosts,
        "_place": place,
    }


# ── "about the town" ──────────────────────────────────────────────────────────────────────

def about_town(text: str, plan: dict) -> bool:
    """The text names the town, one of its area words, or a gazetteer place
    within area_km of it, and is not one of the town's foreign look-alikes
    (Messina in Sicily)."""
    from forage.collectors import infra_signals as _infra

    text = text or ""
    fgn = _word_rx(plan.get("foreign_words") or [])
    if fgn and fgn.search(text):
        return False
    area = _word_rx(plan.get("area_words") or [])
    if area and area.search(text):
        return True
    return _infra.names_town(text, plan["_place"], km=plan.get("area_km", 60))


def civic_keep(plan: dict):
    """Civic Intelligence filter for a town run: anything about the town is
    kept. The town's own local-paper leads are also kept when their headline
    names no place at all (Neltox: "Residents back army crackdown but demand
    border fence"); a local-paper lead that names another place (a
    Louis Trichardt story in the Zoutpansberger) is dropped from a Musina
    run."""
    from forage.collectors import infra_signals as _infra

    def keep(sig: dict, source: dict) -> bool:
        text = f"{sig.get('title') or ''} {sig.get('content') or ''}"
        if about_town(text, plan):
            return True
        if source.get("fetch_mode") == "sitemap":
            return _infra.locate(text, collector_topup=True) is None
        return False
    return keep


# ── the run ─────────────────────────────────────────────────────────────────────────────────

def _ids(db: Path) -> set:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT signal_id FROM signals")}
    finally:
        con.close()


def _bench():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import collector_bench  # noqa: E402
    return collector_bench


def run(town: str, db: Path, start: Optional[str] = None, days: int = 23,
        skip_civic: bool = False, skip_gnews: bool = False,
        report_dir: Optional[Path] = None, score: bool = True) -> dict:
    db = Path(db)
    if not db.exists():
        raise FileNotFoundError(f"town mode: no database at {db}")
    from forage.collectors import run_window as _rw
    window = _rw.RunWindow.build(days=days, start=start)
    if window.start:
        days = window.query_days(default=days)
    plan = build_plan(town, days=days)
    before = _ids(db)
    t0 = time.time()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict = {"town": plan["town"], "province": plan["province"], "db": str(db), "started": started,
                 "window": window.describe(), "plan": {k: v for k, v in plan.items() if not k.startswith("_")}}

    if not skip_gnews:
        from forage.collectors import google_news_collector as _gn
        _gn.DB_PATH = db
        out["google_news"] = _gn.run_collector(
            days=days, max_queries=len(plan["searches"]),
            keywords=[s["query"] for s in plan["searches"]],
            start_date=start, area_town=plan["_place"])
    if not skip_civic:
        from forage.collectors import civic_intel_collector as _civic
        prev = _civic.WINDOW_START
        if start:
            _civic.WINDOW_START = start
        try:
            out["civic"] = _civic.run(db, town=plan["town"], province=plan["province"],
                                      exclude_hosts=plan["excluded_hosts"], keep_signal=civic_keep(plan))
        finally:
            _civic.WINDOW_START = prev
        out["civic"].pop("per_source", None)

    bench = _bench()
    sweep = bench.sweep_for_town(plan["town"])
    rows = bench.live_rows(db, sweep or "_", since_ids=before)
    for r in rows:
        r["about_town"] = about_town(f"{r.get('title') or ''} {r.get('content') or ''}", plan)
    out["saved"] = summarise_rows(rows)
    out["duration_s"] = round(time.time() - t0, 1)
    if score and sweep:
        live = bench.score_live(rows, sweep)
        ref_kw = bench.score_reference(sweep, runs={"kw"}, recorded=True)
        ref_all = bench.score_reference(sweep, runs=None, recorded=True)
        ref_replay = bench.score_reference(sweep, runs=None, recorded=False)
        out["bench"] = {
            "sweep": sweep,
            "town_mode": live,
            "reference_23oct_keyword_run": _ref_brief(ref_kw),
            "reference_23oct_all_runs": _ref_brief(ref_all),
            "reference_today_code_replayed": _ref_brief(ref_replay),
            "vs_keyword_run": bench.compare(live, ref_kw),
            "vs_all_runs": bench.compare(live, ref_all),
            "vs_replay": bench.compare(live, ref_replay),
        }
    elif score:
        out["bench"] = None  # not a bench town: compare by hand (the plan's second-town check)
    out["rows"] = [{"collector": r["run"], "source": r["source"], "title": (r["title"] or "")[:200],
                    "stream": r["stream"], "place": r["outcome"]["place"], "date": r["outcome"]["date"],
                    "link": r["outcome"]["link"], "about_town": r["about_town"],
                    "story_role": r["outcome"].get("story_role")} for r in rows]
    if report_dir is not None:
        write_report(out, Path(report_dir))
    return out


def _ref_brief(ref: dict) -> dict:
    return {"runs": ref["runs"], "mode": ref["mode"], "summary": ref["summary"], "saved_rows": ref["saved_rows"],
            "rows_naming_area": ref["rows_naming_area"],
            "cases": [{k: c.get(k) for k in ("id", "kind", "caught", "records", "streams", "places",
                                             "right_stream", "right_place", "pass", "status")}
                      for c in ref["cases"]]}


def summarise_rows(rows: list[dict]) -> dict:
    from collections import Counter
    out = {}
    for label in sorted({r["run"] for r in rows}) + ["all"]:
        rs = [r for r in rows if label == "all" or r["run"] == label]
        about = [r for r in rs if r["about_town"]]
        out[label] = {
            "saved": len(rs),
            "about_town": len(about),
            "not_about_town": len(rs) - len(about),
            "about_town_placed": sum(1 for r in about if r["outcome"]["place"]),
            "about_town_streams": dict(sorted(Counter(r["stream"] for r in about).items())),
            "supporting_records": sum(1 for r in rs if r["outcome"].get("story_role") == "supporting_evidence"),
        }
    return out


# ── report ─────────────────────────────────────────────────────────────────────────────────────

def to_markdown(out: dict) -> str:
    p = out["plan"]
    s = out.get("saved", {}).get("all", {})
    lines = [
        f"# Town mode run: {out['town']} ({out['started']})",
        "",
        f"- Window: {out['window']}. Database: `{out['db']}`. Took {out.get('duration_s')} s.",
        f"- Searches: {len(p['searches'])}, all with when:{p['days']}d. Places searched from the gazetteer: "
        + (", ".join(f"{x['name']} ({x['source']})" for x in p["places_searched"]) or "none") + ".",
        f"- Local papers read: {', '.join(p['local_papers']) or 'none'}. Not read: "
        + ("; ".join(f"{x['paper']} ({x['why']})" for x in p["local_papers_not_read"]) or "none") + ".",
        f"- Civic Intelligence feeds left out (switched-off publishers): "
        + (", ".join(f"{x['source_key']}" for x in (out.get("civic") or {}).get("excluded_sources", [])) or "none")
        + f". Civic items dropped as not about {out['town']}: {(out.get('civic') or {}).get('off_area', 0)}.",
        f"- Saved: {s.get('saved', 0)} rows, {s.get('about_town', 0)} about {out['town']} "
        f"({s.get('about_town_placed', 0)} of those placed), {s.get('not_about_town', 0)} not.",
    ]
    for label, v in (out.get("saved") or {}).items():
        if label != "all":
            lines.append(f"  - {label}: {v['saved']} saved, {v['about_town']} about the town, streams {v['about_town_streams']}")
    b = out.get("bench")
    if b:
        live = b["town_mode"]
        ls = live["summary"]
        lines += ["", f"## Bench ({b['sweep']}, scored on what this run saved)", "",
                  f"Town mode: {ls['caught']} of {ls['catch_cases']} caught, full pass {ls['catch_pass']}, "
                  f"keep-out {ls['keep_out_pass']} of {ls['keep_out_cases']}."]
        for key, label in (("reference_23oct_keyword_run", "23 Oct keyword run (saved)"),
                           ("reference_23oct_all_runs", "23 Oct all four runs (saved)"),
                           ("reference_today_code_replayed", "today's code replayed over 23 Oct inputs")):
            rs = b[key]["summary"]
            lines.append(f"{label}: {rs['caught']} caught, full pass {rs['catch_pass']}, keep-out "
                         f"{rs['keep_out_pass']} of {rs['keep_out_cases']}; {b[key]['saved_rows']} rows saved, "
                         f"{b[key]['rows_naming_area']} naming the area.")
        lines += ["", "| Case | Records | Streams | Places | Pass | vs keyword run | vs all runs | vs replay |",
                  "|---|---|---|---|---|---|---|---|"]
        v1 = {c["id"]: c["verdict"] for c in b["vs_keyword_run"]}
        v2 = {c["id"]: c["verdict"] for c in b["vs_all_runs"]}
        v3 = {c["id"]: c["verdict"] for c in b["vs_replay"]}
        for c in live["cases"]:
            ok = ("yes" if c["pass"] else "no") if c["kind"] == "catch" else c["status"]
            lines.append(f"| {c['id']} | {c['records']} | {c['streams'] or '-'} | {c['places'] or '-'} | {ok} | "
                         f"{v1.get(c['id'])} | {v2.get(c['id'])} | {v3.get(c['id'])} |")
        n = live["noise"].get("all", {})
        lines += ["", f"Noise (bench area words): {n.get('saved')} saved, {n.get('naming_area')} naming the area, "
                      f"{n.get('not_naming_area')} not."]
    elif "bench" in out:
        lines += ["", "Not a bench town: compare this run with a manual search before calling town mode ready here."]
    about = [r for r in out.get("rows", []) if r["about_town"]]
    if about:
        lines += ["", f"## Rows about {out['town']}", "", "| Collector | Stream | Place | Date | Title |", "|---|---|---|---|---|"]
        for r in sorted(about, key=lambda r: (r["stream"] or "", r["date"] or "")):
            lines.append(f"| {r['collector']} | {r['stream']} | {r['place'] or '-'} | {r['date'] or '-'} | "
                         f"{(r['title'] or '').replace('|', '/')[:120]} |")
    return "\n".join(lines) + "\n"


def write_report(out: dict, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"town_mode_{_norm(out['town']).replace(' ', '_')}_{out['started'].replace(':', '').replace('-', '')}"
    jp, mp = report_dir / f"{stem}.json", report_dir / f"{stem}.md"
    jp.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    mp.write_text(to_markdown(out), encoding="utf-8")
    print(f"[town_mode] report: {mp}")
    return jp, mp


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FORGE town mode (Batch 8): one town's collector sweep")
    ap.add_argument("--town", required=True)
    ap.add_argument("--db", type=Path, help="database to write to (use a copy for a test run)")
    ap.add_argument("--from", dest="start", default=None, help="fixed window start, SAST ('1 October' or YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=23, help="window in days when --from is not given (default 23)")
    ap.add_argument("--plan", action="store_true", help="print the searches and sources; fetch nothing")
    ap.add_argument("--skip-civic", action="store_true")
    ap.add_argument("--skip-gnews", action="store_true")
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--read-articles", action="store_true", help="Batch 6 article reader (off by default)")
    ap.add_argument("--report-dir", type=Path, default=None,
                    help="where the run report goes (default: next to the database)")
    a = ap.parse_args(argv)
    if a.plan:
        plan = build_plan(a.town, days=a.days)
        print(json.dumps({k: v for k, v in plan.items() if not k.startswith("_")}, indent=1, ensure_ascii=False))
        return 0
    if not a.db:
        ap.error("--db is required unless --plan")
    if a.read_articles:
        os.environ["FORGE_ARTICLE_READER"] = "1"
        from forage.collectors import article_step as _art
        _art.READ_ARTICLES = True
    out = run(a.town, a.db, start=a.start, days=a.days, skip_civic=a.skip_civic, skip_gnews=a.skip_gnews,
              report_dir=a.report_dir or a.db.parent, score=not a.no_score)
    print(to_markdown(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
