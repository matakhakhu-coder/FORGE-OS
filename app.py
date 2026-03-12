"""
FORGE — Foundational Open Research & Graph Engine
==================================================
Local investigative archive platform.
Author : Matamela Ramovha
Version: 1.0 (MVP — Phase 3: Visual Framework & Admin Interface)

Phase 3 changes
---------------
- Live DB queries on /, /events, /actors, /search
- Admin POST handler: artifact ingestion with file upload
- File-type detection and routing to correct /media subdirectory
- Thumbnail generation via Pillow for image artifacts
- Flash-message support for admin feedback
- werkzeug secure_filename for upload safety
"""

import os
import sys
import sqlite3
import argparse
import uuid
from pathlib import Path

from flask import (
    Flask, g, render_template, request, redirect,
    url_for, flash, send_from_directory,
)
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR  = Path(__file__).resolve().parent
DB_PATH   = BASE_DIR / "database.db"
MEDIA_DIR = BASE_DIR / "media"

MEDIA_SUBDIRS = ["images", "videos", "documents", "audio"]

ADMIN_PASSWORD = os.environ.get("FORGE_ADMIN_PASSWORD", "forge-admin")

# Allowed upload extensions mapped to media subdirectory
ALLOWED_EXTENSIONS: dict[str, str] = {
    "jpg": "images", "jpeg": "images", "png": "images",
    "gif": "images", "webp": "images",
    "mp4": "videos", "mov": "videos", "avi": "videos", "mkv": "videos",
    "mp3": "audio",  "wav": "audio",  "ogg": "audio",  "m4a": "audio",
    "pdf": "documents", "doc": "documents", "docx": "documents",
    "txt": "documents", "csv": "documents",
}

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

# Source classification display config (used by templates via context)
SOURCE_META = {
    "verified":    {"label": "Verified",         "colour": "#2d7a4f"},
    "unverified":  {"label": "Unverified",        "colour": "#b07d2a"},
    "government":  {"label": "Government Source", "colour": "#1e3a6e"},
    "leaked":      {"label": "Anonymous Leak",    "colour": "#8b1a1a"},
    "citizen":     {"label": "Citizen Footage",   "colour": "#4a4a4a"},
    "media":       {"label": "Media Report",      "colour": "#1a4a6e"},
}


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.secret_key = os.environ.get("FORGE_SECRET_KEY", "forge-dev-secret")

    # -----------------------------------------------------------------------
    # Database helpers
    # -----------------------------------------------------------------------

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL;")
            g.db.execute("PRAGMA foreign_keys=ON;")
        return g.db

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    app.get_db = get_db  # type: ignore[attr-defined]

    # Inject SOURCE_META into every template render context
    @app.context_processor
    def inject_globals():
        return {"SOURCE_META": SOURCE_META}

    # -----------------------------------------------------------------------
    # Route: / — Dashboard
    # -----------------------------------------------------------------------

    @app.route("/")
    def index():
        db = get_db()

        stats = {
            "artifacts": db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
            "events":    db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "actors":    db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
        }

        recent_artifacts = db.execute("""
            SELECT a.artifact_id, a.title, a.type, a.date, a.source, a.thumbnail,
                   e.title AS event_title, e.event_id
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            ORDER  BY a.created_at DESC
            LIMIT  6
        """).fetchall()

        recent_events = db.execute("""
            SELECT event_id, title, date, category, location
            FROM   events
            ORDER  BY date DESC
            LIMIT  5
        """).fetchall()

        type_breakdown = db.execute("""
            SELECT type, COUNT(*) AS cnt
            FROM   artifacts
            GROUP  BY type
            ORDER  BY cnt DESC
        """).fetchall()

        return render_template(
            "index.html",
            stats=stats,
            recent_artifacts=recent_artifacts,
            recent_events=recent_events,
            type_breakdown=type_breakdown,
        )

    # -----------------------------------------------------------------------
    # Route: /events — Event list
    # -----------------------------------------------------------------------

    @app.route("/events")
    def events():
        db       = get_db()
        category = request.args.get("category", "")
        sort     = request.args.get("sort", "date_desc")

        order_map = {
            "date_desc":  "e.date DESC",
            "date_asc":   "e.date ASC",
            "title_asc":  "e.title ASC",
        }
        order_clause = order_map.get(sort, "e.date DESC")

        where_clause = "WHERE e.category = ?" if category else ""
        params       = (category,) if category else ()

        events_rows = db.execute(f"""
            SELECT e.event_id, e.title, e.summary, e.date, e.category,
                   e.location,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {where_clause}
            GROUP  BY e.event_id
            ORDER  BY {order_clause}
        """, params).fetchall()

        categories = db.execute("""
            SELECT DISTINCT category FROM events
            WHERE  category IS NOT NULL
            ORDER  BY category
        """).fetchall()

        return render_template(
            "events.html",
            events=events_rows,
            categories=[r["category"] for r in categories],
            current_category=category,
            current_sort=sort,
        )

    # -----------------------------------------------------------------------
    # Route: /actors — Actor list
    # -----------------------------------------------------------------------

    @app.route("/actors")
    def actors():
        db = get_db()

        actors_rows = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   COUNT(DISTINCT ae.event_id)    AS event_count,
                   COUNT(DISTINCT a.artifact_id)  AS artifact_count
            FROM   actors ac
            LEFT   JOIN actor_events ae ON ae.actor_id = ac.actor_id
            LEFT   JOIN artifacts a     ON a.event_id  = ae.event_id
            GROUP  BY ac.actor_id
            ORDER  BY event_count DESC, ac.name
        """).fetchall()

        return render_template("actors.html", actors=actors_rows)

    # -----------------------------------------------------------------------
    # Route: /search — FTS5 full-text search
    # -----------------------------------------------------------------------

    @app.route("/search")
    def search():
        db    = get_db()
        query = request.args.get("q", "").strip()

        artifact_results = []
        event_results    = []
        error            = None

        if query:
            try:
                # FTS5 MATCH requires the query to be in FTS syntax.
                # Wrap bare terms so they work as a prefix search.
                fts_query = query if any(
                    c in query for c in ('"', '*', 'OR', 'AND', 'NOT')
                ) else f'"{query}"'

                # Note: snippet() is unavailable on content= FTS5 tables without
                # columnsize=0.  We fetch description/summary and build excerpts
                # in the template instead.
                artifact_results = db.execute("""
                    SELECT a.artifact_id, a.title, a.type, a.date, a.source,
                           a.description, a.tags, a.thumbnail,
                           e.title AS event_title, e.event_id
                    FROM   artifacts_fts f
                    JOIN   artifacts a ON a.artifact_id = f.rowid
                    LEFT   JOIN events e ON e.event_id = a.event_id
                    WHERE  artifacts_fts MATCH ?
                    ORDER  BY rank
                """, (fts_query,)).fetchall()

                event_results = db.execute("""
                    SELECT e.event_id, e.title, e.date, e.category,
                           e.summary, e.location,
                           COUNT(a.artifact_id) AS artifact_count
                    FROM   events_fts f
                    JOIN   events e ON e.event_id = f.rowid
                    LEFT   JOIN artifacts a ON a.event_id = e.event_id
                    WHERE  events_fts MATCH ?
                    GROUP  BY e.event_id
                    ORDER  BY rank
                """, (fts_query,)).fetchall()

            except sqlite3.OperationalError as exc:
                error = f"Search syntax error: {exc}"

        total = len(artifact_results) + len(event_results)

        return render_template(
            "search.html",
            query=query,
            artifact_results=artifact_results,
            event_results=event_results,
            total=total,
            error=error,
        )

    # -----------------------------------------------------------------------
    # Route: /timeline — Phase 5: Chronological analysis
    # -----------------------------------------------------------------------

    @app.route("/timeline")
    def timeline():
        db = get_db()

        # All events with artifact counts, sorted chronologically
        rows = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   e.latitude, e.longitude,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            GROUP  BY e.event_id
            ORDER  BY e.date ASC, e.title ASC
        """).fetchall()

        # Group by year → month for template rendering
        from collections import OrderedDict
        grouped: dict = OrderedDict()
        undated = []
        for row in rows:
            if not row["date"]:
                undated.append(row)
                continue
            parts = row["date"].split("-")
            year  = parts[0]
            month = parts[1] if len(parts) > 1 else "01"
            grouped.setdefault(year, OrderedDict()).setdefault(month, []).append(row)

        # Archive span metadata
        dated = [r for r in rows if r["date"]]
        span = {
            "start": dated[0]["date"][:7]  if dated else None,
            "end":   dated[-1]["date"][:7] if dated else None,
            "years": len(grouped),
            "total": len(rows),
        }

        # Month name lookup for template
        MONTH_NAMES = {
            "01": "January", "02": "February", "03": "March",
            "04": "April",   "05": "May",       "06": "June",
            "07": "July",    "08": "August",    "09": "September",
            "10": "October", "11": "November",  "12": "December",
        }

        return render_template(
            "timeline.html",
            grouped=grouped,
            undated=undated,
            span=span,
            MONTH_NAMES=MONTH_NAMES,
        )

    # -----------------------------------------------------------------------
    # Route: /map — Phase 5: Leaflet geographic explorer
    # -----------------------------------------------------------------------

    @app.route("/map")
    def map_explorer():
        db = get_db()

        # Summary counts for the map header stats
        geo_count = db.execute("""
            SELECT COUNT(*) FROM events
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """).fetchone()[0]

        total_events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

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

        return render_template(
            "map.html",
            geo_count=geo_count,
            total_events=total_events,
            categories=[r["category"] for r in categories],
            min_date=date_range["min_date"] or "",
            max_date=date_range["max_date"] or "",
        )

    # -----------------------------------------------------------------------
    # API: /api/geo — GeoJSON endpoint for Leaflet
    # -----------------------------------------------------------------------

    @app.route("/api/geo")
    def api_geo():
        """
        Returns all mappable events as a GeoJSON FeatureCollection.
        Each Feature carries the properties Leaflet needs for popups
        and category-based marker styling.
        """
        import json as _json
        db = get_db()

        rows = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   e.latitude, e.longitude,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  e.latitude  IS NOT NULL
              AND  e.longitude IS NOT NULL
            GROUP  BY e.event_id
            ORDER  BY e.date ASC
        """).fetchall()

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
                "generated": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            },
        }

        from flask import Response
        return Response(
            _json.dumps(geojson, ensure_ascii=False, indent=None),
            mimetype="application/geo+json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -----------------------------------------------------------------------
    # API: /api/geo/case/<case_id> — GeoJSON for a specific Case Workspace
    # Returns only events pinned to that case that have coordinates.
    # -----------------------------------------------------------------------

    @app.route("/api/geo/case/<int:case_id>")
    def api_geo_case(case_id: int):
        """
        Case-scoped GeoJSON endpoint for the Tactical Map tab.
        Properties include sequence_order so the map can number the markers.
        """
        import json as _json
        from flask import jsonify, Response

        db   = get_db()
        case = db.execute(
            "SELECT case_id, title, status FROM cases WHERE case_id=?", (case_id,)
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
                "case_title": case["title"],
                "total":     len(features),
            },
        }

        return Response(
            _json.dumps(geojson, ensure_ascii=False, indent=None),
            mimetype="application/geo+json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -----------------------------------------------------------------------
    # Route: /graph — Phase 6: D3.js Intelligence Graph
    # -----------------------------------------------------------------------

    @app.route("/graph")
    def graph():
        db = get_db()

        # Pre-compute summary stats for the graph header
        node_counts = {
            "actors":    db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
            "events":    db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "artifacts": db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
            "links":     db.execute("SELECT COUNT(*) FROM actor_events").fetchone()[0],
        }

        # Actor types for the filter UI (built server-side to avoid
        # an extra fetch on the client)
        actor_types = [
            r["type"] for r in db.execute(
                "SELECT DISTINCT type FROM actors ORDER BY type"
            ).fetchall()
        ]

        event_categories = [
            r["category"] for r in db.execute(
                "SELECT DISTINCT category FROM events WHERE category IS NOT NULL ORDER BY category"
            ).fetchall()
        ]

        return render_template(
            "graph.html",
            node_counts=node_counts,
            actor_types=actor_types,
            event_categories=event_categories,
        )

    # -----------------------------------------------------------------------
    # API: /api/graph — weighted graph data for D3 force simulation
    # -----------------------------------------------------------------------

    @app.route("/api/graph")
    def api_graph():
        """
        Returns a JSON graph with:
          nodes  — actors, events, (optionally artifacts)
          links  — actor↔event edges with weight = artifact count on that event

        Query params:
          actor_type  (multi) — filter to specific actor types
          category    (multi) — filter to specific event categories
          min_weight  (int)   — only include links with weight >= this value
          include_artifacts (bool) — add artifact nodes (default: false)
        """
        import json as _json
        from flask import request as req, Response

        db = get_db()

        # ── Query param parsing ─────────────────────────────────────────────
        actor_types   = req.args.getlist("actor_type")   # [] means all
        categories    = req.args.getlist("category")     # [] means all
        min_weight    = max(0, int(req.args.get("min_weight", 0)))
        incl_artifacts = req.args.get("include_artifacts", "false").lower() == "true"

        # ── Actors ─────────────────────────────────────────────────────────
        actor_where = ""
        actor_params: list = []
        if actor_types:
            placeholders = ",".join("?" * len(actor_types))
            actor_where  = f"WHERE type IN ({placeholders})"
            actor_params = list(actor_types)

        actors = db.execute(f"""
            SELECT actor_id, name, type, description
            FROM   actors
            {actor_where}
            ORDER  BY name
        """, actor_params).fetchall()

        actor_id_set = {r["actor_id"] for r in actors}

        # ── Events ─────────────────────────────────────────────────────────
        event_where = ""
        event_params: list = []
        if categories:
            placeholders = ",".join("?" * len(categories))
            event_where  = f"WHERE category IN ({placeholders})"
            event_params = list(categories)

        events = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {event_where}
            GROUP  BY e.event_id
            ORDER  BY e.date
        """, event_params).fetchall()

        event_id_set = {r["event_id"] for r in events}

        # ── Links: actor ↔ event with weight ──────────────────────────────
        # Weight = number of artifacts on that event associated with this actor
        # (i.e. artifacts on the event where the actor is linked)
        # A weight of 0 means the actor is on the event but no artifacts yet.
        raw_links = db.execute("""
            SELECT ae.actor_id,
                   ae.event_id,
                   ae.role,
                   COUNT(a.artifact_id) AS weight
            FROM   actor_events ae
            LEFT   JOIN artifacts a ON a.event_id = ae.event_id
            GROUP  BY ae.actor_id, ae.event_id
            ORDER  BY weight DESC
        """).fetchall()

        # ── Artifact nodes (optional) ──────────────────────────────────────
        artifact_nodes = []
        artifact_links = []
        if incl_artifacts:
            artifacts = db.execute("""
                SELECT artifact_id, title, type, source, event_id
                FROM   artifacts
                WHERE  event_id IS NOT NULL
            """).fetchall()

            for a in artifacts:
                if a["event_id"] in event_id_set:
                    artifact_nodes.append({
                        "id":     f"artifact-{a['artifact_id']}",
                        "kind":   "artifact",
                        "label":  a["title"][:50],
                        "subtype": a["type"],
                        "source": a["source"] or "unverified",
                        "url":    f"/artifact/{a['artifact_id']}",
                    })
                    artifact_links.append({
                        "source": f"artifact-{a['artifact_id']}",
                        "target": f"event-{a['event_id']}",
                        "weight": 1,
                        "role":   "evidence",
                    })

        # ── Assemble nodes ──────────────────────────────────────────────────
        nodes = []

        for ac in actors:
            nodes.append({
                "id":          f"actor-{ac['actor_id']}",
                "kind":        "actor",
                "label":       ac["name"],
                "subtype":     ac["type"],
                "description": (ac["description"] or "")[:180],
                "url":         f"/actor/{ac['actor_id']}",
            })

        for ev in events:
            nodes.append({
                "id":             f"event-{ev['event_id']}",
                "kind":           "event",
                "label":          ev["title"],
                "subtype":        ev["category"] or "Other",
                "date":           ev["date"] or "",
                "location":       ev["location"] or "",
                "summary":        (ev["summary"] or "")[:180],
                "artifact_count": ev["artifact_count"],
                "url":            f"/event/{ev['event_id']}",
            })

        nodes.extend(artifact_nodes)

        # ── Assemble links ──────────────────────────────────────────────────
        links = []
        for lk in raw_links:
            # Skip if either endpoint was filtered out
            if lk["actor_id"] not in actor_id_set:
                continue
            if lk["event_id"] not in event_id_set:
                continue
            if lk["weight"] < min_weight:
                continue
            links.append({
                "source": f"actor-{lk['actor_id']}",
                "target": f"event-{lk['event_id']}",
                "weight": lk["weight"],
                "role":   lk["role"] or "",
            })

        links.extend(artifact_links)

        # ── Graph metadata ──────────────────────────────────────────────────
        weight_values = [lk["weight"] for lk in links if lk["weight"] > 0]

        payload = {
            "nodes": nodes,
            "links": links,
            "meta": {
                "node_count":   len(nodes),
                "link_count":   len(links),
                "max_weight":   max(weight_values) if weight_values else 1,
                "actor_types":  list({n["subtype"] for n in nodes if n["kind"] == "actor"}),
                "categories":   list({n["subtype"] for n in nodes if n["kind"] == "event"}),
                "filters_applied": {
                    "actor_types": actor_types,
                    "categories":  categories,
                    "min_weight":  min_weight,
                },
            },
        }

        return Response(
            _json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -----------------------------------------------------------------------
    # Route: /admin — Admin panel (GET + POST)
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Phase 12: /api/graph_data — Vis-Network native payload
    # Returns nodes and edges pre-formatted for Vis-Network.
    # Shares the same filter params as /api/graph.
    # -----------------------------------------------------------------------

    @app.route("/api/graph_data")
    def api_graph_data():
        """
        Vis-Network graph payload.

        Nodes carry Vis-Network rendering properties directly:
          id, label, title (hover tooltip), shape, color, group,
          size, font, kind, subtype, description, url

        Edges carry:
          from, to, label, title, arrows, color, width, kind, role

        Query params (same as /api/graph):
          actor_type  (multi)
          category    (multi)
          min_weight  (int, default 0)
          include_artifacts (bool, default false)
        """
        import json as _json
        from flask import request as req, Response

        db = get_db()

        # ── Colour maps ─────────────────────────────────────────────────────
        ACTOR_COLOURS = {
            "person":      {"border": "#7aa2f7", "background": "#1a2a4a",
                            "highlight": {"border": "#9ab8ff", "background": "#263a5e"}},
            "institution": {"border": "#7aa2f7", "background": "#1a2a4a",
                            "highlight": {"border": "#9ab8ff", "background": "#263a5e"}},
            "media":       {"border": "#7aa2f7", "background": "#1a2a4a",
                            "highlight": {"border": "#9ab8ff", "background": "#263a5e"}},
            "movement":    {"border": "#7aa2f7", "background": "#1a2a4a",
                            "highlight": {"border": "#9ab8ff", "background": "#263a5e"}},
            "government":  {"border": "#7aa2f7", "background": "#1a2a4a",
                            "highlight": {"border": "#9ab8ff", "background": "#263a5e"}},
        }
        ACTOR_COLOUR_DEFAULT = {"border": "#7aa2f7", "background": "#1a2a4a",
                                "highlight": {"border": "#9ab8ff", "background": "#263a5e"}}

        EVENT_COLOURS = {
            "Election":    {"border": "#f7768e", "background": "#3a1a22",
                            "highlight": {"border": "#ff9aac", "background": "#4e2230"}},
            "Security":    {"border": "#f7768e", "background": "#3a1a22",
                            "highlight": {"border": "#ff9aac", "background": "#4e2230"}},
            "Civil Unrest":{"border": "#f7768e", "background": "#3a1a22",
                            "highlight": {"border": "#ff9aac", "background": "#4e2230"}},
            "Legislative": {"border": "#f7768e", "background": "#3a1a22",
                            "highlight": {"border": "#ff9aac", "background": "#4e2230"}},
            "Military":    {"border": "#f7768e", "background": "#3a1a22",
                            "highlight": {"border": "#ff9aac", "background": "#4e2230"}},
        }
        EVENT_COLOUR_DEFAULT = {"border": "#f7768e", "background": "#3a1a22",
                                "highlight": {"border": "#ff9aac", "background": "#4e2230"}}

        ARTIFACT_COLOUR = {"border": "#9ece6a", "background": "#1a2a14",
                           "highlight": {"border": "#b8e48a", "background": "#263a1e"}}

        EDGE_COLOUR     = {"color": "rgba(255,255,255,0.18)", "highlight": "#c8943a", "hover": "#c8943a"}

        # ── Parse filters ───────────────────────────────────────────────────
        actor_types    = req.args.getlist("actor_type")
        categories     = req.args.getlist("category")
        min_weight     = max(0, int(req.args.get("min_weight", 0)))
        incl_artifacts = req.args.get("include_artifacts", "false").lower() == "true"

        # ── Actors ─────────────────────────────────────────────────────────
        actor_where  = ""
        actor_params = []
        if actor_types:
            phs         = ",".join("?" * len(actor_types))
            actor_where = f"WHERE type IN ({phs})"
            actor_params = list(actor_types)

        actor_rows = db.execute(f"""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   COUNT(DISTINCT ae.event_id) AS event_count
            FROM   actors ac
            LEFT   JOIN actor_events ae ON ae.actor_id = ac.actor_id
            {actor_where}
            GROUP  BY ac.actor_id
            ORDER  BY ac.name
        """, actor_params).fetchall()

        actor_id_set = {r["actor_id"] for r in actor_rows}

        # ── Events ─────────────────────────────────────────────────────────
        event_where  = ""
        event_params = []
        if categories:
            phs         = ",".join("?" * len(categories))
            event_where = f"WHERE e.category IN ({phs})"
            event_params = list(categories)

        event_rows = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {event_where}
            GROUP  BY e.event_id
            ORDER  BY e.date
        """, event_params).fetchall()

        event_id_set = {r["event_id"] for r in event_rows}

        # ── actor_events edges ──────────────────────────────────────────────
        ae_rows = db.execute("""
            SELECT ae.actor_id, ae.event_id, ae.role,
                   COUNT(a.artifact_id) AS weight
            FROM   actor_events ae
            LEFT   JOIN artifacts a ON a.event_id = ae.event_id
            GROUP  BY ae.actor_id, ae.event_id
            ORDER  BY weight DESC
        """).fetchall()

        # ── Assemble Vis-Network nodes ──────────────────────────────────────
        vis_nodes = []
        vis_edges = []

        for ac in actor_rows:
            col   = ACTOR_COLOURS.get(ac["type"], ACTOR_COLOUR_DEFAULT)
            desc  = (ac["description"] or "").strip()
            tooltip = f"{ac['name']}\nType: {ac['type']}\nEvents: {ac['event_count']}"
            if desc:
                tooltip += f"\n\n{desc[:200]}"

            vis_nodes.append({
                # Vis-Network required
                "id":    f"actor-{ac['actor_id']}",
                "label": ac["name"][:28] + ("…" if len(ac["name"]) > 28 else ""),
                "title": tooltip,
                "shape": "dot",
                "size":  18,
                "color": col,
                "font":  {"color": "#9ab8e8", "size": 11, "face": "IBM Plex Mono"},
                "borderWidth": 2,
                "borderWidthSelected": 3,
                # custom metadata for side panel
                "kind":        "actor",
                "subtype":     ac["type"],
                "full_label":  ac["name"],
                "description": desc,
                "event_count": ac["event_count"],
                "url":         f"/actor/{ac['actor_id']}",
            })

        for ev in event_rows:
            col   = EVENT_COLOURS.get(ev["category"], EVENT_COLOUR_DEFAULT)
            summ  = (ev["summary"] or "").strip()
            tooltip = f"{ev['title']}"
            if ev["date"]:     tooltip += f"\nDate: {ev['date']}"
            if ev["category"]: tooltip += f"\nCategory: {ev['category']}"
            if ev["location"]: tooltip += f"\nLocation: {ev['location']}"
            # Scale square size by artifact count
            sz = 16 + min(ev["artifact_count"] or 0, 6) * 2

            vis_nodes.append({
                "id":    f"event-{ev['event_id']}",
                "label": ev["title"][:26] + ("…" if len(ev["title"]) > 26 else ""),
                "title": tooltip,
                "shape": "square",
                "size":  sz,
                "color": col,
                "font":  {"color": "#f7a0ae", "size": 11, "face": "IBM Plex Mono"},
                "borderWidth": 2,
                "borderWidthSelected": 3,
                # metadata
                "kind":           "event",
                "subtype":        ev["category"] or "Other",
                "full_label":     ev["title"],
                "date":           ev["date"]     or "",
                "location":       ev["location"] or "",
                "summary":        summ[:240],
                "artifact_count": ev["artifact_count"],
                "url":            f"/event/{ev['event_id']}",
            })

        # ── Artifact nodes (optional) ───────────────────────────────────────
        if incl_artifacts:
            art_rows = db.execute("""
                SELECT artifact_id, title, type, source, event_id, description
                FROM   artifacts
                WHERE  event_id IS NOT NULL
            """).fetchall()
            for ar in art_rows:
                if ar["event_id"] not in event_id_set:
                    continue
                vis_nodes.append({
                    "id":    f"artifact-{ar['artifact_id']}",
                    "label": ar["title"][:22] + ("…" if len(ar["title"]) > 22 else ""),
                    "title": f"{ar['title']}\nType: {ar['type']}\nSource: {ar['source'] or 'unverified'}",
                    "shape": "diamond",
                    "size":  10,
                    "color": ARTIFACT_COLOUR,
                    "font":  {"color": "#b0d090", "size": 9, "face": "IBM Plex Mono"},
                    "borderWidth": 1,
                    "kind":        "artifact",
                    "subtype":     ar["type"],
                    "full_label":  ar["title"],
                    "description": (ar["description"] or "")[:180],
                    "url":         f"/artifact/{ar['artifact_id']}",
                })
                vis_edges.append({
                    "from":   f"artifact-{ar['artifact_id']}",
                    "to":     f"event-{ar['event_id']}",
                    "label":  "evidence",
                    "title":  "evidence of",
                    "arrows": {"to": {"enabled": True, "scaleFactor": 0.6}},
                    "color":  {"color": "rgba(158,206,106,0.25)", "highlight": "#9ece6a", "hover": "#9ece6a"},
                    "width":  1,
                    "dashes": [4, 3],
                    "kind":   "artifact-edge",
                    "role":   "evidence",
                })

        # ── actor→event edges ───────────────────────────────────────────────
        for ae in ae_rows:
            if ae["actor_id"] not in actor_id_set:
                continue
            if ae["event_id"] not in event_id_set:
                continue
            if ae["weight"] < min_weight:
                continue

            w     = ae["weight"] or 0
            width = 1 + min(w, 6) * 0.4          # 1 → 3.4 proportional
            role  = ae["role"] or "participated"
            vis_edges.append({
                "from":   f"actor-{ae['actor_id']}",
                "to":     f"event-{ae['event_id']}",
                "label":  role if role != "participated" else "",
                "title":  f"{role}  (weight: {w})",
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.65}},
                "color":  EDGE_COLOUR,
                "width":  width,
                "kind":   "actor-edge",
                "role":   role,
                "weight": w,
            })

        payload = {
            "nodes": vis_nodes,
            "edges": vis_edges,
            "meta": {
                "node_count":  len(vis_nodes),
                "edge_count":  len(vis_edges),
                "actor_count": len(actor_rows),
                "event_count": len(event_rows),
                "filters": {
                    "actor_types": actor_types,
                    "categories":  categories,
                    "min_weight":  min_weight,
                },
            },
        }

        return Response(
            _json.dumps(payload, ensure_ascii=False),
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        db = get_db()

        if request.method == "POST":
            # ── simple password gate ────────────────────────────────────────
            if request.form.get("password") != ADMIN_PASSWORD:
                flash("Incorrect password.", "error")
                return redirect(url_for("admin"))

            action = request.form.get("action", "")

            # ── ingest artifact ─────────────────────────────────────────────
            if action == "add_artifact":
                title       = request.form.get("title", "").strip()
                description = request.form.get("description", "").strip()
                atype       = request.form.get("type", "document")
                date        = request.form.get("date", "").strip() or None
                location    = request.form.get("location", "").strip() or None
                latitude    = request.form.get("latitude", "").strip() or None
                longitude   = request.form.get("longitude", "").strip() or None
                tags        = request.form.get("tags", "").strip() or None
                source      = request.form.get("source", "unverified")
                event_id    = request.form.get("event_id", "").strip() or None

                if not title:
                    flash("Artifact title is required.", "error")
                    return redirect(url_for("admin"))

                # ── file upload ─────────────────────────────────────────────
                file_path = None
                thumbnail = None
                uploaded  = request.files.get("file")

                if uploaded and uploaded.filename:
                    ext = uploaded.filename.rsplit(".", 1)[-1].lower()
                    if ext not in ALLOWED_EXTENSIONS:
                        flash(f"File type '.{ext}' is not allowed.", "error")
                        return redirect(url_for("admin"))

                    subdir     = ALLOWED_EXTENSIONS[ext]
                    safe_name  = f"{uuid.uuid4().hex}_{secure_filename(uploaded.filename)}"
                    dest_dir   = MEDIA_DIR / subdir
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_path  = dest_dir / safe_name
                    uploaded.save(str(dest_path))
                    file_path  = f"media/{subdir}/{safe_name}"

                    # Generate thumbnail for image uploads
                    if ext in IMAGE_EXTENSIONS:
                        try:
                            from PIL import Image
                            thumb_name = f"thumb_{safe_name}"
                            thumb_path = dest_dir / thumb_name
                            with Image.open(str(dest_path)) as img:
                                img.thumbnail((400, 400))
                                img.save(str(thumb_path))
                            thumbnail = f"media/{subdir}/{thumb_name}"
                        except Exception:
                            thumbnail = file_path  # fallback to original

                db.execute("""
                    INSERT INTO artifacts
                        (title, description, type, date, location,
                         latitude, longitude, tags, source,
                         file_path, thumbnail, event_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title, description, atype, date, location,
                    float(latitude)  if latitude  else None,
                    float(longitude) if longitude else None,
                    tags, source, file_path, thumbnail,
                    int(event_id) if event_id else None,
                ))
                db.commit()
                flash(f"Artifact '{title}' added successfully.", "success")
                return redirect(url_for("admin"))

            # ── add event ────────────────────────────────────────────────────
            if action == "add_event":
                title    = request.form.get("ev_title", "").strip()
                summary  = request.form.get("ev_summary", "").strip() or None
                date     = request.form.get("ev_date", "").strip() or None
                location = request.form.get("ev_location", "").strip() or None
                lat      = request.form.get("ev_latitude", "").strip() or None
                lon      = request.form.get("ev_longitude", "").strip() or None
                category = request.form.get("ev_category", "Other")

                if not title:
                    flash("Event title is required.", "error")
                    return redirect(url_for("admin"))

                db.execute("""
                    INSERT INTO events
                        (title, summary, date, location, latitude, longitude, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (title, summary, date, location,
                      float(lat) if lat else None,
                      float(lon) if lon else None,
                      category))
                db.commit()
                flash(f"Event '{title}' added successfully.", "success")
                return redirect(url_for("admin"))

            # ── add actor ────────────────────────────────────────────────────
            if action == "add_actor":
                name        = request.form.get("ac_name", "").strip()
                atype       = request.form.get("ac_type", "person")
                description = request.form.get("ac_description", "").strip() or None

                if not name:
                    flash("Actor name is required.", "error")
                    return redirect(url_for("admin"))

                db.execute(
                    "INSERT INTO actors (name, type, description) VALUES (?, ?, ?)",
                    (name, atype, description),
                )
                db.commit()
                flash(f"Actor '{name}' added successfully.", "success")
                return redirect(url_for("admin"))

        # ── GET — build admin dashboard data ───────────────────────────────
        events_list = db.execute("""
            SELECT event_id, title, date, category FROM events ORDER BY date DESC
        """).fetchall()

        recent_artifacts = db.execute("""
            SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                   e.title AS event_title
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            ORDER  BY a.created_at DESC
            LIMIT  10
        """).fetchall()

        stats = {
            "artifacts": db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
            "events":    db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "actors":    db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
        }

        event_categories = [
            "Election","Security","Civil Unrest","Legislative",
            "Economic","Diplomatic","Military","Social","Other",
        ]

        actor_types_list = ["person","institution","government","movement","media","paramilitary","other"]

        actors_list = db.execute(
            "SELECT actor_id, name, type FROM actors ORDER BY name"
        ).fetchall()

        return render_template(
            "admin.html",
            events=events_list,
            recent_artifacts=recent_artifacts,
            stats=stats,
            event_categories=event_categories,
            actor_types_list=actor_types_list,
            actors_list=actors_list,
            admin_password=ADMIN_PASSWORD,
        )

    # -----------------------------------------------------------------------
    # Route: Artifact detail — Phase 4: Circle of Evidence
    # -----------------------------------------------------------------------

    @app.route("/artifact/<int:artifact_id>")
    def artifact_detail(artifact_id: int):
        db  = get_db()

        # Core artifact + its parent event in one query
        artifact = db.execute("""
            SELECT a.*,
                   e.title      AS event_title,
                   e.event_id   AS linked_event_id,
                   e.date       AS event_date,
                   e.category   AS event_category,
                   e.location   AS event_location,
                   e.summary    AS event_summary
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            WHERE  a.artifact_id = ?
        """, (artifact_id,)).fetchone()

        if not artifact:
            return render_template("archive.html", page="404"), 404

        # Circle of Evidence: all actors involved in the parent event
        circle_of_evidence = []
        if artifact["linked_event_id"]:
            circle_of_evidence = db.execute("""
                SELECT ac.actor_id, ac.name, ac.type, ac.description,
                       ae.role
                FROM   actor_events ae
                JOIN   actors ac ON ac.actor_id = ae.actor_id
                WHERE  ae.event_id = ?
                ORDER  BY ac.type, ac.name
            """, (artifact["linked_event_id"],)).fetchall()

        # Sibling artifacts: other artifacts in the same event
        siblings = []
        if artifact["linked_event_id"]:
            siblings = db.execute("""
                SELECT artifact_id, title, type, source, date, thumbnail
                FROM   artifacts
                WHERE  event_id = ?
                  AND  artifact_id != ?
                ORDER  BY date
            """, (artifact["linked_event_id"], artifact_id)).fetchall()

        # Tag-based related artifacts (shares at least one tag, different event)
        tag_related = []
        if artifact["tags"]:
            tags = [t.strip() for t in artifact["tags"].split(",") if t.strip()]
            if tags:
                # Build LIKE conditions for each tag
                like_clauses = " OR ".join(
                    ["a.tags LIKE ?"] * len(tags)
                )
                like_params  = [f"%{t}%" for t in tags]
                tag_related  = db.execute(f"""
                    SELECT DISTINCT a.artifact_id, a.title, a.type,
                                    a.source, a.date,
                                    e.title AS event_title, e.event_id
                    FROM   artifacts a
                    LEFT   JOIN events e ON e.event_id = a.event_id
                    WHERE  ({like_clauses})
                      AND  a.artifact_id != ?
                      AND  (a.event_id != ? OR a.event_id IS NULL)
                    ORDER  BY a.date DESC
                    LIMIT  6
                """, (*like_params, artifact_id,
                      artifact["linked_event_id"] or -1)).fetchall()

        return render_template(
            "asset.html",
            artifact=artifact,
            circle_of_evidence=circle_of_evidence,
            siblings=siblings,
            tag_related=tag_related,
        )

    # -----------------------------------------------------------------------
    # Route: Event detail — Phase 4: Deep relationships + related events
    # -----------------------------------------------------------------------

    @app.route("/event/<int:event_id>")
    def event_detail(event_id: int):
        db  = get_db()

        event = db.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not event:
            return render_template("archive.html", page="404"), 404

        # All artifacts for this event, with source metadata
        artifacts = db.execute("""
            SELECT artifact_id, title, type, source, date, thumbnail, description
            FROM   artifacts
            WHERE  event_id = ?
            ORDER  BY date ASC, type
        """, (event_id,)).fetchall()

        # All actors with their role in this specific event
        actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   ae.role
            FROM   actor_events ae
            JOIN   actors ac ON ac.actor_id = ae.actor_id
            WHERE  ae.event_id = ?
            ORDER  BY ac.type, ac.name
        """, (event_id,)).fetchall()

        # Source breakdown for this event's evidence
        source_breakdown = db.execute("""
            SELECT source, COUNT(*) AS cnt
            FROM   artifacts
            WHERE  event_id = ? AND source IS NOT NULL
            GROUP  BY source
            ORDER  BY cnt DESC
        """, (event_id,)).fetchall()

        # Related events: share at least one actor (narrative chain)
        related_by_actor = db.execute("""
            SELECT DISTINCT e.event_id, e.title, e.date, e.category,
                            e.location,
                            COUNT(DISTINCT ae2.actor_id) AS shared_actors
            FROM   actor_events ae1
            JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                                   AND ae2.event_id  != ae1.event_id
            JOIN   events e ON e.event_id = ae2.event_id
            WHERE  ae1.event_id = ?
            GROUP  BY e.event_id
            ORDER  BY shared_actors DESC, e.date
            LIMIT  5
        """, (event_id,)).fetchall()

        # Related events: share tags (category-level narrative proximity)
        related_by_category = db.execute("""
            SELECT event_id, title, date, category, location
            FROM   events
            WHERE  category = ?
              AND  event_id != ?
            ORDER  BY date
            LIMIT  4
        """, (event["category"], event_id)).fetchall() if event["category"] else []

        # Shared actor details for tooltip context
        # Build a map: event_id → list of shared actor names
        shared_actor_map: dict[int, list[str]] = {}
        if related_by_actor:
            related_ids = [r["event_id"] for r in related_by_actor]
            actor_names = db.execute(f"""
                SELECT ae2.event_id, ac.name
                FROM   actor_events ae1
                JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                                       AND ae2.event_id IN ({','.join('?'*len(related_ids))})
                JOIN   actors ac ON ac.actor_id = ae1.actor_id
                WHERE  ae1.event_id = ?
                ORDER  BY ae2.event_id, ac.name
            """, (*related_ids, event_id)).fetchall()
            for row in actor_names:
                shared_actor_map.setdefault(row["event_id"], []).append(row["name"])

        return render_template(
            "event.html",
            event=event,
            artifacts=artifacts,
            actors=actors,
            source_breakdown=source_breakdown,
            related_by_actor=related_by_actor,
            related_by_category=related_by_category,
            shared_actor_map=shared_actor_map,
        )

    # -----------------------------------------------------------------------
    # Route: Actor detail — Phase 4: Full evidence footprint
    # -----------------------------------------------------------------------

    @app.route("/actor/<int:actor_id>")
    def actor_detail(actor_id: int):
        db  = get_db()

        actor = db.execute(
            "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
        ).fetchone()
        if not actor:
            return render_template("archive.html", page="404"), 404

        # All events this actor participated in, with their role and artifact counts
        events = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   ae.role,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ae.actor_id = ?
            GROUP  BY e.event_id
            ORDER  BY e.date ASC
        """, (actor_id,)).fetchall()

        # Full artifact footprint: all artifacts from every linked event
        artifact_footprint = db.execute("""
            SELECT DISTINCT a.artifact_id, a.title, a.type, a.source,
                            a.date, a.thumbnail, a.description,
                            e.title AS event_title, e.event_id
            FROM   actor_events ae
            JOIN   artifacts a ON a.event_id = ae.event_id
            JOIN   events e    ON e.event_id  = ae.event_id
            WHERE  ae.actor_id = ?
            ORDER  BY a.date ASC
        """, (actor_id,)).fetchall()

        # Co-actors: other actors who share events with this actor
        co_actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type,
                   COUNT(DISTINCT ae2.event_id) AS shared_events,
                   GROUP_CONCAT(DISTINCT e.title) AS shared_event_names
            FROM   actor_events ae1
            JOIN   actor_events ae2 ON ae2.event_id  = ae1.event_id
                                   AND ae2.actor_id != ae1.actor_id
            JOIN   actors ac ON ac.actor_id = ae2.actor_id
            JOIN   events e  ON e.event_id  = ae1.event_id
            WHERE  ae1.actor_id = ?
            GROUP  BY ac.actor_id
            ORDER  BY shared_events DESC
            LIMIT  8
        """, (actor_id,)).fetchall()

        # Role timeline: each role this actor has held across events
        role_timeline = db.execute("""
            SELECT ae.role, e.event_id, e.title, e.date, e.category
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            WHERE  ae.actor_id = ?
              AND  ae.role IS NOT NULL
            ORDER  BY e.date
        """, (actor_id,)).fetchall()

        return render_template(
            "actor.html",
            actor=actor,
            events=events,
            artifact_footprint=artifact_footprint,
            co_actors=co_actors,
            role_timeline=role_timeline,
        )

    # -----------------------------------------------------------------------
    # Routes: /cases — Phase 8: Case Workspaces
    # -----------------------------------------------------------------------

    @app.route("/cases")
    def cases():
        db = get_db()
        cases_list = db.execute("""
            SELECT c.case_id, c.title, c.description, c.status, c.created_at,
                   COUNT(DISTINCT ca.artifact_id) AS artifact_count,
                   COUNT(DISTINCT ce.event_id)    AS event_count,
                   COUNT(DISTINCT cac.actor_id)   AS actor_count
            FROM   cases c
            LEFT JOIN case_artifacts ca  ON ca.case_id = c.case_id
            LEFT JOIN case_events    ce  ON ce.case_id = c.case_id
            LEFT JOIN case_actors    cac ON cac.case_id = c.case_id
            GROUP BY c.case_id
            ORDER BY c.created_at DESC
        """).fetchall()
        return render_template("cases.html", cases=cases_list)

    @app.route("/cases/new", methods=["POST"])
    def case_new():
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        status      = request.form.get("status", "active")
        if not title:
            flash("Case title is required.", "error")
            return redirect(url_for("cases"))
        db = get_db()
        cur = db.execute(
            "INSERT INTO cases (title, description, status) VALUES (?, ?, ?)",
            (title, description, status),
        )
        db.commit()
        flash(f"Case '{title}' created.", "success")
        return redirect(url_for("case_detail", case_id=cur.lastrowid))

    @app.route("/cases/<int:case_id>")
    def case_detail(case_id: int):
        db = get_db()
        case = db.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if not case:
            flash("Case not found.", "error")
            return redirect(url_for("cases"))

        artifacts = db.execute("""
            SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                   a.description, a.thumbnail,
                   e.title AS event_title, e.event_id,
                   ca.pinned_at, ca.note, ca.sequence_order, ca.transition_note
            FROM   case_artifacts ca
            JOIN   artifacts a ON a.artifact_id = ca.artifact_id
            LEFT JOIN events e ON e.event_id = a.event_id
            WHERE  ca.case_id = ?
            ORDER  BY ca.pinned_at DESC
        """, (case_id,)).fetchall()

        events = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   COUNT(a.artifact_id) AS artifact_count,
                   ce.pinned_at, ce.note, ce.sequence_order, ce.transition_note
            FROM   case_events ce
            JOIN   events e ON e.event_id = ce.event_id
            LEFT JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ce.case_id = ?
            GROUP  BY e.event_id
            ORDER  BY ce.pinned_at DESC
        """, (case_id,)).fetchall()

        actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   COUNT(DISTINCT ae.event_id) AS event_count,
                   cac.pinned_at, cac.note, cac.sequence_order, cac.transition_note
            FROM   case_actors cac
            JOIN   actors ac ON ac.actor_id = cac.actor_id
            LEFT JOIN actor_events ae ON ae.actor_id = ac.actor_id
            WHERE  cac.case_id = ?
            GROUP  BY ac.actor_id
            ORDER  BY cac.pinned_at DESC
        """, (case_id,)).fetchall()

        all_cases = db.execute(
            "SELECT case_id, title, status FROM cases ORDER BY created_at DESC"
        ).fetchall()

        return render_template(
            "case_detail.html",
            case=case,
            artifacts=artifacts,
            events=events,
            actors=actors,
            all_cases=all_cases,
        )

    @app.route("/cases/<int:case_id>/edit", methods=["POST"])
    def case_edit(case_id: int):
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        status      = request.form.get("status", "active")
        if not title:
            flash("Case title is required.", "error")
            return redirect(url_for("case_detail", case_id=case_id))
        db = get_db()
        db.execute(
            "UPDATE cases SET title=?, description=?, status=? WHERE case_id=?",
            (title, description, status, case_id),
        )
        db.commit()
        flash("Case updated.", "success")
        return redirect(url_for("case_detail", case_id=case_id))

    @app.route("/cases/<int:case_id>/delete", methods=["POST"])
    def case_delete(case_id: int):
        db = get_db()
        case = db.execute(
            "SELECT title FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if case:
            db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
            db.commit()
            flash(f"Case '{case['title']}' deleted.", "success")
        return redirect(url_for("cases"))

    # ── Pin / Unpin API ──────────────────────────────────────────────────────

    # -----------------------------------------------------------------------
    # Phase 10: Narrative Threader — sequence / transition APIs + briefing
    # -----------------------------------------------------------------------

    @app.route("/api/sequence", methods=["POST"])
    def api_sequence():
        """
        Persist a sequence_order for one pinned entity.
        Body JSON: { case_id, entity_type, entity_id, sequence_order }
        Returns JSON: { ok: true }

        sequence_order=null clears the ordering (unthreaded state).
        Accepts a batch list via items=[{entity_type, entity_id, sequence_order}]
        for full-board drag-and-drop saves.
        """
        from flask import jsonify
        data = request.get_json(silent=True) or {}

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }

        db      = get_db()
        case_id = int(data.get("case_id", 0))

        # ── batch mode ─────────────────────────────────────────────────────
        items = data.get("items")
        if items:
            for item in items:
                et = item.get("entity_type", "")
                ei = item.get("entity_id")
                so = item.get("sequence_order")          # None clears it
                if et not in TABLE_MAP or not ei:
                    continue
                table, id_col = TABLE_MAP[et]
                db.execute(
                    f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
                    (so, case_id, int(ei)),
                )
            db.commit()
            return jsonify({"ok": True, "updated": len(items)})

        # ── single mode ─────────────────────────────────────────────────────
        entity_type    = data.get("entity_type", "")
        entity_id      = int(data.get("entity_id", 0))
        sequence_order = data.get("sequence_order")      # None clears it

        if entity_type not in TABLE_MAP:
            return jsonify({"error": "Unknown entity type"}), 400

        table, id_col = TABLE_MAP[entity_type]
        db.execute(
            f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
            (sequence_order, case_id, entity_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.route("/api/transition", methods=["POST"])
    def api_transition():
        """
        Persist the transition_note bridging text that follows a pinned entity.
        Body JSON: { case_id, entity_type, entity_id, transition_note }
        Returns JSON: { ok: true }
        """
        from flask import jsonify
        data = request.get_json(silent=True) or {}

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }

        entity_type     = data.get("entity_type", "")
        entity_id       = int(data.get("entity_id", 0))
        case_id         = int(data.get("case_id", 0))
        transition_note = (data.get("transition_note") or "").strip() or None

        if entity_type not in TABLE_MAP:
            return jsonify({"error": "Unknown entity type"}), 400

        table, id_col = TABLE_MAP[entity_type]
        db = get_db()
        db.execute(
            f"UPDATE {table} SET transition_note=? WHERE case_id=? AND {id_col}=?",
            (transition_note, case_id, entity_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.route("/api/auto-sequence/<int:case_id>", methods=["POST"])
    def api_auto_sequence(case_id: int):
        """
        Algorithmic Sequencing Suggestion:
        Assigns sequence_order to ALL pinned entities based on the best
        available temporal signal, falling back to a stable heuristic.

        PRIORITY ORDER for ordering:
        1. Events: sorted by their own date (ISO string sort).  Events with
           no date are appended after dated ones.
        2. Artifacts: sorted by their date; if null, by the date of their
           parent event; otherwise appended last.
        3. Actors: no intrinsic date — sorted by the earliest event date
           they participate in within this case; otherwise appended last.

        All three entity types are merged into one global sequence — the
        chronological backbone is events, artifacts weave in around their
        event date, actors anchor to their earliest relevant event.

        Returns: { ok, items: [{entity_type, entity_id, sequence_order,
                                effective_date}] }
        """
        from flask import jsonify
        import datetime as _dt

        db   = get_db()
        case = db.execute(
            "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404

        SORT_NONE = "9999-99-99"   # sentinel — pushes undated items to end

        # ── Events ────────────────────────────────────────────────────────
        ev_rows = db.execute("""
            SELECT e.event_id, e.date
            FROM   case_events ce
            JOIN   events e ON e.event_id = ce.event_id
            WHERE  ce.case_id = ?
        """, (case_id,)).fetchall()

        events_seq = [
            ("event", r["event_id"], r["date"] or SORT_NONE)
            for r in ev_rows
        ]

        # ── Artifacts ─────────────────────────────────────────────────────
        ar_rows = db.execute("""
            SELECT a.artifact_id, a.date AS a_date, e.date AS e_date
            FROM   case_artifacts ca
            JOIN   artifacts a ON a.artifact_id = ca.artifact_id
            LEFT JOIN events e ON e.event_id = a.event_id
            WHERE  ca.case_id = ?
        """, (case_id,)).fetchall()

        artifacts_seq = []
        for r in ar_rows:
            eff_date = r["a_date"] or r["e_date"] or SORT_NONE
            artifacts_seq.append(("artifact", r["artifact_id"], eff_date))

        # ── Actors ────────────────────────────────────────────────────────
        # Anchor each actor to the earliest event date they share with this case
        ac_rows = db.execute("""
            SELECT ac.actor_id,
                   MIN(COALESCE(e.date, ?)) AS earliest_event_date
            FROM   case_actors cac
            JOIN   actors ac ON ac.actor_id = cac.actor_id
            LEFT JOIN actor_events ae ON ae.actor_id = ac.actor_id
            LEFT JOIN case_events  ce ON ce.event_id = ae.event_id
                                      AND ce.case_id = cac.case_id
            LEFT JOIN events e ON e.event_id = ae.event_id
                                AND ce.case_id = ?
            WHERE  cac.case_id = ?
            GROUP  BY ac.actor_id
        """, (SORT_NONE, case_id, case_id)).fetchall()

        actors_seq = [
            ("actor", r["actor_id"], r["earliest_event_date"] or SORT_NONE)
            for r in ac_rows
        ]

        # ── Merge + sort ───────────────────────────────────────────────────
        all_items = events_seq + artifacts_seq + actors_seq
        all_items.sort(key=lambda x: (x[2], x[0], x[1]))   # date, type, id

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }

        result = []
        for seq_num, (etype, eid, eff_date) in enumerate(all_items, start=1):
            table, id_col = TABLE_MAP[etype]
            db.execute(
                f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
                (seq_num, case_id, eid),
            )
            result.append({
                "entity_type":    etype,
                "entity_id":      eid,
                "sequence_order": seq_num,
                "effective_date": eff_date if eff_date != SORT_NONE else None,
            })

        db.commit()
        return jsonify({"ok": True, "items": result})

    @app.route("/cases/<int:case_id>/briefing")
    def case_briefing(case_id: int):
        """
        Intelligence Briefing view — all sequenced pins in narrative order
        with transition notes as connective tissue.  Unsequenced items are
        appended at the end under a separator.
        """
        db   = get_db()
        case = db.execute(
            "SELECT * FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            flash("Case not found.", "error")
            return redirect(url_for("cases"))

        # ── Pull sequenced items from all three tables ─────────────────────
        #    We need a unified list ordered by sequence_order, then pinned_at.
        #    We union the three tables and join to their entity tables.

        raw_events = db.execute("""
            SELECT 'event' AS kind,
                   e.event_id AS entity_id,
                   e.title, e.date, e.category, e.location, e.summary,
                   NULL AS type, NULL AS source, NULL AS description_extra,
                   ce.note, ce.sequence_order, ce.transition_note, ce.pinned_at
            FROM case_events ce
            JOIN events e ON e.event_id = ce.event_id
            WHERE ce.case_id = ?
        """, (case_id,)).fetchall()

        raw_actors = db.execute("""
            SELECT 'actor' AS kind,
                   ac.actor_id AS entity_id,
                   ac.name AS title, NULL AS date, NULL AS category,
                   NULL AS location, ac.description AS summary,
                   ac.type, NULL AS source, NULL AS description_extra,
                   cac.note, cac.sequence_order, cac.transition_note, cac.pinned_at
            FROM case_actors cac
            JOIN actors ac ON ac.actor_id = cac.actor_id
            WHERE cac.case_id = ?
        """, (case_id,)).fetchall()

        raw_artifacts = db.execute("""
            SELECT 'artifact' AS kind,
                   a.artifact_id AS entity_id,
                   a.title, a.date, NULL AS category, a.location,
                   a.description AS summary,
                   a.type, a.source, e.title AS description_extra,
                   ca.note, ca.sequence_order, ca.transition_note, ca.pinned_at
            FROM case_artifacts ca
            JOIN artifacts a ON a.artifact_id = ca.artifact_id
            LEFT JOIN events e ON e.event_id = a.event_id
            WHERE ca.case_id = ?
        """, (case_id,)).fetchall()

        all_items = (
            [dict(r) for r in raw_events]
            + [dict(r) for r in raw_actors]
            + [dict(r) for r in raw_artifacts]
        )

        # Sort: sequenced items first (by sequence_order), then unsequenced
        # (by pinned_at).  None sorts after integers in Python so we use a
        # sentinel.
        def _sort_key(item):
            so = item["sequence_order"]
            return (0 if so is not None else 1, so if so is not None else 0, item["pinned_at"] or "")

        all_items.sort(key=_sort_key)

        sequenced   = [i for i in all_items if i["sequence_order"] is not None]
        unsequenced = [i for i in all_items if i["sequence_order"] is None]

        # ── Count stats for header ─────────────────────────────────────────
        stats = {
            "events":    sum(1 for i in all_items if i["kind"] == "event"),
            "actors":    sum(1 for i in all_items if i["kind"] == "actor"),
            "artifacts": sum(1 for i in all_items if i["kind"] == "artifact"),
            "total":     len(all_items),
            "sequenced": len(sequenced),
        }

        import datetime
        generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        return render_template(
            "briefing.html",
            case=case,
            sequenced=sequenced,
            unsequenced=unsequenced,
            stats=stats,
            generated_at=generated_at,
        )

    @app.route("/api/pin", methods=["POST"])
    def api_pin():
        """
        Toggle-pin an entity to/from a case.
        Body (form or JSON): case_id, entity_type (artifact|event|actor), entity_id, note?
        Returns JSON: { pinned: bool, case_id, entity_type, entity_id }
        """
        from flask import jsonify

        data = request.get_json(silent=True) or request.form
        try:
            case_id     = int(data.get("case_id", 0))
            entity_type = data.get("entity_type", "")
            entity_id   = int(data.get("entity_id", 0))
            note        = (data.get("note") or "").strip() or None
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid parameters"}), 400

        if entity_type not in ("artifact", "event", "actor"):
            return jsonify({"error": "Unknown entity type"}), 400

        db = get_db()

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }
        table, id_col = TABLE_MAP[entity_type]

        existing = db.execute(
            f"SELECT 1 FROM {table} WHERE case_id=? AND {id_col}=?",
            (case_id, entity_id),
        ).fetchone()

        if existing:
            db.execute(
                f"DELETE FROM {table} WHERE case_id=? AND {id_col}=?",
                (case_id, entity_id),
            )
            db.commit()
            return jsonify({"pinned": False, "case_id": case_id,
                            "entity_type": entity_type, "entity_id": entity_id})
        else:
            db.execute(
                f"INSERT INTO {table} (case_id, {id_col}, note) VALUES (?, ?, ?)",
                (case_id, entity_id, note),
            )
            db.commit()
            return jsonify({"pinned": True, "case_id": case_id,
                            "entity_type": entity_type, "entity_id": entity_id})

    @app.route("/api/pin/status")
    def api_pin_status():
        """
        Returns pinned status of an entity across all cases.
        Query params: entity_type, entity_id
        """
        from flask import jsonify

        entity_type = request.args.get("entity_type", "")
        try:
            entity_id = int(request.args.get("entity_id", 0))
        except ValueError:
            return jsonify({"error": "Invalid entity_id"}), 400

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }
        if entity_type not in TABLE_MAP:
            return jsonify({"error": "Unknown entity type"}), 400

        table, id_col = TABLE_MAP[entity_type]
        db = get_db()

        pinned_in = db.execute(
            f"""SELECT c.case_id, c.title, c.status
                FROM {table} j
                JOIN cases c ON c.case_id = j.case_id
                WHERE j.{id_col} = ?""",
            (entity_id,),
        ).fetchall()

        return jsonify({
            "entity_type": entity_type,
            "entity_id":   entity_id,
            "pinned_in":   [dict(r) for r in pinned_in],
        })

    @app.route("/api/pin/note", methods=["POST"])
    def api_pin_note():
        """Update the note on a pinned entity."""
        from flask import jsonify
        data      = request.get_json(silent=True) or request.form
        case_id   = int(data.get("case_id", 0))
        entity_type = data.get("entity_type", "")
        entity_id = int(data.get("entity_id", 0))
        note      = (data.get("note") or "").strip() or None

        TABLE_MAP = {
            "artifact": ("case_artifacts", "artifact_id"),
            "event":    ("case_events",    "event_id"),
            "actor":    ("case_actors",    "actor_id"),
        }
        if entity_type not in TABLE_MAP:
            return jsonify({"error": "Unknown entity type"}), 400

        table, id_col = TABLE_MAP[entity_type]
        db = get_db()
        db.execute(
            f"UPDATE {table} SET note=? WHERE case_id=? AND {id_col}=?",
            (note, case_id, entity_id),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.route("/api/cases-list")
    def api_cases_list():
        """Lightweight JSON list of all cases for the pin widget dropdown."""
        from flask import jsonify
        db = get_db()
        rows = db.execute(
            "SELECT case_id, title, status FROM cases ORDER BY created_at DESC"
        ).fetchall()
        return jsonify({"cases": [dict(r) for r in rows]})

    # -----------------------------------------------------------------------
    # Phase 9: Inference Engine — Heat Scoring + Commonality Suggestions
    # -----------------------------------------------------------------------

    def _compute_heat_score(db, case_id: int) -> dict:
        """
        Calculates three sub-scores and a composite Heat Score for a case.

        DENSITY  = artifact_count / max(actor_count, 1)
                   Capped at 5.0, normalised to 0–100.
                   Rationale: high artifact-per-actor ratio = rich evidentiary
                   case; denominator floor prevents div-by-zero on empty cases.

        VOLATILITY = events within a 7-day sliding window / total events
                     We find the densest 7-day window by sorting event dates
                     and using a two-pointer over the sorted list.
                     Normalised to 0–100.  A score of 100 means ALL events
                     cluster within one week — maximum temporal concentration.

        CONNECTIVITY = cross-linked ratio.
                       "Cross-linked" = an entity that appears in MORE THAN ONE
                       entity-type bucket (e.g. an event that both has pinned
                       actors AND pinned artifacts on this case).
                       Formula: cross_linked / max(total_pins, 1) × 100.

        COMPOSITE = 0.35 * density + 0.35 * volatility + 0.30 * connectivity
        These weights reflect the investigative priority order: evidence density
        and temporal clustering are equally primary signals; structural
        connectivity is a secondary quality check.
        """
        import datetime

        # ── Counts ──────────────────────────────────────────────────────────
        actor_count    = db.execute(
            "SELECT COUNT(*) FROM case_actors    WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        event_count    = db.execute(
            "SELECT COUNT(*) FROM case_events    WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        artifact_count = db.execute(
            "SELECT COUNT(*) FROM case_artifacts WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        total_pins = actor_count + event_count + artifact_count

        # ── DENSITY ─────────────────────────────────────────────────────────
        raw_density   = artifact_count / max(actor_count, 1)
        density_norm  = min(raw_density / 5.0, 1.0) * 100          # cap at 5:1

        # ── VOLATILITY (densest 7-day window) ───────────────────────────────
        volatility_norm = 0.0
        if event_count > 1:
            dated_events = db.execute("""
                SELECT e.date
                FROM   case_events ce
                JOIN   events e ON e.event_id = ce.event_id
                WHERE  ce.case_id = ? AND e.date IS NOT NULL
                ORDER  BY e.date ASC
            """, (case_id,)).fetchall()

            if len(dated_events) >= 2:
                dates = []
                for row in dated_events:
                    try:
                        dates.append(datetime.date.fromisoformat(row["date"]))
                    except (ValueError, TypeError):
                        pass

                if len(dates) >= 2:
                    # Two-pointer: find maximum events within any 7-day window
                    max_in_window = 1
                    left = 0
                    for right in range(len(dates)):
                        while (dates[right] - dates[left]).days > 7:
                            left += 1
                        max_in_window = max(max_in_window, right - left + 1)
                    volatility_norm = (max_in_window / len(dates)) * 100

        # ── CONNECTIVITY (cross-linked ratio) ────────────────────────────────
        connectivity_norm = 0.0
        if total_pins > 0:
            # Events that have AT LEAST ONE pinned artifact AND one pinned actor
            cross_events = db.execute("""
                SELECT COUNT(DISTINCT ce.event_id)
                FROM   case_events ce
                JOIN   artifacts a   ON a.event_id = ce.event_id
                JOIN   case_artifacts ca ON ca.artifact_id = a.artifact_id
                                        AND ca.case_id = ce.case_id
                WHERE  ce.case_id = ?
            """, (case_id,)).fetchone()[0]

            # Actors that share at least one event with another pinned actor
            cross_actors = db.execute("""
                SELECT COUNT(DISTINCT cac1.actor_id)
                FROM   case_actors cac1
                JOIN   actor_events ae1 ON ae1.actor_id = cac1.actor_id
                JOIN   actor_events ae2 ON ae2.event_id = ae1.event_id
                                       AND ae2.actor_id != ae1.actor_id
                JOIN   case_actors cac2 ON cac2.actor_id = ae2.actor_id
                                       AND cac2.case_id = cac1.case_id
                WHERE  cac1.case_id = ?
            """, (case_id,)).fetchone()[0]

            cross_total    = cross_events + cross_actors
            connectivity_norm = min(cross_total / max(total_pins, 1), 1.0) * 100

        # ── Composite ────────────────────────────────────────────────────────
        composite = (
            0.35 * density_norm +
            0.35 * volatility_norm +
            0.30 * connectivity_norm
        )

        return {
            "composite":       round(composite, 1),
            "density":         round(density_norm, 1),
            "volatility":      round(volatility_norm, 1),
            "connectivity":    round(connectivity_norm, 1),
            "raw": {
                "actors":    actor_count,
                "events":    event_count,
                "artifacts": artifact_count,
                "total_pins": total_pins,
            },
        }

    def _compute_suggestions(db, case_id: int) -> list:
        """
        Commonality Algorithm — produces ranked "Suggested Leads":

        STEP 1 — Hidden Events:
          For every actor pinned to the case, fetch all events that actor
          participates in. Exclude events already pinned to the case.
          Tally how many pinned actors link to each hidden event (co-occurrence
          count).

        STEP 2 — Hidden Actors:
          For every event pinned to the case, fetch all actors in those events.
          Exclude actors already pinned. Tally co-occurrence across events.

        STEP 3 — Artifact Trails:
          Fetch artifacts whose event_id matches a pinned event but which are
          not yet pinned. These are "dangling evidence" — high confidence
          because the event is already known.

        RANKING:
          confidence_score = co_occurrence / max_possible_co_occurrence
          confidence label: ≥0.67 → HIGH, ≥0.34 → MEDIUM, else LOW
          Ties broken by artifact_count (more evidence = higher priority).

        Returns top 12 suggestions sorted by confidence desc.
        """
        # ── Fetch current case contents ──────────────────────────────────────
        pinned_actor_ids = {
            r["actor_id"] for r in db.execute(
                "SELECT actor_id FROM case_actors WHERE case_id=?", (case_id,)
            ).fetchall()
        }
        pinned_event_ids = {
            r["event_id"] for r in db.execute(
                "SELECT event_id FROM case_events WHERE case_id=?", (case_id,)
            ).fetchall()
        }
        pinned_artifact_ids = {
            r["artifact_id"] for r in db.execute(
                "SELECT artifact_id FROM case_artifacts WHERE case_id=?", (case_id,)
            ).fetchall()
        }

        suggestions = []
        seen_ids: dict[str, bool] = {}   # dedup key: "event-3", "actor-5", etc.

        # ── STEP 1: Hidden Events via pinned actors ───────────────────────────
        if pinned_actor_ids:
            ph = ",".join("?" * len(pinned_actor_ids))
            hidden_events = db.execute(f"""
                SELECT e.event_id, e.title, e.date, e.category,
                       e.location, e.summary,
                       COUNT(DISTINCT ae.actor_id) AS co_occurrence,
                       COUNT(DISTINCT a.artifact_id) AS artifact_count
                FROM   actor_events ae
                JOIN   events e ON e.event_id = ae.event_id
                LEFT   JOIN artifacts a ON a.event_id = e.event_id
                WHERE  ae.actor_id IN ({ph})
                  AND  e.event_id  NOT IN ({','.join('?' * len(pinned_event_ids)) or 'NULL'})
                GROUP  BY e.event_id
                ORDER  BY co_occurrence DESC, artifact_count DESC
                LIMIT  20
            """, (
                *list(pinned_actor_ids),
                *(list(pinned_event_ids) if pinned_event_ids else []),
            )).fetchall()

            max_co = pinned_actor_ids and max(
                (r["co_occurrence"] for r in hidden_events), default=1
            ) or 1

            for r in hidden_events:
                key = f"event-{r['event_id']}"
                if key in seen_ids:
                    continue
                seen_ids[key] = True
                conf_score = r["co_occurrence"] / max_co
                # Find which pinned actors link to this event
                linked_actors = db.execute(f"""
                    SELECT ac.name FROM actor_events ae
                    JOIN actors ac ON ac.actor_id = ae.actor_id
                    WHERE ae.event_id = ? AND ae.actor_id IN ({ph})
                    LIMIT 4
                """, (r["event_id"], *list(pinned_actor_ids))).fetchall()

                suggestions.append({
                    "type":          "event",
                    "entity_id":     r["event_id"],
                    "title":         r["title"],
                    "date":          r["date"] or "",
                    "category":      r["category"] or "Other",
                    "location":      r["location"] or "",
                    "summary":       (r["summary"] or "")[:160],
                    "artifact_count": r["artifact_count"],
                    "co_occurrence": r["co_occurrence"],
                    "confidence":    round(conf_score * 100),
                    "reason":        f"{r['co_occurrence']} pinned actor{'s' if r['co_occurrence'] != 1 else ''} linked: "
                                     + ", ".join(a["name"] for a in linked_actors),
                    "url":           f"/event/{r['event_id']}",
                })

        # ── STEP 2: Hidden Actors via pinned events ───────────────────────────
        if pinned_event_ids:
            ph = ",".join("?" * len(pinned_event_ids))
            hidden_actors = db.execute(f"""
                SELECT ac.actor_id, ac.name, ac.type, ac.description,
                       COUNT(DISTINCT ae.event_id) AS co_occurrence
                FROM   actor_events ae
                JOIN   actors ac ON ac.actor_id = ae.actor_id
                WHERE  ae.event_id  IN ({ph})
                  AND  ac.actor_id  NOT IN ({','.join('?' * len(pinned_actor_ids)) or 'NULL'})
                GROUP  BY ac.actor_id
                ORDER  BY co_occurrence DESC
                LIMIT  15
            """, (
                *list(pinned_event_ids),
                *(list(pinned_actor_ids) if pinned_actor_ids else []),
            )).fetchall()

            max_co = len(pinned_event_ids) or 1

            for r in hidden_actors:
                key = f"actor-{r['actor_id']}"
                if key in seen_ids:
                    continue
                seen_ids[key] = True
                conf_score = r["co_occurrence"] / max_co

                linked_events = db.execute(f"""
                    SELECT e.title FROM actor_events ae
                    JOIN events e ON e.event_id = ae.event_id
                    WHERE ae.actor_id = ? AND ae.event_id IN ({ph})
                    LIMIT 3
                """, (r["actor_id"], *list(pinned_event_ids))).fetchall()

                suggestions.append({
                    "type":          "actor",
                    "entity_id":     r["actor_id"],
                    "title":         r["name"],
                    "subtype":       r["type"],
                    "description":   (r["description"] or "")[:120],
                    "co_occurrence": r["co_occurrence"],
                    "confidence":    round((conf_score) * 100),
                    "reason":        f"Active in {r['co_occurrence']} pinned event{'s' if r['co_occurrence'] != 1 else ''}: "
                                     + ", ".join(e["title"][:40] for e in linked_events),
                    "url":           f"/actor/{r['actor_id']}",
                })

        # ── STEP 3: Dangling artifact trails ─────────────────────────────────
        if pinned_event_ids:
            ph = ",".join("?" * len(pinned_event_ids))
            dangling_artifacts = db.execute(f"""
                SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                       e.title AS event_title, e.event_id
                FROM   artifacts a
                JOIN   events e ON e.event_id = a.event_id
                WHERE  a.event_id IN ({ph})
                  AND  a.artifact_id NOT IN (
                       SELECT artifact_id FROM case_artifacts WHERE case_id=?
                  )
                ORDER  BY a.date DESC
                LIMIT  10
            """, (*list(pinned_event_ids), case_id)).fetchall()

            for r in dangling_artifacts:
                key = f"artifact-{r['artifact_id']}"
                if key in seen_ids:
                    continue
                seen_ids[key] = True
                suggestions.append({
                    "type":        "artifact",
                    "entity_id":   r["artifact_id"],
                    "title":       r["title"],
                    "subtype":     r["type"],
                    "source":      r["source"] or "unverified",
                    "date":        r["date"] or "",
                    "co_occurrence": 1,
                    "confidence":  85,  # artifact on pinned event = high confidence
                    "reason":      f"Evidence on pinned event: {r['event_title'][:50]}",
                    "url":         f"/artifact/{r['artifact_id']}",
                })

        # ── Sort and label ────────────────────────────────────────────────────
        suggestions.sort(key=lambda s: (-s["confidence"], -s.get("co_occurrence", 0)))

        for s in suggestions:
            c = s["confidence"]
            if c >= 67:
                s["confidence_label"] = "HIGH"
            elif c >= 34:
                s["confidence_label"] = "MEDIUM"
            else:
                s["confidence_label"] = "LOW"

        return suggestions[:12]

    @app.route("/api/suggestions/<int:case_id>")
    def api_suggestions(case_id: int):
        """
        Returns a JSON object with:
          - suggestions: ranked list of hidden entities (events, actors, artifacts)
          - heat:        the full Heat Score breakdown
          - meta:        counts and algorithm parameters
        """
        from flask import jsonify
        db = get_db()

        case = db.execute(
            "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404

        heat        = _compute_heat_score(db, case_id)
        suggestions = _compute_suggestions(db, case_id)

        return jsonify({
            "case_id":     case_id,
            "heat":        heat,
            "suggestions": suggestions,
            "meta": {
                "suggestion_count": len(suggestions),
                "types": {
                    "events":    sum(1 for s in suggestions if s["type"] == "event"),
                    "actors":    sum(1 for s in suggestions if s["type"] == "actor"),
                    "artifacts": sum(1 for s in suggestions if s["type"] == "artifact"),
                },
            },
        })

    @app.route("/artifact/<int:artifact_id>/edit", methods=["GET", "POST"])
    def artifact_edit(artifact_id: int):
        db = get_db()
        artifact = db.execute(
            "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not artifact:
            flash("Artifact not found.", "error")
            return redirect(url_for("admin"))

        events_list = db.execute(
            "SELECT event_id, title, date FROM events ORDER BY date DESC"
        ).fetchall()

        if request.method == "POST":
            if request.form.get("password") != ADMIN_PASSWORD:
                flash("Incorrect password.", "error")
                return redirect(url_for("artifact_edit", artifact_id=artifact_id))

            title       = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip() or None
            atype       = request.form.get("type", artifact["type"])
            date        = request.form.get("date", "").strip() or None
            location    = request.form.get("location", "").strip() or None
            latitude    = request.form.get("latitude", "").strip() or None
            longitude   = request.form.get("longitude", "").strip() or None
            tags        = request.form.get("tags", "").strip() or None
            source      = request.form.get("source", artifact["source"])
            event_id    = request.form.get("event_id", "").strip() or None

            if not title:
                flash("Title is required.", "error")
                return redirect(url_for("artifact_edit", artifact_id=artifact_id))

            db.execute("""
                UPDATE artifacts
                SET title=?, description=?, type=?, date=?, location=?,
                    latitude=?, longitude=?, tags=?, source=?, event_id=?
                WHERE artifact_id=?
            """, (
                title, description, atype, date, location,
                float(latitude)  if latitude  else None,
                float(longitude) if longitude else None,
                tags, source,
                int(event_id) if event_id else None,
                artifact_id,
            ))
            db.commit()
            flash(f"Artifact '{title}' updated.", "success")
            return redirect(url_for("artifact_detail", artifact_id=artifact_id))

        return render_template(
            "edit_artifact.html",
            artifact=artifact,
            events=events_list,
        )

    @app.route("/artifact/<int:artifact_id>/delete", methods=["POST"])
    def artifact_delete(artifact_id: int):
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("artifact_detail", artifact_id=artifact_id))
        db = get_db()
        artifact = db.execute(
            "SELECT title, file_path, thumbnail FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if not artifact:
            flash("Artifact not found.", "error")
            return redirect(url_for("admin"))

        # Optionally delete physical files
        for fpath in (artifact["file_path"], artifact["thumbnail"]):
            if fpath:
                try:
                    full = BASE_DIR / fpath
                    if full.exists():
                        full.unlink()
                except Exception:
                    pass

        db.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
        db.commit()
        flash(f"Artifact '{artifact['title']}' deleted.", "success")
        return redirect(url_for("admin"))

    # ── Edit / Delete: Events ────────────────────────────────────────────────

    @app.route("/event/<int:event_id>/edit", methods=["GET", "POST"])
    def event_edit(event_id: int):
        db = get_db()
        event = db.execute(
            "SELECT * FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not event:
            flash("Event not found.", "error")
            return redirect(url_for("events"))

        event_categories = [
            "Election","Security","Civil Unrest","Legislative",
            "Economic","Diplomatic","Military","Social","Other",
        ]

        if request.method == "POST":
            if request.form.get("password") != ADMIN_PASSWORD:
                flash("Incorrect password.", "error")
                return redirect(url_for("event_edit", event_id=event_id))

            title    = request.form.get("title", "").strip()
            summary  = request.form.get("summary", "").strip() or None
            date     = request.form.get("date", "").strip() or None
            location = request.form.get("location", "").strip() or None
            lat      = request.form.get("latitude", "").strip() or None
            lon      = request.form.get("longitude", "").strip() or None
            category = request.form.get("category", event["category"])

            if not title:
                flash("Title is required.", "error")
                return redirect(url_for("event_edit", event_id=event_id))

            db.execute("""
                UPDATE events
                SET title=?, summary=?, date=?, location=?,
                    latitude=?, longitude=?, category=?
                WHERE event_id=?
            """, (
                title, summary, date, location,
                float(lat) if lat else None,
                float(lon) if lon else None,
                category, event_id,
            ))
            db.commit()
            flash(f"Event '{title}' updated.", "success")
            return redirect(url_for("event_detail", event_id=event_id))

        return render_template(
            "edit_event.html",
            event=event,
            event_categories=event_categories,
        )

    @app.route("/event/<int:event_id>/delete", methods=["POST"])
    def event_delete(event_id: int):
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("event_detail", event_id=event_id))
        db = get_db()
        event = db.execute(
            "SELECT title FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if not event:
            flash("Event not found.", "error")
            return redirect(url_for("events"))
        db.execute("DELETE FROM events WHERE event_id=?", (event_id,))
        db.commit()
        flash(f"Event '{event['title']}' deleted.", "success")
        return redirect(url_for("events"))

    # ── Edit / Delete: Actors ────────────────────────────────────────────────

    @app.route("/actor/<int:actor_id>/edit", methods=["GET", "POST"])
    def actor_edit(actor_id: int):
        db = get_db()
        actor = db.execute(
            "SELECT * FROM actors WHERE actor_id=?", (actor_id,)
        ).fetchone()
        if not actor:
            flash("Actor not found.", "error")
            return redirect(url_for("actors"))

        actor_types_list = ["person","institution","government","movement",
                            "media","paramilitary","other"]

        if request.method == "POST":
            if request.form.get("password") != ADMIN_PASSWORD:
                flash("Incorrect password.", "error")
                return redirect(url_for("actor_edit", actor_id=actor_id))

            name        = request.form.get("name", "").strip()
            atype       = request.form.get("type", actor["type"])
            description = request.form.get("description", "").strip() or None

            if not name:
                flash("Name is required.", "error")
                return redirect(url_for("actor_edit", actor_id=actor_id))

            db.execute(
                "UPDATE actors SET name=?, type=?, description=? WHERE actor_id=?",
                (name, atype, description, actor_id),
            )
            db.commit()
            flash(f"Actor '{name}' updated.", "success")
            return redirect(url_for("actor_detail", actor_id=actor_id))

        return render_template(
            "edit_actor.html",
            actor=actor,
            actor_types_list=actor_types_list,
        )

    @app.route("/actor/<int:actor_id>/delete", methods=["POST"])
    def actor_delete(actor_id: int):
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("actor_detail", actor_id=actor_id))
        db = get_db()
        actor = db.execute(
            "SELECT name FROM actors WHERE actor_id=?", (actor_id,)
        ).fetchone()
        if not actor:
            flash("Actor not found.", "error")
            return redirect(url_for("actors"))
        db.execute("DELETE FROM actors WHERE actor_id=?", (actor_id,))
        db.commit()
        flash(f"Actor '{actor['name']}' deleted.", "success")
        return redirect(url_for("actors"))

    # -----------------------------------------------------------------------
    # Route: /dossier — Phase 7: Print-ready intelligence briefs
    # -----------------------------------------------------------------------

    def _dossier_actor_data(db, actor_id: int):
        """
        Shared query function used by both the dossier route and any future
        PDF-export route.  Returns (actor_row, context_dict) or (None, {}).
        """
        actor = db.execute(
            "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
        ).fetchone()
        if not actor:
            return None, {}

        events = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   ae.role,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ae.actor_id = ?
            GROUP  BY e.event_id
            ORDER  BY e.date ASC
        """, (actor_id,)).fetchall()

        artifact_footprint = db.execute("""
            SELECT DISTINCT a.artifact_id, a.title, a.type, a.source,
                            a.date, a.thumbnail, a.description,
                            e.title AS event_title, e.event_id
            FROM   actor_events ae
            JOIN   artifacts a ON a.event_id = ae.event_id
            JOIN   events e    ON e.event_id  = ae.event_id
            WHERE  ae.actor_id = ?
            ORDER  BY a.date ASC
        """, (actor_id,)).fetchall()

        co_actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type,
                   COUNT(DISTINCT ae2.event_id) AS shared_events
            FROM   actor_events ae1
            JOIN   actor_events ae2 ON ae2.event_id  = ae1.event_id
                                   AND ae2.actor_id != ae1.actor_id
            JOIN   actors ac ON ac.actor_id = ae2.actor_id
            WHERE  ae1.actor_id = ?
            GROUP  BY ac.actor_id
            ORDER  BY shared_events DESC
            LIMIT  12
        """, (actor_id,)).fetchall()

        role_timeline = db.execute("""
            SELECT ae.role, e.event_id, e.title, e.date, e.category
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            WHERE  ae.actor_id = ?
              AND  ae.role IS NOT NULL
            ORDER  BY e.date
        """, (actor_id,)).fetchall()

        return actor, {
            "events":             events,
            "artifact_footprint": artifact_footprint,
            "co_actors":          co_actors,
            "role_timeline":      role_timeline,
        }

    def _dossier_event_data(db, event_id: int):
        """
        Shared query function for event dossiers.
        Returns (event_row, context_dict) or (None, {}).
        """
        event = db.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not event:
            return None, {}

        artifacts = db.execute("""
            SELECT artifact_id, title, type, source, date, thumbnail, description
            FROM   artifacts
            WHERE  event_id = ?
            ORDER  BY date ASC, type
        """, (event_id,)).fetchall()

        actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description, ae.role
            FROM   actor_events ae
            JOIN   actors ac ON ac.actor_id = ae.actor_id
            WHERE  ae.event_id = ?
            ORDER  BY ac.type, ac.name
        """, (event_id,)).fetchall()

        source_breakdown = db.execute("""
            SELECT source, COUNT(*) AS cnt
            FROM   artifacts
            WHERE  event_id = ? AND source IS NOT NULL
            GROUP  BY source
            ORDER  BY cnt DESC
        """, (event_id,)).fetchall()

        related_by_actor = db.execute("""
            SELECT DISTINCT e.event_id, e.title, e.date, e.category,
                            e.location,
                            COUNT(DISTINCT ae2.actor_id) AS shared_actors
            FROM   actor_events ae1
            JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                                   AND ae2.event_id  != ae1.event_id
            JOIN   events e ON e.event_id = ae2.event_id
            WHERE  ae1.event_id = ?
            GROUP  BY e.event_id
            ORDER  BY shared_actors DESC, e.date
            LIMIT  5
        """, (event_id,)).fetchall()

        return event, {
            "artifacts":         artifacts,
            "actors":            actors,
            "source_breakdown":  source_breakdown,
            "related_by_actor":  related_by_actor,
        }

    @app.route("/dossier/actor/<int:actor_id>")
    def dossier_actor(actor_id: int):
        db = get_db()
        actor, ctx = _dossier_actor_data(db, actor_id)
        if not actor:
            return render_template("archive.html", page="404"), 404

        import datetime
        return render_template(
            "dossier.html",
            subject_type="actor",
            subject=actor,
            generated_at=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            back_url=url_for("actor_detail", actor_id=actor_id),
            **ctx,
        )

    @app.route("/dossier/event/<int:event_id>")
    def dossier_event(event_id: int):
        db = get_db()
        event, ctx = _dossier_event_data(db, event_id)
        if not event:
            return render_template("archive.html", page="404"), 404

        import datetime
        return render_template(
            "dossier.html",
            subject_type="event",
            subject=event,
            generated_at=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            back_url=url_for("event_detail", event_id=event_id),
            **ctx,
        )

    # -----------------------------------------------------------------------
    # Route: /admin — add_actor POST handler addition (Phase 7)
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Route: media file serving
    # -----------------------------------------------------------------------

    @app.route("/media/<path:filepath>")
    def serve_media(filepath):
        return send_from_directory(str(MEDIA_DIR), filepath)

    # -----------------------------------------------------------------------
    # Error handlers
    # -----------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(e):
        return render_template("archive.html", page="404"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("archive.html", page="500"), 500

    return app


# ---------------------------------------------------------------------------
# Schema SQL (unchanged from Phase 2 — reproduced as single source of truth)
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS actors (
        actor_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        type        TEXT    NOT NULL
                    CHECK(type IN (
                        'person','institution','media','movement','government'
                    )),
        description TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        summary     TEXT,
        date        TEXT,
        location    TEXT,
        latitude    REAL,
        longitude   REAL,
        category    TEXT
                    CHECK(category IN (
                        'Election','Security','Civil Unrest','Legislative',
                        'Economic','Diplomatic','Military','Social','Other'
                    )),
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        description TEXT,
        type        TEXT    NOT NULL
                    CHECK(type IN ('video','photo','document','audio','news')),
        date        TEXT,
        location    TEXT,
        latitude    REAL,
        longitude   REAL,
        tags        TEXT,
        source      TEXT
                    CHECK(source IN (
                        'verified','unverified','government','leaked',
                        'citizen','media'
                    )),
        file_path   TEXT,
        thumbnail   TEXT,
        event_id    INTEGER
                    REFERENCES events(event_id) ON DELETE SET NULL,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actor_events (
        actor_id    INTEGER NOT NULL
                    REFERENCES actors(actor_id)  ON DELETE CASCADE,
        event_id    INTEGER NOT NULL
                    REFERENCES events(event_id)  ON DELETE CASCADE,
        role        TEXT,
        PRIMARY KEY (actor_id, event_id)
    )
    """,
    # ── Phase 8: Case Workspaces ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS cases (
        case_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        description TEXT,
        status      TEXT    NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','closed','archived')),
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_artifacts (
        case_id          INTEGER NOT NULL REFERENCES cases(case_id)        ON DELETE CASCADE,
        artifact_id      INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
        note             TEXT,
        pinned_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        sequence_order   INTEGER,
        transition_note  TEXT,
        PRIMARY KEY (case_id, artifact_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_events (
        case_id          INTEGER NOT NULL REFERENCES cases(case_id)  ON DELETE CASCADE,
        event_id         INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        note             TEXT,
        pinned_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        sequence_order   INTEGER,
        transition_note  TEXT,
        PRIMARY KEY (case_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_actors (
        case_id          INTEGER NOT NULL REFERENCES cases(case_id)  ON DELETE CASCADE,
        actor_id         INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        note             TEXT,
        pinned_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        sequence_order   INTEGER,
        transition_note  TEXT,
        PRIMARY KEY (case_id, actor_id)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts
    USING fts5(
        title,
        description,
        tags,
        location,
        content='artifacts',
        content_rowid='artifact_id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
    USING fts5(
        title,
        summary,
        location,
        content='events',
        content_rowid='event_id'
    )
    """,
]

TRIGGER_SQL = """
DROP TRIGGER IF EXISTS artifacts_ai;
CREATE TRIGGER artifacts_ai
AFTER INSERT ON artifacts BEGIN
    INSERT INTO artifacts_fts(rowid, title, description, tags, location)
    VALUES (new.artifact_id, new.title, new.description, new.tags, new.location);
END;

DROP TRIGGER IF EXISTS artifacts_ad;
CREATE TRIGGER artifacts_ad
AFTER DELETE ON artifacts BEGIN
    INSERT INTO artifacts_fts(artifacts_fts, rowid, title, description, tags, location)
    VALUES ('delete', old.artifact_id, old.title, old.description, old.tags, old.location);
END;

DROP TRIGGER IF EXISTS artifacts_au;
CREATE TRIGGER artifacts_au
AFTER UPDATE ON artifacts BEGIN
    INSERT INTO artifacts_fts(artifacts_fts, rowid, title, description, tags, location)
    VALUES ('delete', old.artifact_id, old.title, old.description, old.tags, old.location);
    INSERT INTO artifacts_fts(rowid, title, description, tags, location)
    VALUES (new.artifact_id, new.title, new.description, new.tags, new.location);
END;

DROP TRIGGER IF EXISTS events_ai;
CREATE TRIGGER events_ai
AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, title, summary, location)
    VALUES (new.event_id, new.title, new.summary, new.location);
END;

DROP TRIGGER IF EXISTS events_ad;
CREATE TRIGGER events_ad
AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, summary, location)
    VALUES ('delete', old.event_id, old.title, old.summary, old.location);
END;

DROP TRIGGER IF EXISTS events_au;
CREATE TRIGGER events_au
AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, summary, location)
    VALUES ('delete', old.event_id, old.title, old.summary, old.location);
    INSERT INTO events_fts(rowid, title, summary, location)
    VALUES (new.event_id, new.title, new.summary, new.location);
END;
"""


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    print(f"[FORGE] Initialising database at {DB_PATH} …")
    conn = _open_db()
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.executescript(TRIGGER_SQL)
    conn.commit()
    conn.close()
    print("[FORGE] Database initialised successfully.")


def migrate_db():
    print(f"[FORGE] Running migrations on {DB_PATH} …")
    conn = _open_db()

    def _columns(table):
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    migrations = [
        ("artifacts",      "thumbnail",        "TEXT"),
        # Phase 10 — Narrative Threader columns on junction tables
        ("case_artifacts", "sequence_order",   "INTEGER"),
        ("case_artifacts", "transition_note",  "TEXT"),
        ("case_events",    "sequence_order",   "INTEGER"),
        ("case_events",    "transition_note",  "TEXT"),
        ("case_actors",    "sequence_order",   "INTEGER"),
        ("case_actors",    "transition_note",  "TEXT"),
    ]
    for table, column, col_type in migrations:
        if column not in _columns(table):
            print(f"  [migrate] {table} ← adding column: {column} {col_type}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.executescript(TRIGGER_SQL)
    conn.commit()
    conn.close()
    print("[FORGE] Migrations complete.")


def ensure_media_dirs():
    for subdir in MEDIA_SUBDIRS:
        (MEDIA_DIR / subdir).mkdir(parents=True, exist_ok=True)
    print(f"[FORGE] Media directories verified at {MEDIA_DIR}")


def main():
    parser = argparse.ArgumentParser(description="FORGE — local archive server")
    parser.add_argument("--init-db",  action="store_true")
    parser.add_argument("--migrate",  action="store_true")
    parser.add_argument("--host",  default="127.0.0.1")
    parser.add_argument("--port",  type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    ensure_media_dirs()

    if args.init_db:
        init_db()
        sys.exit(0)

    if args.migrate:
        migrate_db()
        sys.exit(0)

    if not DB_PATH.exists():
        print("[FORGE] No database found — running first-time initialisation …")
        init_db()

    app = create_app()
    print(f"[FORGE] Server starting → http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()