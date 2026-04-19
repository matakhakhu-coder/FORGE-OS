"""
FORGE — Cluster Engine  (forage/engines/cluster_engine.py)
══════════════════════════════════════════════════════════

Groups raw signals into cohesive event clusters so downstream engines
(Sentinel, CorrelationEngine, feed surface) work from grouped events
rather than individual noisy data points.

Clustering strategy (applied in priority order per signal)
──────────────────────────────────────────────────────────
  1. Geographic  — signals with lat/lng are binned into a spatial grid.
     Bin size depends on source class:
       WIDE sources  (firms, usgs, GDACS): 1.0° grid  ≈ 111 km
       TIGHT sources (all others):         0.5° grid  ≈  55 km
     Time bucket: epoch_day // 2  (48-hour windows, avoids midnight splits)
     cluster_id:  geo_{lat_bin}_{lng_bin}_{day_bucket}

  2. Actor-linked — signals with known actors (via signal_actors) that
     have no coordinates are grouped by frozenset(actor_ids).
     cluster_id:  act_{sorted_actor_ids_hex}

  3. Source-month fallback — ungeolocated signals with no actors.
     cluster_id:  src_{source}_{yyyymm}

Design decisions
────────────────
  • Idempotent: only signals with cluster_id IS NULL are processed.
    Re-running never reclassifies already-clustered signals.
  • Bulk update: all writes in a single transaction per batch for speed.
  • No pairwise comparison: pure grid-cell bucketing is O(n), safe at 150K.
  • FIRMS wildfire pixels get 1° wide cells — a regional fire event
    typically spans multiple pixels inside a 111 km radius.

Compatible with mega_ingest.py _run_engine() — exposes:
    ClusterEngine(db_path=Path).run() -> dict
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── Constants ─────────────────────────────────────────────────────────────────

# Sources where signals represent area-wide sensor readings — use a wide grid
_WIDE_SOURCES: frozenset = frozenset({"firms", "usgs", "GDACS", "earthquake"})
_GRID_WIDE  = 1.0   # degrees (~111 km)
_GRID_TIGHT = 0.5   # degrees (~55 km)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _geo_bin(value: float, step: float) -> float:
    """Snap a coordinate to the nearest grid boundary."""
    return math.floor(value / step) * step


def _day_bucket(timestamp_str: Optional[str]) -> int:
    """
    Convert an ISO timestamp string to a 48-hour epoch bucket.
    Signals within the same 48-hour window share the same bucket,
    avoiding hard midnight splits on rolling events.
    Returns 0 on parse failure.
    """
    if not timestamp_str:
        return 0
    try:
        ts = timestamp_str[:10]              # 'YYYY-MM-DD'
        dt = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        epoch_days = int(dt.timestamp() // 86400)
        return epoch_days // 2              # 48-h bucket
    except Exception:
        return 0


def _actor_key(actor_ids: Set[int]) -> str:
    """
    Stable hash of a frozenset of actor_ids used as the actor cluster key.
    Uses first 12 hex chars of SHA-1 of the sorted id string — short enough
    to be readable, collision-resistant enough for <1M signals.
    """
    joined = ",".join(str(i) for i in sorted(actor_ids))
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def _month_bucket(timestamp_str: Optional[str]) -> str:
    """Return 'YYYYMM' from a timestamp string, '000000' on failure."""
    if not timestamp_str:
        return "000000"
    try:
        return timestamp_str[:7].replace("-", "")   # 'YYYY-MM' -> 'YYYYMM'
    except Exception:
        return "000000"


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        print(f"[{ts}] [cluster_engine] {msg}", flush=True)
    except UnicodeEncodeError:
        safe = msg.encode("utf-8", errors="replace").decode("ascii", errors="replace")
        print(f"[{ts}] [cluster_engine] {safe}", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# Main engine class
# ══════════════════════════════════════════════════════════════════════════════

class ClusterEngine:
    """
    Assigns cluster_id to all unclustered signals.

    Usage (via mega_ingest _run_engine):
        ClusterEngine(db_path=Path('database.db')).run()

    Usage standalone:
        python -m forage.engines.cluster_engine
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    # ── Public interface ──────────────────────────────────────────────────────

    def run(self, batch_size: int = 25_000, only_null: bool = True) -> dict:
        """
        Cluster all unclustered signals and return a summary dict.

        Parameters
        ──────────
        batch_size : int
            Signals loaded per SQLite fetch (controls peak RAM).
        only_null : bool
            If True (default), only process signals where cluster_id IS NULL.
            If False, recluster ALL signals (use with caution on large DBs).
        """
        conn = sqlite3.connect(str(self.db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        try:
            return self._cluster(conn, batch_size, only_null)
        finally:
            conn.close()

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _cluster(self, conn: sqlite3.Connection,
                 batch_size: int, only_null: bool) -> dict:

        null_clause = "WHERE cluster_id IS NULL" if only_null else ""

        total_signals = conn.execute(
            f"SELECT COUNT(*) FROM signals {null_clause}"
        ).fetchone()[0]

        _log(f"Signals to cluster: {total_signals:,} "
             f"({'NULL only' if only_null else 'ALL'})")

        if total_signals == 0:
            return {"status": "ok", "clustered": 0, "clusters_created": 0,
                    "message": "Nothing to cluster."}

        # ── Build actor lookup once — signal_id → frozenset(actor_ids) ────────
        _log("Loading actor linkage map...")
        actor_map: Dict[str, Set[int]] = {}
        for row in conn.execute(
            "SELECT signal_id, actor_id FROM signal_actors"
        ).fetchall():
            actor_map.setdefault(row["signal_id"], set()).add(row["actor_id"])

        _log(f"Actor map: {len(actor_map):,} signals with actors")

        # ── Process in time-ordered batches ───────────────────────────────────
        # NOTE: offset is always 0 — each commit removes rows from the NULL
        # set, so the next SELECT naturally sees the next unclustered batch.
        # Using a non-zero offset would skip rows as the result set shrinks.
        total_clustered = 0
        all_keys: set   = set()

        geo_count   = 0
        actor_count = 0
        src_count   = 0

        while True:
            rows = conn.execute(
                f"""SELECT signal_id, source, lat, lng, timestamp
                    FROM signals {null_clause}
                    ORDER BY timestamp ASC
                    LIMIT ?""",
                (batch_size,),
            ).fetchall()

            if not rows:
                break

            updates: List[Tuple[str, str]] = []   # (cluster_id, signal_id)

            for row in rows:
                sig_id    = row["signal_id"]
                source    = (row["source"] or "").lower()
                lat       = row["lat"]
                lng       = row["lng"]
                ts        = row["timestamp"]

                # Strategy 1 — Geographic
                if lat is not None and lng is not None:
                    try:
                        flat, flng = float(lat), float(lng)
                        grid = (_GRID_WIDE
                                if source in _WIDE_SOURCES
                                else _GRID_TIGHT)
                        lb   = round(_geo_bin(flat,  grid), 2)
                        lgb  = round(_geo_bin(flng,  grid), 2)
                        day  = _day_bucket(ts)
                        key  = f"geo_{lb}_{lgb}_{day}"
                        geo_count += 1
                    except (TypeError, ValueError):
                        key = self._fallback_key(source, ts)
                        src_count += 1

                # Strategy 2 — Actor-linked (no coords)
                elif sig_id in actor_map and actor_map[sig_id]:
                    key = f"act_{_actor_key(actor_map[sig_id])}"
                    actor_count += 1

                # Strategy 3 — Source + month fallback
                else:
                    key = self._fallback_key(source, ts)
                    src_count += 1

                all_keys.add(key)
                updates.append((key, sig_id))

            # Bulk write this batch
            conn.execute("BEGIN")
            conn.executemany(
                "UPDATE signals SET cluster_id=? WHERE signal_id=?",
                updates,
            )
            conn.execute("COMMIT")

            total_clustered += len(updates)

            _log(f"  Batch complete: {total_clustered:,}/{total_signals:,} "
                 f"clustered ({len(all_keys):,} distinct clusters)")

        summary = {
            "status":           "ok",
            "clustered":        total_clustered,
            "clusters_created": len(all_keys),
            "by_strategy": {
                "geographic":  geo_count,
                "actor_linked": actor_count,
                "source_month": src_count,
            },
        }
        _log(f"Done: {summary}")
        return summary

    @staticmethod
    def _fallback_key(source: str, timestamp: Optional[str]) -> str:
        """Source + month bucket for signals with no location and no actors."""
        src = re.sub(r"[^a-z0-9]", "", (source or "unknown").lower())[:20]
        mon = _month_bucket(timestamp)
        return f"src_{src}_{mon}"


# ── Allow `import re` at the top-level for _fallback_key ─────────────────────
import re   # noqa: E402  (placed after class to keep docstring at top)


# ── Standalone runner ─────────────────────────────────────────────────────────

def _resolve_db() -> Path:
    env = __import__("os").environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FORGE Cluster Engine")
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--all",     action="store_true",
                        help="Recluster ALL signals, not just NULL ones")
    parser.add_argument("--batch",   type=int,  default=25_000)
    args = parser.parse_args()

    db_path = args.db.resolve() if args.db else _resolve_db()
    result  = ClusterEngine(db_path=db_path).run(
        batch_size=args.batch,
        only_null=not args.all,
    )
    import json
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "ok" else 1)
