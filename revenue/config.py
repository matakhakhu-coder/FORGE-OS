#!/usr/bin/env python3
from __future__ import annotations
"""
Revenue module configuration.

REVENUE_LIVE = False  →  simulation mode (support button and sponsor slots render as previews)
REVENUE_LIVE = True   →  live mode (support button links out)

There is no paid tier and nothing is gated: since 22 Oct 2026 every page,
the map and the Records download are free and open. The free/pro tier
table, the Paystack/EFT checkout settings and the paid API feed were
removed with the Access Tiers page.

This file is the ONLY place revenue behavior is configured.
If revenue/ directory is absent, publish.py works exactly as before.
"""

import os

# ── Master switch ────────────────────────────────────────────────────────────
REVENUE_LIVE: bool = os.environ.get("FORGE_REVENUE_LIVE", "true").lower() == "true"

# ── Membership button (Ko-fi / Buy Me a Coffee / custom URL) ────────────────
# Set to a URL to render the button. None = hidden.
MEMBERSHIP_URL: str | None = os.environ.get("FORGE_MEMBERSHIP_URL", "https://ko-fi.com/zadivergent")
MEMBERSHIP_LABEL: str = "Support Independent OSINT"

# ── Sponsor slots (rendered in map ticker + article footers) ─────────────────
# Each entry: {"text": "...", "url": "...", "label": "SPONSORED"}
# Empty list = no sponsor slots rendered.
SPONSOR_SLOTS: list[dict] = [
    {"text": "Turn your kid's art into real products — DreamPrint SA", "url": "https://dreamprint-web.vercel.app", "label": "SPONSORED"},
]

# ── Digest provider ──────────────────────────────────────────────────────────
DIGEST_PROVIDER: str | None = os.environ.get("FORGE_DIGEST_PROVIDER", None)
DIGEST_API_KEY: str | None = os.environ.get("FORGE_DIGEST_API_KEY", None)


def get_template_context() -> dict:
    """Build the revenue context dict passed to every Jinja2 template render."""
    return {
        "revenue_live": REVENUE_LIVE,
        "membership_url": MEMBERSHIP_URL if REVENUE_LIVE else None,
        "membership_label": MEMBERSHIP_LABEL,
        "membership_sim": bool(MEMBERSHIP_URL) and not REVENUE_LIVE,
        "sponsor_slots": SPONSOR_SLOTS,
    }
