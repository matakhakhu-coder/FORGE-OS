"""
place_signals.py — place estimates and suggested precincts for every stream.

Batch 3 of the collector fixes (23 Oct 2026). Before this, only
Infrastructure and Priority Intelligence signals got a place estimate, so a
killing the text placed ("Musina businessman shot...") was saved with no
location. This module gives Crime & Security and Africa & World signals the
same estimate (``infra_signals.locate``), adds a suggested SAPS precinct to
every placed signal, and flags a place that is only where an arrest or a
court step happened ("officers arrested in Polokwane", "appeared in the
Musina court") instead of placing the signal there.

What this does not do: it reads only the saved title and summary (full
article reading is a later batch), it does not merge records, and a
suggested precinct is a search lead from an estimated point, not a legal
boundary or a confirmed event location. Infrastructure placement is left as
it was (the arrest wording there is evidence linked to the incident).
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Optional

from forage.collectors import infra_signals as _infra

# Merged 23 Oct 2026 for Crime & Security only. The owner held the Africa &
# World estimates back, and with them the precincts on other streams, the
# top-up and KZN words for Infrastructure and PI, and PI's court rule; those
# stay on branch batch3-places.
PLACE_STREAMS = ("CRIME_INTEL",)


# ── Suggested precinct ───────────────────────────────────────────────────────
# Same point-in-outline test as tools/coverage_areas.py (point_in_rings,
# _bbox, _bbox_near / holding_precinct); copied here so the collectors do
# not import the publishing tools. tests/test_place_signals.py checks the two
# agree.

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


def point_in_rings(lat: float, lng: float, rings: list) -> bool:
    """rings: [[ [lng, lat], ... ], ...] (outer rings of a precinct)."""
    return any(_point_in_ring(lng, lat, r) for r in rings if len(r) >= 3)


def _bbox(rings: list):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(ys), min(xs), max(ys), max(xs)) if xs else None


def _bbox_near(bb, lat, lng, r_km) -> bool:
    dlat = r_km / 110.574
    dlng = r_km / (111.320 * max(0.2, math.cos(math.radians(lat))))
    return not (lat + dlat < bb[0] or lat - dlat > bb[2] or lng + dlng < bb[1] or lng - dlng > bb[3])


@lru_cache(maxsize=1)
def _precincts() -> tuple:
    try:
        d = json.loads((_infra.GAZ_DIR / "saps_precincts.json").read_text())
    except Exception:
        return ()
    out = []
    for p in d.get("precincts", []):
        rings = p.get("rings") or []
        out.append({"name": p["name"], "rings": rings, "_bbox": _bbox(rings) if rings else None})
    return tuple(out)


def holding_precinct(lat: Optional[float], lng: Optional[float]) -> str:
    """Name of the SAPS precinct whose outline holds the point ('' if none)."""
    if lat is None or lng is None:
        return ""
    for pr in _precincts():
        if pr["_bbox"] is not None and _bbox_near(pr["_bbox"], lat, lng, 0) \
                and point_in_rings(lat, lng, pr["rings"]):
            return pr["name"]
    return ""


def add_precinct(loc: Optional[dict]) -> Optional[dict]:
    """Add a suggested precinct to a location candidate that has a point."""
    if not loc or loc.get("precinct") or loc.get("lat") is None or loc.get("lng") is None:
        return loc
    name = holding_precinct(loc["lat"], loc["lng"])
    if name:
        loc["precinct"] = name
        loc["precinct_basis"] = ("suggested: the SAPS precinct outline (2019 snapshot) holding the estimated "
                                 "place point; a search lead, not a confirmed event location")
    return loc


# ── Arrest and court places ──────────────────────────────────────────────────
# A place named only as where someone was arrested, stationed or taken to
# court is not where the incident happened (the Sekhukhune officers were
# filed under Polokwane because the arrest was there). Such a place is kept
# for review, not used to place the signal.

_ARREST_LEAD = re.compile(
    r"\b(?:arrested|re-arrested|nabbed|apprehended|detained|stationed|based|deployed|"
    r"appeared|appear|appears|appearing|court|courts|charged|sentenced|convicted|remanded)\b"
    r"[^.;:!?\n]{0,45}?\b(?:in|at)\s+(?:the\s+)?$", re.I)
_COURT_TRAIL = re.compile(
    r"^(?:['’]s?)?\s+(?:(?:magistrate['’]?s?|magistrates['’]?|regional|district|high|periodical)\s+)*"
    r"courts?\b", re.I)


# Words between the arrest verb and the place that show the place belongs to
# the incident, not the arrest: "arrested after a drug lab was discovered at
# Soweto", "convicted for the murder of ... in Weenen".
_INCIDENT_BETWEEN = re.compile(
    r"\b(?:after|following|during|where|when|while|murder\w*|kill\w*|shoot\w*|shot|robb\w*|heist|attack\w*|"
    r"rape\w*|hijack\w*|stab\w*|discover\w*|found|crash\w*|explosion|raid\w*|house|home|store|shop|scene)\b", re.I)


def _arrest_lead(before: str) -> bool:
    m = _ARREST_LEAD.search(before)
    return bool(m) and not _INCIDENT_BETWEEN.search(m.group(0))


def _role_spans(text: str, matched: str) -> tuple[list, list]:
    """(arrest/court spans, other spans) of the matched place in the text."""
    arrest, other = [], []
    for m in re.finditer(r"(?<![\w])" + re.escape(matched) + r"(?![\w])", text):
        before = text[max(0, m.start() - 70):m.start()]
        after = text[m.end():m.end() + 40]
        if _arrest_lead(before) or _COURT_TRAIL.match(after):
            arrest.append((m.start(), m.end()))
        else:
            other.append((m.start(), m.end()))
    return arrest, other


# Batch 4 look-alike: team names that are also towns. "Springbok Women to
# face Ireland" is rugby, not Springbok in the Northern Cape (seen in the 23
# Oct wide replay once Crime & Security got place estimates).
_TEAM_LOOKALIKE = {
    "Springbok": re.compile(r"\bspringboks\b|\bspringbok\s+(women|sevens|rugby|coach|squad|captain|team|"
                            r"players?|fans|xv|skipper|wing|lock|flyhalf)\b|\brugby\b", re.I),
}


def estimate(text: str, province: Optional[str] = None) -> tuple[Optional[dict], Optional[dict]]:
    """(location candidate, review flag) for one story's text.

    The candidate is ``infra_signals.locate`` plus a suggested precinct. If
    every mention of that place is an arrest or court place, the text is
    read again without those mentions; if no other place is left, there is
    no candidate and the review flag carries the arrest/court place instead.
    """
    loc = _infra.locate(text, province=province, collector_topup=True)
    if not loc:
        return None, None
    team = _TEAM_LOOKALIKE.get(loc["place"])
    if team and team.search(text):
        return None, None
    arrest, other = _role_spans(text, loc["matched_text"])
    if not arrest or other:
        return add_precinct(loc), None
    masked = text
    for a, b in reversed(arrest):
        masked = masked[:a] + " " * (b - a) + masked[b:]
    flagged = add_precinct(dict(loc))
    review = {
        "place_review": "arrest_or_court_place",
        "place_review_note": (f"The text names {loc['matched_text']} only as where an arrest, posting or court "
                              "step happened; the signal is not placed there. Check where the incident was."),
        "arrest_place_candidate": flagged,
    }
    loc2 = _infra.locate(masked, province=province, collector_topup=True)
    if loc2 and loc2["place"] != loc["place"]:
        arrest2, other2 = _role_spans(masked, loc2["matched_text"])
        if other2:
            return add_precinct(loc2), review
    return None, review


def add_place(stream: str, meta: Optional[dict], title: str, summary: str = "",
              province: Optional[str] = None) -> dict:
    """Fill in the place for a routed signal's metadata (returns the dict).

    Crime & Security signals with no candidate get one from ``estimate``; a
    Crime & Security candidate that already has a point gets a suggested
    precinct. Existing candidates are never moved, and other streams are
    returned untouched.
    """
    meta = meta if meta is not None else {}
    if stream not in PLACE_STREAMS:
        return meta  # other streams are left exactly as before Batch 3
    loc = meta.get("location_candidate")
    if loc:
        add_precinct(loc)
        return meta
    if meta.get("place_review"):
        return meta
    loc, review = estimate(f"{title}. {summary}", province=province)
    if loc:
        meta["location_candidate"] = loc
    if review:
        meta.update(review)
    return meta
