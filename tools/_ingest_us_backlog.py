from __future__ import annotations
"""
One-shot ingest of the civic_intel_collector_us.py backlog with a
per-signal timeout. Signals that exceed the timeout (likely stuck on a
network call inside relationship_extractor / EntityResolver) are skipped
and flagged status='timeout_skip' for follow-up rather than blocking the
whole batch.
"""
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline.ingest import ingest_and_persist

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"
SOURCES = ('propublica', 'icij', 'theintercept', 'revealnews', 'occrp_us', 'us_infrastructure')
PER_SIGNAL_TIMEOUT = 45  # seconds


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(SOURCES))
        rows = conn.execute(
            f"SELECT * FROM signals WHERE source IN ({placeholders}) AND status='raw'",
            SOURCES,
        ).fetchall()
        print(f"to process: {len(rows)}", flush=True)

        ok = 0
        err = 0
        timed_out = 0
        gravity_hist: dict[float, int] = {}

        with ThreadPoolExecutor(max_workers=1) as pool:
            for i, r in enumerate(rows, 1):
                sig = dict(r)
                future = pool.submit(ingest_and_persist, sig)
                try:
                    result = future.result(timeout=PER_SIGNAL_TIMEOUT)
                    g = round(result["gravity_signal"].get("gravity_score", 0.0), 1)
                    gravity_hist[g] = gravity_hist.get(g, 0) + 1
                    conn.execute(
                        "UPDATE signals SET status='processed' WHERE signal_id=?",
                        (sig["signal_id"],),
                    )
                    conn.commit()
                    ok += 1
                except FuturesTimeout:
                    timed_out += 1
                    conn.execute(
                        "UPDATE signals SET status='timeout_skip' WHERE signal_id=?",
                        (sig["signal_id"],),
                    )
                    conn.commit()
                    print(f"  [timeout] {sig['signal_id']} {sig['title'][:60]!r}", flush=True)
                except Exception as e:
                    err += 1
                    print(f"  [error] {sig['signal_id']}: {e}", flush=True)

                if i % 25 == 0:
                    print(f"  ... {i}/{len(rows)} (ok={ok} err={err} timeout={timed_out})", flush=True)

        print(f"done. ok={ok} err={err} timeout={timed_out}")
        print("gravity histogram:", sorted(gravity_hist.items()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
