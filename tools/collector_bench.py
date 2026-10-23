"""Collector regression bench (Batch 1, 23 Oct 2026). Measuring only.

The bench answers one question for every later collector batch: on the same
stories, does the changed code do better or worse than today's code?

Three parts:

* ``data/bench/collector_bench_cases.json``: the fixed reference cases from the
  Newcastle, Louis Trichardt / Thohoyandou, Polokwane and Musina manual sweeps.
* ``data/bench/collector_bench_replay.jsonl.gz``: what the Civic Intelligence
  collector and the Google News monitor (keyword, infrastructure-preset and
  PI-preset runs) actually saved in those sweeps, cut down to the rows that name
  the sweep's area or match a reference case. Built once from the sweep
  folders with ``extract``; the bench never fetches anything.
* this module: ``replay`` runs the collectors' own classifying and placing
  steps (``google_news_collector.build_signal`` and
  ``civic_intel_collector.entry_to_signal``) over those saved inputs, offline,
  with outbound sockets blocked; ``score`` produces the scorecard (caught, right
  stream, right place, merged, right date per case, plus noise per run).

Nothing here writes to a database, changes a collector, routing, collection
mode, pins or schedules.

Usage::

    python tools/collector_bench.py score                 # replay today's code
    python tools/collector_bench.py score --recorded      # what the sweeps saved
    python tools/collector_bench.py score --out docs/bench/x.json --md docs/bench/x.md
    python tools/collector_bench.py extract --sweep-root /home/sprite \
        --authority /path/to/forge-candidate.db           # rebuild the fixture

Limits: the replay only covers what the collectors fetched on 23 October. It
cannot score new sources or new searches (Batches 7 and 8); those need a live
run on database copies, scored with the same cases. The replay re-runs
classification and placing per saved row; it does not re-run the collectors'
own in-run de-duplication, so record counts are the saved counts unless a
later batch adds a merge step (pass ``merge=`` to ``score``).
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import re
import socket
import sqlite3
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CASES_PATH = REPO / "data" / "bench" / "collector_bench_cases.json"
REPLAY_PATH = REPO / "data" / "bench" / "collector_bench_replay.jsonl.gz"
BASELINE_PATH = REPO / "docs" / "bench" / "collector_bench_baseline_2026-10-23.json"

WINDOW_START = "2026-10-01"
WINDOW_END = "2026-10-23"

# Run names: civic = Civic Intelligence; kw = Google News town keyword
# searches; infra = Google News province infrastructure preset; pi = Google
# News province Priority Intelligence preset.
RUN_LABELS = {
    "civic": "Civic Intelligence",
    "kw": "News monitor, town keyword searches",
    "infra": "News monitor, infrastructure preset",
    "pi": "News monitor, PI preset",
}

# Sweep folders and area words, copied from each sweep's measure.py /
# manual_search.py so the noise counts mean what they meant in the notes.
TOWNS = {
    "newcastle": {
        "label": "Newcastle",
        "dir": "forge-newcastle-sweep",
        "files": {"civic": "civic", "kw": "gnews-kw", "infra": "gnews-infra", "pi": "gnews-pi"},
        "infra_province": "KwaZulu-Natal",
        "area": r"newcastle|madadeni|osizweni|amajuba|dannhauser|utrecht|charlestown|ingogo|blaauwbosch|kilbarchan|lennoxton|ncandu|emadlangeni|ballengeich|normandien|schuinshoogte|paterson street",
        "foreign": r"newcastle herald|nsw|hunter|knights|united|magpies|tyne|gateshead|staffordshire|under-lyme|chronicle live|northumb|australia|england|uk\b|premier league|lake macquarie|wallsend|charlestown fire|newcastle emlyn|newcastle west|county down|delaware|pennsylvania|oklahoma|wyoming|ontario|clarington|newcastle disease|sydney|nrl|bbc",
    },
    "lt_thohoyandou": {
        "label": "Louis Trichardt and Thohoyandou",
        "dir": "forge-three-town-sweep",
        "files": {"civic": "civic", "kw": "kw", "infra": "infra", "pi": "pi"},
        "infra_province": "Limpopo",
        "area": r"louis trichardt|makhado|tshikota|eltivillas|elti villas|madombidzha|songozwi|vleifontein|tshilwavhusiku|ravele|\bbeja\b|dzanani|waterval|elim\b|levubu|soutpansberg|zoutpansberg|tshirululuni|ha-?mashamba|mpheni|tshakhuma|thohoyandou|sibasa|thulamela|univen|university of venda|shayandima|maungani|phiphidi|tswinga|mashawana|dovheni|khakhanwa|makwarela|mbaleni|muledane|tshisaulu|itsani|lwamondo|tshilidzini|ngovhela|mvudi|tshivhase|mphephu|thavhani|nandoni|vuwani",
        "foreign": None,
    },
    "polokwane": {
        "label": "Polokwane",
        "dir": "forge-polokwane-sweep",
        "files": {"civic": "civic", "kw": "gnews-kw", "infra": "gnews-infra", "pi": "gnews-pi"},
        "infra_province": "Limpopo",
        "area": r"polokwane|pietersburg|seshego|mankweng|turfloop|sovenga|university of limpopo|westenburg|bendor|flora park|fauna park|ivy park|welgelegen|ster park|penina park|annadale|ladanna|serala view|mohlakaneng|legae la batho|moletjie|ga-maja|\bchuene|molepo|sebayeng|dikgale|mothapo|mothiba|makotopong|mamabolo|laastehoop|peter mokaba|mall of the north|mmotong|perskebult|juju valley|ga-magowa|capricorn district",
        "foreign": None,
    },
    "musina": {
        "label": "Musina",
        "dir": "forge-musina-sweep",
        "files": {"civic": "civic", "kw": "gnews-kw", "infra": "gnews-infra", "pi": "gnews-pi"},
        "infra_province": "Limpopo",
        "area": r"musina|messina|nancefield|campbell|harper|antonvilla|beitbridge|beit bridge|madimbo|malale|tshamutumbu|neltox|masisi|tshipise|mapungubwe|pontdrift|venetia|vhembe dongola|dongola|sigonde|domboni|limpopo river|n1 north|border post|musina-makhado|mmsez|\bsez\b|mutale|sagole|matatani|folovhodwe|tshikuwi|dite\b|bende mutale|gumbu|tshiungani",
        "foreign": r"sicily|sicilia|italy|italian|messina strait|ssc messina|stretto",
    },
}


# ── loading ─────────────────────────────────────────────────────────────────

def load_cases(path: Path = CASES_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_replay(path: Path = REPLAY_PATH) -> tuple[dict, list[dict]]:
    """Return (meta, rows) from the fixture. The first line is the meta record."""
    meta: dict = {}
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            obj = json.loads(line)
            if i == 0 and obj.get("_meta"):
                meta = obj
                continue
            rows.append(obj)
    return meta, rows


# ── offline guard ───────────────────────────────────────────────────────────

class NetworkBlocked(RuntimeError):
    pass


@contextlib.contextmanager
def no_network():
    """Block outbound sockets while the bench runs collector code."""
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_create = socket.create_connection

    def _blocked(*_a, **_k):
        raise NetworkBlocked("collector bench is offline: a collector tried to open a connection")

    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = orig_connect  # type: ignore[assignment]
        socket.socket.connect_ex = orig_connect_ex  # type: ignore[assignment]
        socket.create_connection = orig_create  # type: ignore[assignment]


# ── replay ──────────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    for fn in (datetime.fromisoformat,):
        try:
            d = fn(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _outcome(stream: str, timestamp: str, meta: dict) -> dict:
    lc = meta.get("location_candidate") or {}
    return {
        "stream": stream,
        "date": (timestamp or "")[:10] or None,
        "place": lc.get("place"),
        "province": lc.get("province"),
        "precinct": meta.get("precinct") or lc.get("precinct"),
        "pi_kind": meta.get("pi_kind"),
        "court_step": bool(meta.get("court_step") or meta.get("older_conduct") or meta.get("pi_kind") == "court"),
        "event_key": meta.get("event_key") or meta.get("merge_key"),
        "stream_reason": meta.get("stream_reason"),
    }


def recorded_outcome(row: dict) -> dict:
    """What the collector saved on 23 October."""
    return _outcome(row["stream"], row.get("timestamp") or "", row.get("metadata") or {})


def _replay_gnews(row: dict) -> dict:
    from forage.collectors import google_news_collector as gnews

    m = row.get("metadata") or {}
    title = row.get("title") or ""
    content = row.get("content") or ""
    desc = content.split("\n\n", 1)[1] if "\n\n" in content else ""
    query = m.get("query", "")
    run = row["run"]
    town = TOWNS[row["town"]]
    if run == "pi":
        is_crime = False  # every query in a PI-preset run is a PI query
    else:
        is_crime = gnews._is_crime_context(query)
    province = town["infra_province"] if run == "infra" else None
    item = {
        "title": title,
        "description": desc,
        "source_name": m.get("source_outlet", ""),
        "query": query,
        "link": m.get("link", ""),
        "pubdate": m.get("pubdate_raw", ""),
        "pubdate_parsed": row.get("timestamp") or "",
    }
    sig = gnews.build_signal(item, is_crime, province=province)
    return _outcome(sig["stream"], sig["timestamp"], json.loads(sig["metadata_json"] or "{}"))


_CIVIC_SOURCES: dict = {}


def _replay_civic(row: dict) -> dict:
    from forage.collectors import civic_intel_collector as civic

    if not _CIVIC_SOURCES:
        _CIVIC_SOURCES.update({s["source_key"]: s for s in civic.SOURCES})
    source = _CIVIC_SOURCES.get(row["source"])
    if source is None:
        return {**recorded_outcome(row), "replay_error": f"unknown civic source {row['source']}"}
    m = row.get("metadata") or {}
    d = _parse_ts(row.get("timestamp") or "")
    entry = {
        "title": row.get("title") or "",
        "summary": row.get("content") or "",
        "link": m.get("url") or "",
        "published_parsed": d.utctimetuple() if d else None,
    }
    sig = civic.entry_to_signal(entry, source)
    if sig is None:
        return {**recorded_outcome(row), "replay_error": "collector returned no signal"}
    return _outcome(sig["stream"], sig["timestamp"], sig.get("infra_meta") or {})


def replay_outcome(row: dict) -> dict:
    """Run today's collector code on one saved input. Offline only."""
    if row["run"] == "civic":
        return _replay_civic(row)
    return _replay_gnews(row)


def replay(rows: Iterable[dict]) -> list[dict]:
    """Attach ``outcome`` (today's code) and ``recorded`` (23 Oct) to each row."""
    out = []
    with no_network():
        for r in rows:
            rec = recorded_outcome(r)
            try:
                now = replay_outcome(r)
            except NetworkBlocked:
                raise
            except Exception as exc:  # a crash in one row must not hide the rest
                now = {**rec, "replay_error": f"{type(exc).__name__}: {exc}"}
            out.append({**r, "recorded": rec, "outcome": now})
    return out


# ── scoring ─────────────────────────────────────────────────────────────────

def _rx(p: Optional[str]):
    return re.compile(p, re.I) if p else None


def case_rows(case: dict, rows: list[dict]) -> list[dict]:
    m1, m2, mq = _rx(case["match"]), _rx(case.get("match_also")), _rx(case.get("query_match"))
    hits = []
    for r in rows:
        if r["town"] != case["sweep"]:
            continue
        if case.get("title_only"):
            text = r.get("title") or ""
        else:
            text = f"{r.get('title') or ''} {r.get('content') or ''}"
        if not m1.search(text):
            continue
        if m2 and not m2.search(text):
            continue
        if mq and not mq.search((r.get("metadata") or {}).get("query", "")):
            continue
        hits.append(r)
    return hits


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _near(a: Optional[str], b: Optional[str], days: int = 1) -> bool:
    d = _days(a, b)
    return d is not None and d <= days


def _share(flags: list[bool]) -> str:
    if not flags:
        return "n/a"
    if all(flags):
        return "all"
    return "some" if any(flags) else "none"


def score_case(case: dict, hits: list[dict], key: str) -> dict:
    outs = [h[key] for h in hits]
    records = len(outs)
    res = {
        "id": case["id"],
        "sweep": case["sweep"],
        "kind": case["kind"],
        "records": records,
        "caught": records > 0,
        "runs": dict(sorted(Counter(h["run"] for h in hits).items())),
        "streams": dict(sorted(Counter(o["stream"] for o in outs).items())),
        "places": dict(sorted(Counter(o["place"] or "none" for o in outs).items())),
        "dates": sorted({o["date"] for o in outs if o["date"]}),
        "titles": sorted({(h.get("title") or "")[:140] for h in hits})[:12],
        "replay_errors": sum(1 for o in outs if o.get("replay_error")),
    }
    area = re.compile(TOWNS[case["sweep"]]["area"], re.I)
    res["records_naming_area"] = sum(
        1 for h in hits if area.search(f"{h.get('title') or ''} {h.get('content') or ''}"))
    inc = case.get("incident_date")
    if case["kind"] == "catch":
        res["right_stream"] = _share([o["stream"] == case["expected_stream"] for o in outs])
        exp = {p.lower() for p in case.get("expected_place") or []}
        res["right_place"] = _share([(o["place"] or "").lower() in exp for o in outs])
        res["merged"] = (records == 1) if (case.get("one_record") and records) else (None if not records else True)
        if inc and records:
            res["right_date"] = _share([_near(o["date"], inc) for o in outs])
        else:
            res["right_date"] = "n/a" if records else "none"
        if case.get("court_step_expected"):
            res["court_step_flagged"] = _share([o["court_step"] for o in outs])
        res["precinct_scored"] = False  # collectors output no precinct yet
        res["pass"] = bool(
            records and res["right_stream"] == "all" and res["right_place"] in ("all", "some")
            and (res["merged"] in (True, None)) and (not case.get("court_step_expected")
                                                   or res.get("court_step_flagged") == "all"))
    else:
        rule = case["keep_out_rule"]
        forbidden = set(case.get("forbidden") or [])
        res["rule"] = rule
        status = "pass"
        if rule == "absent":
            ok = records == 0
        elif rule == "not_placed_in_province":
            ok = not any(o["province"] in forbidden for o in outs)
        elif rule == "not_placed_at":
            ok = not any(o["place"] in forbidden for o in outs)
        elif rule == "not_stream":
            ok = not any(o["stream"] in forbidden for o in outs)
        elif rule == "court_step_flagged":
            ok = all(o["court_step"] for o in outs)
        elif rule == "date_is_incident":
            if not records:
                ok, status = True, "not_tested"
            else:
                ok = all(_near(o["date"], inc) for o in outs)
        else:
            raise ValueError(f"unknown keep_out_rule {rule}")
        if case.get("one_record") and records:
            res["merged"] = records == 1
        res["pass"] = bool(ok)
        res["status"] = status if ok else "fail"
    return res


def noise(rows: list[dict], key: str, meta: dict) -> dict:
    out = {}
    for town, cfg in TOWNS.items():
        area = re.compile(cfg["area"], re.I)
        foreign = re.compile(cfg["foreign"], re.I) if cfg["foreign"] else None
        per_run = {}
        for run in RUN_LABELS:
            rs = [r for r in rows if r["town"] == town and r["run"] == run]
            named = [r for r in rs if area.search(f"{r.get('title') or ''} {r.get('content') or ''}")]
            inwin = [r for r in named if WINDOW_START <= (r.get("timestamp") or "")[:10] <= WINDOW_END]
            fgn = [r for r in named if foreign and (foreign.search(f"{r.get('title') or ''} {r.get('content') or ''}")
                                                     or foreign.search((r.get("metadata") or {}).get("source_outlet", "")))]
            per_run[run] = {
                "new_rows": (meta.get("run_totals") or {}).get(f"{town}/{run}"),
                "rows_naming_area": len(named),
                "naming_area_in_window": len(inwin),
                "naming_area_foreign_guess": len(fgn) if foreign else None,
                "naming_area_streams": dict(sorted(Counter(r[key]["stream"] for r in named).items())),
                "naming_area_placed": sum(1 for r in named if r[key]["place"]),
            }
        out[town] = per_run
    return out


def fidelity(rows: list[dict]) -> dict:
    """How closely the replay of today's code reproduces what was saved."""
    same_stream = sum(1 for r in rows if r["outcome"]["stream"] == r["recorded"]["stream"])
    same_place = sum(1 for r in rows if r["outcome"]["place"] == r["recorded"]["place"])
    diffs = [
        {"town": r["town"], "run": r["run"], "title": (r.get("title") or "")[:120],
         "recorded": [r["recorded"]["stream"], r["recorded"]["place"]],
         "replay": [r["outcome"]["stream"], r["outcome"]["place"]]}
        for r in rows
        if (r["outcome"]["stream"], r["outcome"]["place"]) != (r["recorded"]["stream"], r["recorded"]["place"])
    ]
    return {
        "rows": len(rows),
        "same_stream": same_stream,
        "same_place": same_place,
        "replay_errors": sum(1 for r in rows if r["outcome"].get("replay_error")),
        "differences": diffs[:40],
        "differences_total": len(diffs),
    }


def _git_head() -> Optional[str]:
    try:
        import subprocess
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def score(recorded: bool = False, cases: Optional[dict] = None,
          replay_path: Path = REPLAY_PATH,
          merge: Optional[Callable[[list[dict]], list[dict]]] = None) -> dict:
    """Build the scorecard. ``recorded=True`` scores what the sweeps saved;
    otherwise today's collector code is replayed over the same inputs.
    ``merge`` is a hook for a later de-duplication batch: it receives the
    replayed rows and returns the rows that survive."""
    cases = cases or load_cases()
    meta, rows = load_replay(replay_path)
    rows = replay(rows)
    key = "recorded" if recorded else "outcome"
    if merge and not recorded:
        rows = merge(rows)
    results = [score_case(c, case_rows(c, rows), key) for c in cases["cases"]]
    catch = [r for r in results if r["kind"] == "catch"]
    keep = [r for r in results if r["kind"] == "keep_out"]
    summary = {
        "catch_cases": len(catch),
        "caught": sum(r["caught"] for r in catch),
        "right_stream_all": sum(r["right_stream"] == "all" for r in catch),
        "right_place_any": sum(r["right_place"] in ("all", "some") for r in catch),
        "one_record_when_caught": sum(r["merged"] is True for r in catch if r["caught"]),
        "right_date_any": sum(r["right_date"] in ("all", "some") for r in catch),
        "catch_pass": sum(r["pass"] for r in catch),
        "keep_out_cases": len(keep),
        "keep_out_pass": sum(r["pass"] and r.get("status") == "pass" for r in keep),
        "keep_out_not_tested": sum(r.get("status") == "not_tested" for r in keep),
    }
    return {
        "bench": "FORGE collector regression bench (Batch 1)",
        "mode": "recorded" if recorded else "replay",
        "code_commit": _git_head(),
        "fixture": {k: meta.get(k) for k in ("built", "source_commit", "rows", "run_totals")},
        "env": {k: os.environ.get(k) for k in ("FORGE_PI_MODE",)},
        "summary": summary,
        "cases": results,
        "noise": noise(rows, key, meta),
        "fidelity": None if recorded else fidelity(rows),
    }


# ── markdown ────────────────────────────────────────────────────────────────

def to_markdown(card: dict) -> str:
    s = card["summary"]
    lines = [
        f"# Collector bench scorecard ({card['mode']}, code {card['code_commit']})",
        "",
        f"- Catch cases: {s['caught']} of {s['catch_cases']} caught; right stream on every record {s['right_stream_all']}; "
        f"a right place {s['right_place_any']}; one record {s['one_record_when_caught']}; a right date {s['right_date_any']}; "
        f"full pass {s['catch_pass']}.",
        f"- Keep-out cases: {s['keep_out_pass']} of {s['keep_out_cases']} pass ({s['keep_out_not_tested']} not testable on this data).",
        "",
        "| Case | Kind | Records | Streams | Places | Stream | Place | One record | Date | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in card["cases"]:
        streams = ", ".join(f"{k} {v}" for k, v in r["streams"].items()) or "-"
        places = ", ".join(f"{k} {v}" for k, v in r["places"].items()) or "-"
        if r["kind"] == "catch":
            row = [r["id"], "catch", r["records"], streams, places, r["right_stream"], r["right_place"],
                   {True: "yes", False: "no", None: "-"}[r["merged"]], r["right_date"], "yes" if r["pass"] else "no"]
        else:
            row = [r["id"], f"keep out ({r['rule']})", r["records"], streams, places, "-", "-",
                   {True: "yes", False: "no", None: "-"}.get(r.get("merged"), "-"), "-", r["status"]]
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    f = card.get("fidelity")
    if f:
        lines += ["", f"Replay fidelity: {f['same_stream']} of {f['rows']} rows got the same stream as saved, "
                      f"{f['same_place']} the same place; {f['replay_errors']} replay errors."]
    return "\n".join(lines) + "\n"


# ── fixture extraction (one-off, from the sweep folders) ────────────────────

def extract(sweep_root: Path, authority_db: Path, out_path: Path = REPLAY_PATH,
            cases: Optional[dict] = None) -> dict:
    """Cut the saved sweep databases down to the bench fixture.

    Keeps, per town and run, every row the run added (rows not already in the
    authority database) whose text names the town's area or matches one of
    that sweep's reference cases. Reads the databases read-only.
    """
    cases = cases or load_cases()
    by_town: dict[str, list[dict]] = {}
    for c in cases["cases"]:
        by_town.setdefault(c["sweep"], []).append(c)
    kept: list[dict] = []
    totals: dict[str, int] = {}
    seen: set = set()
    for town, cfg in TOWNS.items():
        area = re.compile(cfg["area"], re.I)
        tcases = by_town.get(town, [])
        for run, fname in cfg["files"].items():
            db = Path(sweep_root) / cfg["dir"] / "db" / f"{fname}.db"
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.execute("ATTACH ? AS a", (f"file:{authority_db}?mode=ro",))
            q = ("SELECT signal_id, source, external_id, title, content, timestamp, stream, metadata_json "
                 "FROM signals WHERE signal_id NOT IN (SELECT signal_id FROM a.signals)")
            n = 0
            for sid, src, ext, title, content, ts, stream, mj in con.execute(q):
                n += 1
                meta = json.loads(mj or "{}")
                text = f"{title or ''} {content or ''}"
                row = {"town": town, "run": run, "source": src, "external_id": ext, "title": title,
                       "content": content, "timestamp": ts, "stream": stream, "metadata": meta}
                keep = bool(area.search(text)) or any(case_rows(c, [row]) for c in tcases)
                if keep and (town, run, sid) not in seen:
                    seen.add((town, run, sid))
                    kept.append(row)
            totals[f"{town}/{run}"] = n
            con.close()
    meta = {"_meta": True, "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_commit": "collector code at 524a8b4 for all four sweeps (23 Oct comparison check)",
            "rows": len(kept), "run_totals": totals,
            "note": "Rows each sweep run added on 23 Oct 2026 that name the sweep area or match a reference case."}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kept.sort(key=lambda r: (r["town"], r["run"], r["source"], r["external_id"] or ""))
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(out_path, "wb"), mtime=0) as gz:
        for obj in [meta] + kept:
            gz.write((json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return meta


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--recorded", action="store_true", help="score what the sweeps saved, not a replay")
    sc.add_argument("--out", help="write the scorecard JSON here")
    sc.add_argument("--md", help="write a markdown table here")
    ex = sub.add_parser("extract")
    ex.add_argument("--sweep-root", required=True)
    ex.add_argument("--authority", required=True)
    ex.add_argument("--out", default=str(REPLAY_PATH))
    a = ap.parse_args(argv)
    if a.cmd == "extract":
        meta = extract(Path(a.sweep_root), Path(a.authority), Path(a.out))
        print(json.dumps(meta, indent=1))
        return 0
    t0 = time.time()
    card = score(recorded=a.recorded)
    if a.out:
        Path(a.out).write_text(json.dumps(card, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.md:
        Path(a.md).write_text(to_markdown(card), encoding="utf-8")
    print(to_markdown(card))
    print(f"({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
