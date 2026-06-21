#!/usr/bin/env python3
from __future__ import annotations

"""
Diagnostics blueprint — system health, FMS status, discovery/evolution.

Extracted from app.py (Phases 32-33).
"""

import os

from flask import Blueprint, jsonify, render_template, request

from core.web.helpers import DB_PATH, get_db

diagnostics_bp = Blueprint("diagnostics", __name__)


# -----------------------------------------------------------------------
# Phase 32: Diagnostics — Control Room
# -----------------------------------------------------------------------

@diagnostics_bp.route("/diagnostics")
def diagnostics():
    """Phase 32: System Control Room page."""
    return render_template("diagnostics.html")


@diagnostics_bp.route("/api/diagnostics")
def api_diagnostics():
    """
    Phase 32: Returns full pipeline health data for the Control Room.

    Response shape:
      {
        components: [ {component, status, records_in, records_out,
                       duration_s, run_at, hours_ago} ],
        db_stats:   { signals, correlated_pairs, sentinel_alerts_new,
                      sentinel_alerts_total, actors, cases, db_size_mb },
        stream_counts: { CRIME_INTEL, INFRASTRUCTURE, PRIORITY, GLOBAL },
        health:     "ok" | "warn" | "error"
      }
    """
    db = get_db()

    # ── Latest run per component ──────────────────────────────────────
    components = []
    try:
        rows = db.execute("""
            SELECT component, status, records_in, records_out,
                   duration_s, detail_json, run_at,
                   ROUND((julianday('now') - julianday(run_at)) * 24, 2)
                       AS hours_ago
            FROM   pipeline_runs pr
            WHERE  run_at = (
                SELECT MAX(run_at) FROM pipeline_runs pr2
                WHERE  pr2.component = pr.component
            )
            GROUP  BY component
            ORDER  BY component
        """).fetchall()
        components = [dict(r) for r in rows]
    except Exception:
        pass

    # ── DB stats ──────────────────────────────────────────────────────
    db_stats = {}
    try:
        db_stats = {
            "signals":               db.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "signals_raw":           db.execute("SELECT COUNT(*) FROM signals WHERE status='raw'").fetchone()[0],
            "correlated_pairs":      db.execute("SELECT COUNT(*) FROM correlated_incidents").fetchone()[0],
            "sentinel_alerts_new":   db.execute("SELECT COUNT(*) FROM sentinel_alerts WHERE status='new'").fetchone()[0],
            "sentinel_alerts_total": db.execute("SELECT COUNT(*) FROM sentinel_alerts").fetchone()[0],
            "actors":                db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
            "cases":                 db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
            "artifacts_pending":     db.execute("SELECT COUNT(*) FROM artifacts WHERE processing_status='pending'").fetchone()[0],
        }
        try:
            db_size = os.path.getsize(str(DB_PATH)) / (1024 * 1024)
            db_stats["db_size_mb"] = round(db_size, 1)
        except Exception:
            db_stats["db_size_mb"] = None
    except Exception:
        pass

    # ── Stream counts ──────────────────────────────────────────────────
    stream_counts = {}
    try:
        rows = db.execute(
            "SELECT stream, COUNT(*) AS cnt FROM signals "
            "WHERE stream IS NOT NULL GROUP BY stream"
        ).fetchall()
        stream_counts = {r["stream"]: r["cnt"] for r in rows}
    except Exception:
        pass

    # ── Recent runs (last 50 for run history table) ────────────────────
    recent_runs = []
    try:
        rows = db.execute("""
            SELECT component, status, records_in, records_out,
                   duration_s, run_at
            FROM   pipeline_runs
            ORDER  BY run_at DESC
            LIMIT  50
        """).fetchall()
        recent_runs = [dict(r) for r in rows]
    except Exception:
        pass

    # ── Overall health ─────────────────────────────────────────────────
    health = "ok"
    known = {"usgs_collector","gdelt_collector","firms_collector",
             "rss_collector","earthquake_collector","civic_intel_collector",
             "correlation_engine","sentinel","decay_engine","graph_engine"}
    seen  = {c["component"] for c in components}
    if known - seen:
        health = "warn"   # some components have never run
    for c in components:
        if c["status"] == "error":
            health = "error"; break
        if (c.get("hours_ago") or 0) > 6:
            health = "warn"

    return jsonify({
        "components":    components,
        "db_stats":      db_stats,
        "stream_counts": stream_counts,
        "recent_runs":   recent_runs,
        "health":        health,
    })


# -----------------------------------------------------------------------
# Phase 33: FMS Status + Attach/Detach + UI Discovery
# -----------------------------------------------------------------------

@diagnostics_bp.route("/api/fms/status")
def api_fms_status():
    """FMS module readiness — pure observability, no side effects."""
    try:
        from core.fms.readiness import report_readiness
        from core.conclave.context import get_context
        reports  = report_readiness()
        context  = get_context()
        pipeline = context.status()
    except Exception as exc:
        return {"error": str(exc)}, 500

    return {
        "modules":  reports,
        "pipeline": pipeline,
    }


@diagnostics_bp.route("/api/fms/attach/<module_name>", methods=["POST"])
def api_fms_attach(module_name: str):
    """
    Explicitly attach a READY module to Conclave.
    Idempotent — safe to call on an already-active module.
    """
    try:
        from core.fms.activation import attach_module
        from core.conclave.context import get_context
        result = attach_module(module_name, get_context())
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}, 500
    code = 200 if result["status"] in ("attached", "already_active") else 400
    return result, code


@diagnostics_bp.route("/api/fms/detach/<module_name>", methods=["POST"])
def api_fms_detach(module_name: str):
    """
    Detach a module from Conclave.
    Module remains READY — engines and hooks are removed from the pipeline.
    """
    try:
        from core.fms.activation import detach_module
        from core.conclave.context import get_context
        result = detach_module(module_name, get_context())
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}, 500
    code = 200 if result["status"] in ("detached", "not_active") else 400
    return result, code


@diagnostics_bp.route("/api/fms/ui")
def api_fms_ui():
    """
    Return all ACTIVE modules that declare a ui block in their manifest.
    Used by /intel to build the dynamic module panel list.

    Response: list of {module, title, endpoint, type, data_key, panel_group}
    Only modules currently attached to Conclave are included.
    """
    import json as _json
    from core.conclave.context import get_context
    from core.fms.readiness import scan_modules

    try:
        ctx            = get_context()
        active_modules = set(ctx.get_active_modules().keys())
        reports        = scan_modules()

        ui_panels = []
        for report in reports:
            name = report.get("name", "")
            if name not in active_modules:
                continue
            # Read manifest directly for ui block
            manifest_path = report.get("path", "")
            if not manifest_path:
                continue
            try:
                from pathlib import Path as _Path
                mf = _json.loads(
                    (_Path(manifest_path) / "manifest.json").read_text(encoding="utf-8")
                )
            except Exception:
                continue
            ui = mf.get("ui")
            if not ui:
                continue
            ui_panels.append({
                "module":      name,
                "title":       ui.get("title", name),
                "endpoint":    ui.get("endpoint", ""),
                "type":        ui.get("type", "table"),
                "data_key":    ui.get("data_key", "data"),
                "panel_group": ui.get("panel_group", "General"),
            })

        # Sort alphabetically within groups
        ui_panels.sort(key=lambda x: (x["panel_group"], x["title"]))
        return jsonify(ui_panels)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# -----------------------------------------------------------------------
# Phase 33: Evolution Layer — Discovery
# -----------------------------------------------------------------------

@diagnostics_bp.route("/discovery")
def discovery():
    """Phase 33: Discovery dashboard — candidate entities from evolution engine."""
    from utils.rss_parser import parse_rss
    db = get_db()
    try:
        targets = db.execute("""
            SELECT id, entity_name, suggested_query, evidence_count,
                   evidence_json, candidate_score, status, created_at, actioned_at
            FROM   discovery_targets
            ORDER  BY
                CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                candidate_score DESC
        """).fetchall()
        targets = [dict(t) for t in targets]
        # Fetch parsed articles for each target
        for t in targets:
            rss_url = t['suggested_query'].replace('/search?', '/rss/search?')
            t['articles'] = parse_rss(rss_url, limit=3)
    except Exception:
        targets = []

    pending  = sum(1 for t in targets if t["status"] == "pending")
    approved = sum(1 for t in targets if t["status"] == "approved")
    ignored  = sum(1 for t in targets if t["status"] == "ignored")

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    return render_template(
        "discovery.html",
        targets=targets,
        pending=pending,
        approved=approved,
        ignored=ignored,
        lens=lens,
    )


@diagnostics_bp.route("/api/evolution/run", methods=["POST"])
def api_evolution_run():
    """Phase 33: Trigger a full evolution engine scan."""
    try:
        from forage.engines.evolution_engine import EvolutionEngine
        top_n      = request.args.get("top",   25,  type=int)
        pair_limit = request.args.get("pairs", 500, type=int)
        dry_run    = request.args.get("dry_run", "false").lower() == "true"
        result     = EvolutionEngine(db_path=DB_PATH).run(
            top_n=top_n, pair_limit=pair_limit, dry_run=dry_run
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@diagnostics_bp.route("/api/discovery/<int:target_id>/approve", methods=["POST"])
def api_discovery_approve(target_id: int):
    """Phase 33: Approve a discovery candidate."""
    db  = get_db()
    row = db.execute(
        "SELECT * FROM discovery_targets WHERE id=?", (target_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Target not found"}), 404
    db.execute(
        "UPDATE discovery_targets SET status='approved', actioned_at=datetime('now') WHERE id=?",
        (target_id,)
    )
    db.commit()

    # Build the ready-to-paste SOURCES entry for civic_intel_collector.py
    src_key  = row["entity_name"].lower().replace(" ", "_")
    src_label = row["entity_name"].title()
    src_url   = row["suggested_query"]
    snippet = (
        "{\n"
        "    \"source_key\":          \"" + src_key + "\",\n"
        "    \"label\":               \"" + src_label + " (Google News)\",\n"
        "    \"fetch_mode\":          \"google_news\",\n"
        "    \"url\":                 \"" + src_url + "\",\n"
        "    \"stream\":              \"CRIME_INTEL\",\n"
        "    \"base_relevance\":      1.4,\n"
        "    \"default_lat\":         -25.7479,\n"
        "    \"default_lng\":         28.2293,\n"
        "    \"is_priority_default\": 0,\n"
        "},"
    )

    return jsonify({
        "status":     "approved",
        "id":         target_id,
        "entity":     row["entity_name"],
        "snippet":    snippet,
    })


@diagnostics_bp.route("/api/discovery/<int:target_id>/ignore", methods=["POST"])
def api_discovery_ignore(target_id: int):
    """Phase 33: Ignore a discovery candidate."""
    db = get_db()
    row = db.execute(
        "SELECT id FROM discovery_targets WHERE id=?", (target_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Target not found"}), 404
    db.execute(
        "UPDATE discovery_targets SET status='ignored', actioned_at=datetime('now') WHERE id=?",
        (target_id,)
    )
    db.commit()
    return jsonify({"status": "ignored", "id": target_id})


# -----------------------------------------------------------------------
# Phase 33: Evolution Fetched Intelligence
# -----------------------------------------------------------------------

@diagnostics_bp.route("/evolution")
def evolution():
    """Phase 33: Evolution Fetched Intelligence — parsed articles from Google News RSS sources."""
    from utils.rss_parser import parse_rss

    # Google News sources from civic_intel_collector
    google_news_sources = [
        {
            "label": "Daily Maverick",
            "url": "https://news.google.com/rss/search?q=site:dailymaverick.co.za+investigat&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "GroundUp",
            "url": "https://news.google.com/rss/search?q=site:groundup.org.za&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "Daily Maverick — Corruption",
            "url": "https://news.google.com/rss/search?q=site:dailymaverick.co.za+corruption+OR+VBS+OR+tender&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "News24 — Crime & Courts",
            "url": "https://news.google.com/rss/search?q=site:news24.com+crime+OR+court+OR+arrest+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "TimesLive — Corruption",
            "url": "https://news.google.com/rss/search?q=site:timeslive.co.za+corruption+OR+Hawks+OR+NPA&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "Eskom",
            "url": "https://news.google.com/rss/search?q=Eskom+loadshedding+OR+%22load+shedding%22+OR+%22power+outage%22+south+africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "Municipal Infrastructure",
            "url": "https://news.google.com/rss/search?q=south+africa+municipality+%22water+outage%22+OR+%22sewage%22+OR+%22road+collapse%22+OR+%22infrastructure%22&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "SAPS Media Releases",
            "url": "https://news.google.com/rss/search?q=site:saps.gov.za+newsroom&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "Hawks (DPCI) Media",
            "url": "https://news.google.com/rss/search?q=%22Hawks+DPCI%22+OR+%22Directorate+for+Priority+Crime%22+arrest+OR+charge+OR+raid+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
        {
            "label": "NPA Media",
            "url": "https://news.google.com/rss/search?q=site:npa.gov.za+OR+%22NPA%22+prosecution+South+Africa&hl=en-ZA&gl=ZA&ceid=ZA:en",
        },
    ]

    articles = []
    for source in google_news_sources:
        parsed = parse_rss(source["url"], limit=5)
        for article in parsed:
            articles.append({
                "source": source["label"],
                "title": article.title,
                "link": article.link,
                "summary": article.summary,
                "published": article.published,
            })

    return render_template("evolution.html", articles=articles)
