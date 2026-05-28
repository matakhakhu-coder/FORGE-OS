from __future__ import annotations
"""
core/gravity.py — CT-1: Contextual Tunneling gravity scorer.

Computes a gravity_score ∈ [0.0, 1.0] measuring how relevant a feed item
is to an analyst's active case context (actors, locations, keywords).

Component weights
─────────────────
  actor_match    × 0.50  — signal/lead linked to a case actor
  location_match × 0.30  — signal lat/lng within RADIUS_KM of a case anchor
  keyword_match  × 0.20  — text overlaps with case topic keywords

Usage
─────
  ctx = build_context(db, case_id)
  for item in feed_items:
      item['gravity_score'] = score_item(item, ctx)
  # then blend:
  item['final_score'] = blend_score(item['feed_score'], item['gravity_score'], gw)
"""

import json
import math
import re

# ── Constants ─────────────────────────────────────────────────────────────────

RADIUS_KM = 250  # location anchor radius — covers a South African province

_STOPWORDS = frozenset([
    "the", "and", "for", "this", "that", "with", "from", "have", "been",
    "will", "are", "was", "were", "has", "had", "not", "but", "all", "its",
    "into", "they", "their", "them", "also", "more", "when", "where", "which",
    "while", "there", "then", "than", "about", "after", "before", "should",
    "could", "would", "other", "case", "cases", "report", "reports",
    "investigation", "investigations", "signal", "signals", "alert", "alerts",
    "related", "involving", "according", "between", "during", "against",
])


# ── Outbreak GAZETTEER ────────────────────────────────────────────────────────
# Warm Start: location names → (lat, lng) for auto-seeding context_anchors.
# Covers primary disease surveillance zones: SE Asia H5N1 belt, African VHF/mpox
# corridor, South Asian cholera basin, MENA conflict zones, Latin America arboviral.

_OUTBREAK_LOCATIONS: dict[str, tuple[float, float]] = {
    # Southeast Asia — H5N1 / avian influenza primary zone
    "vietnam":          ( 14.058,  108.278),
    "hanoi":            ( 21.028,  105.854),
    "ho chi minh":      ( 10.823,  106.630),
    "cambodia":         ( 12.566,  104.991),
    "phnom penh":       ( 11.562,  104.916),
    "laos":             ( 17.958,  102.620),
    "vientiane":        ( 17.967,  102.600),
    "thailand":         ( 15.870,  100.993),
    "bangkok":          ( 13.756,  100.502),
    "myanmar":          ( 21.916,   95.956),
    "yangon":           ( 16.866,   96.195),
    "indonesia":        ( -0.789,  113.921),
    "jakarta":          ( -6.208,  106.846),
    "philippines":      ( 12.880,  121.774),
    "manila":           ( 14.599,  120.984),
    "malaysia":         (  4.211,  101.976),
    "kuala lumpur":     (  3.149,  101.696),
    # East Asia
    "china":            ( 35.861,  104.195),
    "beijing":          ( 39.904,  116.407),
    "guangdong":        ( 23.380,  113.760),
    "hong kong":        ( 22.319,  114.169),
    "taiwan":           ( 23.698,  120.961),
    "japan":            ( 36.205,  138.252),
    "south korea":      ( 36.638,  127.979),
    "mongolia":         ( 46.863,  103.847),
    # South Asia — cholera / nipah / dengue basin
    "india":            ( 20.594,   78.963),
    "mumbai":           ( 19.076,   72.878),
    "delhi":            ( 28.704,   77.102),
    "kerala":           ( 10.850,   76.271),
    "bangladesh":       ( 23.685,   90.357),
    "dhaka":            ( 23.810,   90.413),
    "pakistan":         ( 30.376,   69.344),
    "afghanistan":      ( 33.939,   67.710),
    "nepal":            ( 28.394,   84.124),
    # Sub-Saharan Africa — VHF / mpox / cholera / meningitis corridor
    "drc":              ( -4.038,   21.759),
    "democratic republic":(-4.038,  21.759),
    "congo":            ( -0.228,   15.827),
    "kinshasa":         ( -4.325,   15.322),
    "uganda":           (  1.373,   32.290),
    "kampala":          (  0.347,   32.582),
    "kenya":            ( -0.023,   37.906),
    "nairobi":          ( -1.286,   36.818),
    "ethiopia":         (  9.145,   40.489),
    "sudan":            ( 12.863,   30.218),
    "south sudan":      (  7.963,   31.617),
    "nigeria":          (  9.082,    8.675),
    "lagos":            (  6.524,    3.379),
    "ghana":            (  7.946,   -1.023),
    "cameroon":         (  3.848,   11.502),
    "niger":            ( 17.607,    8.082),
    "mali":             ( 17.570,   -3.996),
    "guinea":           ( 11.787,  -15.180),
    "sierra leone":     (  8.460,  -11.780),
    "liberia":          (  6.428,   -9.430),
    "ivory coast":      (  7.540,   -5.547),
    "senegal":          ( 14.497,  -14.452),
    "zimbabwe":         (-19.015,   29.155),
    "zambia":           (-13.133,   27.849),
    "mozambique":       (-18.665,   35.530),
    # Middle East / MENA
    "yemen":            ( 15.552,   48.516),
    "syria":            ( 34.802,   38.997),
    "iraq":             ( 33.224,   43.679),
    "iran":             ( 32.427,   53.688),
    "lebanon":          ( 33.854,   35.862),
    "jordan":           ( 30.586,   36.239),
    "saudi arabia":     ( 23.886,   45.079),
    # Central Asia
    "kazakhstan":       ( 48.019,   66.924),
    "kyrgyzstan":       ( 41.204,   74.766),
    "uzbekistan":       ( 41.377,   64.585),
    "tajikistan":       ( 38.861,   71.276),
    # Eastern Europe
    "ukraine":          ( 48.379,   31.165),
    "moldova":          ( 47.412,   28.370),
    # Latin America — arboviral / cholera / Chagas zone
    "brazil":           (-14.235,  -51.925),
    "amazon":           ( -3.465,  -62.216),
    "colombia":         (  4.571,  -74.297),
    "venezuela":        (  6.424,  -66.590),
    "peru":             ( -9.190,  -75.015),
    "bolivia":          (-16.290,  -63.589),
    "haiti":            ( 18.972,  -72.285),
    "mexico":           ( 23.634, -102.553),
    # Pacific
    "papua new guinea": ( -6.315,  143.956),
}


def extract_location_anchors(text: str) -> list:
    """
    Scan free text for known outbreak-surveillance location names.
    Returns a list of {"lat": float, "lng": float, "label": str} dicts
    in order of first match; deduplicates by label.
    """
    if not text:
        return []
    lower = text.lower()
    seen: set = set()
    results = []
    for name, (lat, lng) in _OUTBREAK_LOCATIONS.items():
        if name in lower and name not in seen:
            seen.add(name)
            results.append({"lat": lat, "lng": lng, "label": name})
    return results


# ── Keyword helpers ───────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> set:
    if not text:
        return set()
    tokens = re.findall(r'[a-z]{4,}', text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


# ── Location helpers ──────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return 2.0 * R * math.asin(math.sqrt(min(a, 1.0)))


def _location_match(lat, lng, anchors: list, radius_km: float = RADIUS_KM) -> float:
    """Return 1.0 if (lat, lng) is within radius_km of any anchor, else 0.0."""
    if lat is None or lng is None or not anchors:
        return 0.0
    try:
        flat, flng = float(lat), float(lng)
    except (TypeError, ValueError):
        return 0.0
    for (alat, alng) in anchors:
        if _haversine_km(flat, flng, alat, alng) <= radius_km:
            return 1.0
    return 0.0


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(db, case_id: int) -> dict:
    """
    Extract anchors from a case for gravity scoring.

    Returns
    ───────
    {
      "case_id":           int,
      "actor_ids":         set[int]  — actors pinned to this case,
      "signal_ids_linked": set[str]  — signals linked to case actors,
      "locations":         list[(lat, lng)]  — from pinned signals with coords,
      "keywords":          set[str]  — from case metadata + pinned signal titles,
    }
    """
    # Actors pinned to this case
    actor_rows = db.execute(
        "SELECT actor_id FROM case_actors WHERE case_id = ?", (case_id,)
    ).fetchall()
    actor_ids = {r["actor_id"] for r in actor_rows}

    # Signals linked to case actors (via signal_actors join)
    signal_ids_linked: set = set()
    if actor_ids:
        placeholders = ",".join("?" * len(actor_ids))
        sig_rows = db.execute(
            f"SELECT DISTINCT signal_id FROM signal_actors "
            f"WHERE actor_id IN ({placeholders})",
            list(actor_ids),
        ).fetchall()
        signal_ids_linked = {r["signal_id"] for r in sig_rows}

    # Location anchors from signals pinned to the case
    loc_rows = db.execute("""
        SELECT s.lat, s.lng
        FROM   case_signals cs
        JOIN   signals s ON s.signal_id = cs.signal_id
        WHERE  cs.case_id = ?
          AND  s.lat IS NOT NULL
          AND  s.lng IS NOT NULL
    """, (case_id,)).fetchall()
    locations = [(float(r["lat"]), float(r["lng"])) for r in loc_rows]

    # Keywords from case metadata + Warm Start bootstrap from context_anchors
    case_row = db.execute(
        "SELECT name, description, hypothesis, context_anchors FROM cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    keywords: set = set()
    if case_row:
        keywords |= _extract_keywords(case_row["name"]        or "")
        keywords |= _extract_keywords(case_row["description"] or "")
        keywords |= _extract_keywords(case_row["hypothesis"]  or "")
        # Warm Start: if no location anchors yet from pinned signals, seed from
        # context_anchors (GAZETTEER coords stored at case-creation time).
        if not locations:
            raw_anchors = case_row["context_anchors"]
            if raw_anchors:
                try:
                    for anchor in json.loads(raw_anchors):
                        locations.append((float(anchor["lat"]), float(anchor["lng"])))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass

    # Enrich keywords from pinned signal titles (first 30)
    sig_title_rows = db.execute("""
        SELECT s.title
        FROM   case_signals cs
        JOIN   signals s ON s.signal_id = cs.signal_id
        WHERE  cs.case_id = ?
        LIMIT  30
    """, (case_id,)).fetchall()
    for row in sig_title_rows:
        keywords |= _extract_keywords(row["title"] or "")

    return {
        "case_id":           case_id,
        "actor_ids":         actor_ids,
        "signal_ids_linked": signal_ids_linked,
        "locations":         locations,
        "keywords":          keywords,
    }


# ── Item scorer ───────────────────────────────────────────────────────────────

def score_item(item: dict, context: dict) -> float:
    """
    Score a single feed item against the case context.
    Returns gravity_score ∈ [0.0, 1.0].
    """
    if not context:
        return 0.0

    actor_ids  = context.get("actor_ids", set())
    sig_linked = context.get("signal_ids_linked", set())
    locations  = context.get("locations", [])
    keywords   = context.get("keywords", set())

    # Short-circuit: empty context produces no gravity
    if not actor_ids and not locations and not keywords:
        return 0.0

    kind = item.get("item_type", "")
    actor_m    = 0.0
    location_m = 0.0
    keyword_m  = 0.0

    if kind == "SIGNAL":
        sig_id = item.get("signal_id")
        if sig_id and sig_id in sig_linked:
            actor_m = 1.0
        location_m = _location_match(item.get("lat"), item.get("lng"), locations)
        if keywords:
            text = (item.get("title") or "") + " " + (item.get("summary") or "")
            item_kw = _extract_keywords(text)
            if item_kw:
                keyword_m = min(1.0, len(item_kw & keywords) /
                                max(1, min(len(keywords), len(item_kw))))

    elif kind == "SENTINEL_ALERT":
        location_m = _location_match(
            item.get("location_lat"), item.get("location_lon"), locations
        )
        if keywords:
            text = (item.get("title") or "") + " " + (item.get("summary") or "")
            item_kw = _extract_keywords(text)
            if item_kw:
                keyword_m = min(1.0, len(item_kw & keywords) /
                                max(1, min(len(keywords), len(item_kw))))

    elif kind == "CORRELATION":
        if keywords:
            text = ((item.get("title_a") or "") + " " +
                    (item.get("title_b") or "") + " " +
                    (item.get("title")   or ""))
            item_kw = _extract_keywords(text)
            if item_kw:
                keyword_m = min(1.0, len(item_kw & keywords) /
                                max(1, min(len(keywords), len(item_kw))))

    elif kind == "INTELLIGENCE_LEAD":
        actor_id = item.get("actor_id")
        if actor_id and actor_id in actor_ids:
            actor_m = 1.0
        if keywords:
            item_kw = _extract_keywords(item.get("actor_name") or "")
            if item_kw:
                keyword_m = min(1.0, len(item_kw & keywords) /
                                max(1, min(len(keywords), len(item_kw))))

    gravity = (actor_m * 0.50) + (location_m * 0.30) + (keyword_m * 0.20)
    return round(min(1.0, gravity), 4)


# ── Score blender ─────────────────────────────────────────────────────────────

def blend_score(feed_score: float, gravity_score: float,
                gravity_weight: float) -> float:
    """
    Blend feed_score and gravity_score.

      gravity_weight = 0.0 → pure Phase 29.3 ranking (no case bias)
      gravity_weight = 1.0 → pure gravity ranking (case-relevant only)
    """
    gw = max(0.0, min(1.0, float(gravity_weight)))
    return round((1.0 - gw) * float(feed_score) + gw * float(gravity_score), 4)
