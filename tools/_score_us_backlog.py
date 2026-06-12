from __future__ import annotations
"""
Lightweight gravity-only scoring pass for the civic_intel_collector_us.py
backlog. Skips the full ingest_signal() pipeline (entity materialisation,
relationship extraction, escalation, case evaluation) which was found to
hang indefinitely past the apply_feedback step on this batch — see
TD-21 in docs/tech_debt.md. Writes gravity_score + conclave_meta only.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forage.processors.signal_interpreter import SignalInterpreter
from forage.engines.gravity_engine import score_signal

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"
SOURCES = ('propublica', 'icij', 'theintercept', 'revealnews', 'occrp_us', 'us_infrastructure')


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(SOURCES))
        rows = conn.execute(
            f"SELECT * FROM signals WHERE source IN ({placeholders}) AND status='raw'",
            SOURCES,
        ).fetchall()
        print(f"to score: {len(rows)}", flush=True)

        interpreter = SignalInterpreter()
        gravity_hist: dict[float, int] = {}
        floor_fires = 0

        for r in rows:
            sig = dict(r)
            interpreted = interpreter.interpret(sig)
            gravity_signal = score_signal({**sig, **interpreted}, actors=[])
            g = gravity_signal.get("gravity_score", 0.0)
            path = gravity_signal.get("_gravity_path", "")
            if path == "standard_investigative_floor":
                floor_fires += 1

            bucket = round(g, 1)
            gravity_hist[bucket] = gravity_hist.get(bucket, 0) + 1

            meta = {"gravity_path": path}
            if interpreted.get("investigative_tier"):
                meta["investigative_uplift"] = interpreted.get("investigative_uplift", 0.0)
                meta["investigative_tier"] = interpreted.get("investigative_tier", "")

            conn.execute(
                "UPDATE signals SET gravity_score=?, processed_at=?, conclave_meta=?, status='reviewed' "
                "WHERE signal_id=?",
                (
                    g,
                    datetime.now(timezone.utc).isoformat() + "Z",
                    json.dumps(meta),
                    sig["signal_id"],
                ),
            )

        conn.commit()
        print(f"done. floor_fires={floor_fires}")
        print("gravity histogram:", sorted(gravity_hist.items()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
