"""A town for every published record (interactive charts Batch 8).

Records have no town field. The heat map's town is worked out here, in
Python, from FORGE's coverage-area naming rule (tools/coverage_areas.py)
run on the pin alone: the nearest place of 10,000 people or more within the
coverage radius, else the police precinct holding the pin, with suburbs of a
big city named as that city outside Limpopo, Mpumalanga and KwaZulu-Natal.

That rule names areas for search queries, so a few of its names read oddly
as a town (Westenburg for Polokwane airport). Those are fixed by hand in
docs/signals/record-towns.json, one entry per record, each with the name the
rule gave and why it was changed. An override whose rule name no longer
matches is reported, so a gazetteer change cannot slip past unseen.

publish.py stops if any record ends up without a town.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools import coverage_areas as ca

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "docs" / "signals" / "record-towns.json"


def load_overrides(path: Path = OVERRIDES_PATH) -> dict:
    """signal_id -> {"town", "rule_name", "why"}; {} when the file is absent."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for sid, o in (data.get("overrides") or {}).items():
        town = str(o.get("town") or "").strip()
        if not town or not str(o.get("why") or "").strip():
            raise ValueError(f"record-towns.json: override for {sid} needs a town and a why")
        out[sid] = {"town": town, "rule_name": str(o.get("rule_name") or "").strip(),
                    "why": str(o.get("why")).strip()}
    return out


def rule_town(rec: dict, places: list[dict], precincts: list[dict], r_km: float) -> str:
    """The coverage-area rule's name for one record's pin, without the
    province."""
    try:
        lat, lng = float(rec["latitude"]), float(rec["longitude"])
    except (KeyError, TypeError, ValueError):
        return ""
    pin = {"signal_id": rec.get("signal_id"), "title": rec.get("title") or "",
           "lat": lat, "lng": lng, "province": rec.get("province") or "",
           "place_names": ca.pin_place_names(rec.get("title") or ""),
           "stream": rec.get("stream") or ""}
    if pin["province"] in ca.PRECINCT_SPLIT_PROVINCES:
        pin["precinct"] = ca.holding_precinct(pin, precincts)
    name = ca.describe_area([pin], r_km, places, precincts).get("name") or ""
    if pin["province"] and name.endswith(f" ({pin['province']})"):
        name = name[: -len(pin["province"]) - 3]
    return name.strip()


def record_towns(records: list[dict], overrides: dict | None = None,
                 places=None, precincts=None) -> dict:
    """signal_id -> {"town", "rule_name", "source": "rule" | "override",
    "why"}. Problems that stop a publish (a record with no town) are under
    "_problems"; overrides for records not published, or whose rule name
    moved, are under "_warnings"."""
    if overrides is None:
        overrides = load_overrides()
    if places is None or precincts is None:
        places, precincts = ca.load_gazetteer()
    r_km = ca.radius_km()
    out: dict = {}
    problems: list[str] = []   # publish stops on these
    warnings: list[str] = []   # publish prints these
    for rec in records:
        sid = rec.get("signal_id")
        rule = rule_town(rec, places, precincts, r_km)
        o = overrides.get(sid)
        if o:
            if o["rule_name"] and o["rule_name"] != rule:
                warnings.append(f"override for {str(sid)[:8]} expected the rule to say "
                                f"{o['rule_name']!r}, it now says {rule!r}")
            out[sid] = {"town": o["town"], "rule_name": rule, "source": "override", "why": o["why"]}
        else:
            out[sid] = {"town": rule, "rule_name": rule, "source": "rule", "why": ""}
        if not out[sid]["town"]:
            problems.append(f"no town for {str(sid)[:8]} ({rec.get('title', '')[:60]})")
    published = {r.get("signal_id") for r in records}
    for sid in overrides:
        if sid not in published:
            warnings.append(f"override for {sid[:8]} matches no published record")
    out["_problems"] = problems
    out["_warnings"] = warnings
    return out


def with_towns(records: list[dict], towns: dict) -> list[dict]:
    """Copies of the records with a "town" field for the charts only; the
    open data (records.json, records.csv) stays as it is."""
    return [dict(r, town=(towns.get(r.get("signal_id")) or {}).get("town", "")) for r in records]


def towns_for_charts(records: list[dict], **kw) -> tuple[list[dict], list[str], list[str]]:
    """(records with a town for the charts, problems, warnings): what
    publish.py and the browser checks use. publish.py stops on any problem
    (a record with no town) and prints the warnings."""
    t = record_towns(records, **kw)
    problems, warnings = t.pop("_problems"), t.pop("_warnings")
    return with_towns(records, t), problems, warnings


def check_towns(records: list[dict], problems: list[str]) -> None:
    """The publish check: raises SystemExit if any record has no town."""
    missing = [r.get("signal_id") for r in records if not str(r.get("town") or "").strip()]
    if problems or missing:
        lines = problems or [f"no town for {str(s)[:8]}" for s in missing]
        raise SystemExit("[publish] heat map: " + str(len(lines)) + " record(s) have no town: "
                         + "; ".join(lines[:10]))


if __name__ == "__main__":  # list every town and override for review
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "records.json"
    recs = json.loads(src.read_text(encoding="utf-8"))["records"]
    t = record_towns(recs)
    for p in t.pop("_problems"):
        print("PROBLEM", p)
    for p in t.pop("_warnings"):
        print("WARNING", p)
    for sid, v in sorted(t.items(), key=lambda kv: (kv[1]["town"], kv[0])):
        print(f"{v['town']:<22} {sid[:8]}  {v['source']:<8} rule={v['rule_name']}")
