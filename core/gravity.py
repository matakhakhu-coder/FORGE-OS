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

    # Keywords from case metadata
    case_row = db.execute(
        "SELECT name, description, hypothesis FROM cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    keywords: set = set()
    if case_row:
        keywords |= _extract_keywords(case_row["name"]        or "")
        keywords |= _extract_keywords(case_row["description"] or "")
        keywords |= _extract_keywords(case_row["hypothesis"]  or "")

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
