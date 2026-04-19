"""
entropy_engine.py — Phase 67: Gravity Decay (Entropy)
======================================================
Applies time-based LINEAR gravity decay to uncorroborated signals.

DISTINCTION FROM decay_engine.py
  decay_engine.py  — exponential decay on relevance_score (content freshness).
  entropy_engine.py — linear decay on gravity_score (investigative significance).

A signal's investigative gravity should fade if nothing new has been learned
about it. Signals that gain new relationship edges or remain in actively-updated
clusters stay corroborated. Everything else loses 0.05 gravity per 30-day epoch
it ages unchallenged, down to a floor of 0.10.

CORROBORATION DEFINITION
  A signal is considered corroborated (exempt from decay) if ANY of:
    1. Its source_artifact_id links to an entity_relationship created within
       the last 30 days.
    2. Another signal in the same cluster_id was ingested within 30 days
       (the cluster is still active).
    3. It is itself less than 30 days old (too new to decay).

DECAY FORMULA
  periods_elapsed  = floor(age_days / 30)   -- full 30-day epochs only
  decay_amount     = 0.05 * periods_elapsed
  new_gravity      = max(current_gravity - decay_amount, FLOOR)

USAGE
  python scripts/entropy_engine.py --dry-run
  python scripts/entropy_engine.py --dry-run --verbose
  python scripts/entropy_engine.py               # live run
  python scripts/entropy_engine.py --period 14 --decay 0.03 --floor 0.05
"""

import sys
import math
import argparse
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from core.db.connection import get_connection  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS (overridable via CLI)
# ─────────────────────────────────────────────────────────────────────────────
DECAY_PERIOD_DAYS = 30       # one epoch = 30 days
DECAY_PER_PERIOD  = 0.05     # gravity reduction per full epoch
GRAVITY_FLOOR     = 0.10     # minimum gravity (signals never fully decay)
BATCH_SIZE        = 500      # rows per commit


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[{_ts()}] [entropy_engine] {msg}", flush=True)


def _parse_ts(raw: str) -> datetime:
    """Parse signal timestamp into UTC-aware datetime."""
    if not raw:
        raise ValueError("empty timestamp")
    raw = raw.strip()
    if "T" in raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# CORROBORATION CHECK
# ─────────────────────────────────────────────────────────────────────────────

def build_corroboration_sets(conn, cutoff: datetime) -> tuple[set, set]:
    """
    Returns two sets of signal_ids considered corroborated:
      1. active_cluster_signals — signals whose cluster has a recent member
      2. fresh_er_signals       — signals whose source_artifact links to a recent ER

    Callers combine these to determine full corroboration.
    """
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # Set 1: clusters that contain at least one signal newer than cutoff
    #         → all signals in those clusters are corroborated
    active_clusters = {
        row["cluster_id"]
        for row in conn.execute(
            "SELECT DISTINCT cluster_id FROM signals "
            "WHERE cluster_id IS NOT NULL AND timestamp >= ?",
            (cutoff_str,),
        ).fetchall()
        if row["cluster_id"]
    }
    active_cluster_signals: set[str] = set()
    if active_clusters:
        placeholders = ",".join("?" * len(active_clusters))
        active_cluster_signals = {
            row["signal_id"]
            for row in conn.execute(
                f"SELECT signal_id FROM signals WHERE cluster_id IN ({placeholders})",
                list(active_clusters),
            ).fetchall()
        }

    # Set 2: signals whose source_artifact_id appears in a recent entity_relationship
    fresh_er_artifact_ids = {
        row["source_artifact_id"]
        for row in conn.execute(
            "SELECT DISTINCT source_artifact_id FROM entity_relationships "
            "WHERE source_artifact_id IS NOT NULL AND created_at >= ?",
            (cutoff_str,),
        ).fetchall()
        if row["source_artifact_id"]
    }
    fresh_er_signals: set[str] = set()
    if fresh_er_artifact_ids:
        placeholders = ",".join("?" * len(fresh_er_artifact_ids))
        fresh_er_signals = {
            row["signal_id"]
            for row in conn.execute(
                f"SELECT signal_id FROM signals WHERE source_artifact_id IN ({placeholders})",
                list(fresh_er_artifact_ids),
            ).fetchall()
        }

    return active_cluster_signals, fresh_er_signals


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def run(
    dry_run: bool = True,
    verbose: bool = False,
    decay_period_days: int = DECAY_PERIOD_DAYS,
    decay_per_period: float = DECAY_PER_PERIOD,
    gravity_floor: float = GRAVITY_FLOOR,
) -> dict:
    """
    Apply gravity decay to uncorroborated, ageing signals.

    Returns a summary dict.
    """
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON;")

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(days=decay_period_days)

    _log(f"Starting entropy pass  (dry_run={dry_run})")
    _log(f"  Decay per period : -{decay_per_period} per {decay_period_days}d epoch")
    _log(f"  Gravity floor    : {gravity_floor}")
    _log(f"  Corroboration cutoff : {cutoff.strftime('%Y-%m-%d')}")

    # Build corroboration exemption sets
    _log("Building corroboration sets...")
    active_cluster_sigs, fresh_er_sigs = build_corroboration_sets(conn, cutoff)
    corroborated = active_cluster_sigs | fresh_er_sigs
    _log(f"  Corroborated via active cluster : {len(active_cluster_sigs):,}")
    _log(f"  Corroborated via fresh ER link  : {len(fresh_er_sigs):,}")
    _log(f"  Total corroborated (exempt)     : {len(corroborated):,}")

    # Load signals eligible for decay:
    # - gravity_score above floor (otherwise already at minimum)
    # - older than one full decay period
    # - not dismissed
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT signal_id, gravity_score, timestamp, cluster_id, title
           FROM signals
           WHERE gravity_score > ?
             AND timestamp < ?
             AND status != 'dismissed'""",
        (gravity_floor, cutoff_str),
    ).fetchall()

    _log(f"Candidates (gravity>{gravity_floor}, age>{decay_period_days}d): {len(rows):,}")

    # Categorise
    stats = {
        "total_candidates":   len(rows),
        "corroborated_skip":  0,
        "already_at_floor":   0,
        "decayed":            0,
        "total_gravity_lost": 0.0,
        "by_epoch": {},          # {epoch_count: signal_count}
    }

    updates: list[tuple[float, str]] = []
    verbose_lines: list[str] = []

    for row in rows:
        sig_id  = row["signal_id"]
        cur_g   = row["gravity_score"]
        raw_ts  = row["timestamp"] or ""

        # Skip corroborated signals
        if sig_id in corroborated:
            stats["corroborated_skip"] += 1
            continue

        # Parse age
        try:
            ts = _parse_ts(raw_ts)
        except Exception:
            continue

        age_days = (now_utc - ts).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0.0

        # Full 30-day epochs only
        epochs = int(age_days // decay_period_days)
        if epochs == 0:
            continue

        decay_amount = decay_per_period * epochs
        new_g        = round(max(cur_g - decay_amount, gravity_floor), 4)

        if new_g >= cur_g:
            # Already at floor, no change
            stats["already_at_floor"] += 1
            continue

        delta = cur_g - new_g
        stats["decayed"] += 1
        stats["total_gravity_lost"] = round(stats["total_gravity_lost"] + delta, 4)
        stats["by_epoch"][epochs]   = stats["by_epoch"].get(epochs, 0) + 1

        if verbose:
            title = (row["title"] or "")[:45]
            verbose_lines.append(
                f"  [{sig_id[:8]}] g={cur_g:.4f} -> {new_g:.4f}  "
                f"age={int(age_days)}d  epochs={epochs}  \"{title}\""
            )

        if not dry_run:
            updates.append((new_g, sig_id))
            if len(updates) >= BATCH_SIZE:
                conn.executemany(
                    "UPDATE signals SET gravity_score = ? WHERE signal_id = ?", updates
                )
                conn.commit()
                updates = []

    # Flush remaining
    if updates and not dry_run:
        conn.executemany(
            "UPDATE signals SET gravity_score = ? WHERE signal_id = ?", updates
        )
        conn.commit()

    conn.close()

    stats["dry_run"] = dry_run
    stats["run_at"]  = now_utc.isoformat()

    return stats, verbose_lines


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def print_report(stats: dict, verbose_lines: list[str], verbose: bool) -> None:
    LINE = "-" * 72

    print()
    print("=" * 72)
    mode = "DRY-RUN" if stats["dry_run"] else "LIVE RUN"
    print(f"  ENTROPY ENGINE REPORT [{mode}]")
    print("=" * 72)
    print(f"  Candidates (age>30d, gravity>floor) : {stats['total_candidates']:>8,}")
    print(f"  Corroborated (exempt from decay)    : {stats['corroborated_skip']:>8,}")
    print(f"  Already at floor (0.10)             : {stats['already_at_floor']:>8,}")
    print(f"  Signals that WOULD be decayed       : {stats['decayed']:>8,}")
    print(f"  Total gravity points removed        : {stats['total_gravity_lost']:>8.4f}")
    print(LINE)

    if stats["by_epoch"]:
        print("  DECAY DISTRIBUTION BY AGE EPOCH:")
        for epoch in sorted(stats["by_epoch"]):
            age_range = f"{epoch * 30}–{(epoch + 1) * 30}d"
            cnt = stats["by_epoch"][epoch]
            bar = "#" * min(cnt // 5 + 1, 40)
            print(f"  {age_range:>12}  ({epoch} epoch{'s' if epoch > 1 else ''})  "
                  f"{cnt:>6} signals  {bar}")
        print(LINE)

    if verbose and verbose_lines:
        print(f"  VERBOSE SIGNAL LIST ({len(verbose_lines)} entries):")
        print(LINE)
        for line in verbose_lines[:100]:
            print(line)
        if len(verbose_lines) > 100:
            print(f"  ... and {len(verbose_lines) - 100} more")
        print(LINE)

    action = "will be written" if stats["dry_run"] else "written"
    print(f"  {stats['decayed']} gravity updates {action}.")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="FORGE Phase 67 — Entropy Engine: time-based gravity decay.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python scripts/entropy_engine.py --dry-run
              python scripts/entropy_engine.py --dry-run --verbose
              python scripts/entropy_engine.py
              python scripts/entropy_engine.py --period 14 --decay 0.03
        """),
    )
    ap.add_argument("--dry-run",  action="store_true", default=False,
                    help="Simulate decay without writing to DB (default: False)")
    ap.add_argument("--verbose",  action="store_true",
                    help="Print per-signal decay details")
    ap.add_argument("--period",   type=int,   default=DECAY_PERIOD_DAYS,
                    help=f"Epoch length in days (default {DECAY_PERIOD_DAYS})")
    ap.add_argument("--decay",    type=float, default=DECAY_PER_PERIOD,
                    help=f"Gravity reduction per epoch (default {DECAY_PER_PERIOD})")
    ap.add_argument("--floor",    type=float, default=GRAVITY_FLOOR,
                    help=f"Minimum gravity floor (default {GRAVITY_FLOOR})")

    args = ap.parse_args()

    stats, verbose_lines = run(
        dry_run            = args.dry_run,
        verbose            = args.verbose,
        decay_period_days  = args.period,
        decay_per_period   = args.decay,
        gravity_floor      = args.floor,
    )
    print_report(stats, verbose_lines, args.verbose)


if __name__ == "__main__":
    main()
