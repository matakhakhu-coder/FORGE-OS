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

# Shared mutable state — imported from core.web.state so blueprints
# and app.py operate on the same objects.
from core.web.state import _KILL_FLAGS, _PIPELINE_LOCK, _PIPELINE_ACTIVE


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

from core.web.state import _COLLECTOR_REGISTRY, _DEAD_NODES

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


from core.web.helpers import (
    telemetry_init as _telemetry_init,
    create_job as _create_job,
    update_job as _update_job,
    finalize_job as _finalize_job,
)


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

    # ── SEC-1: Block production boot with default secrets ─────────────────
    _flask_env = os.environ.get("FLASK_ENV", "development").lower()
    if _flask_env == "production":
        if app.secret_key == "forge-dev-secret":
            raise RuntimeError(
                "FORGE_SECRET_KEY is set to the default value. "
                "Set a strong secret via FORGE_SECRET_KEY env var before running in production."
            )
        if ADMIN_PASSWORD == "forge-admin":
            raise RuntimeError(
                "FORGE_ADMIN_PASSWORD is set to the default value. "
                "Set a strong password via FORGE_ADMIN_PASSWORD env var before running in production."
            )

    # -----------------------------------------------------------------------
    # Database helpers — get_db() imported from core.web.helpers
    # -----------------------------------------------------------------------
    from core.web.helpers import get_db

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

    # ── Extracted Blueprints (Phase 2 modularization) ─────────────────────────
    from core.web.blueprints.pages import pages_bp
    from core.web.blueprints.signals import signals_bp
    from core.web.blueprints.cases import cases_bp
    from core.web.blueprints.admin import admin_bp
    from core.web.blueprints.graph import graph_bp
    from core.web.blueprints.map_routes import map_bp
    from core.web.blueprints.control import control_bp
    from core.web.blueprints.diagnostics import diagnostics_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(signals_bp)
    app.register_blueprint(cases_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(map_bp)
    app.register_blueprint(control_bp)
    app.register_blueprint(diagnostics_bp)

    # ── Error handlers ───────────────────────────────────────────────────────

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500


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
        FOREIGN KEY(source_slug) REFERENCES wiki_articles(slug) ON DELETE CASCADE,
        FOREIGN KEY(target_slug) REFERENCES wiki_articles(slug) ON DELETE CASCADE
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
    # ── FK indexes — SQLite does NOT auto-index FK columns ────────────────
    "CREATE INDEX IF NOT EXISTS idx_signal_actors_signal ON signal_actors (signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_signal_actors_actor  ON signal_actors (actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_actors_event   ON event_actors (event_id)",
    "CREATE INDEX IF NOT EXISTS idx_event_actors_actor   ON event_actors (actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_source   ON graph_edges (source_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_edges_target   ON graph_edges (target_node_id)",
    "CREATE INDEX IF NOT EXISTS idx_entity_rel_subject   ON entity_relationships (subject_actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_entity_rel_object    ON entity_relationships (object_actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_corr_signal_a        ON correlated_incidents (signal_a)",
    "CREATE INDEX IF NOT EXISTS idx_corr_signal_b        ON correlated_incidents (signal_b)",
    "CREATE INDEX IF NOT EXISTS idx_case_signals_signal  ON case_signals (signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_signal_entities_sig  ON signal_entities (signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_socint_signals_actor ON socint_signals (actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_actor_coalitions_aid ON actor_coalitions (actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_net_emergence_actor  ON network_emergence (actor_id)",
    "CREATE INDEX IF NOT EXISTS idx_socint_resonance_a   ON socint_resonance (actor_a)",
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

    # Phase 19: backfill processing_status for existing artifacts
    conn.execute("""
        UPDATE artifacts SET processing_status='pending'
        WHERE processing_status IS NULL
    """)

    # ── Stable 1.1: Schema Harmonization — heal any existing database ────────
    # cases.title → cases.name  (SQLite 3.25+ RENAME COLUMN; safe on fresh DBs)
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(cases)")}
        if "title" in _cols and "name" not in _cols:
            conn.execute("ALTER TABLE cases RENAME COLUMN title TO name")
            print("  [migrate] cases ← renamed column: title → name")
    except Exception as _e:
        print(f"  [migrate] cases rename skipped: {_e}")

    # ── Stable 1.2: actor_network_metrics parity — add columns that
    # SCHEMA_STATEMENTS defines but old migrate_db() inline CREATE missed.
    _anm_extra = [
        ("actor_network_metrics", "community_id_socint", "INTEGER DEFAULT NULL"),
        ("actor_network_metrics", "influence_score",     "REAL NOT NULL DEFAULT 0"),
    ]
    for _tbl, _col, _ctype in (_anm_extra):
        if _col not in _columns(_tbl):
            print(f"  [migrate] {_tbl} ← adding column: {_col} {_ctype}")
            conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_ctype}")

    # ── Stable 1.2 (AD-4 fix): Recreate wiki_links with ON DELETE CASCADE.
    # Existing wiki_links has RESTRICT (no ON DELETE clause). Table-recreation
    # is the only way to alter FK constraints in SQLite.
    try:
        fk_info = conn.execute("PRAGMA foreign_key_list(wiki_links)").fetchall()
        needs_cascade = any(
            row["on_delete"] != "CASCADE" for row in fk_info
        ) if fk_info else False
        if needs_cascade:
            print("  [migrate] wiki_links ← recreating with ON DELETE CASCADE")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wiki_links_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_slug     TEXT,
                    target_slug     TEXT,
                    connection_type TEXT DEFAULT 'related',
                    FOREIGN KEY(source_slug) REFERENCES wiki_articles(slug) ON DELETE CASCADE,
                    FOREIGN KEY(target_slug) REFERENCES wiki_articles(slug) ON DELETE CASCADE
                )
            """)
            conn.execute("INSERT INTO wiki_links_new SELECT * FROM wiki_links")
            conn.execute("DROP TABLE wiki_links")
            conn.execute("ALTER TABLE wiki_links_new RENAME TO wiki_links")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            print("  [migrate] wiki_links ← ON DELETE CASCADE applied")
    except Exception as _wl_exc:
        print(f"  [migrate] wiki_links cascade migration skipped: {_wl_exc}")

    conn.commit()

    # ── Stable 1.2 (AD-3 fix): All CREATE TABLE and CREATE INDEX stanzas
    # are now driven by the single canonical SCHEMA_STATEMENTS array.
    # Duplicate inline CREATE TABLE blocks that previously drifted from
    # SCHEMA_STATEMENTS have been removed. CREATE TABLE IF NOT EXISTS is
    # idempotent for tables that already exist, and new tables/indexes are
    # automatically picked up.
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