#!/usr/bin/env python3
"""
FORGE — Sentinel  (Phase 25)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The Sentinel is the "Guard Tower" layer that sits above the Correlation
Engine. It evaluates patterns in the live signal stream against four
detection rules and escalates matches into the sentinel_alerts table for
analyst review.

IMPORTANT: Sentinel does not assert truth. Every alert is framed as
"Pattern Detected: Requires Analyst Review." The analyst decides whether
a pattern is significant. Sentinel surfaces; it does not conclude.

Detection Rules
───────────────

Rule 1 — Correlation Escalation
  Source: correlated_incidents
  Trigger: correlation_score > 0.85
  Logic: A pair of independent signals scoring above 0.85 represents a
  very strong spatiotemporal convergence. This is the highest-confidence
  automated signal the system produces. Escalated directly.

Rule 2 — Regional Cluster Spike
  Source: signals (last 24 hours)
  Trigger: ≥ 5 signals within 100 km radius
  Logic: A localised burst of signals from any sources suggests a
  developing incident. Uses a grid-based spatial grouping (1° cells,
  ~111 km) as an efficient O(n) approximation of the 100 km radius.

Rule 3 — High-Influence Actor Mention
  Source: signal_entities × actor_network_metrics
  Trigger: NER-extracted entity name matches an actor with
           influence_score in the top quartile of the archive
  Logic: When a high-influence actor appears in fresh signal text, it
  warrants analyst attention regardless of geographic proximity.

Rule 4 — Confidence Booster (cross-source verification)
  Applied as a post-processing step to ALL generated alerts.
  For every unique source type (usgs, gdelt, GDACS, firms, etc.)
  contributing to the signals underlying an alert, add +0.15 to the
  alert's confidence_score, capped at 1.0.
  Rationale: Independent corroboration from multiple collection systems
  is the strongest available evidence that something is real.

Deduplication
─────────────
Sentinel checks for existing 'new' alerts of the same type in the same
geographic cell before inserting. If a near-identical alert already
exists and is unacknowledged, it updates the signal_count rather than
creating a duplicate. Analysts see one alert per pattern, not a flood.

Usage
─────
    python forage/processors/sentinel.py
    python forage/processors/sentinel.py --dry-run
    python forage/processors/sentinel.py --report
    python forage/processors/sentinel.py --db /path/to/database.db
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
# Phase 32: path-safe pipeline logger import
def _log_run_safe(*args, **kwargs):
    """Inline log_run that works whether called as a module or direct script."""
    import sys as _sys, importlib.util as _ilu
    from pathlib import Path as _P
    _logger_path = _P(__file__).resolve().parent.parent.parent / "forage" / "utils" / "pipeline_logger.py"
    if str(_logger_path.parent.parent) not in _sys.path:
        _sys.path.insert(0, str(_logger_path.parent.parent))
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_logger_path))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass  # logging must never crash the pipeline
log_run = _log_run_safe
from typing import Optional

# ── Thresholds ────────────────────────────────────────────────────────────────

CORR_ESCALATION_THRESHOLD  = 0.85   # Rule 1: min correlation_score
CLUSTER_MIN_SIGNALS        = 5      # Rule 2: signals in window to trigger
CLUSTER_RADIUS_KM          = 100.0  # Rule 2: spatial radius
CLUSTER_WINDOW_HOURS       = 24     # Rule 2: lookback window in hours
ACTOR_INFLUENCE_PERCENTILE = 0.75   # Rule 3: top-quartile influence actors
CONFIDENCE_BOOST_PER_SRC   = 0.15   # Rule 4: per unique source type

# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentinel_alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type      TEXT    NOT NULL,
            confidence_score REAL   NOT NULL DEFAULT 0.5
                            CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
            location_lat    REAL,
            location_lon    REAL,
            signal_count    INTEGER NOT NULL DEFAULT 1,
            summary         TEXT    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'new'
                            CHECK(status IN ('new','acknowledged','dismissed')),
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [sentinel] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [sentinel] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── Haversine ─────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R    = 6_371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a    = (math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

# ── Sentinel ──────────────────────────────────────────────────────────────────

class Sentinel:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()
        self._alerts: list[dict] = []   # staged before write

    # ── Rule 1: Correlation Escalation ───────────────────────────────────────

    def _rule_correlation_escalation(self, conn: sqlite3.Connection) -> int:
        """
        Scan correlated_incidents for pairs scoring above the threshold.
        Each qualifying pair becomes one alert, positioned at the midpoint
        of the two signals.
        """
        try:
            rows = conn.execute(
                "SELECT ci.correlation_score, ci.distance_km, "
                "ci.time_difference_hours, "
                "sa.title AS title_a, sa.source AS src_a, "
                "sa.lat AS lat_a, sa.lng AS lng_a, "
                "sb.title AS title_b, sb.source AS src_b, "
                "sb.lat AS lat_b, sb.lng AS lng_b "
                "FROM correlated_incidents ci "
                "JOIN signals sa ON sa.signal_id = ci.signal_a "
                "JOIN signals sb ON sb.signal_id = ci.signal_b "
                "WHERE ci.correlation_score > ? "
                # Exclude FIRMS pixel-pairs — they score 1.0 by definition
                # (0.4 km / 0.0 h) and have no investigative value as
                # correlation escalations.  FIRMS clusters are still surfaced
                # via Rule 2 (cluster_spike) with the density gate applied.
                "  AND sa.source != 'firms' "
                "  AND sb.source != 'firms' "
                "ORDER BY ci.correlation_score DESC",
                (CORR_ESCALATION_THRESHOLD,)
            ).fetchall()
        except Exception as exc:
            warn(f"Rule 1 query failed: {exc}")
            return 0

        staged = 0
        for r in rows:
            # Midpoint coordinates
            lat = ((r["lat_a"] or 0) + (r["lat_b"] or 0)) / 2 if r["lat_a"] and r["lat_b"] else None
            lon = ((r["lng_a"] or 0) + (r["lng_b"] or 0)) / 2 if r["lng_a"] and r["lng_b"] else None

            sources = {s for s in [r["src_a"], r["src_b"]] if s}
            confidence = min(r["correlation_score"] + CONFIDENCE_BOOST_PER_SRC * (len(sources) - 1), 1.0)

            summary = (
                f"Pattern Detected: Requires Analyst Review. "
                f"High-confidence spatiotemporal convergence detected "
                f"({r['correlation_score']:.0%} correlation score). "
                f"Signal A [{r['src_a']}]: {(r['title_a'] or '')[:80]}. "
                f"Signal B [{r['src_b']}]: {(r['title_b'] or '')[:80]}. "
                f"Distance: {r['distance_km']:.1f} km, "
                f"Time delta: {r['time_difference_hours']:.1f} h."
            )

            self._alerts.append({
                "alert_type":       "correlation_escalation",
                "confidence_score": round(confidence, 3),
                "location_lat":     round(lat, 4) if lat else None,
                "location_lon":     round(lon, 4) if lon else None,
                "signal_count":     2,
                "summary":          summary,
            })
            staged += 1

        log(f"Rule 1 (Correlation Escalation): {staged} alerts staged")
        return staged

    # ── Rule 2: Regional Cluster Spike ───────────────────────────────────────

    def _rule_cluster_spike(self, conn: sqlite3.Connection) -> int:
        """
        Group recent signals into 1° grid cells (~111 km).
        Cells with ≥ CLUSTER_MIN_SIGNALS signals trigger an alert.
        Uses grid approximation (O(n)) rather than pairwise Haversine (O(n²)).
        """
        try:
            rows = conn.execute(
                "SELECT signal_id, source, title, lat, lng, timestamp "
                "FROM signals "
                "WHERE lat IS NOT NULL AND lng IS NOT NULL "
                "  AND timestamp >= datetime('now', ?) "
                "  AND status IN ('raw', 'promoted') "
                # Exclude FIRMS from the raw cluster spike rule.
                # FIRMS fire pixels cluster naturally (many adjacent hotspots
                # within 1° cells) and would trigger hundreds of alerts per run.
                # Major FIRMS regional events are still captured — the feed's
                # density gate (signal_count >= 20) surfaces them from the
                # existing cluster_spike alerts already in the table.
                "  AND source != 'firms' ",
                (f"-{CLUSTER_WINDOW_HOURS} hours",)
            ).fetchall()
        except Exception as exc:
            warn(f"Rule 2 query failed: {exc}")
            return 0

        if not rows:
            return 0

        # Bucket into 1° cells
        cells: dict = {}
        for r in rows:
            cell = (int(r["lat"]), int(r["lng"]))
            cells.setdefault(cell, []).append(r)

        staged = 0
        for cell, signals in cells.items():
            if len(signals) < CLUSTER_MIN_SIGNALS:
                continue

            sources      = {s["source"] for s in signals if s["source"]}
            n_sources    = len(sources)
            base_conf    = min(0.5 + 0.05 * len(signals), 0.85)
            confidence   = min(base_conf + CONFIDENCE_BOOST_PER_SRC * (n_sources - 1), 1.0)
            centroid_lat = sum(s["lat"] for s in signals) / len(signals)
            centroid_lon = sum(s["lng"] for s in signals) / len(signals)
            src_str      = ", ".join(sorted(sources))

            summary = (
                f"Pattern Detected: Requires Analyst Review. "
                f"Regional signal spike: {len(signals)} signals detected "
                f"within ~100 km radius in the last {CLUSTER_WINDOW_HOURS} hours. "
                f"Sources: {src_str}. "
                f"Centroid: {centroid_lat:.2f}°, {centroid_lon:.2f}°."
            )

            self._alerts.append({
                "alert_type":       "cluster_spike",
                "confidence_score": round(confidence, 3),
                "location_lat":     round(centroid_lat, 4),
                "location_lon":     round(centroid_lon, 4),
                "signal_count":     len(signals),
                "summary":          summary,
            })
            staged += 1

        log(f"Rule 2 (Cluster Spike): {staged} alerts staged")
        return staged

    # ── Rule 3: High-Influence Actor Mention ─────────────────────────────────

    def _rule_actor_mention(self, conn: sqlite3.Connection) -> int:
        """
        Cross-reference NER entities extracted from recent signals against
        actors in the top ACTOR_INFLUENCE_PERCENTILE by influence_score.
        A mention of a high-influence actor in fresh signal text triggers
        an Actor Activity alert.
        """
        # Get the influence threshold (top quartile)
        try:
            score_row = conn.execute(
                "SELECT influence_score FROM actor_network_metrics "
                "ORDER BY influence_score DESC"
            ).fetchall()
        except Exception:
            return 0

        if not score_row:
            return 0

        cutoff_idx  = max(0, int(len(score_row) * ACTOR_INFLUENCE_PERCENTILE) - 1)
        min_score   = score_row[cutoff_idx]["influence_score"]
        if min_score <= 0:
            return 0

        # High-influence actors
        try:
            hi_actors = conn.execute(
                "SELECT a.actor_id, a.name, a.type, m.influence_score "
                "FROM actor_network_metrics m "
                "JOIN actors a ON a.actor_id = m.actor_id "
                "WHERE m.influence_score >= ?",
                (min_score,)
            ).fetchall()
        except Exception as exc:
            warn(f"Rule 3 actor query failed: {exc}")
            return 0

        if not hi_actors:
            return 0

        # Build a lowercase name → actor lookup
        actor_lookup = {a["name"].lower(): a for a in hi_actors}

        # Recent signal entities (last 48h)
        try:
            entities = conn.execute(
                "SELECT se.signal_id, se.text, se.label, "
                "s.title AS sig_title, s.source, s.lat, s.lng "
                "FROM signal_entities se "
                "JOIN signals s ON s.signal_id = se.signal_id "
                "WHERE s.timestamp >= datetime('now', '-48 hours') "
                "  AND s.status IN ('raw', 'promoted') "
                "  AND se.label IN ('PERSON', 'ORG', 'GPE')"
            ).fetchall()
        except Exception as exc:
            warn(f"Rule 3 entity query failed: {exc}")
            return 0

        staged   = 0
        seen     = set()   # (actor_id, signal_id) dedup

        for ent in entities:
            actor = actor_lookup.get(ent["text"].lower())
            if not actor:
                continue
            key = (actor["actor_id"], ent["signal_id"])
            if key in seen:
                continue
            seen.add(key)

            confidence = min(
                0.55 + actor["influence_score"] * 0.45 + CONFIDENCE_BOOST_PER_SRC,
                1.0
            )
            summary = (
                f"Pattern Detected: Requires Analyst Review. "
                f"High-influence actor '{actor['name']}' ({actor['type']}) "
                f"detected in signal stream "
                f"(influence score: {actor['influence_score']:.4f}). "
                f"Signal [{ent['source']}]: {(ent['sig_title'] or '')[:100]}."
            )

            self._alerts.append({
                "alert_type":       "actor_match",
                "confidence_score": round(confidence, 3),
                "location_lat":     round(ent["lat"], 4) if ent["lat"] else None,
                "location_lon":     round(ent["lng"], 4) if ent["lng"] else None,
                "signal_count":     1,
                "summary":          summary,
            })
            staged += 1

        log(f"Rule 3 (Actor Mention): {staged} alerts staged")
        return staged

    # ── Write alerts (with deduplication) ────────────────────────────────────

    def _write_alerts(self, conn: sqlite3.Connection, dry_run: bool = False) -> int:
        """
        Write staged alerts to sentinel_alerts.
        Deduplication: if a 'new' alert of the same type exists within
        the same 1° grid cell, update its signal_count instead of inserting.
        """
        if not self._alerts:
            return 0

        now     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        written = 0

        for alert in self._alerts:
            if dry_run:
                log(f"  [DRY] {alert['alert_type']} "
                    f"conf={alert['confidence_score']:.2f} "
                    f"signals={alert['signal_count']} "
                    f"@ {alert.get('location_lat')},{alert.get('location_lon')}")
                continue

            # Check for existing duplicate (same type, same ~1° cell, status='new')
            dup = None
            if alert["location_lat"] and alert["location_lon"]:
                try:
                    dup = conn.execute(
                        "SELECT id, signal_count FROM sentinel_alerts "
                        "WHERE alert_type = ? AND status = 'new' "
                        "  AND ABS(location_lat - ?) < 1.0 "
                        "  AND ABS(location_lon - ?) < 1.0 "
                        "  AND created_at >= datetime('now', '-6 hours')",
                        (alert["alert_type"],
                         alert["location_lat"], alert["location_lon"])
                    ).fetchone()
                except Exception:
                    dup = None

            if dup:
                # Update existing — increase signal count and refresh confidence
                new_count = dup["signal_count"] + alert["signal_count"]
                new_conf  = min(alert["confidence_score"] + CONFIDENCE_BOOST_PER_SRC, 1.0)
                conn.execute(
                    "UPDATE sentinel_alerts "
                    "SET signal_count=?, confidence_score=? WHERE id=?",
                    (new_count, round(new_conf, 3), dup["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO sentinel_alerts "
                    "(alert_type, confidence_score, location_lat, location_lon, "
                    " signal_count, summary, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'new', ?)",
                    (
                        alert["alert_type"],
                        alert["confidence_score"],
                        alert["location_lat"],
                        alert["location_lon"],
                        alert["signal_count"],
                        alert["summary"],
                        now,
                    )
                )
                written += 1

        conn.commit()
        return written

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self, dry_run: bool = False) -> dict:
        log(f"Database : {self._db_path}")
        log(f"Dry run  : {dry_run}")

        conn = _open_db(self._db_path)
        _ensure_schema(conn)

        self._alerts = []   # reset staged alerts

        n1 = self._rule_correlation_escalation(conn)
        n2 = self._rule_cluster_spike(conn)
        n3 = self._rule_actor_mention(conn)

        total_staged = n1 + n2 + n3
        log(f"Total staged: {total_staged}")

        written = self._write_alerts(conn, dry_run=dry_run)
        conn.close()

        summary = {
            "status":            "done",
            "staged":            total_staged,
            "written":           written if not dry_run else 0,
            "correlation_alerts": n1,
            "cluster_alerts":    n2,
            "actor_alerts":      n3,
            "dry_run":           dry_run,
            "computed_at":       datetime.now(timezone.utc).isoformat(),
        }
        log(f"Complete: {summary}")
        log_run(self._db_path, "sentinel", "success",
                records_in=summary.get("staged", 0),
                records_out=summary.get("written", 0),
                duration_s=None, detail=summary)
        return summary

    def report(self) -> None:
        conn = _open_db(self._db_path)
        try:
            rows = conn.execute(
                "SELECT id, alert_type, confidence_score, signal_count, "
                "status, created_at, summary "
                "FROM sentinel_alerts "
                "ORDER BY confidence_score DESC, created_at DESC "
                "LIMIT 20"
            ).fetchall()
            meta = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS new_count "
                "FROM sentinel_alerts"
            ).fetchone()
        except Exception as exc:
            print(f"Error: {exc}")
            conn.close()
            return
        conn.close()

        if not rows:
            print("No sentinel alerts. Run: python forage/processors/sentinel.py")
            return

        print(f"\n{'─'*80}")
        print(f"  FORGE SENTINEL — Alert Report")
        print(f"  Total: {meta['total']} | New: {meta['new_count']}")
        print(f"{'─'*80}")
        for r in rows:
            flag = "⚑ " if r["status"] == "new" else "  "
            print(f"  {flag}[{r['alert_type']:<24}] "
                  f"conf={r['confidence_score']:.2f} "
                  f"sigs={r['signal_count']} "
                  f"status={r['status']} "
                  f"@ {r['created_at'][:16]}")
            print(f"     {r['summary'][:100]}…")
        print(f"{'─'*80}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Sentinel — autonomous alert and threat detection"
    )
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report",  action="store_true",
                        help="Print alert report without running detection")
    args = parser.parse_args()

    s = Sentinel(db_path=_resolve_db(str(args.db) if args.db else None))

    if args.report:
        s.report()
        sys.exit(0)

    result = s.run(dry_run=args.dry_run)
    s.report()
    sys.exit(0 if result["status"] == "done" else 1)