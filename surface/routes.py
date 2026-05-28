"""
surface/routes.py
═════════════════
FORGE Intelligence Surface — Flask Blueprint.

Routes:
    GET  /surface                  — main dashboard page
    GET  /api/surface/top          — top 3 situations
    GET  /api/surface/incidents    — incident feed
    GET  /api/surface/map          — geolocated incidents for Leaflet
    GET  /api/surface/signals      — chronological signal stream

All API routes return JSON.
Falls back to mock data when FORGE tables are empty.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, current_app, request

from surface.queries import (
    get_top_situations,
    get_incidents,
    get_map_incidents,
    get_signal_stream,
    get_actor_socint_profile,
    get_socint_matches,
    get_flux_discovery,
    get_flux_cooccurrence_summary,
    MOCK_TOP,
    MOCK_INCIDENTS,
    MOCK_MAP,
    MOCK_STREAM,
)

surface_bp = Blueprint(
    "surface",
    __name__,
    template_folder="../templates",
    url_prefix="",
)


def _db():
    """Get the request-scoped DB connection from the app."""
    return current_app.get_db()


# ── Page ──────────────────────────────────────────────────────────────────────

@surface_bp.route("/surface")
def surface():
    return render_template("surface.html")


# ── API: Top Situations ───────────────────────────────────────────────────────

@surface_bp.route("/api/surface/top")
def api_surface_top():
    try:
        data = get_top_situations(_db(), limit=3)
        if not data:
            data = MOCK_TOP
            mock = True
        else:
            mock = False
        return jsonify({"top": data, "total": len(data), "mock": mock})
    except Exception as exc:
        return jsonify({"top": MOCK_TOP, "total": len(MOCK_TOP),
                        "mock": True, "error": str(exc)})


# ── API: Incident Feed ────────────────────────────────────────────────────────

@surface_bp.route("/api/surface/incidents")
def api_surface_incidents():
    try:
        data = get_incidents(_db(), limit=20)
        if not data:
            data = MOCK_INCIDENTS
            mock = True
        else:
            mock = False
        return jsonify({"incidents": data, "total": len(data), "mock": mock})
    except Exception as exc:
        return jsonify({"incidents": MOCK_INCIDENTS, "total": len(MOCK_INCIDENTS),
                        "mock": True, "error": str(exc)})


# ── API: Map Incidents ────────────────────────────────────────────────────────

@surface_bp.route("/api/surface/map")
def api_surface_map():
    try:
        data = get_map_incidents(_db(), hours=48, limit=200)
        if not data:
            data = MOCK_MAP
            mock = True
        else:
            mock = False
        return jsonify({"incidents": data, "total": len(data), "mock": mock})
    except Exception as exc:
        return jsonify({"incidents": MOCK_MAP, "total": len(MOCK_MAP),
                        "mock": True, "error": str(exc)})


# ── API: Signal Stream ────────────────────────────────────────────────────────

@surface_bp.route("/api/surface/signals")
def api_surface_signals():
    try:
        data = get_signal_stream(_db(), limit=50)
        if not data:
            data = MOCK_STREAM
            mock = True
        else:
            mock = False
        return jsonify({"signals": data, "total": len(data), "mock": mock})
    except Exception as exc:
        return jsonify({"signals": MOCK_STREAM, "total": len(MOCK_STREAM),
                        "mock": True, "error": str(exc)})


# ── Page: FLUX Discovery ─────────────────────────────────────────────────────

@surface_bp.route("/flux/discovery")
def flux_discovery_page():
    """Tag cloud + expansion frontier for the FLUX discovery surface."""
    return render_template("flux_discovery.html")


# ── API: FLUX Discovery data ──────────────────────────────────────────────────

@surface_bp.route("/api/flux/discovery")
def api_flux_discovery():
    """
    FLUX latent seed tag cloud data.

    Response schema
    ---------------
    {
        "seeds": [
            {
                "tag": str, "parent_seed": str, "discovery_depth": int,
                "jaccard_score": float, "velocity": float,
                "composite_score": float, "total_count": int, "last_seen": str
            }, ...
        ],
        "edges": [
            { "seed_tag": str, "co_tag": str, "total_count": int }, ...
        ],
        "total": int
    }
    """
    try:
        seeds = get_flux_discovery(_db(), limit=60)
        edges = get_flux_cooccurrence_summary(_db(), limit=30)
        return jsonify({"seeds": seeds, "edges": edges, "total": len(seeds)})
    except Exception as exc:
        return jsonify({"seeds": [], "edges": [], "total": 0, "error": str(exc)})


# ── API: Run Discovery Engine (POST trigger) ─────────────────────────────────

@surface_bp.route("/api/flux/run-discovery", methods=["POST"])
def api_flux_run_discovery():
    """
    Trigger the FLUX discovery engine inline.
    Runs synchronously — intended for analyst-triggered passes, not
    high-frequency automation (use a scheduled job for that).
    """
    try:
        from pathlib import Path as _Path
        import os as _os
        from flux.processors.discovery import run as _disc_run, DB_PATH as _DISC_DB

        dry_run   = request.json.get("dry_run", False) if request.is_json else False
        threshold = float(
            request.json.get("threshold", 0.30) if request.is_json else 0.30
        )
        result = _disc_run(dry_run=dry_run, threshold=threshold)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


# ── API: SOCINT Dossier ───────────────────────────────────────────────────────

@surface_bp.route("/api/actor/<int:actor_id>/socint")
def api_actor_socint(actor_id: int):
    """
    SOCINT dossier for one actor — FLUX corpus stats + top stylometric matches.

    Used by the actor dossier page to populate the SOCINT intel panel without
    blocking the main actor_detail render. Returns 200 with empty profile/matches
    when no SOCINT data exists (the UI renders a 'no data' state gracefully).

    Response schema
    ---------------
    {
        "actor_id": int,
        "profile": {
            "corpus_ready": bool,
            "sample_count": int,
            "total_chars":  int,
            "x_handles":    [str, ...],
            "x_display_names": [str, ...]
        },
        "matches": [
            {
                "peer_id": int, "peer_name": str, "peer_type": str,
                "resonance_score": float, "computed_at": str
            }, ...
        ]
    }
    """
    try:
        profile = get_actor_socint_profile(_db(), actor_id)
        matches = get_socint_matches(_db(), actor_id, limit=3)
        return jsonify({
            "actor_id": actor_id,
            "profile":  profile,
            "matches":  matches,
        })
    except Exception as exc:
        return jsonify({
            "actor_id": actor_id,
            "profile":  {},
            "matches":  [],
            "error":    str(exc),
        }), 500