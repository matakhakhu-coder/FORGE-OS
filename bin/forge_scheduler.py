#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — Background Scheduler Daemon
════════════════════════════════════

Cross-platform background service that automates the FORGE operational
cadence. Runs headlessly as a persistent process.

Schedule:
  Every 6 hours  — Collection sweep (mega_ingest --collect-only)
  Every 6 hours  — Decay engine (exponential score decay)
  Daily 02:00    — Wiki compilation
  Daily 06:00    — Static publisher build + deploy
  Weekly Sun 03:00 — Database sanitization (integrity + VACUUM)

Usage:
  python bin/forge_scheduler.py              # run in foreground
  python bin/forge_scheduler.py --once       # run all tasks once and exit
  pythonw bin/forge_scheduler.py             # run headless (Windows)
  nohup python bin/forge_scheduler.py &      # run headless (Linux)

Logs to: logs/scheduler.log
"""

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [scheduler] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOGS_DIR / "scheduler.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("forge.scheduler")

# ── Task definitions ─────────────────────────────────────────────────────────

def _run_script(name: str, args: list[str], timeout: int = 900) -> bool:
    """Run a Python script as a subprocess. Returns True on success."""
    cmd = [PYTHON] + args
    log.info(f"[{name}] Starting: {' '.join(args)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            lines = proc.stdout.strip().split("\n")
            last = lines[-1] if lines else ""
            log.info(f"[{name}] OK ({last[:100]})")
            return True
        else:
            err = proc.stderr.strip()[-200:] if proc.stderr else proc.stdout.strip()[-200:]
            log.error(f"[{name}] FAILED (exit {proc.returncode}): {err}")
            return False
    except subprocess.TimeoutExpired:
        log.error(f"[{name}] TIMEOUT after {timeout}s")
        return False
    except Exception as exc:
        log.error(f"[{name}] ERROR: {exc}")
        return False


def task_collect():
    return _run_script("collect", ["tools/mega_ingest.py", "--collect-only"], timeout=900)

def task_decay():
    return _run_script("decay", [
        "-c", "from forage.engines.decay_engine import DecayEngine; DecayEngine().run()"
    ], timeout=120)

def task_wiki():
    return _run_script("wiki", ["tools/init_wiki.py"], timeout=300)

def task_publish():
    return _run_script("publish", ["tools/publish.py", "--deploy"], timeout=300)

def task_sanitize():
    return _run_script("sanitize", ["tools/sanitize_db.py"], timeout=300)

def task_health():
    return _run_script("health", ["tools/health_check.py"], timeout=60)


# ── Schedule engine ──────────────────────────────────────────────────────────

class ScheduleEntry:
    def __init__(self, name: str, task, interval_hours: float = 0,
                 daily_hour: int = -1, weekly_day: int = -1):
        self.name = name
        self.task = task
        self.interval_hours = interval_hours
        self.daily_hour = daily_hour
        self.weekly_day = weekly_day  # 0=Monday, 6=Sunday
        self.last_run: float = 0

    def should_run(self, now: datetime) -> bool:
        elapsed_hours = (time.time() - self.last_run) / 3600

        # Interval-based (every N hours)
        if self.interval_hours > 0:
            return elapsed_hours >= self.interval_hours

        # Daily at specific hour
        if self.daily_hour >= 0:
            if now.hour == self.daily_hour and elapsed_hours >= 20:
                return True

        # Weekly on specific day at specific hour
        if self.weekly_day >= 0:
            if now.weekday() == self.weekly_day and now.hour == 3 and elapsed_hours >= 160:
                return True

        return False

    def run(self):
        self.last_run = time.time()
        return self.task()


SCHEDULE = [
    ScheduleEntry("collection_sweep", task_collect, interval_hours=6),
    ScheduleEntry("decay_engine",     task_decay,   interval_hours=6),
    ScheduleEntry("wiki_compile",     task_wiki,    daily_hour=2),
    ScheduleEntry("publisher_deploy", task_publish,  daily_hour=6),
    ScheduleEntry("db_sanitize",      task_sanitize, weekly_day=6),  # Sunday
    ScheduleEntry("health_check",     task_health,   interval_hours=1),
]


def run_scheduler(once: bool = False):
    log.info("FORGE Scheduler starting")
    log.info(f"  Python: {PYTHON}")
    log.info(f"  Base: {BASE_DIR}")
    log.info(f"  Tasks: {len(SCHEDULE)}")
    log.info(f"  Mode: {'single pass' if once else 'persistent daemon'}")

    if once:
        for entry in SCHEDULE:
            log.info(f"Running: {entry.name}")
            entry.run()
        log.info("Single pass complete")
        return

    # Persistent loop — check every 60 seconds
    try:
        while True:
            now = datetime.now(timezone.utc)
            for entry in SCHEDULE:
                if entry.should_run(now):
                    try:
                        entry.run()
                    except Exception as exc:
                        log.error(f"[{entry.name}] Unhandled: {exc}")
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Scheduler stopped by operator")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FORGE Background Scheduler")
    parser.add_argument("--once", action="store_true",
                        help="Run all tasks once and exit (no loop)")
    args = parser.parse_args()
    run_scheduler(once=args.once)
