#!/usr/bin/env python3
from __future__ import annotations

"""Blueprint: map and geospatial routes — Leaflet explorer, GeoJSON endpoints,
signal layer, graph-edge polylines, cluster centroids."""

import json as _json
from datetime import datetime, timezone
from urllib.parse import quote_plus as _qp

from flask import Blueprint, Response, jsonify, render_template, request

from core.web.helpers import get_db

map_bp = Blueprint("map_routes", __name__)


# -----------------------------------------------------------------------
# Route: /map — Phase 5: Leaflet geographic explorer
# -----------------------------------------------------------------------

@map_bp.route("/map")
def map_explorer():
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    event_where = "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    event_params = []
    if lens != 'all':
        event_where += " AND source_type = ?"
        event_params.append(lens)

    # Summary counts for the map header stats
    geo_count = db.execute(f"""
        SELECT COUNT(*) FROM events
        {event_where}
    """, event_params).fetchone()[0]

    if lens == 'all':
        total_events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    else:
        total_events = db.execute("SELECT COUNT(*) FROM events WHERE source_type = ?", (lens,)).fetchone()[0]

    # Category list for the filter legend
    categories = db.execute("""
        SELECT DISTINCT category FROM events
        WHERE  category IS NOT NULL
          AND  latitude IS NOT NULL
        ORDER  BY category
    """).fetchall()

    # Date range for the time-slider — only events with dates AND coordinates
    date_range = db.execute("""
        SELECT MIN(date) AS min_date, MAX(date) AS max_date
        FROM   events
        WHERE  latitude IS NOT NULL
          AND  longitude IS NOT NULL
          AND  date IS NOT NULL
    """).fetchone()

    # Phase 16: active cases for the map popup pin-to-case widget
    active_cases = db.execute("""
        SELECT case_id, name, status
        FROM   cases
        WHERE  status = 'active'
        ORDER  BY created_at DESC
    """).fetchall()

    return render_template(
        "map.html",
        geo_count=geo_count,
        total_events=total_events,
        categories=[r["category"] for r in categories],
        min_date=date_range["min_date"] or "",
        max_date=date_range["max_date"] or "",
        active_cases=[dict(r) for r in active_cases],
    )


# -----------------------------------------------------------------------
# API: /api/geo — GeoJSON endpoint for Leaflet
# -----------------------------------------------------------------------

@map_bp.route("/api/geo")
def api_geo():
    """
    Returns all mappable events as a GeoJSON FeatureCollection.
    Each Feature carries the properties Leaflet needs for popups
    and category-based marker styling.
    """
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    where_clauses = ["e.latitude IS NOT NULL", "e.longitude IS NOT NULL"]
    params = []
    if lens != 'all':
        where_clauses.append("e.source_type = ?")
        params.append(lens)

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(f"""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               e.latitude, e.longitude,
               COUNT(a.artifact_id) AS artifact_count
        FROM   events e
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        WHERE  {where_sql}
        GROUP  BY e.event_id
        ORDER  BY e.date ASC
    """, params).fetchall()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [r["longitude"], r["latitude"]],  # GeoJSON: [lon, lat]
            },
            "properties": {
                "id":             r["event_id"],
                "title":          r["title"],
                "date":           r["date"]     or "",
                "category":       r["category"] or "Other",
                "location":       r["location"] or "",
                "summary":        (r["summary"] or "")[:160],
                "artifact_count": r["artifact_count"],
                "url":            f"/event/{r['event_id']}",
            },
        })

    geojson = {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": {
            "total":    len(features),
            "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }

    return Response(
        _json.dumps(geojson, ensure_ascii=False, indent=None),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


# -----------------------------------------------------------------------
# API: /api/geo/case/<case_id> — GeoJSON for a specific Case Workspace
# Returns only events pinned to that case that have coordinates.
# -----------------------------------------------------------------------

@map_bp.route("/api/geo/case/<int:case_id>")
def api_geo_case(case_id: int):
    """
    Case-scoped GeoJSON endpoint for the Tactical Map tab.
    Properties include sequence_order so the map can number the markers.
    """
    db   = get_db()
    case = db.execute(
        "SELECT case_id, name, status FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404

    rows = db.execute("""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               e.latitude, e.longitude,
               COUNT(a.artifact_id) AS artifact_count,
               ce.sequence_order, ce.note AS pin_note
        FROM   case_events ce
        JOIN   events e ON e.event_id = ce.event_id
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        WHERE  ce.case_id   = ?
          AND  e.latitude   IS NOT NULL
          AND  e.longitude  IS NOT NULL
        GROUP  BY e.event_id
        ORDER  BY ce.sequence_order ASC NULLS LAST, e.date ASC
    """, (case_id,)).fetchall()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [r["longitude"], r["latitude"]],
            },
            "properties": {
                "id":             r["event_id"],
                "title":          r["title"],
                "date":           r["date"]      or "",
                "category":       r["category"]  or "Other",
                "location":       r["location"]  or "",
                "summary":        (r["summary"]  or "")[:160],
                "artifact_count": r["artifact_count"],
                "pin_note":       r["pin_note"]  or "",
                "sequence_order": r["sequence_order"],
                "url":            f"/event/{r['event_id']}",
            },
        })

    geojson = {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": {
            "case_id":   case_id,
            "case_title": case["name"],
            "total":     len(features),
        },
    }

    return Response(
        _json.dumps(geojson, ensure_ascii=False, indent=None),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


# -----------------------------------------------------------------------
# Phase 15: /api/signals/geojson — Live Signal Layer for map.html
# Returns raw + promoted signals that have coordinates as GeoJSON.
# Properties include is_priority, cluster_id, source, status so the
# Leaflet layer can style markers (grey / pulsing-red / cluster-purple).
# -----------------------------------------------------------------------

@map_bp.route("/api/signals/geojson")
def api_signals_geojson():
    """
    FeatureCollection of mappable signals (raw + promoted, lat/lng present).
    Used by the Phase 15 live-signal Leaflet overlay on map.html.

    Optimisations (Phase 20):
      - Coordinates pruned to 6 decimal places (~11 cm precision, ~30% smaller JSON)
      - Hard cap of 2 000 features — priority signals ranked first
      - Optional query params:
          ?source=usgs        — filter to one source
          ?hours=24           — only signals from last N hours
          ?priority_only=1    — only is_priority signals
    """
    db = get_db()

    source        = request.args.get("source",        "").strip()
    hours         = request.args.get("hours",         type=int)
    priority_only = request.args.get("priority_only", type=int, default=0)
    mode          = request.args.get("mode",          "").strip().lower()  # "relevant" = case-pinned only

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    features = []

    # ── Relevant mode: only signals pinned to a case ─────────────────────
    if mode == "relevant":
        rows = db.execute("""
            SELECT DISTINCT
                   s.signal_id, s.source, s.title, s.content,
                   ROUND(s.lat, 6) AS lat,
                   ROUND(s.lng, 6) AS lng,
                   s.timestamp, s.status,
                   s.is_priority, s.cluster_id, s.stream,
                   COALESCE(s.relevance_score, 1.0) AS relevance_score,
                   s.gravity_score,
                   cs.case_id,
                   c.name AS case_name
            FROM   signals s
            JOIN   case_signals cs ON s.signal_id = cs.signal_id
            JOIN   cases c         ON cs.case_id  = c.case_id
            WHERE  s.lat IS NOT NULL
              AND  s.lng IS NOT NULL
              AND  s.lat != 0
              AND  s.lng != 0
            ORDER  BY s.gravity_score DESC NULLS LAST
        """).fetchall()

        for r in rows:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type":        "Point",
                    "coordinates": [r["lng"], r["lat"]],
                },
                "properties": {
                    "signal_id":       r["signal_id"],
                    "source":          r["source"]          or "",
                    "title":           r["title"]           or "",
                    "content":         (r["content"]        or "")[:200],
                    "timestamp":       r["timestamp"]       or "",
                    "status":          r["status"]          or "raw",
                    "is_priority":     r["is_priority"]     or 0,
                    "cluster_id":      r["cluster_id"]      or None,
                    "stream":          r["stream"]          or "GLOBAL",
                    "relevance_score": round(float(r["relevance_score"] or 1.0), 3),
                    "gravity_score":   round(float(r["gravity_score"] or 0.0), 3),
                    "case_id":         r["case_id"],
                    "case_name":       r["case_name"]       or "",
                    "mode":            "relevant",
                    "promote_url": (
                        f"/admin/event/new"
                        f"?title={_qp(r['title'] or '')}"
                        f"&signal_id={r['signal_id']}"
                    ),
                },
            })

        geojson = {
            "type":     "FeatureCollection",
            "features": features,
            "metadata": {"total": len(features), "mode": "relevant"},
        }
        return Response(
            _json.dumps(geojson, ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # ── Default (corpus) mode ────────────────────────────────────────────
    clauses = [
        "status IN ('raw', 'promoted')",
        "lat IS NOT NULL",
        "lng IS NOT NULL",
    ]
    params: list = []
    if lens != 'all':
        clauses.append("source_type = ?")
        params.append(lens)

    if source:
        clauses.append("source = ?")
        params.append(source)
    if hours:
        clauses.append("timestamp >= datetime('now', ?)")
        params.append(f"-{hours} hours")
    if priority_only:
        clauses.append("is_priority = 1")

    where = " AND ".join(clauses)

    rows = db.execute(f"""
        SELECT signal_id, source, title, content,
               ROUND(lat, 6) AS lat,
               ROUND(lng, 6) AS lng,
               timestamp, status,
               is_priority, cluster_id, stream,
               COALESCE(relevance_score, 1.0) AS relevance_score
        FROM   signals
        WHERE  {where}
        ORDER  BY is_priority DESC, timestamp DESC
        LIMIT  2000
    """, params).fetchall()

    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [r["lng"], r["lat"]],  # GeoJSON [lon, lat]
            },
            "properties": {
                "signal_id":       r["signal_id"],
                "source":          r["source"]          or "",
                "title":           r["title"]           or "",
                "content":         (r["content"]        or "")[:200],
                "timestamp":       r["timestamp"]       or "",
                "status":          r["status"]          or "raw",
                "is_priority":     r["is_priority"]     or 0,
                "cluster_id":      r["cluster_id"]      or None,
                "stream":          r["stream"]          or "GLOBAL",
                "relevance_score": round(float(r["relevance_score"] or 1.0), 3),
                "promote_url": (
                    f"/admin/event/new"
                    f"?title={_qp(r['title'] or '')}"
                    f"&summary={_qp((r['content'] or '')[:300])}"
                    f"&date={r['timestamp'][:10] if r['timestamp'] else ''}"
                    f"&latitude={r['lat']}"
                    f"&longitude={r['lng']}"
                    f"&category=Other"
                    f"&signal_id={r['signal_id']}"
                ),
            },
        })

    geojson = {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": {
            "total":    len(features),
            "priority": sum(1 for f in features if f["properties"]["is_priority"]),
            "capped":   len(features) == 2000,
            "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }

    return Response(
        _json.dumps(geojson, ensure_ascii=False),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


# -----------------------------------------------------------------------
# Map: /api/map/graph-edges — Case-graph polyline layer
# Returns LineString features for every entity_relationship whose two
# actors each have at least one case-pinned signal with coordinates.
# Each actor is positioned at the geographic centroid of its case-pinned
# signals.  Used by map.html "Relevant" (Intel) toggle.
# -----------------------------------------------------------------------

@map_bp.route("/api/map/graph-edges")
def api_map_graph_edges():
    db = get_db()

    # Actor centroids: avg lat/lng of their case-pinned signals
    actor_rows = db.execute("""
        SELECT sa.actor_id,
               AVG(s.lat) AS lat,
               AVG(s.lng) AS lng,
               COUNT(DISTINCT s.signal_id) AS signal_count
        FROM signal_actors sa
        JOIN signals s        ON sa.signal_id = s.signal_id
        JOIN case_signals cs  ON s.signal_id  = cs.signal_id
        WHERE s.lat IS NOT NULL
          AND s.lng IS NOT NULL
          AND s.lat != 0
          AND s.lng != 0
        GROUP BY sa.actor_id
    """).fetchall()

    centroids = {r["actor_id"]: (r["lat"], r["lng"], r["signal_count"])
                 for r in actor_rows}

    rel_rows = db.execute("""
        SELECT er.relation_type,
               ROUND(er.confidence, 3) AS confidence,
               er.extraction_method,
               a.name  AS actor_a,
               b.name  AS actor_b,
               er.subject_actor_id AS aid_a,
               er.object_actor_id  AS aid_b
        FROM entity_relationships er
        JOIN actors a ON er.subject_actor_id = a.actor_id
        JOIN actors b ON er.object_actor_id  = b.actor_id
    """).fetchall()

    features = []
    for r in rel_rows:
        ca = centroids.get(r["aid_a"])
        cb = centroids.get(r["aid_b"])
        if not ca or not cb:
            continue
        # Skip relationships where both actors geocode to the exact same
        # default centroid (collector artifact) — they produce zero-length edges
        if abs(ca[0] - cb[0]) < 0.001 and abs(ca[1] - cb[1]) < 0.001:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(ca[1], 5), round(ca[0], 5)],
                    [round(cb[1], 5), round(cb[0], 5)],
                ],
            },
            "properties": {
                "relation_type":      r["relation_type"],
                "confidence":         r["confidence"],
                "extraction_method":  r["extraction_method"] or "manual",
                "actor_a":            r["actor_a"],
                "actor_b":            r["actor_b"],
            },
        })

    return Response(
        _json.dumps({"type": "FeatureCollection", "features": features},
                    ensure_ascii=False),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


# -----------------------------------------------------------------------
# Phase 15: /api/clusters/geojson — Cluster Centroid Layer
# One point per distinct cluster_id, located at the arithmetic centroid
# of all member signals.  Properties carry member count, cluster_id, and
# a sample title so the popup has something useful to say.
# -----------------------------------------------------------------------

@map_bp.route("/api/clusters/geojson")
def api_clusters_geojson():
    """
    One GeoJSON Feature per FORAGE cluster — positioned at the centroid
    of all member signals.  Used by the Phase 15 cluster overlay layer.
    """
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    where_clauses = [
        "cluster_id IS NOT NULL",
        "lat IS NOT NULL",
        "lng IS NOT NULL",
    ]
    params = []
    if lens != 'all':
        where_clauses.append("source_type = ?")
        params.append(lens)

    # Aggregate per cluster_id: centroid + count + earliest/latest timestamp
    rows = db.execute(f"""
        SELECT cluster_id,
               ROUND(AVG(lat), 6) AS centroid_lat,
               ROUND(AVG(lng), 6) AS centroid_lng,
               COUNT(*)           AS member_count,
               MIN(timestamp)     AS earliest,
               MAX(timestamp)     AS latest,
               MAX(is_priority)   AS any_priority,
               GROUP_CONCAT(title, ' | ') AS sample_titles
        FROM   signals
        WHERE  {' AND '.join(where_clauses)}
        GROUP  BY cluster_id
        ORDER  BY member_count DESC
    """, params).fetchall()

    features = []
    for r in rows:
        # Trim sample_titles to a readable preview
        raw_titles = r["sample_titles"] or ""
        titles     = raw_titles.split(" | ")[:3]
        preview    = " · ".join(t[:60] for t in titles)
        if len(raw_titles.split(" | ")) > 3:
            preview += " …"

        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [r["centroid_lng"], r["centroid_lat"]],
            },
            "properties": {
                "cluster_id":    r["cluster_id"],
                "member_count":  r["member_count"],
                "earliest":      r["earliest"]     or "",
                "latest":        r["latest"]        or "",
                "any_priority":  r["any_priority"]  or 0,
                "preview":       preview,
                # 8-char truncated ID for the badge label
                "short_id":      r["cluster_id"][:8],
            },
        })

    geojson = {
        "type":     "FeatureCollection",
        "features": features,
        "metadata": {
            "total":    len(features),
            "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }

    return Response(
        _json.dumps(geojson, ensure_ascii=False),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )
