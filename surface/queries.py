"""
surface/queries.py
══════════════════
Phase 1: reads from existing FORGE tables.
Phase 2: swap each function body to read from intel_objects.

All functions accept a sqlite3.Connection (row_factory=sqlite3.Row already set).
All return plain list[dict] — routes never touch raw Row objects.
"""

from __future__ import annotations


# ── TOP SITUATIONS ────────────────────────────────────────────────────────────
# Source: sentinel_alerts (confidence DESC, limit 3)
# Phase 2: SELECT * FROM intel_objects WHERE visibility='public'
#          ORDER BY consequence_score DESC LIMIT 3

def get_top_situations(db, limit: int = 3) -> list[dict]:
    """
    Top N most significant active situations.
    Drawn from sentinel_alerts ordered by confidence score.
    """
    try:
        rows = db.execute("""
            SELECT
                id,
                alert_type          AS title,
                summary,
                confidence_score    AS confidence,
                location_lat        AS latitude,
                location_lon        AS longitude,
                signal_count,
                created_at
            FROM   sentinel_alerts
            WHERE  status = 'new'
            ORDER  BY confidence_score DESC, created_at DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── INCIDENT FEED ─────────────────────────────────────────────────────────────
# Source: correlated_incidents + sentinel_alerts merged and ranked
# Phase 2: SELECT * FROM intel_objects WHERE object_type IN ('incident','situation')
#          AND visibility='public' ORDER BY consequence_score DESC LIMIT 20

def get_incidents(db, limit: int = 20) -> list[dict]:
    """
    Incident feed — correlated incidents ranked by correlation score,
    enriched with sentinel alerts as a secondary source.
    """
    incidents: list[dict] = []

    # Correlated incident pairs
    try:
        rows = db.execute("""
            SELECT
                ci.id,
                'CORRELATED INCIDENT'           AS title,
                (sa.title || ' ↔ ' || sb.title) AS summary,
                ci.correlation_score            AS confidence,
                ci.distance_km,
                ci.time_difference_hours,
                sa.lat                          AS latitude,
                sa.lng                          AS longitude,
                ci.detected_at                  AS created_at,
                'correlation_engine'            AS source_module
            FROM   correlated_incidents ci
            JOIN   signals sa ON sa.signal_id = ci.signal_a
            JOIN   signals sb ON sb.signal_id = ci.signal_b
            ORDER  BY ci.correlation_score DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        incidents.extend([dict(r) for r in rows])
    except Exception:
        pass

    # Pad with sentinel alerts if feed is thin
    if len(incidents) < limit:
        try:
            rows = db.execute("""
                SELECT
                    id,
                    alert_type              AS title,
                    summary,
                    confidence_score        AS confidence,
                    NULL                    AS distance_km,
                    NULL                    AS time_difference_hours,
                    location_lat            AS latitude,
                    location_lon            AS longitude,
                    created_at,
                    'sentinel'              AS source_module
                FROM   sentinel_alerts
                WHERE  status != 'dismissed'
                ORDER  BY confidence_score DESC, created_at DESC
                LIMIT  ?
            """, (limit - len(incidents),)).fetchall()
            incidents.extend([dict(r) for r in rows])
        except Exception:
            pass

    return incidents[:limit]


# ── MAP INCIDENTS ─────────────────────────────────────────────────────────────
# Source: signals with lat/lng in last 48h
# Phase 2: SELECT * FROM intel_objects WHERE object_type='incident'
#          AND visibility='public' AND latitude IS NOT NULL

def get_map_incidents(db, hours: int = 48, limit: int = 200) -> list[dict]:
    """
    Geolocated incidents for the Leaflet map.
    Returns signals with coordinates from the last N hours.
    """
    try:
        rows = db.execute("""
            SELECT
                signal_id           AS id,
                title,
                content             AS summary,
                source,
                is_priority         AS confidence,
                stream,
                lat                 AS latitude,
                lng                 AS longitude,
                timestamp           AS created_at
            FROM   signals
            WHERE  lat IS NOT NULL
              AND  lng IS NOT NULL
              AND  timestamp >= datetime('now', ?)
            ORDER  BY is_priority DESC, timestamp DESC
            LIMIT  ?
        """, (f"-{hours} hours", limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── SIGNAL STREAM ─────────────────────────────────────────────────────────────
# Source: signals ORDER BY created_at DESC LIMIT 50
# Phase 2: SELECT * FROM intel_objects WHERE object_type='signal'
#          ORDER BY created_at DESC LIMIT 50

def get_signal_stream(db, limit: int = 50) -> list[dict]:
    """
    Chronological signal stream — most recent first.
    """
    try:
        rows = db.execute("""
            SELECT
                signal_id           AS id,
                title,
                content             AS summary,
                source,
                is_priority,
                status,
                stream,
                relevance_score,
                lat                 AS latitude,
                lng                 AS longitude,
                timestamp           AS created_at
            FROM   signals
            ORDER  BY timestamp DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        import logging
        logging.getLogger("forge.surface").error(f"get_signal_stream error: {e}")
        return []


# ── SOCINT DOSSIER ────────────────────────────────────────────────────────────
# Source: actors.socint_profile (JSON), socint_resonance

def get_actor_socint_profile(db, actor_id: int) -> dict:
    """
    SOCINT corpus statistics for one actor.

    Reads actors.socint_profile (JSON) and returns corpus health metrics.
    Returns an empty dict when no SOCINT data exists for this actor.

    Returned keys
    -------------
    corpus_ready     : bool   — gate passed (>= 7 samples, >= 2000 chars)
    sample_count     : int    — number of tweet samples in rolling corpus
    total_chars      : int    — total character count across all samples
    x_handles        : list   — X handles observed for this actor
    x_display_names  : list   — display names observed for this actor
    """
    import json
    try:
        row = db.execute(
            "SELECT socint_profile FROM actors WHERE actor_id = ?",
            (actor_id,),
        ).fetchone()
    except Exception:
        return {}

    if not row or not row["socint_profile"]:
        return {}

    try:
        profile = json.loads(row["socint_profile"])
    except Exception:
        return {}

    corpus      = profile.get("corpus", [])
    total_chars = sum(len(s) for s in corpus)

    return {
        "corpus_ready":     len(corpus) >= 7 and total_chars >= 2000,
        "sample_count":     len(corpus),
        "total_chars":      total_chars,
        "x_handles":        profile.get("x_handles", []),
        "x_display_names":  profile.get("x_display_names", []),
    }


def get_socint_matches(db, actor_id: int, limit: int = 3) -> list[dict]:
    """
    Top-N stylometric match peers for one actor from socint_resonance.

    The table uses a actor_a < actor_b CHECK constraint, so a single actor
    can appear in either column — the CASE expression resolves the peer.

    Returned keys per row
    ---------------------
    peer_id         : int   — actor_id of the matching actor
    peer_name       : str   — name of the matching actor
    peer_type       : str   — actor type classification
    resonance_score : float — stylometric resonance [0.0–1.0]
    computed_at     : str   — ISO timestamp of last score computation
    """
    try:
        rows = db.execute("""
            SELECT
                CASE WHEN sr.actor_a = ? THEN sr.actor_b
                     ELSE                     sr.actor_a
                END                     AS peer_id,
                a.name                  AS peer_name,
                a.type                  AS peer_type,
                sr.resonance_score,
                sr.computed_at
            FROM   socint_resonance sr
            JOIN   actors a
                ON a.actor_id = CASE WHEN sr.actor_a = ? THEN sr.actor_b
                                     ELSE                     sr.actor_a END
            WHERE  sr.actor_a = ? OR sr.actor_b = ?
            ORDER  BY sr.resonance_score DESC
            LIMIT  ?
        """, (actor_id, actor_id, actor_id, actor_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── FLUX DISCOVERY ───────────────────────────────────────────────────────────
# Source: flux_latent_seeds + flux_tag_cooccurrence

def get_flux_discovery(db, limit: int = 60) -> list[dict]:
    """
    Tag cloud data for the FLUX discovery surface.

    Returns active latent seeds ordered by composite score
    (jaccard_score × velocity DESC). Each row includes:
        tag, parent_seed, discovery_depth,
        jaccard_score, velocity, total_count, last_seen.

    Returns empty list when flux_latent_seeds doesn't exist yet.
    """
    try:
        rows = db.execute("""
            SELECT
                tag,
                parent_seed,
                discovery_depth,
                ROUND(jaccard_score, 4)          AS jaccard_score,
                ROUND(velocity, 4)               AS velocity,
                ROUND(jaccard_score * velocity, 4) AS composite_score,
                total_count,
                last_seen
            FROM   flux_latent_seeds
            WHERE  is_active = 1
            ORDER  BY jaccard_score * velocity DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_flux_cooccurrence_summary(db, limit: int = 30) -> list[dict]:
    """
    Top co-occurring tag pairs across all pulses, for the neighborhood graph.

    Returns rows of: seed_tag, co_tag, total_count — ordered by count DESC.
    Excludes the __total__ sentinel rows.
    """
    try:
        rows = db.execute("""
            SELECT
                seed_tag,
                co_tag,
                SUM(count) AS total_count
            FROM  flux_tag_cooccurrence
            WHERE co_tag != '__total__'
            GROUP BY seed_tag, co_tag
            ORDER BY total_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── MOCK DATA (fallback when tables are empty) ────────────────────────────────

MOCK_TOP = [
    {
        "id": 1,
        "title": "ELEVATED ACTIVITY — EASTERN CORRIDOR",
        "summary": "Multiple correlated signals detected across three nodes. Pattern consistent with coordinated movement.",
        "confidence": 0.87,
        "latitude": -25.7,
        "longitude": 28.2,
        "created_at": "2026-01-01 00:00:00",
    },
    {
        "id": 2,
        "title": "NARRATIVE SURGE — INFORMATION DOMAIN",
        "summary": "Rapid amplification of recurring keyword clusters across monitored feeds. Possible coordinated campaign.",
        "confidence": 0.74,
        "latitude": -26.2,
        "longitude": 28.0,
        "created_at": "2026-01-01 00:00:00",
    },
    {
        "id": 3,
        "title": "ACTOR NETWORK EMERGENCE DETECTED",
        "summary": "Three previously unlinked actors now showing co-occurrence patterns above threshold.",
        "confidence": 0.61,
        "latitude": -25.9,
        "longitude": 28.4,
        "created_at": "2026-01-01 00:00:00",
    },
]

MOCK_INCIDENTS = [
    {"id": 1, "title": "CORRELATED INCIDENT", "summary": "Signal cluster Alpha ↔ Signal cluster Beta. Spatial overlap detected.", "confidence": 0.82, "latitude": -25.7, "longitude": 28.2, "created_at": "2026-01-01 00:00:00", "source_module": "correlation_engine"},
    {"id": 2, "title": "SENTINEL ALERT", "summary": "Anomalous signal frequency spike — GDELT source. 3x baseline.", "confidence": 0.69, "latitude": -26.1, "longitude": 27.9, "created_at": "2026-01-01 00:00:00", "source_module": "sentinel"},
    {"id": 3, "title": "SENTINEL ALERT", "summary": "Priority signal cluster detected near monitored region.", "confidence": 0.55, "latitude": -25.5, "longitude": 28.6, "created_at": "2026-01-01 00:00:00", "source_module": "sentinel"},
]

MOCK_MAP = [
    {"id": "mock-1", "title": "Mock Signal — Pretoria Region",   "summary": "Simulated signal for map rendering.", "confidence": 1, "latitude": -25.7, "longitude": 28.2, "created_at": "2026-01-01 00:00:00"},
    {"id": "mock-2", "title": "Mock Signal — Johannesburg",      "summary": "Simulated signal for map rendering.", "confidence": 1, "latitude": -26.2, "longitude": 28.0, "created_at": "2026-01-01 00:00:00"},
    {"id": "mock-3", "title": "Mock Signal — Mpumalanga Node",   "summary": "Simulated signal for map rendering.", "confidence": 1, "latitude": -25.5, "longitude": 30.9, "created_at": "2026-01-01 00:00:00"},
]

MOCK_STREAM = [
    {"id": "mock-s1", "title": "GDELT — Political tension reported", "summary": None, "source": "gdelt",  "is_priority": 1, "stream": "PRIORITY", "created_at": "2026-01-01 00:00:00"},
    {"id": "mock-s2", "title": "USGS — Seismic event M2.1",          "summary": None, "source": "usgs",   "is_priority": 0, "stream": "GLOBAL",   "created_at": "2026-01-01 00:00:00"},
    {"id": "mock-s3", "title": "RSS  — Regional media bulletin",     "summary": None, "source": "rss",    "is_priority": 0, "stream": "GLOBAL",   "created_at": "2026-01-01 00:00:00"},
    {"id": "mock-s4", "title": "FIRMS — Fire activity detected",     "summary": None, "source": "firms",  "is_priority": 0, "stream": "GLOBAL",   "created_at": "2026-01-01 00:00:00"},
]