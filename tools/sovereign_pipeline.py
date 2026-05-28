#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — Sovereign Pipeline  (tools/sovereign_pipeline.py)
══════════════════════════════════════════════════════════════════════════════

Optimized Day Zero orchestration: Hypothesis → Finished Intelligence.

This script supersedes the ad-hoc legacy run order with a formally sequenced,
gate-driven pipeline. It imports and reuses all functions from mega_ingest.py
and adds the orchestration logic that was missing:

  1. Two-Wave Collection (solves the Dork cold-start problem)
  2. Bleach Safety Net (fills the sanitize_text gap in 4 of 6 collectors)
  3. Sovereign Threshold Gate (prevents premature WikiCompiler + TripleExtractor)
  4. Phase 0 Sterilization (Day Zero only — separates live/seed layers)

═══════════════════════════════════════════════════════════════════════════════
ABSOLUTE RUN ORDER
═══════════════════════════════════════════════════════════════════════════════

  Phase 0  [--day-zero only]
    └─ System Decontamination
         Tags existing rows as source_type='seed' or 'live'.
         ONE-SHOT ONLY. Never run in a recurring loop.

  Phase 1A  Wave A Collection  [concurrent asyncio.gather]
    ├─ GDELT DOC API       no-auth  ~1 req/s  5 queries  SA news
    ├─ Civic Intel         no-auth  RSS/GNews  12 SA investigative sources
    ├─ RSS (GDACS)         no-auth  single feed  crisis/disaster alerts
    ├─ Disease Outbreak    no-auth  multi-tier  WHO/PAHO/HealthMap
    ├─ PDF Infiltrator     no-auth  portal crawl + OCR  [slowest — runs last]
    └─ ACLED               CREDENTIAL-GATED  skipped if env vars absent

  Phase 2  Bleach Safety Net
    └─ sanitize_text() bulk pass on any signals that bypassed collector-level
       cleaning (ACLED, RSS, Disease Outbreak, PDF Infiltrator don't call it).

  Phase 3  Refine & Enrich  [sequential, dependency-ordered]
    ├─ 3.1  artifact_processor   extract text from uploaded artifacts
    ├─ 3.2  ner_processor        populate signal_entities  ← THE NER HOOK
    ├─ 3.3  cluster_engine       assign cluster_id (required by Sentinel Rule 3)
    ├─ 3.4  anomaly_engine       signal baselines (required by correlation)
    ├─ 3.5  correlation_engine   correlated_incidents (required by Sentinel Rule 1)
    ├─ 3.6  decay_engine         relevance decay
    ├─ 3.7  evolution_engine     discovery_targets
    ├─ 3.8  triple_extractor     entity_relationships from artifact cache
    ├─ 3.9  graph_engine         actor_network_metrics (required by Dork)
    └─ 3.10 sentinel             threshold alerts

  Phase 4  Bridge Pass A  [NER output → actor/case graph]
    ├─ 4.1  bridge_ner_to_actors        signal_entities → actors registry
    ├─ 4.2  bridge_pdf_signals_to_cases pdf_infiltrator signals → case_signals
    └─ 4.3  bridge_cooccurrence_to_relationships  co-occurrence → entity_relationships

  Phase 5  Conclave  [unprocessed signals only, priority-first]
    └─ ingest_signal() × N  (processed_at IS NULL)

  Phase 1B  Wave B Collection  [actor-dependent, runs AFTER Conclave]
    └─ Dork Collector  [requires actors with influence_score > 0 in
                        actor_network_metrics — only populated after Phase 3.9]

  Phase 6  Bridge Pass B  [absorb new Dork signals]
    ├─ 6.1  bridge_dork_to_cases        dork signals → case_signals
    ├─ 6.2  triple_extractor (re-run)   catch any new artifact signals
    └─ 6.3  graph_engine (recalculate)  refresh influence scores with new edges

  Phase 7  Sovereign Gate  [conditional]
    IF sovereign_threshold_met():
      ├─ WikiCompiler + WikiLinkEngine
      └─ Log wiki article count

  Phase 8  Telemetry
    └─ pipeline_runs INSERT + summary print

═══════════════════════════════════════════════════════════════════════════════
DESIGN DECISIONS
═══════════════════════════════════════════════════════════════════════════════

  Bleach Protocol position:
    sanitize_text() is the authoritative cleaner in core/pipeline/ingest.py.
    civic_intel and gdelt call it at write time (correct).
    ACLED, RSS, Disease Outbreak, PDF Infiltrator do NOT — this is tech debt.
    Phase 2 bulk-cleans any remaining dirty signals before NER and Conclave.
    This means NER always operates on clean text regardless of collector source.

  Dork collector position (Wave B):
    Dork queries actors by actor_network_metrics.influence_score > 0 (primary)
    or signals.gravity_score > 0.6 (fallback). Both tables are empty on a cold
    Day Zero DB. Running Dork in Wave A on a fresh DB yields 0 qualifying actors
    and wastes all 20 Google News RSS quota slots. Moving it after Conclave
    (which materializes actors and sets gravity_score) ensures every Dork query
    targets a real, scored actor.

  NER position (Phase 3.2):
    NER fires BEFORE ClusterEngine. This is intentional — cluster_id is needed
    by Sentinel but NOT by NER. NER output (signal_entities) feeds:
      a) bridge_ner_to_actors (Phase 4.1) → new actor rows before Conclave
      b) WikiCompiler (Phase 7) → min_mentions entity dossiers
      c) triple_extractor (Phase 3.8) → subject/object extraction
    Starting NER early maximizes the actor population before Conclave runs.

  Sovereign Threshold:
    WikiCompiler.compile_from_entities(min_mentions=3) is cheap to call but
    produces noise pages on a sparse DB. The Sovereign Threshold gate checks
    three conditions before triggering the wiki synthesis:
      - processed_signals ≥ SOVEREIGN_MIN_SIGNALS   (default 10)
      - actors in registry  ≥ SOVEREIGN_MIN_ACTORS   (default 3)
      - signal_entities rows ≥ SOVEREIGN_MIN_ENTITIES (default 50)
    On a full operational run all three conditions are always met. The gate
    only fires a no-op on a dry-run or severely constrained network.

  System Decontamination timing:
    system_decontamination.run_migration() resets ALL source_type values
    based on deterministic rules. Running it in a loop would flip newly-created
    live actors back to 'seed'. It is guarded behind --day-zero and a
    database_is_fresh() check. Safe to re-run on the same Day Zero DB
    (idempotent within a single session) but should NEVER be scheduled.

Usage
─────
  python tools/sovereign_pipeline.py                # normal operational run
  python tools/sovereign_pipeline.py --day-zero     # first run: sterilize + full pipeline
  python tools/sovereign_pipeline.py --collect-only # Wave A only (no engines, no Conclave)
  python tools/sovereign_pipeline.py --no-dork      # skip Wave B (useful mid-session)
  python tools/sovereign_pipeline.py --dry-run      # collect but no DB writes
  python tools/sovereign_pipeline.py --reprocess-all # force re-Conclave every signal
"""

import argparse
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

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[SOVEREIGN] %(asctime)s >> %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sovereign_pipeline")
nest_asyncio.apply()

# ── Path resolution ───────────────────────────────────────────────────────────
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

_env_db = os.environ.get("FORGE_DB")
if _env_db:
    DB_PATH = Path(_env_db).resolve()
else:
    try:
        from core.db.connection import DB_PATH as _core_db
        DB_PATH = _core_db
    except ImportError:
        DB_PATH = _root / "database.db"

log.info(f"[config] DB_PATH = {DB_PATH}")

# ── FMS bootstrap ─────────────────────────────────────────────────────────────
try:
    from core.fms.bootstrap import bootstrap_fms
    bootstrap_fms(verbose=False)
    log.info("[FMS] Bootstrap complete")
except Exception as _fms_err:
    log.warning(f"[FMS] Bootstrap failed (non-fatal): {_fms_err}")

# ── Sovereign Threshold constants ─────────────────────────────────────────────
# Minimum conditions before WikiCompiler is allowed to run.
# All three must be satisfied simultaneously.
SOVEREIGN_MIN_SIGNALS   = 10    # signals with processed_at IS NOT NULL
SOVEREIGN_MIN_ACTORS    = 3     # rows in actors table
SOVEREIGN_MIN_ENTITIES  = 50    # rows in signal_entities (NER output)

# ── Import reusable pipeline functions from mega_ingest ───────────────────────
# We reuse every function rather than copy — sovereign_pipeline is an
# orchestration layer, not a replacement.
try:
    from tools.mega_ingest import (
        run_all_collectors,
        run_engines_processors,
        run_full_ingest,
        bridge_ner_to_actors,
        bridge_dork_to_cases,
        bridge_pdf_signals_to_cases,
        bridge_cooccurrence_to_relationships,
        _log_pipeline_run,
        _safe_import,
    )
    log.info("[import] mega_ingest functions loaded")
except ImportError as _e:
    log.error(f"[import] Cannot import mega_ingest: {_e}")
    sys.exit(1)

# ── Engine classes (used in Phase 3 and Wave B re-runs) ──────────────────────
TripleExtractor = _safe_import("forage.processors.triple_extractor", "TripleExtractor")
GraphEngine     = _safe_import("forage.engines.graph_engine",        "GraphEngine")

# ── Dork collector (Wave B — isolated from Wave A) ────────────────────────────
try:
    from forage.collectors.dork_collector import collect as _dork_collect
    _dork_available = True
    log.info("[collector] Dork collector loaded for Wave B")
except ImportError as _e:
    _dork_collect    = None
    _dork_available  = False
    log.warning(f"[collector] Dork not importable (Wave B disabled): {_e}")

# ── sanitize_text — the Bleach Protocol ──────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
    log.info("[bleach] sanitize_text loaded from core.pipeline.ingest")
except ImportError:
    log.warning("[bleach] sanitize_text not importable — Phase 2 bleach pass disabled")
    _sanitize = None  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 — System Decontamination  (Day Zero only)
# ══════════════════════════════════════════════════════════════════════════════

def run_sterilization() -> dict:
    """
    Run system_decontamination.run_migration() to separate live/seed layers.

    GUARD: Only runs when --day-zero is passed. Never scheduled. Running this
    on a mature DB resets source_type on every row — newly-created live actors
    and signals would be misclassified as 'seed'.

    Safe to run on a fresh Day Zero DB (tables exist, rows present from
    --init-db + fix_schema.py, but no live signals yet).
    """
    log.info("=" * 60)
    log.info("PHASE 0 — SYSTEM DECONTAMINATION (Day Zero)")
    log.info("=" * 60)
    try:
        from maintenance.system_decontamination import run_migration
        run_migration(DB_PATH)
        log.info("[decontamination] Live/seed layers separated.")
        return {"status": "ok"}
    except Exception as exc:
        log.error(f"[decontamination] FAILED: {exc}")
        return {"status": "error", "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Bleach Safety Net
# ══════════════════════════════════════════════════════════════════════════════

def run_bleach_pass() -> dict:
    """
    Bulk-sanitize any signal title/content that bypassed collector-level cleaning.

    civic_intel and gdelt call sanitize_text() at write time. ACLED, RSS,
    Disease Outbreak, and PDF Infiltrator do NOT — this is tracked as tech debt
    but not patched at the collector level yet.

    This pass queries all signals with processed_at IS NULL (not yet through
    Conclave) and applies sanitize_text() to title and content in-place.
    Skips signals where both fields are already clean (no HTML tags, no
    &-entities, no %-encoding).

    Runs AFTER Wave A collection and BEFORE NER so that signal_entities
    are built from clean text.
    """
    if _sanitize is None:
        log.warning("[bleach] sanitize_text unavailable — skipping bleach pass")
        return {"status": "skipped", "cleaned": 0}

    log.info("[bleach] Running Bleach Safety Net on unprocessed signals...")
    start  = time.monotonic()
    # Pattern that indicates a signal needs cleaning
    import re
    _dirty = re.compile(r'<[^>]{0,500}>|&[a-zA-Z]+;|%[0-9A-Fa-f]{2}')

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    try:
        rows = conn.execute("""
            SELECT signal_id, title, content
            FROM   signals
            WHERE  processed_at IS NULL
              AND  (title IS NOT NULL OR content IS NOT NULL)
        """).fetchall()

        cleaned = 0
        batch   = []

        for row in rows:
            sid            = row["signal_id"]
            orig_title     = row["title"]   or ""
            orig_content   = row["content"] or ""
            needs_clean    = (
                bool(_dirty.search(orig_title)) or
                bool(_dirty.search(orig_content))
            )
            if not needs_clean:
                continue

            clean_title   = _sanitize(orig_title)
            clean_content = _sanitize(orig_content)
            batch.append((clean_title, clean_content, sid))
            cleaned += 1

        if batch:
            conn.executemany(
                "UPDATE signals SET title = ?, content = ? WHERE signal_id = ?",
                batch,
            )
            conn.commit()

        duration = round(time.monotonic() - start, 2)
        log.info(
            f"[bleach] {cleaned}/{len(rows)} signals cleaned in {duration}s"
        )
        return {
            "status":   "ok",
            "scanned":  len(rows),
            "cleaned":  cleaned,
            "duration_s": duration,
        }
    except Exception as exc:
        conn.rollback()
        log.error(f"[bleach] FAILED: {exc}")
        return {"status": "error", "error": str(exc), "cleaned": 0}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1B — Wave B Collection  (Dork, actor-dependent)
# ══════════════════════════════════════════════════════════════════════════════

async def run_wave_b() -> dict:
    """
    Run the Dork Collector AFTER Conclave has materialized actors.

    Dork queries actor_network_metrics.influence_score > 0 (primary) or
    signals.gravity_score > 0.6 (fallback). Both require Conclave to have
    run first and the graph_engine to have computed influence scores.

    Running Dork in Wave A on a cold DB yields 0 qualifying actors and
    wastes the entire 20-actor / 1-req-per-second quota on empty queries.
    """
    if not _dork_available or _dork_collect is None:
        log.warning("[wave_b] Dork collector not available — skipping")
        return {"status": "skipped", "reason": "dork not importable"}

    log.info("[wave_b] Running Dork Collector (actor-dependent)...")
    start = time.monotonic()
    try:
        result = await _dork_collect(db_path=DB_PATH)
        duration = round(time.monotonic() - start, 2)
        inserted = result.get("inserted", result.get("records_out", "?")) if isinstance(result, dict) else "?"
        log.info(f"[wave_b] Dork complete in {duration}s — {inserted} signals")
        return result if isinstance(result, dict) else {"status": "ok", "duration_s": duration}
    except Exception as exc:
        log.error(f"[wave_b] Dork FAILED: {exc}")
        return {"status": "error", "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 7 — Sovereign Gate + Wiki Synthesis
# ══════════════════════════════════════════════════════════════════════════════

def sovereign_threshold_met() -> tuple[bool, dict]:
    """
    Check all three Sovereign Threshold conditions before triggering the
    WikiCompiler and a final TripleExtractor pass.

    Returns (met: bool, stats: dict) where stats describes the live state.

    Conditions (all three must be True):
      1. processed_signals  ≥ SOVEREIGN_MIN_SIGNALS   (Conclave has run)
      2. actors count       ≥ SOVEREIGN_MIN_ACTORS    (entities materialized)
      3. signal_entities    ≥ SOVEREIGN_MIN_ENTITIES  (NER has populated)

    These thresholds are conservative. On a full operational run all three
    are satisfied by Phase 5. The gate only trips on dry-runs or isolated
    test environments where no real data flows.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row

        processed_signals = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE processed_at IS NOT NULL"
        ).fetchone()[0]

        actors_count = conn.execute(
            "SELECT COUNT(*) FROM actors"
        ).fetchone()[0]

        try:
            entities_count = conn.execute(
                "SELECT COUNT(*) FROM signal_entities"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            entities_count = 0   # table not yet created

        conn.close()

        stats = {
            "processed_signals": processed_signals,
            "actors":            actors_count,
            "signal_entities":   entities_count,
        }

        met = (
            processed_signals >= SOVEREIGN_MIN_SIGNALS  and
            actors_count      >= SOVEREIGN_MIN_ACTORS   and
            entities_count    >= SOVEREIGN_MIN_ENTITIES
        )

        if met:
            log.info(
                f"[sovereign] Threshold MET — "
                f"signals={processed_signals}/{SOVEREIGN_MIN_SIGNALS}  "
                f"actors={actors_count}/{SOVEREIGN_MIN_ACTORS}  "
                f"entities={entities_count}/{SOVEREIGN_MIN_ENTITIES}"
            )
        else:
            log.info(
                f"[sovereign] Threshold NOT met — "
                f"signals={processed_signals}/{SOVEREIGN_MIN_SIGNALS}  "
                f"actors={actors_count}/{SOVEREIGN_MIN_ACTORS}  "
                f"entities={entities_count}/{SOVEREIGN_MIN_ENTITIES}  "
                f"(wiki synthesis skipped)"
            )
        return met, stats

    except Exception as exc:
        log.error(f"[sovereign] threshold check failed: {exc}")
        return False, {}


def run_wiki_synthesis() -> dict:
    """
    Three-stage wiki synthesis: schema_init → WikiCompiler → WikiLinkEngine.

    Only called when sovereign_threshold_met() returns True.
    Mirrors the in-process logic of /api/control/run_wiki_pipeline.
    """
    log.info("[wiki] Starting wiki synthesis pipeline...")
    start   = time.monotonic()
    articles = 0

    try:
        from core.db.wiki import init_wiki_db
        init_wiki_db()
        log.info("[wiki] Stage 1/3 — schema ready")
    except Exception as exc:
        log.warning(f"[wiki] schema_init failed (non-fatal): {exc}")

    try:
        from wiki.processors.wiki_compiler import WikiCompiler
        WikiCompiler(DB_PATH).run()
        log.info("[wiki] Stage 2/3 — dossiers compiled")
    except Exception as exc:
        log.error(f"[wiki] wiki_compiler FAILED: {exc}")
        return {"status": "error", "stage": "wiki_compiler", "error": str(exc)}

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        articles = int(conn.execute(
            "SELECT COUNT(*) FROM wiki_articles"
        ).fetchone()[0])
        conn.close()
    except Exception:
        pass

    try:
        from wiki.engines.wiki_link_engine import WikiLinkEngine
        WikiLinkEngine(DB_PATH).run()
        log.info(f"[wiki] Stage 3/3 — cross-reference graph built ({articles} articles)")
    except Exception as exc:
        log.error(f"[wiki] link_engine FAILED: {exc}")
        return {
            "status":   "partial",
            "articles": articles,
            "error":    str(exc),
        }

    duration = round(time.monotonic() - start, 2)
    return {
        "status":       "ok",
        "articles":     articles,
        "duration_s":   duration,
    }


def run_post_dork_enrichment() -> dict:
    """
    Phase 6: Re-run TripleExtractor and GraphEngine after Wave B (Dork)
    introduces new signals and dork_actor actor-case links.

    TripleExtractor catches any artifact-linked signals from dork queries.
    GraphEngine recalculates influence scores with new edges from co-occurrence
    bridge, updating the scoring available to the next Dork run or the
    WikiCompiler entity sort.
    """
    results = {}

    if TripleExtractor is not None:
        log.info("[post_dork] Re-running TripleExtractor...")
        try:
            te = TripleExtractor(db_path=DB_PATH)
            results["triple_extractor_2"] = te.run()
            log.info("[post_dork] TripleExtractor pass 2 complete")
        except Exception as exc:
            log.warning(f"[post_dork] TripleExtractor failed (non-fatal): {exc}")
            results["triple_extractor_2"] = {"status": "error", "error": str(exc)}

    if GraphEngine is not None:
        log.info("[post_dork] Re-running GraphEngine (influence score refresh)...")
        try:
            ge = GraphEngine(db_path=DB_PATH)
            results["graph_engine_2"] = ge.run(recalculate=True)
            log.info("[post_dork] GraphEngine influence scores refreshed")
        except Exception as exc:
            log.warning(f"[post_dork] GraphEngine failed (non-fatal): {exc}")
            results["graph_engine_2"] = {"status": "error", "error": str(exc)}

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5B — FLUX Wave  (SOCINT corpus → resonance → discovery)
# ══════════════════════════════════════════════════════════════════════════════

# Corpus preflight thresholds — must match flux/processors/stylometric.py
_FLUX_CORPUS_MIN_ITEMS  = 7     # samples per actor
_FLUX_CORPUS_MIN_CHARS  = 2000  # chars per actor corpus
_FLUX_MIN_READY_ACTORS  = 2     # need at least 2 to compute any pairs
_FLUX_MAX_ACTOR_PAIRS   = 50000 # n*(n-1)/2 — safety cap against O(n²) explosion
                                 # 50k pairs ≈ 317 actors — well inside safe zone


def _flux_corpus_preflight() -> dict:
    """
    Check whether the FLUX resonance engine has enough corpus material to run.

    Returns a dict with:
        ready          (bool)  — True iff resonance is safe to execute
        socint_signals (int)   — total rows in socint_signals
        actors_with_profile (int) — actors with non-NULL socint_profile
        actors_ready   (int)   — actors whose corpus passes the readiness gate
        pair_count     (int)   — n*(n-1)/2 where n = actors_ready
        skip_reason    (str | None) — human-readable reason when ready=False
    """
    try:
        import json as _json
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row

        socint_count = conn.execute(
            "SELECT COUNT(*) FROM socint_signals"
        ).fetchone()[0]

        profile_rows = conn.execute(
            "SELECT socint_profile FROM actors WHERE socint_profile IS NOT NULL"
        ).fetchall()
        actors_with_profile = len(profile_rows)

        # Count actors whose corpus passes the readiness gate
        ready_count = 0
        for row in profile_rows:
            try:
                profile = _json.loads(row["socint_profile"])
                corpus  = profile.get("corpus", [])
                total_chars = sum(len(t) for t in corpus)
                if (len(corpus) >= _FLUX_CORPUS_MIN_ITEMS and
                        total_chars >= _FLUX_CORPUS_MIN_CHARS):
                    ready_count += 1
            except Exception:
                pass

        conn.close()

        pair_count = ready_count * (ready_count - 1) // 2 if ready_count > 1 else 0

        if socint_count == 0:
            return {
                "ready": False, "socint_signals": 0,
                "actors_with_profile": 0, "actors_ready": 0,
                "pair_count": 0,
                "skip_reason": "no socint_signals — run flux/collectors/x_pulse.py first",
            }

        if ready_count < _FLUX_MIN_READY_ACTORS:
            return {
                "ready": False, "socint_signals": socint_count,
                "actors_with_profile": actors_with_profile,
                "actors_ready": ready_count,
                "pair_count": pair_count,
                "skip_reason": (
                    f"only {ready_count}/{_FLUX_MIN_READY_ACTORS} actors "
                    f"have corpus-ready profiles "
                    f"(need >={_FLUX_CORPUS_MIN_ITEMS} samples + "
                    f">={_FLUX_CORPUS_MIN_CHARS} chars each)"
                ),
            }

        if pair_count > _FLUX_MAX_ACTOR_PAIRS:
            return {
                "ready": False, "socint_signals": socint_count,
                "actors_with_profile": actors_with_profile,
                "actors_ready": ready_count,
                "pair_count": pair_count,
                "skip_reason": (
                    f"O(n²) explosion risk: {pair_count} pairs exceeds "
                    f"safety cap {_FLUX_MAX_ACTOR_PAIRS} — "
                    f"run resonance.py manually with --batch flag"
                ),
            }

        return {
            "ready": True, "socint_signals": socint_count,
            "actors_with_profile": actors_with_profile,
            "actors_ready": ready_count,
            "pair_count": pair_count,
            "skip_reason": None,
        }

    except Exception as exc:
        return {
            "ready": False, "socint_signals": 0,
            "actors_with_profile": 0, "actors_ready": 0,
            "pair_count": 0,
            "skip_reason": f"preflight check failed: {exc}",
        }


def run_flux_wave(dry_run: bool = False) -> dict:
    """
    Phase 5B — FLUX SOCINT Wave.

    Executes after Conclave (Phase 5) + Bridge Pass B (Phase 6) because:
      • corpus_builder needs actors materialized by Conclave to map handles
      • resonance needs corpus_builder to have run (actors.socint_profile)
      • discovery needs flux_tag_cooccurrence (written by x_pulse)

    Execution order (all non-fatal — failure in one step does not block next):
      5B.1  Corpus preflight check (O(1) — guards against O(n²) explosion)
      5B.2  corpus_builder — bridge socint_signals → actors.socint_profile
      5B.3  resonance      — pairwise stylometric comparison (O(n²))
      5B.4  discovery      — Jaccard + velocity → flux_latent_seeds

    Graceful skip conditions:
      • No socint_signals rows (x_pulse has never run)
      • Fewer than 2 actors with corpus-ready profiles after corpus_builder
      • Pair count exceeds _FLUX_MAX_ACTOR_PAIRS safety cap
    """
    log.info("=" * 60)
    log.info("PHASE 5B — FLUX SOCINT WAVE")
    log.info("=" * 60)

    results: dict = {}
    start = time.monotonic()

    # ── 5B.1  Corpus preflight ─────────────────────────────────────────────
    log.info("[flux] Running corpus preflight check...")
    preflight = _flux_corpus_preflight()
    results["preflight"] = preflight
    log.info(
        f"[flux] socint_signals={preflight['socint_signals']}  "
        f"actors_with_profile={preflight['actors_with_profile']}  "
        f"corpus_ready={preflight['actors_ready']}  "
        f"pairs={preflight['pair_count']}"
    )

    if not preflight["ready"]:
        log.info(f"[flux] SKIP — {preflight['skip_reason']}")
        return {
            "status":   "skipped",
            "reason":   preflight["skip_reason"],
            "preflight": preflight,
        }

    # ── 5B.2  corpus_builder ───────────────────────────────────────────────
    log.info("[flux] Running corpus_builder (socint_signals → socint_profile)...")
    try:
        from flux.processors.corpus_builder import run as _corpus_run
        cb_result = _corpus_run(dry_run=dry_run)
        results["corpus_builder"] = cb_result
        log.info(f"[flux] corpus_builder: {cb_result}")
    except ImportError as exc:
        log.warning(f"[flux] corpus_builder not importable (non-fatal): {exc}")
        results["corpus_builder"] = {"status": "skipped", "error": str(exc)}
    except Exception as exc:
        log.error(f"[flux] corpus_builder FAILED (non-fatal): {exc}")
        results["corpus_builder"] = {"status": "error", "error": str(exc)}

    # Re-run preflight after corpus_builder runs (it may have promoted actors)
    preflight2 = _flux_corpus_preflight()
    if not preflight2["ready"]:
        log.info(
            f"[flux] Post-corpus preflight: corpus still not ready "
            f"({preflight2['actors_ready']} ready actors). "
            f"Skipping resonance."
        )
        results["resonance"] = {"status": "skipped", "reason": preflight2["skip_reason"]}
        results["discovery"] = {"status": "skipped", "reason": "resonance skipped"}
        results["status"]    = "partial"
        results["duration_s"] = round(time.monotonic() - start, 2)
        return results

    # ── 5B.3  resonance (O(n²) pairwise stylometric comparison) ───────────
    log.info(
        f"[flux] Running resonance ({preflight2['actors_ready']} actors, "
        f"{preflight2['pair_count']} pairs)..."
    )
    try:
        from flux.processors.resonance import run as _resonance_run
        res_result = _resonance_run(db_path=DB_PATH, dry_run=dry_run)
        results["resonance"] = res_result
        log.info(f"[flux] resonance: {res_result}")
    except ImportError as exc:
        log.warning(f"[flux] resonance not importable (non-fatal): {exc}")
        results["resonance"] = {"status": "skipped", "error": str(exc)}
    except Exception as exc:
        log.error(f"[flux] resonance FAILED (non-fatal): {exc}")
        results["resonance"] = {"status": "error", "error": str(exc)}

    # ── 5B.4  discovery (Jaccard + velocity → flux_latent_seeds) ──────────
    log.info("[flux] Running discovery (tag co-occurrence → latent seeds)...")
    try:
        from flux.processors.discovery import run as _discovery_run
        disc_result = _discovery_run(db_path=DB_PATH, dry_run=dry_run)
        results["discovery"] = disc_result
        log.info(f"[flux] discovery: {disc_result}")
    except ImportError as exc:
        log.warning(f"[flux] discovery not importable (non-fatal): {exc}")
        results["discovery"] = {"status": "skipped", "error": str(exc)}
    except Exception as exc:
        log.error(f"[flux] discovery FAILED (non-fatal): {exc}")
        results["discovery"] = {"status": "error", "error": str(exc)}

    results["status"]     = "done"
    results["duration_s"] = round(time.monotonic() - start, 2)
    log.info(f"[flux] Phase 5B complete in {results['duration_s']}s")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# CLI argument parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FORGE Sovereign Pipeline — Day Zero to Finished Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--day-zero", action="store_true",
        help=(
            "First-run mode: run System Decontamination (Phase 0) to separate "
            "live/seed layers before any collection. ONE-SHOT — do not use on "
            "a mature database."
        ),
    )
    p.add_argument(
        "--collect-only", action="store_true",
        help="Run Wave A collection only. Skip engines, Conclave, and Dork.",
    )
    p.add_argument(
        "--no-dork", action="store_true",
        help="Skip Wave B (Dork Collector). Useful for mid-session quick passes.",
    )
    p.add_argument(
        "--no-flux", action="store_true",
        help="Skip Phase 5B FLUX SOCINT wave (corpus_builder + resonance + discovery).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Collect but do not write signals to DB.",
    )
    p.add_argument(
        "--reprocess-all", action="store_true",
        help="Force Conclave to reprocess ALL signals, not just unprocessed ones.",
    )
    p.add_argument(
        "--batch-size", type=int, default=50,
        help="Conclave ingest batch size (default: 50).",
    )
    p.add_argument(
        "--sovereign-min-signals", type=int, default=SOVEREIGN_MIN_SIGNALS,
        help=f"Override Sovereign Threshold min processed signals (default: {SOVEREIGN_MIN_SIGNALS}).",
    )
    p.add_argument(
        "--sovereign-min-actors", type=int, default=SOVEREIGN_MIN_ACTORS,
        help=f"Override Sovereign Threshold min actors (default: {SOVEREIGN_MIN_ACTORS}).",
    )
    p.add_argument(
        "--sovereign-min-entities", type=int, default=SOVEREIGN_MIN_ENTITIES,
        help=f"Override Sovereign Threshold min signal_entities (default: {SOVEREIGN_MIN_ENTITIES}).",
    )
    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = _parse_args()

    # Allow threshold overrides from CLI
    SOVEREIGN_MIN_SIGNALS  = args.sovereign_min_signals
    SOVEREIGN_MIN_ACTORS   = args.sovereign_min_actors
    SOVEREIGN_MIN_ENTITIES = args.sovereign_min_entities

    pipeline_start = time.time()
    results: dict = {}

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║  S O V E R E I G N   P I P E L I N E   ONLINE       ║")
    log.info("╚══════════════════════════════════════════════════════╝")
    if args.day_zero:
        log.info("MODE: DAY ZERO — sterilization enabled")
    if args.dry_run:
        log.info("MODE: DRY RUN — no DB writes")

    # ── Phase 0 — System Decontamination  [--day-zero only] ──────────────────
    if args.day_zero:
        log.info("\n── PHASE 0: SYSTEM DECONTAMINATION ─────────────────────")
        results["phase_0_sterilization"] = run_sterilization()

    # ── Phase 1A — Wave A Collection  [concurrent, no-auth + credentialed] ──
    log.info("\n── PHASE 1A: WAVE A COLLECTION ─────────────────────────")
    log.info(
        "Order rationale:\n"
        "  GDELT        — no-auth, 1 req/s, 5 queries, fastest reliable SA signal source\n"
        "  Civic Intel  — no-auth, RSS/GNews, 12 SA investigative outlets\n"
        "  RSS (GDACS)  — no-auth, single feed, near-zero latency\n"
        "  Disease OB   — no-auth, multi-tier WHO/PAHO/HealthMap, medium latency\n"
        "  PDF Infiltr. — no-auth, portal crawl + OCR, slowest (runs concurrently)\n"
        "  ACLED        — CREDENTIAL-GATED, skipped silently if env vars absent"
    )
    collect_result = asyncio.run(
        run_all_collectors(dry_run=args.dry_run)
    )
    results["phase_1a_wave_a"] = collect_result

    if args.dry_run or args.collect_only:
        log.info("[sovereign] Stopping after Wave A (--dry-run / --collect-only)")
        _log_pipeline_run(
            "sovereign_pipeline", "ok",
            0, 0,
            round(time.time() - pipeline_start, 2),
            {"mode": "collect_only", "collect": collect_result},
        )
        sys.exit(0)

    # ── Phase 2 — Bleach Safety Net ──────────────────────────────────────────
    log.info("\n── PHASE 2: BLEACH SAFETY NET ──────────────────────────")
    log.info(
        "Sanitizing signals from collectors that lack collector-level bleaching\n"
        "(ACLED, RSS, Disease Outbreak, PDF Infiltrator). NER will receive\n"
        "clean text regardless of collector source."
    )
    results["phase_2_bleach"] = run_bleach_pass()

    # ── Phase 3 — Refine & Enrich  [sequential, dependency-ordered] ─────────
    log.info("\n── PHASE 3: REFINE & ENRICH ────────────────────────────")
    log.info(
        "Engine dependency order:\n"
        "  3.1 artifact_processor  → extract text from uploaded artifacts\n"
        "  3.2 ner_processor       → signal_entities (required by bridge + wiki)\n"
        "  3.3 cluster_engine      → cluster_id (required by Sentinel Rule 3)\n"
        "  3.4 anomaly_engine      → baselines (required by correlation)\n"
        "  3.5 correlation_engine  → correlated_incidents (Sentinel Rule 1)\n"
        "  3.6 decay_engine        → relevance decay\n"
        "  3.7 evolution_engine    → discovery_targets\n"
        "  3.8 triple_extractor    → entity_relationships (before graph = Factor 2)\n"
        "  3.9 graph_engine        → actor_network_metrics (required by Dork)\n"
        "  3.10 sentinel           → threshold alerts"
    )
    engine_result = run_engines_processors()
    results["phase_3_engines"] = engine_result

    # ── Phase 4 — Bridge Pass A  [NER → actor/case graph] ───────────────────
    log.info("\n── PHASE 4: BRIDGE PASS A ──────────────────────────────")
    results["phase_4_bridge_ner"]  = bridge_ner_to_actors()
    results["phase_4_bridge_pdf"]  = bridge_pdf_signals_to_cases()
    results["phase_4_bridge_cooc"] = bridge_cooccurrence_to_relationships()
    # Note: bridge_dork_to_cases intentionally omitted here —
    # Dork hasn't run yet. It runs in Bridge Pass B after Wave B.

    # ── Phase 5 — Conclave  [unprocessed signals, priority-first] ───────────
    log.info("\n── PHASE 5: CONCLAVE ───────────────────────────────────")
    ingest_result = run_full_ingest(
        batch_size=args.batch_size,
        sleep_interval=0.1,
        reprocess_all=args.reprocess_all,
    )
    results["phase_5_conclave"] = ingest_result

    # ── Phase 1B — Wave B: Dork  [actor-dependent] ───────────────────────────
    if not args.no_dork:
        log.info("\n── PHASE 1B: WAVE B — DORK COLLECTOR ──────────────────")
        log.info(
            "Runs AFTER Conclave because:\n"
            "  • actor_network_metrics requires GraphEngine (Phase 3.9)\n"
            "  • gravity_score > 0.6 fallback requires Conclave (Phase 5)\n"
            "  • On a cold DB, Wave A Dork finds 0 qualifying actors"
        )
        wave_b_result = asyncio.run(run_wave_b())
        results["phase_1b_wave_b_dork"] = wave_b_result
    else:
        log.info("[sovereign] Wave B (Dork) skipped (--no-dork)")
        results["phase_1b_wave_b_dork"] = {"status": "skipped"}

    # ── Phase 6 — Bridge Pass B  [absorb Dork output] ────────────────────────
    log.info("\n── PHASE 6: BRIDGE PASS B + POST-DORK ENRICHMENT ──────")
    results["phase_6_bridge_dork"] = bridge_dork_to_cases()
    results["phase_6_enrichment"]  = run_post_dork_enrichment()

    # ── Phase 5B — FLUX SOCINT Wave  [corpus → resonance → discovery] ────────
    # Runs after Phase 6 (not before) so it has the final actor graph state:
    #   • actor_network_metrics populated (Phase 3.9 + Phase 6 refresh)
    #   • Dork signals absorbed into case_signals (Phase 6)
    # Gracefully skips when x_pulse has not been run (socint_signals empty).
    if not args.no_flux:
        log.info("\n── PHASE 5B: FLUX SOCINT WAVE ──────────────────────────")
        log.info(
            "Executes AFTER Bridge Pass B because:\n"
            "  • corpus_builder needs Conclave-materialized actors\n"
            "  • resonance O(n^2) must not block ingestion\n"
            "  • discovery reads flux_tag_cooccurrence (from x_pulse)\n"
            "  Gracefully skips when x_pulse corpus is not ready."
        )
        results["phase_5b_flux"] = run_flux_wave(dry_run=args.dry_run)
    else:
        log.info("[sovereign] Phase 5B (FLUX) skipped (--no-flux)")
        results["phase_5b_flux"] = {"status": "skipped"}

    # ── Phase 7 — Sovereign Gate + Wiki Synthesis ────────────────────────────
    log.info("\n── PHASE 7: SOVEREIGN GATE ─────────────────────────────")
    threshold_met, threshold_stats = sovereign_threshold_met()
    results["phase_7_threshold"] = threshold_stats

    if threshold_met:
        log.info("[sovereign] Gate OPEN — running wiki synthesis pipeline")
        results["phase_7_wiki"] = run_wiki_synthesis()
    else:
        log.info("[sovereign] Gate CLOSED — wiki synthesis deferred")
        results["phase_7_wiki"] = {"status": "deferred"}

    # ── Phase 8 — Telemetry ───────────────────────────────────────────────────
    total_duration = round(time.time() - pipeline_start, 2)
    log.info("\n── PHASE 8: TELEMETRY ──────────────────────────────────")

    errors = [k for k, v in results.items()
              if isinstance(v, dict) and v.get("status") == "error"]
    overall_status = "error" if errors else "success"

    _log_pipeline_run(
        component="sovereign_pipeline",
        status=overall_status,
        records_in=ingest_result.get("total", 0),
        records_out=ingest_result.get("processed", 0),
        duration_s=total_duration,
        detail={k: (v.get("status") if isinstance(v, dict) else str(v))
                for k, v in results.items()},
    )

    # ── Final summary ─────────────────────────────────────────────────────────
    wiki_articles = results.get("phase_7_wiki", {}).get("articles", 0)
    dork_inserted = (
        results.get("phase_1b_wave_b_dork", {}).get("inserted") or
        results.get("phase_1b_wave_b_dork", {}).get("records_out") or 0
    )
    bleach_cleaned  = results.get("phase_2_bleach", {}).get("cleaned", 0)
    flux_result     = results.get("phase_5b_flux", {})
    flux_status     = flux_result.get("status", "skipped")
    flux_res_pairs  = (flux_result.get("resonance") or {}).get("pairs_written", "—")
    flux_seeds      = (flux_result.get("discovery") or {}).get("promoted", "—")

    print(f"\n{'=' * 56}")
    print("  SOVEREIGN PIPELINE -- COMPLETE")
    print(f"{'=' * 56}")
    print(f"  Total runtime:         {total_duration}s")
    print(f"  Overall status:        {overall_status.upper()}")
    print(f"  --- Collection -------------------------------------------")
    print(f"  Wave A collectors:     {collect_result.get('collectors_run', 0)}")
    print(f"  Wave A errors:         {len(collect_result.get('errors', []))}")
    print(f"  Wave B (Dork) signals: {dork_inserted}")
    print(f"  --- Refinement -------------------------------------------")
    print(f"  Bleach cleaned:        {bleach_cleaned} signals")
    print(f"  --- Conclave ---------------------------------------------")
    print(f"  Signals processed:     {ingest_result.get('processed', 0)}")
    print(f"  Priority signals:      {ingest_result.get('priority_processed', 0)}")
    print(f"  New actors:            {ingest_result.get('db_actors_live', 0)}")
    print(f"  Events created:        {ingest_result.get('db_events', 0)}")
    print(f"  Cases opened:          {ingest_result.get('db_cases', 0)}")
    print(f"  --- FLUX SOCINT ------------------------------------------")
    print(f"  FLUX wave status:      {flux_status}")
    print(f"  Resonance pairs:       {flux_res_pairs}")
    print(f"  Latent seeds promoted: {flux_seeds}")
    print(f"  --- Intelligence -----------------------------------------")
    print(f"  Wiki articles:         {wiki_articles}")
    print(f"  Sovereign threshold:   {'MET' if threshold_met else 'NOT MET'}")
    if errors:
        print(f"  --- Errors -----------------------------------------------")
        for e in errors:
            print(f"  FAIL  {e}")
    print(f"{'=' * 56}\n")

    sys.exit(0 if overall_status != "error" else 1)
