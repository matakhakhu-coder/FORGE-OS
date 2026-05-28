#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — FMS Module Entry Point  (forge_modules/flux/module.py)
════════════════════════════════════════════════════════════════════
Standard FMS contract: single register(conclave) function, all imports
inside it, zero side-effects on module load.

What this module registers
──────────────────────────
  Engine  : flux_socint_engine
      → Called by run_conclave_with_modules() for every signal.
      → Returns None for non-x_pulse; returns AnalysisResult for x_pulse.

  Hook    : on_ingest
      → Fires AFTER Conclave completes for every ingested signal.
      → For x_pulse signals only:
          1. Extracts full stylometric fingerprint from signal content.
          2. Resolves linked actors via signal_actors join table.
          3. Appends content to each actor's rolling corpus
             (actors.socint_profile JSON).
          4. Scores signal against actor corpus when corpus is ready.
          5. Writes socint_resonance score + socint_tags back to the
             signals row so the graph engine can consume them.
      → Opens its own short-lived DB connection (timeout=10, try/finally).
      → Failures are logged at WARNING level — never propagated.

FMS Contract rules enforced
────────────────────────────
  • All imports happen ONCE inside register() — never inside hooks.
  • Hooks capture function references via closure.
  • No side effects on import of this file.
"""

import logging

log = logging.getLogger("forge.modules.flux")


def register(conclave) -> None:
    """
    Register the FLUX SOCINT module into the Conclave context.
    Called once at startup by attach_module() via bootstrap_fms().
    """
    import json
    import sqlite3
    from pathlib import Path

    # ── Single import pass ────────────────────────────────────────────────────
    from forge_modules.flux.engine import run as engine_run

    from flux.processors.stylometric import (
        extract_fingerprint,
        update_actor_corpus,
        corpus_from_profile,
        score_signal_against_corpus,
    )

    _DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "database.db")

    # ── Engine registration ───────────────────────────────────────────────────
    conclave.register_engine("flux_socint_engine", engine_run)

    # ── on_ingest hook ────────────────────────────────────────────────────────

    def on_ingest(signal: dict, result: dict) -> None:
        """
        Post-Conclave SOCINT enrichment hook.

        Runs only for x_pulse signals. For every other source this hook
        returns in O(1) with a single dict lookup — zero overhead on
        the OSINT pipeline.
        """
        if signal.get("source") != "x_pulse":
            return

        signal_id = signal.get("signal_id")
        content   = (signal.get("content") or "").strip()
        if not signal_id or not content:
            return

        try:
            conn = sqlite3.connect(_DB_PATH, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")

                # ── 1. Resolve linked actors from signal_actors ───────────────
                rows = conn.execute(
                    "SELECT actor_id FROM signal_actors WHERE signal_id = ?",
                    (signal_id,),
                ).fetchall()
                actor_ids = [r[0] for r in rows]

                # ── 2. Extract full fingerprint once ─────────────────────────
                fp   = extract_fingerprint(content)
                tags = {
                    "cashtags": fp["cashtags"],
                    "hashtags": fp["hashtags"],
                    "emojis":   fp["emojis"][:15],
                    "leet_density": fp["leet_density"],
                    "aggression":   fp["aggression"],
                }

                # ── 3. Per-actor corpus update + resonance scoring ────────────
                best_score: float | None = None

                for actor_id in actor_ids:
                    row = conn.execute(
                        "SELECT socint_profile FROM actors WHERE actor_id = ?",
                        (actor_id,),
                    ).fetchone()
                    profile_json = row[0] if row else None

                    # Append new text sample to rolling corpus
                    new_profile = update_actor_corpus(profile_json, content)
                    conn.execute(
                        "UPDATE actors SET socint_profile = ? WHERE actor_id = ?",
                        (new_profile, actor_id),
                    )

                    # Score against corpus only when gate passes
                    corpus = corpus_from_profile(new_profile)
                    score  = score_signal_against_corpus(content, corpus)
                    if score is not None:
                        if best_score is None or score > best_score:
                            best_score = score

                # ── 4. Write enrichment back to signals row ───────────────────
                conn.execute(
                    "UPDATE signals SET socint_tags = ?, socint_resonance = ? "
                    "WHERE signal_id = ?",
                    (
                        json.dumps(tags),
                        best_score,   # None if corpus not yet ready — stays NULL
                        signal_id,
                    ),
                )

                conn.commit()

                log.debug(
                    "[FLUX on_ingest] signal=%s  actors=%d  score=%s  "
                    "cashtags=%s  emojis=%d",
                    signal_id[:8],
                    len(actor_ids),
                    f"{best_score:.4f}" if best_score is not None else "pending",
                    fp["cashtags"],
                    len(fp["emojis"]),
                )

            finally:
                conn.close()

        except Exception as exc:
            log.warning(
                "[FLUX on_ingest] signal=%s enrichment failed: %s",
                signal_id[:8] if signal_id else "?",
                exc,
            )

    conclave.register_hook("on_ingest", on_ingest)

    log.info(
        "[flux] Registered — engine: flux_socint_engine | hook: on_ingest"
    )
