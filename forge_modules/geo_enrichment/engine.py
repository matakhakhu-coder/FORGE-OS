"""
geo_enrichment — Engine  (v1.0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Maps signal coordinates to South African geographic context.

Produces an AnalysisResult with:
    - Province and major city/municipality as entities
    - Location type classification
    - Gravity boost based on strategic location tier
    - Rich provenance for map and targeting systems

Returns None if:
    - Signal has no coordinates
    - Coordinates are outside South Africa
    - Coordinates are 0,0 (null island — bad data)

Follows FORGE Pipeline Contracts:
    - One public function: run(signal) -> AnalysisResult | None
    - No DB access
    - No imports from other forge_modules
    - All helpers prefixed with _
"""

from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
from core.conclave.registry import AnalysisResult


# ── SA bounding box ───────────────────────────────────────────────────────────
# Signals outside this box are not South African — return None immediately

SA_BOUNDS = {
    "lat_min": -35.0, "lat_max": -22.0,
    "lng_min":  16.0, "lng_max":  33.0,
}

# ── Province definitions ──────────────────────────────────────────────────────
# Simplified bounding boxes — accurate enough for intelligence triage
# Order matters: more specific provinces checked first

PROVINCES = [
    {
        "name":    "Gauteng",
        "capital": "Pretoria",
        "lat":     (-26.7, -25.2),
        "lng":     (27.3,  29.0),
    },
    {
        "name":    "Western Cape",
        "capital": "Cape Town",
        "lat":     (-34.5, -31.0),
        "lng":     (17.8,  23.0),
    },
    {
        "name":    "KwaZulu-Natal",
        "capital": "Pietermaritzburg",
        "lat":     (-31.0, -26.8),
        "lng":     (28.5,  32.9),
    },
    {
        "name":    "Eastern Cape",
        "capital": "Bhisho",
        "lat":     (-34.0, -30.0),
        "lng":     (24.0,  30.0),
    },
    {
        "name":    "Limpopo",
        "capital": "Polokwane",
        "lat":     (-25.2, -22.0),
        "lng":     (26.0,  31.5),
    },
    {
        "name":    "Mpumalanga",
        "capital": "Mbombela",
        "lat":     (-27.0, -24.0),
        "lng":     (29.0,  32.0),
    },
    {
        "name":    "North West",
        "capital": "Mahikeng",
        "lat":     (-27.8, -24.5),
        "lng":     (22.5,  27.8),
    },
    {
        "name":    "Free State",
        "capital": "Bloemfontein",
        "lat":     (-30.7, -26.5),
        "lng":     (24.0,  29.5),
    },
    {
        "name":    "Northern Cape",
        "capital": "Kimberley",
        "lat":     (-32.0, -26.0),
        "lng":     (16.0,  25.0),
    },
]

# ── Strategic locations ───────────────────────────────────────────────────────
# (lat, lng, radius_deg, name, tier)
# tier: "major_city" | "provincial_capital" | "strategic_site"

STRATEGIC_LOCATIONS = [
    # Major cities
    (-26.2041, 28.0473, 0.5,  "Johannesburg",     "major_city"),
    (-25.7479, 28.2293, 0.5,  "Pretoria",          "major_city"),
    (-33.9249, 18.4241, 0.5,  "Cape Town",         "major_city"),
    (-29.8587, 31.0218, 0.5,  "Durban",            "major_city"),
    (-33.9608, 25.6022, 0.4,  "Port Elizabeth",    "major_city"),
    (-26.3054, 27.8546, 0.3,  "Soweto",            "major_city"),
    # Provincial capitals (not already listed)
    (-29.1179, 26.2140, 0.3,  "Bloemfontein",      "provincial_capital"),
    (-25.4658, 30.9856, 0.3,  "Mbombela",          "provincial_capital"),
    (-23.9045, 29.4689, 0.3,  "Polokwane",         "provincial_capital"),
    (-25.8553, 25.6450, 0.3,  "Mahikeng",          "provincial_capital"),
    (-28.7282, 24.7499, 0.3,  "Kimberley",         "provincial_capital"),
    (-29.6006, 30.3794, 0.3,  "Pietermaritzburg",  "provincial_capital"),
    # Strategic sites
    (-26.4042, 27.4832, 0.4,  "Ekurhuleni",        "strategic_site"),
    (-33.0292, 27.8546, 0.3,  "East London",       "strategic_site"),
    (-28.4478, 29.9835, 0.4,  "Newcastle",         "strategic_site"),
]

# ── Gravity contribution by tier ──────────────────────────────────────────────

TIER_GRAVITY = {
    "major_city":         0.20,
    "provincial_capital": 0.12,
    "strategic_site":     0.08,
    "province_only":      0.04,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _in_sa(lat: float, lng: float) -> bool:
    return (
        SA_BOUNDS["lat_min"] <= lat <= SA_BOUNDS["lat_max"] and
        SA_BOUNDS["lng_min"] <= lng <= SA_BOUNDS["lng_max"]
    )


def _match_province(lat: float, lng: float) -> Optional[Dict[str, str]]:
    for p in PROVINCES:
        if p["lat"][0] <= lat <= p["lat"][1] and p["lng"][0] <= lng <= p["lng"][1]:
            return {"name": p["name"], "capital": p["capital"]}
    return None


def _match_strategic(lat: float, lng: float) -> Optional[Tuple[str, str]]:
    """Return (location_name, tier) for the nearest strategic location."""
    best_dist = float("inf")
    best      = None
    for slat, slng, radius, name, tier in STRATEGIC_LOCATIONS:
        dist = ((lat - slat) ** 2 + (lng - slng) ** 2) ** 0.5
        if dist <= radius and dist < best_dist:
            best_dist = dist
            best      = (name, tier)
    return best


def _extract_coords(signal: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        lat = float(signal.get("lat") or 0)
        lng = float(signal.get("lng") or 0)
        if lat == 0.0 and lng == 0.0:
            return None
        return lat, lng
    except (TypeError, ValueError):
        return None


# ── Public engine function ────────────────────────────────────────────────────

def run(signal: Dict[str, Any]) -> Optional[AnalysisResult]:
    """
    Produce a geo-enriched AnalysisResult for signals with SA coordinates.
    Returns None if signal has no coords or is outside South Africa.
    """
    coords = _extract_coords(signal)
    if coords is None:
        return None

    lat, lng = coords

    if not _in_sa(lat, lng):
        return None

    entities     = []
    entity_types = {}
    provenance   = {
        "module":      "geo_enrichment",
        "engine":      "geo_enrichment_engine",
        "coordinates": {"lat": lat, "lng": lng},
    }

    # ── Province match ────────────────────────────────────────────────────────
    province = _match_province(lat, lng)
    tier     = "province_only"

    if province:
        entities.append(province["name"])
        entity_types[province["name"]] = "location"
        provenance["province"] = province["name"]
        provenance["capital"]  = province["capital"]

    # ── Strategic location match ──────────────────────────────────────────────
    strategic = _match_strategic(lat, lng)
    if strategic:
        loc_name, tier = strategic
        if loc_name not in entities:
            entities.append(loc_name)
            entity_types[loc_name] = "location"
        provenance["strategic_location"] = loc_name
        provenance["location_tier"]      = tier
    else:
        provenance["location_tier"] = tier

    if not entities:
        return None  # In SA bounds but no province matched

    # ── Gravity ───────────────────────────────────────────────────────────────
    gravity_boost = TIER_GRAVITY.get(tier, 0.04)

    # Stream modifier — crime intel in major cities is more significant
    stream = str(signal.get("stream", "")).upper()
    if stream == "CRIME_INTEL" and tier == "major_city":
        gravity_boost = min(gravity_boost + 0.05, 1.0)

    gravity = round(min(gravity_boost, 1.0), 4)

    if gravity >= 0.55:
        recommendation = "ESCALATE"
    elif gravity >= 0.35:
        recommendation = "MONITOR"
    else:
        recommendation = "IGNORE"

    # Confidence: location data is either right or wrong — high when matched
    confidence = round(0.6 + (0.2 if strategic else 0.0) + (0.1 if province else 0.0), 3)

    provenance["entity_types"] = entity_types

    return AnalysisResult(
        entities=entities,
        intent="geo_enrichment",
        gravity=gravity,
        recommendation=recommendation,
        confidence=confidence,
        provenance=provenance,
    )