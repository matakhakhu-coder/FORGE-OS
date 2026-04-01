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

from flask import Blueprint, jsonify, render_template, current_app

from surface.queries import (
    get_top_situations,
    get_incidents,
    get_map_incidents,
    get_signal_stream,
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