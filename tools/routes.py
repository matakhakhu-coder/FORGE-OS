"""Reported routes: a South African case joined to a named place abroad.

Owner decisions, Matamela Ramovha, 12 November 2026 (docs/routes/POLICY.md):
  1. Map lines may also be reported routes, beside the story threads.
  2. A seizure with no arrest or charge may be a record, of a new kind, shown
     only in the "Reported routes" (Africa and the world) view, never on the
     everyday South Africa map.
  3. A place abroad is a specific city, port, airport or border post a source
     names, with its own label. Never a country on its own.

One file per case: docs/routes/<case_id>.json (schema forge.route_case, v1).
A line is drawn only when a source names both places for that same case.
The page groups cases that share both ends into one line.

    python tools/routes.py list      # each case, and whether it is public
    python tools/routes.py check     # validate every case; exit 1 on a problem
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_DIR = ROOT / "docs" / "routes"

SCHEMA = "forge.route_case"
VERSIONS = {1}
KINDS = {"seizure"}
STATUSES = {"approved", "held", "retired"}
PLACE_KINDS = {"city", "port", "airport", "border post"}
ROLES = {"flight origin": "Flight left from", "shipment origin": "Shipped from",
         "destination": "Bound for", "transit": "Passed through"}
PRECISIONS = {"site", "city"}
MAX_PUBLIC_CASES = 24            # pilot cap

# Lat/lng box around South Africa, the same one the area check uses.
ZA_BOX = (-35.2, -22.0, 16.3, 33.0)

CAPTION = ("A line joins two places a source names for the same case. It shows what "
           "was reported, not a checked path, and says nothing about the place or its people.")
RECORDS_CAVEAT = ("Reported routes (owner policy 12 Nov 2026, docs/routes/POLICY.md): each line "
                  "joins a South African case to a place abroad that a source names for that "
                  "same case. A line is what was reported, not a checked path. Seizure records "
                  "here may have no arrest or charge, and appear only in the routes view, not "
                  "on the everyday map or in records.")

# A place abroad is never a country on its own. Labels read "Place, Country";
# the place part may not itself be a country name.
COUNTRIES = {
    "brazil", "argentina", "colombia", "peru", "bolivia", "venezuela", "ecuador", "chile",
    "mexico", "usa", "united states", "canada", "united kingdom", "uk", "ireland",
    "france", "germany", "spain", "portugal", "italy", "netherlands", "belgium",
    "switzerland", "turkey", "russia", "ukraine", "india", "pakistan", "bangladesh",
    "china", "hong kong", "japan", "thailand", "malaysia", "indonesia", "vietnam",
    "australia", "uae", "united arab emirates", "qatar", "saudi arabia", "iran",
    "afghanistan", "egypt", "ethiopia", "kenya", "tanzania", "uganda", "rwanda",
    "nigeria", "ghana", "ivory coast", "cote d'ivoire", "senegal", "angola", "namibia",
    "botswana", "zimbabwe", "zambia", "malawi", "mozambique", "eswatini", "swaziland",
    "lesotho", "madagascar", "mauritius", "drc", "dr congo", "democratic republic of the congo",
    "congo", "cameroon", "somalia", "sudan", "south sudan", "libya", "morocco", "algeria",
    "tunisia", "south africa", "europe", "africa", "asia", "south america", "latin america",
    "middle east", "west africa", "east africa",
}

# Words a route may never use about a place or its people (bias guard).
BANNED = re.compile(
    r"\b(threat|threatening|hotspot|hot spot|dangerous|notorious|infested|drug capital|"
    r"narco[- ]?state|cartel country|source country|smuggling hub|crime hub|high[- ]risk country)\b",
    re.I)

CLAIM_RE = re.compile(r"^(police|customs|court|source|(party|agency): .{2,60})$")
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{4,80}$")


class RouteError(ValueError):
    """A route case file breaks the rule."""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _in_za(lat, lng) -> bool:
    return ZA_BOX[0] <= lat <= ZA_BOX[1] and ZA_BOX[2] <= lng <= ZA_BOX[3]


def _basis_ok(b, where: str, problems: list[str], place: str | None = None) -> None:
    if not isinstance(b, dict):
        problems.append(f"{where}: basis must be an object with quote and url")
        return
    q, u = str(b.get("quote") or "").strip(), str(b.get("url") or "").strip()
    if len(q) < 12:
        problems.append(f"{where}: a quote from the source is required")
    if not u.startswith("https://"):
        problems.append(f"{where}: an https link to the source is required")
    if not str(b.get("outlet") or "").strip():
        problems.append(f"{where}: the outlet or publisher is required")
    if place and q and _norm(place) not in _norm(q):
        problems.append(f"{where}: the quote must name {place!r} itself")


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def validate_case(c: dict, filename: str | None = None) -> list[str]:
    """Every problem with one case file; an empty list means it follows the rule."""
    p: list[str] = []
    cid = str(c.get("case_id") or "")
    where = cid or filename or "case"
    if c.get("schema") != SCHEMA or c.get("version") not in VERSIONS:
        p.append(f"{where}: schema must be {SCHEMA} version 1")
    if not CASE_ID_RE.match(cid):
        p.append(f"{where}: case_id must be a lowercase slug")
    if filename and cid and filename != f"{cid}.json":
        p.append(f"{where}: file name must be {cid}.json")
    if c.get("kind") not in KINDS:
        p.append(f"{where}: kind must be one of {sorted(KINDS)}")
    status = c.get("status")
    if status not in STATUSES:
        p.append(f"{where}: status must be one of {sorted(STATUSES)}")
    if status == "approved" and not (c.get("approved_by") and c.get("approved_at")):
        p.append(f"{where}: an approved case needs approved_by and approved_at")
    try:
        date.fromisoformat(str(c.get("incident_date") or ""))
    except ValueError:
        p.append(f"{where}: incident_date must be YYYY-MM-DD")
    for key in ("title", "summary"):
        if len(str(c.get(key) or "").strip()) < 12:
            p.append(f"{where}: {key} is required")
    if not str(c.get("arrests") or "").strip():
        p.append(f"{where}: say what is known about arrests or charges, even 'none reported'")

    sa = c.get("sa_end") or {}
    lat, lng = _num(sa.get("lat")), _num(sa.get("lng"))
    if not str(sa.get("label") or "").strip() or not str(sa.get("province") or "").strip():
        p.append(f"{where}: sa_end needs a label and a province")
    if lat is None or lng is None or not _in_za(lat, lng):
        p.append(f"{where}: sa_end must be a point inside South Africa")
    if sa.get("precision") not in PRECISIONS:
        p.append(f"{where}: sa_end precision must be one of {sorted(PRECISIONS)}")
    if not sa.get("basis"):
        p.append(f"{where}: sa_end needs at least one source")
    for i, b in enumerate(sa.get("basis") or []):
        _basis_ok(b, f"{where}: sa_end source {i + 1}", p)

    fars = c.get("far_ends") or []
    if not fars:
        p.append(f"{where}: a route needs at least one place abroad a source names")
    for i, f in enumerate(fars):
        fw = f"{where}: far end {i + 1}"
        label = str(f.get("label") or "")
        place = str(f.get("place") or "")
        country = str(f.get("country") or "")
        if f.get("place_kind") not in PLACE_KINDS:
            p.append(f"{fw}: place_kind must be one of {sorted(PLACE_KINDS)} (never a country)")
        if not place or not country or label != f"{place}, {country}":
            p.append(f"{fw}: label must read 'Place, Country' from place and country")
        if _norm(place) in COUNTRIES or not _norm(place):
            p.append(f"{fw}: {place!r} is a country or region, not a place: a route end must be a city, port, airport or border post")
        if f.get("role") not in ROLES:
            p.append(f"{fw}: role must be one of {sorted(ROLES)}")
        if f.get("precision") not in PRECISIONS:
            p.append(f"{fw}: precision must be one of {sorted(PRECISIONS)}")
        flat, flng = _num(f.get("lat")), _num(f.get("lng"))
        if flat is None or flng is None or not (-90 <= flat <= 90 and -180 <= flng <= 180):
            p.append(f"{fw}: needs a lat and lng")
        elif _in_za(flat, flng):
            p.append(f"{fw}: a place abroad cannot sit inside South Africa")
        if not CLAIM_RE.match(str(f.get("claim_by") or "")):
            p.append(f"{fw}: claim_by must be police, customs, court, source, 'party: <name>' or 'agency: <name>'")
        _basis_ok(f.get("basis"), fw, p, place=place or None)
        for j, b in enumerate(f.get("also") or []):
            _basis_ok(b, f"{fw} further source {j + 1}", p, place=place or None)

    for key in ("title", "summary"):
        if BANNED.search(str(c.get(key) or "")):
            p.append(f"{where}: {key} uses wording the route rule forbids about places or people")
    for f in fars:
        for key in ("label", "note"):
            if BANNED.search(str(f.get(key) or "")):
                p.append(f"{where}: far end {key} uses wording the route rule forbids")
    return p


def load_cases(routes_dir: Path = ROUTES_DIR) -> list[tuple[str, dict]]:
    out = []
    if not routes_dir.is_dir():
        return out
    for f in sorted(routes_dir.glob("*.json")):
        try:
            out.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            raise RouteError(f"{f.name}: not valid JSON ({exc})") from exc
    return out


def check(routes_dir: Path = ROUTES_DIR) -> list[dict]:
    """Load and validate every case. Raises RouteError listing every problem."""
    cases = load_cases(routes_dir)
    problems: list[str] = []
    seen = set()
    for name, c in cases:
        problems += validate_case(c, name)
        if c.get("case_id") in seen:
            problems.append(f"{name}: case_id used twice")
        seen.add(c.get("case_id"))
    if sum(1 for _, c in cases if c.get("status") == "approved") > MAX_PUBLIC_CASES:
        problems.append(f"more than {MAX_PUBLIC_CASES} approved cases: the pilot cap")
    if problems:
        raise RouteError("; ".join(problems))
    return [c for _, c in cases]


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _pub_basis(b: dict) -> dict:
    out = {"quote": b["quote"], "url": b["url"], "outlet": b["outlet"]}
    if b.get("locator"):
        out["locator"] = str(b["locator"])
    return out


def public_routes(cases: list[dict]) -> list[dict]:
    """The page / records.json form: one entry per line (a South African end
    and a place abroad), with every approved case that names both."""
    lines: dict[tuple, dict] = {}
    for c in sorted((c for c in cases if c.get("status") == "approved"),
                    key=lambda c: c["incident_date"]):
        sa = c["sa_end"]
        for f in c["far_ends"]:
            key = (round(sa["lat"], 4), round(sa["lng"], 4), round(f["lat"], 4), round(f["lng"], 4))
            line = lines.get(key)
            if line is None:
                line = lines[key] = {
                    "id": f"{_norm(f['place']).replace(' ', '-')}--{_norm(sa['label']).replace(' ', '-')}"[:90],
                    "sa": {"label": sa["label"], "province": sa["province"],
                           "lat": sa["lat"], "lng": sa["lng"], "precision": sa["precision"]},
                    "far": {"label": f["label"], "place": f["place"], "country": f["country"],
                            "place_kind": f["place_kind"], "lat": f["lat"], "lng": f["lng"],
                            "precision": f["precision"], "note": f.get("note") or ""},
                    "cases": [],
                }
            line["cases"].append({
                "case_id": c["case_id"], "kind": c["kind"], "title": c["title"],
                "date": c["incident_date"], "when": _day(c["incident_date"]),
                "summary": c["summary"], "arrests": c["arrests"],
                "role": f["role"], "role_text": ROLES[f["role"]],
                "claim_by": f["claim_by"],
                # What this case's own source does and does not say about the
                # place abroad. Kept with the case: a line shared by several
                # cases must not credit one case's source for all of them.
                "far_note": str(f.get("note") or "").strip(),
                "far_sources": [_pub_basis(b) for b in [f["basis"], *(f.get("also") or [])]],
                "sa_sources": [_pub_basis(b) for b in sa["basis"]],
                "disagreements": [str(d) for d in c.get("disagreements") or []],
                "approved_at": str(c.get("approved_at"))[:10],
            })
    for line in lines.values():
        # The line's own note only when every case on it says the same thing;
        # otherwise each case carries its note (far_note) and the line has none.
        notes = {c["far_note"] for c in line["cases"]}
        line["far"]["note"] = notes.pop() if len(notes) == 1 else ""
    return [dict(l, caption=CAPTION) for l in lines.values()]


def page_routes(routes_dir: Path = ROUTES_DIR) -> list[dict]:
    return public_routes(check(routes_dir))


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    try:
        cases = check()
    except RouteError as exc:
        print(f"[routes] REFUSED: {exc}")
        return 1
    if cmd == "check":
        print(f"[routes] {len(cases)} case(s) follow the rule")
        return 0
    for c in cases:
        ends = ", ".join(f["label"] for f in c["far_ends"])
        print(f"{c['case_id']:45} {c['status']:9} {c['incident_date']}  {c['sa_end']['label']} <- {ends}")
    lines = public_routes(cases)
    print(f"[routes] public: {len(lines)} line(s), {sum(len(l['cases']) for l in lines)} case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
