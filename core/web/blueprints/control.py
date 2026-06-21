#!/usr/bin/env python3
from __future__ import annotations

"""
Control Room blueprint — pipeline dispatch, telemetry, quarantine management.

Extracted from app.py (Stable 1.1–Phase 72).
"""

import os
import re
import sqlite3
import subprocess
import sys
import threading

from flask import Blueprint, jsonify, render_template, request

from core.web.helpers import (
    BASE_DIR,
    DB_PATH,
    create_job,
    finalize_job,
    get_db,
    update_job,
)
from core.web.state import (
    _COLLECTOR_REGISTRY,
    _DEAD_NODES,
    _KILL_FLAGS,
    _PIPELINE_ACTIVE,
)

# Avoid circular — imported lazily where needed:
#   signal (as _signal) — only in kill_job

control_bp = Blueprint("control", __name__)


# ---------------------------------------------------------------------------
# Helper: Soft Scoped Intake — auto-pin membrane
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helper: Artifact processor stdout stream parser
# ---------------------------------------------------------------------------

# Match "Loop batch N: this=X total=Y done=Z skipped=W entities=V rate=R/s ..."
_ARTIFACT_PROGRESS_RE = re.compile(
    r'Loop\s+batch\s+\d+\s*:\s*this=(\d+)\s+total=(\d+)\s+done=(\d+)\s+skipped=(\d+)'
)
# Match per-artifact processing line: "Extracting: somefile.pdf (type=pdf)"
# or "Progress N/M -- title..." -- used to tag poisoned artifacts.
_ARTIFACT_FILE_RE = re.compile(
    r'Extracting:\s+(\S+\.\S+)|Processing\s+single\s+artifact:\s+(\d+)',
    re.IGNORECASE,
)


def _stream_artifact_processor(proc: subprocess.Popen, job_id: int,
                               max_artifacts: int) -> None:
    """
    Wrapper thread: parses stdout, drives pipeline_jobs row.
    Progress is computed as total_processed / max_artifacts (the cap we
    passed on the command line) — gives a reliable 0->1 trajectory even
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
                finalize_job(
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
                update_job(
                    job_id,
                    status="running",
                    progress=progress,
                    message=line[-300:],   # tail — last 300 chars of log line
                    records_out=done,
                )
            elif "ERROR" in line or "Traceback" in line:
                poison = f" [poison: {last_artifact_seen}]" if last_artifact_seen else ""
                update_job(
                    job_id,
                    message=f"[ERR]{poison} {line[-260:]}",
                )
            elif line.startswith("[") and "Loop complete" in line:
                # Final summary line — capture for completion message
                update_job(job_id, message=line[-300:])

        rc = proc.wait()
        if rc == 0:
            finalize_job(
                job_id, "completed",
                f"Drain complete — {done} artifacts processed",
                records_out=done, progress=1.0,
            )
        else:
            finalize_job(
                job_id, "failed",
                f"Process exited with code {rc}",
                records_out=done,
                progress=(done / max_artifacts) if max_artifacts else 0.0,
            )
    except Exception as exc:
        finalize_job(job_id, "failed",
                     f"Reader thread crash: {exc}", records_out=done)


# =======================================================================
# Routes
# =======================================================================


# -----------------------------------------------------------------------
# Stable 1.1 — Collector Autodiscovery: registry + per-collector dispatch
# -----------------------------------------------------------------------

@control_bp.route("/api/control/registry", methods=["GET"])
def api_control_registry():
    """
    Return the full collector registry with per-source signal counts
    and last pipeline run timestamps. The frontend uses this to render
    the collector control matrix with health badges.
    """
    db = get_db()
    collectors = list(_COLLECTOR_REGISTRY.values())

    # Per-source signal counts
    try:
        rows = db.execute(
            "SELECT source, COUNT(*) AS cnt FROM signals GROUP BY source"
        ).fetchall()
        source_counts = {r[0]: r[1] for r in rows}
    except Exception:
        source_counts = {}

    # Last pipeline run per component
    try:
        rows = db.execute(
            "SELECT component, MAX(run_at) AS last_run, status "
            "FROM pipeline_runs GROUP BY component"
        ).fetchall()
        last_runs = {r[0]: {"last_run": r[1], "status": r[2]} for r in rows}
    except Exception:
        last_runs = {}

    # Enrich each collector with live metrics
    for c in collectors:
        cid = c.get("id", "")
        c["signal_count"] = source_counts.get(cid, 0)
        run_info = last_runs.get(cid, {})
        c["last_run"] = run_info.get("last_run")
        c["last_status"] = run_info.get("status")

    return jsonify({
        "collectors": collectors,
        "dead_nodes": _DEAD_NODES,
        "total":      len(collectors),
        "dead_count": len(_DEAD_NODES),
    })


# ── Stable 1.2: Soft Scoped Intake — discover route ─────────────────────

@control_bp.route("/api/discover/<int:actor_id>", methods=["POST"])
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


@control_bp.route("/api/control/run_collector/<collector_id>", methods=["POST"])
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
    job_id  = create_job(job_key, f"Queued: {manifest['name']}")

    def _reader(proc, jid):
        """Generic stdout reader — streams output into job message."""
        _pinned = [0]
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    update_job(jid, message=line[-300:])
            rc = proc.wait()
            if rc == 0:
                # ── Soft Scoped Intake membrane ───────────────────────
                if context_case_id is not None:
                    update_job(jid, message=(
                        f"{manifest['name']} finished — running "
                        f"Scoped Intake for case {context_case_id}…"
                    ))
                    _auto_pin_to_case(
                        context_case_id,
                        manifest["id"],   # collector source key
                        job_start_iso,
                        _pinned,
                    )
                    finalize_job(
                        jid, "completed",
                        f"{manifest['name']} done — "
                        f"{_pinned[0]} signals auto-pinned to case "
                        f"{context_case_id}",
                    )
                else:
                    finalize_job(jid, "completed",
                                f"{manifest['name']} finished (exit 0)")
            else:
                finalize_job(jid, "failed",
                             f"{manifest['name']} exited with code {rc}")
        except Exception as exc:
            finalize_job(jid, "failed", f"Reader thread error: {exc}")

    try:
        if _KILL_FLAGS.pop(job_id, False):
            finalize_job(job_id, "killed", "Terminated before start")
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
        update_job(job_id, status="running", pid=proc.pid,
                   message=f"PID {proc.pid} — {manifest['name']} started"
                           + (f" [case {context_case_id}]"
                              if context_case_id else ""))

        threading.Thread(target=_reader, args=(proc, job_id),
                         daemon=True).start()

    except Exception as exc:
        finalize_job(job_id, "failed", f"Failed to spawn: {exc}")
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

@control_bp.route("/api/control/run_collectors", methods=["POST"])
def api_control_run_collectors():
    """
    Spawn all FORAGE collectors concurrently.
    Mirrors mega_ingest.run_all_collectors() — async coroutines driven
    in a background thread via asyncio.run() so Flask is never blocked.
    """
    import asyncio

    if _PIPELINE_ACTIVE.get("run_collectors"):
        return jsonify({"status": "rejected", "reason": "already running"}), 409

    def _run():
        _PIPELINE_ACTIVE["run_collectors"] = True
        try:
            from tools.mega_ingest import run_all_collectors
            asyncio.run(run_all_collectors())
        except Exception as exc:
            import logging
            logging.getLogger("forge.control").error(
                f"[control/run_collectors] {exc}"
            )
        finally:
            _PIPELINE_ACTIVE.pop("run_collectors", None)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job": "run_collectors"})


@control_bp.route("/api/control/run_ingest", methods=["POST"])
def api_control_run_ingest():
    """
    Run the full Conclave ingest pass over all signals.
    Mirrors mega_ingest.run_full_ingest().
    """
    if _PIPELINE_ACTIVE.get("run_ingest"):
        return jsonify({"status": "rejected", "reason": "already running"}), 409

    def _run():
        _PIPELINE_ACTIVE["run_ingest"] = True
        try:
            from tools.mega_ingest import run_full_ingest
            run_full_ingest(batch_size=50, sleep_interval=0.1)
        except Exception as exc:
            import logging
            logging.getLogger("forge.control").error(
                f"[control/run_ingest] {exc}"
            )
        finally:
            _PIPELINE_ACTIVE.pop("run_ingest", None)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job": "run_ingest"})


@control_bp.route("/api/control/run_conclave", methods=["POST"])
def api_control_run_conclave():
    """
    Run the full engines + processors pass in one shot.
    Mirrors mega_ingest.run_engines_processors():
      artifact_processor -> cluster_engine -> ner_processor -> anomaly_engine
      -> correlation_engine -> decay_engine -> evolution_engine
      -> graph_engine -> sentinel
    """
    if _PIPELINE_ACTIVE.get("run_conclave"):
        return jsonify({"status": "rejected", "reason": "already running"}), 409

    def _run():
        _PIPELINE_ACTIVE["run_conclave"] = True
        try:
            from tools.mega_ingest import run_engines_processors
            run_engines_processors()
        except Exception as exc:
            import logging
            logging.getLogger("forge.control").error(
                f"[control/run_conclave] {exc}"
            )
        finally:
            _PIPELINE_ACTIVE.pop("run_conclave", None)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job": "run_conclave"})


@control_bp.route("/api/control/run_graph_engine", methods=["POST"])
def api_control_run_graph_engine():
    """
    Recompute actor network graph metrics in isolation.
    GraphEngine(db_path=...).run() confirmed: class at line 119,
    .run() at line 353 of forage/engines/graph_engine.py.
    """
    if _PIPELINE_ACTIVE.get("run_graph_engine"):
        return jsonify({"status": "rejected", "reason": "already running"}), 409

    def _run():
        _PIPELINE_ACTIVE["run_graph_engine"] = True
        try:
            from forage.engines.graph_engine import GraphEngine
            GraphEngine(db_path=DB_PATH).run()
        except Exception as exc:
            import logging
            logging.getLogger("forge.control").error(
                f"[control/run_graph_engine] {exc}"
            )
        finally:
            _PIPELINE_ACTIVE.pop("run_graph_engine", None)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job": "run_graph_engine"})


@control_bp.route("/api/control/run_coalition_detector", methods=["POST"])
def api_control_run_coalition_detector():
    """
    Run coalition detection in isolation (replaces the Phase 34 stub).
    Accepts optional JSON body: { "threshold": <int> } (default 5).
    Runs in a background thread — returns immediately.
    """
    body      = request.get_json(silent=True) or {}
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

@control_bp.route("/api/control/run_emergence", methods=["POST"])
def api_control_run_emergence():
    """
    Trigger the emergence engine (time-window actor growth analysis).
    Runs in a background thread — returns immediately.

    No request body required.
    """
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

@control_bp.route("/api/control/archive_case/<int:case_id>", methods=["POST"])
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
    try:
        from forage.engines.archive_engine import ArchiveEngine
        result = ArchiveEngine(db_path=DB_PATH).archive_case(case_id)
        code   = 200 if result["status"] in ("success", "skipped") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"status": "error", "case_id": case_id,
                        "error": str(exc)}), 500


@control_bp.route("/api/archive/<int:case_id>")
def api_archive_query(case_id: int):
    """
    Query the archive for a specific case.
    Returns all archived signals, events and artifacts with their
    original data intact plus archived_at timestamp.
    """
    try:
        from forage.engines.archive_engine import ArchiveEngine
        result = ArchiveEngine(db_path=DB_PATH).query_archive(case_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@control_bp.route("/api/archive")
def api_archive_index():
    """
    Summary of all archived cases — which cases have been archived
    and how many records each holds.
    """
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

@control_bp.route("/api/counterintel/flags")
def api_counterintel_flags():
    """
    Return flagged signals.
    Query params:
      ?type=narrative_cluster|bot_pattern|information_campaign
      ?min_confidence=0.0-1.0  (default 0.0)
      ?limit=200               (max 500)
    """
    try:
        from forge_modules.counterintel.engine import query_flags
        flag_type      = request.args.get("type", "").strip() or None
        min_confidence = request.args.get("min_confidence", 0.0, type=float)
        limit          = min(request.args.get("limit", 200, type=int), 500)
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


@control_bp.route("/api/counterintel/summary")
def api_counterintel_summary():
    """Return aggregate flag counts by type."""
    try:
        from forge_modules.counterintel.engine import query_summary
        return jsonify(query_summary(db_path=DB_PATH))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@control_bp.route("/api/control/run_counterintel", methods=["POST"])
def api_control_run_counterintel():
    """
    Run CounterIntel scan in background thread.
    Returns immediately with { "status": "started" }.
    Results queryable at /api/counterintel/flags after completion.
    """
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
# =======================================================================

@control_bp.route("/api/control/run_artifact_processor", methods=["POST"])
def api_control_run_artifact_processor():
    """
    Drain the artifact queue. Spawns python -m forage.processors.artifact_processor
    as a subprocess so the Flask process never accumulates spaCy memory.

    Body (JSON, optional):
      { "batch_size": 500, "max_artifacts": 5000, "status": "pending" }
    """
    body = request.get_json(silent=True) or {}
    batch_size    = int(body.get("batch_size", 500))
    max_artifacts = int(body.get("max_artifacts", 5000))
    status_filter = str(body.get("status", "pending"))

    # Sanity clamps — protect the OS from absurd inputs
    batch_size    = max(1,  min(batch_size,    5000))
    max_artifacts = max(1,  min(max_artifacts, 200000))

    job_id = create_job(
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
        update_job(job_id, status="running", pid=proc.pid,
                   message=f"PID {proc.pid} — drain started")

        threading.Thread(
            target=_stream_artifact_processor,
            args=(proc, job_id, max_artifacts),
            daemon=True,
        ).start()
    except Exception as exc:
        finalize_job(job_id, "failed", f"Failed to spawn: {exc}")
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

@control_bp.route("/api/control/run_promote_staged", methods=["POST"])
def api_control_run_promote_staged():
    """
    Promote high-fidelity PERSON entities from signal_entities into actors.
    Backed by scripts/promote_staged_entities.run().
    """
    job_id = create_job("promote_staged", "Queued: actor promotion gate")

    def _run():
        try:
            if _KILL_FLAGS.pop(job_id, False):
                finalize_job(job_id, "killed", "Terminated before start")
                return
            update_job(job_id, status="running",
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
            finalize_job(
                job_id, "completed",
                f"Promoted {inserted} new actors (examined {examined})",
                records_out=inserted, progress=1.0,
            )
        except Exception as exc:
            finalize_job(job_id, "failed", f"{exc}")
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

@control_bp.route("/api/control/run_triple_extractor", methods=["POST"])
def api_control_run_triple_extractor():
    """
    Run NLP relationship extraction over signals. Body (optional):
      { "limit": 2000, "pdf_only": false }
    """
    body = request.get_json(silent=True) or {}
    limit    = max(1, min(int(body.get("limit", 2000)), 50000))
    pdf_only = bool(body.get("pdf_only", False))

    job_id = create_job(
        "triple_extractor",
        f"Queued: limit={limit} pdf_only={pdf_only}",
    )

    def _run():
        try:
            if _KILL_FLAGS.pop(job_id, False):
                finalize_job(job_id, "killed", "Terminated before start")
                return
            update_job(job_id, status="running",
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
            finalize_job(
                job_id, "completed",
                f"{triples} triples · {examined} signals scanned",
                records_out=triples, progress=1.0,
            )
        except Exception as exc:
            finalize_job(job_id, "failed", f"{exc}")
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

@control_bp.route("/api/control/run_wiki_pipeline", methods=["POST"])
def api_control_run_wiki_pipeline():
    """
    Three-stage wiki synthesis:
      1) schema_init   — wiki schema guard
      2) wiki_compiler — entity-driven dossier synthesis
      3) link_engine   — bidirectional cross-reference graph
    Stage transitions are kill-checkpoints (no mid-stage interruption).
    """
    job_id = create_job("wiki_pipeline", "Queued: 3-stage wiki synthesis")

    def _run():
        articles = 0
        try:
            # ── Stage 1: schema_init ─────────────────────────────────
            if _KILL_FLAGS.pop(job_id, False):
                finalize_job(job_id, "killed", "Terminated before start")
                return
            update_job(job_id, status="running",
                       stage="schema_init [1/3]", progress=0.05,
                       message="Initializing wiki schema")
            from core.db.wiki import init_wiki_db
            init_wiki_db()
            update_job(job_id, progress=0.10, message="Schema ready")

            # ── Stage 2: wiki_compiler ───────────────────────────────
            if _KILL_FLAGS.pop(job_id, False):
                finalize_job(job_id, "killed",
                             "Terminated between schema_init and compiler")
                return
            update_job(job_id,
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
            update_job(job_id, progress=0.85,
                       records_out=articles,
                       message=f"Compiler done — {articles} dossiers")

            # ── Stage 3: link_engine ─────────────────────────────────
            if _KILL_FLAGS.pop(job_id, False):
                finalize_job(job_id, "killed",
                             "Terminated between compiler and link_engine",
                             records_out=articles, progress=0.85)
                return
            update_job(job_id,
                       stage="link_engine [3/3]", progress=0.90,
                       message="Building cross-reference graph")
            from wiki.engines.wiki_link_engine import WikiLinkEngine
            WikiLinkEngine(DB_PATH).run()

            finalize_job(
                job_id, "completed",
                f"Wiki pipeline complete — {articles} dossiers linked",
                records_out=articles, progress=1.0,
            )
        except Exception as exc:
            finalize_job(job_id, "failed", f"{exc}",
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

@control_bp.route("/api/control/jobs/active", methods=["GET"])
def api_control_jobs_active():
    """
    Return all non-terminal jobs plus the most recent terminal job per
    job_key from the last 6 hours. The frontend Poller calls this every
    ~1.5s while jobs are live; the response drives the progress UI.
    """
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


@control_bp.route("/api/control/kill_job/<int:job_id>", methods=["POST"])
def api_control_kill_job(job_id: int):
    """
    Terminate a running/pending job:
      - Subprocess jobs (artifact_processor): os.kill on stored PID.
      - In-process jobs: set _KILL_FLAGS[job_id]=True; worker checks at
        stage boundaries.
    """
    import signal as _signal

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

        finalize_job(job_id, "killed", "Terminated by operator")
        return jsonify({"status": "killed", "job_id": job_id,
                        "job_key": row["job_key"]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# -----------------------------------------------------------------------
# Quarantine Manager — Contagion Ward
# -----------------------------------------------------------------------

@control_bp.route("/quarantine")
def quarantine_manager():
    """
    Contagion Ward — display all files that failed the PDF Air-Lock.

    Each quarantined file has a matching .meta.json sidecar written by
    forge_security.detonator._quarantine() containing:
      { original_name, quarantined_at, reason, size_bytes }

    Files without a sidecar are still shown with inferred metadata.
    """
    import json as _json
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from pathlib import Path as _Path

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
        from pathlib import Path as _Path2
        log_dir = _Path2("logs")
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


@control_bp.route("/api/quarantine/<path:filename>/delete", methods=["POST"])
def api_quarantine_delete(filename: str):
    """Permanently delete a single file (and its sidecar) from quarantine/."""
    from pathlib import Path as _Path

    # Security: prevent path traversal — filename must not contain separators
    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"error": "Invalid filename"}), 400

    qdir = _Path("quarantine")
    target = qdir / filename

    # Only delete files that actually live inside quarantine/
    try:
        target.resolve().relative_to(qdir.resolve())
    except ValueError:
        return jsonify({"error": "Path traversal denied"}), 403

    if not target.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        target.unlink()
        # Remove sidecar too
        sidecar = _Path(str(target) + ".meta.json")
        if sidecar.exists():
            sidecar.unlink()
        return jsonify({"status": "deleted", "filename": filename})
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500


@control_bp.route("/api/quarantine/purge-all", methods=["POST"])
def api_quarantine_purge_all():
    """Permanently delete every file in quarantine/ and return count."""
    from pathlib import Path as _Path

    qdir = _Path("quarantine")
    if not qdir.exists():
        return jsonify({"status": "purged", "deleted": 0})

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
        return jsonify({"status": "partial", "deleted": deleted,
                        "errors": errors[:5]}), 207
    return jsonify({"status": "purged", "deleted": deleted})


# ── Sprint 3: One-click publish ──────────────────────────────────────────────

@control_bp.route("/api/admin/publish", methods=["POST"])
def api_admin_publish():
    """Trigger a ZA-DIVERGENT publish + deploy cycle from the admin panel."""
    import subprocess, sys, threading

    if _PIPELINE_ACTIVE.get("publish"):
        return jsonify({"status": "rejected", "reason": "publish already running"}), 409

    job_id = create_job("publish", "ZA-DIVERGENT publish + deploy")

    def _run():
        _PIPELINE_ACTIVE["publish"] = True
        try:
            update_job(job_id, status="running", message="Publishing to ZA-DIVERGENT...")
            proc = subprocess.run(
                [sys.executable, "tools/publish.py", "--deploy"],
                capture_output=True, text=True, timeout=300,
                cwd=str(BASE_DIR), encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                finalize_job(job_id, "completed", "Publish + deploy complete")
            else:
                err = (proc.stderr or proc.stdout or "")[-200:]
                finalize_job(job_id, "failed", f"Exit {proc.returncode}: {err}")
        except subprocess.TimeoutExpired:
            finalize_job(job_id, "failed", "Publish timed out after 300s")
        except Exception as exc:
            finalize_job(job_id, "failed", str(exc)[:200])
        finally:
            _PIPELINE_ACTIVE.pop("publish", None)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "job_id": job_id, "job": "publish"})
