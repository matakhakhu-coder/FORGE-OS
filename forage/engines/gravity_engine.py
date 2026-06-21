"""
FORGE — Gravity Engine  (forage/engines/gravity_engine.py)
══════════════════════════════════════════════════════════

Computes a gravity_score (0.0–1.0) representing the urgency and
analytical importance of a signal. Used by:

  - core/pipeline/ingest.py        score_signal() call
  - forage/engines/case_engine.py  escalation threshold checks
  - app.py                         /actors query: is_targeted threshold (0.55)
                                   /feed: feed_score formula
                                   /api/surface/top: influence ranking

Score architecture
──────────────────
Two scoring paths share the same output contract:

  PATH A — ACLED (structured conflict data)
  ┌──────────────────────────────────────────────────────────────┐
  │  Metadata fields available: fatalities, event_type, actors   │
  │  These are pre-computed by acled_collector._build_signal()   │
  │  and stored in metadata_json.                                 │
  │                                                               │
  │  base = fatality_severity (0.4 * base_event + 0.6 * fat/50) │
  │  actor_importance from inter1/inter2 type weights            │
  │  sentiment from event_type map (-1.0 to -0.3)               │
  │  source_credibility = 0.85 (ACLED is peer-reviewed)         │
  │  frequency from existing signal history (DB lookup)          │
  │                                                               │
  │  Momentum multiplier raised to 0.9 + 0.1*frequency          │
  │  (ACLED data is high-confidence — floor is higher)           │
  └──────────────────────────────────────────────────────────────┘

  PATH B — Standard (RSS, GDELT DOC, USGS, FIRMS, civic intel)
  ┌──────────────────────────────────────────────────────────────┐
  │  Five-factor model unchanged from original design:           │
  │  severity, actor_importance, frequency, sentiment,           │
  │  source_credibility                                          │
  │                                                               │
  │  Weights: 0.35 / 0.25 / 0.15 / 0.15 / 0.10                 │
  │  Momentum: 0.8 + 0.2 * frequency                            │
  └──────────────────────────────────────────────────────────────┘

Actor feedback
──────────────
actor_influence() from feedback_engine applies a multiplier from
tracked actor credibility/relevance weights. Applied identically
to both paths post-scoring. Output clamped to [0.0, 1.0].

UI contract (from app.py)
──────────────────────────
  /actors query:    is_targeted = 1 when gravity_score >= 0.55
  /feed formula:    feed_score = rel*0.40 + prio*0.30 + sentinel*0.20 + sw*0.10
                    gravity_score is stored as relevance_score proxy for feed
  /api/surface/top: orders by influence_score from actor_network_metrics
                    (not directly gravity_score — no conflict)

  Urgency thresholds from actor_detail route:
    >= 0.75 or is_priority=1  → threat_level = "critical"
    >= 0.55                   → threat_level = "elevated"
    >= 0.35                   → threat_level = "monitored"
    < 0.35                    → threat_level = "none"

  is_targeted threshold: 0.55 (app.py /actors query)
  These thresholds are read-only from this engine — do not change
  scoring without updating app.py actor_detail and actors queries.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("forge.engines.gravity")

# ── Lazy import of feedback engine (avoids circular import at module level) ───
def _actor_influence_safe(actors: List[Dict]) -> float:
    """Import actor_influence only when needed. Returns 1.0 on any failure."""
    try:
        from forage.engines.feedback_engine import actor_influence
        return actor_influence(actors)
    except Exception:
        return 1.0


# ══════════════════════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════════════════════

def _clamp(value: float, minv: float = 0.0, maxv: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return minv
    return max(minv, min(maxv, v))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# ACLED metadata extraction
# ══════════════════════════════════════════════════════════════════════════════

# Matches acled_collector.EVENT_TYPE_MAP
_ACLED_EVENT_SEVERITY: Dict[str, float] = {
    "Battles":                     0.85,
    "Violence against civilians":  0.90,
    "Explosions/Remote violence":  0.80,
    "Riots":                       0.65,
    "Protests":                    0.35,
    "Strategic developments":      0.45,
}

_ACLED_EVENT_SENTIMENT: Dict[str, float] = {
    "Battles":                     -0.90,
    "Violence against civilians":  -1.00,
    "Explosions/Remote violence":  -0.90,
    "Riots":                       -0.70,
    "Protests":                    -0.30,
    "Strategic developments":      -0.40,
}

# Matches acled_collector._actor_importance() type weight map
_ACLED_ACTOR_WEIGHTS: Dict[str, float] = {
    "state forces":          0.90,
    "rebel groups":          0.85,
    "political militias":    0.80,
    "identity militias":     0.75,
    "external/other forces": 0.70,
    "rioters":               0.55,
    "protesters":            0.30,
    "civilians":             0.20,
}

_ACLED_FATALITY_CAP = 50.0   # matches acled_collector.FATALITY_CAP
_ACLED_CREDIBILITY  = 0.85   # peer-reviewed sourced data

# Phase 73 — Investigative source gravity floor.
# RSS feeds from these outlets arrive headline-only, which starves the
# standard five-factor model of severity signal even when the underlying
# story is analytically significant (procurement fraud, commission
# testimony, institutional failure). Gated on stream + non-zero severity so
# lifestyle/sport/wire content from the same domains is never boosted.
_INVESTIGATIVE_SOURCES = frozenset({
    "amabhungane", "dailymaverick", "dailymaverick_corruption",
    "news24_crime", "timeslive_corruption", "groundup",
    # Stable 1.2.1 — SA security/defence + general news (ACLED replacement)
    "defenceweb", "citizen_news",
    # Phase 74 — US region (civic_intel_collector_us.py)
    "propublica", "icij", "theintercept", "revealnews", "occrp_us",
    "us_infrastructure",
})
_INVESTIGATIVE_BOOST_STREAMS = frozenset({"CRIME_INTEL", "INFRASTRUCTURE", "PRIORITY"})
_INVESTIGATIVE_FLOOR = 0.35


def _extract_acled_inputs(signal: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    Try to parse ACLED-specific gravity inputs from a signal dict.

    ACLED signals store pre-computed inputs in two places:
      1. Top-level keys: signal['severity'], signal['actor_importance'],
         signal['sentiment'], signal['source_credibility']
         (written by acled_collector._build_signal())
      2. metadata_json sub-keys under 'severity', 'fatalities', etc.

    Returns a dict of gravity inputs if this is an ACLED signal,
    or None if it is not.

    Detection: signal['source'] == 'acled' OR metadata_json contains
    'acled_id' key.
    """
    source = (signal.get("source") or "").lower()
    if source != "acled":
        # Check metadata_json as fallback detection
        meta_raw = signal.get("metadata_json")
        if not meta_raw:
            return None
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except (json.JSONDecodeError, TypeError):
            return None
        if "acled_id" not in meta:
            return None
    else:
        try:
            meta = (
                json.loads(signal["metadata_json"])
                if isinstance(signal.get("metadata_json"), str)
                else (signal.get("metadata_json") or {})
            )
        except (json.JSONDecodeError, TypeError):
            meta = {}

    # ── Extract from top-level signal keys first (fastest path) ──────────────
    # acled_collector writes these directly onto the signal dict
    if "severity" in signal and "actor_importance" in signal:
        return {
            "severity":           _safe_float(signal.get("severity"),           0.3),
            "actor_importance":   _safe_float(signal.get("actor_importance"),   0.4),
            "sentiment":          _safe_float(signal.get("sentiment"),          -0.5),
            "source_credibility": _safe_float(signal.get("source_credibility"), _ACLED_CREDIBILITY),
            "fatalities":         _safe_float(meta.get("fatalities"),            0.0),
            "event_type":         meta.get("event_type", ""),
            "_path":              "acled_toplevel",
        }

    # ── Reconstruct from metadata_json (signals loaded from DB) ──────────────
    # When a signal is loaded from DB via run_full_ingest(), the top-level
    # gravity keys are not present (they weren't schema columns).
    # We recompute them from metadata_json.
    fatalities = _safe_float(meta.get("fatalities"), 0.0)
    event_type = meta.get("event_type", "")
    inter1     = (meta.get("inter1") or "").lower()
    inter2     = (meta.get("inter2") or "").lower()

    base_sev   = _ACLED_EVENT_SEVERITY.get(event_type, 0.40)
    fat_component = min(fatalities / _ACLED_FATALITY_CAP, 1.0)
    severity   = round(0.5 * base_sev + 0.5 * fat_component, 4)

    a1w = max((v for k, v in _ACLED_ACTOR_WEIGHTS.items() if k in inter1), default=0.40)
    a2w = max((v for k, v in _ACLED_ACTOR_WEIGHTS.items() if k in inter2), default=0.40)
    actor_importance = round(max(a1w, a2w), 4)

    sentiment = _ACLED_EVENT_SENTIMENT.get(event_type, -0.50)

    # Use pre-stored values from metadata if available (collector may have
    # stored them there for DB-round-trip fidelity)
    return {
        "severity":           _safe_float(meta.get("severity"),           severity),
        "actor_importance":   _safe_float(meta.get("actor_importance"),   actor_importance),
        "sentiment":          _safe_float(meta.get("sentiment"),          sentiment),
        "source_credibility": _safe_float(meta.get("source_credibility"), _ACLED_CREDIBILITY),
        "fatalities":         fatalities,
        "event_type":         event_type,
        "_path":              "acled_metadata",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Path A — ACLED scoring
# ══════════════════════════════════════════════════════════════════════════════

def _score_acled(inputs: Dict[str, float], frequency: float) -> float:
    """
    ACLED fast-path gravity scoring.

    Uses pre-computed structured inputs — no NLP needed.
    Momentum floor raised to 0.9 (vs 0.8 standard) because ACLED data
    is authoritative: even low-frequency ACLED events carry high analytical
    weight for FORGE's SA-focused mission.

    Formula (same weights as standard path — consistency for UI thresholds):
      base = 0.35*severity + 0.25*actor_importance + 0.15*frequency
           + 0.15*urgency_sentiment + 0.10*source_credibility
      momentum = 0.9 + 0.1 * frequency   ← raised floor vs standard 0.8
      score = clamp(base * momentum)

    The fatality component is already baked into severity by the collector
    (_fatality_severity formula: 0.5*base_event + 0.5*fat/50). We add a
    direct fatality bonus on top for events with high casualties — this
    ensures mass-casualty events reach critical threshold (>= 0.75) even
    when other factors are moderate.

    Fatality bonus: +0.05 per 10 fatalities, capped at +0.15
    """
    severity    = _clamp(inputs["severity"])
    actor_imp   = _clamp(inputs["actor_importance"])
    sentiment   = _safe_float(inputs["sentiment"], -0.5)
    credibility = _clamp(inputs["source_credibility"], 0.0, 1.0)
    fatalities  = max(0.0, _safe_float(inputs.get("fatalities"), 0.0))
    freq        = _clamp(frequency)

    # Normalise sentiment (-1..+1) → urgency (0..1), inverted
    norm_sentiment  = _clamp((sentiment + 1.0) / 2.0)
    urgency_sentiment = 1.0 - norm_sentiment

    base = (
        0.35 * severity +
        0.25 * actor_imp +
        0.15 * freq +
        0.15 * urgency_sentiment +
        0.10 * credibility
    )

    # Raised momentum floor for ACLED (high-confidence source)
    momentum = 0.9 + 0.1 * freq
    score    = base * momentum

    # Direct fatality bonus — mass-casualty events must reach critical tier
    # +0.05 per bracket of 10 fatalities, max +0.15
    # Thresholds: 10 fat → +0.05, 25 fat → +0.10, 50+ fat → +0.15
    fatality_bonus = min(0.05 * (fatalities // 10), 0.15)
    score += fatality_bonus

    return _clamp(score)


# ══════════════════════════════════════════════════════════════════════════════
# Path B — Standard scoring
# ══════════════════════════════════════════════════════════════════════════════

def calculate_gravity(inputs: Dict[str, Any]) -> float:
    """
    Standard five-factor gravity computation.
    Used for RSS, GDELT DOC, USGS, FIRMS, civic intel, and any source
    that doesn't supply ACLED-style structured metadata.

    All inputs expected in [0, 1] except sentiment which is [-1, +1].
    Missing inputs default to safe minimums (not zero — zero would
    suppress all non-priority signals from appearing in the feed).
    """
    severity     = _clamp(inputs.get("severity",           0.0))
    actor_imp    = _clamp(inputs.get("actor_importance",   0.0))
    frequency    = _clamp(inputs.get("frequency",          0.0))
    credibility  = _clamp(inputs.get("source_credibility", 0.5))

    sentiment = _safe_float(inputs.get("sentiment", 0.0), 0.0)
    sentiment = _clamp((sentiment + 1.0) / 2.0)   # normalise to 0..1
    urgency_sentiment = 1.0 - sentiment             # invert: negative = high urgency

    base = (
        0.35 * severity +
        0.25 * actor_imp +
        0.15 * frequency +
        0.15 * urgency_sentiment +
        0.10 * credibility
    )

    # Momentum: slight upward curve for repeated/high-frequency signals
    momentum = 0.8 + 0.2 * frequency
    return _clamp(base * momentum)


# ══════════════════════════════════════════════════════════════════════════════
# Public interface
# ══════════════════════════════════════════════════════════════════════════════

def score_signal(
    signal: Dict[str, Any],
    actors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Score a signal and return an enriched copy with gravity metadata.

    This is the primary public function called by:
      core/pipeline/ingest.py
      forage/collectors/gdelt_collector.py (old event stream)
      any future collector

    Returns the original signal dict plus:
      gravity_score       float [0.0, 1.0]
      feedback_influence  float (actor weight multiplier, 1.0 if no actors)
      _gravity_path       str   "acled_toplevel" | "acled_metadata" | "standard"
      _gravity_inputs     dict  the inputs used (for debugging / audit)

    The returned dict is a shallow copy — original signal is not mutated.
    """
    if not isinstance(signal, dict):
        log.warning(f"[gravity] score_signal received non-dict: {type(signal)}")
        signal_out = {}
        signal_out["gravity_score"]      = 0.0
        signal_out["feedback_influence"] = 1.0
        signal_out["_gravity_path"]      = "error"
        return signal_out

    signal_out = dict(signal)
    frequency  = _clamp(signal.get("frequency", 0.0))

    # ── Attempt ACLED path first ──────────────────────────────────────────────
    acled_inputs = _extract_acled_inputs(signal)

    if acled_inputs is not None:
        gravity_score = _score_acled(acled_inputs, frequency)
        path          = acled_inputs.get("_path", "acled")
        gravity_inputs = {k: v for k, v in acled_inputs.items() if k != "_path"}
        gravity_inputs["frequency"] = frequency
        log.debug(
            f"[gravity] ACLED path={path} "
            f"fat={acled_inputs.get('fatalities', 0):.0f} "
            f"sev={acled_inputs.get('severity', 0):.3f} "
            f"→ {gravity_score:.4f}"
        )
    else:
        # ── Standard path ─────────────────────────────────────────────────────
        gravity_inputs = {
            "severity":           _safe_float(signal.get("severity"),           0.0),
            "actor_importance":   _safe_float(signal.get("actor_importance"),   0.0),
            "frequency":          frequency,
            "sentiment":          _safe_float(signal.get("sentiment"),          0.0),
            "source_credibility": _safe_float(signal.get("source_credibility"), 0.5),
        }
        gravity_score = calculate_gravity(gravity_inputs)
        path          = "standard"
        log.debug(
            f"[gravity] standard source={signal.get('source','?')} "
            f"sev={gravity_inputs['severity']:.3f} "
            f"→ {gravity_score:.4f}"
        )

        # ── Investigative source floor (gated) ─────────────────────────────────
        source = str(signal.get("source", "")).lower()
        stream = str(signal.get("stream", "")).upper()
        if (
            source in _INVESTIGATIVE_SOURCES
            and stream in _INVESTIGATIVE_BOOST_STREAMS
            and gravity_inputs["severity"] > 0.0
            and gravity_score < _INVESTIGATIVE_FLOOR
        ):
            log.debug(
                f"[gravity] investigative floor applied source={source} "
                f"stream={stream} {gravity_score:.4f} → {_INVESTIGATIVE_FLOOR:.2f}"
            )
            gravity_score = _INVESTIGATIVE_FLOOR
            path = "standard_investigative_floor"

    # ── Actor feedback multiplier ─────────────────────────────────────────────
    feedback_influence = 1.0
    if actors:
        try:
            feedback_influence = _actor_influence_safe(actors)
            gravity_score      = _clamp(gravity_score * feedback_influence)
        except Exception as exc:
            log.warning(f"[gravity] actor_influence failed (using 1.0): {exc}")

    # ── Write output fields ───────────────────────────────────────────────────
    signal_out["gravity_score"]      = round(gravity_score, 6)
    signal_out["feedback_influence"] = round(feedback_influence, 6)
    signal_out["_gravity_path"]      = path
    signal_out["_gravity_inputs"]    = gravity_inputs

    return signal_out


# ══════════════════════════════════════════════════════════════════════════════
# Standalone utility — batch rescoring
# ══════════════════════════════════════════════════════════════════════════════

def rescore_signals_from_db(
    db_path,
    source_filter: Optional[str] = None,
    null_only: bool = False,
    batch_size: int = 10_000,
) -> dict:
    """
    Re-score signals already in the DB and update gravity_score in place.

    Parameters
    ──────────
    source_filter : str, optional
        Restrict to signals from this source (e.g. 'acled').
    null_only : bool
        If True, only score signals where gravity_score IS NULL.
        Use this for incremental backfills after new ingestion.
    batch_size : int
        Rows fetched per DB round-trip (controls peak RAM at scale).

    Does NOT call ingest_signal — writes gravity_score directly.
    Does NOT trigger apply_conclave_stub.

    Usage:
      from forage.engines.gravity_engine import rescore_signals_from_db
      # Full rescore
      rescore_signals_from_db(DB_PATH, source_filter='acled')
      # Backfill only NULL-scored signals
      rescore_signals_from_db(DB_PATH, null_only=True)
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    conditions: list = []
    params: list     = []

    if source_filter:
        conditions.append("source = ?")
        params.append(source_filter)
    if null_only:
        conditions.append("gravity_score IS NULL")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = conn.execute(f"SELECT COUNT(*) FROM signals {where}", params).fetchone()[0]

    log.info(f"[rescore] Signals to score: {total:,} "
             f"(null_only={null_only}, source={source_filter or 'all'})")

    updated = 0
    errors  = 0
    offset  = 0

    while True:
        rows = conn.execute(
            f"SELECT * FROM signals {where} ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            params + [batch_size, offset],
        ).fetchall()

        if not rows:
            break

        conn.execute("BEGIN")
        for row in rows:
            try:
                result = score_signal(dict(row))
                conn.execute(
                    "UPDATE signals SET gravity_score = ? WHERE signal_id = ?",
                    (result["gravity_score"], row["signal_id"]),
                )
                updated += 1
            except Exception as exc:
                log.warning(f"[rescore] signal={row['signal_id']}: {exc}")
                errors += 1
        conn.execute("COMMIT")

        offset += len(rows)
        log.info(f"[rescore] {updated:,}/{total:,} scored...")

    conn.close()

    summary = {
        "status":  "ok" if errors == 0 else "partial",
        "updated": updated,
        "errors":  errors,
        "source":  source_filter or "all",
        "null_only": null_only,
    }
    log.info(f"[rescore] Complete: {summary}")
    return summary
