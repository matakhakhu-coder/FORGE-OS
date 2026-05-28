#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Conclave Engine  (forge_modules/flux/engine.py)
═════════════════════════════════════════════════════════════
FMS engine function consumed by run_conclave_with_modules().

Contract
────────
  run(signal) → AnalysisResult | None

  Returns None  for any signal that is NOT from x_pulse.
  Returns an AnalysisResult for x_pulse signals, contributing SOCINT
  context to the Conclave merge:
    - Entities derived from cashtag and hashtag presence
    - Gravity calibrated to behavioural indicators (lower than OSINT events)
    - Recommendation reflects evasion/manipulation pattern detection

Design note
───────────
  This engine intentionally produces LOW gravity scores. SOCINT signals
  are behavioural — they flag patterns, not events. Keeping gravity low
  prevents FLUX from inflating escalation rates for posts that are merely
  suspicious in style but not confirmed threats.

  The real analytical power of FLUX lives in the on_ingest hook (corpus
  accumulation + pairwise resonance) and the graph engine's C-SOCINT
  community pass — not here.
"""

import re
from typing import Any, Dict, Optional

from core.conclave.registry import AnalysisResult

# ── Lightweight feature patterns (no import of stylometric.py needed here) ───
# engine.py must be importable with zero side-effects for FMS readiness checks.

_CASHTAG_RE    = re.compile(r'\$[A-Z]{1,6}\b')
_LEET_CHARS    = frozenset("013457@")
_AGGR_BANG_RE  = re.compile(r'!{3,}')
_AGGR_INTER_RE = re.compile(r'\?!')

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000027BF"
    "]",
    flags=re.UNICODE,
)


def _quick_features(text: str) -> Dict[str, Any]:
    """
    Lightweight feature extraction — a subset of the full stylometric
    fingerprint, computed without importing the processor module.
    """
    cashtags  = _CASHTAG_RE.findall(text.upper())
    emojis    = _EMOJI_RE.findall(text)
    leet_d    = sum(1 for c in text if c in _LEET_CHARS) / max(len(text), 1)
    bang_d    = (text.count("!") + len(_AGGR_BANG_RE.findall(text)) * 2
                 + len(_AGGR_INTER_RE.findall(text))) / max(len(text), 1)
    return {
        "cashtags":     cashtags,
        "emojis":       emojis,
        "leet_density": leet_d,
        "aggression":   bang_d,
    }


def run(signal: Dict[str, Any]) -> Optional[AnalysisResult]:
    """
    FLUX Conclave engine. Called once per signal by run_conclave_with_modules.

    Returns None for all non-x_pulse signals — zero Conclave contribution,
    zero performance overhead on OSINT signals.

    Parameters
    ----------
    signal : dict
        The raw signal dict as produced by the collector.

    Returns
    -------
    AnalysisResult | None
    """
    if signal.get("source") != "x_pulse":
        return None

    text = " ".join([
        str(signal.get("title",   "")),
        str(signal.get("content", "")),
    ]).strip()

    if not text:
        return None

    feat = _quick_features(text)

    # ── Gravity: behavioural signal, deliberately capped low ─────────────────
    # Cashtag presence → potential market manipulation signal
    cash_factor  = min(len(feat["cashtags"]) * 0.08, 0.24)
    # Emoji density → visual coordination signal
    emoji_factor = min(len(feat["emojis"]) * 0.03, 0.12)
    # Leet + aggression → evasion / emotional manipulation
    evas_factor  = min(feat["leet_density"] * 2.0, 0.15)
    aggr_factor  = min(feat["aggression"]   * 8.0, 0.10)

    gravity = round(min(cash_factor + emoji_factor + evas_factor + aggr_factor, 0.55), 4)

    if gravity >= 0.35:
        recommendation = "MONITOR"
    else:
        recommendation = "IGNORE"

    # ── Entities: map cashtags to readable instrument names ──────────────────
    # Cashtags ARE the entity signals in SOCINT — flag them explicitly.
    entities = [f"CASHTAG:{t}" for t in feat["cashtags"]]
    entities += [f"EMOJI_SIGNAL:{len(feat['emojis'])}"] if len(feat["emojis"]) >= 3 else []

    confidence = round(
        min(0.30 + len(feat["cashtags"]) * 0.08 + feat["leet_density"] * 0.5, 0.75),
        3,
    )

    return AnalysisResult(
        entities=entities,
        intent="socint_behavioral",
        gravity=gravity,
        recommendation=recommendation,
        confidence=confidence,
        provenance={
            "module":       "flux",
            "engine":       "flux_socint_engine",
            "cashtags":     feat["cashtags"],
            "emoji_count":  len(feat["emojis"]),
            "leet_density": round(feat["leet_density"], 4),
            "aggression":   round(feat["aggression"],   4),
        },
    )
