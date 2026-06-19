#!/usr/bin/env python3
from __future__ import annotations
"""
Revenue module configuration.

REVENUE_LIVE = False  →  simulation mode (all surfaces render, payments are no-ops)
REVENUE_LIVE = True   →  live mode (real payment links, gated content enforced)

This file is the ONLY place revenue behavior is configured.
If revenue/ directory is absent, publish.py works exactly as before.
"""

import os

# ── Master switch ────────────────────────────────────────────────────────────
REVENUE_LIVE: bool = os.environ.get("FORGE_REVENUE_LIVE", "").lower() == "true"

# ── Tier definitions ─────────────────────────────────────────────────────────
TIERS = {
    "free": {
        "label": "Public Bulletin",
        "signals_limit": 20,
        "case_detail": False,
        "entity_detail": False,
        "articles_limit": 3,
        "api_feed": False,
        "digest": False,
    },
    "pro": {
        "label": "Pro Intelligence",
        "signals_limit": None,
        "case_detail": True,
        "entity_detail": True,
        "articles_limit": None,
        "api_feed": True,
        "digest": True,
    },
}

# ── Membership button (Ko-fi / Buy Me a Coffee / custom URL) ────────────────
# Set to a URL to render the button. None = hidden.
MEMBERSHIP_URL: str | None = os.environ.get("FORGE_MEMBERSHIP_URL", "https://ko-fi.com/zadivergent")
MEMBERSHIP_LABEL: str = "Support Independent OSINT"

# ── Sponsor slots (rendered in map ticker + article footers) ─────────────────
# Each entry: {"text": "...", "url": "...", "label": "SPONSORED"}
# Empty list = no sponsor slots rendered.
SPONSOR_SLOTS: list[dict] = [
    {"text": "Protect your credentials with Bitwarden", "url": "https://bitwarden.com", "label": "SPONSORED"},
]

# ── Payment provider (deferred — no SDK until needed) ────────────────────────
PAYMENT_PROVIDER: str | None = os.environ.get("FORGE_PAYMENT_PROVIDER", None)
PAYMENT_PRODUCT_ID: str | None = os.environ.get("FORGE_PAYMENT_PRODUCT_ID", None)
PAYMENT_CHECKOUT_URL: str | None = os.environ.get("FORGE_PAYMENT_CHECKOUT_URL", None)

# ── Digest provider (deferred — Phase 7) ─────────────────────────────────────
DIGEST_PROVIDER: str | None = os.environ.get("FORGE_DIGEST_PROVIDER", None)
DIGEST_API_KEY: str | None = os.environ.get("FORGE_DIGEST_API_KEY", None)


def get_template_context() -> dict:
    """Build the revenue context dict passed to every Jinja2 template render."""
    return {
        "revenue_live": REVENUE_LIVE,
        "membership_url": MEMBERSHIP_URL if REVENUE_LIVE else None,
        "membership_label": MEMBERSHIP_LABEL,
        "membership_sim": MEMBERSHIP_URL and not REVENUE_LIVE,
        "sponsor_slots": SPONSOR_SLOTS,
        "payment_checkout_url": PAYMENT_CHECKOUT_URL if REVENUE_LIVE else None,
    }
