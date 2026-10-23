#!/usr/bin/env python3
"""Coverage areas: where the scheduled collection runs look (19 Oct 2026).

Every collection run starts by working out its search areas from the pins that
are published on the map right now, so a pin published somewhere new brings its
area into the next run with no schedule edit:

  1. Take every published, non-retracted checked pin (docs/signals, through
     tools/checked_signals.py). Held-back places (a batch's "held_back" list),
     staged candidates and anything without published_at make no coverage.
  2. Draw a circle of COVERAGE_RADIUS_KM around each pin.
  3. Merge circles that overlap (pins up to 2 x radius apart) into one area,
     separately per province, so a Western Cape area can never absorb a pin
     from another province (the Sprint 5 pattern test is Western Cape only).
  4. Name what each area covers, from the offline gazetteer in data/gazetteer:
     SAPS precincts whose outline touches a circle (with the precincts that
     hold a pin marked), places, suburbs, townships and villages inside a
     circle, and the districts and municipalities they fall in.
  5. Turn that into a capped list of news searches per area.

The Western Cape areas are listed but are collected by the locked Sprint 5
Western Cape pass (private/sprint5/predictions.md), not by this sweep, unless
SWEEP_PATTERN_TEST_AREAS is switched on. Nothing found by this tool feeds the
Sprint 5 predictions, scoring or thresholds: every staged item carries
feeds_scoring = false and the tool refuses to write under private/sprint5.

It writes nothing public. Areas and sweep results go to private/coverage/
(git-ignored, like the rest of private/). It never imports, publishes, edits a
pin or draws a link.

Commands (run from the repo root):
    python tools/coverage_areas.py compute            # areas from the pins now
    python tools/coverage_areas.py compute --radius-km 40
    python tools/coverage_areas.py sweep --from 2026-10-19 --to 2026-10-21
        # Google News staging for each area outside the pattern test, each
        # area in its own process with a time limit (AREA_TIME_LIMIT_S); an
        # area that runs out of time is logged as timed_out and its count is
        # inconclusive, never zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import checked_signals as cs_mod  # noqa: E402

# ── Config: change these, nothing else ────────────────────────────────────────

# Radius of the circle drawn around each published pin, in km. FORGE_COVERAGE_
# RADIUS_KM (environment) or --radius-km overrides it for one run.
# 30 km: in the 28 pins of 19 Oct 2026 it is the smallest round value that
# reaches the police station of the precinct every pin lies in, with room for
# approximate pins (the farthest is Doornpan Farm to Ottosdal station, 18 km,
# and that pin may be a different farm of the same name), and it still keeps
# towns that police and papers treat separately apart: at 40 km Ottosdal and
# Ipelegeng (63 km apart) and Worcester and Cape Town (78 km) would merge.
COVERAGE_RADIUS_KM = 30.0

# Time one area's searches may take in a sweep before it is stopped and logged
# as timed_out (its count is then inconclusive, not zero).
AREA_TIME_LIMIT_S = 900
# Cap on news searches per area and stream, so one big area cannot eat the
# run. An area with pins in two streams can run up to twice this.
MAX_QUERIES_PER_AREA = 45
# Western Cape areas are collected by the locked Sprint 5 pass; leave False
# unless the user asks for an extra Western Cape sweep (kept out of scoring).
SWEEP_PATTERN_TEST_AREAS = False
# Provinces where pins share an area only when they also lie in the same SAPS
# precinct (from the gazetteer outlines). Limpopo's rural pins are reported by
# village and policing area, and at 2 x 30 km single linkage chains across
# districts: on 22 Oct 2026 it put Polokwane (Capricorn), Masemola and
# Mecklenburg (Sekhukhune) under Lebowakgomo.
# Mpumalanga added 23 Oct 2026 for the same reason (rural trusts and villages
# 40 km apart, e.g. Lillydale Trust under Calcutta and Masoyi).
# KwaZulu-Natal added 23 Oct 2026 (owner decision, voice call, Newcastle sweep):
# Madadeni (about 9 km) and Osizweni (about 20 km) have their own precincts and
# would otherwise chain into the Newcastle area at 2 x 30 km.
PRECINCT_SPLIT_PROVINCES = {"Limpopo", "Mpumalanga", "KwaZulu-Natal"}

GAZ_DIR = ROOT / "data" / "gazetteer"
PLACES_PATH = GAZ_DIR / "za_places.json"
PRECINCTS_PATH = GAZ_DIR / "saps_precincts.json"
OUT_DIR = ROOT / "private" / "coverage"
PATTERN_TEST_DIR = ROOT / "private" / "sprint5"

SAST = timezone(timedelta(hours=2))

# News search wording per province: English everywhere, plus the languages the
# earlier sweeps found local reporting in.
QUERY_TERMS = {
    "en": '(murder OR "shot dead" OR stabbed OR "body found" OR killed)',
    "af": "(moord OR doodgeskiet OR doodgesteek OR lyk)",
    "tn": "(polao OR bolailwe)",
}

# Search wording per stream (owner decision, 23 Oct 2026). Until then every
# area searched the killing words above, whatever its pins were. Now each
# stream searches its own words, and only in areas that already hold a pin
# in that stream, to keep the number of searches down:
#   CRIME_INTEL     killings: the words above, unchanged
#   INFRASTRUCTURE  failures, theft and vandalism of services (the incident
#                   types of the 23 Oct Limpopo benchmark)
#   PRIORITY        Priority Intelligence, worded so court follow-ups (charged,
#                   bail, court) come back as well as first reports
# A language with no wording for a stream is skipped for that stream: no
# Setswana words were written for infrastructure or PI, so North West and
# Northern Cape areas search those in English and Afrikaans only.
# A pin in a stream with no wording here (for example GLOBAL) adds no
# searches; the area records it under streams_without_terms.
QUERY_TERMS_BY_STREAM = {
    "CRIME_INTEL": QUERY_TERMS,
    "INFRASTRUCTURE": {
        "en": '(substation OR "cable theft" OR "copper theft" OR vandalised OR "water outage" '
              'OR "without water" OR "burst pipe" OR "power outage" OR "sewage spill" OR borehole)',
        "af": '(substasie OR kabeldiefstal OR koperdiefstal OR "sonder water" OR kragonderbreking OR pypbreuk)',
    },
    "PRIORITY": {
        "en": '(Hawks OR SIU OR NPA OR IDAC OR charged OR bail OR court OR syndicate OR fraud '
              'OR corruption OR extortion)',
        "af": "(Valke OR aangekla OR borgtog OR hof OR sindikaat OR bedrog OR korrupsie OR afpersing)",
    },
}
# Stream used for a pin record that has no stream field (records from before
# the stream column were all killings).
DEFAULT_PIN_STREAM = "CRIME_INTEL"
# Order in which an area's streams are searched and listed.
STREAM_ORDER = ["CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY"]
# Map Streams panel names, for the report.
STREAM_LABEL = {"CRIME_INTEL": "Crime & Security", "INFRASTRUCTURE": "Infrastructure",
                "PRIORITY": "Priority Intelligence", "GLOBAL": "Africa & World"}
PROVINCE_LANGS = {
    "North West": ["en", "af", "tn"],
    "Western Cape": ["en", "af"],
    "Northern Cape": ["en", "af", "tn"],
    "Free State": ["en", "af"],
}


def radius_km(override: float | None = None) -> float:
    if override is not None:
        r = float(override)
    elif os.environ.get("FORGE_COVERAGE_RADIUS_KM", "").strip():
        r = float(os.environ["FORGE_COVERAGE_RADIUS_KM"])
    else:
        r = COVERAGE_RADIUS_KM
    if not (0 < r <= 300):
        raise ValueError(f"coverage radius {r} km is outside 0-300 km")
    return r


# ── Geometry (pure Python; distances in km) ───────────────────────────────────

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def _xy(lat: float, lng: float, lat0: float, lng0: float) -> tuple[float, float]:
    """Local flat projection around (lat0, lng0), km. Fine at these distances."""
    return ((lng - lng0) * 111.320 * math.cos(math.radians(lat0)), (lat - lat0) * 110.574)


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xc > x:
                inside = not inside
    return inside


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_rings(lat: float, lng: float, rings: list) -> bool:
    """rings: [[ [lng, lat], ... ], ...] (outer rings of a precinct)."""
    return any(_point_in_ring(lng, lat, r) for r in rings if len(r) >= 3)


def ring_distance_km(lat: float, lng: float, rings: list) -> float:
    """0 inside the outline, else km from the point to the nearest edge."""
    if point_in_rings(lat, lng, rings):
        return 0.0
    best = math.inf
    for ring in rings:
        pts = [_xy(y, x, lat, lng) for x, y in ring]
        for i in range(len(pts) - 1):
            best = min(best, _seg_dist(0.0, 0.0, *pts[i], *pts[i + 1]))
    return best


def _bbox(rings: list) -> tuple[float, float, float, float] | None:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(ys), min(xs), max(ys), max(xs)) if xs else None


def _bbox_near(bb, lat, lng, r_km) -> bool:
    dlat = r_km / 110.574
    dlng = r_km / (111.320 * max(0.2, math.cos(math.radians(lat))))
    return not (lat + dlat < bb[0] or lat - dlat > bb[2] or lng + dlng < bb[1] or lng - dlng > bb[3])


# ── Pins and clusters ─────────────────────────────────────────────────────────

_PLACE_SPLIT = re.compile(r"\s*[,/]\s*|\s+\(|\)")
_NOT_A_PLACE = re.compile(r"\b(approx|area only|village only|farm area|open field|near|primary|school|walk)\b"
                          r"|\d|\bst\b|\brd\b|\bave\b|\bcres\b|\bdr\b", re.I)


def pin_place_names(title: str) -> list[str]:
    """Place names in a pin title's location part (before the colon):
    'Voorbrug, Delft (Camdeboo St, approx.)' -> ['Voorbrug', 'Delft']."""
    loc = re.sub(r"\s+near\s+", ", ", title.split(":", 1)[0], flags=re.I)
    out = []
    for part in _PLACE_SPLIT.split(loc):
        p = part.strip(" .()")
        p = re.sub(r"^(near|farm near|farm)\s+|\s+area$", "", p, flags=re.I)
        if len(p) < 3 or _NOT_A_PLACE.search(p) or p.lower() in ("area", "farm", "village", "township"):
            continue
        out.append(p)
    return out


def published_pins(index_path: pathlib.Path = cs_mod.INDEX_PATH) -> list[dict]:
    """Published, non-retracted checked pins with the province their batch
    declares. Held-back places never appear here (they have no pin record)."""
    cset = cs_mod.load_index(index_path)
    prov = cs_mod.provinces(cset)
    pins = []
    for s in cset.active:
        if not str(s.get("published_at") or "").strip():
            continue
        pins.append({
            "signal_id": s["signal_id"], "title": s["title"],
            "lat": float(s["lat"]), "lng": float(s["lng"]),
            "province": prov.get(s["signal_id"], ""),
            "place_names": pin_place_names(s["title"]),
            "stream": str(s.get("stream") or DEFAULT_PIN_STREAM),
        })
    pins.sort(key=lambda p: p["signal_id"])
    return pins


def held_back(index_path: pathlib.Path = cs_mod.INDEX_PATH) -> list[dict]:
    """The batches' held_back lists (for the report and for dedup only)."""
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    out = []
    for e in idx.get("batches", []):
        data = json.loads((index_path.parent / e["file"]).read_text(encoding="utf-8"))
        for h in data.get("held_back") or []:
            out.append({**h, "province": data.get("province")})
    return out


def cluster_pins(pins: list[dict], r_km: float) -> list[list[dict]]:
    """Single-linkage clusters: two pins share an area when their circles
    overlap (distance <= 2r). Never across provinces."""
    parent = list(range(len(pins)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pins)):
        for j in range(i + 1, len(pins)):
            if pins[i]["province"] != pins[j]["province"]:
                continue
            if pins[i].get("precinct") != pins[j].get("precinct"):
                continue
            if haversine_km(pins[i]["lat"], pins[i]["lng"], pins[j]["lat"], pins[j]["lng"]) <= 2 * r_km:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, p in enumerate(pins):
        groups.setdefault(find(i), []).append(p)
    return list(groups.values())


# ── Gazetteer ─────────────────────────────────────────────────────────────────

_GAZ_CACHE: dict = {}


def load_gazetteer(places_path: pathlib.Path = PLACES_PATH,
                   precincts_path: pathlib.Path = PRECINCTS_PATH) -> tuple[list, list]:
    key = (str(places_path), str(precincts_path))
    if key not in _GAZ_CACHE:
        places = json.loads(places_path.read_text(encoding="utf-8"))
        fields = places["fields"]
        rows = [dict(zip(fields, r)) for r in places["rows"]]
        prec = json.loads(precincts_path.read_text(encoding="utf-8"))["precincts"]
        for p in prec:
            p["_bbox"] = _bbox(p["rings"]) if p["rings"] else None
        _GAZ_CACHE[key] = (rows, prec)
    return _GAZ_CACHE[key]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def describe_area(members: list[dict], r_km: float, places: list, precincts: list) -> dict:
    lat0 = sum(p["lat"] for p in members) / len(members)
    lng0 = sum(p["lng"] for p in members) / len(members)
    province = members[0]["province"]

    # Precincts: outline within r of a pin (or, with no outline, station within r).
    prec_out = []
    for pr in precincts:
        best, holds = math.inf, []
        for p in members:
            if pr["_bbox"] is not None:
                if not _bbox_near(pr["_bbox"], p["lat"], p["lng"], r_km):
                    continue
                d = ring_distance_km(p["lat"], p["lng"], pr["rings"])
                if d == 0.0:
                    holds.append(p["signal_id"])
            elif pr.get("station"):
                d = haversine_km(p["lat"], p["lng"], pr["station"][1], pr["station"][0])
            else:
                continue
            best = min(best, d)
        if best <= r_km:
            st = pr.get("station")
            st_km = (min(haversine_km(p["lat"], p["lng"], st[1], st[0]) for p in members)
                     if st else None)
            prec_out.append({"name": pr["name"], "holds_pins": holds,
                             "edge_km": round(best, 1),
                             "station_km": round(st_km, 1) if st_km is not None else None})
    prec_out.sort(key=lambda x: (-len(x["holds_pins"]), x["station_km"] if x["station_km"] is not None else 999, x["name"]))

    # Places inside a circle.
    dlat = r_km / 110.574
    plc = []
    for row in places:
        if not any(abs(row["lat"] - p["lat"]) <= dlat for p in members):
            continue
        d = min(haversine_km(p["lat"], p["lng"], row["lat"], row["lng"]) for p in members)
        if d <= r_km:
            plc.append({"name": row["name"], "km": round(d, 1), "kind": row["feature_code"],
                        "population": row["population"], "province": row["province"],
                        "district": row["district"], "municipality": row["municipality"]})
    plc.sort(key=lambda x: (-x["population"], x["km"], x["name"]))
    seen_names: set = set()
    plc = [x for x in plc if not (x["name"].lower() in seen_names or seen_names.add(x["name"].lower()))]

    districts = sorted({x["district"] for x in plc if x["district"]})
    munis = sorted({x["municipality"] for x in plc if x["municipality"]})
    provinces_touched = sorted({x["province"] for x in plc if x["province"]} | {province})

    # Name: the nearest notable place (population >= 10 000 within half the
    # radius) to each pin, most pins first; else the precincts holding pins.
    # In the same-precinct provinces an area is one precinct's pins, so the
    # notable place must lie inside that precinct: Seshego (no population in
    # the gazetteer) was named after Polokwane city, 9 km away in the next
    # precinct, and showed up as a second "Polokwane" (23 Oct 2026).
    split = province in PRECINCT_SPLIT_PROVINCES
    anchors: dict[str, int] = {}
    for p in members:
        near = [x for x in plc if x["population"] >= 10000
                and haversine_km(p["lat"], p["lng"], *_latlng(x, places)) <= r_km / 2]
        if split and p.get("precinct"):
            rings = _precinct_rings(p["precinct"], precincts)
            near = [x for x in near if rings and point_in_rings(*_latlng(x, places), rings)]
        if near:
            pick = min(near, key=lambda x: haversine_km(p["lat"], p["lng"], *_latlng(x, places)))["name"]
        else:
            holding = [pr["name"] for pr in prec_out if p["signal_id"] in pr["holds_pins"]]
            pick = holding[0] if holding else (p["place_names"][-1] if p["place_names"] else "Unnamed")
        anchors[pick] = anchors.get(pick, 0) + 1
    # Suburbs of a city of 500 000+ in the area are named as that city
    # (not in the same-precinct provinces, where the area is one precinct).
    big = {x["municipality"]: x["name"] for x in reversed(plc) if x["population"] >= 500000}
    if big and not split:
        merged: dict[str, int] = {}
        muni_of = {x["name"]: x["municipality"] for x in plc}
        for k, n in anchors.items():
            k2 = big.get(muni_of.get(k), k)
            merged[k2] = merged.get(k2, 0) + n
        anchors = merged
    ordered = sorted(anchors, key=lambda k: (-anchors[k], k))
    name = " / ".join(ordered[:3]) + (f" and {len(ordered) - 3} more" if len(ordered) > 3 else "")

    pattern = province == cs_mod.PATTERN_TEST_PROVINCE
    area = {
        "key": f"{_slug(province)}--{_slug(ordered[0])}",
        "name": f"{name} ({province})",
        "province": province,
        "provinces_touched": provinces_touched,
        "pattern_test": pattern,
        "collected_by": ("locked Sprint 5 Western Cape pass" if pattern and not SWEEP_PATTERN_TEST_AREAS
                         else "coverage sweep"),
        "centre": [round(lat0, 4), round(lng0, 4)],
        "radius_km": r_km,
        "pins": [{"signal_id": p["signal_id"], "title": p["title"], "lat": p["lat"], "lng": p["lng"]}
                 for p in sorted(members, key=lambda p: p["title"])],
        "districts": districts,
        "municipalities": munis,
        "precincts": prec_out,
        "places": plc,
    }
    by_stream = build_queries_by_stream(area, members)
    area["streams"] = list(by_stream)
    area["queries_by_stream"] = by_stream
    area["streams_without_terms"] = sorted({_pin_stream(p) for p in members}
                                           - set(QUERY_TERMS_BY_STREAM))
    area["queries"] = [q for qs in by_stream.values() for q in qs]
    return area


def _precinct_rings(name: str, precincts: list) -> list:
    """Outline rings of the named precinct ([] if it has none)."""
    for pr in precincts:
        if pr["name"] == name:
            return pr.get("rings") or []
    return []


def label_duplicates(areas: list[dict]) -> None:
    """No two areas may carry the same label. When they do (two areas named
    after the same place), each one is labelled with the precincts that hold
    its pins as well, e.g. "Polokwane, Seshego precinct (Limpopo)"; any that
    still clash get a number."""
    def clash():
        names: dict[str, list[dict]] = {}
        for a in areas:
            names.setdefault(a["name"], []).append(a)
        return [g for g in names.values() if len(g) > 1]

    for group in clash():
        for a in group:
            base = a["name"][: -len(f" ({a['province']})")] if a["name"].endswith(f" ({a['province']})") else a["name"]
            holders = [pr["name"] for pr in a["precincts"] if pr["holds_pins"]]
            if holders:
                tag = ", ".join(holders[:2]) + (" precinct" if len(holders[:2]) == 1 else " precincts")
                a["name"] = f"{base}, {tag} ({a['province']})"
    for group in clash():
        for n, a in enumerate(sorted(group, key=lambda a: a["key"])[1:], start=2):
            a["name"] = a["name"][: -len(f" ({a['province']})")] + f" #{n} ({a['province']})"


def holding_precinct(pin: dict, precincts: list) -> str:
    """Name of the SAPS precinct whose outline holds the pin ('' if none)."""
    for pr in precincts:
        if pr["_bbox"] is not None and _bbox_near(pr["_bbox"], pin["lat"], pin["lng"], 0) \
                and point_in_rings(pin["lat"], pin["lng"], pr["rings"]):
            return pr["name"]
    return ""


def _latlng(place: dict, rows: list) -> tuple[float, float]:
    # places entries carry only km; look the point up once by name+municipality.
    key = (id(rows), place["name"], place["municipality"])
    cache = _latlng.__dict__.setdefault("cache", {})
    if key not in cache:
        for r in rows:
            if (r["name"], r["municipality"]) == key[1:]:
                cache[key] = (r["lat"], r["lng"])
                break
        else:
            cache[key] = (0.0, 0.0)
    return cache[key]


def _pin_stream(p: dict) -> str:
    return str(p.get("stream") or DEFAULT_PIN_STREAM)


def search_names(area: dict, members: list[dict], stream: str | None = None) -> list[str]:
    """Names to search, most useful first: precincts that hold a pin, the
    pins' own place names, other precincts by station distance, then the
    biggest places. With a stream, only that stream's pins count as the
    area's pins for the first two groups."""
    seen, out = set(), []

    def add(n):
        k = n.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip())

    own = [p for p in members if stream is None or _pin_stream(p) == stream]
    own_ids = {p["signal_id"] for p in own}
    for pr in area["precincts"]:
        if pr["holds_pins"] and (stream is None or own_ids & set(pr["holds_pins"])):
            add(pr["name"])
    for p in own:
        for n in p["place_names"]:
            add(n)
    for pr in area["precincts"]:
        add(pr["name"])
    for pl in area["places"]:
        if pl["population"] >= 5000:
            add(pl["name"])
    return out


def build_queries(area: dict, members: list[dict], stream: str = DEFAULT_PIN_STREAM) -> list[str]:
    """One stream's searches for an area, capped at MAX_QUERIES_PER_AREA."""
    terms = QUERY_TERMS_BY_STREAM.get(stream)
    if not terms:
        return []
    langs = [l for l in PROVINCE_LANGS.get(area["province"], ["en"]) if l in terms]
    qs = []
    for n in search_names(area, members, stream):
        for lang in langs:
            qs.append(f'"{n}" {terms[lang]}')
            if len(qs) >= MAX_QUERIES_PER_AREA:
                return qs
    return qs


def build_queries_by_stream(area: dict, members: list[dict]) -> dict[str, list[str]]:
    """Searches per stream, only for the streams the area holds pins in."""
    held = {_pin_stream(p) for p in members}
    order = STREAM_ORDER + sorted(held - set(STREAM_ORDER))
    out = {}
    for st in order:
        if st in held and st in QUERY_TERMS_BY_STREAM:
            out[st] = build_queries(area, members, st)
    return out


def query_stream_map(area: dict) -> dict[str, str]:
    """Search text -> the stream it was built for (areas written before
    23 Oct 2026 have no queries_by_stream: all their searches were killings)."""
    by = area.get("queries_by_stream")
    if not by:
        return {q: DEFAULT_PIN_STREAM for q in area.get("queries", [])}
    return {q: st for st, qs in by.items() for q in qs}


def compute_areas(pins: list[dict], r_km: float | None = None,
                  places_path: pathlib.Path = PLACES_PATH,
                  precincts_path: pathlib.Path = PRECINCTS_PATH) -> dict:
    r = radius_km(r_km)
    places, precincts = load_gazetteer(places_path, precincts_path)
    pins = [{**p, "precinct": holding_precinct(p, precincts)}
            if p["province"] in PRECINCT_SPLIT_PROVINCES else p for p in pins]
    areas = [describe_area(m, r, places, precincts) for m in cluster_pins(pins, r)]
    # Keys must be unique; add a number when two areas share an anchor.
    seen: dict[str, int] = {}
    for a in areas:
        n = seen.get(a["key"], 0)
        seen[a["key"]] = n + 1
        if n:
            a["key"] = f"{a['key']}-{n + 1}"
    label_duplicates(areas)
    areas.sort(key=lambda a: (not a["pattern_test"], a["province"], -len(a["pins"]), a["key"]))
    pin_ids = sorted(p["signal_id"] for p in pins)
    return {
        "schema": "forge.coverage_areas", "version": 1,
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "radius_km": r,
        "pattern_test_province": cs_mod.PATTERN_TEST_PROVINCE,
        "pins_fingerprint": "sha256:" + hashlib.sha256("\n".join(pin_ids).encode()).hexdigest(),
        "pin_count": len(pins),
        "areas": areas,
    }


def new_areas(current: dict, previous: dict | None) -> list[str]:
    """Keys of areas none of whose pins were covered last time."""
    if not previous:
        return [a["key"] for a in current["areas"]]
    old = {p["signal_id"] for a in previous.get("areas", []) for p in a["pins"]}
    return [a["key"] for a in current["areas"] if not any(p["signal_id"] in old for p in a["pins"])]


# ── Output ────────────────────────────────────────────────────────────────────

def _safe_out(path: pathlib.Path) -> pathlib.Path:
    path = path.resolve()
    if PATTERN_TEST_DIR.resolve() in path.parents or path == PATTERN_TEST_DIR.resolve():
        raise ValueError("coverage output must not go under private/sprint5 (pattern test files)")
    for public in (ROOT / "docs", ROOT / "dist", ROOT / "publisher"):
        if public.resolve() in path.parents:
            raise ValueError(f"coverage output must stay private, not under {public.name}/")
    return path


def render_markdown(result: dict, held: list[dict], new_keys: list[str]) -> str:
    L = [f"# Coverage areas, computed {result['computed_at']}", "",
         f"Radius {result['radius_km']:g} km around each of {result['pin_count']} published pins; "
         f"overlapping circles merged, per province. Western Cape areas are collected by the "
         f"locked Sprint 5 pass and are the only ones that can feed the pattern test.", ""]
    for a in result["areas"]:
        tag = " (NEW since last run)" if a["key"] in new_keys else ""
        L += [f"## {a['name']}{tag}", "",
              f"- Key: {a['key']}. Collected by: {a['collected_by']}. "
              f"Feeds Sprint 5 scoring: {'only through the locked WC pass' if a['pattern_test'] else 'no'}.",
              f"- Pins ({len(a['pins'])}): " + "; ".join(p["title"].split(":")[0] for p in a["pins"]),
              f"- Districts: {', '.join(a['districts']) or 'none found'}",
              f"- Municipalities: {', '.join(a['municipalities']) or 'none found'}",
              f"- Precincts ({len(a['precincts'])}; * holds a pin): "
              + ", ".join(("*" if pr["holds_pins"] else "") + pr["name"] for pr in a["precincts"]),
              f"- Places ({len(a['places'])}, biggest first): "
              + ", ".join(p["name"] for p in a["places"][:60]) + (" ..." if len(a["places"]) > 60 else ""),
              f"- Searches: {len(a['queries'])}"
              + (" (" + ", ".join(f"{STREAM_LABEL.get(st, st)} {len(q)}"
                                  for st, q in a.get("queries_by_stream", {}).items()) + ")"
                 if a.get("queries_by_stream") else ""), ""]
        if set(a["provinces_touched"]) - {a["province"]}:
            L += [f"- Crosses into: {', '.join(sorted(set(a['provinces_touched']) - {a['province']}))}", ""]
    if held:
        L += ["## Held back (no coverage until published)", ""]
        L += [f"- {h.get('place')} ({h.get('province')})" for h in held]
        L.append("")
    return "\n".join(L)


def write_areas(result: dict, out_dir: pathlib.Path = OUT_DIR,
                index_path: pathlib.Path = cs_mod.INDEX_PATH, today: str | None = None) -> dict:
    """Save the areas. 'NEW' compares with the last areas saved on an earlier
    day, so computing twice in one run (compute, then sweep) keeps the flags."""
    out_dir = _safe_out(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = today or datetime.now(SAST).strftime("%Y-%m-%d")
    latest = out_dir / "areas-latest.json"
    earlier = sorted(f for f in out_dir.glob("areas-????-??-??.json") if f.stem[6:] < stamp)
    previous = json.loads(earlier[-1].read_text()) if earlier else None
    keys = new_areas(result, previous)
    result = {**result, "new_since_last_run": keys, "compared_with": earlier[-1].name if earlier else None}
    blob = json.dumps(result, indent=1, ensure_ascii=False)
    (out_dir / f"areas-{stamp}.json").write_text(blob)
    latest.write_text(blob)
    (out_dir / "areas-latest.md").write_text(render_markdown(result, held_back(index_path), keys))
    return result


# ── Dedup helpers ─────────────────────────────────────────────────────────────

def known_links(index_path: pathlib.Path = cs_mod.INDEX_PATH,
                private_dir: pathlib.Path = ROOT / "private") -> dict[str, str]:
    """Every source link already on a pin, held back, or staged in an earlier
    pass -> where it was seen. Used to mark repeats in a sweep."""
    seen: dict[str, str] = {}

    def note(url, where):
        if isinstance(url, str) and url.startswith("http"):
            seen.setdefault(url.split("#")[0].rstrip("/"), where)

    def note_title(title, where):
        k = title_key(title)
        if k:
            seen.setdefault(k, where)

    idx = json.loads(index_path.read_text(encoding="utf-8"))
    for e in idx.get("batches", []):
        data = json.loads((index_path.parent / e["file"]).read_text(encoding="utf-8"))
        for s in data.get("signals", []):
            md = s.get("metadata") or {}
            note(md.get("link"), f"pin {s['signal_id'][:8]}")
            for src in (md.get("checked") or {}).get("sources") or []:
                note(src.get("url"), f"pin {s['signal_id'][:8]}")
        for h in data.get("held_back") or []:
            note(h.get("source"), f"held back: {h.get('place')}")
    if private_dir.exists():
        for path in list(private_dir.glob("**/candidates*.json")) + list(private_dir.glob("**/staged*.json")) \
                + list(private_dir.glob("coverage/runs/**/sweep*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for url in _urls(data):
                note(url, f"earlier pass: {path.relative_to(private_dir)}")
            for t in _titles(data):
                note_title(t, f"earlier pass: {path.relative_to(private_dir)}")
    return seen


def title_key(title) -> str:
    """'Two arrested after murders ... - EWN' -> 'title:two arrested after murders ...'.
    The same story often comes back under several Google links."""
    if not isinstance(title, str):
        return ""
    t = re.sub(r"\s+-\s+[^-]{2,60}$", "", title.strip())
    t = re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    return f"title:{t}" if len(t) >= 20 else ""


def _titles(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "title" and isinstance(v, str):
                yield v
            else:
                yield from _titles(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _titles(v)


def _urls(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("link", "url", "source") and isinstance(v, str):
                yield v
            else:
                yield from _urls(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _urls(v)


# ── Sweep with a time limit per area ──────────────────────────────────────────

def gnews_fetch(query: str, start: datetime, end: datetime) -> list[dict]:
    """One Google News query, items published in [start, end)."""
    from forage.collectors import google_news_collector as g
    days = (datetime.now(timezone.utc) - start).days + 2
    xml = g._fetch_rss(g._build_rss_url(query))
    out = []
    for it in (g._parse_rss_items(xml, query, days) if xml else []):
        try:
            dt = datetime.strptime(it["pubdate_parsed"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if start <= dt < end:
            out.append({"title": it.get("title"), "outlet": it.get("source_name"), "link": it.get("link"),
                        "published_sast": dt.astimezone(SAST).strftime("%Y-%m-%d %H:%M")})
    time.sleep(g.REQUEST_DELAY)
    return out


def _area_worker(queries, start_iso, end_iso, part_path, fetch):
    start, end = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    with open(part_path, "a", encoding="utf-8") as fh:
        for q in queries:
            try:
                items = fetch(q, start, end)
                fh.write(json.dumps({"query": q, "items": items}, ensure_ascii=False) + "\n")
            except Exception as e:  # one bad query must not end the area
                fh.write(json.dumps({"query": q, "error": f"{type(e).__name__}: {e}"}) + "\n")
            fh.flush()


def sweep_area(area: dict, start: datetime, end: datetime, work_dir: pathlib.Path,
               time_limit_s: float = AREA_TIME_LIMIT_S, fetch=gnews_fetch) -> dict:
    """Run one area's searches in its own process; stop it at the time limit."""
    part = work_dir / f"part-{area['key']}.jsonl"
    part.write_text("")
    ctx = mp.get_context("fork")
    proc = ctx.Process(target=_area_worker,
                       args=(area["queries"], start.isoformat(), end.isoformat(), str(part), fetch),
                       daemon=True)
    t0 = time.monotonic()
    proc.start()
    proc.join(time_limit_s)
    timed_out = proc.is_alive()
    if timed_out:
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
    done = [json.loads(l) for l in part.read_text(encoding="utf-8").splitlines() if l.strip()]
    part.unlink(missing_ok=True)
    errors = [d for d in done if "error" in d]
    if timed_out:
        status = "timed_out"
    elif proc.exitcode not in (0, None):
        status = "error"
    elif len(done) < len(area["queries"]):
        status = "error"
    else:
        status = "complete" if not errors else "partial"
    return {"status": status, "seconds": round(time.monotonic() - t0, 1),
            "queries_planned": len(area["queries"]), "queries_done": len(done) - len(errors),
            "query_errors": [{"query": d["query"], "error": d["error"]} for d in errors],
            "results": [d for d in done if "error" not in d]}


def count_reading(status: str, count: int) -> str:
    """How a run must read an area's count. Only a complete search can say
    'none found'; a timed-out, failed or partial one is inconclusive."""
    if status == "complete":
        return f"{count} found" if count else "none found (search complete)"
    if status == "skipped":
        return "not searched by this sweep"
    return f"inconclusive ({status}): {count} found before it stopped; a low count here is not zero"


def sweep(areas_doc: dict, start: datetime, end: datetime, out_dir: pathlib.Path,
          time_limit_s: float = AREA_TIME_LIMIT_S, fetch=gnews_fetch,
          include_pattern_test: bool | None = None,
          known: dict[str, str] | None = None) -> dict:
    include = SWEEP_PATTERN_TEST_AREAS if include_pattern_test is None else include_pattern_test
    out_dir = _safe_out(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    known = known if known is not None else known_links()
    report = {"schema": "forge.coverage_sweep", "version": 1,
              "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "window_sast": [start.astimezone(SAST).strftime("%Y-%m-%d"),
                              (end - timedelta(seconds=1)).astimezone(SAST).strftime("%Y-%m-%d")],
              "radius_km": areas_doc["radius_km"], "time_limit_s": time_limit_s,
              "feeds_scoring": False, "areas": []}
    for area in areas_doc["areas"]:
        entry = {"key": area["key"], "name": area["name"], "province": area["province"],
                 "pattern_test": area["pattern_test"], "feeds_scoring": False}
        if area["pattern_test"] and not include:
            entry.update(status="skipped", note="collected by the locked Sprint 5 Western Cape pass",
                         items=[], reading=count_reading("skipped", 0))
            report["areas"].append(entry)
            continue
        res = sweep_area(area, start, end, out_dir, time_limit_s, fetch)
        qstream = query_stream_map(area)
        items, by_link = [], {}
        for r in res["results"]:
            st = qstream.get(r["query"], DEFAULT_PIN_STREAM)
            for it in r["items"]:
                link = (it.get("link") or "").split("#")[0].rstrip("/")
                tkey = title_key(it.get("title"))
                prev = by_link.get(link) or (by_link.get(tkey) if tkey else None)
                if prev:
                    prev["queries"].append(r["query"])
                    if st not in prev["searched_for_streams"]:
                        prev["searched_for_streams"].append(st)
                    if link != prev.get("link"):
                        prev.setdefault("other_links", []).append(it.get("link"))
                    continue
                rec = {**it, "queries": [r["query"]], "searched_for_streams": [st], "feeds_scoring": False,
                       "already_known": known.get(link) or (known.get(tkey) if tkey else None)}
                by_link[link] = rec
                if tkey:
                    by_link[tkey] = rec
                items.append(rec)
        new = [i for i in items if not i["already_known"]]
        entry.update(status=res["status"], seconds=res["seconds"],
                     queries_planned=res["queries_planned"], queries_done=res["queries_done"],
                     queries_planned_by_stream={k: len(v) for k, v in
                                                (area.get("queries_by_stream") or {}).items()},
                     query_errors=res["query_errors"], items=items,
                     reading=count_reading(res["status"], len(new)))
        report["areas"].append(entry)
    report["timed_out"] = [a["key"] for a in report["areas"] if a["status"] == "timed_out"]
    (out_dir / "sweep.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    lines = [f"# Coverage sweep {report['ran_at']} (window {report['window_sast'][0]} to "
             f"{report['window_sast'][1]} SAST). Not part of the Sprint 5 pattern test.", ""]
    for a in report["areas"]:
        lines.append(f"- {a['name']}: {a['status']}; {a['reading']}")
    if report["timed_out"]:
        lines += ["", "Timed out (counts inconclusive): " + ", ".join(report["timed_out"])]
    (out_dir / "sweep-log.md").write_text("\n".join(lines) + "\n")
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute", help="work out the areas from the published pins")
    c.add_argument("--radius-km", type=float)
    c.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    c.add_argument("--dry-run", action="store_true", help="print, write nothing")
    s = sub.add_parser("sweep", help="compute, then search every area outside the pattern test")
    s.add_argument("--from", dest="frm", required=True, help="YYYY-MM-DD, SAST")
    s.add_argument("--to", dest="to", required=True, help="YYYY-MM-DD, SAST, inclusive")
    s.add_argument("--radius-km", type=float)
    s.add_argument("--time-limit-s", type=float, default=AREA_TIME_LIMIT_S)
    s.add_argument("--out-dir", type=pathlib.Path, default=None,
                   help="default private/coverage/runs/<today>")
    a = ap.parse_args(argv)

    pins = published_pins()
    result = compute_areas(pins, a.radius_km)
    if a.cmd == "compute":
        if a.dry_run:
            print(render_markdown(result, held_back(), []))
            return 0
        result = write_areas(result, a.out_dir)
        print(f"{len(result['areas'])} areas from {result['pin_count']} pins at {result['radius_km']:g} km "
              f"-> {a.out_dir}/areas-latest.json")
        for ar in result["areas"]:
            new = " NEW" if ar["key"] in result["new_since_last_run"] else ""
            print(f"  {ar['key']}: {ar['name']}, {len(ar['pins'])} pins, {len(ar['precincts'])} precincts, "
                  f"{len(ar['places'])} places, {len(ar['queries'])} searches, {ar['collected_by']}{new}")
        return 0

    result = write_areas(result)
    start = datetime.strptime(a.frm, "%Y-%m-%d").replace(tzinfo=SAST)
    end = datetime.strptime(a.to, "%Y-%m-%d").replace(tzinfo=SAST) + timedelta(days=1)
    out = a.out_dir or OUT_DIR / "runs" / datetime.now(SAST).strftime("%Y-%m-%d")
    rep = sweep(result, start, end, out, a.time_limit_s)
    for ar in rep["areas"]:
        print(f"  {ar['name']}: {ar['status']}; {ar['reading']}")
    print(f"-> {out}/sweep.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
