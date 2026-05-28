#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — Social Intelligence (SOCINT) Root
═══════════════════════════════════════════════
Parallel intelligence root to forage/ (OSINT).

Architecture
────────────
  flux/collectors/   — Source collectors (x_pulse, future: reddit_pulse, etc.)
  flux/processors/   — Stylometric engine, resonance scoring

Integration
───────────
  FLUX integrates with FORGE via the FMS on_ingest hook.
  See forge_modules/flux/ for the Conclave registration point.
  No changes to core/pipeline/ingest.py are required or permitted.

Environment variables
─────────────────────
  X_PULSE_MODE       — "nitter" (default) | "guest_api"
  X_BEARER_TOKEN     — Required for guest_api mode only
  X_PULSE_TARGETS    — Comma-separated: handle1,handle2,#hashtag1,$CASHTAG1
"""

__version__ = "0.1.0"
__root__    = "flux"
