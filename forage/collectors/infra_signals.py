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
    for nm, lat, lng, prov, src in EXTRA_PLACES:
        idx.setdefault(_fold(nm), []).append({"name": nm, "lat": lat, "lng": lng, "population": 0,
            "province": prov, "feature_code": "PPL", "district": "", "municipality": "", "source": src})
    return idx


_CAP_RUN = re.compile(r"\b([A-Z][\w'’\-]+(?:[ \-](?:[A-Z][\w'’\-]+|la|le|ga|ha|wa|of))*)")


# ── Batch 4: look-alike names and foreign stories ───────────────────────────
# South African places that share a name with a well-known foreign place.
# Google News returns Newcastle (Australia, England), Belfast (Northern
# Ireland), Charlestown (NSW, Rhode Island) and so on for these searches. A
# namesake only counts as the South African place when the story itself shows
# it is South African (sa_sign_for). Found by checking the gazetteer for SA
# places named after foreign ones; the first four were seen in the 23 Oct
# sweeps.
NAMESAKES = {
    "newcastle", "belfast", "charlestown", "dundee", "ladysmith", "richmond", "middelburg",
    "wellington", "aberdeen", "bethlehem", "bedford", "heidelberg", "frankfort", "utrecht",
    "ermelo", "balfour", "stanford", "darling", "queenstown", "devon", "adelaide", "napier",
    "greytown", "howick", "margate", "ramsgate", "hamburg", "london", "windsor", "cambridge",
    "berlin", "amsterdam", "kingston", "york", "perth", "hamilton", "stockton", "salem",
    "sydney", "boston", "jamestown", "springfield", "clarens", "westville",
}

# Signs that a namesake story is foreign. Kept to place and institution words
# that do not occur in South African copy about these towns.
_NAMESAKE_FOREIGN = re.compile(
    r"(\b(nsw|new south wales|australia\w*|queensland|tasmania|victoria police|hunter (region|valley|new england)|"
    r"lake macquarie|maitland|wallsend|knights|nrl|nbl|a-league|tyne\w*|northumb\w*|gateshead|sunderland|"
    r"premier league|newcastle united|magpies|toon|england|english|uk|britain|british|scotland|scottish|"
    r"wales|welsh|ireland|irish|ulster|stormont|psni|garda\w*|sinn f[eé]in|loyalist\w*|uda|ira|nhs|"
    r"council tax|ontario|canada|canadian|usa|u\.s\.|united states|rhode island|new zealand|county|"
    r"germany|german|netherlands|dutch)\b|[£$€])", re.I)

# Signs that cannot come from a foreign story about a namesake town.
_SA_HARD = re.compile(
    r"\b(south africa\w*|mzansi|kwazulu|kzn|natal|gauteng|limpopo|mpumalanga|free state|eastern cape|"
    r"western cape|northern cape|north west province|saps|sandf|eskom|amajuba|umzinyathi|uthukela|"
    r"nkangala|gert sibande|ehlanzeni)\b", re.I)
# Softer SA words: also used abroad (Ontario has municipalities), so a foreign
# sign outweighs them.
_SA_SOFT_LOCAL = re.compile(r"\b(municipality|municipal|metro police|rti|ward councillor)\b", re.I)
_RAND = re.compile(r"\bR ?\d[\d.,]* ?(million|billion|m|bn|k)\b|\bR ?\d{1,3}(?:[ ,]\d{3})+\b", re.I)

# Local South African outlets the national outlet list in pi_signals lacks.
_SA_LOCAL_OUTLET = re.compile(
    r"\b(Newcastillian|Northern Natal|LNN|Central News South Africa|thenational\.co\.za|Ladysmith Gazette|"
    r"Vryheid Herald|Highway Mail|Zululand Observer|South Coast Herald|Ridge Times|Middelburg Observer|"
    r"Bethlehem Express|Express News)\b", re.I)

# Outlets that cover one foreign city. A story from one of them is never South
# African, whatever place names it carries. Google News puts the outlet after
# the last " - " in the title and in source_name.
FOREIGN_LOCAL_OUTLETS = re.compile(
    r"^(Newcastle Herald|Newcastle Weekly|Newcastle Star|NBN News|ABC (News )?\(?Australia\)?|ABC iview|"
    r"Chronicle Live|ChronicleLive|The Northern Echo|Northern Echo|Belfast Telegraph|Belfast Live|"
    r"The Irish News|Irish News|BelfastLive|Transport for NSW|Nine\.com\.au|SMH\.com\.au|"
    r"The Sydney Morning Herald|The Nightly|WAtoday|Durham Radio News|Durham Post|City of Newcastle|"
    r"Newcastle City Council|The Mag|The Newcastle Pacer|(Greatest )?Hits Radio.*|The Chronicle Journal)$",
    re.I)

# Border towns whose name covers both sides of the border. A story about the
# far side is not placed in South Africa. Beitbridge is not in the gazetteer
# today, so this guard is dormant until a border place is added.
BORDER_TWINS = {
    "beitbridge": (
        re.compile(r"\b(zimbabwe\w*|zrp|zimra|masvingo|matabeleland|dulivhadzimu|beitbridge (town|municipality|rural|district hospital)|"
                   r"beitbridge, zimbabwe)\b", re.I),
        re.compile(r"\b(musina|limpopo|saps|sandf|sars|border management authority|bma|n1|south african side)\b", re.I),
    ),
}


def outlet_of(text: str, source_name: str = "") -> str:
    """The outlet name: source_name, else the Google News title suffix."""
    if source_name:
        return source_name.strip()
    if " - " in (text or ""):
        return text.split(". ", 1)[0].rsplit(" - ", 1)[-1].strip()
    return ""


def foreign_outlet(text: str = "", source_name: str = "") -> bool:
    """True when the outlet covers one foreign city (Newcastle Herald...)."""
    out = outlet_of(text, source_name)
    return bool(out and FOREIGN_LOCAL_OUTLETS.match(out))


def _km(a: dict, b: dict) -> float:
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def _near_sa_place(text: str, p: dict, skip: str, km: float = 60) -> bool:
    """True when the text names another gazetteer place near ``p`` (a
    suburb or neighbouring town), other than look-alike names."""
    idx = _places()
    for cand in _candidates(text):
        key = _fold(cand).strip("'’-")
        if key == skip or key in NAMESAKES or key in _AMBIGUOUS or len(key) < 4:
            continue
        for q in idx.get(key, []):
            if q is not p and _km(p, q) <= km:
                return True
    return False


def sa_sign_for(text: str, p: dict, key: str = "") -> bool:
    """True when a story naming the look-alike place ``p`` shows it is about
    the South African one: the province or another SA marker, rands, a
    neighbouring SA place, or a South African outlet, and no foreign sign
    outweighs a soft marker."""
    if not text:
        return False
    if foreign_outlet(text):
        return False
    hard = bool(_SA_HARD.search(text) or _RAND.search(text)
                or _near_sa_place(text, p, key or _fold(p.get("name", ""))))
    if hard:
        return True
    from forage.collectors import pi_signals as _pi  # lazy: pi_signals imports this module
    soft = bool(_pi._SA.search(text) or _pi._SA_PLACES.search(text)
                or _pi._SA_OUTLET.search(outlet_of(text) or "") or _SA_LOCAL_OUTLET.search(text)
            or _SA_SOFT_LOCAL.search(text))
    foreign = bool(_NAMESAKE_FOREIGN.search(text) or _pi._FOREIGN.search(text))
    return soft and not foreign


def _border_far_side(text: str, key: str) -> bool:
    twin = BORDER_TWINS.get(key)
    if not twin:
        return False
    far, near = twin
    return bool(far.search(text) and not near.search(text))


def _gaz_place_for(key: str) -> Optional[dict]:
    """The biggest gazetteer place with this folded name."""
    ps = _places().get(key) or []
    return max(ps, key=lambda p: p.get("population") or 0) if ps else None


def query_town(query: str) -> Optional[dict]:
    """The town a town keyword search is about ("Musina protest" -> Musina).
    The user named the town, so look-alike names are taken as the SA place."""
    for cand in _candidates(query or ""):
        key = _fold(cand).strip("'’-")
        if key in _AMBIGUOUS or len(key) < 4:
            continue
        p = _gaz_place_for(key)
        if p:
            return {**p, "key": key}
    return None


def _names_key(text: str, key: str) -> bool:
    return bool(re.search(r"\b" + re.escape(key) + r"\b", _fold(text or "")))


def names_town(text: str, town: dict, km: float = 35) -> bool:
    """True when the text names the town or a gazetteer place within ``km``
    of it. A look-alike town name also needs a South African sign."""
    key = town["key"]
    if _names_key(text, key) and (key not in NAMESAKES or sa_sign_for(text, town, key)):
        return True
    idx = _places()
    for cand in _candidates(text or ""):
        k = _fold(cand).strip("'’-")
        if k == key or k in _AMBIGUOUS or len(k) < 4 or k in NAMESAKES:
            continue
        if any(_km(town, q) <= km for q in idx.get(k, [])):
            return True
    return False


# Countries far from the border. Border-town stories name Zimbabwe,
# Mozambique, Botswana or Malawi routinely (Carrim's Malawi-bound cars at
# Beitbridge), so neighbours are not here.
_FAR_FOREIGN = re.compile(
    r"\b(tunisia\w*|morocc\w*|egypt\w*|algeria\w*|libya\w*|united states|usa|america\w*|uk|britain|british|"
    r"england|london|mexic\w*|colombia\w*|brazil\w*|india\w*|pakistan\w*|china|chinese|russia\w*|"
    r"ukrain\w*|israel\w*|gaza|iran\w*|europe\w*|nigeria\w*|kenya\w*|ghana\w*|australia\w*|"
    r"canada|canadian|philippines|italy|france|french|germany|spain|ireland|irish|afghan\w*|syria\w*)\b", re.I)


def town_search_off_target(text: str, town: dict, km: float = 35, far_km: float = 80) -> Optional[str]:
    """Why a town keyword search result is about somewhere else, or None.

    Batch 4. A story is dropped only when it points elsewhere: it names the
    look-alike town with no South African sign (Newcastle NSW), it names a
    South African place more than ``far_km`` from the town and nothing near
    it (Durban for "Musina protest"; Levubu, 40 km from Makhado, stays), or its headline names a far-off country and nothing
    South African (Tunisia). A story that names no place is kept: Google News
    headlines often omit the town (the Phiphidi and Schuinshoogte cases)."""
    if names_town(text, town, km):
        return None
    key = town["key"]
    if key in NAMESAKES and _names_key(text, key):
        return "names a foreign place of the same name"
    loc = locate(text)
    if loc and _km(town, loc) > far_km:
        return f"about {loc['place']}"
    from forage.collectors import pi_signals as _pi  # lazy: pi_signals imports this module
    title = (text or "").split(". ", 1)[0]
    strong = bool(_pi._SA.search(text) or _pi._SA_PLACES.search(text) or _RAND.search(text))
    if _FAR_FOREIGN.search(title) and not strong:
        return "about another country"
    return None

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
        if _border_far_side(text, key):
            continue  # Batch 4: the far side of a border town
        for p in idx.get(key, []):
            in_prov = p["province"] in named_provs
            pop = p.get("population") or 0
            big = pop >= 5000 or str(p.get("feature_code", "")).startswith(("PPLA", "PPLC"))
            if not in_prov and (named_provs or not big):
                continue
            if key in NAMESAKES and not sa_sign_for(text, p, key):
                continue  # Batch 4: Newcastle NSW is not Newcastle KZN
            score = (2 if in_prov else 0) + len(key.split()) + min(pop, 500000) / 500000
            if best is None or score > best[0]:
                best = (score, p, cand)
    if not best:
        return None
    _, p, cand = best
    return {"place": p["name"], "matched_text": cand, "province": p["province"],
            "district": p.get("district") or "", "municipality": p.get("municipality") or "",
            "lat": p["lat"], "lng": p["lng"], "gazetteer": p.get("source", ""),
            "precision": "town_centroid_estimate",
            "note": "Candidate from the story text; not a confirmed event point. Lat/lng are the place centroid."}


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
