#!/usr/bin/env python3
from __future__ import annotations

"""Blueprint: graph routes — D3/Vis-Network graph, intel-graph, actor-network,
relationships CRUD, graph metrics, coalitions."""

import json as _json
import re as _re
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, render_template, request

from core.web.helpers import get_db, DB_PATH

graph_bp = Blueprint("graph", __name__)


# -----------------------------------------------------------------------
# Route: /graph — Phase 6: D3.js Intelligence Graph
# -----------------------------------------------------------------------

@graph_bp.route("/graph")
def graph():
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    event_filter = '' if lens == 'all' else f"WHERE source_type = '{lens}'"
    artifact_filter = '' if lens == 'all' else f"WHERE source_type = '{lens}'"

    # Pre-compute summary stats for the graph header
    node_counts = {
        "actors":    db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
        "events":    db.execute(f"SELECT COUNT(*) FROM events {event_filter}").fetchone()[0],
        "artifacts": db.execute(f"SELECT COUNT(*) FROM artifacts {artifact_filter}").fetchone()[0],
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

@graph_bp.route("/api/graph")
def api_graph():
    """
    Returns a JSON graph with:
      nodes  — actors, events, (optionally artifacts)
      links  — actor-event edges with weight = artifact count on that event

    Query params:
      actor_type  (multi) — filter to specific actor types
      category    (multi) — filter to specific event categories
      min_weight  (int)   — only include links with weight >= this value
      include_artifacts (bool) — add artifact nodes (default: false)
    """
    db = get_db()

    # ── Query param parsing ─────────────────────────────────────────────
    actor_types   = request.args.getlist("actor_type")   # [] means all
    categories    = request.args.getlist("category")     # [] means all
    min_weight    = max(0, int(request.args.get("min_weight", 0)))
    incl_artifacts = request.args.get("include_artifacts", "false").lower() == "true"

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    # ── Actors ─────────────────────────────────────────────────────────
    actor_where = ""
    actor_params: list = []
    if actor_types:
        placeholders = ",".join("?" * len(actor_types))
        actor_where  = f"WHERE type IN ({placeholders})"
        actor_params = list(actor_types)

    if lens != 'all':
        actor_where = ("WHERE " if not actor_where else actor_where + " AND ") + "source_type = ?"
        actor_params.append(lens)

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

    if lens != 'all':
        event_where = ("WHERE " if not event_where else event_where + " AND ") + "e.source_type = ?"
        event_params.append(lens)

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

    # ── Links: actor <-> event with weight ──────────────────────────────
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
        if lens == 'all':
            artifacts = db.execute("""
                SELECT artifact_id, title, type, source, event_id
                FROM   artifacts
                WHERE  event_id IS NOT NULL
            """).fetchall()
        else:
            artifacts = db.execute("""
                SELECT artifact_id, title, type, source, event_id
                FROM   artifacts
                WHERE  event_id IS NOT NULL AND source_type = ?
            """, (lens,)).fetchall()

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
# Phase 12: /api/graph_data — Vis-Network native payload
# Returns nodes and edges pre-formatted for Vis-Network.
# Shares the same filter params as /api/graph.
# -----------------------------------------------------------------------

@graph_bp.route("/api/graph_data")
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
    actor_types    = request.args.getlist("actor_type")
    categories     = request.args.getlist("category")
    min_weight     = max(0, int(request.args.get("min_weight", 0)))
    incl_artifacts = request.args.get("include_artifacts", "false").lower() == "true"
    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    # ── Actors ─────────────────────────────────────────────────────────
    actor_where  = ""
    actor_params = []
    if actor_types:
        phs         = ",".join("?" * len(actor_types))
        actor_where = f"WHERE type IN ({phs})"
        actor_params = list(actor_types)

    if lens != 'all':
        actor_where = ("WHERE " if not actor_where else actor_where + " AND ") + "source_type = ?"
        actor_params.append(lens)

    actor_rows = db.execute(f"""
        SELECT ac.actor_id, ac.name, ac.type, ac.description,
               COUNT(DISTINCT all_ev.event_id) AS event_count
        FROM   actors ac
        LEFT   JOIN (
            SELECT actor_id, event_id FROM actor_events
            UNION
            SELECT actor_id, event_id FROM event_actors
        ) all_ev ON all_ev.actor_id = ac.actor_id
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

    if lens != 'all':
        event_where = ("WHERE " if not event_where else event_where + " AND ") + "e.source_type = ?"
        event_params.append(lens)

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

    # ── actor_events + event_actors edges (combined) ──────────────────
    ae_rows = db.execute("""
        SELECT actor_id, event_id, role,
               COUNT(DISTINCT artifact_id) AS weight
        FROM (
            SELECT ae.actor_id, ae.event_id, ae.role,
                   a.artifact_id
            FROM   actor_events ae
            LEFT   JOIN artifacts a ON a.event_id = ae.event_id
            UNION ALL
            SELECT ea.actor_id, ea.event_id, ea.role,
                   a.artifact_id
            FROM   event_actors ea
            LEFT   JOIN artifacts a ON a.event_id = ea.event_id
        )
        GROUP  BY actor_id, event_id
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
        # Clean title — strip common HTML artifacts from civic intel collector
        clean_title = _re.sub(
            r'\s*(Date Published|Published|SAPS|saps\.gov\.za|&nbsp;).*$',
            '', ev['title'], flags=_re.IGNORECASE
        ).strip()
        clean_title = clean_title or ev['title']

        tooltip = f"{clean_title}"
        if ev["date"]:     tooltip += f"\nDate: {ev['date']}"
        if ev["category"]: tooltip += f"\nCategory: {ev['category']}"
        if ev["location"]: tooltip += f"\nLocation: {ev['location']}"
        # Scale square size by artifact count
        sz = 16 + min(ev["artifact_count"] or 0, 6) * 2

        vis_nodes.append({
            "id":    f"event-{ev['event_id']}",
            "label": clean_title[:26] + ("…" if len(clean_title) > 26 else ""),
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
        if lens == 'all':
            art_rows = db.execute("""
                SELECT artifact_id, title, type, source, event_id, description
                FROM   artifacts
                WHERE  event_id IS NOT NULL
            """).fetchall()
        else:
            art_rows = db.execute("""
                SELECT artifact_id, title, type, source, event_id, description
                FROM   artifacts
                WHERE  event_id IS NOT NULL AND source_type = ?
            """, (lens,)).fetchall()
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

    # ── actor->event edges ───────────────────────────────────────────────
    for ae in ae_rows:
        if ae["actor_id"] not in actor_id_set:
            continue
        if ae["event_id"] not in event_id_set:
            continue
        if ae["weight"] < min_weight:
            continue

        w     = ae["weight"] or 0
        width = 1 + min(w, 6) * 0.4          # 1 -> 3.4 proportional
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

    # ── Phase 22: entity_relationships edges ───────────────────────────
    # Directed named arrows between actor nodes.
    # Colour-coded by extraction method; amber for manual, indigo for NLP.
    REL_COLOURS = {
        "manual": {"color": "rgba(200,148,58,0.70)", "highlight": "#c8943a", "hover": "#c8943a"},
        "spacy":  {"color": "rgba(129,140,248,0.70)", "highlight": "#818cf8", "hover": "#818cf8"},
        "llm":    {"color": "rgba(52,211,153,0.70)",  "highlight": "#34d399", "hover": "#34d399"},
    }
    try:
        rel_rows = db.execute(
            "SELECT r.relationship_id, r.subject_actor_id, r.object_actor_id, "
            "r.relation_type, r.description, r.confidence, r.extraction_method, "
            "a1.name AS subject_name, a2.name AS object_name "
            "FROM entity_relationships r "
            "JOIN actors a1 ON a1.actor_id = r.subject_actor_id "
            "JOIN actors a2 ON a2.actor_id = r.object_actor_id "
            "ORDER BY r.confidence DESC"
        ).fetchall()
        for rel in rel_rows:
            if rel["subject_actor_id"] not in actor_id_set:
                continue
            if rel["object_actor_id"] not in actor_id_set:
                continue
            method = rel["extraction_method"] or "manual"
            col    = REL_COLOURS.get(method, REL_COLOURS["manual"])
            tip    = (f"{rel['subject_name']} → {rel['relation_type']} → {rel['object_name']}"
                      f"\nConfidence: {rel['confidence']:.0%}"
                      f"\nMethod: {method}")
            if rel["description"]:
                tip += f"\n{rel['description'][:120]}"
            vis_edges.append({
                "from":   f"actor-{rel['subject_actor_id']}",
                "to":     f"actor-{rel['object_actor_id']}",
                "label":  rel["relation_type"],
                "title":  tip,
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.8}},
                "color":  col,
                "width":  max(1.5, rel["confidence"] * 3),
                "dashes": False,
                "font":   {"size": 9, "color": "#c8943a", "face": "IBM Plex Mono",
                           "align": "middle", "strokeWidth": 2,
                           "strokeColor": "#080d12"},
                "kind":        "relationship",
                "relation_type": rel["relation_type"],
                "confidence":  rel["confidence"],
                "method":      method,
                "relationship_id": rel["relationship_id"],
            })
    except Exception:
        pass  # table may not exist on older databases

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


# -----------------------------------------------------------------------
# Path B: Actor Intelligence Graph
# /intel-graph        — full-page Cytoscape.js visualization
# /api/actor-network  — nodes (actors+metrics) + edges (entity_relationships)
# /api/actor/<id>/panel — click-panel data: signals, artifacts, risk
# -----------------------------------------------------------------------

@graph_bp.route("/intel-graph")
def intel_graph():
    db = get_db()
    stats = {
        "actors":       db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
        "hard_edges":   db.execute(
            "SELECT COUNT(*) FROM entity_relationships "
            "WHERE relation_type != 'co_occurrence'"
        ).fetchone()[0],
        "co_edges":     db.execute(
            "SELECT COUNT(*) FROM entity_relationships "
            "WHERE relation_type = 'co_occurrence'"
        ).fetchone()[0],
        "communities":  db.execute(
            "SELECT COUNT(DISTINCT community_id) FROM actor_network_metrics "
            "WHERE community_id IS NOT NULL"
        ).fetchone()[0],
    }
    return render_template("intel_graph.html", stats=stats)


@graph_bp.route("/api/actor-network")
def api_actor_network():
    db = get_db()

    hard_only = request.args.get("hard_only", "false").lower() == "true"
    show_all  = request.args.get("show_all",  "false").lower() == "true"

    # Hard edge = any named investigative relationship (not co-occurrence)
    if hard_only:
        edges_rows = db.execute("""
            SELECT relationship_id, subject_actor_id, object_actor_id,
                   relation_type, confidence, source_artifact_id
            FROM entity_relationships
            WHERE relation_type != 'co_occurrence'
        """).fetchall()
    else:
        edges_rows = db.execute("""
            SELECT relationship_id, subject_actor_id, object_actor_id,
                   relation_type, confidence, source_artifact_id
            FROM entity_relationships
        """).fetchall()

    edges = [dict(r) for r in edges_rows]

    # Actors that appear in the selected edge set
    connected_ids = set()
    for e in edges:
        connected_ids.add(e["subject_actor_id"])
        connected_ids.add(e["object_actor_id"])

    actor_rows = db.execute("""
        SELECT a.actor_id, a.name, a.type, a.description,
               COALESCE(m.influence_score, 0) AS influence_score,
               m.community_id, m.pagerank
        FROM   actors a
        LEFT   JOIN actor_network_metrics m ON a.actor_id = m.actor_id
        ORDER  BY a.name
    """).fetchall()

    nodes = []
    for a in actor_rows:
        if not show_all and a["actor_id"] not in connected_ids:
            continue
        desc = a["description"] or ""
        nodes.append({
            "id":              a["actor_id"],
            "label":           a["name"] or f"Actor #{a['actor_id']}",
            "actor_type":      a["type"] or "unknown",
            "influence_score": round(float(a["influence_score"] or 0), 4),
            "community_id":    a["community_id"],
            "pagerank":        round(float(a["pagerank"] or 0), 6),
            "is_high_risk":    "HIGH_RISK" in desc,
        })

    return jsonify({"nodes": nodes, "edges": edges,
                    "meta": {"total_actors": len(actor_rows),
                             "shown_nodes": len(nodes),
                             "edges": len(edges)}})


@graph_bp.route("/api/actor/<int:actor_id>/panel")
def api_actor_panel(actor_id: int):
    db = get_db()

    actor = db.execute("""
        SELECT a.actor_id, a.name, a.type, a.description, a.confidence_score,
               COALESCE(m.influence_score, 0) AS influence_score,
               m.community_id, m.betweenness, m.pagerank
        FROM   actors a
        LEFT   JOIN actor_network_metrics m ON a.actor_id = m.actor_id
        WHERE  a.actor_id = ?
    """, (actor_id,)).fetchone()
    if not actor:
        return jsonify({"error": "Actor not found"}), 404

    desc = actor["description"] or ""
    inf  = float(actor["influence_score"] or 0)
    if "HIGH_RISK" in desc:
        risk_level = "HIGH_RISK"
    elif inf > 0.3 or (actor["confidence_score"] or 0) > 0.7:
        risk_level = "ELEVATED"
    else:
        risk_level = "STANDARD"

    # Linked signals — signal_actors (pipeline) + socint_signals (FLUX)
    signals = db.execute("""
        SELECT DISTINCT s.signal_id, s.title, s.timestamp,
                        s.stream, s.relevance_score, s.source
        FROM   signals s
        WHERE  s.signal_id IN (
            SELECT signal_id FROM signal_actors  WHERE actor_id = ?
            UNION
            SELECT signal_id FROM socint_signals WHERE actor_id = ?
              AND  signal_id IS NOT NULL
        )
        ORDER  BY s.relevance_score DESC, s.timestamp DESC
        LIMIT  10
    """, (actor_id, actor_id)).fetchall()

    # PDF evidence: artifacts referenced by entity_relationships for this actor
    artifacts = db.execute("""
        SELECT DISTINCT a.artifact_id, a.title, a.description AS pdf_url,
                        a.file_path, a.source_type, er.relation_type
        FROM   entity_relationships er
        JOIN   artifacts a ON er.source_artifact_id = a.artifact_id
        WHERE  (er.subject_actor_id = ? OR er.object_actor_id = ?)
          AND  a.file_path IS NOT NULL
        ORDER  BY a.artifact_id DESC
        LIMIT  10
    """, (actor_id, actor_id)).fetchall()

    # Named relationships (excludes co_occurrence noise)
    relationships = db.execute("""
        SELECT er.relation_type, er.confidence, er.extraction_method,
               CASE WHEN er.subject_actor_id = ?
                    THEN 'outbound' ELSE 'inbound' END AS direction,
               CASE WHEN er.subject_actor_id = ?
                    THEN ao.name ELSE as2.name END      AS peer_name,
               CASE WHEN er.subject_actor_id = ?
                    THEN er.object_actor_id
                    ELSE er.subject_actor_id END        AS peer_id
        FROM   entity_relationships er
        JOIN   actors ao  ON ao.actor_id  = er.object_actor_id
        JOIN   actors as2 ON as2.actor_id = er.subject_actor_id
        WHERE  (er.subject_actor_id = ? OR er.object_actor_id = ?)
          AND  er.relation_type != 'co_occurrence'
        ORDER  BY er.confidence DESC
        LIMIT  20
    """, (actor_id, actor_id, actor_id, actor_id, actor_id)).fetchall()

    return jsonify({
        "actor": {
            "id":              actor["actor_id"],
            "name":            actor["name"],
            "type":            actor["type"],
            "description":     actor["description"],
            "influence_score": round(inf, 4),
            "community_id":    actor["community_id"],
            "risk_level":      risk_level,
        },
        "signals":       [dict(r) for r in signals],
        "artifacts":     [dict(r) for r in artifacts],
        "relationships": [dict(r) for r in relationships],
    })


# -----------------------------------------------------------------------
# Relationships CRUD
# -----------------------------------------------------------------------

@graph_bp.route("/api/relationships", methods=["GET"])
def api_relationships():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT r.relationship_id, r.subject_actor_id, r.object_actor_id, "
            "r.relation_type, r.description, r.confidence, r.extraction_method, "
            "r.source_artifact_id, r.source_event_id, r.created_at, "
            "a1.name AS subject_name, a2.name AS object_name "
            "FROM entity_relationships r "
            "JOIN actors a1 ON a1.actor_id=r.subject_actor_id "
            "JOIN actors a2 ON a2.actor_id=r.object_actor_id "
            "ORDER BY r.created_at DESC"
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"relationships": [dict(r) for r in rows], "total": len(rows)})


@graph_bp.route("/api/relationships", methods=["POST"])
def api_relationship_create():
    db   = get_db()
    data = request.get_json(silent=True) or {}
    subject_id  = data.get("subject_actor_id")
    object_id   = data.get("object_actor_id")
    rel_type    = (data.get("relation_type") or "").strip()
    description = (data.get("description") or "").strip() or None
    confidence  = float(data.get("confidence", 1.0))
    src_art     = data.get("source_artifact_id")
    src_evt     = data.get("source_event_id")
    if not subject_id or not object_id or not rel_type:
        return jsonify({"error": "subject_actor_id, object_actor_id, relation_type required"}), 400
    if subject_id == object_id:
        return jsonify({"error": "Subject and object must be different actors"}), 400
    try:
        cur = db.execute(
            "INSERT OR REPLACE INTO entity_relationships "
            "(subject_actor_id, object_actor_id, relation_type, description, "
            "confidence, source_artifact_id, source_event_id, extraction_method) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (subject_id, object_id, rel_type, description,
             confidence, src_art, src_evt, "manual")
        )
        db.commit()
        return jsonify({"relationship_id": cur.lastrowid, "status": "created"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@graph_bp.route("/api/relationships/<int:relationship_id>", methods=["DELETE"])
def api_relationship_delete(relationship_id: int):
    db = get_db()
    try:
        db.execute("DELETE FROM entity_relationships WHERE relationship_id=?",
                   (relationship_id,))
        db.commit()
        return jsonify({"status": "deleted", "relationship_id": relationship_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@graph_bp.route("/api/actors/<int:actor_id>/relationships")
def api_actor_relationships(actor_id: int):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT r.relationship_id, r.subject_actor_id, r.object_actor_id, "
            "r.relation_type, r.description, r.confidence, r.extraction_method, "
            "r.created_at, a1.name AS subject_name, a2.name AS object_name "
            "FROM entity_relationships r "
            "JOIN actors a1 ON a1.actor_id=r.subject_actor_id "
            "JOIN actors a2 ON a2.actor_id=r.object_actor_id "
            "WHERE r.subject_actor_id=? OR r.object_actor_id=? "
            "ORDER BY r.confidence DESC",
            (actor_id, actor_id)
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"relationships": [dict(r) for r in rows], "total": len(rows)})


# -----------------------------------------------------------------------
# Graph metrics + recalculate
# -----------------------------------------------------------------------

@graph_bp.route("/api/graph/metrics")
def api_graph_metrics():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT m.actor_id, a.name, a.type, m.betweenness, m.eigenvector, "
            "m.pagerank, m.community_id, m.node_count, m.edge_count, m.computed_at "
            "FROM actor_network_metrics m JOIN actors a ON a.actor_id=m.actor_id "
            "ORDER BY m.pagerank DESC"
        ).fetchall()
        meta = db.execute(
            "SELECT MAX(computed_at) AS last_run, COUNT(*) AS actor_count "
            "FROM actor_network_metrics"
        ).fetchone()
    except Exception:
        rows, meta = [], None
    return jsonify({
        "actors": [dict(r) for r in rows], "total": len(rows),
        "last_computed": meta["last_run"] if meta else None,
    })


@graph_bp.route("/api/graph/recalculate", methods=["POST"])
def api_graph_recalculate():
    try:
        from forage.engines.graph_engine import GraphEngine
        result = GraphEngine(db_path=DB_PATH).run()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# -----------------------------------------------------------------------
# Coalitions
# -----------------------------------------------------------------------

@graph_bp.route("/api/graph/coalitions")
def api_graph_coalitions():
    """
    Return all detected actor coalitions with member details.
    Sourced from actor_coalitions table written by coalition_detector engine.

    Response shape:
      {
        coalitions: [
          {
            coalition_label: "COALITION_1",
            member_count: 4,
            threshold_used: 5,
            computed_at: "...",
            members: [
              { actor_id, actor_name, actor_type,
                co_occurrence, pagerank, influence_score }
            ]
          }
        ],
        total: <int>,
        total_actors: <int>
      }
    """
    try:
        from forge_modules.coalition_detector.engine import query_coalitions
        data = query_coalitions(db_path=DB_PATH)
        return jsonify({
            "coalitions":   data,
            "total":        len(data),
            "total_actors": sum(len(c["members"]) for c in data),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
