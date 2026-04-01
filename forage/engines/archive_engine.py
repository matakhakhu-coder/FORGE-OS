"""
FORGE — Archive Engine
======================
Moves a closed case's signals, events and artifacts out of the live tables
and into archive tables. All operations run inside a single transaction —
either the entire archive completes or nothing changes.

Safety rules
------------
1. SHARED signals  — a signal pinned to multiple cases is only moved if ALL
   other cases it belongs to are also archived/closed. Otherwise it stays
   live and is COPIED (not moved) to the archive for provenance.
2. SHARED events   — same rule as signals.
3. SHARED artifacts— same rule.
4. Actors          — never archived. Actors are identity nodes that span
   cases; removing them would break the graph.
5. Child records   — signal_entities and correlated_incidents rows that
   reference archived signals are deleted from live tables after the copy
   (ON DELETE CASCADE handles this if FK enforcement is on, but we do it
   explicitly for safety).
6. The operation is IDEMPOTENT — running it twice on the same case is safe;
   the second run finds nothing to move and returns early.

Returns a result dict compatible with the existing pipeline_runs log format.
"""

import sqlite3
import time
import logging
from pathlib import Path

log = logging.getLogger("forge.archive")

# Resolved at import time so the engine works both as a module and standalone
DB_PATH = Path(__file__).resolve().parent / "database.db"


class ArchiveEngine:

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # ── Public entry point ────────────────────────────────────────────────

    def archive_case(self, case_id: int) -> dict:
        """
        Archive all intelligence associated with case_id.

        Steps
        -----
        1. Validate case exists and is not already archived.
        2. Collect signal_ids, event_ids, artifact_ids via junction tables.
        3. Filter out any IDs shared with other ACTIVE cases (safety rule 1-3).
        4. Copy surviving IDs into *_archive tables with archived_case_id.
        5. Delete copied rows from live junction tables then live data tables.
        6. Mark case status = 'archived'.
        7. Log to pipeline_runs.

        All steps 2-6 run inside ONE transaction.
        """
        start = time.time()
        conn  = self._connect()

        try:
            # ── Pre-flight checks ─────────────────────────────────────────
            case = conn.execute(
                "SELECT case_id, title, status FROM cases WHERE case_id = ?",
                (case_id,)
            ).fetchone()

            if not case:
                return {"status": "error", "error": f"Case {case_id} not found"}

            if case["status"] == "archived":
                return {
                    "status":  "skipped",
                    "case_id": case_id,
                    "reason":  "Case is already archived",
                }

            conn.execute("BEGIN")

            # ── 1. Collect candidate IDs from junction tables ─────────────
            signal_ids = [
                r["signal_id"] for r in conn.execute(
                    "SELECT signal_id FROM case_signals WHERE case_id = ?",
                    (case_id,)
                ).fetchall()
            ]
            event_ids = [
                r["event_id"] for r in conn.execute(
                    "SELECT event_id FROM case_events WHERE case_id = ?",
                    (case_id,)
                ).fetchall()
            ]
            artifact_ids = [
                r["artifact_id"] for r in conn.execute(
                    "SELECT artifact_id FROM case_artifacts WHERE case_id = ?",
                    (case_id,)
                ).fetchall()
            ]

            # ── 2. Filter: keep only IDs not shared with other active cases
            def _exclusive(ids, table, id_col):
                """
                Return IDs that belong ONLY to this case (or to
                archived/closed cases — those are safe to move).
                """
                if not ids:
                    return [], []
                ph = ",".join("?" * len(ids))
                shared = conn.execute(f"""
                    SELECT DISTINCT j.{id_col}
                    FROM   {table} j
                    JOIN   cases c ON c.case_id = j.case_id
                    WHERE  j.{id_col} IN ({ph})
                      AND  j.case_id  != ?
                      AND  c.status   NOT IN ('archived', 'closed')
                """, (*ids, case_id)).fetchall()
                shared_set    = {r[0] for r in shared}
                exclusive_ids = [i for i in ids if i not in shared_set]
                shared_ids    = [i for i in ids if i in shared_set]
                return exclusive_ids, shared_ids

            excl_signals,   shared_signals   = _exclusive(
                signal_ids,   "case_signals",   "signal_id")
            excl_events,    shared_events     = _exclusive(
                event_ids,    "case_events",    "event_id")
            excl_artifacts, shared_artifacts  = _exclusive(
                artifact_ids, "case_artifacts", "artifact_id")

            # ── 3. Copy exclusive rows into archive tables ─────────────────
            def _copy_signals(ids):
                if not ids:
                    return 0
                ph = ",".join("?" * len(ids))
                conn.execute(f"""
                    INSERT OR IGNORE INTO signals_archive (
                        signal_id, source, external_id, title, content,
                        lat, lng, timestamp, status, metadata_json,
                        cluster_id, is_priority, confidence_score,
                        source_artifact_id, stream, relevance_score,
                        source_type, archived_case_id
                    )
                    SELECT
                        signal_id, source, external_id, title, content,
                        lat, lng, timestamp, status, metadata_json,
                        cluster_id, is_priority, confidence_score,
                        source_artifact_id, stream, relevance_score,
                        source_type, ?
                    FROM signals
                    WHERE signal_id IN ({ph})
                """, (case_id, *ids))
                return len(ids)

            def _copy_events(ids):
                if not ids:
                    return 0
                ph = ",".join("?" * len(ids))
                conn.execute(f"""
                    INSERT OR IGNORE INTO events_archive (
                        event_id, title, summary, date, location,
                        latitude, longitude, category, source_type,
                        created_at, archived_case_id
                    )
                    SELECT
                        event_id, title, summary, date, location,
                        latitude, longitude, category, source_type,
                        created_at, ?
                    FROM events
                    WHERE event_id IN ({ph})
                """, (case_id, *ids))
                return len(ids)

            def _copy_artifacts(ids):
                if not ids:
                    return 0
                ph = ",".join("?" * len(ids))
                conn.execute(f"""
                    INSERT OR IGNORE INTO artifacts_archive (
                        artifact_id, title, description, type, date,
                        location, latitude, longitude, tags, source,
                        source_type, file_path, thumbnail, event_id,
                        created_at, raw_text_cache, processing_status,
                        file_hash_sha256, file_hash_md5, file_size_bytes,
                        exif_json, gps_lat, gps_lng, device_make,
                        device_model, exif_datetime, archived_case_id
                    )
                    SELECT
                        artifact_id, title, description, type, date,
                        location, latitude, longitude, tags, source,
                        source_type, file_path, thumbnail, event_id,
                        created_at, raw_text_cache, processing_status,
                        file_hash_sha256, file_hash_md5, file_size_bytes,
                        exif_json, gps_lat, gps_lng, device_make,
                        device_model, exif_datetime, ?
                    FROM artifacts
                    WHERE artifact_id IN ({ph})
                """, (case_id, *ids))
                return len(ids)

            # Also copy shared IDs for provenance (INSERT OR IGNORE — safe)
            _copy_signals(signal_ids)       # all (excl + shared)
            _copy_events(event_ids)
            _copy_artifacts(artifact_ids)

            # ── 4. Delete exclusive rows from live tables ──────────────────

            def _delete_in(table, col, ids):
                if not ids:
                    return
                ph = ",".join("?" * len(ids))
                conn.execute(
                    f"DELETE FROM {table} WHERE {col} IN ({ph})", ids
                )

            # Child records first (FK cascade may handle some, explicit is safer)
            if excl_signals:
                ph = ",".join("?" * len(excl_signals))
                # signal_entities
                conn.execute(
                    f"DELETE FROM signal_entities WHERE signal_id IN ({ph})",
                    excl_signals
                )
                # correlated_incidents — both sides
                conn.execute(
                    f"DELETE FROM correlated_incidents "
                    f"WHERE signal_a IN ({ph}) OR signal_b IN ({ph})",
                    (*excl_signals, *excl_signals)
                )
                # case_signals for ALL cases (junction cleaned up)
                conn.execute(
                    f"DELETE FROM case_signals WHERE signal_id IN ({ph})",
                    excl_signals
                )

            if excl_events:
                ph = ",".join("?" * len(excl_events))
                # actor_events
                conn.execute(
                    f"DELETE FROM actor_events WHERE event_id IN ({ph})",
                    excl_events
                )
                # event_actors (pipeline-generated)
                try:
                    conn.execute(
                        f"DELETE FROM event_actors WHERE event_id IN ({ph})",
                        excl_events
                    )
                except Exception:
                    pass  # table may not exist on all schemas
                # case_events for ALL cases
                conn.execute(
                    f"DELETE FROM case_events WHERE event_id IN ({ph})",
                    excl_events
                )

            if excl_artifacts:
                ph = ",".join("?" * len(excl_artifacts))
                # artifact_duplicates
                try:
                    conn.execute(
                        f"DELETE FROM artifact_duplicates "
                        f"WHERE artifact_id IN ({ph}) "
                        f"OR duplicate_of_id IN ({ph})",
                        (*excl_artifacts, *excl_artifacts)
                    )
                except Exception:
                    pass
                # case_artifacts for ALL cases
                conn.execute(
                    f"DELETE FROM case_artifacts WHERE artifact_id IN ({ph})",
                    excl_artifacts
                )

            # Now delete the live rows themselves
            _delete_in("signals",   "signal_id",   excl_signals)
            _delete_in("events",    "event_id",     excl_events)
            _delete_in("artifacts", "artifact_id",  excl_artifacts)

            # ── 5. Clean up remaining junction rows for THIS case ──────────
            # (shared rows that were not deleted above)
            conn.execute(
                "DELETE FROM case_signals   WHERE case_id = ?", (case_id,))
            conn.execute(
                "DELETE FROM case_events    WHERE case_id = ?", (case_id,))
            conn.execute(
                "DELETE FROM case_artifacts WHERE case_id = ?", (case_id,))
            conn.execute(
                "DELETE FROM case_actors    WHERE case_id = ?", (case_id,))

            # ── 6. Mark case archived ──────────────────────────────────────
            conn.execute(
                "UPDATE cases SET status = 'archived' WHERE case_id = ?",
                (case_id,)
            )

            conn.commit()

            duration = round(time.time() - start, 2)
            result = {
                "status":           "success",
                "case_id":          case_id,
                "case_title":       case["title"],
                "archived_signals": len(excl_signals),
                "shared_signals":   len(shared_signals),
                "archived_events":  len(excl_events),
                "shared_events":    len(shared_events),
                "archived_artifacts": len(excl_artifacts),
                "shared_artifacts": len(shared_artifacts),
                "duration_s":       duration,
            }

            # ── 7. Log to pipeline_runs ────────────────────────────────────
            try:
                import json as _json
                conn2 = self._connect()
                conn2.execute("""
                    INSERT INTO pipeline_runs
                        (component, status, records_in, records_out,
                         duration_s, detail_json)
                    VALUES (?, 'success', ?, ?, ?, ?)
                """, (
                    "archive_engine",
                    len(signal_ids) + len(event_ids) + len(artifact_ids),
                    len(excl_signals) + len(excl_events) + len(excl_artifacts),
                    duration,
                    _json.dumps({"case_id": case_id,
                                 "case_title": case["title"]}),
                ))
                conn2.commit()
                conn2.close()
            except Exception:
                pass  # logging failure never blocks the archive

            log.info(
                f"[archive] Case {case_id} archived in {duration}s — "
                f"signals: {len(excl_signals)}, events: {len(excl_events)}, "
                f"artifacts: {len(excl_artifacts)}"
            )
            return result

        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            log.error(f"[archive] Case {case_id} archive failed: {exc}")

            try:
                import json as _json
                conn2 = self._connect()
                conn2.execute("""
                    INSERT INTO pipeline_runs
                        (component, status, records_in, records_out,
                         duration_s, detail_json)
                    VALUES ('archive_engine', 'error', 0, 0, ?, ?)
                """, (
                    round(time.time() - start, 2),
                    _json.dumps({"case_id": case_id, "error": str(exc)}),
                ))
                conn2.commit()
                conn2.close()
            except Exception:
                pass

            return {"status": "error", "case_id": case_id, "error": str(exc)}

        finally:
            conn.close()

    def query_archive(self, case_id: int = None) -> dict:
        """
        Return archived intelligence for a case (or all cases if case_id=None).
        Used by the /api/archive/<case_id> query endpoint.
        """
        conn = self._connect()
        where = "WHERE archived_case_id = ?" if case_id else ""
        params = (case_id,) if case_id else ()

        signals = conn.execute(
            f"SELECT * FROM signals_archive {where} "
            f"ORDER BY timestamp DESC", params
        ).fetchall()
        events = conn.execute(
            f"SELECT * FROM events_archive {where} "
            f"ORDER BY date DESC", params
        ).fetchall()
        artifacts = conn.execute(
            f"SELECT * FROM artifacts_archive {where} "
            f"ORDER BY created_at DESC", params
        ).fetchall()
        conn.close()

        return {
            "case_id":   case_id,
            "signals":   [dict(r) for r in signals],
            "events":    [dict(r) for r in events],
            "artifacts": [dict(r) for r in artifacts],
            "totals": {
                "signals":   len(signals),
                "events":    len(events),
                "artifacts": len(artifacts),
            },
        }