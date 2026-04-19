"""
FORGE — Mega Runner  (mega_ingest.py)
══════════════════════════════════════════════════════════════════════════════

Four-phase pipeline runner:

  Phase 1  Collection    — async concurrent signal intake from all sources
  Phase 2  Engines       — sync: cluster → NER → anomaly → correlation →
                           decay → evolution → graph → sentinel
  Phase 3  Ingest        — Conclave + EntityResolver + escalation on NEW
                           (unprocessed) signals only
  Phase 4  Summary       — pipeline health stats to stdout + pipeline_runs

Fixes applied vs previous version
───────────────────────────────────
  - log defined BEFORE FMS bootstrap (was crashing on bootstrap failure)
  - ACLED collector wired in alongside new GDELT DOC API collector
  - Old gdelt_collector kept as fallback with graceful ImportError guard
  - run_full_ingest now filters to unprocessed signals only (processed_at IS NULL)
    preventing Conclave from re-running and apply_conclave_stub from
    overwriting already-scored signals on every run
  - Correct engine entry points: Sentinel(db_path).run(), GraphEngine(db_path).run()
    etc. — previous stubs (sentinel.process_alerts, graph_engine.build_graphs)
    were no-ops that silently did nothing
  - actors count query fixed: uses source_type filter, not non-existent
    automated column
  - Environment variable handling documented and validated at startup

Environment variables (solo dev)
─────────────────────────────────
  FORGE_DB          Path to database.db   (default: auto-detect from repo root)
  ACLED_KEY         ACLED API key         (required for ACLED collector)
  ACLED_EMAIL       ACLED registered email (required for ACLED collector)
  FORGE_ADMIN_PASSWORD                    (optional, used by app.py only)

Usage
─────
  python mega_ingest.py                  full run
  python mega_ingest.py --collect-only   collection phase only
  python mega_ingest.py --ingest-only    Conclave phase only (skips collection)
  python mega_ingest.py --dry-run        collect but don't write to DB
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import nest_asyncio

# ── Logging must be configured BEFORE any imports that use log ────────────────
# (Previous version called log.warning before log was defined — crash on FMS err)
logging.basicConfig(
    level=logging.INFO,
    format="[SYSTEM_INQUIRY] %(asctime)s >> %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mega_runner")

# Patch asyncio to allow nested event loops (required by some collectors)
nest_asyncio.apply()

# ── Path resolution ───────────────────────────────────────────────────────────
# Prefer FORGE_DB env var; fall back to repo-root database.db
_env_db = os.environ.get("FORGE_DB")
if _env_db:
    DB_PATH = Path(_env_db).resolve()
else:
    try:
        from core.db.connection import DB_PATH as _core_db
        DB_PATH = _core_db
    except ImportError:
        DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

log.info(f"[config] DB_PATH = {DB_PATH}")

# ── FMS bootstrap (must come after log is defined) ────────────────────────────
try:
    from core.fms.bootstrap import bootstrap_fms
    bootstrap_fms(verbose=False)
    log.info("[FMS] Bootstrap complete")
except Exception as _fms_err:
    log.warning(f"[FMS] Bootstrap failed (non-fatal): {_fms_err}")

# ── Core imports ──────────────────────────────────────────────────────────────

# ── FORGE path bootstrap (added by refactor) ──────────────────────────
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from core.pipeline.ingest import ingest_signal

# ── Collector imports ─────────────────────────────────────────────────────────
# Each collector exposes an async collect() coroutine.
# Failures are isolated — a missing collector never kills the full run.

_collectors_available: list[str] = []

# NEW: ACLED — primary high-signal collector
try:
    from forage.collectors.acled_collector import collect as acled_collect
    _collectors_available.append("acled")
except ImportError as _e:
    log.warning(f"[collector] ACLED not importable (skipping): {_e}")
    acled_collect = None

# NEW: GDELT DOC API — replaces noisy event stream
try:
    from forage.collectors.gdelt_collector import collect as gdelt_collect
    _collectors_available.append("gdelt_doc")
except ImportError as _e:
    log.warning(f"[collector] GDELT DOC collector not importable (skipping): {_e}")
    gdelt_collect = None

# Existing collectors — unchanged async_main() interface
try:
    from forage.collectors import civic_intel_collector
    _collectors_available.append("civic_intel")
except ImportError:
    civic_intel_collector = None

# ── SEVERED FEEDS (Phase 3.3 — Source Severance 2026-04-14) ─────────────────
# FIRMS (NASA wildfire sensor), earthquake_collector, and usgs_collector are
# permanently disabled. They produced 142k+ noise signals with no investigative
# value for FORGE's SA accountability mandate. Historical data purged via
# scripts/purge_noise_sources.py. Do not re-enable without a deliberate
# architecture decision.
firms_collector    = None  # SEVERED — NASA FIRMS thermal sensor noise
earthquake_collector = None  # SEVERED — USGS earthquake feed
usgs_collector     = None  # SEVERED — USGS duplicate feed

try:
    from forage.collectors import rss_collector
    _collectors_available.append("rss")
except ImportError:
    rss_collector = None

try:
    from forage.collectors import dork_collector
    _collectors_available.append("dork")
except ImportError:
    dork_collector = None

# C-1: Sovereign-First PDF vault — async portal crawler with OCR bridge
try:
    from forage.collectors.pdf_infiltrator import collect as pdf_infiltrator_collect
    _collectors_available.append("pdf_infiltrator")
except ImportError as _e:
    log.warning(f"[collector] pdf_infiltrator not importable (skipping): {_e}")
    pdf_infiltrator_collect = None

log.info(f"[collector] Available: {_collectors_available}")

# ── Engine imports ────────────────────────────────────────────────────────────
# Engines are class-based: Engine(db_path=DB_PATH).run()
# We import the classes directly so we can call .run() with DB_PATH.

def _safe_import(module_path: str, attr: str):
    """Import an attribute from a module, returning None on failure."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr, None)
    except Exception as exc:
        log.warning(f"[engine] Could not import {module_path}.{attr}: {exc}")
        return None

ArtifactProcessor  = _safe_import("forage.processors.artifact_processor", "ProcessorManager")
ClusterEngine      = _safe_import("forage.engines.cluster_engine",      "ClusterEngine")
NERProcessor       = _safe_import("forage.processors.ner_processor",    "NERProcessor")
TripleExtractor    = _safe_import("forage.processors.triple_extractor",  "TripleExtractor")
AnomalyEngine      = _safe_import("forage.engines.anomaly_engine",      "AnomalyEngine")
CorrelationEngine  = _safe_import("forage.engines.correlation_engine",  "CorrelationEngine")
DecayEngine        = _safe_import("forage.engines.decay_engine",        "DecayEngine")
EvolutionEngine    = _safe_import("forage.engines.evolution_engine",    "EvolutionEngine")
GraphEngine        = _safe_import("forage.engines.graph_engine",        "GraphEngine")
SentinelClass      = _safe_import("forage.processors.sentinel",         "Sentinel")


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Collection
# ══════════════════════════════════════════════════════════════════════════════

async def run_all_collectors(dry_run: bool = False) -> dict:
    """
    Run all available collectors concurrently.
    return_exceptions=True ensures one failing collector never kills the batch.
    Results are logged per-collector for pipeline_runs observability.
    """
    log.info("S A M A R I T A N . O N L I N E")
    log.info("DETERMINING_RELEVANCE...")
    log.info(f"[collect] Running {len(_collectors_available)} collectors concurrently")

    tasks: list = []
    labels: list[str] = []

    # ── New collectors (use collect() coroutine interface) ────────────────────
    if acled_collect is not None:
        # Only run ACLED if credentials are present
        if os.environ.get("ACLED_KEY") and os.environ.get("ACLED_EMAIL"):
            tasks.append(acled_collect(db_path=DB_PATH))
            labels.append("acled")
        else:
            log.warning(
                "[collector] ACLED skipped: ACLED_KEY and ACLED_EMAIL env vars not set. "
                "Register free at https://developer.acleddata.com/"
            )

    if gdelt_collect is not None:
        tasks.append(gdelt_collect(db_path=DB_PATH))
        labels.append("gdelt_doc")

    # C-1: Sovereign-First PDF vault — concurrent portal crawl + OCR bridge
    if pdf_infiltrator_collect is not None:
        tasks.append(pdf_infiltrator_collect(db_path=DB_PATH))
        labels.append("pdf_infiltrator")

    # ── Existing collectors (use async_main() interface) ──────────────────────
    _legacy = [
        (civic_intel_collector, "civic_intel",  {"": None}),
        # firms_collector     SEVERED (Phase 3.3 — noise purge)
        (rss_collector,         "rss",           {"": None}),
        # earthquake_collector SEVERED (Phase 3.3 — noise purge)
        # usgs_collector       SEVERED (Phase 3.3 — noise purge)
        (dork_collector,        "dork",          {"": None}),
    ]
    for mod, label, _kwargs in _legacy:
        if mod is not None and hasattr(mod, "async_main"):
            tasks.append(mod.async_main())
            labels.append(label)

    if not tasks:
        log.warning("[collect] No collectors available — skipping collection phase")
        return {"collectors_run": 0, "errors": []}

    start = time.monotonic()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    duration = round(time.monotonic() - start, 2)

    errors = []
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            log.error(f"[collector:{label}] FAILED: {result}")
            errors.append({"collector": label, "error": str(result)})
        elif isinstance(result, dict):
            status   = result.get("status", "unknown")
            inserted = result.get("inserted", result.get("records_out", "?"))
            log.info(f"[collector:{label}] {status} — {inserted} signals written")
        else:
            log.info(f"[collector:{label}] completed (no structured result)")

    log.info(
        f"[collect] Collection complete in {duration}s — "
        f"{len(tasks)} collectors, {len(errors)} errors"
    )
    return {
        "collectors_run": len(tasks),
        "errors":         errors,
        "duration_s":     duration,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Engines & Processors
# ══════════════════════════════════════════════════════════════════════════════

def _run_engine(cls, name: str, **kwargs) -> dict:
    """
    Instantiate an engine class with DB_PATH and call .run().
    Returns a result dict. Catches all exceptions — engine failure is
    logged and reported but never crashes the pipeline.
    """
    if cls is None:
        log.warning(f"[engine:{name}] Not available (import failed)")
        return {"status": "unavailable", "engine": name}
    start = time.monotonic()
    try:
        instance = cls(db_path=DB_PATH, **kwargs)
        result   = instance.run()
        duration = round(time.monotonic() - start, 2)
        status   = result.get("status", "ok") if isinstance(result, dict) else "ok"
        log.info(f"[engine:{name}] {status} in {duration}s")
        return result if isinstance(result, dict) else {"status": "ok"}
    except Exception as exc:
        duration = round(time.monotonic() - start, 2)
        log.error(f"[engine:{name}] FAILED in {duration}s: {exc}")
        return {"status": "error", "engine": name, "error": str(exc)}


def run_engines_processors() -> dict:
    """
    Run all engines and processors in dependency order.

    Order matters:
      1. artifact_processor  — extract text from uploaded artifacts
      2. cluster_engine      — assign cluster_id to signals (needed by Sentinel)
      3. ner_processor       — populate signal_entities (needed by Sentinel Rule 3)
      4. anomaly_engine      — compute signal_baselines (needed by correlation)
      5. correlation_engine  — write correlated_incidents (needed by Sentinel Rule 1)
      6. decay_engine        — decay relevance_score on stale signals
      7. evolution_engine    — write discovery_targets
      8. graph_engine        — compute actor_network_metrics (needed by Sentinel Rule 3)
      9. sentinel            — escalate patterns into sentinel_alerts

    Note on ProcessorManager: it requires db_path as a positional arg,
    not a keyword arg — handled specially below.
    """
    log.info("[engines] Executing analysis pipeline...")
    results: dict[str, dict] = {}

    # artifact_processor has a different constructor signature
    if ArtifactProcessor is not None:
        try:
            start = time.monotonic()
            pm = ArtifactProcessor(db_path=DB_PATH)
            pm.process_all()
            results["artifact_processor"] = {
                "status": "ok",
                "duration_s": round(time.monotonic() - start, 2),
            }
            log.info(f"[engine:artifact_processor] ok")
        except Exception as exc:
            log.error(f"[engine:artifact_processor] FAILED: {exc}")
            results["artifact_processor"] = {"status": "error", "error": str(exc)}
    else:
        results["artifact_processor"] = {"status": "unavailable"}

    # ── Vision status: report NULL cluster_id count before clustering ───────────
    # This gives the analyst a perception-restoration metric on every run.
    try:
        _vc = sqlite3.connect(str(DB_PATH), timeout=10)
        _null_before = _vc.execute(
            "SELECT COUNT(*) FROM signals WHERE cluster_id IS NULL"
        ).fetchone()[0]
        _vc.close()
        log.info(
            f"[cluster] Vision status PRE-run: {_null_before:,} signals with "
            f"NULL cluster_id (analytical blindness target)"
        )
    except Exception as _ve:
        log.debug(f"[cluster] Pre-run NULL count failed (non-fatal): {_ve}")
        _null_before = None

    # Standard class-based engines
    engine_sequence = [
        (ClusterEngine,     "cluster_engine"),
        (NERProcessor,      "ner_processor"),
        (AnomalyEngine,     "anomaly_engine"),
        (CorrelationEngine, "correlation_engine"),
        (DecayEngine,       "decay_engine"),
        (EvolutionEngine,   "evolution_engine"),
        # Phase 43: NLP triple extraction populates entity_relationships
        # before graph_engine so Factor 2 PageRank uses typed directed edges
        (TripleExtractor,   "triple_extractor"),
        (GraphEngine,       "graph_engine"),
        (SentinelClass,     "sentinel"),
    ]

    for cls, name in engine_sequence:
        results[name] = _run_engine(cls, name)

    errors = [n for n, r in results.items() if r.get("status") == "error"]
    log.info(
        f"[engines] Pipeline complete — "
        f"{len(results)} engines run, {len(errors)} errors"
        + (f": {errors}" if errors else "")
    )

    # ── Vision status: post-run delta ─────────────────────────────────────────
    try:
        _vc2 = sqlite3.connect(str(DB_PATH), timeout=10)
        _null_after = _vc2.execute(
            "SELECT COUNT(*) FROM signals WHERE cluster_id IS NULL"
        ).fetchone()[0]
        _vc2.close()
        if _null_before is not None:
            _cleared = _null_before - _null_after
            log.info(
                f"[cluster] Vision status POST-run: {_null_after:,} signals still "
                f"unclassed | {_cleared:,} clustered this cycle"
            )
        else:
            log.info(
                f"[cluster] Vision status POST-run: {_null_after:,} signals with "
                f"NULL cluster_id remaining"
            )
    except Exception as _ve2:
        log.debug(f"[cluster] Post-run NULL count failed (non-fatal): {_ve2}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2.5 — NER → Actor Bridge
# ══════════════════════════════════════════════════════════════════════════════

# Terms that NER extracts as entity names but are not real actors.
# Covers: FIRMS sensor metadata, compass directions, NLP type labels,
# SA publication names that appear in bylines, and HTML artifacts.
_NER_BLOCKLIST = frozenset({
    # FIRMS / satellite sensor terms
    "mw", "frp", "nrt", "modis", "viirs",
    # Full 16-point compass rose
    "n", "s", "e", "w", "ne", "nw", "se", "sw",
    "nne", "nnw", "ene", "ese", "sse", "ssw", "wsw", "wnw",
    # NLP pipeline type labels extracted as names
    "date", "actor2", "location", "government", "person",
    "institution", "organization", "gpe", "org", "per", "loc", "misc",
    # SA publication / source names that aren't investigative targets
    "groundup", "iol", "sa", "mybroadband", "hawk", "sana",
    "south african government news agency",
    # Generic nouns that slip through NER
    "company", "police", "media", "minister",
})


def bridge_ner_to_actors() -> dict:
    """
    Bridge Phase 2 NER output into the Phase 3 actor registry.

    NERProcessor writes extracted entities to signal_entities, but
    ingest_signal() resolves actors only from SignalInterpreter keyword
    output — the two pipelines are siloed. This function closes the gap.

    Strategy:
      1. Read signal_entities for PERSON/ORG appearing in >= 2 distinct signals
      2. Apply _NER_BLOCKLIST to remove sensor noise, directions, and artifacts
      3. Materialize survivors via get_or_create_actor() (idempotent, source_type=live)

    Must run AFTER Phase 2 (NER has written signal_entities) and
    BEFORE Phase 3 (EntityResolver finds new actors during Conclave).
    """
    from forage.engines.entity_engine import get_or_create_actor

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    try:
        rows = conn.execute("""
            SELECT text, label, COUNT(DISTINCT signal_id) AS signal_freq
            FROM signal_entities
            WHERE label IN ('PERSON', 'ORG')
              AND text IS NOT NULL
              AND length(trim(text)) > 2
            GROUP BY lower(trim(text)), label
            HAVING signal_freq >= 2
            ORDER BY signal_freq DESC
        """).fetchall()
    except Exception as exc:
        log.warning(f"[bridge_ner] signal_entities query failed: {exc}")
        conn.close()
        return {"status": "error", "error": str(exc), "materialized": 0}

    materialized    = 0
    skipped_noise   = 0
    skipped_existing = 0

    for row in rows:
        raw_text = (row["text"] or "").strip()
        label    = row["label"]

        if not raw_text or len(raw_text) <= 2:
            skipped_noise += 1
            continue

        if raw_text.lower() in _NER_BLOCKLIST:
            skipped_noise += 1
            continue

        # Reject pure numerics and HTML artifacts
        if raw_text.isdigit() or "&" in raw_text or "\xa0" in raw_text:
            skipped_noise += 1
            continue

        try:
            existing = conn.execute(
                "SELECT actor_id FROM actors WHERE lower(trim(name)) = ?",
                (raw_text.lower(),)
            ).fetchone()
            if existing:
                skipped_existing += 1
                continue

            actor_type = "person" if label == "PERSON" else "institution"
            get_or_create_actor(raw_text, conn, actor_type=actor_type)
            materialized += 1
            log.debug(f"[bridge_ner] materialized: {raw_text!r} ({label})")
        except Exception as exc:
            log.warning(f"[bridge_ner] failed for {raw_text!r}: {exc}")

    conn.close()

    summary = {
        "status":            "ok",
        "materialized":      materialized,
        "skipped_noise":     skipped_noise,
        "skipped_existing":  skipped_existing,
    }
    log.info(
        f"[bridge_ner] {materialized} new actors · "
        f"{skipped_noise} noise filtered · "
        f"{skipped_existing} already registered"
    )
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2.6 — Dork Signal → Case Evidence Bridge
# ══════════════════════════════════════════════════════════════════════════════

def bridge_dork_to_cases() -> dict:
    """
    Auto-pin dork signals to their highest-confidence cases by following the
    3-hop join: dork_actor (metadata_json) -> actor_id -> event_actors ->
    case_events -> case_id -> case_signals.

    The dork collector stores the targeted actor name in each signal's
    metadata_json as {"dork_actor": "South African Police Service", ...}.
    This function resolves that name to an actor_id, finds which cases that
    actor is already linked to via event_actors, and pins the signal to those
    cases in case_signals.

    Idempotent: case_signals has UNIQUE(case_id, signal_id) — safe to re-run.
    Gracefully skips actors that were pruned (MURDER, Date-actors, etc.).
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    # Build actor name -> actor_id index (lowercase)
    actor_index = {
        r["name"].strip().lower(): int(r["actor_id"])
        for r in conn.execute(
            "SELECT actor_id, name FROM actors WHERE name IS NOT NULL"
        )
    }

    # Load all dork signals with metadata_json
    dork_sigs = conn.execute("""
        SELECT s.signal_id, s.metadata_json
        FROM   signals s
        WHERE  s.source = 'dork'
          AND  s.metadata_json IS NOT NULL
    """).fetchall()

    log.info(f"[bridge_dork] Processing {len(dork_sigs)} dork signals...")

    pinned        = 0
    skipped_actor = 0   # actor name not in registry (pruned / never materialized)
    skipped_case  = 0   # actor exists but not linked to any case
    skipped_dup   = 0   # already in case_signals

    for sig in dork_sigs:
        try:
            meta = json.loads(sig["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue

        actor_name = (meta.get("dork_actor") or "").strip()
        if not actor_name:
            continue

        actor_id = actor_index.get(actor_name.lower())
        if actor_id is None:
            skipped_actor += 1
            continue

        # Find cases linked to this actor via event_actors -> case_events
        linked_cases = conn.execute("""
            SELECT DISTINCT ce.case_id
            FROM   event_actors ea
            JOIN   case_events  ce ON ce.event_id = ea.event_id
            WHERE  ea.actor_id = ?
        """, (actor_id,)).fetchall()

        if not linked_cases:
            # Fallback: check actor_events (older pipeline table)
            linked_cases = conn.execute("""
                SELECT DISTINCT ce.case_id
                FROM   actor_events ae
                JOIN   case_events  ce ON ce.event_id = ae.event_id
                WHERE  ae.actor_id = ?
            """, (actor_id,)).fetchall()

        if not linked_cases:
            skipped_case += 1
            continue

        # Pin to the highest case_id found (most recent / most specific case)
        target_case_id = max(r["case_id"] for r in linked_cases)

        try:
            conn.execute("""
                INSERT OR IGNORE INTO case_signals
                    (case_id, signal_id, note)
                VALUES (?, ?, 'auto-linked via dork_actor bridge')
            """, (target_case_id, sig["signal_id"]))
            pinned += 1
        except Exception:
            skipped_dup += 1

    conn.commit()
    conn.close()

    summary = {
        "status":         "ok",
        "dork_signals":   len(dork_sigs),
        "pinned":         pinned,
        "skipped_actor":  skipped_actor,
        "skipped_case":   skipped_case,
        "skipped_dup":    skipped_dup,
    }
    log.info(
        f"[bridge_dork] {pinned} signals pinned to cases · "
        f"{skipped_actor} actors not in registry · "
        f"{skipped_case} actors with no case link"
    )
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2.75 — Co-occurrence → entity_relationships Bridge
# ══════════════════════════════════════════════════════════════════════════════

def bridge_cooccurrence_to_relationships(min_shared: int = 2) -> dict:
    """
    Promote actor co-occurrence pairs into entity_relationships so that
    Factor 2 (named relationship PageRank, weight 0.35) is populated.

    Without this bridge, entity_relationships is empty and rel_pagerank = 0
    for every actor, suppressing 35% of the influence score formula.

    Strategy:
      1. Derive actor pairs that share >= min_shared events using the same
         UNION of actor_events + event_actors that graph_engine uses.
      2. Compute confidence = min(1.0, shared_count / 10.0) — capped at 1.0.
      3. INSERT OR IGNORE into entity_relationships with:
           relation_type     = 'co_occurrence'
           extraction_method = 'co_occurrence_bridge'
      4. Run graph_engine recalculate in-process to refresh influence scores.

    Idempotent: INSERT OR IGNORE prevents duplicates on repeated runs.
    Must run AFTER Phase 2 (graph_engine has computed co-occurrence edges)
    and BEFORE Phase 3 (so dork_collector targets updated influence scores).
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    try:
        pairs = conn.execute("""
            WITH all_actor_events AS (
                SELECT actor_id, event_id FROM actor_events
                UNION
                SELECT actor_id, event_id FROM event_actors
            )
            SELECT ae1.actor_id AS a,
                   ae2.actor_id AS b,
                   COUNT(DISTINCT ae1.event_id) AS shared
            FROM all_actor_events ae1
            JOIN all_actor_events ae2
                ON ae2.event_id = ae1.event_id
               AND ae2.actor_id > ae1.actor_id
            GROUP BY ae1.actor_id, ae2.actor_id
            HAVING COUNT(DISTINCT ae1.event_id) >= ?
        """, (min_shared,)).fetchall()
    except Exception as exc:
        conn.close()
        log.warning(f"[bridge_cooc] co-occurrence query failed: {exc}")
        return {"status": "error", "error": str(exc), "inserted": 0}

    inserted  = 0
    skipped   = 0

    for row in pairs:
        actor_a, actor_b, shared = row["a"], row["b"], row["shared"]
        confidence = min(1.0, shared / 10.0)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO entity_relationships
                    (subject_actor_id, object_actor_id, relation_type,
                     confidence, extraction_method)
                VALUES (?, ?, 'co_occurrence', ?, 'spacy')
            """, (actor_a, actor_b, round(confidence, 4)))
            inserted += 1
        except Exception:
            skipped += 1

    conn.commit()
    conn.close()

    summary = {
        "status":   "ok",
        "pairs_evaluated": len(pairs),
        "inserted": inserted,
        "skipped":  skipped,
    }
    log.info(
        f"[bridge_cooc] {inserted} relationship edges written · "
        f"{skipped} skipped · min_shared={min_shared}"
    )

    # Re-run graph_engine immediately so influence scores reflect Factor 2
    if inserted > 0 and GraphEngine is not None:
        log.info("[bridge_cooc] Refreshing graph metrics with Factor 2 active...")
        try:
            ge = GraphEngine(db_path=DB_PATH)
            ge.run(recalculate=True)
            log.info("[bridge_cooc] Graph metrics refreshed")
        except Exception as exc:
            log.warning(f"[bridge_cooc] Graph refresh failed (non-fatal): {exc}")

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Conclave Ingest
# ══════════════════════════════════════════════════════════════════════════════

def run_full_ingest(
    batch_size: int = 50,
    sleep_interval: float = 0.1,
    reprocess_all: bool = False,
) -> dict:
    """
    Run the Conclave ingest pipeline over signals.

    CRITICAL FIX: Previous version processed ALL signals on every run,
    causing apply_conclave_stub to overwrite gravity_score with 0.1 on
    already-processed signals. This version filters to:
      - processed_at IS NULL  (never processed), OR
      - reprocess_all=True    (explicit re-run flag for recovery)

    Note: apply_conclave_stub() has been removed from ingest.py.
    Conclave scoring is authoritative. graph_sync receives actor_ids
    and event_id via conclave_meta on every processed signal.
    """
    log.info("[ingest] Opening signal batch...")

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if reprocess_all:
        query = "SELECT * FROM signals ORDER BY timestamp ASC"
        params: tuple = ()
        log.info("[ingest] reprocess_all=True — processing every signal")
    else:
        # Only signals that haven't been through Conclave yet
        query = (
            "SELECT * FROM signals "
            "WHERE processed_at IS NULL "
            "ORDER BY is_priority DESC, timestamp ASC"
        )
        params = ()
        log.info("[ingest] Filtering to unprocessed signals (processed_at IS NULL)")

    signals = [dict(row) for row in cur.execute(query, params).fetchall()]
    conn.close()

    total = len(signals)
    if total == 0:
        log.info("[ingest] No unprocessed signals found — Conclave phase skipped")
        return {
            "status":   "skipped",
            "total":    0,
            "processed": 0,
            "errors":   0,
        }

    log.info(f"CALCULATING_RESPONSE: {total} signals to process")

    processed = 0
    error_count = 0
    priority_count = 0

    for idx, row in enumerate(signals, 1):
        signal_id = row.get("signal_id", f"unknown-{idx}")
        try:
            result = ingest_signal(row)
            processed += 1
            if row.get("is_priority"):
                priority_count += 1
        except Exception as exc:
            log.error(f"[ingest] FAILURE signal={signal_id[:12]}…: {exc}")
            error_count += 1

        if idx % batch_size == 0:
            pct = round(idx / total * 100)
            log.info(
                f"[ingest] Progress: {idx}/{total} ({pct}%) | "
                f"ok={processed} err={error_count}"
            )
            time.sleep(sleep_interval)

    # Post-ingest DB counts — use correct schema column names
    db_actors = db_events = db_cases = db_signals_processed = 0
    try:
        _c = sqlite3.connect(str(DB_PATH), timeout=10)
        _cur = _c.cursor()
        # actors.source_type, not actors.automated (column doesn't exist)
        _cur.execute("SELECT COUNT(*) FROM actors WHERE source_type = 'live'")
        db_actors = _cur.fetchone()[0]
        _cur.execute("SELECT COUNT(*) FROM events")
        db_events = _cur.fetchone()[0]
        _cur.execute("SELECT COUNT(*) FROM cases")
        db_cases = _cur.fetchone()[0]
        # Count signals that now have processed_at set
        _cur.execute("SELECT COUNT(*) FROM signals WHERE processed_at IS NOT NULL")
        db_signals_processed = _cur.fetchone()[0]
        _c.close()
    except Exception as exc:
        log.warning(f"[ingest] Post-ingest DB count failed: {exc}")

    summary = {
        "status":               "ok" if error_count == 0 else "partial",
        "total":                total,
        "processed":            processed,
        "errors":               error_count,
        "priority_processed":   priority_count,
        "db_actors_live":       db_actors,
        "db_events":            db_events,
        "db_cases":             db_cases,
        "db_signals_processed": db_signals_processed,
    }

    print(f"\n{'═'*40}")
    print(f"  CONCLAVE PROGRESS SUMMARY")
    print(f"{'═'*40}")
    print(f"  Signals analyzed:     {total}")
    print(f"  Successfully ingested:{processed}")
    print(f"  Errors:               {error_count}")
    print(f"  Priority signals:     {priority_count}")
    print(f"  ── DB totals ──")
    print(f"  Actors (live):        {db_actors}")
    print(f"  Events:               {db_events}")
    print(f"  Cases:                {db_cases}")
    print(f"  Signals processed:    {db_signals_processed}")
    print(f"{'═'*40}\n")

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — Pipeline Run Logging
# ══════════════════════════════════════════════════════════════════════════════

def _log_pipeline_run(
    component: str,
    status: str,
    records_in: int,
    records_out: int,
    duration_s: float,
    detail: dict,
) -> None:
    """Write a pipeline_runs entry. Non-fatal on any error."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            component, status, records_in, records_out,
            round(duration_s, 2),
            json.dumps(detail, ensure_ascii=False, default=str),
        ))
        conn.commit()
        conn.close()
    except Exception as exc:
        log.debug(f"[pipeline_runs] log failed (non-fatal): {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(
        description="FORGE Mega Runner — full pipeline execution"
    )
    p.add_argument(
        "--collect-only", action="store_true",
        help="Run collection phase only (skip engines and Conclave)",
    )
    p.add_argument(
        "--ingest-only", action="store_true",
        help="Run Conclave ingest only (skip collection and engines)",
    )
    p.add_argument(
        "--engines-only", action="store_true",
        help="Run engines/processors only",
    )
    p.add_argument(
        "--reprocess-all", action="store_true",
        help="Reprocess ALL signals through Conclave (default: unprocessed only)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Collection phase only, no DB writes",
    )
    p.add_argument(
        "--batch-size", type=int, default=50,
        help="Conclave ingest batch size (default: 50)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    pipeline_start = time.time()

    collect_result  = {"collectors_run": 0, "errors": []}
    engine_result   = {}
    ingest_result   = {"status": "skipped", "total": 0, "processed": 0}

    # ── Phase 1: Collection ───────────────────────────────────────────────────
    if not args.ingest_only and not args.engines_only:
        collect_result = asyncio.run(
            run_all_collectors(dry_run=args.dry_run)
        )

    if args.dry_run or args.collect_only:
        log.info("[mega] Stopping after collection phase (--dry-run / --collect-only)")
        sys.exit(0)

    # ── Phase 2: Engines ──────────────────────────────────────────────────────
    if not args.ingest_only and not args.collect_only:
        engine_result = run_engines_processors()

    if args.engines_only:
        log.info("[mega] Stopping after engines phase (--engines-only)")
        sys.exit(0)

    # ── Phase 2.5: NER → Actor Bridge ────────────────────────────────────────
    if not args.collect_only and not args.engines_only:
        bridge_ner_to_actors()

    # ── Phase 2.6: Dork Signal → Case Evidence Bridge ─────────────────────
    if not args.collect_only and not args.engines_only:
        bridge_dork_to_cases()

    # ── Phase 2.75: Co-occurrence → entity_relationships Bridge ──────────────
    if not args.collect_only and not args.engines_only:
        bridge_cooccurrence_to_relationships()

    # ── Phase 3: Conclave Ingest ──────────────────────────────────────────────
    if not args.collect_only and not args.engines_only:
        ingest_result = run_full_ingest(
            batch_size=args.batch_size,
            sleep_interval=0.1,
            reprocess_all=args.reprocess_all,
        )

    # ── Phase 4: Log run ──────────────────────────────────────────────────────
    total_duration = round(time.time() - pipeline_start, 2)
    overall_status = (
        "error"
        if collect_result.get("errors") or ingest_result.get("errors", 0) > 0
        else "success"
    )

    _log_pipeline_run(
        component="mega_ingest",
        status=overall_status,
        records_in=ingest_result.get("total", 0),
        records_out=ingest_result.get("processed", 0),
        duration_s=total_duration,
        detail={
            "collect":  collect_result,
            "engines":  {k: v.get("status") for k, v in engine_result.items()},
            "ingest":   ingest_result,
        },
    )

    log.info(
        f"MEGA_RUNNER_COMPLETE — {overall_status.upper()} in {total_duration}s"
    )
    sys.exit(0 if overall_status != "error" else 1)
