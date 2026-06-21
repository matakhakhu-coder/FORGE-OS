#!/usr/bin/env python3
from __future__ import annotations
"""
_seed_outbreak_gravity.py
─────────────────────────
Seeds case 11 "Project Aegis: Regional Pathogen Surveillance Cluster" and
pins the highest-gravity disease signals to it.

Safe to re-run (INSERT OR IGNORE throughout).  Zero schema changes.

Selection criteria:
  - source = 'disease_outbreak_collector'
  - lat/lng within Africa / SADC bounding box OR no coordinates (global outbreak)
  - gravity_score >= 0.30
  - Top 10 by gravity, then by timestamp DESC
"""

import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone

ROOT    = pathlib.Path(__file__).parent.parent
DB_PATH = ROOT / "database.db"

# ── SADC + broader Africa bounding box ───────────────────────────────────────
# lat:  -35  ->  38   (Cape to North Africa)
# lng:  -20  ->  55   (Atlantic coast to East Africa)
LAT_MIN, LAT_MAX = -35.0,  38.0
LNG_MIN, LNG_MAX = -20.0,  55.0

MAX_SIGNALS = 10    # maximum signals to publish for this case

# ── Case definition ───────────────────────────────────────────────────────────
CASE = {
    "name":        "Project Aegis: Regional Pathogen Surveillance Cluster",
    "description": (
        "Forensic open-source monitoring of anomalous disease vectors and "
        "early-warning signals across SADC and international transit nodes. "
        "Aggregates WHO outbreak alerts, CDC Health Alert Network advisories, "
        "and ProMED early-warning feeds through a multi-tier sensor stack."
    ),
    "hypothesis": (
        "Cross-border disease vectors and humanitarian health crises in the "
        "SADC and Central African corridors represent underreported security "
        "risks with potential knock-on effects for South African port-of-entry "
        "and border health infrastructure."
    ),
    "case_type":   "humanitarian",   # closest fit — schema: general|financial|geopolitical|criminal|infrastructure|cyber|humanitarian|other
    "status":      "active",
    "source_type": "live",
}


def run() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        # ── 1. Insert case (skip if already present by name) ─────────────────
        existing = cur.execute(
            "SELECT case_id FROM cases WHERE name = ?", (CASE["name"],)
        ).fetchone()

        if existing:
            case_id = existing["case_id"]
            print(f"[aegis] Case already exists -> case_id={case_id}")
        else:
            cur.execute("""
                INSERT INTO cases (name, description, hypothesis, case_type,
                                   status, source_type, created_at, auto_generated)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                CASE["name"], CASE["description"], CASE["hypothesis"],
                CASE["case_type"], CASE["status"], CASE["source_type"],
                now_iso,
            ))
            case_id = cur.lastrowid
            print(f"[aegis] Case inserted -> case_id={case_id}")

        # ── 2. Select candidate signals ───────────────────────────────────────
        # Primary pass: Africa/SADC bounding box
        africa_signals = cur.execute("""
            SELECT signal_id, title, gravity_score, lat, lng, timestamp
            FROM   signals
            WHERE  source       = 'disease_outbreak_collector'
              AND  gravity_score >= 0.30
              AND  lat IS NOT NULL AND lng IS NOT NULL
              AND  lat BETWEEN ? AND ?
              AND  lng BETWEEN ? AND ?
            ORDER  BY gravity_score DESC, timestamp DESC
            LIMIT  ?
        """, (LAT_MIN, LAT_MAX, LNG_MIN, LNG_MAX, MAX_SIGNALS)).fetchall()

        # Supplementary pass: global signals with no geo (WHO/CDC advisories)
        # Fill remaining slots up to MAX_SIGNALS
        remaining = MAX_SIGNALS - len(africa_signals)
        global_signals = []
        if remaining > 0:
            africa_ids = tuple(r["signal_id"] for r in africa_signals)
            placeholders = ",".join("?" * len(africa_ids)) if africa_ids else "'__none__'"
            global_signals = cur.execute(f"""
                SELECT signal_id, title, gravity_score, lat, lng, timestamp
                FROM   signals
                WHERE  source       = 'disease_outbreak_collector'
                  AND  gravity_score >= 0.30
                  AND  (lat IS NULL OR lng IS NULL OR lat = 0.0)
                  AND  signal_id NOT IN ({placeholders})
                ORDER  BY gravity_score DESC, timestamp DESC
                LIMIT  {remaining}
            """, africa_ids).fetchall()

        selected = list(africa_signals) + list(global_signals)
        print(f"[aegis] Selected {len(selected)} signals "
              f"({len(africa_signals)} Africa, {len(global_signals)} global)")

        if not selected:
            print("[aegis] WARNING: no qualifying signals found — run the "
                  "collector first (python forage/collectors/disease_outbreak_collector.py)")
            return

        # ── 3. Publish selected signals + pin to case ─────────────────────────
        for row in selected:
            sid = row["signal_id"]

            # Set published_at so publish.py can render them on the timeline
            cur.execute("""
                UPDATE signals
                SET    published_at = COALESCE(published_at, ?)
                WHERE  signal_id    = ?
                  AND  published_at IS NULL
            """, (now_iso, sid))

            # Pin to case (INSERT OR IGNORE = idempotent)
            cur.execute("""
                INSERT OR IGNORE INTO case_signals (case_id, signal_id, note, pinned_at)
                VALUES (?, ?, 'Auto-pinned by _seed_outbreak_gravity.py', ?)
            """, (case_id, sid, now_iso))

            print(f"  pinned -> [{row['gravity_score']:.3f}] {row['title'][:72]}")

        conn.commit()

        # ── 4. Verification summary ───────────────────────────────────────────
        total_pinned = cur.execute(
            "SELECT COUNT(*) FROM case_signals WHERE case_id = ?", (case_id,)
        ).fetchone()[0]

        print(f"\n[aegis] Summary")
        print(f"  case_id       : {case_id}")
        print(f"  name          : {CASE['name']}")
        print(f"  pinned signals: {total_pinned}")
        print(f"  slug (expected): regional-pathogen-surveillance")

    finally:
        conn.close()


if __name__ == "__main__":
    run()
