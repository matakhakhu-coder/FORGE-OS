"""Shared infrastructure-event logic for the civic and Google News collectors.

Built 23 Oct 2026 from a benchmark against the manual standard-search method
(Limpopo, 1 to 23 Oct 2026). The baseline collectors routed 0 of 11 manually
found Limpopo infrastructure incidents with a place attached, sent cable theft
to CRIME_INTEL or GLOBAL, kept items back to 2017, and labelled national Eskom
corporate news and unrelated GroundUp stories as INFRASTRUCTURE.

What this module does, without network access:

* ``classify(title, summary)`` decides whether a story is an infrastructure
  event, which type (cable_theft, substation_transformer, power_outage,
  water_failure, sewage, road_bridge) and which kind (incident, arrest,
  planned, restored, project, policy). Theft or vandalism of infrastructure is
  an infrastructure event first: an arrest for it is supporting evidence, not
  a separate crime incident.
* ``locate(text, province=None)`` finds a candidate place from the GeoNames
  gazetteer already in the repo. It returns a *candidate* only: collectors
  store it in metadata and leave the signal's lat/lng empty, because Sprint 1
  removed shared per-source coordinates for producing false 0 km
  correlations, and a town centroid is an estimate that a reviewer must
  confirm (the same rule the pins follow: "map point estimated").
* ``event_key`` and ``link_supporting`` tie an arrest or follow-up report to
  an earlier incident of the same type at the same place.
* ``in_window`` drops stale items by publication date.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
GAZ_DIR = ROOT / "data" / "gazetteer"

# ── Classification ───────────────────────────────────────────────────────────

_THEFT = r"(theft|thie(f|ves)|stol(e|en)|steal(ing)?|vandal(s|ism|ised|ized)?|sabotag\w*|illegal(ly)? connect\w*|tamper\w*)"

EVENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cable_theft", re.compile(
        r"\b(copper|cable|cables|conductor|overhead line|railway line)\b.{0,60}\b" + _THEFT
        + r"|\b" + _THEFT + r"\b.{0,60}\b(copper|cable|cables|conductor)\b"
        + r"|\bcable (theft|thieves)\b|\bcopper (theft|thieves)\b"
        + r"|\b(essential )?infrastructure (theft|thieves|vandalism|crime)\b", re.I)),
    ("substation_transformer", re.compile(
        r"\b(sub-?stations?|transformers?|mini-?subs?|pylons?|switching station|kiosks?)\b", re.I)),
    ("sewage", re.compile(r"\b(sewage|sewer|wastewater|waste water|raw effluent)\b", re.I)),
    ("water_failure", re.compile(
        r"\b(water (outages?|cuts?|crisis|shortages?|interruptions?|supply|restored|restoration)|"
        r"(without|no) water|dry taps?|burst (water )?(pipe|main)s?|pipe ?(leak|burst|blockage)s?|"
        r"pipeline leaks?|reservoir|boreholes?|water treatment works|\bWTW\b|pump station|"
        r"pumps? (broke|breakdown|failure))\b", re.I)),
    ("power_outage", re.compile(
        r"\b(power (outage|cut|failure|interruption)s?|electricity (outage|cut|interruption|supply)|"
        r"(without|no) (power|electricity)|blackouts?|load[\s-]?shedding|load reduction|(?<!water )outages?)\b", re.I)),
    ("road_bridge", re.compile(
        r"\b(potholes?|sinkholes?|bridge (collapse|washed|damage|closed)|bridge section|"
        r"washed away|washaway|road (collapse|closed|closure|caved)|traffic lights? (out|vandal))\b", re.I)),
]

KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    # order matters: first match wins
    ("restored", re.compile(r"\b(restored|restoration|back on|reconnected|unblocked)\b", re.I)),
    ("planned", re.compile(r"\b(planned|scheduled|maintenance|notice of interruption)\b", re.I)),
    ("procurement", re.compile(r"\b(deal|tenders?|fraud|corrupt\w*|irregular\w*|kickbacks?|contracts?|R\d+(\.\d+)?\s?(m|bn|million|billion))\b", re.I)),
    ("arrest", re.compile(r"\b(arrest(ed|s)?|nabbed|caught|apprehended|in court|appear(s|ed)? in court|charged|sentenced|convicted)\b", re.I)),
    ("project", re.compile(r"\b(hands? over|handed over|sod[- ]turning|launch(es|ed)?|commission(s|ed)?|unveil\w*|budget|tender|contract awarded|drilled)\b", re.I)),
    ("policy", re.compile(r"\b(tariffs?|minister|parliament|portfolio committee|board|results|financial|report says|plan to|strategy|bill)\b", re.I)),
]

_ARREST = dict(KIND_PATTERNS)["arrest"]
_LOADSHED_STATUS = re.compile(r"load[\s-]?shedding", re.I)
_OUTAGE_EVENT = re.compile(r"\b(outages?|blackouts?|without (power|electricity)|power cut|stage \d+ (load[\s-]?shedding )?(from|until|tonight|today|implemented|announced|returns))\b", re.I)

# Words that mark a story as about a place's infrastructure at all; a story
# must match one EVENT_PATTERN in its title, or in its summary when the title
# names a utility word. Bare "municipality" or "Eskom" is not enough.
_UTILITY_WORD = re.compile(r"\b(water|electricity|power|eskom|sewage|road|bridge|cable|copper|substation|transformer|infrastructure)\b", re.I)


def classify(title: str, summary: str = "") -> dict:
    """Return {'is_infra', 'event_type', 'kind', 'reason'} for a story."""
    title = title or ""
    summary = summary or ""
    event_type = None
    where = None
    for et, pat in EVENT_PATTERNS:
        if pat.search(title):
            event_type, where = et, "title"
            break
    if event_type is None and _UTILITY_WORD.search(title):
        for et, pat in EVENT_PATTERNS:
            if pat.search(summary):
                event_type, where = et, "summary"
                break
    if event_type is None:
        return {"is_infra": False, "event_type": None, "kind": None, "reason": "no infrastructure event words"}
    text = f"{title} {summary}"
    # National load-shedding status and commentary ("500 days without load
    # shedding", "batteries to cover 6 stages") is context, not an outage.
    if event_type == "power_outage" and _LOADSHED_STATUS.search(title) and not _OUTAGE_EVENT.search(title):
        return {"is_infra": True, "event_type": event_type, "kind": "policy",
                "reason": "load-shedding status or commentary, not an outage event"}
    kind = "incident"
    for k, pat in KIND_PATTERNS:
        if pat.search(title) or (k in ("arrest", "restored", "planned") and pat.search(summary[:300])):
            kind = k
            break
    # A cable-theft arrest or court story is still about the theft.
    if event_type == "cable_theft" and kind in ("project", "policy", "procurement"):
        kind = "arrest" if _ARREST.search(text) else "incident"
    return {"is_infra": True, "event_type": event_type, "kind": kind,
            "reason": f"{event_type} words in {where}"}


def is_event_kind(kind: Optional[str]) -> bool:
    """Incidents, arrests and restorations are events; projects and policy are context."""
    return kind in ("incident", "arrest", "planned", "restored")


# ── Location candidates ──────────────────────────────────────────────────────

# GeoNames still files a few towns under their pre-2006 province. These are in
# Limpopo today (Elias Motsoaledi and Fetakgomo Tubatse local municipalities).
PROVINCE_FIX = {"Groblersdal": "Limpopo", "Burgersfort": "Limpopo", "Marble Hall": "Limpopo",
                "Jane Furse": "Limpopo", "Steelpoort": "Limpopo", "Ohrigstad": "Limpopo",
                # Bushbuckridge moved to Mpumalanga in 2006; GeoNames still says Limpopo.
                "Acornhoek": "Mpumalanga", "Dwarsloop": "Mpumalanga", "Bushbuckridge": "Mpumalanga",
                "Thulamahashe": "Mpumalanga", "Mkhuhlu": "Mpumalanga"}

# Place names that are also ordinary words or common surnames in news copy.
_AMBIGUOUS = {"george", "hope", "union", "victoria", "kimberley", "alice", "elim", "power", "water",
              "bridge", "church", "station", "river", "park", "central", "north", "south", "east",
              "west", "limpopo", "mopani", "waterberg", "capricorn", "vhembe", "sekhukhune", "thaba",
              "lion", "eden", "paradise", "rust", "protest", "service", "delivery", "mama", "mamba"}

DISTRICT_WORDS = {
    "vhembe": "Limpopo", "mopani": "Limpopo", "capricorn": "Limpopo", "waterberg": "Limpopo",
    "sekhukhune": "Limpopo", "limpopo": "Limpopo", "ehlanzeni": "Mpumalanga", "nkangala": "Mpumalanga",
    "gert sibande": "Mpumalanga", "mpumalanga": "Mpumalanga",
    # KwaZulu-Natal (Batch 3, 23 Oct 2026): the province and its district
    # municipalities, so KZN copy can count small places as in-province and
    # the KZN infrastructure run's province filter recognises KZN stories.
    "kwazulu-natal": "KwaZulu-Natal", "kwazulu natal": "KwaZulu-Natal", "kzn": "KwaZulu-Natal",
    "amajuba": "KwaZulu-Natal", "umzinyathi": "KwaZulu-Natal", "uthukela": "KwaZulu-Natal",
    "umgungundlovu": "KwaZulu-Natal", "harry gwala": "KwaZulu-Natal", "ilembe": "KwaZulu-Natal",
    "king cetshwayo": "KwaZulu-Natal", "umkhanyakude": "KwaZulu-Natal", "zululand": "KwaZulu-Natal",
    "ethekwini": "KwaZulu-Natal", "ugu district": "KwaZulu-Natal",
}

# Extra Limpopo names the benchmark needed that GeoNames lacks. Points are the
# nearest GeoNames place and are marked as such; precision stays 'area'.
EXTRA_PLACES = [
    ("Makhado", -23.04306, 29.90472, "Limpopo", "Louis Trichardt (Makhado town) GeoNames point"),
    ("Lulekani", -23.86667, 31.06667, "Limpopo", "near Namakgale; approximate"),
]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


@lru_cache(maxsize=1)
def _places() -> dict:
    """name(lower, folded) -> list of place dicts."""
    idx: dict[str, list] = {}
    try:
        d = json.loads((GAZ_DIR / "za_places.json").read_text())
        f = d["fields"]
        for row in d["rows"]:
            r = dict(zip(f, row))
            r["province"] = PROVINCE_FIX.get(r["name"], r["province"])
            r["source"] = "GeoNames"
            idx.setdefault(_fold(r["name"]), []).append(r)
    except Exception:
        pass
    try:
        t = json.loads((GAZ_DIR / "za_places_topup.json").read_text())
        for p in t.get("places", []):
            for nm in [p["name"]] + list(p.get("aliases", [])):
                idx.setdefault(_fold(nm), []).append({"name": p["name"], "lat": p["lat"], "lng": p["lng"],
                    "population": 0, "province": p.get("province", ""), "feature_code": p.get("kind", ""),
                    "district": "", "municipality": p.get("municipality", ""), "source": p.get("source", "topup")})
    except Exception:
        pass
    # Collector-only top-up (Batch 3): sections and villages the town sweeps
    # named. Not read by tools/publish.py. See the file's 'about'.
    try:
        t = json.loads((GAZ_DIR / "collector_places_topup.json").read_text())
        for p in t.get("places", []):
            for nm in [p["name"]] + list(p.get("aliases", [])):
                idx.setdefault(_fold(nm), []).append({"name": p["name"], "lat": p["lat"], "lng": p["lng"],
                    "population": 0, "province": p.get("province", ""), "feature_code": p.get("kind", ""),
                    "district": "", "municipality": p.get("municipality", ""),
                    "source": p.get("source", "collector top-up"),
                    "context": [_fold(c) for c in p.get("context", [])],
                    "context_required": bool(p.get("context_required")),
                    "parent": p.get("parent", ""), "estimated": bool(p.get("estimated")),
                    "uncertainty_km": p.get("uncertainty_km"), "point_basis": p.get("point_basis", ""),
                    "precinct": p.get("precinct", ""), "precinct_basis": p.get("precinct_basis", "")})
    except Exception:
        pass
    for nm, lat, lng, prov, src in EXTRA_PLACES:
        idx.setdefault(_fold(nm), []).append({"name": nm, "lat": lat, "lng": lng, "population": 0,
            "province": prov, "feature_code": "PPL", "district": "", "municipality": "", "source": src})
    return idx


_CAP_RUN = re.compile(r"\b([A-Z][\w'’\-]+(?:[ \-](?:[A-Z][\w'’\-]+|la|le|ga|ha|wa|of))*)")


def _candidates(text: str) -> list[str]:
    """Capitalised word runs and every 1-3 word sub-run of them."""
    out = []
    for m in _CAP_RUN.finditer(text):
        words = re.split(r"[ ]", m.group(1))
        for n in (3, 2, 1):
            for i in range(0, len(words) - n + 1):
                out.append(" ".join(words[i:i + n]))
    return out


def locate(text: str, province: Optional[str] = None) -> Optional[dict]:
    """Best candidate place named in ``text``, or None.

    With ``province`` (a feed known to cover one province), places in that
    province win and small villages count. Without it, only places in a
    province the text names, or towns with population >= 5000, count, so
    national copy does not land on a same-named hamlet.
    """
    if not text:
        return None
    idx = _places()
    if not idx:
        return None
    folded_text = _fold(text)
    named_provs = {p for w, p in DISTRICT_WORDS.items() if re.search(r"\b" + w + r"\b", folded_text)}
    if province:
        named_provs.add(province)
    best = None
    for cand in _candidates(text):
        key = _fold(cand).strip("'’-")
        if key in _AMBIGUOUS or len(key) < 4:
            continue
        for p in idx.get(key, []):
            # Collector top-up entries (Batch 3) carry context words: the
            # parent town or district. Named context counts as in-province
            # and makes the section beat its parent town ("Matswale, Musina").
            ctx = p.get("context") or []
            ctx_hit = bool(ctx) and any(re.search(r"\b" + re.escape(c) + r"\b", folded_text) for c in ctx)
            if p.get("context_required") and not ctx_hit:
                continue
            in_prov = p["province"] in named_provs or ctx_hit
            pop = p.get("population") or 0
            big = pop >= 5000 or str(p.get("feature_code", "")).startswith(("PPLA", "PPLC"))
            if not in_prov and (named_provs or not big):
                continue
            score = (2 if in_prov else 0) + len(key.split()) + min(pop, 500000) / 500000 + (1 if ctx_hit else 0)
            if best is None or score > best[0]:
                best = (score, p, cand)
    if not best:
        return None
    _, p, cand = best
    out = {"place": p["name"], "matched_text": cand, "province": p["province"],
           "district": p.get("district") or "", "municipality": p.get("municipality") or "",
           "lat": p["lat"], "lng": p["lng"], "gazetteer": p.get("source", ""),
           "precision": "town_centroid_estimate",
           "note": "Candidate from the story text; not a confirmed event point. Lat/lng are the place centroid."}
    if p.get("parent"):
        out["within"] = p["parent"]
    if p.get("precinct"):
        out["precinct"] = p["precinct"]
        out["precinct_basis"] = p.get("precinct_basis") or "collector top-up"
    if p.get("estimated"):
        out["point_estimated"] = True
        out["uncertainty_km"] = p.get("uncertainty_km")
        out["note"] = ("Candidate from the story text; not a confirmed event point. The point is an estimate: "
                       + (p.get("point_basis") or "nearest sourced point"))
    return out


def province_mentioned(text: str, province: str) -> bool:
    """True if the text names the province, one of its districts, or a place in it."""
    folded = _fold(text or "")
    if any(p == province and re.search(r"\b" + w + r"\b", folded) for w, p in DISTRICT_WORDS.items()):
        return True
    loc = locate(text, province=province)
    return bool(loc and loc["province"] == province)


# ── Dates, event keys, supporting evidence ───────────────────────────────────

def parse_ts(ts: str) -> Optional[datetime]:
    """Read the timestamp shapes the collectors write (ISO with Z or offset,
    'YYYY-MM-DD HH:MM:SS', bare date). Naive values are UTC."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso_utc(dt: datetime) -> str:
    """The signals table's dominant timestamp format: 2026-10-15T09:23:33Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_window(ts: str, days: Optional[int], now: Optional[datetime] = None) -> bool:
    if not days:
        return True
    d = parse_ts(ts)
    if d is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=days) <= d <= now + timedelta(days=1)


THEFT_FAMILY = {"cable_theft": "power_network", "substation_transformer": "power_network",
                "power_outage": "power_network", "water_failure": "water", "sewage": "water",
                "road_bridge": "roads"}


def event_key(event_type: str, loc: Optional[dict]) -> Optional[str]:
    if not event_type or not loc:
        return None
    return f"{THEFT_FAMILY.get(event_type, event_type)}|{_fold(loc['place'])}|{_fold(loc['province'])}"


def infra_metadata(title: str, summary: str, province: Optional[str] = None) -> tuple[bool, dict]:
    """Classification plus location candidate, as metadata fields."""
    c = classify(title, summary)
    if not c["is_infra"]:
        return False, {}
    loc = locate(f"{title}. {summary}", province=province)
    meta = {"infra_event_type": c["event_type"], "infra_kind": c["kind"], "stream_reason": c["reason"],
            "infra_role": ("arrest_only" if c["kind"] == "arrest"
                           else "event" if is_event_kind(c["kind"]) else "context")}
    if loc:
        meta["location_candidate"] = loc
        meta["event_key"] = event_key(c["event_type"], loc)
    return True, meta


def link_supporting(conn: sqlite3.Connection, meta: dict, ts: str, days: int = 21) -> dict:
    """If an earlier live INFRASTRUCTURE signal has the same event_key, mark
    this one as supporting evidence for it (an arrest, a follow-up, a second
    outlet), so review sees one incident with its evidence, not two."""
    key = meta.get("event_key")
    if not key or meta.get("infra_role") == "context":
        return meta
    d = parse_ts(ts) or datetime.now(timezone.utc)
    # An arrest or restoration links back up to `days`; a second report of an
    # incident links only within 3 days and only for the same event type, so
    # two separate pipe bursts in one town a week apart stay two events.
    follow_up = meta.get("infra_kind") in ("arrest", "restored")
    window = days if follow_up else 3
    try:
        rows = conn.execute(
            "SELECT signal_id, metadata_json, timestamp FROM signals WHERE source_type='live' "
            "AND stream='INFRASTRUCTURE' AND metadata_json LIKE ? ORDER BY timestamp",
            (f'%"event_key": "{key}"%',)).fetchall()
    except sqlite3.Error:
        return meta
    for sid, mj, rts in rows:
        rd = parse_ts(rts) or d
        if not (d - timedelta(days=window) <= rd <= d + timedelta(days=1)):
            continue
        try:
            other = json.loads(mj or "{}")
        except ValueError:
            continue
        if not follow_up and other.get("infra_event_type") != meta.get("infra_event_type"):
            continue
        if other.get("infra_role") == "event":
            meta["infra_role"] = "supporting_evidence"
            meta["supports_signal_id"] = other.get("supports_signal_id") or sid
            break
    return meta
