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
import re
import sys
import signal as _signal
import sqlite3
import argparse
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict
from core.api.context import inject_globals
from core.api.routes.wiki_routes import register_wiki_routes, wiki_bp
from core.db.connection import get_connection
from core.diagnostics.health import compute_pipeline_health
from core.pipeline.ingest import process_artifact_upload, IMAGE_EXTENSIONS

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

MEDIA_SUBDIRS = ["images", "videos", "documents", "audio", "actors"]
ACTOR_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

ADMIN_PASSWORD = os.environ.get("FORGE_ADMIN_PASSWORD", "forge-admin")

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
# Phase 72 — Telemetry Registry: pipeline_jobs
# ---------------------------------------------------------------------------
#
# A persistent, real-time job registry for the Control Room. Every long-
# running pipeline action (artifact drain, promote_staged, triple_extractor,
# wiki_pipeline) is registered here on dispatch and updated with progress,
# stage, and final status by the worker thread.
#
# Design notes
# ------------
# • WAL mode is enabled at table-init time so telemetry writes never block
#   the ingestion pipe.
# • _update_job() opens its own short-lived connection (≤1ms held) — never
#   shares the request-scoped Flask connection.
# • _KILL_FLAGS is the in-process kill mechanism; subprocess jobs are killed
#   via os.kill on the stored PID.
# ---------------------------------------------------------------------------

_PIPELINE_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key      TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    stage        TEXT,
    progress     REAL    DEFAULT 0.0,
    message      TEXT,
    pid          INTEGER,
    records_in   INTEGER DEFAULT 0,
    records_out  INTEGER DEFAULT 0,
    started_at   TEXT,
    updated_at   TEXT,
    finished_at  TEXT
)
"""

# In-process kill flags — module-level dict, thread-safe for set/check ops
_KILL_FLAGS: Dict[int, bool] = {}


# ---------------------------------------------------------------------------
# Stable 1.1 — Collector Autodiscovery Registry
# ---------------------------------------------------------------------------
#
# Scans forage/collectors/*.py at boot, extracts __manifest__ dicts using
# ast.literal_eval (no imports, no execution), and builds two module-level
# structures:
#
#   _COLLECTOR_REGISTRY  — dict[id -> manifest]  healthy collectors only
#   _DEAD_NODES          — list of dicts          broken/missing manifests
#
# Required manifest fields: id, name, description, icon, entry, job_key
# The entry path is validated to be within forage/collectors/ to prevent
# path traversal at dispatch time.
# ---------------------------------------------------------------------------

_COLLECTOR_REGISTRY: Dict[str, Dict] = {}
_DEAD_NODES: list = []

_MANIFEST_REQUIRED = {"id", "name", "description", "icon", "entry", "job_key"}


def _load_collector_registry() -> None:
    """
    Idempotent boot-time scan. Populates _COLLECTOR_REGISTRY and _DEAD_NODES.
    A broken or missing manifest never raises — it is quarantined as a Dead Node.

    Scans two collector roots:
        forage/collectors/  — OSINT collectors (original)
        flux/collectors/    — SOCINT collectors (FLUX Phase F)

    Security: each collector's entry path must resolve inside one of these
    two allowed roots. Any entry that escapes both is rejected as a Dead Node.
    """
    import ast as _ast
    import logging as _log

    _COLLECTOR_REGISTRY.clear()
    _DEAD_NODES.clear()

    log = _log.getLogger("forge.autodiscovery")

    # ── Collector root directories ────────────────────────────────────────────
    collector_roots: list[Path] = [
        BASE_DIR / "forage" / "collectors",
        BASE_DIR / "flux"   / "collectors",
    ]

    # Security: resolved allowed paths for entry validation
    allowed_roots: list[Path] = [p.resolve() for p in collector_roots if p.exists()]

    for collectors_dir in collector_roots:
        if not collectors_dir.exists():
            log.debug(f"[autodiscovery] Skipping missing root: {collectors_dir}")
            continue

        for py_path in sorted(collectors_dir.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            stem = py_path.stem
            try:
                source = py_path.read_text(encoding="utf-8")
                tree   = _ast.parse(source, filename=str(py_path))
                manifest = None
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.Assign):
                        for target in node.targets:
                            if (isinstance(target, _ast.Name)
                                    and target.id == "__manifest__"):
                                manifest = _ast.literal_eval(node.value)
                if manifest is None:
                    raise ValueError("No __manifest__ dict found in module body")

                # Schema validation
                missing = _MANIFEST_REQUIRED - set(manifest.keys())
                if missing:
                    raise ValueError(f"Manifest missing required fields: {missing}")

                # Security: entry must resolve inside one of the allowed roots
                entry_path = (BASE_DIR / manifest["entry"]).resolve()
                if not any(
                    str(entry_path).startswith(str(allowed))
                    for allowed in allowed_roots
                ):
                    raise ValueError(
                        f"entry path escapes all collector roots: {manifest['entry']}"
                    )
                if not entry_path.exists():
                    raise ValueError(f"entry script not found: {manifest['entry']}")

                _COLLECTOR_REGISTRY[manifest["id"]] = manifest
                log.info(
                    f"[autodiscovery] Registered: {manifest['id']} "
                    f"— {manifest['name']} ({collectors_dir.parent.name}/{collectors_dir.name})"
                )

            except SyntaxError as exc:
                _DEAD_NODES.append({
                    "id":     stem,
                    "file":   py_path.name,
                    "reason": f"SyntaxError in script: {exc}",
                })
                log.warning(
                    f"[autodiscovery] Dead Node: {py_path.name} — SyntaxError: {exc}"
                )
            except Exception as exc:
                _DEAD_NODES.append({
                    "id":     stem,
                    "file":   py_path.name,
                    "reason": str(exc),
                })
                log.warning(f"[autodiscovery] Dead Node: {py_path.name} — {exc}")

    log.info(
        f"[autodiscovery] Registry complete — "
        f"{len(_COLLECTOR_REGISTRY)} healthy, {len(_DEAD_NODES)} dead nodes"
    )


def _telemetry_init() -> None:
    """
    Idempotent: creates pipeline_jobs table + indexes, marks orphaned
    pending/running jobs as failed (stale-job recovery on server restart).
    Called once at app boot from create_app().
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_PIPELINE_JOBS_SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pj_status ON pipeline_jobs (status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pj_key    ON pipeline_jobs (job_key)"
            )
            # Stale-job recovery — any pending/running survivor of a previous
            # process is marked failed with a clear cause.
            conn.execute(
                """
                UPDATE pipeline_jobs
                SET    status      = 'failed',
                       message     = 'Server restarted while job was active',
                       finished_at = datetime('now'),
                       updated_at  = datetime('now')
                WHERE  status IN ('pending', 'running')
                """
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        import logging as _log
        _log.getLogger("forge.telemetry").warning(
            f"[telemetry init] non-fatal: {exc}"
        )


def _create_job(job_key: str, message: str = "") -> int:
    """Insert a new job row in 'pending' state. Returns job_id."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipeline_jobs
                   (job_key, status, message, started_at, updated_at)
            VALUES (?,        'pending', ?,     datetime('now'), datetime('now'))
            """,
            (job_key, message),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _update_job(job_id: int, **fields: Any) -> None:
    """
    Fire-and-forget telemetry update. Never raises — a telemetry failure
    must never crash a worker. Refuses to overwrite terminal states
    (completed/failed/killed) so late-arriving callbacks can't clobber a
    finished job.
    """
    if not fields:
        return
    # Sentinel: 'now' means datetime('now') for finished_at field
    sets: list[str] = []
    values: list[Any] = []
    for k, v in fields.items():
        if v == "now":
            sets.append(f"{k} = datetime('now')")
        else:
            sets.append(f"{k} = ?")
            values.append(v)
    sets.append("updated_at = datetime('now')")
    values.append(job_id)
    sql = (
        f"UPDATE pipeline_jobs SET {', '.join(sets)} "
        f"WHERE job_id = ? AND status NOT IN ('completed','failed','killed')"
    )
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            conn.execute(sql, values)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # telemetry must never crash a worker


def _finalize_job(job_id: int, status: str, message: str = "",
                  records_out: int = 0, progress: float = 1.0) -> None:
    """
    Force-write a terminal state. Bypasses the 'no overwrite terminal'
    guard so the same call can mark completed/failed/killed unconditionally.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            conn.execute(
                """
                UPDATE pipeline_jobs
                SET    status      = ?,
                       message     = ?,
                       records_out = ?,
                       progress    = ?,
                       finished_at = datetime('now'),
                       updated_at  = datetime('now')
                WHERE  job_id      = ?
                """,
                (status, message[:500] if message else "",
                 records_out, progress, job_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


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
            g.db = get_connection()
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

    app.context_processor(inject_globals(get_db))

    # ── Phase 72: Telemetry registry init + stale-job recovery ────────────
    _telemetry_init()

    # ── Stable 1.1: Collector Autodiscovery ────────────────────────────────
    _load_collector_registry()

    # ── FMS: auto-attach all READY modules at startup (Phase 38) ───────────
    # Replaces observability-only scan. Discovers, validates, and attaches
    # every READY module in one pass. Never crashes app startup.
    try:
        import logging as _fms_log
        _fms_logger = _fms_log.getLogger('forge.fms.startup')

        # Step 1: discover all modules (idempotent)
        from core.fms.bootstrap import bootstrap_fms
        bootstrap_fms(verbose=False)

        # Step 2: scan readiness (no side effects)
        from core.fms.readiness import report_readiness
        reports = report_readiness()

        # Step 3: attach each READY module to Conclave
        from core.fms.activation import attach_module
        from core.conclave.context import get_context
        _context = get_context()

        attached = []
        skipped  = []
        failed   = []

        for _report in reports:
            _mod_name = _report.get('name', '')
            _status   = _report.get('status', '')

            if _status != 'READY':
                skipped.append(f'{_mod_name}({_status})')
                continue

            try:
                _result = attach_module(_mod_name, _context)
                if _result.get('status') in ('attached', 'already_active'):
                    attached.append(_mod_name)
                else:
                    failed.append(_mod_name)
                    _fms_logger.warning(
                        f"[FMS] Auto-attach failed '{_mod_name}': "
                        f"{_result.get('reason', 'unknown')}"
                    )
            except Exception as _mod_exc:
                failed.append(_mod_name)
                _fms_logger.warning(
                    f"[FMS] Auto-attach error '{_mod_name}': {_mod_exc}"
                )

        _fms_logger.info(f'[FMS] Auto-attached modules: {attached}')
        if skipped:
            _fms_logger.info(f'[FMS] Skipped (not READY): {skipped}')
        if failed:
            _fms_logger.warning(f'[FMS] Failed to attach: {failed}')

    except Exception as _fms_startup_exc:
        import logging as _fms_log
        _fms_log.getLogger('forge.fms.startup').warning(
            f'[FMS] Startup sequence failed (non-fatal): {_fms_startup_exc}'
        )

    # Initialize wiki schema and register wiki routes
    from core.db.wiki import init_wiki_db
    init_wiki_db()

    register_wiki_routes(app)
    app.register_blueprint(wiki_bp, url_prefix='/wiki')
    # expose graph API at root path for D3 payload fetch
    app.add_url_rule('/api/wiki/graph_data', endpoint='api_wiki_graph_data', view_func=app.view_functions.get('wiki.graph_data'))

    # ── Surface Intelligence Blueprint ────────────────────────────────────────
    from surface.routes import surface_bp
    app.register_blueprint(surface_bp)

    # -----------------------------------------------------------------------
    # Route: / — Dashboard
    # -----------------------------------------------------------------------

    @app.route("/")
    def index():
        db = get_db()

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        # Case-level filter — when set, scope events + actors to one case
        case_id = request.args.get('case_id', type=int)

        artifact_where = ""
        event_where = ""
        artifact_params = []
        event_params = []
        if lens != 'all':
            artifact_where = "WHERE a.source_type = ?"
            artifact_params = [lens]
            event_where = "WHERE e.source_type = ?"
            event_params = [lens]

        actor_where = ""
        actor_params = []
        if lens != 'all':
            actor_where = "WHERE ac.source_type = ?"
            actor_params = [lens]

        stats = {
            "artifacts": db.execute(f"SELECT COUNT(*) FROM artifacts a {artifact_where}", artifact_params).fetchone()[0],
            "events":    db.execute(f"SELECT COUNT(*) FROM events e {event_where}", event_params).fetchone()[0],
            "actors":    db.execute(f"SELECT COUNT(*) FROM actors ac {actor_where}", actor_params).fetchone()[0],
        }

        recent_artifacts = db.execute(f"""
            SELECT a.artifact_id, a.title, a.type, a.date, a.source, a.thumbnail,
                   e.title AS event_title, e.event_id
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            {artifact_where}
            ORDER  BY a.created_at DESC
            LIMIT  6
        """, artifact_params).fetchall()

        if case_id:
            recent_events = db.execute("""
                SELECT e.event_id, e.title, e.date, e.category, e.location
                FROM   events e
                JOIN   case_events ce ON ce.event_id = e.event_id
                WHERE  ce.case_id = ?
                ORDER  BY e.date DESC
                LIMIT  10
            """, (case_id,)).fetchall()
        else:
            recent_events = db.execute(f"""
                SELECT e.event_id, e.title, e.date, e.category, e.location
                FROM   events e
                {event_where}
                ORDER  BY e.date DESC
                LIMIT  5
            """, event_params).fetchall()

        type_breakdown = db.execute(f"""
            SELECT type, COUNT(*) AS cnt
            FROM   artifacts a
            {artifact_where}
            GROUP  BY type
            ORDER  BY cnt DESC
        """, artifact_params).fetchall()

        # Phase 17: 48-hour signal pulse (hourly buckets for Chart.js)
        pulse_source_clause = ""
        pulse_params = []
        if lens != 'all':
            pulse_source_clause = "AND source_type = ?"
            pulse_params = [lens]

        try:
            pulse_rows = db.execute("""
                WITH RECURSIVE hours(n) AS (
                    SELECT 0 UNION ALL SELECT n+1 FROM hours WHERE n < 47
                ),
                buckets AS (
                    SELECT strftime('%Y-%m-%dT%H:00',
                           datetime('now', '-' || (47-n) || ' hours')) AS bucket
                    FROM hours
                ),
                counts AS (
                    SELECT strftime('%Y-%m-%dT%H:00', timestamp) AS bucket,
                           COUNT(*)       AS total,
                           SUM(is_priority) AS priority
                    FROM signals
                    WHERE timestamp >= datetime('now', '-48 hours')
                      """ + pulse_source_clause + """
                    GROUP BY bucket
                )
                SELECT b.bucket,
                       COALESCE(c.total,    0) AS total,
                       COALESCE(c.priority, 0) AS priority
                FROM   buckets b
                LEFT   JOIN counts c ON c.bucket = b.bucket
                ORDER  BY b.bucket ASC
            """, pulse_params).fetchall()
            pulse_data = [dict(r) for r in pulse_rows]
        except Exception:
            pulse_data = []

        # Phase 17: signal summary stats for dashboard cards
        try:
            signal_stats = db.execute("""
                SELECT COUNT(*)                                               AS total,
                       SUM(CASE WHEN status='raw'   THEN 1 ELSE 0 END)       AS raw,
                       SUM(CASE WHEN is_priority=1  THEN 1 ELSE 0 END)       AS priority,
                       SUM(CASE WHEN source='usgs'  THEN 1 ELSE 0 END)       AS usgs,
                       SUM(CASE WHEN source='gdelt' THEN 1 ELSE 0 END)       AS gdelt,
                       SUM(CASE WHEN source='GDACS' THEN 1 ELSE 0 END)       AS gdacs,
                       SUM(CASE WHEN source='firms' THEN 1 ELSE 0 END)       AS firms
                FROM signals
            """).fetchone()
        except Exception:
            signal_stats = None

        # Phase 23: top correlated incidents for dashboard panel
        try:
            correlated = db.execute("""
                SELECT ci.correlation_score,
                       ci.distance_km,
                       ci.time_difference_hours,
                       ci.detected_at,
                       sa.signal_id AS sid_a, sa.title AS title_a,
                       sa.source AS src_a, sa.lat AS lat_a, sa.lng AS lng_a,
                       sb.signal_id AS sid_b, sb.title AS title_b,
                       sb.source AS src_b, sb.lat AS lat_b, sb.lng AS lng_b
                FROM   correlated_incidents ci
                JOIN   signals sa ON sa.signal_id = ci.signal_a
                JOIN   signals sb ON sb.signal_id = ci.signal_b
                ORDER  BY ci.correlation_score DESC
                LIMIT  8
            """).fetchall()
            correlated = [dict(r) for r in correlated]
        except Exception:
            correlated = []

        # Phase 24: top actors by global influence score (case-scoped when case_id set)
        intelligence_leads = []
        try:
            if case_id:
                leads_rows = db.execute("""
                    SELECT m.actor_id, a.name, a.type,
                           m.influence_score, m.betweenness, m.pagerank,
                           m.community_id, m.computed_at
                    FROM   actor_network_metrics m
                    JOIN   actors a ON a.actor_id = m.actor_id
                    WHERE  m.actor_id IN (
                        SELECT DISTINCT ea.actor_id
                        FROM   event_actors ea
                        JOIN   case_events ce ON ce.event_id = ea.event_id
                        WHERE  ce.case_id = ?
                    )
                    ORDER  BY m.influence_score DESC LIMIT 10
                """, (case_id,)).fetchall()
            else:
                leads_rows = db.execute(
                    "SELECT m.actor_id, a.name, a.type, "
                    "m.influence_score, m.betweenness, m.pagerank, "
                    "m.community_id, m.computed_at "
                    "FROM actor_network_metrics m "
                    "JOIN actors a ON a.actor_id = m.actor_id "
                    "ORDER BY m.influence_score DESC LIMIT 10"
                ).fetchall()
            intelligence_leads = [dict(r) for r in leads_rows]
        except Exception:
            pass

        # Phase 25: recent new Sentinel alerts for dashboard
        try:
            sentinel_alerts_dash = db.execute(
                "SELECT id, alert_type, confidence_score, signal_count, "
                "summary, location_lat, location_lon, created_at "
                "FROM sentinel_alerts "
                "WHERE status = 'new' "
                "ORDER BY confidence_score DESC, created_at DESC "
                "LIMIT 5"
            ).fetchall()
            sentinel_alerts_dash = [dict(r) for r in sentinel_alerts_dash]
        except Exception:
            sentinel_alerts_dash = []

        # Phase 27: stream counts for dashboard summary widget
        try:
            stream_counts = dict(
                db.execute(
                    "SELECT stream, COUNT(*) FROM signals "
                    "WHERE stream IS NOT NULL GROUP BY stream ORDER BY COUNT(*) DESC"
                ).fetchall()
            )
        except Exception:
            stream_counts = {}

        # Active cases for the case-filter selector
        active_cases = []
        selected_case = None
        try:
            active_cases = [dict(r) for r in db.execute(
                "SELECT case_id, name, status FROM cases "
                "WHERE LOWER(status)='active' ORDER BY case_id DESC"
            ).fetchall()]
            if case_id:
                row = db.execute(
                    "SELECT case_id, name FROM cases WHERE case_id=?", (case_id,)
                ).fetchone()
                if row:
                    selected_case = dict(row)
        except Exception:
            pass

        return render_template(
            "index.html",
            stats=stats,
            recent_artifacts=recent_artifacts,
            recent_events=recent_events,
            type_breakdown=type_breakdown,
            pulse_data=pulse_data,
            signal_stats=signal_stats,
            correlated=correlated,
            intelligence_leads=intelligence_leads,
            sentinel_alerts_dash=sentinel_alerts_dash,
            stream_counts=stream_counts,
            active_cases=active_cases,
            selected_case=selected_case,
            selected_case_id=case_id,
        )

    # -----------------------------------------------------------------------
    # Phase 17: /api/pulse — signal frequency for Chart.js pulse graph
    # -----------------------------------------------------------------------

    @app.route("/api/pulse")
    def api_pulse():
        from flask import jsonify
        db     = get_db()
        window = min(int(request.args.get("hours", 48)), 168)
        source = request.args.get("source", "").strip()
        source_clause = "AND source = :source" if source else ""
        try:
            rows = db.execute(f"""
                WITH RECURSIVE hours(n) AS (
                    SELECT 0 UNION ALL SELECT n+1 FROM hours WHERE n < :window - 1
                ),
                buckets AS (
                    SELECT strftime('%Y-%m-%dT%H:00',
                        datetime('now', '-' || (:window-1-n) || ' hours')) AS bucket
                    FROM hours
                ),
                counts AS (
                    SELECT strftime('%Y-%m-%dT%H:00', timestamp) AS bucket,
                           COUNT(*)         AS total,
                           SUM(is_priority) AS priority
                    FROM  signals
                    WHERE timestamp >= datetime('now', '-' || :window || ' hours')
                    {source_clause}
                    GROUP BY bucket
                )
                SELECT b.bucket,
                       COALESCE(c.total,    0) AS total,
                       COALESCE(c.priority, 0) AS priority
                FROM   buckets b
                LEFT   JOIN counts c ON c.bucket = b.bucket
                ORDER  BY b.bucket ASC
            """, {"window": window, "source": source or None}).fetchall()
            return jsonify({"hours": window, "source": source or None,
                            "buckets": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Phase 18: /api/heatmap — [lat, lng, intensity] for Leaflet.heat
    # -----------------------------------------------------------------------

    @app.route("/api/heatmap")
    def api_heatmap():
        """
        [lat, lng, intensity] triples for Leaflet.heat.
        Phase 20: coords pruned to 4dp, hard cap 5000 points.
        """
        import json as _json
        from flask import jsonify
        db     = get_db()
        source = request.args.get("source", "").strip()
        hours  = request.args.get("hours",  type=int)
        status = request.args.get("status", "raw,promoted")
        allowed = [s.strip() for s in status.split(",")]

        clauses = [
            "lat IS NOT NULL", "lng IS NOT NULL",
            f"status IN ({','.join('?' for _ in allowed)})",
        ]
        params = list(allowed)
        if source:
            clauses.append("source = ?");                      params.append(source)
        if hours:
            clauses.append("timestamp >= datetime('now', ?)"); params.append(f"-{hours} hours")

        try:
            rows = db.execute(
                "SELECT ROUND(lat,4) AS lat, ROUND(lng,4) AS lng, "
                "source, is_priority, metadata_json "
                f"FROM signals WHERE {' AND '.join(clauses)} "
                "ORDER BY is_priority DESC, timestamp DESC LIMIT 5000",
                params,
            ).fetchall()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        points = []
        for r in rows:
            src  = (r["source"] or "").lower()
            prio = r["is_priority"] or 0
            meta = {}
            if r["metadata_json"]:
                try:    meta = _json.loads(r["metadata_json"])
                except: pass
            if src == "usgs":
                mag = meta.get("mag")
                intensity = min(float(mag) / 9.0, 1.0) if mag is not None else 0.5
            elif src == "firms":
                frp = meta.get("frp")
                intensity = min(float(frp) / 500.0, 1.0) if frp is not None else 0.4
            else:
                intensity = 0.5
            if prio:
                intensity = max(intensity, 0.7)
            points.append([r["lat"], r["lng"], round(intensity, 3)])

        return jsonify({"points": points, "count": len(points), "source": source or None})

    # -----------------------------------------------------------------------
    # Phase 18: /api/signals/<id>/entities  &  /api/entities/top
    # -----------------------------------------------------------------------

    @app.route("/api/signals/<signal_id>/entities")
    def api_signal_entities(signal_id: str):
        from flask import jsonify
        db  = get_db()
        sig = db.execute("SELECT signal_id, title FROM signals WHERE signal_id=?",
                         (signal_id,)).fetchone()
        if not sig:
            return jsonify({"error": "Signal not found"}), 404
        try:
            rows = db.execute(
                "SELECT text, label, count FROM signal_entities "
                "WHERE signal_id=? ORDER BY label, count DESC, text",
                (signal_id,),
            ).fetchall()
        except Exception:
            rows = []
        grouped = {"PERSON": [], "ORG": [], "GPE": []}
        for r in rows:
            if r["label"] in grouped:
                grouped[r["label"]].append({"text": r["text"], "count": r["count"]})
        return jsonify({"signal_id": signal_id, "title": sig["title"],
                        "entities": grouped, "total": len(rows)})

    @app.route("/api/entities/top")
    def api_entities_top():
        from flask import jsonify
        db     = get_db()
        label  = request.args.get("label", "").strip().upper()
        limit  = min(int(request.args.get("limit", 20)), 100)
        source = request.args.get("source", "").strip()
        lc     = "AND se.label = ?"  if label  else ""
        sc     = "AND s.source = ?"  if source else ""
        params = ([label] if label else []) + ([source] if source else []) + [limit]
        try:
            rows = db.execute(
                f"SELECT se.text, se.label, SUM(se.count) AS total_count, "
                f"COUNT(DISTINCT se.signal_id) AS signal_count "
                f"FROM signal_entities se JOIN signals s ON s.signal_id=se.signal_id "
                f"WHERE 1=1 {lc} {sc} GROUP BY se.text, se.label "
                f"ORDER BY total_count DESC LIMIT ?", params,
            ).fetchall()
        except Exception:
            rows = []
        return jsonify({"label": label or None, "source": source or None,
                        "entities": [dict(r) for r in rows], "total": len(rows)})

    # -----------------------------------------------------------------------
    # Phase 19: /artifacts gallery + artifact API routes
    # -----------------------------------------------------------------------

    @app.route("/artifacts")
    def artifact_gallery():
        db       = get_db()
        atype    = request.args.get("type",   "").strip()
        source   = request.args.get("source", "").strip()
        status   = request.args.get("status", "").strip()
        q        = request.args.get("q",      "").strip()
        lens     = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'
        page     = max(1, int(request.args.get("page", 1)))
        per_page = 24

        clauses, params = [], []
        if atype:   clauses.append("a.type = ?");                params.append(atype)
        if source:  clauses.append("a.source = ?");              params.append(source)
        if status:  clauses.append("a.processing_status = ?");   params.append(status)
        if lens != 'all':
            clauses.append("a.source_type = ?");                 params.append(lens)
        if q:
            clauses.append("(a.title LIKE ? OR a.description LIKE ? OR a.tags LIKE ?)")
            like = f"%{q}%"; params += [like, like, like]

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        try:
            total = db.execute(
                f"SELECT COUNT(*) FROM artifacts a {where}", params
            ).fetchone()[0]
            rows = db.execute(f"""
                SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                       a.file_path, a.thumbnail, a.tags, a.location,
                       a.processing_status,
                       e.title AS event_title, e.event_id AS event_id,
                       CASE WHEN a.raw_text_cache IS NOT NULL THEN 1 ELSE 0 END AS has_text
                FROM   artifacts a
                LEFT   JOIN events e ON e.event_id = a.event_id
                {where}
                ORDER  BY a.created_at DESC
                LIMIT  ? OFFSET ?
            """, params + [per_page, (page-1)*per_page]).fetchall()
        except Exception:
            # raw_text_cache / processing_status columns may not exist yet
            # (pre-migration) — fall back to base columns
            total = db.execute(
                f"SELECT COUNT(*) FROM artifacts a {where}", params
            ).fetchone()[0]
            rows = db.execute(f"""
                SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                       a.file_path, a.thumbnail, a.tags, a.location,
                       'pending' AS processing_status,
                       e.title AS event_title, e.event_id AS event_id,
                       0 AS has_text
                FROM   artifacts a
                LEFT   JOIN events e ON e.event_id = a.event_id
                {where}
                ORDER  BY a.created_at DESC
                LIMIT  ? OFFSET ?
            """, params + [per_page, (page-1)*per_page]).fetchall()

        def _facet(col):
            try:
                if lens == 'all':
                    return {r[0]: r[1] for r in db.execute(
                        f"SELECT {col}, COUNT(*) FROM artifacts "
                        f"WHERE {col} IS NOT NULL GROUP BY {col}"
                    ).fetchall()}
                return {r[0]: r[1] for r in db.execute(
                    f"SELECT {col}, COUNT(*) FROM artifacts "
                    f"WHERE {col} IS NOT NULL AND source_type = ? GROUP BY {col}",
                    (lens,)
                ).fetchall()}
            except Exception:
                return {}

        return render_template(
            "gallery.html",
            artifacts=rows, total=total, page=page,
            total_pages=max(1, (total+per_page-1)//per_page),
            per_page=per_page, active_type=atype, active_source=source,
            active_status=status, q=q,
            type_counts=_facet("type"), source_counts=_facet("source"),
            status_counts=_facet("processing_status"),
        )

    @app.route("/api/artifacts/<int:artifact_id>/process", methods=["POST"])
    def api_artifact_process(artifact_id: int):
        from flask import jsonify
        db  = get_db()
        row = db.execute(
            "SELECT artifact_id, type, raw_text_cache, title, description "
            "FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        raw_text = (row["raw_text_cache"] if "raw_text_cache" in row.keys()
                    else None) or row["description"] or ""
        if not raw_text.strip():
            try:
                db.execute("UPDATE artifacts SET processing_status='skipped' "
                           "WHERE artifact_id=?", (artifact_id,))
                db.commit()
            except Exception:
                pass
            return jsonify({"status": "skipped", "entities": 0})
        try:
            from forage.processors.artifact_processor import ProcessorManager
            pm     = ProcessorManager(db_path=DB_PATH)
            result = pm.process_artifact(artifact_id=artifact_id, raw_text=raw_text,
                                         artifact_type=row["type"])
            try:
                db.execute("UPDATE artifacts SET processing_status='done' "
                           "WHERE artifact_id=?", (artifact_id,))
                db.commit()
            except Exception:
                pass
            return jsonify({"status": "done", "entities": result.get("entities", 0)})
        except Exception as exc:
            try:
                db.execute("UPDATE artifacts SET processing_status='failed' "
                           "WHERE artifact_id=?", (artifact_id,))
                db.commit()
            except Exception:
                pass
            return jsonify({"status": "failed", "error": str(exc)}), 500

    @app.route("/api/artifacts/<int:artifact_id>/signal", methods=["POST"])
    def api_artifact_to_signal(artifact_id: int):
        from flask import jsonify
        import json as _json
        db  = get_db()
        row = db.execute("SELECT * FROM artifacts WHERE artifact_id=?",
                         (artifact_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        body       = request.get_json(silent=True) or {}
        title      = body.get("title") or row["title"]
        content    = body.get("content") or row["description"] or ""
        lat        = body.get("lat")  or row["latitude"]
        lng        = body.get("lng")  or row["longitude"]
        is_priority= int(body.get("is_priority", 0))
        ext_id     = f"artifact:{artifact_id}:{(row['title'] or '')[:40]}"
        existing   = db.execute("SELECT signal_id FROM signals WHERE external_id=?",
                                (ext_id,)).fetchone()
        if existing:
            return jsonify({"status": "exists", "signal_id": existing["signal_id"]})
        sid = str(__import__("uuid").uuid4())
        try:
            db.execute("""
                INSERT INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority, source_artifact_id, source_type)
                VALUES (?,?,?,?,?,?,?,datetime('now'),'raw',?,?, 'live')
            """, (sid, row["source"] or "artifact", ext_id, title, content[:1000],
                  float(lat) if lat else None, float(lng) if lng else None,
                  is_priority, artifact_id))
        except Exception:
            # source_artifact_id column may not exist yet pre-migration
            db.execute("""
                INSERT INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority, source_type)
                VALUES (?,?,?,?,?,?,?,datetime('now'),'raw',?,'live')
            """, (sid, row["source"] or "artifact", ext_id, title, content[:1000],
                  float(lat) if lat else None, float(lng) if lng else None, is_priority))
        db.commit()
        return jsonify({"status": "created", "signal_id": sid})

    # -----------------------------------------------------------------------
    # Route: /events — Event list
    # -----------------------------------------------------------------------

    @app.route("/events")
    def events():
        db       = get_db()
        lens     = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        category = request.args.get("category", "")
        sort     = request.args.get("sort", "date_desc")

        order_map = {
            "date_desc":  "e.date DESC",
            "date_asc":   "e.date ASC",
            "title_asc":  "e.title ASC",
        }
        order_clause = order_map.get(sort, "e.date DESC")

        where_clause = []
        params       = []
        if category:
            where_clause.append("e.category = ?")
            params.append(category)
        if lens != 'all':
            where_clause.append("e.source_type = ?")
            params.append(lens)

        where_sql = "WHERE " + " AND ".join(where_clause) if where_clause else ""

        events_rows = db.execute(f"""
            SELECT e.event_id, e.title, e.summary, e.date, e.category,
                   e.location,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {where_sql}
            GROUP  BY e.event_id
            ORDER  BY {order_clause}
        """, params).fetchall()

        category_where = "WHERE category IS NOT NULL"
        category_params = []
        if lens != 'all':
            category_where += " AND source_type = ?"
            category_params.append(lens)

        categories = db.execute(f"""
            SELECT DISTINCT category FROM events
            {category_where}
            ORDER  BY category
        """, category_params).fetchall()

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

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        actor_where = '' if lens == 'all' else f"WHERE ac.source_type = '{lens}'"

        actors_rows = db.execute(f"""
            SELECT ac.actor_id, ac.name, ac.type, ac.description, ac.blacklisted,
                   COUNT(DISTINCT all_ev.event_id)    AS event_count,
                   COUNT(DISTINCT a.artifact_id)      AS artifact_count,
                   COUNT(DISTINCT sa.signal_id)       AS signal_count,
                   MAX(COALESCE(s.gravity_score, 0))  AS max_gravity,
                   MAX(COALESCE(s.is_priority, 0))    AS has_priority_signal,
                   CASE WHEN MAX(COALESCE(s.gravity_score, 0)) >= 0.55
                             OR MAX(COALESCE(s.is_priority, 0)) = 1
                        THEN 1 ELSE 0 END              AS is_targeted
            FROM   actors ac
            LEFT   JOIN (
                SELECT actor_id, event_id FROM actor_events
                UNION
                SELECT actor_id, event_id FROM event_actors
            ) all_ev ON all_ev.actor_id = ac.actor_id
            LEFT   JOIN artifacts a      ON a.event_id   = all_ev.event_id
            LEFT   JOIN signal_actors sa ON sa.actor_id  = ac.actor_id
            LEFT   JOIN signals s        ON s.signal_id  = sa.signal_id
            {actor_where}
            GROUP  BY ac.actor_id
            ORDER  BY is_targeted DESC, signal_count DESC, ac.name
        """).fetchall()

        return render_template("actors.html", actors=actors_rows, lens=lens)

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

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        event_where = '' if lens == 'all' else 'WHERE e.source_type = ?'
        params = [] if lens == 'all' else [lens]

        # All events with artifact counts, sorted chronologically
        rows = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   e.latitude, e.longitude,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {event_where}
            GROUP  BY e.event_id
            ORDER  BY e.date ASC, e.title ASC
        """, params).fetchall()

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
    # API: /api/timeline — timeline data payload
    # -----------------------------------------------------------------------

    @app.route("/api/timeline")
    def api_timeline():
        from flask import jsonify
        db = get_db()

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        event_where = '' if lens == 'all' else 'WHERE e.source_type = ?'
        params = [] if lens == 'all' else [lens]

        rows = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   e.latitude, e.longitude,
                   COUNT(a.artifact_id) AS artifact_count
            FROM   events e
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            {event_where}
            GROUP  BY e.event_id
            ORDER  BY e.date ASC, e.title ASC
        """, params).fetchall()

        return jsonify({
            'events': [dict(r) for r in rows],
        })

    # -----------------------------------------------------------------------
    # Route: /map — Phase 5: Leaflet geographic explorer
    # -----------------------------------------------------------------------

    @app.route("/map")
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

    @app.route("/api/geo")
    def api_geo():
        """
        Returns all mappable events as a GeoJSON FeatureCollection.
        Each Feature carries the properties Leaflet needs for popups
        and category-based marker styling.
        """
        import json as _json
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
                "generated": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00","Z"),
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

    @app.route("/api/signals/geojson")
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
        import json as _json
        from flask import Response
        db = get_db()

        source        = request.args.get("source",        "").strip()
        hours         = request.args.get("hours",         type=int)
        priority_only = request.args.get("priority_only", type=int, default=0)
        mode          = request.args.get("mode",          "").strip().lower()  # "relevant" = case-pinned only

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        from urllib.parse import quote_plus as _qp
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
                "generated": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat().replace("+00:00", "Z"),
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

    @app.route("/api/map/graph-edges")
    def api_map_graph_edges():
        import json as _json
        from flask import Response
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

    @app.route("/api/clusters/geojson")
    def api_clusters_geojson():
        """
        One GeoJSON Feature per FORAGE cluster — positioned at the centroid
        of all member signals.  Used by the Phase 15 cluster overlay layer.
        """
        import json as _json
        from flask import Response
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
                "generated": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00","Z"),
            },
        }

        return Response(
            _json.dumps(geojson, ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -----------------------------------------------------------------------
    # Route: /graph — Phase 6: D3.js Intelligence Graph
    # -----------------------------------------------------------------------

    @app.route("/graph")
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

        lens = req.args.get('lens', 'live').lower()
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
        lens = req.args.get('lens', 'live').lower()
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
            import re as _re
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
    # Phase 13: FORAGE — Signal routes
    # -----------------------------------------------------------------------

    @app.route("/signals")
    def signals():
        """
        FORAGE signal monitor — lists the 50 most recent ingested signals.
        Phase 14: includes cluster_id, is_priority columns.
        Phase 15.5: source_counts for triage badges.
        Phase 16: pinned_case_count per signal + active_cases for Pin-to-Case.
        """
        db = get_db()

        try:
            rows = db.execute("""
                SELECT s.signal_id,
                       s.source,
                       s.external_id,
                       s.title,
                       s.content,
                       s.lat,
                       s.lng,
                       s.timestamp,
                       s.status,
                       s.metadata_json,
                       s.cluster_id,
                       s.is_priority,
                       s.source_artifact_id,
                       a.title      AS artifact_title,
                       COUNT(cs.case_id) AS pinned_case_count
                FROM   signals s
                LEFT   JOIN case_signals cs ON cs.signal_id = s.signal_id
                LEFT   JOIN artifacts a ON a.artifact_id = s.source_artifact_id
                GROUP  BY s.signal_id
                ORDER  BY s.is_priority DESC, s.timestamp DESC
                LIMIT  50
            """).fetchall()
        except Exception:
            # case_signals / source_artifact_id may not exist pre-migration
            rows = db.execute("""
                SELECT signal_id, source, external_id, title, content,
                       lat, lng, timestamp, status, metadata_json,
                       cluster_id, is_priority,
                       NULL AS source_artifact_id,
                       NULL AS artifact_title,
                       0    AS pinned_case_count
                FROM   signals
                ORDER  BY is_priority DESC, timestamp DESC
                LIMIT  50
            """).fetchall()

        # Summary counts for the header bar
        counts = db.execute("""
            SELECT
                COUNT(*)                                              AS total,
                SUM(CASE WHEN status = 'raw'      THEN 1 ELSE 0 END) AS raw,
                SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed,
                SUM(CASE WHEN status = 'promoted' THEN 1 ELSE 0 END) AS promoted,
                SUM(CASE WHEN status = 'dismissed'THEN 1 ELSE 0 END) AS dismissed,
                SUM(CASE WHEN is_priority = 1     THEN 1 ELSE 0 END) AS priority,
                COUNT(DISTINCT CASE WHEN cluster_id IS NOT NULL
                                    THEN cluster_id END)              AS clusters
            FROM signals
        """).fetchone()

        # Phase 15.5 — per-source counts for triage badges
        source_counts_raw = db.execute("""
            SELECT source, COUNT(*) AS cnt
            FROM   signals
            WHERE  source IS NOT NULL
            GROUP  BY source
            ORDER  BY cnt DESC
        """).fetchall()
        source_counts = {r["source"]: r["cnt"] for r in source_counts_raw}

        # Phase 16 — active cases for the "Pin to Case" dropdown
        active_cases = db.execute("""
            SELECT case_id, name, status
            FROM   cases
            WHERE  status = 'active'
            ORDER  BY created_at DESC
        """).fetchall()

        # Phase 27 — stream filter + stream counts for pills
        active_stream = request.args.get("stream", "").strip().upper()
        try:
            stream_counts_raw = db.execute(
                "SELECT stream, COUNT(*) AS cnt FROM signals "
                "WHERE stream IS NOT NULL GROUP BY stream ORDER BY cnt DESC"
            ).fetchall()
            stream_counts = {r["stream"]: r["cnt"] for r in stream_counts_raw}
        except Exception:
            stream_counts = {}
            active_stream = ""

        if active_stream and active_stream != "ALL":
            try:
                filtered = db.execute(
                    "SELECT s.signal_id, s.source, s.external_id, s.title, "
                    "s.content, s.lat, s.lng, s.timestamp, s.status, "
                    "s.metadata_json, s.cluster_id, s.is_priority, "
                    "s.source_artifact_id, s.stream, "
                    "a.title AS artifact_title, "
                    "COUNT(cs.case_id) AS pinned_case_count "
                    "FROM signals s "
                    "LEFT JOIN case_signals cs ON cs.signal_id = s.signal_id "
                    "LEFT JOIN artifacts a "
                    "  ON a.artifact_id = s.source_artifact_id "
                    "WHERE s.stream = ? "
                    "GROUP BY s.signal_id "
                    "ORDER BY s.is_priority DESC, s.timestamp DESC LIMIT 50",
                    (active_stream,)
                ).fetchall()
                rows = filtered
            except Exception:
                pass

        return render_template(
            "signals.html",
            signals=rows,
            counts=counts,
            source_counts=source_counts,
            stream_counts=stream_counts,
            active_stream=active_stream or "ALL",
            active_cases=[dict(r) for r in active_cases],
        )

    # ── Smart promotion helpers ───────────────────────────────────────────────

    import re as _re

    _TITLE_STRIP = _re.compile(
        r'^(?:'
        r'@\w[\w.]*\s*:\s*'       # @handle: tweet text
        r'|GDELT\s*[—–-]\s*'      # GDELT — headline
        r'|USGS\s*[—–-]\s*'       # USGS — M4.5 earthquake
        r'|RSS\s*[—–-]\s*'        # RSS — article title
        r'|FIRMS\s*[—–-]\s*'      # FIRMS — fire activity
        r'|ACLED\s*[—–-]\s*'      # ACLED — event
        r'|x_pulse\s*[—–-]\s*'   # x_pulse — tweet
        r')',
        _re.IGNORECASE,
    )

    # Ordered: first match wins. Keywords are lowercased for comparison.
    _CATEGORY_RULES: list[tuple[list[str], str]] = [
        (["election", "vote", "voter", "ballot", "anc policy", "da policy",
          "eff policy", "party manifesto", "general election", "by-election",
          "iec", "electoral commission"], "Election"),
        (["parliament", "national assembly", "national council of provinces",
          "ncop", "legislature", "legislation", "bill passed",
          "portfolio committee", "standing committee"], "Legislative"),
        (["sandf", "south african national defence", "saaf", "sa navy",
          "military", "defence force", "army", "air force"], "Military"),
        (["protest", "strike", "shutdown", "riot", "looting", "civil unrest",
          "demonstration", "march", "picket", "stay-away", "stayaway",
          "community uprising"], "Civil Unrest"),
        (["murder", "killed", "crime", "robbery", "hijack", "hijacking",
          "arrested", "arrested for", "drug", "gang", "gang-related",
          "shooting", "attacked", "cash-in-transit", "cit heist", "police operation",
          "corruption", "bribery", "fraud"], "Security"),
        (["rand", "rands", "economy", "inflation", "budget", "reserve bank",
          "sarb", "national treasury", "load shedding", "load-shedding",
          "eskom", "stage 4", "stage 6", "tax", "gdp", "economic growth",
          "unemployment", "investment", "forex", "jse", "interest rate",
          "repo rate", "cpi"], "Economic"),
        (["diplomatic", "diplomat", "embassy", "foreign minister", "bilateral",
          "summit", "un security council", "brics", "au summit",
          "geopolitical", "sanctions", "trade deal"], "Diplomatic"),
        (["social grant", "sassa", "welfare", "poverty", "healthcare",
          "education", "community", "housing", "rncs", "social development"], "Social"),
    ]

    def _infer_category(title: str, content: str, stream: str, source: str) -> str:
        """Return the best-fit event category from signal metadata."""
        text = (title + " " + (content or "")).lower()
        for keywords, category in _CATEGORY_RULES:
            if any(kw in text for kw in keywords):
                return category
        # Stream fallback
        if stream == "CRIME_INTEL":
            return "Security"
        return "Other"

    def _clean_title(raw: str) -> str:
        """Strip collector prefixes and normalise the event title."""
        cleaned = _TITLE_STRIP.sub("", (raw or "").strip())
        # If stripping consumed everything, fall back to original
        if not cleaned:
            cleaned = raw or ""
        # Sentence-case if the title is ALL CAPS
        if cleaned == cleaned.upper() and len(cleaned) > 6:
            cleaned = cleaned.capitalize()
        return cleaned[:200]

    def _build_summary(signal: dict) -> str:
        """
        Build an enriched summary combining signal content, source context,
        and SOCINT metadata (hashtags, cashtags) where available.
        """
        import json as _json
        lines: list[str] = []

        content = (signal.get("content") or "").strip()
        if content:
            lines.append(content)

        # Parse metadata_json for extra context
        try:
            meta = _json.loads(signal.get("metadata_json") or "{}")
        except Exception:
            meta = {}

        # x_pulse: show handle + tags
        x_handle = meta.get("x_handle", "")
        hashtags  = meta.get("hashtags",  [])
        cashtags  = meta.get("cashtags",  [])

        if x_handle:
            tag_str = " ".join(f"#{h}" for h in hashtags[:8])
            cash_str = " ".join(cashtags[:4])
            context_parts = [f"via {x_handle}"]
            if tag_str:
                context_parts.append(tag_str)
            if cash_str:
                context_parts.append(cash_str)
            lines.append("[" + " · ".join(context_parts) + "]")

        # Source + stream badge for non-FLUX signals
        source = signal.get("source", "")
        stream = signal.get("stream", "")
        score  = signal.get("relevance_score")
        if source and not x_handle:
            meta_parts = [f"Source: {source.upper()}"]
            if stream:
                meta_parts.append(f"Stream: {stream}")
            if score is not None:
                meta_parts.append(f"Relevance: {round(float(score), 2)}")
            lines.append("[" + " · ".join(meta_parts) + "]")

        return "\n".join(lines)[:1000]

    @app.route("/admin/event/new")
    def admin_event_new():
        """
        Pre-filled event creation form — smart mode.

        When a signal_id is provided, fetches the full signal from the DB
        and applies intelligent inference:
          • title   — stripped of collector prefixes, normalised
          • category — inferred from content keywords, stream, source
          • summary  — enriched with source metadata and SOCINT tags
          • date     — extracted from signal timestamp
          • coords   — pulled directly from signal lat/lng

        Query-string params serve as fallback when no signal_id is given.
        """
        import json as _json
        db = get_db()

        signal_id   = request.args.get("signal_id", "")
        source_signal: dict | None = None

        if signal_id:
            row = db.execute(
                """
                SELECT signal_id, title, content, source, stream,
                       relevance_score, timestamp, lat, lng,
                       metadata_json, status, is_priority
                FROM   signals WHERE signal_id = ?
                """,
                (signal_id,),
            ).fetchone()
            if row:
                source_signal = dict(row)

        if source_signal:
            # ── Smart inference from full signal record ────────────────────
            prefill = {
                "title":    _clean_title(source_signal.get("title", "")),
                "summary":  _build_summary(source_signal),
                "date":     (source_signal.get("timestamp") or "")[:10],
                "location": request.args.get("location", ""),
                "latitude":  str(source_signal["lat"])  if source_signal.get("lat")  else "",
                "longitude": str(source_signal["lng"])  if source_signal.get("lng")  else "",
                "category": _infer_category(
                    source_signal.get("title",   ""),
                    source_signal.get("content", ""),
                    source_signal.get("stream",  ""),
                    source_signal.get("source",  ""),
                ),
            }
        else:
            # ── Fallback: honour raw query-string params ───────────────────
            prefill = {
                "title":     request.args.get("title",     ""),
                "summary":   request.args.get("summary",   ""),
                "date":      request.args.get("date",      ""),
                "location":  request.args.get("location",  ""),
                "latitude":  request.args.get("latitude",  ""),
                "longitude": request.args.get("longitude", ""),
                "category":  request.args.get("category",  "Other"),
            }

        event_categories = [
            "Election", "Security", "Civil Unrest", "Legislative",
            "Economic", "Diplomatic", "Military", "Social", "Other",
        ]

        return render_template(
            "admin_event_new.html",
            prefill=prefill,
            signal_id=signal_id,
            source_signal=source_signal,
            event_categories=event_categories,
            admin_password=ADMIN_PASSWORD,
        )

    @app.route("/admin/event/new", methods=["POST"])
    def admin_event_new_post():
        """
        Handles submission of the pre-filled event creation form.
        On success, marks the originating signal as 'promoted' if a
        signal_id was provided, then redirects to the new event.
        """
        db = get_db()

        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password — event not saved.", "error")
            return redirect(url_for("admin_event_new"))

        title    = request.form.get("ev_title",    "").strip()
        summary  = request.form.get("ev_summary",  "").strip() or None
        date     = request.form.get("ev_date",     "").strip() or None
        location = request.form.get("ev_location", "").strip() or None
        category = request.form.get("ev_category", "Other")
        signal_id= request.form.get("signal_id",   "").strip() or None

        raw_lat = request.form.get("ev_latitude",  "").strip()
        raw_lon = request.form.get("ev_longitude", "").strip()

        if not title:
            flash("Event title is required.", "error")
            return redirect(url_for("admin_event_new"))

        try:
            lat = float(raw_lat) if raw_lat else None
            lon = float(raw_lon) if raw_lon else None
        except ValueError:
            lat = lon = None

        cur = db.execute("""
            INSERT INTO events
                (title, summary, date, location, latitude, longitude, category, source_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'live')
        """, (title, summary, date, location, lat, lon, category))
        db.commit()
        new_event_id = cur.lastrowid

        # Mark the originating signal as promoted
        if signal_id:
            db.execute(
                "UPDATE signals SET status = 'promoted' WHERE signal_id = ?",
                (signal_id,),
            )
            db.commit()

        flash(f"Event '{title}' created successfully.", "success")
        return redirect(url_for("event_detail", event_id=new_event_id))

    @app.route("/api/signals/<signal_id>/dismiss", methods=["POST"])
    def api_signal_dismiss(signal_id: str):
        """Mark a signal as dismissed (no further action needed)."""
        import json as _json
        from flask import Response
        db = get_db()
        row = db.execute(
            "SELECT signal_id FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if not row:
            return Response(
                _json.dumps({"error": "Signal not found"}),
                status=404, mimetype="application/json"
            )
        db.execute(
            "UPDATE signals SET status = 'dismissed' WHERE signal_id = ?",
            (signal_id,),
        )
        db.commit()
        return Response(
            _json.dumps({"ok": True, "signal_id": signal_id, "status": "dismissed"}),
            mimetype="application/json",
        )

    # ── Phase 16: FORAGE → FORGE Synthesis APIs ─────────────────────────────

    @app.route("/api/cases/<int:case_id>/pin/<signal_id>", methods=["POST"])
    def api_case_pin_signal(case_id: int, signal_id: str):
        """Toggle-pin a FORAGE signal into a FORGE case."""
        from flask import jsonify
        db     = get_db()
        case   = db.execute("SELECT case_id, name FROM cases WHERE case_id=?", (case_id,)).fetchone()
        signal = db.execute("SELECT signal_id FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        if not case:   return jsonify({"error": "Case not found"}), 404
        if not signal: return jsonify({"error": "Signal not found"}), 404

        data = request.get_json(silent=True) or {}
        note = (data.get("note") or "").strip() or None

        existing = db.execute(
            "SELECT 1 FROM case_signals WHERE case_id=? AND signal_id=?",
            (case_id, signal_id),
        ).fetchone()

        if existing:
            db.execute("DELETE FROM case_signals WHERE case_id=? AND signal_id=?", (case_id, signal_id))
            db.commit()
            return jsonify({"pinned": False, "case_id": case_id, "signal_id": signal_id, "case_title": case["name"]})
        else:
            db.execute("INSERT INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)", (case_id, signal_id, note))
            db.commit()
            return jsonify({"pinned": True, "case_id": case_id, "signal_id": signal_id, "case_title": case["name"]})

    @app.route("/api/cases/<int:case_id>/signals")
    def api_case_signals(case_id: int):
        """Returns all signals pinned to a case as JSON, ordered chronologically."""
        from flask import jsonify
        db   = get_db()
        case = db.execute("SELECT case_id, name, status FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not case: return jsonify({"error": "Case not found"}), 404
        rows = db.execute("""
            SELECT s.signal_id, s.source, s.external_id, s.title, s.content,
                   s.lat, s.lng, s.timestamp, s.status, s.is_priority, s.cluster_id,
                   cs.note, cs.pinned_at
            FROM   case_signals cs
            JOIN   signals s ON s.signal_id = cs.signal_id
            WHERE  cs.case_id = ?
            ORDER  BY s.timestamp ASC
        """, (case_id,)).fetchall()
        return jsonify({"case_id": case_id, "case_title": case["name"],
                        "total": len(rows), "signals": [dict(r) for r in rows]})

    @app.route("/api/signals/<signal_id>/cases")
    def api_signal_cases(signal_id: str):
        """Returns all cases a signal is pinned to."""
        from flask import jsonify
        db  = get_db()
        sig = db.execute("SELECT signal_id FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
        if not sig: return jsonify({"error": "Signal not found"}), 404
        rows = db.execute("""
            SELECT c.case_id, c.name, c.status, cs.pinned_at, cs.note
            FROM   case_signals cs
            JOIN   cases c ON c.case_id = cs.case_id
            WHERE  cs.signal_id = ?
            ORDER  BY cs.pinned_at DESC
        """, (signal_id,)).fetchall()
        return jsonify({"signal_id": signal_id, "pinned_in": [dict(r) for r in rows]})

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
                    try:
                        upload_info = process_artifact_upload(uploaded, metadata={
                            "media_dir": str(MEDIA_DIR)
                        })
                        file_path = upload_info.get("file_path")
                        thumbnail = upload_info.get("thumbnail")
                        ext = upload_info.get("extension")
                    except ValueError as exc:
                        flash(str(exc), "error")
                        return redirect(url_for("admin"))
                    except Exception as exc:
                        flash("Failed to process uploaded file.", "error")
                        return redirect(url_for("admin"))

                # ── Phase 19 rev.2: live extraction on ingest ────────────────
                # OCR (pytesseract) and PDF (PyMuPDF) are now live.
                # Audio/video remain 'pending' until Whisper is installed.
                raw_text_cache    = None
                processing_status = "pending"

                if file_path:
                    abs_path = BASE_DIR / file_path
                    if ext == "txt":
                        try:
                            raw_text_cache    = abs_path.read_text(
                                encoding="utf-8", errors="replace")[:50_000]
                            processing_status = "done"
                        except Exception:
                            processing_status = "failed"

                    elif ext == "pdf":
                        try:
                            from forage.processors.artifact_processor import (
                                PDFPipeline, OCRPipeline,
                            )
                            _pdf = PDFPipeline()
                            _ocr = OCRPipeline() if OCRPipeline().available() else None
                            raw_text_cache = _pdf.extract(abs_path, ocr_pipeline=_ocr)
                            if raw_text_cache:
                                raw_text_cache    = raw_text_cache[:50_000]
                                processing_status = "done"
                            else:
                                processing_status = "failed"
                        except Exception:
                            processing_status = "failed"

                    elif ext in IMAGE_EXTENSIONS or atype in ("photo", "capture"):
                        try:
                            from forage.processors.artifact_processor import OCRPipeline
                            _ocr = OCRPipeline()
                            if _ocr.available():
                                raw_text_cache = _ocr.extract(abs_path)
                                if raw_text_cache:
                                    raw_text_cache    = raw_text_cache[:50_000]
                                    processing_status = "done"
                                else:
                                    processing_status = "skipped"
                            else:
                                processing_status = "pending"
                        except Exception:
                            processing_status = "failed"

                    elif ext in {"mp3","wav","ogg","m4a","mp4","mov","avi","mkv"}:
                        processing_status = "pending"   # future: Whisper
                    else:
                        processing_status = "skipped"
                else:
                    if description:
                        raw_text_cache    = description
                        processing_status = "done"
                    else:
                        processing_status = "skipped"

                # Try full Phase 19 INSERT; fall back to base INSERT if
                # columns don't exist yet (pre-migration safety net)
                try:
                    cur = db.execute("""
                        INSERT INTO artifacts
                            (title, description, type, date, location,
                             latitude, longitude, tags, source,
                             file_path, thumbnail, event_id,
                             raw_text_cache, processing_status, source_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')
                    """, (
                        title, description, atype, date, location,
                        float(latitude)  if latitude  else None,
                        float(longitude) if longitude else None,
                        tags, source, file_path, thumbnail,
                        int(event_id) if event_id else None,
                        raw_text_cache, processing_status,
                    ))
                except Exception:
                    cur = db.execute("""
                        INSERT INTO artifacts
                            (title, description, type, date, location,
                             latitude, longitude, tags, source,
                             file_path, thumbnail, event_id, source_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')
                    """, (
                        title, description, atype, date, location,
                        float(latitude)  if latitude  else None,
                        float(longitude) if longitude else None,
                        tags, source, file_path, thumbnail,
                        int(event_id) if event_id else None,
                    ))
                new_artifact_id = cur.lastrowid
                db.commit()

                # Trigger inline NER if text is ready
                if raw_text_cache and processing_status == "done":
                    try:
                        from forage.processors.artifact_processor import ProcessorManager
                        ProcessorManager(db_path=DB_PATH).process_artifact(
                            artifact_id=new_artifact_id,
                            raw_text=raw_text_cache,
                            artifact_type=atype,
                        )
                    except Exception:
                        pass  # NER failure never blocks ingest

                # ── Phase 20: Forensic extraction on ingest ───────────────────────
                if file_path:
                    try:
                        from forage.processors.forensic_processor import hash_file, extract_exif
                        import json as _fj
                        _abs = BASE_DIR / file_path
                        _sha, _md5 = hash_file(_abs)
                        _sz   = _abs.stat().st_size if _abs.exists() else None
                        _exif = extract_exif(_abs)
                        _ej   = _fj.dumps(_exif, ensure_ascii=False) if _exif else None
                        _glat = _exif.get("gps_lat")           if _exif else None
                        _glng = _exif.get("gps_lng")           if _exif else None
                        _make = _exif.get("make")              if _exif else None
                        _mod  = _exif.get("model")             if _exif else None
                        _edt  = _exif.get("datetime_original") if _exif else None
                        try:
                            db.execute(
                                "UPDATE artifacts SET file_hash_sha256=?,file_hash_md5=?,"
                                "file_size_bytes=?,exif_json=?,gps_lat=?,gps_lng=?,"
                                "device_make=?,device_model=?,exif_datetime=? "
                                "WHERE artifact_id=?",
                                (_sha,_md5,_sz,_ej,_glat,_glng,_make,_mod,_edt,cur.lastrowid))
                            if _glat and _glng and not latitude:
                                db.execute(
                                    "UPDATE artifacts SET latitude=?,longitude=? WHERE artifact_id=?",
                                    (_glat, _glng, cur.lastrowid))
                            if _sha:
                                _dups = db.execute(
                                    "SELECT artifact_id FROM artifacts "
                                    "WHERE file_hash_sha256=? AND artifact_id!=?",
                                    (_sha, cur.lastrowid)).fetchall()
                                for _d in _dups:
                                    db.execute(
                                        "INSERT OR IGNORE INTO artifact_duplicates "
                                        "(artifact_id,duplicate_of_id,hash_sha256) VALUES(?,?,?)",
                                        (cur.lastrowid, _d["artifact_id"], _sha))
                            db.commit()
                        except Exception:
                            pass
                    except Exception:
                        pass

                flash(f"Artifact '{title}' ingested successfully.", "success")
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
                atype       = request.form.get("ac_type", "unknown").strip().lower()
                description = request.form.get("ac_description", "").strip() or None

                _VALID_ACTOR_TYPES = frozenset([
                    "person", "institution", "media", "movement",
                    "government", "location", "political_party",
                    "organization", "unknown", "other", "paramilitary",
                ])

                if not name:
                    flash("Actor name is required.", "error")
                    return redirect(url_for("admin"))

                if atype not in _VALID_ACTOR_TYPES:
                    flash(
                        f"Invalid actor type '{atype}'. "
                        f"Allowed: {', '.join(sorted(_VALID_ACTOR_TYPES))}",
                        "error",
                    )
                    return redirect(url_for("admin"))

                try:
                    db.execute(
                        "INSERT INTO actors (name, type, description, source_type) VALUES (?, ?, ?, 'live')",
                        (name, atype, description),
                    )
                    db.commit()
                    flash(f"Actor '{name}' added successfully.", "success")
                except Exception as _e:
                    db.rollback()
                    flash(f"Could not add actor: {_e}", "error")
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

        actor_types_list = [
            "person", "institution", "government", "organization",
            "movement", "media", "political_party", "location",
            "other", "paramilitary", "unknown",
        ]

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

        # Phase 20: forensic context
        forensic_exif = {}
        if artifact["exif_json"]:
            try:
                import json as _fj; forensic_exif = _fj.loads(artifact["exif_json"])
            except Exception: pass
        duplicates = []
        if artifact["file_hash_sha256"]:
            try:
                duplicates = db.execute(
                    "SELECT a.artifact_id, a.title, a.type, a.source, "
                    "a.date, a.thumbnail, ad.detected_at "
                    "FROM artifact_duplicates ad "
                    "JOIN artifacts a ON a.artifact_id = ad.duplicate_of_id "
                    "WHERE ad.artifact_id = ? "
                    "UNION "
                    "SELECT a.artifact_id, a.title, a.type, a.source, "
                    "a.date, a.thumbnail, ad.detected_at "
                    "FROM artifact_duplicates ad "
                    "JOIN artifacts a ON a.artifact_id = ad.artifact_id "
                    "WHERE ad.duplicate_of_id = ? AND ad.artifact_id != ? "
                    "ORDER BY detected_at DESC",
                    (artifact_id, artifact_id, artifact_id),
                ).fetchall()
            except Exception: pass

        return render_template(
            "asset.html",
            artifact=artifact,
            circle_of_evidence=circle_of_evidence,
            siblings=siblings,
            tag_related=tag_related,
            forensic_exif=forensic_exif,
            duplicates=duplicates,
        )

    # -----------------------------------------------------------------------
    # Phase 20/21/22: Forensic + Graph + Relationship API routes
    # -----------------------------------------------------------------------

    @app.route("/api/artifacts/<int:artifact_id>/forensic")
    def api_artifact_forensic(artifact_id: int):
        from flask import jsonify
        import json as _fj
        db  = get_db()
        row = db.execute(
            "SELECT artifact_id, title, file_path, file_hash_sha256, file_hash_md5, "
            "file_size_bytes, exif_json, gps_lat, gps_lng, "
            "device_make, device_model, exif_datetime "
            "FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if not row: return jsonify({"error": "Not found"}), 404
        exif = {}
        if row["exif_json"]:
            try: exif = _fj.loads(row["exif_json"])
            except Exception: pass
        try:
            dups = db.execute(
                "SELECT a.artifact_id, a.title, a.type, ad.detected_at "
                "FROM artifact_duplicates ad "
                "JOIN artifacts a ON a.artifact_id = ad.duplicate_of_id "
                "WHERE ad.artifact_id = ? "
                "UNION "
                "SELECT a.artifact_id, a.title, a.type, ad.detected_at "
                "FROM artifact_duplicates ad "
                "JOIN artifacts a ON a.artifact_id = ad.artifact_id "
                "WHERE ad.duplicate_of_id = ? AND ad.artifact_id != ?",
                (artifact_id, artifact_id, artifact_id)
            ).fetchall()
            duplicates = [dict(d) for d in dups]
        except Exception:
            duplicates = []
        return jsonify({
            "artifact_id": artifact_id, "title": row["title"],
            "hashes": {"sha256": row["file_hash_sha256"], "md5": row["file_hash_md5"]},
            "file_size_bytes": row["file_size_bytes"], "exif": exif,
            "gps": {"lat": row["gps_lat"], "lng": row["gps_lng"]} if row["gps_lat"] else None,
            "device": {"make": row["device_make"], "model": row["device_model"]} if row["device_make"] else None,
            "exif_datetime": row["exif_datetime"],
            "duplicates": duplicates, "duplicate_count": len(duplicates),
        })

    @app.route("/api/artifacts/<int:artifact_id>/forensic-process", methods=["POST"])
    def api_artifact_forensic_process(artifact_id: int):
        from flask import jsonify
        db  = get_db()
        row = db.execute(
            "SELECT artifact_id, file_path, latitude FROM artifacts WHERE artifact_id=?",
            (artifact_id,)
        ).fetchone()
        if not row: return jsonify({"error": "Not found"}), 404
        try:
            from forage.processors.forensic_processor import ForensicProcessor
            fp = ForensicProcessor(db_path=DB_PATH)
            result = fp.process_artifact(row)
            fp.close()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/artifacts/duplicates")
    def api_artifact_duplicates():
        from flask import jsonify
        db = get_db()
        try:
            rows = db.execute(
                "SELECT ad.hash_sha256, "
                "COUNT(DISTINCT ad.artifact_id)+COUNT(DISTINCT ad.duplicate_of_id) AS total_copies, "
                "MIN(ad.detected_at) AS first_detected "
                "FROM artifact_duplicates ad GROUP BY ad.hash_sha256 ORDER BY total_copies DESC"
            ).fetchall()
        except Exception:
            rows = []
        return jsonify({"groups": [dict(r) for r in rows], "total": len(rows)})

    @app.route("/api/correlations/geojson")
    def api_correlations_geojson():
        """
        Returns correlated pairs as GeoJSON LineString features.
        Each feature is a line connecting signal_a to signal_b.
        Properties carry correlation_score, distance_km, time_diff.
        Used by map.html to draw L.polyline connections.
        """
        import json as _j
        from flask import Response
        db = get_db()
        try:
            rows = db.execute(
                "SELECT ci.correlation_score, ci.distance_km, "
                "ci.time_difference_hours, ci.detected_at, "
                "sa.title AS title_a, sa.source AS src_a, "
                "sa.lat AS lat_a, sa.lng AS lng_a, "
                "sb.title AS title_b, sb.source AS src_b, "
                "sb.lat AS lat_b, sb.lng AS lng_b "
                "FROM correlated_incidents ci "
                "JOIN signals sa ON sa.signal_id = ci.signal_a "
                "JOIN signals sb ON sb.signal_id = ci.signal_b "
                "WHERE ci.correlation_score >= 0.7 "
                "ORDER BY ci.correlation_score DESC LIMIT 200"
            ).fetchall()
        except Exception:
            rows = []
        features = []
        for r in rows:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(r["lng_a"], 6), round(r["lat_a"], 6)],
                        [round(r["lng_b"], 6), round(r["lat_b"], 6)],
                    ],
                },
                "properties": {
                    "score":     r["correlation_score"],
                    "dist_km":   r["distance_km"],
                    "time_diff": r["time_difference_hours"],
                    "title_a":   r["title_a"] or "",
                    "src_a":     r["src_a"]   or "",
                    "title_b":   r["title_b"] or "",
                    "src_b":     r["src_b"]   or "",
                    "detected_at": r["detected_at"] or "",
                },
            })
        return Response(
            _j.dumps({"type": "FeatureCollection", "features": features},
                     ensure_ascii=False),
            mimetype="application/geo+json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.route("/api/correlations/recalculate", methods=["POST"])
    def api_correlations_recalculate():
        from flask import jsonify
        try:
            from forage.engines.correlation_engine import CorrelationEngine
            result = CorrelationEngine(db_path=DB_PATH).run()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Phase 25: Sentinel API routes
    # -----------------------------------------------------------------------

    @app.route("/api/alerts")
    def api_alerts():
        """Return new sentinel alerts, optionally filtered by type."""
        from flask import jsonify
        db     = get_db()
        status = request.args.get("status", "new")
        limit  = min(int(request.args.get("limit", 50)), 200)
        try:
            rows = db.execute(
                "SELECT id, alert_type, confidence_score, signal_count, "
                "summary, location_lat, location_lon, status, created_at "
                "FROM sentinel_alerts WHERE status = ? "
                "ORDER BY confidence_score DESC, created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        except Exception:
            rows = []
        return jsonify({"alerts": [dict(r) for r in rows], "total": len(rows)})

    @app.route("/api/alerts/<int:alert_id>/acknowledge", methods=["POST"])
    def api_alert_acknowledge(alert_id: int):
        from flask import jsonify
        db = get_db()
        try:
            db.execute(
                "UPDATE sentinel_alerts SET status='acknowledged' WHERE id=?",
                (alert_id,)
            )
            db.commit()
            return jsonify({"status": "acknowledged", "id": alert_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/alerts/<int:alert_id>/dismiss", methods=["POST"])
    def api_alert_dismiss(alert_id: int):
        from flask import jsonify
        db = get_db()
        try:
            db.execute(
                "UPDATE sentinel_alerts SET status='dismissed' WHERE id=?",
                (alert_id,)
            )
            db.commit()
            return jsonify({"status": "dismissed", "id": alert_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/alerts/<int:alert_id>/promote-case", methods=["POST"])
    def api_alert_promote_case(alert_id: int):
        """Promote a sentinel alert to a new Case workspace."""
        from flask import jsonify, request as req
        db   = get_db()
        alert = db.execute(
            "SELECT * FROM sentinel_alerts WHERE id=?", (alert_id,)
        ).fetchone()
        if not alert:
            return jsonify({"error": "Alert not found"}), 404
        data       = req.get_json(silent=True) or {}
        case_title = data.get("title") or f"SENTINEL: {alert['alert_type']} ({alert['created_at'][:10]})"
        hypothesis = f"Pattern: {alert['summary'][:200]}"
        try:
            cur = db.execute(
                "INSERT INTO cases (name, description, hypothesis, status, case_type, source_type) "
                "VALUES (?, ?, ?, 'active', 'general', 'live') ",
                (case_title,
                 f"Auto-generated from Sentinel alert #{alert_id}. "
                 f"Type: {alert['alert_type']} | "
                 f"Confidence: {alert['confidence_score']:.0%}",
                 hypothesis)
            )
            case_id = cur.lastrowid
            db.execute(
                "UPDATE sentinel_alerts SET status='acknowledged' WHERE id=?",
                (alert_id,)
            )
            db.commit()
            return jsonify({"case_id": case_id, "status": "promoted"})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/sentinel/run", methods=["POST"])
    def api_sentinel_run():
        """Trigger a Sentinel analysis run. Option B: on-demand."""
        from flask import jsonify
        try:
            from forage.processors.sentinel import Sentinel
            result = Sentinel(db_path=DB_PATH).run()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/signals/streams")
    def api_signals_streams():
        """Phase 27: stream counts summary used by dashboard and feed."""
        from flask import jsonify
        db = get_db()
        try:
            rows = db.execute(
                "SELECT stream, COUNT(*) AS total, "
                "SUM(CASE WHEN status='raw' THEN 1 ELSE 0 END) AS raw, "
                "SUM(CASE WHEN is_priority=1 THEN 1 ELSE 0 END) AS priority, "
                "MAX(timestamp) AS latest "
                "FROM signals WHERE stream IS NOT NULL AND source_type = 'live' "
                "GROUP BY stream ORDER BY total DESC"
            ).fetchall()
        except Exception:
            rows = []
        return jsonify({"streams": [dict(r) for r in rows]})

    @app.route("/api/decay/run", methods=["POST"])
    def api_decay_run():
        """Phase 28: Trigger a decay pass on all signals. Option B: on-demand."""
        from flask import jsonify
        try:
            from forage.engines.decay_engine import DecayEngine
            result = DecayEngine(db_path=DB_PATH).run()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Phase 29: Intelligence Feed — /feed  +  /api/feed
    # -----------------------------------------------------------------------

    # Stream → numeric weight used in feed_score formula
    _STREAM_WEIGHTS = {
        "CRIME_INTEL":    1.0,
        "PRIORITY":       0.9,
        "INFRASTRUCTURE": 0.7,
        "GLOBAL":         0.3,
    }
    _STREAM_WEIGHT_DEFAULT = 0.3

    @app.route("/feed")
    def feed():
        """Phase 29: Analyst Intelligence Feed page."""
        return render_template("feed.html")

    @app.route("/api/feed")
    def api_feed():
        """
        CT-1 / Phase 29.3: Unified, ranked intelligence feed with optional
        gravity-based contextual tunneling.

        Query params
        ───────────
          limit      (int, default 50, max 200)
          stream     (str)   — filter to a single stream; omit for all
          offset     (int)   — for infinite scroll pagination
          case_id    (int)   — activate CT-1 gravity scoring against this case
          gravity    (int)   — gravity weight 0–100 (default 50 when case_id set)
          lens       (str)   — 'live' | 'seed' | 'all'

        Item types returned
        ───────────────────
          SENTINEL_ALERT    — new sentinel escalations; FIRMS-sourced alerts excluded
          SIGNAL            — ranked signals; FIRMS signals shown only if high-impact
          CORRELATION       — non-FIRMS pairs with score >= 0.85; stream_weight = 1.0
          INTELLIGENCE_LEAD — actors in the top 10% by influence_score (90th-pct subquery)

        CT-1 gravity blending (when case_id provided)
        ──────────────────────────────────────────────
          gravity_score  = actor_match×0.50 + location_match×0.30 + keyword_match×0.20
          final_score    = (1 - gw) × feed_score + gw × gravity_score
          where gw       = gravity / 100  (default 0.50)

        FIRMS noise-reduction (Phase 29.3)
        ───────────────────────────────────
          SENTINEL_ALERT : exclude any alert whose summary references [firms]
          CORRELATION    : exclude any pair where either signal is source='firms'
          SIGNAL (FIRMS) : show only if is_priority=1 OR isolated, and not redundant
        """
        from flask import jsonify

        db     = get_db()
        limit  = min(int(request.args.get("limit",  50)),  200)
        offset = max(int(request.args.get("offset",  0)),    0)
        stream_filter = request.args.get("stream", "").strip().upper() or None

        lens = request.args.get('lens', 'live').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'live'

        if lens == 'all':
            source_type_clause = "1=1"
            source_type_param = None
            sentinel_allowed = True
        else:
            source_type_clause = "s.source_type = ?"
            source_type_param = lens
            sentinel_allowed = (lens == 'live')

        # ── CT-1: gravity params ─────────────────────────────────────────────
        try:
            ct_case_id = int(request.args.get("case_id", 0)) or None
        except (ValueError, TypeError):
            ct_case_id = None
        try:
            ct_gravity = max(0, min(100, int(request.args.get("gravity", 50))))
        except (ValueError, TypeError):
            ct_gravity = 50
        gravity_weight = ct_gravity / 100.0  # 0.0–1.0

        items = []

        # ── 1. SENTINEL_ALERT items ─────────────────────────────────────────
        # Score: confidence-weighted 0.20–1.00 → always floats above signals.
        #
        # Phase 29.4 — Sentinel Density Gate
        # ────────────────────────────────────
        # FIRMS data floods sentinel_alerts via two alert_type paths, each
        # requiring a different suppression strategy:
        #
        # 1. correlation_escalation + '[firms]' in summary
        #    → Pure pixel-pair noise (0.4 km / 0.0 h). Always exclude.
        #    → Identified by summary LIKE '%[firms]%'
        #
        # 2. cluster_spike + 'Sources: firms' in summary
        #    → Regional fire cluster. May be genuine (Khuzestan, Nebraska).
        #    → Density gate: exclude only if signal_count < 20.
        #    → 20+ signals in a 100 km radius = major regional event.
        #
        # 3. cluster_spike without FIRMS source → standard guard (>= 3).
        #
        # 4. All other alert types (actor_match etc.) → standard guard (>= 3).
        #
        # Cap: max 5 SENTINEL_ALERT items per feed call so other item types
        # always get page-space regardless of alert volume.
        if sentinel_allowed:
            try:
                sa_rows = db.execute("""
                    SELECT id, alert_type, confidence_score, signal_count,
                           summary, location_lat, location_lon,
                           status, created_at
                    FROM   sentinel_alerts
                    WHERE  status = 'new'
                      AND  (
                    -- Rule 1: correlation_escalation — exclude all FIRMS pixel-pairs
                    (
                        alert_type = 'correlation_escalation'
                        AND summary NOT LIKE '%[firms]%'
                        AND signal_count >= 10
                    )
                    OR
                    -- Rule 2: cluster_spike FIRMS — density gate, major clusters only
                    (
                        alert_type = 'cluster_spike'
                        AND summary LIKE '%Sources: firms%'
                        AND signal_count >= 20
                    )
                    OR
                    -- Rule 3: cluster_spike non-FIRMS — standard minimum guard
                    (
                        alert_type = 'cluster_spike'
                        AND summary NOT LIKE '%Sources: firms%'
                        AND signal_count >= 3
                    )
                    OR
                    -- Rule 4: all other types (actor_match etc.) — standard guard
                    (
                        alert_type NOT IN ('correlation_escalation', 'cluster_spike')
                        AND signal_count >= 3
                    )
                  )
                ORDER  BY confidence_score DESC, signal_count DESC, created_at DESC
                LIMIT  5
            """).fetchall()
                for r in sa_rows:
                    items.append({
                        "item_type":        "SENTINEL_ALERT",
                        "id":               f"sa-{r['id']}",
                        "alert_id":         r["id"],
                        "feed_score":       round(0.20 + r["confidence_score"] * 0.80, 4),
                        "title":            r["summary"][:120],
                        "summary":          r["summary"],
                        "alert_type":       r["alert_type"],
                        "confidence_score": r["confidence_score"],
                        "signal_count":     r["signal_count"],
                        "location_lat":     r["location_lat"],
                        "location_lon":     r["location_lon"],
                        "timestamp":        r["created_at"],
                        "stream":           None,
                        "source":           "SENTINEL",
                        "is_priority":      0,
                    })
            except Exception:
                pass

        # ── 2. SIGNAL items ─────────────────────────────────────────────────
        # sentinel_flag: 1 if a non-dismissed sentinel alert exists within
        # ~100 km (±0.9°) in the last 6 h — bounding-box keeps it fast.
        #
        # FIRMS high-impact filter (Phase 29.3):
        # For source='firms' signals we only surface them if they are NOT
        # already covered by a sentinel/correlation AND meet one of:
        #   (a) is_priority = 1   — intensity threshold hit by ingestion rules
        #   (b) isolated          — no other firms signal within ±0.45° / 24 h
        #                           (±0.45° ≈ 50 km at mid-latitudes)
        # Non-FIRMS signals pass through without additional filtering.
        try:
            stream_clause = "AND s.stream = :stream" if stream_filter else ""
            params = {}
            if stream_filter:
                params["stream"] = stream_filter
            if source_type_param is not None:
                params["source_type"] = source_type_param

            sig_rows = db.execute(f"""
                SELECT
                    s.signal_id,
                    s.title,
                    s.content,
                    s.source,
                    s.stream,
                    s.timestamp,
                    s.status,
                    s.is_priority,
                    s.lat,
                    s.lng,
                    COALESCE(s.relevance_score, 1.0) AS relevance_score,
                    CASE
                        WHEN s.lat IS NOT NULL AND s.lng IS NOT NULL
                             AND EXISTS (
                                 SELECT 1 FROM sentinel_alerts sa
                                 WHERE  sa.status    != 'dismissed'
                                   AND  sa.created_at >= datetime('now', '-6 hours')
                                   AND  sa.location_lat BETWEEN s.lat - 0.9 AND s.lat + 0.9
                                   AND  sa.location_lon BETWEEN s.lng - 0.9 AND s.lng + 0.9
                             )
                        THEN 1.0 ELSE 0.0
                    END AS sentinel_flag,
                    -- Isolation flag: 1 if NO other firms signal nearby in 24 h
                    CASE
                        WHEN s.source = 'firms'
                             AND s.lat IS NOT NULL AND s.lng IS NOT NULL
                             AND NOT EXISTS (
                                 SELECT 1 FROM signals nb
                                 WHERE  nb.source    = 'firms'
                                   AND  nb.signal_id != s.signal_id
                                   AND  nb.timestamp >= datetime('now', '-24 hours')
                                   AND  nb.lat BETWEEN s.lat - 0.45 AND s.lat + 0.45
                                   AND  nb.lng BETWEEN s.lng - 0.45 AND s.lng + 0.45
                             )
                        THEN 1 ELSE 0
                    END AS firms_isolated,
                    -- Redundancy flag: 1 if already in a sentinel or correlation
                    CASE
                        WHEN s.source = 'firms' AND (
                             EXISTS (
                                 SELECT 1 FROM sentinel_alerts sa2
                                 WHERE  sa2.status != 'dismissed'
                                   AND  sa2.location_lat BETWEEN s.lat - 0.9 AND s.lat + 0.9
                                   AND  sa2.location_lon BETWEEN s.lng - 0.9 AND s.lng + 0.9
                             )
                          OR EXISTS (
                                 SELECT 1 FROM correlated_incidents ci
                                 WHERE  ci.signal_a = s.signal_id
                                    OR  ci.signal_b = s.signal_id
                             )
                        )
                        THEN 1 ELSE 0
                    END AS firms_redundant
                FROM  signals s
                WHERE s.status IN ('raw', 'promoted')
                  AND (" + source_type_clause + ")
                " + stream_clause + "
                ORDER BY s.is_priority DESC, s.relevance_score DESC
                LIMIT  500
            """, params).fetchall()

            for r in sig_rows:
                # FIRMS high-impact gate
                if r["source"] == "firms":
                    # Suppress if already covered by sentinel/correlation
                    if r["firms_redundant"]:
                        continue
                    # Only pass through if priority OR isolated
                    if not r["is_priority"] and not r["firms_isolated"]:
                        continue

                sw    = _STREAM_WEIGHTS.get(r["stream"] or "GLOBAL", _STREAM_WEIGHT_DEFAULT)
                rel   = float(r["relevance_score"] or 1.0)
                prio  = float(r["is_priority"]     or 0)
                sflag = float(r["sentinel_flag"]   or 0)
                score = round(rel * 0.40 + prio * 0.30 + sflag * 0.20 + sw * 0.10, 4)
                items.append({
                    "item_type":       "SIGNAL",
                    "id":              f"sig-{r['signal_id']}",
                    "signal_id":       r["signal_id"],
                    "feed_score":      score,
                    "title":           r["title"]    or "(untitled)",
                    "summary":         (r["content"] or "")[:200],
                    "source":          r["source"]   or "",
                    "stream":          r["stream"]   or "GLOBAL",
                    "timestamp":       r["timestamp"],
                    "is_priority":     r["is_priority"],
                    "relevance_score": round(rel, 3),
                    "sentinel_flag":   sflag,
                    "stream_weight":   sw,
                })
        except Exception:
            pass

        # ── 3. CORRELATION items ────────────────────────────────────────────
        # Threshold: >= 0.85 (strong patterns only).
        # stream_weight: pinned to CRIME_INTEL (1.0) — patterns always compete
        #   at the top of the feed regardless of constituent signal streams.
        # FIRMS exclusion (Phase 29.3): exclude any pair where either signal
        #   is source='firms'. Fire pixel-pairs are not investigative incidents.
        try:
            corr_params = {}
            corr_source_clause = ""
            if source_type_param is not None:
                corr_source_clause = "\n                  AND  sa.source_type = :source_type\n                  AND  sb.source_type = :source_type"
                corr_params['source_type'] = source_type_param

            corr_rows = db.execute("""
                SELECT ci.id,
                       ci.correlation_score,
                       ci.distance_km,
                       ci.time_difference_hours,
                       ci.detected_at,
                       sa.title          AS title_a,
                       sa.source         AS src_a,
                       sa.stream         AS stream_a,
                       sa.is_priority    AS prio_a,
                       COALESCE(sa.relevance_score, 1.0) AS rel_a,
                       sb.title          AS title_b,
                       sb.source         AS src_b,
                       sb.stream         AS stream_b,
                       sb.is_priority    AS prio_b,
                       COALESCE(sb.relevance_score, 1.0) AS rel_b
                FROM   correlated_incidents ci
                JOIN   signals sa ON sa.signal_id = ci.signal_a
                JOIN   signals sb ON sb.signal_id = ci.signal_b
                WHERE  ci.correlation_score >= 0.85
                  AND  sa.source != 'firms'
                  AND  sb.source != 'firms'" + corr_source_clause + "
                ORDER  BY ci.correlation_score DESC
                LIMIT  100
            """, corr_params).fetchall()

            for r in corr_rows:
                rel   = (float(r["rel_a"]) + float(r["rel_b"])) / 2.0
                prio  = max(r["prio_a"] or 0, r["prio_b"] or 0)
                sw    = 1.0   # pinned: patterns are always CRIME_INTEL weight
                score = round(rel * 0.40 + prio * 0.30 + 0.0 * 0.20 + sw * 0.10, 4)
                items.append({
                    "item_type":        "CORRELATION",
                    "id":               f"corr-{r['id']}",
                    "corr_db_id":       r["id"],
                    "feed_score":       score,
                    "title":            (
                        f"Pattern: {(r['title_a'] or '')[:50]} "
                        f"↔ {(r['title_b'] or '')[:50]}"
                    ),
                    "summary":          (
                        f"Correlation score {r['correlation_score']:.3f} · "
                        f"{r['distance_km']:.0f} km apart · "
                        f"{r['time_difference_hours']:.1f} h apart"
                    ),
                    "correlation_score": r["correlation_score"],
                    "distance_km":      r["distance_km"],
                    "time_diff_hours":  r["time_difference_hours"],
                    "title_a":          r["title_a"] or "",
                    "title_b":          r["title_b"] or "",
                    "source":           r["src_a"]   or "",
                    "stream":           r["stream_a"] or "GLOBAL",
                    "timestamp":        r["detected_at"],
                    "is_priority":      prio,
                    "stream_weight":    sw,
                })
        except Exception:
            pass

        # ── 4. INTELLIGENCE_LEAD items ──────────────────────────────────────
        # "Top 10%" = influence_score >= 90th-percentile value, computed once
        # via NTILE(10) window function (SQLite >= 3.25, ships with Win10+).
        # Fallback: threshold = 0.0 if table is empty or ntile unavailable.
        # p90_threshold is included in the item payload for UI transparency.
        try:
            p90_row = db.execute("""
                SELECT influence_score AS p90
                FROM (
                    SELECT influence_score,
                           NTILE(10) OVER (ORDER BY influence_score ASC) AS decile
                    FROM   actor_network_metrics
                    WHERE  influence_score > 0
                )
                WHERE decile = 10
                ORDER BY influence_score ASC
                LIMIT  1
            """).fetchone()

            p90_threshold = float(p90_row["p90"]) if p90_row else 0.0

            lead_rows = db.execute("""
                SELECT m.actor_id, a.name, a.type,
                       m.influence_score, m.betweenness, m.pagerank,
                       m.community_id, m.computed_at
                FROM   actor_network_metrics m
                JOIN   actors a ON a.actor_id = m.actor_id
                WHERE  m.influence_score >= :threshold
                ORDER  BY m.influence_score DESC
                LIMIT  50
            """, {"threshold": p90_threshold}).fetchall()

            for r in lead_rows:
                inf = float(r["influence_score"] or 0)
                # Normalise to 0–1 for feed_score: inf / (inf + 1) is a
                # smooth sigmoid that never exceeds 1 regardless of raw value.
                rel_proxy = inf / (inf + 1.0) if inf > 0 else 0.0
                # stream_weight uses PRIORITY tier (0.9) — actor leads are
                # strategic intelligence, not real-time operational signals.
                score = round(rel_proxy * 0.40 + 0.0 * 0.30 + 0.0 * 0.20 + 0.9 * 0.10, 4)
                items.append({
                    "item_type":       "INTELLIGENCE_LEAD",
                    "id":              f"lead-{r['actor_id']}",
                    "actor_id":        r["actor_id"],
                    "feed_score":      score,
                    "title":           f"Actor: {r['name']}",
                    "summary":         (
                        f"Type: {r['type']} · "
                        f"Influence {inf:.3f} · "
                        f"PageRank {(r['pagerank'] or 0):.4f}"
                    ),
                    "actor_name":      r["name"],
                    "actor_type":      r["type"],
                    "influence_score": inf,
                    "p90_threshold":   p90_threshold,
                    "community_id":    r["community_id"],
                    "computed_at":     r["computed_at"],
                    "source":          "GRAPH",
                    "stream":          None,
                    "timestamp":       r["computed_at"],
                    "is_priority":     0,
                })
        except Exception:
            pass

        # ── CT-1: gravity scoring pass ────────────────────────────────────────
        ct_context = None
        if ct_case_id:
            try:
                from core.gravity import build_context, score_item, blend_score
                ct_context = build_context(db, ct_case_id)
                for item in items:
                    gs = score_item(item, ct_context)
                    item["gravity_score"] = gs
                    item["feed_score"]    = blend_score(
                        item["feed_score"], gs, gravity_weight
                    )
            except Exception:
                ct_context = None  # degrade gracefully to Phase 29.3 behaviour

        # ── Sort: feed_score DESC, then timestamp DESC as tiebreaker ─────────
        def _sort_key(item):
            ts = item.get("timestamp") or "1970-01-01"
            return (item["feed_score"], ts)

        items.sort(key=_sort_key, reverse=True)

        # ── Paginate ─────────────────────────────────────────────────────────
        total = len(items)
        page  = items[offset : offset + limit]

        return jsonify({
            "total":      total,
            "offset":     offset,
            "limit":      limit,
            "has_more":   (offset + limit) < total,
            "items":      page,
            "ct_active":  ct_case_id is not None and ct_context is not None,
            "ct_case_id": ct_case_id,
            "gravity":    ct_gravity if ct_case_id else None,
        })

    @app.route("/api/anomaly/run", methods=["POST"])
    def api_anomaly_run():
        """Trigger a full anomaly detection pass (Option B: on-demand)."""
        from flask import jsonify
        try:
            from forage.engines.anomaly_engine import AnomalyEngine
            result = AnomalyEngine(db_path=DB_PATH).run()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/anomaly/baselines")
    def api_anomaly_baselines():
        """Return baseline coverage stats — useful for the admin panel."""
        from flask import jsonify
        db = get_db()
        try:
            meta = db.execute(
                "SELECT COUNT(*) AS rows, "
                "COUNT(DISTINCT source) AS sources, "
                "COUNT(DISTINCT region_key) AS regions, "
                "MIN(bucket_date) AS earliest, "
                "MAX(bucket_date) AS latest "
                "FROM signal_baselines"
            ).fetchone()
            breakdown = db.execute(
                "SELECT source, COUNT(*) AS rows, "
                "COUNT(DISTINCT region_key) AS regions "
                "FROM signal_baselines "
                "GROUP BY source ORDER BY rows DESC"
            ).fetchall()
        except Exception:
            return jsonify({"rows": 0, "sources": 0, "regions": 0})
        return jsonify({
            "rows":     meta["rows"],
            "sources":  meta["sources"],
            "regions":  meta["regions"],
            "earliest": meta["earliest"],
            "latest":   meta["latest"],
            "breakdown": [dict(r) for r in breakdown],
        })

    # ── CT-1: Case anchors ────────────────────────────────────────────────────

    @app.route("/api/cases/<int:case_id>/anchors")
    def api_case_anchors(case_id: int):
        """
        CT-1: Return the gravity anchors for a case — actors, location count,
        keywords, and signal stats. Used by the feed UI to display the CT banner.
        """
        from flask import jsonify
        db = get_db()
        case = db.execute(
            "SELECT case_id, name, status FROM cases WHERE case_id = ?",
            (case_id,)
        ).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404

        try:
            from core.gravity import build_context
            ctx = build_context(db, case_id)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        actor_rows = db.execute("""
            SELECT a.actor_id, a.name, a.type
            FROM   case_actors ca
            JOIN   actors a ON a.actor_id = ca.actor_id
            WHERE  ca.case_id = ?
        """, (case_id,)).fetchall()

        return jsonify({
            "case_id":       case_id,
            "case_title":    case["name"],
            "case_status":   case["status"],
            "actors":        [dict(r) for r in actor_rows],
            "signal_count":  db.execute(
                "SELECT COUNT(*) FROM case_signals WHERE case_id = ?",
                (case_id,)
            ).fetchone()[0],
            "location_count": len(ctx["locations"]),
            "keyword_count":  len(ctx["keywords"]),
            "keywords_sample": sorted(ctx["keywords"])[:20],
        })

    # ── CT-1: Fetch suggestions ───────────────────────────────────────────────

    @app.route("/api/cases/<int:case_id>/fetch-suggestions")
    def api_case_fetch_suggestions(case_id: int):
        """
        CT-1: Return the top 20 signals NOT already pinned to this case,
        ranked by gravity_score against the case context. Helps the analyst
        discover evidence they may have missed.
        """
        from flask import jsonify
        db = get_db()
        if not db.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone():
            return jsonify({"error": "Case not found"}), 404

        try:
            from core.gravity import build_context, score_item
            ctx = build_context(db, case_id)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        # Get signals not yet pinned to this case
        candidate_rows = db.execute("""
            SELECT s.signal_id, s.title, s.content, s.source, s.stream,
                   s.timestamp, s.lat, s.lng, s.is_priority,
                   COALESCE(s.relevance_score, 0.5) AS relevance_score
            FROM   signals s
            WHERE  s.status IN ('raw', 'promoted')
              AND  s.signal_id NOT IN (
                       SELECT signal_id FROM case_signals WHERE case_id = ?
                   )
            ORDER  BY s.relevance_score DESC, s.timestamp DESC
            LIMIT  500
        """, (case_id,)).fetchall()

        scored = []
        for r in candidate_rows:
            item = {
                "item_type":      "SIGNAL",
                "signal_id":      r["signal_id"],
                "title":          r["title"] or "(untitled)",
                "summary":        (r["content"] or "")[:200],
                "source":         r["source"] or "",
                "stream":         r["stream"] or "GLOBAL",
                "timestamp":      r["timestamp"],
                "is_priority":    r["is_priority"],
                "relevance_score": float(r["relevance_score"] or 0),
                "lat":            r["lat"],
                "lng":            r["lng"],
            }
            item["gravity_score"] = score_item(item, ctx)
            scored.append(item)

        scored.sort(key=lambda x: x["gravity_score"], reverse=True)
        top = [s for s in scored if s["gravity_score"] > 0][:20]

        return jsonify({
            "case_id":    case_id,
            "total":      len(top),
            "suggestions": top,
        })

    # ── CT-1: Surface signals with gravity column ─────────────────────────────

    @app.route("/api/surface/signals/context")
    def api_surface_signals_context():
        """
        CT-1: Signal monitor feed with gravity_score column relative to
        case_id. Backs the ⊡ Context Signals view in signals.html.

        Query params
        ────────────
          case_id  (int, required) — active case to score against
          limit    (int, default 50, max 200)
          offset   (int, default 0)
          stream   (str) — optional stream filter
        """
        from flask import jsonify
        db = get_db()

        try:
            case_id = int(request.args.get("case_id", 0))
        except (ValueError, TypeError):
            return jsonify({"error": "case_id required"}), 400
        if not case_id:
            return jsonify({"error": "case_id required"}), 400

        limit  = min(int(request.args.get("limit",  50)), 200)
        offset = max(int(request.args.get("offset",  0)),   0)
        stream_filter = request.args.get("stream", "").strip().upper() or None

        stream_clause = "AND s.stream = ?" if stream_filter else ""
        params = [stream_filter] if stream_filter else []

        rows = db.execute(f"""
            SELECT s.signal_id, s.title, s.content, s.source, s.stream,
                   s.timestamp, s.lat, s.lng, s.is_priority, s.status,
                   COALESCE(s.relevance_score, 0.5) AS relevance_score,
                   COUNT(cs.case_id) AS pinned_case_count
            FROM   signals s
            LEFT   JOIN case_signals cs ON cs.signal_id = s.signal_id
            WHERE  s.status IN ('raw', 'promoted')
            {stream_clause}
            GROUP  BY s.signal_id
            ORDER  BY s.is_priority DESC, s.relevance_score DESC
            LIMIT  500
        """, params).fetchall()

        try:
            from core.gravity import build_context, score_item
            ctx = build_context(db, case_id)
        except Exception:
            ctx = {}

        items = []
        for r in rows:
            item = {
                "signal_id":       r["signal_id"],
                "title":           r["title"] or "(untitled)",
                "summary":         (r["content"] or "")[:160],
                "source":          r["source"] or "",
                "stream":          r["stream"] or "GLOBAL",
                "timestamp":       r["timestamp"],
                "lat":             r["lat"],
                "lng":             r["lng"],
                "is_priority":     r["is_priority"],
                "relevance_score": float(r["relevance_score"] or 0),
                "pinned_case_count": r["pinned_case_count"],
                "item_type":       "SIGNAL",
                "gravity_score":   score_item(
                    {"item_type": "SIGNAL", "signal_id": r["signal_id"],
                     "title": r["title"], "summary": (r["content"] or "")[:160],
                     "lat": r["lat"], "lng": r["lng"]},
                    ctx
                ) if ctx else 0.0,
            }
            items.append(item)

        items.sort(key=lambda x: (x["gravity_score"], x["relevance_score"]), reverse=True)
        page = items[offset: offset + limit]

        return jsonify({
            "case_id":  case_id,
            "total":    len(items),
            "offset":   offset,
            "limit":    limit,
            "has_more": (offset + limit) < len(items),
            "items":    page,
        })

    # ── E-2: Case evidence graph ──────────────────────────────────────────────

    @app.route("/api/cases/<int:case_id>/evidence-graph")
    def api_case_evidence_graph(case_id: int):
        """
        E-2: Return nodes (actors) and edges (entity_relationships) scoped to
        the actors pinned to this case.

        Nodes: all actors in case_actors for this case, enriched with
               actor_network_metrics (influence, betweenness, community).
        Edges: entity_relationships where BOTH subject AND object are case actors.
               Also includes one-hop edges where only one end is a case actor
               (flag: 'bridging': true) — surfaces connected actors not yet in case.

        Response is Cytoscape.js-compatible:
          { nodes: [{data: {...}}], edges: [{data: {...}}] }
        """
        from flask import jsonify
        db = get_db()

        if not db.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone():
            return jsonify({"error": "Case not found"}), 404

        # Case actors
        case_actor_rows = db.execute("""
            SELECT a.actor_id, a.name, a.type,
                   m.influence_score, m.betweenness, m.pagerank, m.community_id
            FROM   case_actors ca
            JOIN   actors a ON a.actor_id = ca.actor_id
            LEFT JOIN actor_network_metrics m ON m.actor_id = a.actor_id
            WHERE  ca.case_id = ?
        """, (case_id,)).fetchall()

        case_actor_ids = {r["actor_id"] for r in case_actor_rows}

        if not case_actor_ids:
            return jsonify({"nodes": [], "edges": [], "case_id": case_id,
                            "total_actors": 0, "total_edges": 0})

        # All edges touching at least one case actor
        placeholders = ",".join("?" * len(case_actor_ids))
        id_list = list(case_actor_ids)

        edge_rows = db.execute(f"""
            SELECT er.subject_actor_id, er.object_actor_id,
                   er.relation_type, er.confidence,
                   ar.title AS artifact_title,
                   ar.artifact_id
            FROM   entity_relationships er
            LEFT JOIN artifacts ar ON ar.artifact_id = er.source_artifact_id
            WHERE  er.subject_actor_id IN ({placeholders})
               OR  er.object_actor_id  IN ({placeholders})
        """, id_list + id_list).fetchall()

        # Collect bridging actor ids (one-hop neighbours not in case)
        bridging_ids = set()
        for e in edge_rows:
            if e["subject_actor_id"] not in case_actor_ids:
                bridging_ids.add(e["subject_actor_id"])
            if e["object_actor_id"] not in case_actor_ids:
                bridging_ids.add(e["object_actor_id"])

        # Fetch bridging actor data
        bridging_nodes = []
        if bridging_ids:
            bph = ",".join("?" * len(bridging_ids))
            bridging_rows = db.execute(f"""
                SELECT a.actor_id, a.name, a.type,
                       m.influence_score, m.betweenness, m.pagerank, m.community_id
                FROM   actors a
                LEFT JOIN actor_network_metrics m ON m.actor_id = a.actor_id
                WHERE  a.actor_id IN ({bph})
            """, list(bridging_ids)).fetchall()
            bridging_nodes = [dict(r) for r in bridging_rows]

        # Build Cytoscape node list
        nodes = []
        for r in case_actor_rows:
            nodes.append({"data": {
                "id":           str(r["actor_id"]),
                "label":        r["name"],
                "type":         r["type"] or "unknown",
                "influence":    round(float(r["influence_score"] or 0), 4),
                "betweenness":  round(float(r["betweenness"] or 0), 4),
                "pagerank":     round(float(r["pagerank"] or 0), 4),
                "community":    r["community_id"],
                "in_case":      True,
            }})
        for r in bridging_nodes:
            nodes.append({"data": {
                "id":           str(r["actor_id"]),
                "label":        r["name"],
                "type":         r["type"] or "unknown",
                "influence":    round(float(r["influence_score"] or 0), 4),
                "betweenness":  round(float(r["betweenness"] or 0), 4),
                "pagerank":     round(float(r["pagerank"] or 0), 4),
                "community":    r["community_id"],
                "in_case":      False,
            }})

        # Build Cytoscape edge list
        edges = []
        seen_edges: set = set()
        for e in edge_rows:
            key = (e["subject_actor_id"], e["object_actor_id"], e["relation_type"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            bridging = (
                e["subject_actor_id"] not in case_actor_ids or
                e["object_actor_id"]  not in case_actor_ids
            )
            edges.append({"data": {
                "id":             f"{e['subject_actor_id']}-{e['object_actor_id']}-{e['relation_type']}",
                "source":         str(e["subject_actor_id"]),
                "target":         str(e["object_actor_id"]),
                "relation":       e["relation_type"],
                "confidence":     round(float(e["confidence"] or 0), 3),
                "artifact_title": e["artifact_title"],
                "artifact_id":    e["artifact_id"],
                "bridging":       bridging,
            }})

        return jsonify({
            "case_id":     case_id,
            "total_actors": len(nodes),
            "total_edges":  len(edges),
            "nodes":        nodes,
            "edges":        edges,
        })

    @app.route("/api/graph/metrics")
    def api_graph_metrics():
        from flask import jsonify
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

    @app.route("/api/graph/recalculate", methods=["POST"])
    def api_graph_recalculate():
        from flask import jsonify
        try:
            from forage.engines.graph_engine import GraphEngine
            result = GraphEngine(db_path=DB_PATH).run()
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Path B: Actor Intelligence Graph
    # /intel-graph        — full-page Cytoscape.js visualization
    # /api/actor-network  — nodes (actors+metrics) + edges (entity_relationships)
    # /api/actor/<id>/panel — click-panel data: signals, artifacts, risk
    # -----------------------------------------------------------------------

    @app.route("/intel-graph")
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

    @app.route("/api/actor-network")
    def api_actor_network():
        from flask import jsonify, request as req
        db = get_db()

        hard_only = req.args.get("hard_only", "false").lower() == "true"
        show_all  = req.args.get("show_all",  "false").lower() == "true"

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

    @app.route("/api/actor/<int:actor_id>/panel")
    def api_actor_panel(actor_id: int):
        from flask import jsonify
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

    @app.route("/api/relationships", methods=["GET"])
    def api_relationships():
        from flask import jsonify
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

    @app.route("/api/relationships", methods=["POST"])
    def api_relationship_create():
        from flask import jsonify, request as req
        db   = get_db()
        data = req.get_json(silent=True) or {}
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

    @app.route("/api/relationships/<int:relationship_id>", methods=["DELETE"])
    def api_relationship_delete(relationship_id: int):
        from flask import jsonify
        db = get_db()
        try:
            db.execute("DELETE FROM entity_relationships WHERE relationship_id=?",
                       (relationship_id,))
            db.commit()
            return jsonify({"status": "deleted", "relationship_id": relationship_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/actors/<int:actor_id>/relationships")
    def api_actor_relationships(actor_id: int):
        from flask import jsonify
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

        # All events this actor participated in — manual (actor_events) +
        # automated pipeline links (event_actors), deduplicated by event_id
        events = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   ae.role,
                   COUNT(DISTINCT a.artifact_id) AS artifact_count
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ae.actor_id = ?
            GROUP  BY e.event_id

            UNION

            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   ea.role,
                   COUNT(DISTINCT a.artifact_id) AS artifact_count
            FROM   event_actors ea
            JOIN   events e ON e.event_id = ea.event_id
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ea.actor_id = ?
            GROUP  BY e.event_id

            ORDER  BY 3 ASC
        """, (actor_id, actor_id)).fetchall()

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

        # Co-actors: actors sharing events via both manual and automated links
        co_actors = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type,
                   COUNT(DISTINCT e.event_id) AS shared_events,
                   GROUP_CONCAT(DISTINCT e.title) AS shared_event_names
            FROM (
                SELECT event_id FROM actor_events WHERE actor_id = ?
                UNION
                SELECT event_id FROM event_actors WHERE actor_id = ?
            ) my_events
            JOIN (
                SELECT event_id, actor_id FROM actor_events
                UNION
                SELECT event_id, actor_id FROM event_actors
            ) all_links ON all_links.event_id = my_events.event_id
                       AND all_links.actor_id != ?
            JOIN actors ac ON ac.actor_id = all_links.actor_id
            JOIN events e  ON e.event_id  = my_events.event_id
            GROUP  BY ac.actor_id
            ORDER  BY shared_events DESC
            LIMIT  8
        """, (actor_id, actor_id, actor_id)).fetchall()

        # Role timeline: each role this actor has held across events
        role_timeline = db.execute("""
            SELECT ae.role, e.event_id, e.title, e.date, e.category
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            WHERE  ae.actor_id = ?
              AND  ae.role IS NOT NULL
            ORDER  BY e.date
        """, (actor_id,)).fetchall()

        # Phase 21: network metrics
        network_metrics = None
        try:
            network_metrics = db.execute(
                "SELECT betweenness, eigenvector, pagerank, community_id, "
                "node_count, edge_count, computed_at "
                "FROM actor_network_metrics WHERE actor_id=?",
                (actor_id,)
            ).fetchone()
        except Exception: pass
        network_top = []
        try:
            network_top = db.execute(
                "SELECT m.actor_id, a.name, a.type, m.pagerank, m.community_id "
                "FROM actor_network_metrics m "
                "JOIN actors a ON a.actor_id=m.actor_id "
                "ORDER BY m.pagerank DESC LIMIT 10"
            ).fetchall()
        except Exception: pass

        # Phase 22: named relationships for this actor
        relationships = []
        try:
            relationships = db.execute(
                "SELECT r.relationship_id, r.subject_actor_id, r.object_actor_id, "
                "r.relation_type, r.description, r.confidence, r.extraction_method, "
                "a1.name AS subject_name, a2.name AS object_name "
                "FROM entity_relationships r "
                "JOIN actors a1 ON a1.actor_id=r.subject_actor_id "
                "JOIN actors a2 ON a2.actor_id=r.object_actor_id "
                "WHERE r.subject_actor_id=? OR r.object_actor_id=? "
                "ORDER BY r.confidence DESC",
                (actor_id, actor_id)
            ).fetchall()
        except Exception: pass

        # All actors list for the relationship form
        all_actors = []
        try:
            all_actors = db.execute(
                "SELECT actor_id, name, type FROM actors "
                "WHERE actor_id != ? ORDER BY name",
                (actor_id,)
            ).fetchall()
        except Exception: pass

        # Targeting: signals linked via relationship engine
        targeting = {
            "is_targeted": False,
            "signal_count": 0,
            "max_gravity": 0.0,
            "has_priority_signal": False,
            "threat_level": "none",
        }
        try:
            t = db.execute("""
                SELECT COUNT(DISTINCT sa.signal_id)        AS signal_count,
                       MAX(COALESCE(s.gravity_score, 0))   AS max_gravity,
                       MAX(COALESCE(s.is_priority, 0))     AS has_priority_signal
                FROM   signal_actors sa
                JOIN   signals s ON s.signal_id = sa.signal_id
                WHERE  sa.actor_id = ?
            """, (actor_id,)).fetchone()
            if t:
                max_g     = float(t["max_gravity"] or 0)
                has_pri   = bool(t["has_priority_signal"])
                sig_count = int(t["signal_count"] or 0)
                is_targeted = max_g >= 0.55 or has_pri
                if max_g >= 0.75 or has_pri:
                    threat_level = "critical"
                elif max_g >= 0.55:
                    threat_level = "elevated"
                elif max_g >= 0.35:
                    threat_level = "monitored"
                else:
                    threat_level = "none"
                targeting = {
                    "is_targeted":         is_targeted,
                    "signal_count":        sig_count,
                    "max_gravity":         round(max_g, 3),
                    "has_priority_signal": has_pri,
                    "threat_level":        threat_level,
                }
        except Exception: pass

        return render_template(
            "actor.html",
            actor=actor,
            events=events,
            artifact_footprint=artifact_footprint,
            co_actors=co_actors,
            role_timeline=role_timeline,
            network_metrics=network_metrics,
            network_top=network_top,
            relationships=relationships,
            all_actors=all_actors,
            targeting=targeting,
        )

    # -----------------------------------------------------------------------
    # Routes: /cases — Phase 8: Case Workspaces
    # -----------------------------------------------------------------------

    @app.route("/cases")
    def cases():
        db = get_db()

        lens = request.args.get('lens', 'all').lower()
        if lens not in ('live', 'seed', 'all'):
            lens = 'all'

        case_where = '' if lens == 'all' else 'WHERE c.source_type = ?'
        params = [] if lens == 'all' else [lens]

        cases_list = db.execute(f"""
            SELECT c.case_id, c.name, c.description, c.status, c.created_at,
                   COUNT(DISTINCT ca.artifact_id) AS artifact_count,
                   COUNT(DISTINCT ce.event_id)    AS event_count,
                   COUNT(DISTINCT cac.actor_id)   AS actor_count
            FROM   cases c
            LEFT JOIN case_artifacts ca  ON ca.case_id = c.case_id
            LEFT JOIN case_events    ce  ON ce.case_id = c.case_id
            LEFT JOIN case_actors    cac ON cac.case_id = c.case_id
            {case_where}
            GROUP BY c.case_id
            ORDER BY c.created_at DESC
        """, params).fetchall()
        return render_template("cases.html", cases=cases_list, lens=lens)

    @app.route("/cases/new", methods=["POST"])
    def case_new():
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        hypothesis  = request.form.get("hypothesis", "").strip() or None
        case_type   = request.form.get("case_type", "general")
        status      = request.form.get("status", "active")
        if not title:
            flash("Case title is required.", "error")
            return redirect(url_for("cases"))
        db = get_db()
        cur = db.execute(
            "INSERT INTO cases (name, description, hypothesis, case_type, status, source_type) VALUES (?, ?, ?, ?, ?, 'live')",
            (title, description, hypothesis, case_type, status),
        )
        new_case_id = cur.lastrowid
        db.commit()
        # Warm Start: auto-seed context_anchors from GAZETTEER scan of case text
        try:
            from core.gravity import extract_location_anchors
            import json as _json
            seed_text = " ".join(filter(None, [title, description, hypothesis]))
            anchors = extract_location_anchors(seed_text)
            if anchors:
                db.execute(
                    "UPDATE cases SET context_anchors = ? WHERE case_id = ?",
                    (_json.dumps(anchors), new_case_id),
                )
                db.commit()
        except Exception:
            pass
        flash(f"Case '{title}' created.", "success")
        return redirect(url_for("case_detail", case_id=new_case_id))

    @app.route("/cases/<int:case_id>")
    def case_detail(case_id: int):
        db = get_db()
        case = db.execute(
            "SELECT case_id, name, description, status, created_at, "
            "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
            "FROM cases WHERE case_id = ?", (case_id,)
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
            "SELECT case_id, name, status FROM cases ORDER BY created_at DESC"
        ).fetchall()

        # Phase 16: FORAGE signals pinned to this case, chronological.
        # Guard: case_signals may not exist if --init-db hasn't run yet.
        try:
            pinned_signals = db.execute("""
                SELECT s.signal_id,
                       s.source,
                       s.title,
                       s.content,
                       s.lat,
                       s.lng,
                       s.timestamp,
                       s.status,
                       s.is_priority,
                       s.cluster_id,
                       cs.note      AS pin_note,
                       cs.pinned_at
                FROM   case_signals cs
                JOIN   signals s ON s.signal_id = cs.signal_id
                WHERE  cs.case_id = ?
                ORDER  BY s.timestamp ASC
            """, (case_id,)).fetchall()
        except Exception:
            pinned_signals = []

        return render_template(
            "case_detail.html",
            case=case,
            artifacts=artifacts,
            events=events,
            actors=actors,
            pinned_signals=pinned_signals,
            all_cases=all_cases,
        )

    # -----------------------------------------------------------------------
    # Path B: Case Workbench — /workbench/<case_id>
    # Dedicated view: Evidence Timeline + Actor Roster + Overlap Panel + PDF.js
    # -----------------------------------------------------------------------

    @app.route("/workbench/<int:case_id>")
    def case_workbench(case_id: int):
        db   = get_db()
        case = db.execute(
            "SELECT case_id, name, description, status, created_at, "
            "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
            "FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            flash("Case not found.", "error")
            return redirect(url_for("cases"))

        # ── Roster: actors + influence scores + risk ─────────────────────────
        roster = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   COALESCE(m.influence_score, 0)  AS influence_score,
                   COALESCE(m.community_id, NULL)  AS community_id,
                   COALESCE(m.pagerank, 0)          AS pagerank,
                   cac.note, cac.pinned_at,
                   (SELECT COUNT(*) FROM entity_relationships er
                    WHERE (er.subject_actor_id = ac.actor_id
                        OR er.object_actor_id  = ac.actor_id)
                      AND er.relation_type != 'co_occurrence') AS rel_count
            FROM   case_actors cac
            JOIN   actors ac ON ac.actor_id = cac.actor_id
            LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
            WHERE  cac.case_id = ?
            ORDER  BY COALESCE(m.influence_score, 0) DESC, ac.name
        """, (case_id,)).fetchall()

        # ── Timeline: signals + artifacts merged chronologically ─────────────
        raw_signals = db.execute("""
            SELECT 'signal' AS kind,
                   s.signal_id    AS item_id,
                   s.title,
                   s.content      AS body,
                   s.timestamp    AS effective_ts,
                   s.source,
                   s.stream,
                   COALESCE(s.relevance_score, 0) AS relevance_score,
                   s.is_priority,
                   NULL           AS file_path,
                   NULL           AS artifact_type,
                   cs.note        AS pin_note
            FROM   case_signals cs
            JOIN   signals s ON s.signal_id = cs.signal_id
            WHERE  cs.case_id = ?
        """, (case_id,)).fetchall()

        raw_artifacts = db.execute("""
            SELECT 'artifact'     AS kind,
                   CAST(a.artifact_id AS TEXT) AS item_id,
                   a.title,
                   a.description  AS body,
                   COALESCE(a.date, ca.pinned_at) AS effective_ts,
                   a.source,
                   NULL           AS stream,
                   0.0            AS relevance_score,
                   0              AS is_priority,
                   a.file_path,
                   a.type         AS artifact_type,
                   ca.note        AS pin_note
            FROM   case_artifacts ca
            JOIN   artifacts a ON a.artifact_id = ca.artifact_id
            WHERE  ca.case_id = ?
        """, (case_id,)).fetchall()

        # Merge and sort — items without timestamp go to the bottom
        def _sort_ts(row):
            ts = row["effective_ts"]
            return ts if ts else "9999-99-99"

        timeline = sorted(
            [dict(r) for r in raw_signals] + [dict(r) for r in raw_artifacts],
            key=_sort_ts
        )

        # Pre-compute media URL for document artifacts
        for item in timeline:
            fp = item.get("file_path") or ""
            if fp:
                # Strip leading media/ to get the path relative to MEDIA_DIR
                rel = fp.replace("\\", "/")
                if rel.startswith("media/"):
                    rel = rel[len("media/"):]
                item["media_url"] = f"/media/{rel}"
            else:
                item["media_url"] = None

        # ── Overlap: actors in this case who appear in OTHER active cases ─────
        overlap = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type,
                   COALESCE(m.influence_score, 0) AS influence_score,
                   COUNT(DISTINCT ca2.case_id)    AS overlap_count,
                   GROUP_CONCAT(
                       c2.case_id || '||' || c2.name, ';;'
                   ) AS other_cases_raw
            FROM   case_actors ca1
            JOIN   actors ac  ON ac.actor_id  = ca1.actor_id
            LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
            JOIN   case_actors ca2 ON ca2.actor_id = ca1.actor_id
                                   AND ca2.case_id != ca1.case_id
            JOIN   cases c2   ON c2.case_id   = ca2.case_id
                               AND LOWER(c2.status) = 'active'
            WHERE  ca1.case_id = ?
            GROUP  BY ac.actor_id
            ORDER  BY overlap_count DESC, COALESCE(m.influence_score, 0) DESC
        """, (case_id,)).fetchall()

        # Parse the GROUP_CONCAT into structured lists
        overlap_parsed = []
        for row in overlap:
            actor_dict = dict(row)
            cases_raw = (row["other_cases_raw"] or "").split(";;")
            parsed_cases = []
            for raw in cases_raw:
                if "||" in raw:
                    cid_str, ctitle = raw.split("||", 1)
                    try:
                        parsed_cases.append({"case_id": int(cid_str), "title": ctitle})
                    except ValueError:
                        pass
            actor_dict["other_cases"] = parsed_cases
            del actor_dict["other_cases_raw"]
            desc = db.execute(
                "SELECT description FROM actors WHERE actor_id=?",
                (actor_dict["actor_id"],)
            ).fetchone()
            actor_dict["is_high_risk"] = (
                "HIGH_RISK" in (desc["description"] or "") if desc else False
            )
            overlap_parsed.append(actor_dict)

        stats = {
            "signals":   len(raw_signals),
            "artifacts": len(raw_artifacts),
            "actors":    len(roster),
            "overlap":   len(overlap_parsed),
        }

        return render_template(
            "case_workbench.html",
            case=case,
            roster=roster,
            timeline=timeline,
            overlap=overlap_parsed,
            stats=stats,
        )

    @app.route("/api/cases/<int:case_id>/overlap")
    def api_case_overlap(case_id: int):
        """Cross-case actor overlap — JSON for the overlap panel."""
        from flask import jsonify
        db   = get_db()
        case = db.execute("SELECT case_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404

        rows = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type,
                   COALESCE(m.influence_score, 0) AS influence_score,
                   COUNT(DISTINCT ca2.case_id)    AS overlap_count,
                   GROUP_CONCAT(c2.case_id || '||' || c2.name, ';;') AS other_cases_raw
            FROM   case_actors ca1
            JOIN   actors ac  ON ac.actor_id  = ca1.actor_id
            LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
            JOIN   case_actors ca2 ON ca2.actor_id = ca1.actor_id
                                   AND ca2.case_id != ca1.case_id
            JOIN   cases c2   ON c2.case_id = ca2.case_id AND LOWER(c2.status) = 'active'
            WHERE  ca1.case_id = ?
            GROUP  BY ac.actor_id
            ORDER  BY overlap_count DESC
        """, (case_id,)).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            parsed = []
            for raw in (d.pop("other_cases_raw") or "").split(";;"):
                if "||" in raw:
                    cid_str, ctitle = raw.split("||", 1)
                    try:
                        parsed.append({"case_id": int(cid_str), "title": ctitle})
                    except ValueError:
                        pass
            d["other_cases"] = parsed
            result.append(d)

        return jsonify({"case_id": case_id, "actors": result, "total": len(result)})

    # ── Stable 1.2: Case-Scoped Conclave (Context Tunnel) ────────────────────

    @app.route("/api/cases/<int:case_id>/run_conclave", methods=["POST"])
    def api_case_run_conclave(case_id: int):
        """
        Context Tunnel Conclave — synthesizes ONLY the intelligence belonging
        to this case.

        Sequence
        ────────
        1. Fetch all signal_ids from case_signals WHERE case_id=N
        2. Run NER + triple extraction scoped to those signals
        3. Compile a case-scoped wiki from case_actors seed entities
        4. Returns a pipeline_jobs job_id for telemetry tracking

        The global Control Room engines are untouched — this is additive.
        """
        from flask import jsonify
        import threading
        import datetime as _dt

        db = get_db()
        if not db.execute(
            "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone():
            return jsonify({"error": "Case not found"}), 404

        # Snapshot of signal IDs scoped to this case
        sig_rows = db.execute(
            "SELECT signal_id FROM case_signals WHERE case_id = ?", (case_id,)
        ).fetchall()
        signal_ids = [r["signal_id"] for r in sig_rows]

        actor_rows = db.execute(
            "SELECT actor_id FROM case_actors WHERE case_id = ?", (case_id,)
        ).fetchall()
        actor_ids = [r["actor_id"] for r in actor_rows]

        if not signal_ids:
            return jsonify({
                "error": "No signals pinned to this case yet. "
                         "Run a collector from the Workbench first.",
                "signal_count": 0,
            }), 422

        job_id = _create_job(
            f"conclave_case_{case_id}",
            f"Context Tunnel: {len(signal_ids)} signals, "
            f"{len(actor_ids)} actors"
        )

        def _tunnel_worker(jid: int, sids: list, aids: list, cid: int):
            """Background synthesis — NER, triples, wiki scoped to this case."""
            try:
                from core.db.connection import get_connection as _gc
                conn = _gc()

                # ── 1. NER pass over case signals ─────────────────────────
                _update_job(jid, message="Context Tunnel: running NER pass…")
                try:
                    from forage.processors.ner_processor import process_all as _ner
                    _ner(signal_ids=sids, db_path=str(DB_PATH))
                    _update_job(jid, message=f"NER complete ({len(sids)} signals)")
                except Exception as exc:
                    _update_job(jid, message=f"NER skipped: {exc}")

                # ── 2. Triple extraction scoped to case signals ───────────
                _update_job(jid, message="Context Tunnel: extracting triples…")
                try:
                    from forage.processors.triple_extractor import run as _triples
                    triples_written = _triples(signal_ids=sids, db_path=str(DB_PATH))
                    _update_job(jid, message=(
                        f"Triples: {triples_written or 0} relationships extracted"
                    ))
                except Exception as exc:
                    _update_job(jid, message=f"Triples skipped: {exc}")

                # ── 3. Case-scoped wiki compilation from seed actors ──────
                _update_job(jid, message="Context Tunnel: compiling case wiki…")
                try:
                    from forage.processors.wiki_compiler import compile_case as _wiki
                    wiki_count = _wiki(
                        case_id=cid, actor_ids=aids, db_path=str(DB_PATH)
                    )
                    _update_job(jid, message=(
                        f"Wiki: {wiki_count or 0} articles compiled for case {cid}"
                    ))
                except Exception as exc:
                    _update_job(jid, message=f"Wiki skipped: {exc}")

                conn.close()
                _finalize_job(
                    jid, "completed",
                    f"Context Tunnel complete — {len(sids)} signals synthesized",
                    records_out=len(sids),
                    progress=1.0,
                )

            except Exception as exc:
                _finalize_job(jid, "failed", f"Context Tunnel error: {exc}")

        threading.Thread(
            target=_tunnel_worker,
            args=(job_id, signal_ids, actor_ids, case_id),
            daemon=True,
        ).start()

        return jsonify({
            "status":       "started",
            "job_id":       job_id,
            "job_key":      f"conclave_case_{case_id}",
            "case_id":      case_id,
            "signal_count": len(signal_ids),
            "actor_count":  len(actor_ids),
        })

    @app.route("/cases/<int:case_id>/edit", methods=["POST"])
    def case_edit(case_id: int):
        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        hypothesis  = request.form.get("hypothesis", "").strip() or None
        case_type   = request.form.get("case_type", "general")
        status      = request.form.get("status", "active")
        if not title:
            flash("Case title is required.", "error")
            return redirect(url_for("case_detail", case_id=case_id))
        db = get_db()
        # Warm Start: recompute context_anchors whenever case text changes
        anchors_json = None
        try:
            from core.gravity import extract_location_anchors
            import json as _json
            seed_text = " ".join(filter(None, [title, description, hypothesis]))
            anchors = extract_location_anchors(seed_text)
            anchors_json = _json.dumps(anchors) if anchors else None
        except Exception:
            pass
        db.execute(
            "UPDATE cases SET name=?, description=?, hypothesis=?, case_type=?, status=?, context_anchors=? WHERE case_id=?",
            (title, description, hypothesis, case_type, status, anchors_json, case_id),
        )
        db.commit()
        flash("Case updated.", "success")
        return redirect(url_for("case_detail", case_id=case_id))

    @app.route("/api/cases/<int:case_id>/seed", methods=["POST"])
    def api_case_seed(case_id: int):
        """
        Warm Start seed endpoint — manually trigger or override context_anchors.

        Body JSON (all fields optional):
          { "seed_text": "...", "anchors": [{"lat": 14.0, "lng": 108.3, "label": "vietnam"}] }

        If `anchors` is provided it is stored as-is (explicit override).
        If only `seed_text` is provided, the GAZETTEER is scanned and results stored.
        Both fields may be sent together; explicit `anchors` takes precedence.
        Returns JSON: { ok: true, anchors: [...], count: N }
        """
        from flask import jsonify
        import json as _json
        from core.gravity import extract_location_anchors

        body = request.get_json(silent=True) or {}
        db = get_db()

        case = db.execute("SELECT case_id FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if not case:
            return jsonify({"ok": False, "error": "Case not found"}), 404

        explicit = body.get("anchors")
        if explicit is not None:
            anchors = explicit
        else:
            seed_text = body.get("seed_text", "")
            anchors = extract_location_anchors(seed_text)

        db.execute(
            "UPDATE cases SET context_anchors = ? WHERE case_id = ?",
            (_json.dumps(anchors) if anchors else None, case_id),
        )
        db.commit()
        return jsonify({"ok": True, "anchors": anchors, "count": len(anchors)})

    @app.route("/cases/<int:case_id>/delete", methods=["POST"])
    def case_delete(case_id: int):
        db = get_db()
        case = db.execute(
            "SELECT name FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if case:
            db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
            db.commit()
            flash(f"Case '{case['name']}' deleted.", "success")
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
            "SELECT case_id, name, description, status, created_at, "
            "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
            "FROM cases WHERE case_id=?", (case_id,)
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
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
            f"""SELECT c.case_id, c.name, c.status
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
            "SELECT case_id, name, status FROM cases ORDER BY created_at DESC"
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
            blacklisted = 1 if request.form.get("blacklisted") == "on" else 0
            blacklist_reason = request.form.get("blacklist_reason", "").strip() or None
            image_url = actor["image_url"]

            if request.form.get("remove_image") == "on":
                if image_url:
                    old_path = MEDIA_DIR / image_url
                    if old_path.exists():
                        old_path.unlink()
                image_url = None

            photo = request.files.get("image_file")
            if photo and photo.filename:
                ext = photo.filename.rsplit(".", 1)[-1].lower() if "." in photo.filename else ""
                if ext not in ACTOR_PHOTO_EXTENSIONS:
                    flash("Photo must be PNG, JPG, JPEG, WEBP, or GIF.", "error")
                    return redirect(url_for("actor_edit", actor_id=actor_id))
                if image_url:
                    old_path = MEDIA_DIR / image_url
                    if old_path.exists():
                        old_path.unlink()
                filename = f"{actor_id}.{ext}"
                (MEDIA_DIR / "actors").mkdir(parents=True, exist_ok=True)
                photo.save(str(MEDIA_DIR / "actors" / filename))
                image_url = f"actors/{filename}"

            if not name:
                flash("Name is required.", "error")
                return redirect(url_for("actor_edit", actor_id=actor_id))

            db.execute(
                """
                UPDATE actors
                SET name=?, type=?, description=?, image_url=?,
                    blacklisted=?, blacklist_reason=?,
                    blacklist_added_at = CASE
                        WHEN ? = 1 AND blacklisted = 0 AND blacklist_added_at IS NULL
                            THEN datetime('now')
                        WHEN ? = 0 THEN NULL
                        ELSE blacklist_added_at
                    END
                WHERE actor_id=?
                """,
                (
                    name, atype, description, image_url,
                    blacklisted, blacklist_reason,
                    blacklisted,
                    blacklisted,
                    actor_id,
                ),
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
            generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
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
            generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            back_url=url_for("event_detail", event_id=event_id),
            **ctx,
        )

    # -----------------------------------------------------------------------
    # Route: /document — Arkadia-style rich document briefs (case-independent)
    # -----------------------------------------------------------------------

    # Palette rotated across chart slices / entity cards
    _DOC_PALETTE = [
        ("rgba(6,182,212,0.75)",   "#06b6d4"),   # cyan
        ("rgba(217,70,239,0.75)",  "#d946ef"),   # fuchsia
        ("rgba(139,92,246,0.75)",  "#8b5cf6"),   # violet
        ("rgba(245,158,11,0.75)",  "#f59e0b"),   # amber
        ("rgba(16,185,129,0.75)",  "#10b981"),   # emerald
        ("rgba(244,63,94,0.75)",   "#f43f5e"),   # rose
        ("rgba(59,130,246,0.75)",  "#3b82f6"),   # blue
        ("rgba(249,115,22,0.75)",  "#f97316"),   # orange
    ]

    def _doc_rgba(i: int) -> str:
        return _DOC_PALETTE[i % len(_DOC_PALETTE)][0]

    def _doc_hex(i: int) -> str:
        return _DOC_PALETTE[i % len(_DOC_PALETTE)][1]

    def _build_actor_document(actor, ctx: dict) -> dict:
        """Convert _dossier_actor_data output into document_brief template context."""
        import collections, datetime as _dt

        events           = ctx.get("events", [])
        artifact_footprint = ctx.get("artifact_footprint", [])
        co_actors        = ctx.get("co_actors", [])
        role_timeline    = ctx.get("role_timeline", [])

        # ── Stats ────────────────────────────────────────────────────────────
        role_counts: dict = collections.Counter(r["role"] or "unspecified" for r in role_timeline)
        art_type_counts: dict = collections.Counter(
            (a["type"] or "unknown") for a in artifact_footprint
        )
        stats = [
            {"value": len(events),            "label": "Events Linked",    "sublabel": "Verified appearances",  "color": "#06b6d4"},
            {"value": len(artifact_footprint), "label": "Evidence Items",   "sublabel": "Artifact footprint",    "color": "#d946ef"},
            {"value": len(co_actors),          "label": "Co-Actors",        "sublabel": "Network connections",   "color": "#8b5cf6"},
            {"value": len(role_counts),        "label": "Distinct Roles",   "sublabel": "Operational profile",   "color": "#f59e0b"},
        ]

        # ── Primary section ──────────────────────────────────────────────────
        top_roles = sorted(role_counts.items(), key=lambda x: -x[1])[:8]
        role_tags = [
            {"label": role.title(), "value": f"{cnt} event{'s' if cnt != 1 else ''}", "color": _doc_hex(i)}
            for i, (role, cnt) in enumerate(top_roles)
        ]
        primary_section = {
            "title": "Activity & Role Profile",
            "body": (
                f"This actor has been identified across {len(events)} event{'s' if len(events) != 1 else ''} "
                f"in the intelligence corpus, accumulating {len(artifact_footprint)} linked evidence items. "
                f"Role distribution across those events reveals {len(role_counts)} distinct operational "
                f"context{'s' if len(role_counts) != 1 else ''}, providing a functional profile of how "
                f"this entity operates within documented incidents."
            ),
            "tags": role_tags,
        }

        # ── Primary chart: role distribution (polar area) ────────────────────
        if role_counts:
            labels_r, data_r = zip(*sorted(role_counts.items(), key=lambda x: -x[1]))
        else:
            labels_r, data_r = (["No roles recorded"],), ([1],)
        primary_chart = {
            "labels":        list(labels_r),
            "data":          list(data_r),
            "colors":        [_doc_rgba(i) for i in range(len(labels_r))],
            "dataset_label": "Events per Role",
        }

        # ── Flow section ─────────────────────────────────────────────────────
        flow_section = {
            "title": "Intelligence Pipeline",
            "body": (
                "Raw signals ingested by FORGE collectors are scored, enriched via NER extraction, "
                "and materialised into the actor registry. Event linkages and artifact evidence form "
                "the dossier substrate below."
            ),
            "nodes": [
                {"label": "Raw Signals",    "sublabel": "Collector ingest",   "color_start": "#334155", "color_end": "#475569"},
                {"label": "Actor Identified","sublabel": "Confidence ≥ 0.2",  "color_start": "#0e7490", "color_end": "#1d4ed8"},
                {"label": "Event Network",  "sublabel": "Participation graph","color_start": "#c2410c", "color_end": "#d97706"},
                {"label": "Evidence Trail", "sublabel": "Artifact footprint", "color_start": "#a21caf", "color_end": "#7c3aed"},
                {"label": "Association Map","sublabel": "Co-actor linkage",   "color_start": "#065f46", "color_end": "#0f766e"},
            ],
            "cards": [
                {
                    "color": "#06b6d4",
                    "title": "Event Involvement (Gravity-Ranked)",
                    "body": (
                        f"{len(events)} events linked via actor_events table. Roles include: "
                        f"{', '.join(list(role_counts.keys())[:5]) or 'none recorded'}. "
                        "Each event carries an independent gravity score from the ingest pipeline."
                    ),
                },
                {
                    "color": "#d946ef",
                    "title": "Evidence Footprint",
                    "body": (
                        f"{len(artifact_footprint)} evidence items across "
                        f"{len(art_type_counts)} type{'s' if len(art_type_counts) != 1 else ''}: "
                        f"{', '.join(list(art_type_counts.keys())[:5]) or 'none'}. "
                        "Artifacts are attached via event linkage, not direct actor assignment."
                    ),
                },
            ],
        }

        # ── Entity grid: top events ───────────────────────────────────────────
        card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
        event_cards = []
        for i, ev in enumerate(events[:6]):
            summary = (ev["summary"] or "")[:160]
            if len(ev["summary"] or "") > 160:
                summary += "…"
            event_cards.append({
                "color":   card_colors[i % len(card_colors)],
                "eyebrow": f"{ev['category'] or 'EVENT'} · {ev['date'] or '—'}",
                "title":   ev["title"] or "Untitled Event",
                "body":    summary or "No summary recorded.",
                "meta":    f"ID #{ev['event_id']} · {ev['artifact_count']} artifact{'s' if ev['artifact_count'] != 1 else ''} · Role: {ev['role'] or 'unspecified'}",
                "wide":    False,
            })
        if not event_cards:
            event_cards.append({
                "color": "#475569", "eyebrow": "NO DATA",
                "title": "No Events Linked",
                "body":  "This actor has not yet been associated with any events in the corpus.",
                "meta":  "", "wide": False,
            })

        entity_section = {
            "title":    "Event Timeline",
            "subtitle": "Documented events in which this actor has been identified, ordered chronologically.",
            "cards":    event_cards,
        }

        # ── Secondary chart: artifact type distribution (doughnut) ────────────
        if art_type_counts:
            labels_a, data_a = zip(*sorted(art_type_counts.items(), key=lambda x: -x[1]))
            secondary_chart: dict | None = {
                "labels":        list(labels_a),
                "data":          list(data_a),
                "colors":        [_doc_rgba(i) for i in range(len(labels_a))],
                "dataset_label": "Artifacts by Type",
            }
        else:
            secondary_chart = None

        top_source_counts: dict = collections.Counter(
            a["source"] for a in artifact_footprint if a["source"]
        )
        top_src = top_source_counts.most_common(1)
        callout_body = (
            f"Primary evidence source: {top_src[0][0]} ({top_src[0][1]} items)."
            if top_src else "No artifact sources recorded."
        )

        secondary_section: dict | None = {
            "title": "Evidence Composition",
            "body": (
                f"The artifact footprint spans {len(art_type_counts)} evidence type{'s' if len(art_type_counts) != 1 else ''}. "
                "Distribution across categories reveals collection patterns and potential intelligence gaps."
            ),
            "callout": {
                "title": "Dominant Source",
                "body":  callout_body,
            },
        } if artifact_footprint else None

        return dict(
            subject_type="actor",
            doc_title=actor["name"],
            doc_subtitle=actor["description"] or None,
            back_url=url_for("actor_detail", actor_id=actor["actor_id"]),
            stats=stats,
            primary_section=primary_section,
            primary_chart=primary_chart,
            flow_section=flow_section,
            entity_section=entity_section,
            secondary_section=secondary_section,
            secondary_chart=secondary_chart,
        )

    def _build_event_document(event, ctx: dict) -> dict:
        """Convert _dossier_event_data output into document_brief template context."""
        import collections

        artifacts       = ctx.get("artifacts", [])
        actors          = ctx.get("actors", [])
        source_breakdown= ctx.get("source_breakdown", [])
        related_by_actor= ctx.get("related_by_actor", [])

        actor_type_counts: dict = collections.Counter(
            (a["type"] or "unknown") for a in actors
        )

        # ── Stats ────────────────────────────────────────────────────────────
        stats = [
            {"value": len(artifacts),        "label": "Evidence Items",  "sublabel": "Artifact records",     "color": "#06b6d4"},
            {"value": len(actors),            "label": "Actors Linked",   "sublabel": "Identified parties",   "color": "#d946ef"},
            {"value": len(source_breakdown),  "label": "Source Feeds",    "sublabel": "Distinct origins",     "color": "#f59e0b"},
            {"value": len(related_by_actor),  "label": "Related Events",  "sublabel": "Via shared actors",    "color": "#10b981"},
        ]

        # ── Primary section ──────────────────────────────────────────────────
        actor_tags = [
            {"label": a["name"], "value": f"{a['type'] or 'unknown'} · {a['role'] or 'no role'}", "color": _doc_hex(i)}
            for i, a in enumerate(actors[:8])
        ]
        primary_section = {
            "title": "Actor Involvement",
            "body": (
                f"{len(actors)} actor{'s' if len(actors) != 1 else ''} identified in connection with this event, "
                f"spanning {len(actor_type_counts)} entity type{'s' if len(actor_type_counts) != 1 else ''}. "
                f"The event is documented by {len(artifacts)} evidence item{'s' if len(artifacts) != 1 else ''} "
                f"drawn from {len(source_breakdown)} source{'s' if len(source_breakdown) != 1 else ''}."
            ),
            "tags": actor_tags,
        }

        # ── Primary chart: source distribution (polar area) ──────────────────
        if source_breakdown:
            src_labels = [row["source"] for row in source_breakdown[:8]]
            src_data   = [row["cnt"]    for row in source_breakdown[:8]]
        else:
            src_labels, src_data = ["No sources"], [1]
        primary_chart = {
            "labels":        src_labels,
            "data":          src_data,
            "colors":        [_doc_rgba(i) for i in range(len(src_labels))],
            "dataset_label": "Artifacts per Source",
        }

        # ── Flow section ─────────────────────────────────────────────────────
        flow_section = {
            "title": "Evidence Chain",
            "body": (
                "Open-source collectors feed raw signals that are grouped into this event record "
                "by the FORGE event constructor. Actors are materialised via NER; artifacts are "
                "attached directly. Related events surface through shared actor traversal."
            ),
            "nodes": [
                {"label": "Intel Sources",  "sublabel": "OSINT collectors",  "color_start": "#334155", "color_end": "#475569"},
                {"label": "Event Record",   "sublabel": "Canonical entry",   "color_start": "#0e7490", "color_end": "#1d4ed8"},
                {"label": "Actor Web",      "sublabel": f"{len(actors)} parties",  "color_start": "#c2410c", "color_end": "#d97706"},
                {"label": "Artifacts",      "sublabel": f"{len(artifacts)} items", "color_start": "#a21caf", "color_end": "#7c3aed"},
                {"label": "Cross-Reference","sublabel": f"{len(related_by_actor)} related", "color_start": "#065f46", "color_end": "#0f766e"},
            ],
            "cards": [
                {
                    "color": "#f59e0b",
                    "title": "Source Distribution",
                    "body": (
                        f"Top source{'s' if len(source_breakdown) > 1 else ''}: "
                        + ", ".join(f"{r['source']} ({r['cnt']})" for r in source_breakdown[:4])
                        + "." if source_breakdown else "No source data recorded."
                    ),
                },
                {
                    "color": "#8b5cf6",
                    "title": "Actor Type Breakdown",
                    "body": (
                        "; ".join(f"{t.title()}: {c}" for t, c in actor_type_counts.most_common(5))
                        or "No actors recorded."
                    ),
                },
            ],
        }

        # ── Entity grid: artifacts ────────────────────────────────────────────
        card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
        artifact_cards = []
        for i, art in enumerate(artifacts[:6]):
            body = (art["description"] or "")[:160]
            if len(art["description"] or "") > 160:
                body += "…"
            artifact_cards.append({
                "color":   card_colors[i % len(card_colors)],
                "eyebrow": f"{art['type'] or 'ARTIFACT'} · {art['source'] or '—'}",
                "title":   art["title"] or "Untitled Artifact",
                "body":    body or "No description recorded.",
                "meta":    f"ID #{art['artifact_id']} · {art['date'] or 'No date'}",
                "wide":    False,
            })
        if not artifact_cards:
            artifact_cards.append({
                "color": "#475569", "eyebrow": "NO DATA",
                "title": "No Artifacts Recorded",
                "body":  "No evidence items have been attached to this event.",
                "meta":  "", "wide": False,
            })

        entity_section = {
            "title":    "Evidence Inventory",
            "subtitle": "Artifacts and source documents linked directly to this event record.",
            "cards":    artifact_cards,
        }

        # ── Secondary chart: actor type distribution (doughnut) ───────────────
        secondary_chart: dict | None = None
        if actor_type_counts:
            labels_at, data_at = zip(*sorted(actor_type_counts.items(), key=lambda x: -x[1]))
            secondary_chart = {
                "labels":        list(labels_at),
                "data":          list(data_at),
                "colors":        [_doc_rgba(i) for i in range(len(labels_at))],
                "dataset_label": "Actors by Type",
            }

        secondary_section: dict | None = None
        if actors:
            dominant = actor_type_counts.most_common(1)
            secondary_section = {
                "title": "Actor Type Analysis",
                "body": (
                    f"The {len(actors)} actor{'s' if len(actors) != 1 else ''} linked to this event span "
                    f"{len(actor_type_counts)} entity classification{'s' if len(actor_type_counts) != 1 else ''}. "
                    "The doughnut chart reveals the organisational composition of documented participation."
                ),
                "callout": {
                    "title": "Dominant Actor Class",
                    "body": (
                        f"{dominant[0][0].title()} entities account for the largest share "
                        f"({dominant[0][1]} of {len(actors)} actors). "
                        "Cross-reference with related events to assess whether this pattern holds network-wide."
                    ) if dominant else "Actor types could not be determined.",
                },
            }

        return dict(
            subject_type="event",
            doc_title=event["title"],
            doc_subtitle=event["summary"] or None,
            back_url=url_for("event_detail", event_id=event["event_id"]),
            stats=stats,
            primary_section=primary_section,
            primary_chart=primary_chart,
            flow_section=flow_section,
            entity_section=entity_section,
            secondary_section=secondary_section,
            secondary_chart=secondary_chart,
        )

    def _document_signals_data(db, stream: str | None, days: int) -> dict:
        """Query aggregate signal data for a stream brief (no case required)."""
        period_clause = f"datetime('now', '-{days} days')"

        base_where = f"created_at >= {period_clause}"
        if stream:
            base_where += f" AND stream = '{stream}'"

        total = db.execute(
            f"SELECT COUNT(*) AS n FROM signals WHERE {base_where}"
        ).fetchone()["n"]

        high_gravity = db.execute(
            f"SELECT COUNT(*) AS n FROM signals WHERE {base_where} AND gravity_score >= 0.5"
        ).fetchone()["n"]

        stream_dist = db.execute(
            f"SELECT stream, COUNT(*) AS cnt FROM signals WHERE {base_where} "
            f"GROUP BY stream ORDER BY cnt DESC"
        ).fetchall()

        source_dist = db.execute(
            f"SELECT source, COUNT(*) AS cnt FROM signals WHERE {base_where} AND source IS NOT NULL "
            f"GROUP BY source ORDER BY cnt DESC LIMIT 8"
        ).fetchall()

        unique_sources = db.execute(
            f"SELECT COUNT(DISTINCT source) AS n FROM signals WHERE {base_where}"
        ).fetchone()["n"]

        gravity_tiers = db.execute(
            f"""SELECT
                SUM(CASE WHEN gravity_score >= 0.7 THEN 1 ELSE 0 END) AS high,
                SUM(CASE WHEN gravity_score >= 0.35 AND gravity_score < 0.7 THEN 1 ELSE 0 END) AS med,
                SUM(CASE WHEN gravity_score < 0.35 THEN 1 ELSE 0 END) AS low
            FROM signals WHERE {base_where}"""
        ).fetchone()

        top_signals = db.execute(
            f"SELECT signal_id, title, source, stream, gravity_score, created_at, content "
            f"FROM signals WHERE {base_where} "
            f"ORDER BY gravity_score DESC LIMIT 6"
        ).fetchall()

        return {
            "total":         total,
            "high_gravity":  high_gravity,
            "unique_sources":unique_sources,
            "days":          days,
            "stream":        stream,
            "stream_dist":   stream_dist,
            "source_dist":   source_dist,
            "gravity_tiers": gravity_tiers,
            "top_signals":   top_signals,
        }

    def _build_signals_document(data: dict) -> dict:
        """Convert _document_signals_data output into document_brief template context."""
        stream  = data["stream"]
        total   = data["total"]
        days    = data["days"]

        doc_title = f"{stream} Stream Brief" if stream else "Signal Intelligence Brief"

        stats = [
            {"value": total,                 "label": "Total Signals",    "sublabel": f"Last {days} days",     "color": "#06b6d4"},
            {"value": data["high_gravity"],  "label": "High Gravity",     "sublabel": "Score ≥ 0.50",          "color": "#f43f5e"},
            {"value": data["unique_sources"],"label": "Unique Sources",   "sublabel": "Distinct origins",      "color": "#8b5cf6"},
            {"value": days,                  "label": "Day Window",       "sublabel": "Collection period",     "color": "#f59e0b"},
        ]

        # Stream distribution body
        stream_lines = ", ".join(
            f"{r['stream'] or 'unclassified'} ({r['cnt']})"
            for r in data["stream_dist"]
        ) or "No stream data."

        primary_section = {
            "title": "Stream Distribution",
            "body": (
                f"{total} signal{'s' if total != 1 else ''} ingested over the past {days} days "
                f"across {len(data['stream_dist'])} stream{'s' if len(data['stream_dist']) != 1 else ''}. "
                f"Breakdown: {stream_lines}. "
                f"{data['high_gravity']} signal{'s' if data['high_gravity'] != 1 else ''} "
                f"scored above the 0.50 gravity threshold, flagging elevated operational significance."
            ),
            "tags": [
                {"label": r["stream"] or "unclassified", "value": f"{r['cnt']} signals", "color": _doc_hex(i)}
                for i, r in enumerate(data["stream_dist"][:8])
            ],
        }

        # Primary chart: stream distribution (polar area)
        if data["stream_dist"]:
            sd_labels = [r["stream"] or "unclassified" for r in data["stream_dist"]]
            sd_data   = [r["cnt"] for r in data["stream_dist"]]
        else:
            sd_labels, sd_data = ["No data"], [1]
        primary_chart = {
            "labels":        sd_labels,
            "data":          sd_data,
            "colors":        [_doc_rgba(i) for i in range(len(sd_labels))],
            "dataset_label": "Signals per Stream",
        }

        # Flow section
        flow_section = {
            "title": "FORGE Ingest Pipeline",
            "body": (
                "Signals originate from open-source collectors and are scored via the gravity engine "
                "(urgency/importance model). NER extraction materialises entities; stream classification "
                "routes signals to the appropriate decay schedule."
            ),
            "nodes": [
                {"label": "Collectors",    "sublabel": "OSINT / FLUX",     "color_start": "#334155", "color_end": "#475569"},
                {"label": "Signal Ingest", "sublabel": "Dedup + normalise","color_start": "#0e7490", "color_end": "#1d4ed8"},
                {"label": "Gravity Score", "sublabel": "0.0 – 1.0",        "color_start": "#c2410c", "color_end": "#d97706"},
                {"label": "Entity Extract","sublabel": "NER / spaCy",      "color_start": "#a21caf", "color_end": "#7c3aed"},
                {"label": "Stream Route",  "sublabel": "Decay scheduling", "color_start": "#065f46", "color_end": "#0f766e"},
            ],
            "cards": [
                {
                    "color": "#06b6d4",
                    "title": "Top Sources",
                    "body": (
                        ", ".join(f"{r['source']} ({r['cnt']})" for r in data["source_dist"][:5])
                        or "No source data available."
                    ),
                },
                {
                    "color": "#f59e0b",
                    "title": "Gravity Tier Breakdown",
                    "body": (
                        f"High (≥0.70): {data['gravity_tiers']['high'] or 0} · "
                        f"Medium (0.35–0.70): {data['gravity_tiers']['med'] or 0} · "
                        f"Low (<0.35): {data['gravity_tiers']['low'] or 0}"
                    ),
                },
            ],
        }

        # Entity grid: top signals by gravity
        card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
        sig_cards = []
        for i, sig in enumerate(data["top_signals"]):
            body = (sig["content"] or sig["title"] or "")[:160]
            if len(sig["content"] or sig["title"] or "") > 160:
                body += "…"
            grav = sig["gravity_score"]
            grav_str = f"{grav:.2f}" if grav is not None else "—"
            sig_cards.append({
                "color":   card_colors[i % len(card_colors)],
                "eyebrow": f"{sig['stream'] or 'UNCLASSIFIED'} · Gravity {grav_str}",
                "title":   sig["title"] or f"Signal #{sig['signal_id']}",
                "body":    body or "No content available.",
                "meta":    f"ID #{sig['signal_id']} · Source: {sig['source'] or '—'} · {sig['created_at'] or '—'}",
                "wide":    False,
            })
        if not sig_cards:
            sig_cards.append({
                "color": "#475569", "eyebrow": "NO DATA",
                "title": "No Signals in Window",
                "body":  f"No signals recorded in the past {days} days for the selected filter.",
                "meta":  "", "wide": False,
            })

        entity_section = {
            "title":    "Top Signals by Gravity",
            "subtitle": f"Highest-scoring signals ingested in the last {days} days, ranked by gravity score.",
            "cards":    sig_cards,
        }

        # Secondary chart: gravity tier doughnut
        tiers = data["gravity_tiers"]
        secondary_chart: dict | None = {
            "labels": ["High ≥ 0.70", "Medium 0.35–0.70", "Low < 0.35"],
            "data":   [tiers["high"] or 0, tiers["med"] or 0, tiers["low"] or 0],
            "colors": ["rgba(244,63,94,0.8)", "rgba(245,158,11,0.8)", "rgba(148,163,184,0.6)"],
            "dataset_label": "Signals by Gravity Tier",
        } if total > 0 else None

        secondary_section: dict | None = {
            "title": "Gravity Tier Analysis",
            "body": (
                "Gravity score distribution reflects the operational weight of ingested signals. "
                "High-tier signals trigger ESCALATE flags; medium-tier triggers MONITOR. "
                "Low-tier signals enter passive decay."
            ),
            "callout": {
                "title": "Escalation Threshold",
                "body": (
                    f"{data['high_gravity']} of {total} signals ({int(data['high_gravity']/total*100) if total else 0}%) "
                    f"scored ≥ 0.50 (ESCALATE boundary). "
                    "High concentrations in this tier indicate elevated threat tempo in the selected window."
                ),
            },
        } if total > 0 else None

        return dict(
            subject_type="signals",
            doc_title=doc_title,
            doc_subtitle=f"Aggregate signal intelligence across the last {days} days" + (f" · Stream: {stream}" if stream else ""),
            back_url=url_for("signals"),
            stats=stats,
            primary_section=primary_section,
            primary_chart=primary_chart,
            flow_section=flow_section,
            entity_section=entity_section,
            secondary_section=secondary_section,
            secondary_chart=secondary_chart,
        )

    @app.route("/document/actor/<int:actor_id>")
    def document_actor(actor_id: int):
        db = get_db()
        actor, ctx = _dossier_actor_data(db, actor_id)
        if not actor:
            return render_template("archive.html", page="404"), 404
        import datetime
        context = _build_actor_document(actor, ctx)
        context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return render_template("document_brief.html", **context)

    @app.route("/document/event/<int:event_id>")
    def document_event(event_id: int):
        db = get_db()
        event, ctx = _dossier_event_data(db, event_id)
        if not event:
            return render_template("archive.html", page="404"), 404
        import datetime
        context = _build_event_document(event, ctx)
        context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return render_template("document_brief.html", **context)

    @app.route("/document/signals")
    def document_signals():
        import datetime
        db     = get_db()
        stream = request.args.get("stream")
        try:
            days = max(1, min(int(request.args.get("days", 7)), 90))
        except (TypeError, ValueError):
            days = 7
        data    = _document_signals_data(db, stream or None, days)
        context = _build_signals_document(data)
        context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return render_template("document_brief.html", **context)

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


    # -----------------------------------------------------------------------
    # Phase 32: Diagnostics — Control Room
    # -----------------------------------------------------------------------

    @app.route("/diagnostics")
    def diagnostics():
        """Phase 32: System Control Room page."""
        return render_template("diagnostics.html")

    @app.route("/api/diagnostics")
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
        from flask import jsonify
        import os as _os
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
                db_size = _os.path.getsize(str(DB_PATH)) / (1024 * 1024)
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
    # Phase 33: Evolution Layer — Discovery
    # -----------------------------------------------------------------------

    @app.route("/api/fms/status")
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

    @app.route("/api/fms/attach/<module_name>", methods=["POST"])
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

    @app.route("/api/fms/detach/<module_name>", methods=["POST"])
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

    @app.route("/discovery")
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

    @app.route("/api/evolution/run", methods=["POST"])
    def api_evolution_run():
        """Phase 33: Trigger a full evolution engine scan."""
        from flask import jsonify
        try:
            from forage.engines.evolution_engine import EvolutionEngine
            top_n      = int(request.args.get("top",   25))
            pair_limit = int(request.args.get("pairs", 500))
            dry_run    = request.args.get("dry_run", "false").lower() == "true"
            result     = EvolutionEngine(db_path=DB_PATH).run(
                top_n=top_n, pair_limit=pair_limit, dry_run=dry_run
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/discovery/<int:target_id>/approve", methods=["POST"])
    def api_discovery_approve(target_id: int):
        """Phase 33: Approve a discovery candidate."""
        from flask import jsonify
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

    @app.route("/api/discovery/<int:target_id>/ignore", methods=["POST"])
    def api_discovery_ignore(target_id: int):
        """Phase 33: Ignore a discovery candidate."""
        from flask import jsonify
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

    @app.route("/evolution")
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

    # -----------------------------------------------------------------------
    # Phase 30E: Case Correlation Intelligence Routes
    # -----------------------------------------------------------------------

    @app.route("/api/cases/<int:case_id>/correlations")
    def api_case_correlations(case_id: int):
        """
        Returns correlated_incidents pairs where BOTH signals are pinned
        to this case via case_signals. Surfaces VBS/Makhado 1.000 score
        and all other in-case patterns automatically.
        """
        from flask import jsonify
        db = get_db()
        case = db.execute(
            "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        try:
            rows = db.execute("""
                SELECT ci.id,
                       ci.correlation_score,
                       ci.distance_km,
                       ci.time_difference_hours,
                       ci.space_score,
                       ci.time_score,
                       ci.detected_at,
                       sa.signal_id AS signal_id_a,
                       sa.title     AS title_a,
                       sa.source    AS src_a,
                       sa.stream    AS stream_a,
                       sa.lat       AS lat_a,
                       sa.lng       AS lng_a,
                       sb.signal_id AS signal_id_b,
                       sb.title     AS title_b,
                       sb.source    AS src_b,
                       sb.stream    AS stream_b,
                       sb.lat       AS lat_b,
                       sb.lng       AS lng_b
                FROM   correlated_incidents ci
                JOIN   signals sa ON sa.signal_id = ci.signal_a
                JOIN   signals sb ON sb.signal_id = ci.signal_b
                WHERE  EXISTS (
                           SELECT 1 FROM case_signals cs
                           WHERE  cs.case_id   = :cid
                             AND  cs.signal_id = ci.signal_a
                       )
                  AND  EXISTS (
                           SELECT 1 FROM case_signals cs
                           WHERE  cs.case_id   = :cid
                             AND  cs.signal_id = ci.signal_b
                       )
                ORDER  BY ci.correlation_score DESC
                LIMIT  200
            """, {"cid": case_id}).fetchall()
        except Exception as exc:
            return jsonify({"error": str(exc), "pairs": []}), 500
        return jsonify({
            "case_id": case_id,
            "pairs":   [dict(r) for r in rows],
            "total":   len(rows),
        })

    @app.route("/api/cases/<int:case_id>/pin-signal", methods=["POST"])
    def api_case_pin_signal_inline(case_id: int):
        """
        Pins a signal by UUID to this case. Used by case_detail inline form.
        Body JSON: { signal_id: str, note?: str }
        Distinct from api_case_pin_signal (URL-param route at /api/cases/<id>/pin/<signal_id>).
        """
        from flask import jsonify, request as req
        db   = get_db()
        data = req.get_json(silent=True) or {}
        sig_id = (data.get("signal_id") or "").strip()
        note   = (data.get("note")      or "").strip() or None
        if not sig_id:
            return jsonify({"error": "signal_id required"}), 400
        case = db.execute(
            "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        sig = db.execute(
            "SELECT signal_id FROM signals WHERE signal_id=?", (sig_id,)
        ).fetchone()
        if not sig:
            return jsonify({"error": "Signal not found"}), 404
        try:
            db.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                (case_id, sig_id, note)
            )
            db.commit()
            return jsonify({"ok": True, "status": "pinned",
                            "case_id": case_id, "signal_id": sig_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/cases/<int:case_id>/signals/<signal_id>", methods=["DELETE"])
    def api_case_unpin_signal(case_id: int, signal_id: str):
        """Unpins a signal from a case. Used by case_detail unpin button."""
        from flask import jsonify
        db = get_db()
        try:
            db.execute(
                "DELETE FROM case_signals WHERE case_id=? AND signal_id=?",
                (case_id, signal_id)
            )
            db.commit()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/correlations/promote-case", methods=["POST"])
    def api_correlation_promote_case():
        """
        Creates a new Case seeded with both signals from a correlated pair.
        Body JSON: { correlation_id: int, title?: str }
        Used by the feed.html CORRELATION card "Open as Case" button.
        """
        from flask import jsonify, request as req
        db   = get_db()
        data = req.get_json(silent=True) or {}
        corr_id    = data.get("correlation_id")
        case_title = (data.get("title") or "").strip() or None
        if not corr_id:
            return jsonify({"error": "correlation_id required"}), 400
        row = db.execute(
            "SELECT ci.id, ci.signal_a, ci.signal_b, ci.correlation_score, "
            "ci.distance_km, ci.time_difference_hours, "
            "sa.title AS title_a, sa.source AS src_a, "
            "sb.title AS title_b, sb.source AS src_b "
            "FROM correlated_incidents ci "
            "JOIN signals sa ON sa.signal_id = ci.signal_a "
            "JOIN signals sb ON sb.signal_id = ci.signal_b "
            "WHERE ci.id = ?",
            (corr_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Correlation not found"}), 404
        auto_title = case_title or (
            f"Pattern: {(row['title_a'] or '')[:40]} ↔ {(row['title_b'] or '')[:40]}"
        )
        hypothesis = (
            f"Correlation score {row['correlation_score']:.3f} — "
            f"{row['distance_km']:.1f} km apart, "
            f"{row['time_difference_hours']:.2f} h apart. "
            f"Sources: {row['src_a']} ↔ {row['src_b']}."
        )
        description = (
            f"Auto-generated from correlated pair #{corr_id}. "
            f"Signals: [{row['signal_a'][:8]}…] and [{row['signal_b'][:8]}…]. "
            f"Score: {row['correlation_score']:.3f}."
        )
        try:
            cur = db.execute(
                "INSERT INTO cases (name, description, hypothesis, status, case_type) "
                "VALUES (?,?,?,'active','general')",
                (auto_title, description, hypothesis)
            )
            case_id = cur.lastrowid
            for sig_id in (row["signal_a"], row["signal_b"]):
                db.execute(
                    "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                    (case_id, sig_id,
                     f"Auto-pinned from correlation #{corr_id} (score {row['correlation_score']:.3f})")
                )
            db.commit()
            return jsonify({
                "case_id":  case_id,
                "status":   "created",
                "score":    row["correlation_score"],
                "signal_a": row["signal_a"],
                "signal_b": row["signal_b"],
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Stable 1.1 — Collector Autodiscovery: registry + per-collector dispatch
    # -----------------------------------------------------------------------

    @app.route("/api/control/registry", methods=["GET"])
    def api_control_registry():
        """
        Return the full collector registry — healthy collectors and dead nodes.
        The frontend uses this to stamp Control Room buttons and health cards
        dynamically, replacing hard-coded COLLECTOR_ORDER arrays.
        """
        from flask import jsonify
        collectors = list(_COLLECTOR_REGISTRY.values())
        return jsonify({
            "collectors": collectors,
            "dead_nodes": _DEAD_NODES,
            "total":      len(collectors),
            "dead_count": len(_DEAD_NODES),
        })

    # ── Stable 1.2: Soft Scoped Intake — auto-pin membrane ───────────────────
    def _auto_pin_to_case(case_id: int, collector_source: str,
                          job_start_iso: str, pinned_count_ref: list) -> None:
        """
        Post-collection gravity membrane.

        After a scoped collector run completes, fetch every signal this source
        inserted since job_start_iso, score each against the case context via
        CT-1 (build_context + score_item), and INSERT OR IGNORE into
        case_signals for any signal above AUTO_PIN_THRESHOLD.

        Runs inside the reader daemon thread — never blocks Flask.
        pinned_count_ref is a single-element list used as a mutable out-param.
        """
        AUTO_PIN_THRESHOLD = 0.10   # low bar: analyst explicitly asked for this
        try:
            from core.db.connection import get_connection as _gc
            from core.gravity import build_context, score_item
            conn = _gc()
            try:
                ctx = build_context(conn, case_id)
                rows = conn.execute(
                    """
                    SELECT signal_id, title, content, lat, lng
                    FROM   signals
                    WHERE  source   = ?
                      AND  timestamp >= ?
                    """,
                    (collector_source, job_start_iso),
                ).fetchall()

                pinned = 0
                for r in rows:
                    item = {
                        "item_type": "SIGNAL",
                        "signal_id": r["signal_id"],
                        "title":     r["title"] or "",
                        "summary":   (r["content"] or "")[:200],
                        "lat":       r["lat"],
                        "lng":       r["lng"],
                    }
                    gs = score_item(item, ctx)
                    if gs >= AUTO_PIN_THRESHOLD:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO case_signals
                                (case_id, signal_id, note)
                            VALUES (?, ?, ?)
                            """,
                            (case_id, r["signal_id"],
                             f"auto-pinned by collector (gravity {gs:.3f})"),
                        )
                        pinned += 1

                conn.commit()
                pinned_count_ref[0] = pinned
                print(f"[Scoped Intake] case {case_id} ← {pinned}/{len(rows)} "
                      f"signals auto-pinned (src={collector_source})")
            finally:
                conn.close()
        except Exception as exc:
            print(f"[Scoped Intake] auto-pin failed for case {case_id}: {exc}")

    @app.route("/api/discover/<int:actor_id>", methods=["POST"])
    def api_discover(actor_id: int):
        """
        Transform-on-click: run SAFLII collector scoped to a specific actor,
        return discovered signals as JSON. Maltego-inspired interactive discovery.
        """
        db = get_db()
        actor = db.execute(
            "SELECT name, type FROM actors WHERE actor_id = ?", (actor_id,)
        ).fetchone()
        if not actor:
            return jsonify({"error": "Actor not found"}), 404

        try:
            from forage.collectors.saflii_collector import run as saflii_run
            results = saflii_run(
                actor_name=actor["name"], dry_run=False,
                max_actors=1, max_results=5,
            )
        except Exception as exc:
            results = {"error": str(exc), "signals_written": 0}

        new_signals = db.execute("""
            SELECT s.signal_id, s.title, s.gravity_score, s.source, s.lat, s.lng
            FROM signal_actors sa
            JOIN signals s ON s.signal_id = sa.signal_id
            WHERE sa.actor_id = ?
            ORDER BY s.timestamp DESC LIMIT 10
        """, (actor_id,)).fetchall()

        return jsonify({
            "actor_id": actor_id,
            "actor_name": actor["name"],
            "stats": results,
            "signals": [dict(r) for r in new_signals],
        })

    @app.route("/api/control/run_collector/<collector_id>", methods=["POST"])
    def api_control_run_collector(collector_id: str):
        """
        Generic per-collector dispatch. Looks up collector_id in the
        registry — never constructs a path from the URL parameter directly.
        Spawns python <entry> as a subprocess, registers a pipeline_jobs row,
        and starts a generic stdout reader thread.

        Stable 1.2 — Scoped Intake:
          POST body may include {"case_id": N} (optional).
          When present, the reader thread runs _auto_pin_to_case() after
          completion — the CT-1 gravity membrane auto-populates case_signals.
          Collectors remain fully case-agnostic (no argv changes).
        """
        from flask import jsonify
        import threading
        import datetime as _dt

        manifest = _COLLECTOR_REGISTRY.get(collector_id)
        if manifest is None:
            if any(d["id"] == collector_id for d in _DEAD_NODES):
                return jsonify({
                    "error": f"Collector '{collector_id}' is a Dead Node and cannot be dispatched.",
                    "dead": True,
                }), 422
            return jsonify({"error": f"Unknown collector: {collector_id}"}), 404

        # ── Case context (optional — None = global Control Room dispatch) ──
        body          = request.get_json(silent=True) or {}
        context_case  = body.get("case_id")
        try:
            context_case_id = int(context_case) if context_case is not None else None
        except (TypeError, ValueError):
            context_case_id = None

        # ISO timestamp captured before spawn — used as the query window
        job_start_iso = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M:%S")

        job_key = manifest["job_key"]
        job_id  = _create_job(job_key, f"Queued: {manifest['name']}")

        def _reader(proc, jid):
            """Generic stdout reader — streams output into job message."""
            _pinned = [0]
            try:
                for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line:
                        _update_job(jid, message=line[-300:])
                rc = proc.wait()
                if rc == 0:
                    # ── Soft Scoped Intake membrane ───────────────────────
                    if context_case_id is not None:
                        _update_job(jid, message=(
                            f"{manifest['name']} finished — running "
                            f"Scoped Intake for case {context_case_id}…"
                        ))
                        _auto_pin_to_case(
                            context_case_id,
                            manifest["id"],   # collector source key
                            job_start_iso,
                            _pinned,
                        )
                        _finalize_job(
                            jid, "completed",
                            f"{manifest['name']} done — "
                            f"{_pinned[0]} signals auto-pinned to case "
                            f"{context_case_id}",
                        )
                    else:
                        _finalize_job(jid, "completed",
                                      f"{manifest['name']} finished (exit 0)")
                else:
                    _finalize_job(jid, "failed",
                                  f"{manifest['name']} exited with code {rc}")
            except Exception as exc:
                _finalize_job(jid, "failed", f"Reader thread error: {exc}")

        try:
            if _KILL_FLAGS.pop(job_id, False):
                _finalize_job(job_id, "killed", "Terminated before start")
                return jsonify({"status": "killed", "job_id": job_id}), 200

            cmd = [sys.executable,
                   str(BASE_DIR / manifest["entry"])] + manifest.get("args", [])

            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            proc = subprocess.Popen(cmd, **popen_kwargs)
            _update_job(job_id, status="running", pid=proc.pid,
                        message=f"PID {proc.pid} — {manifest['name']} started"
                                + (f" [case {context_case_id}]"
                                   if context_case_id else ""))

            threading.Thread(target=_reader, args=(proc, job_id),
                             daemon=True).start()

        except Exception as exc:
            _finalize_job(job_id, "failed", f"Failed to spawn: {exc}")
            return jsonify({"status": "error", "error": str(exc),
                            "job_id": job_id}), 500

        return jsonify({
            "status":         "started",
            "job":            job_key,
            "job_id":         job_id,
            "collector_id":   collector_id,
            "name":           manifest["name"],
            "context_case_id": context_case_id,
        })

    # -----------------------------------------------------------------------
    # Phase 34: Control Room — pipeline execution endpoints
    # -----------------------------------------------------------------------

    @app.route("/api/control/run_collectors", methods=["POST"])
    def api_control_run_collectors():
        """
        Spawn all FORAGE collectors concurrently.
        Mirrors mega_ingest.run_all_collectors() — async coroutines driven
        in a background thread via asyncio.run() so Flask is never blocked.
        """
        from flask import jsonify
        import threading, asyncio

        def _run():
            try:
                from tools.mega_ingest import run_all_collectors
                asyncio.run(run_all_collectors())
            except Exception as exc:
                import logging
                logging.getLogger("forge.control").error(
                    f"[control/run_collectors] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_collectors"})


    @app.route("/api/control/run_ingest", methods=["POST"])
    def api_control_run_ingest():
        """
        Run the full Conclave ingest pass over all signals.
        Mirrors mega_ingest.run_full_ingest().
        """
        from flask import jsonify
        import threading

        def _run():
            try:
                from tools.mega_ingest import run_full_ingest
                run_full_ingest(batch_size=50, sleep_interval=0.1)
            except Exception as exc:
                import logging
                logging.getLogger("forge.control").error(
                    f"[control/run_ingest] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_ingest"})


    @app.route("/api/control/run_conclave", methods=["POST"])
    def api_control_run_conclave():
        """
        Run the full engines + processors pass in one shot.
        Mirrors mega_ingest.run_engines_processors():
          artifact_processor -> cluster_engine -> ner_processor -> anomaly_engine
          -> correlation_engine -> decay_engine -> evolution_engine
          -> graph_engine -> sentinel
        """
        from flask import jsonify
        import threading

        def _run():
            try:
                from tools.mega_ingest import run_engines_processors
                run_engines_processors()
            except Exception as exc:
                import logging
                logging.getLogger("forge.control").error(
                    f"[control/run_conclave] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_conclave"})


    @app.route("/api/control/run_graph_engine", methods=["POST"])
    def api_control_run_graph_engine():
        """
        Recompute actor network graph metrics in isolation.
        GraphEngine(db_path=...).run() confirmed: class at line 119,
        .run() at line 353 of forage/engines/graph_engine.py.
        """
        from flask import jsonify
        import threading

        def _run():
            try:
                from forage.engines.graph_engine import GraphEngine
                GraphEngine(db_path=DB_PATH).run()
            except Exception as exc:
                import logging
                logging.getLogger("forge.control").error(
                    f"[control/run_graph_engine] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_graph_engine"})


    @app.route("/api/graph/coalitions")
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
        from flask import jsonify
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


    @app.route("/api/control/run_coalition_detector", methods=["POST"])
    def api_control_run_coalition_detector():
        """
        Run coalition detection in isolation (replaces the Phase 34 stub).
        Accepts optional JSON body: { "threshold": <int> } (default 5).
        Runs in a background thread — returns immediately.
        """
        from flask import jsonify, request as req
        import threading

        body      = req.get_json(silent=True) or {}
        threshold = int(body.get("threshold", 5))

        def _run():
            try:
                from forge_modules.coalition_detector.engine import run
                result = run(threshold=threshold, db_path=DB_PATH)
                import logging as _log
                _log.getLogger("forge.control").info(
                    f"[coalition_detector] {result}"
                )
            except Exception as exc:
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[control/run_coalition_detector] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            "status":    "started",
            "job":       "run_coalition_detector",
            "threshold": threshold,
        })

    # -----------------------------------------------------------------------
    # Phase 38+: Emergence Engine routes
    # -----------------------------------------------------------------------

    @app.route("/api/intel/emergence", methods=["GET"])
    def api_intel_emergence():
        """
        Return all actors currently flagged as emerging.

        Actors are scored across two 24-hour windows:
            current  window : [now - 24h → now]
            baseline window : [now - 48h → now - 24h]

        growth_rate     = current_links / previous_links
                          (if previous == 0 → growth_rate = current_links)
        emergence_score = log(1 + growth_rate) * current_links

        Only actors with current_links >= 3 AND growth_rate >= 2.0
        are returned.

        Response shape:
            {
                "emergence": [
                    {
                        "actor_id":            <int>,
                        "actor_name":          <str>,
                        "growth_rate":         <float>,
                        "current_links":       <int>,
                        "previous_link_count": <int>,
                        "emergence_score":     <float>,
                        "window_start":        <str>,
                        "window_end":          <str>
                    },
                    ...
                ],
                "total": <int>
            }
        """
        from flask import jsonify
        try:
            from forge_modules.emergence_engine.engine import query_emergence
            data = query_emergence(db_path=DB_PATH)
            return jsonify({"emergence": data, "total": len(data)})
        except Exception as exc:
            import logging as _log
            _log.getLogger("forge.control").error(
                f"[api/intel/emergence] {exc}"
            )
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/control/run_emergence", methods=["POST"])
    def api_control_run_emergence():
        """
        Trigger the emergence engine (time-window actor growth analysis).
        Runs in a background thread — returns immediately.

        No request body required.
        """
        from flask import jsonify
        import threading

        def _run():
            try:
                from forge_modules.emergence_engine.engine import run
                result = run(db_path=DB_PATH)
                import logging as _log
                _log.getLogger("forge.control").info(
                    f"[emergence_engine] {result}"
                )
            except Exception as exc:
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[control/run_emergence] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({
            "status": "started",
            "job":    "run_emergence",
        })

    # -----------------------------------------------------------------------
    # Phase 35: Archive Engine routes
    # -----------------------------------------------------------------------

    @app.route("/api/control/archive_case/<int:case_id>", methods=["POST"])
    def api_control_archive_case(case_id: int):
        """
        Archive all intelligence tied to a case:
          1. Copies signals / events / artifacts to *_archive tables
          2. Deletes exclusive rows from live tables
          3. Marks case status = 'archived'

        Runs synchronously (not threaded) so the UI gets a real result
        immediately — archive operations are fast (single transaction).

        Returns full result dict including counts of what was moved.
        """
        from flask import jsonify
        try:
            from forage.engines.archive_engine import ArchiveEngine
            result = ArchiveEngine(db_path=DB_PATH).archive_case(case_id)
            code   = 200 if result["status"] in ("success", "skipped") else 400
            return jsonify(result), code
        except Exception as exc:
            return jsonify({"status": "error", "case_id": case_id,
                            "error": str(exc)}), 500


    @app.route("/api/archive/<int:case_id>")
    def api_archive_query(case_id: int):
        """
        Query the archive for a specific case.
        Returns all archived signals, events and artifacts with their
        original data intact plus archived_at timestamp.
        """
        from flask import jsonify
        try:
            from forage.engines.archive_engine import ArchiveEngine
            result = ArchiveEngine(db_path=DB_PATH).query_archive(case_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/archive")
    def api_archive_index():
        """
        Summary of all archived cases — which cases have been archived
        and how many records each holds.
        """
        from flask import jsonify
        db = get_db()
        try:
            rows = db.execute("""
                SELECT
                    c.case_id,
                    c.title,
                    c.status,
                    c.created_at,
                    COUNT(DISTINCT sa.signal_id)   AS signal_count,
                    COUNT(DISTINCT ea.event_id)    AS event_count,
                    COUNT(DISTINCT aa.artifact_id) AS artifact_count,
                    MAX(sa.archived_at)            AS last_archived_at
                FROM   cases c
                LEFT JOIN signals_archive   sa ON sa.archived_case_id = c.case_id
                LEFT JOIN events_archive    ea ON ea.archived_case_id = c.case_id
                LEFT JOIN artifacts_archive aa ON aa.archived_case_id = c.case_id
                WHERE  c.status = 'archived'
                GROUP  BY c.case_id
                ORDER  BY last_archived_at DESC
            """).fetchall()
            return jsonify({
                "archived_cases": [dict(r) for r in rows],
                "total": len(rows),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # -----------------------------------------------------------------------
    # Phase 37: CounterIntel routes
    # -----------------------------------------------------------------------

    @app.route("/api/counterintel/flags")
    def api_counterintel_flags():
        """
        Return flagged signals.
        Query params:
          ?type=narrative_cluster|bot_pattern|information_campaign
          ?min_confidence=0.0-1.0  (default 0.0)
          ?limit=200               (max 500)
        """
        from flask import jsonify
        try:
            from forge_modules.counterintel.engine import query_flags
            flag_type      = request.args.get("type", "").strip() or None
            min_confidence = float(request.args.get("min_confidence", 0.0))
            limit          = min(int(request.args.get("limit", 200)), 500)
            data = query_flags(
                db_path=DB_PATH,
                flag_type=flag_type,
                min_confidence=min_confidence,
                limit=limit,
            )
            return jsonify({
                "flags":  data,
                "total":  len(data),
                "filter": {"type": flag_type, "min_confidence": min_confidence},
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/counterintel/summary")
    def api_counterintel_summary():
        """Return aggregate flag counts by type."""
        from flask import jsonify
        try:
            from forge_modules.counterintel.engine import query_summary
            return jsonify(query_summary(db_path=DB_PATH))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/control/run_counterintel", methods=["POST"])
    def api_control_run_counterintel():
        """
        Run CounterIntel scan in background thread.
        Returns immediately with { "status": "started" }.
        Results queryable at /api/counterintel/flags after completion.
        """
        from flask import jsonify
        import threading

        def _run():
            try:
                from forge_modules.counterintel.engine import run
                result = run(db_path=DB_PATH)
                import logging as _log
                _log.getLogger("forge.control").info(
                    f"[counterintel] {result}"
                )
            except Exception as exc:
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[control/run_counterintel] {exc}"
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_counterintel"})

    # =======================================================================
    # Phase 72 — Telemetry-Backed Processing Endpoints
    # -----------------------------------------------------------------------
    # Four new pipeline actions wired into pipeline_jobs registry:
    #   • run_artifact_processor   (subprocess + stdout reader)
    #   • run_promote_staged       (in-process thread)
    #   • run_triple_extractor     (in-process thread)
    #   • run_wiki_pipeline        (in-process thread, 3 sub-stages)
    #
    # Plus telemetry endpoints:
    #   • GET  /api/control/jobs/active     — list non-terminal + recent jobs
    #   • POST /api/control/kill_job/<id>   — terminate running job
    # =======================================================================

    # -----------------------------------------------------------------------
    # Artifact Drain — subprocess.Popen with stdout reader thread
    # -----------------------------------------------------------------------

    # Match "Loop batch N: this=X total=Y done=Z skipped=W entities=V rate=R/s ..."
    _ARTIFACT_PROGRESS_RE = re.compile(
        r'Loop\s+batch\s+\d+\s*:\s*this=(\d+)\s+total=(\d+)\s+done=(\d+)\s+skipped=(\d+)'
    )
    # Match per-artifact processing line: "Extracting: somefile.pdf (type=pdf)"
    # or "Progress N/M — title..." — used to tag poisoned artifacts.
    _ARTIFACT_FILE_RE = re.compile(
        r'Extracting:\s+(\S+\.\S+)|Processing\s+single\s+artifact:\s+(\d+)',
        re.IGNORECASE,
    )

    def _stream_artifact_processor(proc: subprocess.Popen, job_id: int,
                                   max_artifacts: int) -> None:
        """
        Wrapper thread: parses stdout, drives pipeline_jobs row.
        Progress is computed as total_processed / max_artifacts (the cap we
        passed on the command line) — gives a reliable 0→1 trajectory even
        though the artifact_processor itself doesn't print a remaining count.

        Captures the most recent artifact filename/id seen so that on
        ERROR/Traceback we can record the poisoned artifact identity for
        UI quarantine.
        """
        done = 0
        last_artifact_seen: str = ""
        try:
            for raw in proc.stdout:                           # type: ignore[union-attr]
                if _KILL_FLAGS.pop(job_id, False):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    _finalize_job(
                        job_id, "killed",
                        "Terminated by operator", records_out=done,
                        progress=(done / max_artifacts) if max_artifacts else 0.0,
                    )
                    return

                line = raw.decode("utf-8", errors="replace").rstrip() \
                       if isinstance(raw, bytes) else str(raw).rstrip()
                if not line:
                    continue

                # Track latest artifact identifier — used to tag Tracebacks
                m_file = _ARTIFACT_FILE_RE.search(line)
                if m_file:
                    last_artifact_seen = m_file.group(1) or m_file.group(2) or ""

                m = _ARTIFACT_PROGRESS_RE.search(line)
                if m:
                    # m.group(2) = cumulative "total" processed count
                    done = int(m.group(2))
                    progress = min(done / max_artifacts, 1.0) if max_artifacts else 0.0
                    _update_job(
                        job_id,
                        status="running",
                        progress=progress,
                        message=line[-300:],   # tail — last 300 chars of log line
                        records_out=done,
                    )
                elif "ERROR" in line or "Traceback" in line:
                    poison = f" [poison: {last_artifact_seen}]" if last_artifact_seen else ""
                    _update_job(
                        job_id,
                        message=f"[ERR]{poison} {line[-260:]}",
                    )
                elif line.startswith("[") and "Loop complete" in line:
                    # Final summary line — capture for completion message
                    _update_job(job_id, message=line[-300:])

            rc = proc.wait()
            if rc == 0:
                _finalize_job(
                    job_id, "completed",
                    f"Drain complete — {done} artifacts processed",
                    records_out=done, progress=1.0,
                )
            else:
                _finalize_job(
                    job_id, "failed",
                    f"Process exited with code {rc}",
                    records_out=done,
                    progress=(done / max_artifacts) if max_artifacts else 0.0,
                )
        except Exception as exc:
            _finalize_job(job_id, "failed",
                          f"Reader thread crash: {exc}", records_out=done)


    @app.route("/api/control/run_artifact_processor", methods=["POST"])
    def api_control_run_artifact_processor():
        """
        Drain the artifact queue. Spawns python -m forage.processors.artifact_processor
        as a subprocess so the Flask process never accumulates spaCy memory.

        Body (JSON, optional):
          { "batch_size": 500, "max_artifacts": 5000, "status": "pending" }
        """
        from flask import jsonify, request as req
        body = req.get_json(silent=True) or {}
        batch_size    = int(body.get("batch_size", 500))
        max_artifacts = int(body.get("max_artifacts", 5000))
        status_filter = str(body.get("status", "pending"))

        # Sanity clamps — protect the OS from absurd inputs
        batch_size    = max(1,  min(batch_size,    5000))
        max_artifacts = max(1,  min(max_artifacts, 200000))

        job_id = _create_job(
            "artifact_processor",
            f"Queued: --status {status_filter} --batch-size {batch_size} "
            f"--max-artifacts {max_artifacts}",
        )

        cmd = [
            sys.executable, "-u", "-m", "forage.processors.artifact_processor",
            "--status", status_filter,
            "--all",
            "--batch-size", str(batch_size),
            "--max-artifacts", str(max_artifacts),
        ]

        try:
            # Windows-friendly subprocess: combine stderr into stdout so the
            # reader thread sees Tracebacks. CREATE_NEW_PROCESS_GROUP allows
            # a clean SIGTERM/CTRL_BREAK_EVENT delivery from the kill endpoint.
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            proc = subprocess.Popen(cmd, **popen_kwargs)
            _update_job(job_id, status="running", pid=proc.pid,
                        message=f"PID {proc.pid} — drain started")

            threading.Thread(
                target=_stream_artifact_processor,
                args=(proc, job_id, max_artifacts),
                daemon=True,
            ).start()
        except Exception as exc:
            _finalize_job(job_id, "failed", f"Failed to spawn: {exc}")
            return jsonify({"status": "error", "error": str(exc),
                            "job_id": job_id}), 500

        return jsonify({
            "status":        "started",
            "job":           "run_artifact_processor",
            "job_id":        job_id,
            "batch_size":    batch_size,
            "max_artifacts": max_artifacts,
        })


    # -----------------------------------------------------------------------
    # Promote Staged Entities — in-process thread
    # -----------------------------------------------------------------------

    @app.route("/api/control/run_promote_staged", methods=["POST"])
    def api_control_run_promote_staged():
        """
        Promote high-fidelity PERSON entities from signal_entities into actors.
        Backed by scripts/promote_staged_entities.run().
        """
        from flask import jsonify
        job_id = _create_job("promote_staged", "Queued: actor promotion gate")

        def _run():
            try:
                if _KILL_FLAGS.pop(job_id, False):
                    _finalize_job(job_id, "killed", "Terminated before start")
                    return
                _update_job(job_id, status="running",
                            stage="scanning",
                            progress=0.1,
                            message="Loading PERSON candidates from A-tier sources")
                from scripts.promote_staged_entities import run as _promote_run
                result = _promote_run(
                    db_path=DB_PATH,
                    min_signals=1,
                    dry_run=False,
                    verbose=False,
                ) or {}
                inserted = int(result.get("inserted", 0))
                examined = int(result.get("examined", inserted))
                _finalize_job(
                    job_id, "completed",
                    f"Promoted {inserted} new actors (examined {examined})",
                    records_out=inserted, progress=1.0,
                )
            except Exception as exc:
                _finalize_job(job_id, "failed", f"{exc}")
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[promote_staged] {exc}", exc_info=True
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_promote_staged",
                        "job_id": job_id})


    # -----------------------------------------------------------------------
    # Triple Extractor — in-process thread
    # -----------------------------------------------------------------------

    @app.route("/api/control/run_triple_extractor", methods=["POST"])
    def api_control_run_triple_extractor():
        """
        Run NLP relationship extraction over signals. Body (optional):
          { "limit": 2000, "pdf_only": false }
        """
        from flask import jsonify, request as req
        body = req.get_json(silent=True) or {}
        limit    = max(1, min(int(body.get("limit", 2000)), 50000))
        pdf_only = bool(body.get("pdf_only", False))

        job_id = _create_job(
            "triple_extractor",
            f"Queued: limit={limit} pdf_only={pdf_only}",
        )

        def _run():
            try:
                if _KILL_FLAGS.pop(job_id, False):
                    _finalize_job(job_id, "killed", "Terminated before start")
                    return
                _update_job(job_id, status="running",
                            stage="extracting",
                            progress=0.1,
                            message=f"Extracting triples — limit={limit}")
                from forage.processors.triple_extractor import _run_extraction
                result = _run_extraction(
                    db_path=DB_PATH,
                    limit=limit,
                    dry_run=False,
                    since=None,
                    pdf_only=pdf_only,
                ) or {}
                triples = int(result.get("triples_inserted",
                                        result.get("triples", 0)))
                examined = int(result.get("signals_processed",
                                          result.get("examined", 0)))
                _finalize_job(
                    job_id, "completed",
                    f"{triples} triples · {examined} signals scanned",
                    records_out=triples, progress=1.0,
                )
            except Exception as exc:
                _finalize_job(job_id, "failed", f"{exc}")
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[triple_extractor] {exc}", exc_info=True
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_triple_extractor",
                        "job_id": job_id})


    # -----------------------------------------------------------------------
    # Wiki Pipeline — in-process, 3 sequential sub-stages
    # -----------------------------------------------------------------------

    @app.route("/api/control/run_wiki_pipeline", methods=["POST"])
    def api_control_run_wiki_pipeline():
        """
        Three-stage wiki synthesis:
          1) schema_init   — wiki schema guard
          2) wiki_compiler — entity-driven dossier synthesis
          3) link_engine   — bidirectional cross-reference graph
        Stage transitions are kill-checkpoints (no mid-stage interruption).
        """
        from flask import jsonify
        job_id = _create_job("wiki_pipeline", "Queued: 3-stage wiki synthesis")

        def _run():
            articles = 0
            try:
                # ── Stage 1: schema_init ─────────────────────────────────
                if _KILL_FLAGS.pop(job_id, False):
                    _finalize_job(job_id, "killed", "Terminated before start")
                    return
                _update_job(job_id, status="running",
                            stage="schema_init [1/3]", progress=0.05,
                            message="Initializing wiki schema")
                from core.db.wiki import init_wiki_db
                init_wiki_db()
                _update_job(job_id, progress=0.10, message="Schema ready")

                # ── Stage 2: wiki_compiler ───────────────────────────────
                if _KILL_FLAGS.pop(job_id, False):
                    _finalize_job(job_id, "killed",
                                  "Terminated between schema_init and compiler")
                    return
                _update_job(job_id,
                            stage="wiki_compiler [2/3]", progress=0.15,
                            message="Synthesizing dossiers from signal_entities")
                from wiki.processors.wiki_compiler import WikiCompiler
                WikiCompiler(DB_PATH).run()

                # Probe article count for records_out telemetry
                try:
                    _conn = sqlite3.connect(str(DB_PATH), timeout=5)
                    articles = int(_conn.execute(
                        "SELECT COUNT(*) FROM wiki_articles"
                    ).fetchone()[0])
                    _conn.close()
                except Exception:
                    pass
                _update_job(job_id, progress=0.85,
                            records_out=articles,
                            message=f"Compiler done — {articles} dossiers")

                # ── Stage 3: link_engine ─────────────────────────────────
                if _KILL_FLAGS.pop(job_id, False):
                    _finalize_job(job_id, "killed",
                                  "Terminated between compiler and link_engine",
                                  records_out=articles, progress=0.85)
                    return
                _update_job(job_id,
                            stage="link_engine [3/3]", progress=0.90,
                            message="Building cross-reference graph")
                from wiki.engines.wiki_link_engine import WikiLinkEngine
                WikiLinkEngine(DB_PATH).run()

                _finalize_job(
                    job_id, "completed",
                    f"Wiki pipeline complete — {articles} dossiers linked",
                    records_out=articles, progress=1.0,
                )
            except Exception as exc:
                _finalize_job(job_id, "failed", f"{exc}",
                              records_out=articles)
                import logging as _log
                _log.getLogger("forge.control").error(
                    f"[wiki_pipeline] {exc}", exc_info=True
                )

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"status": "started", "job": "run_wiki_pipeline",
                        "job_id": job_id})


    # -----------------------------------------------------------------------
    # Telemetry — list active jobs / kill a running job
    # -----------------------------------------------------------------------

    @app.route("/api/control/jobs/active", methods=["GET"])
    def api_control_jobs_active():
        """
        Return all non-terminal jobs plus the most recent terminal job per
        job_key from the last 6 hours. The frontend Poller calls this every
        ~1.5s while jobs are live; the response drives the progress UI.
        """
        from flask import jsonify
        try:
            db = get_db()
            # Non-terminal jobs (full set)
            live = db.execute(
                """
                SELECT job_id, job_key, status, stage, progress, message,
                       pid, records_in, records_out,
                       started_at, updated_at, finished_at
                FROM   pipeline_jobs
                WHERE  status IN ('pending', 'running')
                ORDER  BY job_id DESC
                """
            ).fetchall()
            # Most-recent terminal job per job_key (last 6h) — for "just finished"
            recent = db.execute(
                """
                SELECT job_id, job_key, status, stage, progress, message,
                       pid, records_in, records_out,
                       started_at, updated_at, finished_at
                FROM   pipeline_jobs
                WHERE  status IN ('completed', 'failed', 'killed')
                  AND  finished_at >= datetime('now', '-6 hours')
                  AND  job_id IN (
                        SELECT MAX(job_id) FROM pipeline_jobs
                        WHERE  status IN ('completed', 'failed', 'killed')
                        GROUP  BY job_key
                  )
                ORDER  BY finished_at DESC
                """
            ).fetchall()
            jobs = [dict(r) for r in live] + [dict(r) for r in recent]
            return jsonify({"jobs": jobs, "count": len(jobs)})
        except Exception as exc:
            return jsonify({"error": str(exc), "jobs": []}), 500


    @app.route("/api/control/kill_job/<int:job_id>", methods=["POST"])
    def api_control_kill_job(job_id: int):
        """
        Terminate a running/pending job:
          • Subprocess jobs (artifact_processor): os.kill on stored PID.
          • In-process jobs: set _KILL_FLAGS[job_id]=True; worker checks at
            stage boundaries.
        """
        from flask import jsonify
        try:
            db = get_db()
            row = db.execute(
                "SELECT pid, status, job_key FROM pipeline_jobs "
                "WHERE  job_id = ?",
                (job_id,)
            ).fetchone()
            if row is None:
                return jsonify({"error": "job not found"}), 404
            if row["status"] not in ("running", "pending"):
                return jsonify({
                    "error": f"job not killable (status={row['status']})"
                }), 400

            # Subprocess path — deliver SIGTERM (or CTRL_BREAK_EVENT on Windows)
            pid = row["pid"]
            if pid:
                try:
                    if os.name == "nt":
                        os.kill(pid, _signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    else:
                        os.kill(pid, _signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass  # already dead — still mark killed

            # In-process path — flag for next checkpoint
            _KILL_FLAGS[job_id] = True

            _finalize_job(job_id, "killed", "Terminated by operator")
            return jsonify({"status": "killed", "job_id": job_id,
                            "job_key": row["job_key"]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    # -----------------------------------------------------------------------
    # Phase 39: FMS UI Discovery + Intel Dashboard
    # -----------------------------------------------------------------------

    @app.route("/api/fms/ui")
    def api_fms_ui():
        """
        Return all ACTIVE modules that declare a ui block in their manifest.
        Used by /intel to build the dynamic module panel list.

        Response: list of {module, title, endpoint, type, data_key, panel_group}
        Only modules currently attached to Conclave are included.
        """
        from flask import jsonify
        from core.conclave.context import get_context
        from core.fms.readiness import scan_modules
        import json as _json

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

    @app.route("/intel")
    def intel():
        """Phase 39: Unified FMS Intelligence Dashboard."""
        return render_template("intel.html")

    # ── FORGE Security Manifest — Quarantine Manager routes ──────────────────
    #    forge_security v1.0 | detonator.py routes files to quarantine/
    #    These routes serve the Contagion Ward UI and its JSON API.
    # ─────────────────────────────────────────────────────────────────────────

    @app.route("/quarantine")
    def quarantine_manager():
        """
        Contagion Ward — display all files that failed the PDF Air-Lock.

        Each quarantined file has a matching .meta.json sidecar written by
        forge_security.detonator._quarantine() containing:
          { original_name, quarantined_at, reason, size_bytes }

        Files without a sidecar are still shown with inferred metadata.
        """
        import json as _json
        from pathlib import Path as _Path
        from datetime import datetime as _dt, timezone as _tz

        qdir = _Path("quarantine")
        files = []

        if qdir.exists():
            for p in sorted(qdir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                # Skip sidecar files themselves
                if p.suffix == ".json" and p.stem.endswith(".meta"):
                    continue
                if p.name.startswith("."):
                    continue

                # Try to read sidecar metadata
                sidecar = _Path(str(p) + ".meta.json")
                meta = {}
                if sidecar.exists():
                    try:
                        meta = _json.loads(sidecar.read_text(encoding="utf-8"))
                    except Exception:
                        pass

                reason = meta.get("reason", "Unknown failure")
                size_bytes = meta.get("size_bytes") or p.stat().st_size
                original_name = meta.get("original_name", p.name)
                quarantined_at_raw = meta.get("quarantined_at", "")

                # Human-readable timestamp
                try:
                    dt = _dt.fromisoformat(quarantined_at_raw.replace("Z", "+00:00"))
                    ts_human = dt.strftime("%Y-%m-%d %H:%M UTC")
                    ts_full  = dt.isoformat()
                except Exception:
                    mtime = _dt.fromtimestamp(p.stat().st_mtime, tz=_tz.utc)
                    ts_human = mtime.strftime("%Y-%m-%d %H:%M UTC")
                    ts_full  = mtime.isoformat()

                # Human-readable size
                if size_bytes >= 1_048_576:
                    size_human = f"{size_bytes / 1_048_576:.1f} MB"
                elif size_bytes >= 1_024:
                    size_human = f"{size_bytes / 1_024:.0f} KB"
                else:
                    size_human = f"{size_bytes} B"

                # Classify failure type for UI tagging
                r_lower = reason.lower()
                if "magic" in r_lower or "not a valid pdf" in r_lower or "%pdf" in r_lower:
                    fail_type = "magic"
                elif "too large" in r_lower or "size" in r_lower:
                    fail_type = "size"
                elif "pages" in r_lower or "page" in r_lower:
                    fail_type = "pages"
                elif "pikepdf" in r_lower or "parse" in r_lower or "open" in r_lower:
                    fail_type = "parse"
                else:
                    fail_type = "unknown"

                files.append({
                    "filename":          p.name,
                    "original_name":     original_name,
                    "reason":            reason,
                    "fail_type":         fail_type,
                    "size_bytes":        size_bytes,
                    "size_human":        size_human,
                    "quarantined_at":    ts_human,
                    "quarantined_at_full": ts_full,
                })

        # Count all-time successful detonations (stored in logs/pip_audit_* as a proxy;
        # a real implementation would persist this counter — default 0 for now)
        total_detonated = 0
        try:
            log_dir = _Path("logs")
            if log_dir.exists():
                # Count how many times forensic-process succeeded (approximation via
                # a lightweight counter file written by the detonator wrapper in app)
                counter_file = log_dir / "detonation_success_count.txt"
                if counter_file.exists():
                    total_detonated = int(counter_file.read_text().strip())
        except Exception:
            pass

        return render_template(
            "quarantine.html",
            files=files,
            total_detonated=total_detonated,
        )

    @app.route("/api/quarantine/<path:filename>/delete", methods=["POST"])
    def api_quarantine_delete(filename: str):
        """Permanently delete a single file (and its sidecar) from quarantine/."""
        from flask import jsonify as _jsonify
        from pathlib import Path as _Path

        # Security: prevent path traversal — filename must not contain separators
        if "/" in filename or "\\" in filename or ".." in filename:
            return _jsonify({"error": "Invalid filename"}), 400

        qdir = _Path("quarantine")
        target = qdir / filename

        # Only delete files that actually live inside quarantine/
        try:
            target.resolve().relative_to(qdir.resolve())
        except ValueError:
            return _jsonify({"error": "Path traversal denied"}), 403

        if not target.exists():
            return _jsonify({"error": "File not found"}), 404

        try:
            target.unlink()
            # Remove sidecar too
            sidecar = _Path(str(target) + ".meta.json")
            if sidecar.exists():
                sidecar.unlink()
            return _jsonify({"status": "deleted", "filename": filename})
        except OSError as exc:
            return _jsonify({"error": str(exc)}), 500

    @app.route("/api/quarantine/purge-all", methods=["POST"])
    def api_quarantine_purge_all():
        """Permanently delete every file in quarantine/ and return count."""
        from flask import jsonify as _jsonify
        from pathlib import Path as _Path

        qdir = _Path("quarantine")
        if not qdir.exists():
            return _jsonify({"status": "purged", "deleted": 0})

        deleted = 0
        errors  = []
        for p in list(qdir.iterdir()):
            if p.name.startswith("."):
                continue
            try:
                p.unlink()
                deleted += 1
            except OSError as exc:
                errors.append(str(exc))

        if errors:
            return _jsonify({"status": "partial", "deleted": deleted,
                             "errors": errors[:5]}), 207
        return _jsonify({"status": "purged", "deleted": deleted})

    return app


# ---------------------------------------------------------------------------
# Schema SQL (unchanged from Phase 2 — reproduced as single source of truth)
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS actors (
        actor_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT    NOT NULL,
        type             TEXT    NOT NULL
                         CHECK(type IN (
                             'person','institution','media','movement','government',
                             'location','political_party','organization','unknown',
                             'other','paramilitary'
                         )),
        description      TEXT,
        source_type      TEXT    NOT NULL DEFAULT 'live',
        created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
        confidence_score REAL    NOT NULL DEFAULT 0.5
                         CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
        automated        INTEGER NOT NULL DEFAULT 0,
        socint_profile   TEXT    DEFAULT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS priorities (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL UNIQUE,
        description TEXT,
        status      TEXT    NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','deprecated','disabled')),
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title            TEXT    NOT NULL,
        summary          TEXT,
        description      TEXT,
        date             TEXT,
        location         TEXT,
        latitude         REAL,
        longitude        REAL,
        category         TEXT
                         CHECK(category IN (
                             'Election','Security','Civil Unrest','Legislative',
                             'Economic','Diplomatic','Military','Social','Other'
                         )),
        source_type      TEXT    NOT NULL DEFAULT 'live',
        confidence_score REAL             DEFAULT 0.0,
        automated        INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """, 
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        title             TEXT    NOT NULL,
        description       TEXT,
        type              TEXT    NOT NULL
                          CHECK(type IN ('video','photo','document','audio','news','capture')),
        date              TEXT,
        location          TEXT,
        latitude          REAL,
        longitude         REAL,
        tags              TEXT,
        source            TEXT
                          CHECK(source IN (
                              'verified','unverified','government','leaked',
                              'citizen','media'
                          )),
        source_type       TEXT    NOT NULL DEFAULT 'live',
        file_path         TEXT,
        thumbnail         TEXT,
        event_id          INTEGER
                          REFERENCES events(event_id) ON DELETE SET NULL,
        created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        raw_text_cache    TEXT,
        processing_status TEXT    NOT NULL DEFAULT 'pending'
                          CHECK(processing_status IN
                              ('pending','processing','done','failed','skipped')),
        -- Phase 20: Forensic Artifact Intelligence Layer
        file_hash_sha256  TEXT,
        file_hash_md5     TEXT,
        file_size_bytes   INTEGER,
        exif_json         TEXT,
        gps_lat           REAL,
        gps_lng           REAL,
        device_make       TEXT,
        device_model      TEXT,
        exif_datetime     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_duplicates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        artifact_id     INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
        duplicate_of_id INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
        hash_sha256     TEXT    NOT NULL,
        detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (artifact_id, duplicate_of_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actor_network_metrics (
        actor_id             INTEGER PRIMARY KEY REFERENCES actors(actor_id) ON DELETE CASCADE,
        betweenness          REAL    NOT NULL DEFAULT 0,
        eigenvector          REAL    NOT NULL DEFAULT 0,
        pagerank             REAL    NOT NULL DEFAULT 0,
        community_id         INTEGER,
        community_id_socint  INTEGER DEFAULT NULL,
        node_count           INTEGER,
        edge_count           INTEGER,
        influence_score      REAL    NOT NULL DEFAULT 0,
        computed_at          TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_baselines (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        bucket_date  TEXT    NOT NULL,
        source       TEXT    NOT NULL,
        region_key   TEXT    NOT NULL,
        daily_count  INTEGER NOT NULL DEFAULT 0,
        computed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (bucket_date, source, region_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentinel_alerts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type       TEXT    NOT NULL,
        confidence_score REAL    NOT NULL DEFAULT 0.5
                         CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
        location_lat     REAL,
        location_lon     REAL,
        signal_count     INTEGER NOT NULL DEFAULT 1,
        summary          TEXT    NOT NULL,
        status           TEXT    NOT NULL DEFAULT 'new'
                         CHECK(status IN ('new','acknowledged','dismissed')),
        created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS correlated_incidents (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_a              TEXT    NOT NULL
                              REFERENCES signals(signal_id) ON DELETE CASCADE,
        signal_b              TEXT    NOT NULL
                              REFERENCES signals(signal_id) ON DELETE CASCADE,
        correlation_score     REAL    NOT NULL,
        distance_km           REAL    NOT NULL,
        time_difference_hours REAL    NOT NULL,
        space_score           REAL    NOT NULL,
        time_score            REAL    NOT NULL,
        detected_at           TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (signal_a, signal_b)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_relationships (
        relationship_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_actor_id  INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        object_actor_id   INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        relation_type     TEXT    NOT NULL,
        description       TEXT,
        confidence        REAL    NOT NULL DEFAULT 1.0
                          CHECK(confidence >= 0.0 AND confidence <= 1.0),
        source_artifact_id INTEGER REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
        source_event_id    INTEGER REFERENCES events(event_id) ON DELETE SET NULL,
        extraction_method  TEXT    NOT NULL DEFAULT 'manual'
                           CHECK(extraction_method IN ('manual','spacy','llm')),
        created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (subject_actor_id, object_actor_id, relation_type)
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
        case_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name              TEXT    NOT NULL,
        description       TEXT,
        hypothesis        TEXT,
        case_type         TEXT    DEFAULT 'general'
                          CHECK(case_type IN (
                              'general','financial','geopolitical','criminal',
                              'infrastructure','cyber','humanitarian','other'
                          )),
        status            TEXT    NOT NULL DEFAULT 'active'
                          CHECK(status IN ('active','closed','archived')),
        source_type       TEXT    NOT NULL DEFAULT 'live',
        created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        auto_generated    INTEGER NOT NULL DEFAULT 0,
        trigger_signal_id TEXT,
        context_anchors   TEXT
    )
    """,
    # Phase 16: case_signals — FORAGE→FORGE synthesis junction
    """
    CREATE TABLE IF NOT EXISTS case_signals (
        case_id     INTEGER NOT NULL REFERENCES cases(case_id)    ON DELETE CASCADE,
        signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
        note        TEXT,
        pinned_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (case_id, signal_id)
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
    # ── Phase 13 / 14: FORAGE Signal Ingestion + Pattern Engine ─────────────
    # Phase 33: Discovery targets — candidates suggested by evolution engine
    """
    CREATE TABLE IF NOT EXISTS discovery_targets (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_name     TEXT    NOT NULL UNIQUE,
        suggested_query TEXT    NOT NULL,
        evidence_count  INTEGER NOT NULL DEFAULT 0,
        evidence_json   TEXT,
        candidate_score REAL    NOT NULL DEFAULT 0.0,
        status          TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','approved','ignored')),
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        actioned_at     TEXT
    )
    """,
    # Phase 32: Pipeline run log — written by every collector and engine
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        component   TEXT    NOT NULL,
        status      TEXT    NOT NULL
                    CHECK(status IN ('success','error')),
        records_in  INTEGER,
        records_out INTEGER,
        duration_s  REAL,
        detail_json TEXT,
        run_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # Phase 14 adds cluster_id and is_priority.  CREATE TABLE IF NOT EXISTS is
    # idempotent for fresh databases.  The migrate_db() ALTER TABLE stanzas
    # below handle existing databases that have the Phase 13 schema already.
    """
    CREATE TABLE IF NOT EXISTS signals (
        signal_id          TEXT    PRIMARY KEY,
        source             TEXT    NOT NULL,
        external_id        TEXT    NOT NULL UNIQUE,
        title              TEXT    NOT NULL,
        content            TEXT,
        lat                REAL,
        lng                REAL,
        timestamp          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status             TEXT    NOT NULL DEFAULT 'raw'
                           CHECK(status IN ('raw','reviewed','promoted','dismissed')),
        metadata_json      TEXT,
        cluster_id         TEXT,
        is_priority        INTEGER NOT NULL DEFAULT 0,
        confidence_score   REAL,
        source_artifact_id INTEGER REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
        stream             TEXT    NOT NULL DEFAULT 'GLOBAL'
                           CHECK(stream IN
                               ('GLOBAL','CRIME_INTEL','INFRASTRUCTURE','PRIORITY')),
        relevance_score    REAL    NOT NULL DEFAULT 1.0,
        source_type        TEXT    NOT NULL DEFAULT 'live',
        -- Stable 1.1: Conclave cognition columns
        gravity_score      REAL,
        processed_at       TEXT,
        conclave_meta      TEXT,
        -- Stable 1.2: HealthMap dedup counter
        duplicate_count    INTEGER NOT NULL DEFAULT 0,
        -- FLUX SOCINT columns
        socint_tags        TEXT    DEFAULT NULL,
        socint_resonance   REAL    DEFAULT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_entities (
        entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
        text        TEXT    NOT NULL,
        label       TEXT    NOT NULL,
        count       INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (signal_id, text, label)
    )
    """,
    # ── Phase 63 / Stable 1.1: Actor–Signal and Actor–Event junction tables ──
    # signal_actors links pipeline-extracted actors to the signals that
    # surfaced them.  event_actors links actors to the escalated events they
    # were associated with.  Both carry FK constraints so dangling rows are
    # cleaned automatically when parent rows are deleted.
    """
    CREATE TABLE IF NOT EXISTS signal_actors (
        id         INTEGER  PRIMARY KEY AUTOINCREMENT,
        signal_id  TEXT     NOT NULL
                   REFERENCES signals(signal_id) ON DELETE CASCADE,
        actor_id   INTEGER  NOT NULL
                   REFERENCES actors(actor_id)   ON DELETE CASCADE,
        role       TEXT     DEFAULT 'mentioned',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (signal_id, actor_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_actors (
        id         INTEGER  PRIMARY KEY AUTOINCREMENT,
        event_id   INTEGER  NOT NULL
                   REFERENCES events(event_id)   ON DELETE CASCADE,
        actor_id   INTEGER  NOT NULL
                   REFERENCES actors(actor_id)   ON DELETE CASCADE,
        role       TEXT     DEFAULT 'involved',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (event_id, actor_id)
    )
    """,
    # ── Graph Substrate (Injection 04 / Sprint 2) ─────────────────────────────
    # graph_nodes MUST precede graph_edges — FK dependency.
    """
    CREATE TABLE IF NOT EXISTS graph_nodes (
        node_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        node_type     TEXT    NOT NULL,
        ref_id        TEXT    NOT NULL,
        label         TEXT,
        metadata_json TEXT,
        created_at    TEXT    DEFAULT (datetime('now')),
        UNIQUE(node_type, ref_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS graph_edges (
        edge_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source_node_id     INTEGER NOT NULL,
        target_node_id     INTEGER NOT NULL,
        relation_type      TEXT    NOT NULL,
        weight             REAL    DEFAULT 1.0,
        confidence         REAL    DEFAULT 1.0,
        source_event_id    INTEGER,
        source_signal_id   TEXT,
        source_artifact_id INTEGER,
        created_at         TEXT    DEFAULT (datetime('now')),
        UNIQUE(source_node_id, target_node_id, relation_type),
        FOREIGN KEY(source_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
        FOREIGN KEY(target_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
    )
    """,
    # ── Signal flags ──────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS signal_flags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
        flag_type   TEXT    NOT NULL,
        flag_label  TEXT    NOT NULL,
        confidence  REAL    NOT NULL DEFAULT 0.5
                    CHECK(confidence >= 0.0 AND confidence <= 1.0),
        cluster_id  TEXT,
        detail_json TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (signal_id, flag_type)
    )
    """,
    # ── Actor coalition & network emergence ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS actor_coalitions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id        INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        coalition_label TEXT    NOT NULL,
        co_occurrence   INTEGER NOT NULL DEFAULT 0,
        member_count    INTEGER NOT NULL DEFAULT 1,
        threshold_used  INTEGER NOT NULL DEFAULT 5,
        computed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (actor_id, coalition_label)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS actor_weights (
        actor_id   INTEGER PRIMARY KEY,
        weight     REAL    NOT NULL DEFAULT 1.0,
        updated_at TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS network_emergence (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id            INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        window_start        TEXT    NOT NULL,
        window_end          TEXT    NOT NULL,
        link_count          INTEGER NOT NULL DEFAULT 0,
        previous_link_count INTEGER NOT NULL DEFAULT 0,
        growth_rate         REAL    NOT NULL DEFAULT 0.0,
        emergence_score     REAL    NOT NULL DEFAULT 0.0,
        created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # ── FLUX SOCINT tables ────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS socint_signals (
        id            INTEGER  PRIMARY KEY AUTOINCREMENT,
        source        TEXT     NOT NULL DEFAULT 'x_pulse',
        actor_id      INTEGER  REFERENCES actors(actor_id) ON DELETE SET NULL,
        signal_id     TEXT     REFERENCES signals(signal_id) ON DELETE SET NULL,
        content       TEXT     NOT NULL,
        metadata_json TEXT     DEFAULT NULL,
        timestamp     TEXT     NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS socint_resonance (
        id            INTEGER  PRIMARY KEY AUTOINCREMENT,
        actor_a       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        actor_b       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
        score         REAL     NOT NULL DEFAULT 0.0,
        features_json TEXT     DEFAULT NULL,
        updated_at    TEXT     NOT NULL DEFAULT (datetime('now')),
        UNIQUE(actor_a, actor_b),
        CHECK(score >= 0.0 AND score <= 1.0),
        CHECK(actor_a < actor_b)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flux_latent_seeds (
        tag             TEXT    PRIMARY KEY,
        parent_seed     TEXT,
        discovery_depth INTEGER NOT NULL DEFAULT 1,
        jaccard_score   REAL    NOT NULL DEFAULT 0.0,
        velocity        REAL    NOT NULL DEFAULT 1.0,
        total_count     INTEGER NOT NULL DEFAULT 0,
        first_seen      TEXT    NOT NULL,
        last_seen       TEXT    NOT NULL,
        is_active       INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flux_tag_cooccurrence (
        pulse_id TEXT    NOT NULL,
        pulse_ts TEXT    NOT NULL,
        seed_tag TEXT    NOT NULL,
        co_tag   TEXT    NOT NULL,
        count    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (pulse_id, seed_tag, co_tag)
    )
    """,
    # ── Wiki intelligence tables ──────────────────────────────────────────────
    # wiki_articles MUST precede wiki_links — FK dependency on slug.
    """
    CREATE TABLE IF NOT EXISTS wiki_articles (
        id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
        slug               TEXT     UNIQUE,
        title              TEXT     NOT NULL,
        summary            TEXT,
        content            TEXT,
        content_html       TEXT,
        tags               TEXT,
        behavior           TEXT,
        features           TEXT,
        max_pulse_strength REAL,
        source_type        TEXT     DEFAULT 'live',
        last_updated       DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wiki_entries (
        id        INTEGER  PRIMARY KEY,
        actor_id  TEXT,
        event_id  TEXT,
        artifact  TEXT,
        timestamp DATETIME,
        narrative TEXT,
        context   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wiki_links (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_slug     TEXT,
        target_slug     TEXT,
        connection_type TEXT DEFAULT 'related',
        FOREIGN KEY(source_slug) REFERENCES wiki_articles(slug),
        FOREIGN KEY(target_slug) REFERENCES wiki_articles(slug)
    )
    """,
    # ── Pipeline jobs & case feedback ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS pipeline_jobs (
        job_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        job_key     TEXT    NOT NULL,
        status      TEXT    NOT NULL DEFAULT 'pending',
        stage       TEXT,
        progress    REAL    DEFAULT 0.0,
        message     TEXT,
        pid         INTEGER,
        records_in  INTEGER DEFAULT 0,
        records_out INTEGER DEFAULT 0,
        started_at  TEXT,
        updated_at  TEXT,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_feedback (
        case_id       TEXT PRIMARY KEY,
        gravity_score REAL,
        decision      TEXT,
        assigned_at   TEXT
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
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
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
    # discovery_targets and pipeline_runs are now in SCHEMA_STATEMENTS —
    # the CREATE TABLE IF NOT EXISTS stanzas that were here are removed to
    # eliminate the divergence.  Both tables are created by init_db() and
    # by the SCHEMA_STATEMENTS loop at the end of this function.

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
        # Phase 14 — Pattern Detection: spatiotemporal clustering + priority flag
        ("signals",        "cluster_id",       "TEXT"),
        ("signals",        "is_priority",      "INTEGER NOT NULL DEFAULT 0"),
        # Phase 15.5 — GDELT expansion: source column
        ("signals",        "source",           "TEXT"),
        # Phase 16 — Synthesis: hypothesis + case_type on cases
        ("cases",          "hypothesis",          "TEXT"),
        ("cases",          "case_type",           "TEXT DEFAULT 'general'"),
        # Phase 18 — Confidence scoring
        ("signals",        "confidence_score",    "REAL"),
        # Phase 19 — Artifact-First Architecture
        ("artifacts",      "raw_text_cache",      "TEXT"),
        ("artifacts",      "processing_status",   "TEXT NOT NULL DEFAULT 'pending'"),
        ("signals",        "source_artifact_id",  "INTEGER"),
        # Phase 27 — Signal Stream Engine
        ("signals", "stream",           "TEXT NOT NULL DEFAULT 'GLOBAL'"),
        # Phase 28 — Signal Decay Engine
        ("signals", "relevance_score",  "REAL NOT NULL DEFAULT 1.0"),
        # Phase 20 — Forensic Artifact Intelligence Layer
        ("artifacts",      "file_hash_sha256",    "TEXT"),
        ("artifacts",      "file_hash_md5",       "TEXT"),
        ("artifacts",      "file_size_bytes",     "INTEGER"),
        ("artifacts",      "exif_json",           "TEXT"),
        ("artifacts",      "gps_lat",             "REAL"),
        ("artifacts",      "gps_lng",             "REAL"),
        ("artifacts",      "device_make",         "TEXT"),
        ("artifacts",      "device_model",        "TEXT"),
        ("artifacts",      "exif_datetime",       "TEXT"),
        # C-4 Remediation — columns required by escalation_engine.create_event()
        ("events",         "description",         "TEXT"),
        ("events",         "confidence_score",    "REAL DEFAULT 0.0"),
        ("events",         "automated",           "INTEGER NOT NULL DEFAULT 0"),
        # ENT-01 Remediation — columns required by entity_engine.get_or_create_actor()
        # fix_schema.py adds these as FLOAT/BOOLEAN; migrate_db uses REAL/INTEGER for
        # consistency with the rest of the schema. Both work — SQLite type affinity is flexible.
        ("actors",         "confidence_score",    "REAL NOT NULL DEFAULT 0.5"),
        ("actors",         "automated",           "INTEGER NOT NULL DEFAULT 0"),
        ("actors",         "socint_profile",      "TEXT DEFAULT NULL"),
        # Analyst-internal flag — surfaced in FORGE UI only, not used by publish.py
        ("actors",         "blacklisted",         "INTEGER NOT NULL DEFAULT 0"),
        ("actors",         "blacklist_reason",    "TEXT"),
        ("actors",         "blacklist_added_at",  "TEXT"),
        # Analyst-curated portrait/building photo URL — surfaced on the
        # ZA-DIVERGENT Entity Directory cards and profile pages.
        ("actors",         "image_url",           "TEXT"),
    ]
    for table, column, col_type in migrations:
        if column not in _columns(table):
            print(f"  [migrate] {table} ← adding column: {column} {col_type}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    # Drop blacklist_public — superseded by the graph-eligibility-based
    # Entity Directory on ZA-DIVERGENT (no per-actor public-exposure flag).
    if "blacklist_public" in _columns("actors"):
        print("  [migrate] actors ← dropping column: blacklist_public")
        conn.execute("ALTER TABLE actors DROP COLUMN blacklist_public")

    # Phase 16: case_signals junction table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS case_signals (
            case_id     INTEGER NOT NULL REFERENCES cases(case_id)    ON DELETE CASCADE,
            signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
            note        TEXT,
            pinned_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (case_id, signal_id)
        )
    """)

    # Phase 18: NER entity extraction results
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_entities (
            entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
            text        TEXT    NOT NULL,
            label       TEXT    NOT NULL,
            count       INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (signal_id, text, label)
        )
    """)

    # Phase 19: backfill processing_status for existing artifacts
    conn.execute("""
        UPDATE artifacts SET processing_status='pending'
        WHERE processing_status IS NULL
    """)

    # Phase 20: duplicate registry
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifact_duplicates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id     INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
            duplicate_of_id INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
            hash_sha256     TEXT    NOT NULL,
            detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (artifact_id, duplicate_of_id)
        )
    """)

    # Phase 21: actor network metrics
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actor_network_metrics (
            actor_id        INTEGER PRIMARY KEY REFERENCES actors(actor_id) ON DELETE CASCADE,
            betweenness     REAL    NOT NULL DEFAULT 0,
            eigenvector     REAL    NOT NULL DEFAULT 0,
            pagerank        REAL    NOT NULL DEFAULT 0,
            community_id    INTEGER,
            node_count      INTEGER,
            edge_count      INTEGER,
            computed_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Phase 23: correlated incidents
    conn.execute("""
        CREATE TABLE IF NOT EXISTS correlated_incidents (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_a              TEXT    NOT NULL
                                  REFERENCES signals(signal_id) ON DELETE CASCADE,
            signal_b              TEXT    NOT NULL
                                  REFERENCES signals(signal_id) ON DELETE CASCADE,
            correlation_score     REAL    NOT NULL,
            distance_km           REAL    NOT NULL,
            time_difference_hours REAL    NOT NULL,
            space_score           REAL    NOT NULL,
            time_score            REAL    NOT NULL,
            detected_at           TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (signal_a, signal_b)
        )
    """)

    # Phase 22: entity relationships
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entity_relationships (
            relationship_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_actor_id   INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
            object_actor_id    INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
            relation_type      TEXT    NOT NULL,
            description        TEXT,
            confidence         REAL    NOT NULL DEFAULT 1.0
                               CHECK(confidence >= 0.0 AND confidence <= 1.0),
            source_artifact_id INTEGER REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
            source_event_id    INTEGER REFERENCES events(event_id) ON DELETE SET NULL,
            extraction_method  TEXT    NOT NULL DEFAULT 'manual'
                               CHECK(extraction_method IN ('manual','spacy','llm')),
            created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (subject_actor_id, object_actor_id, relation_type)
        )
    """)

    # Phase 24: influence_score column on actor_network_metrics
    try:
        existing_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(actor_network_metrics)")}
        if "influence_score" not in existing_cols:
            conn.execute(
                "ALTER TABLE actor_network_metrics "
                "ADD COLUMN influence_score REAL NOT NULL DEFAULT 0"
            )
            print("  [migrate] actor_network_metrics <- adding column: influence_score REAL")
    except Exception:
        pass

    # Phase 26: Anomaly engine baseline cache
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_baselines (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_date  TEXT    NOT NULL,
            source       TEXT    NOT NULL,
            region_key   TEXT    NOT NULL,
            daily_count  INTEGER NOT NULL DEFAULT 0,
            computed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE (bucket_date, source, region_key)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_baselines_lookup "
        "ON signal_baselines (source, region_key, bucket_date)"
    )

    # Phase 25: Sentinel alert store
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentinel_alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type       TEXT    NOT NULL,
            confidence_score REAL    NOT NULL DEFAULT 0.5
                             CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
            location_lat     REAL,
            location_lon     REAL,
            signal_count     INTEGER NOT NULL DEFAULT 1,
            summary          TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'new'
                             CHECK(status IN ('new','acknowledged','dismissed')),
            created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ── Stable 1.1: Schema Harmonization — heal any existing database ────────
    #
    # cases.title → cases.name  (SQLite 3.25+ RENAME COLUMN; safe on fresh DBs)
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(cases)")}
        if "title" in _cols and "name" not in _cols:
            conn.execute("ALTER TABLE cases RENAME COLUMN title TO name")
            print("  [migrate] cases ← renamed column: title → name")
    except Exception as _e:
        print(f"  [migrate] cases rename skipped: {_e}")

    # New columns on cases and signals — idempotent via column presence check
    _stable11 = [
        ("cases",   "auto_generated",    "INTEGER NOT NULL DEFAULT 0"),
        ("cases",   "trigger_signal_id", "TEXT"),
        ("cases",   "context_anchors",        "TEXT"),
        ("signals", "gravity_score",          "REAL"),
        ("signals", "processed_at",           "TEXT"),
        ("signals", "conclave_meta",          "TEXT"),
        ("signals", "duplicate_count",        "INTEGER NOT NULL DEFAULT 0"),
    ]
    for _tbl, _col, _ctype in _stable11:
        _existing = {r[1] for r in conn.execute(f"PRAGMA table_info({_tbl})")}
        if _col not in _existing:
            print(f"  [migrate] {_tbl} ← adding column: {_col} {_ctype}")
            conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_ctype}")
    conn.commit()
    # ── End Stable 1.1 ────────────────────────────────────────────────────────

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