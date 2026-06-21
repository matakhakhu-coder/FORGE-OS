#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — Pipeline Health Monitor
════════════════════════════════

Diagnostic checks for automated health monitoring:
  1. Database integrity (PRAGMA integrity_check)
  2. Pipeline freshness (last successful run < 12 hours)
  3. Collector fleet importability (AST manifest scan)
  4. Database file size (not frozen/empty)

Output: JSON health report to stdout + logs/health_alert.json on failure.

Usage:
  python tools/health_check.py              # full check
  python tools/health_check.py --json       # JSON-only output (for cron)
  python tools/health_check.py --alert-email admin@example.com  # future
"""

import ast
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))
ALERT_PATH = BASE_DIR / "logs" / "health_alert.json"
STALE_THRESHOLD_HOURS = 12


def check_integrity(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    ok = len(rows) == 1 and rows[0][0] == "ok"
    return {
        "check": "database_integrity",
        "status": "pass" if ok else "fail",
        "detail": "ok" if ok else str(rows[:5]),
    }


def check_pipeline_freshness(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT MAX(run_at) FROM pipeline_runs WHERE status = 'success'"
    ).fetchone()
    last_run = row[0] if row and row[0] else None

    if not last_run:
        return {
            "check": "pipeline_freshness",
            "status": "warn",
            "detail": "No successful pipeline runs recorded",
            "last_run": None,
        }

    try:
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        last_dt = None

    if last_dt:
        age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        stale = age_hours > STALE_THRESHOLD_HOURS
    else:
        age_hours = None
        stale = True

    return {
        "check": "pipeline_freshness",
        "status": "fail" if stale else "pass",
        "detail": f"Last run {age_hours:.1f}h ago" if age_hours else "Could not parse timestamp",
        "last_run": last_run,
        "age_hours": round(age_hours, 1) if age_hours else None,
        "threshold_hours": STALE_THRESHOLD_HOURS,
    }


def check_collector_fleet() -> dict:
    collector_roots = [
        BASE_DIR / "forage" / "collectors",
        BASE_DIR / "flux" / "collectors",
    ]
    healthy = 0
    broken = []

    for root in collector_roots:
        if not root.exists():
            continue
        for py_path in sorted(root.glob("*.py")):
            if py_path.name.startswith("_") or py_path.name == "__init__.py":
                continue
            try:
                source = py_path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_path))
                has_manifest = any(
                    isinstance(node, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == "__manifest__" for t in node.targets)
                    for node in ast.walk(tree)
                )
                if has_manifest:
                    healthy += 1
                else:
                    broken.append({"file": py_path.name, "reason": "no __manifest__"})
            except SyntaxError as exc:
                broken.append({"file": py_path.name, "reason": f"SyntaxError: {exc}"})

    return {
        "check": "collector_fleet",
        "status": "pass" if not broken else "fail",
        "healthy": healthy,
        "broken": broken,
        "total": healthy + len(broken),
    }


def check_database_size() -> dict:
    if not DB_PATH.exists():
        return {
            "check": "database_size",
            "status": "fail",
            "detail": f"Database not found: {DB_PATH}",
            "size_mb": 0,
        }

    size_bytes = DB_PATH.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 2)

    return {
        "check": "database_size",
        "status": "pass" if size_bytes > 1024 else "fail",
        "size_mb": size_mb,
        "detail": f"{size_mb} MB" if size_bytes > 1024 else "Database is empty or corrupt",
    }


def check_signal_count(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT COUNT(*) FROM signals").fetchone()
    count = row[0] if row else 0
    return {
        "check": "signal_count",
        "status": "pass" if count > 0 else "warn",
        "count": count,
    }


def run_health_check(json_only: bool = False) -> dict:
    start = time.monotonic()
    now = datetime.now(timezone.utc).isoformat()

    results = []

    # DB size check (no connection needed)
    results.append(check_database_size())

    # Collector fleet (AST scan, no DB needed)
    results.append(check_collector_fleet())

    # DB-dependent checks
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.row_factory = sqlite3.Row
        try:
            results.append(check_integrity(conn))
            results.append(check_pipeline_freshness(conn))
            results.append(check_signal_count(conn))
        finally:
            conn.close()
    else:
        results.append({"check": "database_connection", "status": "fail", "detail": "DB not found"})

    duration = round(time.monotonic() - start, 2)

    # Overall verdict
    statuses = [r["status"] for r in results]
    if "fail" in statuses:
        verdict = "UNHEALTHY"
    elif "warn" in statuses:
        verdict = "DEGRADED"
    else:
        verdict = "HEALTHY"

    report = {
        "timestamp": now,
        "verdict": verdict,
        "duration_s": duration,
        "checks": results,
    }

    # Write alert file if unhealthy
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if verdict != "HEALTHY":
        ALERT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not json_only:
            print(f"[ALERT] Health check: {verdict} — details at {ALERT_PATH}", file=sys.stderr)
    else:
        # Clear stale alerts
        if ALERT_PATH.exists():
            ALERT_PATH.unlink()

    if json_only:
        print(json.dumps(report, indent=2))
    else:
        print(f"[health] FORGE Health Check — {verdict}")
        print(f"[health] DB: {DB_PATH}")
        for r in results:
            icon = "OK" if r["status"] == "pass" else "WARN" if r["status"] == "warn" else "FAIL"
            detail = r.get("detail", r.get("count", r.get("healthy", "")))
            print(f"  [{icon:4s}] {r['check']:25s} {detail}")
        print(f"[health] Completed in {duration}s")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FORGE Pipeline Health Monitor")
    parser.add_argument("--json", action="store_true", help="JSON-only output")
    args = parser.parse_args()
    report = run_health_check(json_only=args.json)
    sys.exit(0 if report["verdict"] == "HEALTHY" else 1)
