#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE Schema Verifier  (migrations/verify_schema.py)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Validates that the live database matches the Conclave contract and the
canonical schema defined in migrations/schema.sql.

Prevents ENT-01–class failures: columns referenced by pipeline code that
are absent from the live schema (causing silent INSERT failures).

Usage
─────
    python migrations/verify_schema.py
    python migrations/verify_schema.py --db /path/to/database.db
    python migrations/verify_schema.py --quiet   # exit-code only, no output

Exit codes
──────────
    0  — all checks pass
    1  — one or more required tables or columns are missing

Can also be imported as a module:
    from migrations.verify_schema import verify, REQUIRED_COLUMNS
    ok, failures = verify(db_path)
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# ── Required schema contract ──────────────────────────────────────────────────
# Maps table_name → set of required column names.
# This is the *minimum* contract — the Conclave pipeline and FMS modules
# break silently when these are absent.  Add entries here whenever a new
# engine or module references a column that may not exist on older DBs.
#
# Rationale per group:
#   actors      — ENT-01 fix: entity_engine.get_or_create_actor() inserts
#                 confidence_score, automated; flux module writes socint_profile
#   signals     — decay_engine, feed route, CT-1 scorer all require these cols
#   cases       — CT-1 build_context() reads context_anchors
#   events      — entity_engine.materialize_entities() inserts confidence_score,
#                 automated, description
#   actor_network_metrics — FLUX resonance writes community_id_socint

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "signals": {
        "signal_id", "source", "external_id", "title", "content",
        "lat", "lng", "timestamp", "status", "stream",
        "is_priority", "relevance_score", "source_type",
        "gravity_score", "processed_at", "conclave_meta",
        "confidence_score", "duplicate_count",
        "socint_tags", "socint_resonance",
    },
    "actors": {
        "actor_id", "name", "type", "description",
        "source_type", "created_at",
        "confidence_score",  # ENT-01
        "automated",         # ENT-01
        "socint_profile",    # FLUX
    },
    "events": {
        "event_id", "title", "summary", "date", "location",
        "latitude", "longitude", "category", "source_type", "created_at",
        "confidence_score", "automated", "description",
    },
    "cases": {
        "case_id", "name", "description", "hypothesis",
        "case_type", "status", "source_type", "created_at",
        "auto_generated", "trigger_signal_id",
        "context_anchors",  # CT-1 build_context()
    },
    "actor_network_metrics": {
        "actor_id", "betweenness", "eigenvector", "pagerank",
        "community_id", "community_id_socint", "node_count",
        "edge_count", "influence_score", "computed_at",
    },
    "signal_actors": {
        "id", "signal_id", "actor_id", "role", "created_at",
    },
    "case_signals": {
        "case_id", "signal_id", "note", "pinned_at",
    },
    "case_actors": {
        "case_id", "actor_id", "note", "pinned_at",
    },
    "entity_relationships": {
        "relationship_id", "subject_actor_id", "object_actor_id",
        "relation_type", "confidence", "extraction_method", "created_at",
    },
    "signal_entities": {
        "entity_id", "signal_id", "text", "label", "count", "created_at",
    },
    "socint_resonance": {
        "id", "actor_a", "actor_b", "score", "features_json", "updated_at",
    },
    "socint_signals": {
        "id", "source", "actor_id", "signal_id", "content",
        "metadata_json", "timestamp",
    },
    "correlated_incidents": {
        "id", "signal_a", "signal_b", "correlation_score",
        "distance_km", "time_difference_hours", "space_score",
        "time_score", "detected_at",
    },
    "sentinel_alerts": {
        "id", "alert_type", "confidence_score", "location_lat",
        "location_lon", "signal_count", "summary", "status", "created_at",
    },
    "pipeline_runs": {
        "id", "component", "status", "records_in", "records_out",
        "duration_s", "detail_json", "run_at",
    },
}

# Tables that must exist (superset of REQUIRED_COLUMNS keys)
REQUIRED_TABLES: set[str] = set(REQUIRED_COLUMNS.keys()) | {
    "artifacts", "artifact_duplicates",
    "case_events", "case_artifacts", "actor_events", "event_actors",
    "actor_coalitions", "actor_weights", "network_emergence",
    "graph_nodes", "graph_edges",
    "signal_baselines", "signal_flags",
    "discovery_targets", "priorities",
    "pipeline_jobs", "case_feedback",
    "flux_latent_seeds", "flux_tag_cooccurrence",
    "wiki_articles", "wiki_entries", "wiki_links",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent / "database.db"


def _live_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _live_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# ── Verifier ──────────────────────────────────────────────────────────────────

def verify(db_path: Path | None = None) -> tuple[bool, list[str]]:
    """
    Run all schema checks against the database at db_path.

    Returns (ok: bool, failures: list[str]) where failures lists
    every missing table or column found.
    """
    path     = db_path or _resolve_db()
    failures = []

    if not path.exists():
        return False, [f"Database not found at {path}"]

    conn = sqlite3.connect(str(path))
    try:
        live_tables = _live_tables(conn)

        # 1. Table existence
        missing_tables = REQUIRED_TABLES - live_tables
        for t in sorted(missing_tables):
            failures.append(f"MISSING TABLE: {t}")

        # 2. Column existence
        for table, required_cols in sorted(REQUIRED_COLUMNS.items()):
            if table not in live_tables:
                continue  # already flagged above
            live_cols = _live_columns(conn, table)
            for col in sorted(required_cols - live_cols):
                failures.append(f"MISSING COLUMN: {table}.{col}")

    finally:
        conn.close()

    return len(failures) == 0, failures


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="FORGE Schema Verifier — checks Conclave column contract"
    )
    parser.add_argument("--db",    type=Path, default=None,
                        help="Override path to database.db")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress output; use exit code only")
    args = parser.parse_args()

    db_path = _resolve_db(str(args.db) if args.db else None)

    if not args.quiet:
        print(f"[verify_schema] Database: {db_path}")

    ok, failures = verify(db_path)

    if ok:
        if not args.quiet:
            print(f"[verify_schema] OK All {len(REQUIRED_TABLES)} tables and "
                  f"{sum(len(v) for v in REQUIRED_COLUMNS.values())} "
                  f"required columns present.")
        return 0

    if not args.quiet:
        print(f"[verify_schema] FAIL {len(failures)} schema issue(s) found:")
        for f in failures:
            print(f"  • {f}")
        print()
        print("  Run: python app.py --migrate")
        print("  Or:  python migrations/fix_schema.py")

    return 1


if __name__ == "__main__":
    sys.exit(main())
