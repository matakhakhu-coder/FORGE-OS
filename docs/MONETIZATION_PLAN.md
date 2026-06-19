# FORGE Revenue Module — Implementation Plan

**Classification:** INTERNAL — DEVELOPMENT BRIEF  
**Status:** Pre-implementation  
**Constraint:** Zero financial dependencies. Everything runs in simulation mode with a switch (`REVENUE_LIVE = False`) that activates real payment/delivery integrations when providers are configured. Every module is pluggable — its absence does not break the pipeline.

---

## Architecture Principle

Revenue modules follow the same pattern as FORGE's existing systems:

```
FORGE Module System (FMS)         Revenue Module System (RMS)
─────────────────────────         ─────────────────────────────
forge_modules/<name>/             revenue/<name>/
  manifest.json                     manifest.json
  module.py                         module.py
  register(conclave)                register(publisher_context)

Hook: on_signal, on_ingest        Hook: on_publish, on_build, on_deploy
Failure isolation: yes            Failure isolation: yes
```

If `revenue/` directory doesn't exist, `publish.py` builds exactly as it does today. If it exists but a module fails, the publish continues — revenue modules cannot kill the build.

---

## Configuration

All revenue config lives in one file: `revenue/config.py`

```python
# ── Revenue Configuration ────────────────────────────────────────────────
# Set REVENUE_LIVE = True when payment/delivery providers are configured.
# In simulation mode, all revenue surfaces render but payments are no-ops
# and gated content is accessible with a banner: "This content requires
# a subscription. [Simulation mode — access granted]"

REVENUE_LIVE = False

# ── Tier definitions ─────────────────────────────────────────────────────
TIERS = {
    "free": {
        "label": "Public Bulletin",
        "signals_limit": 20,        # most recent N signals on timeline
        "cases_visible": True,      # case list visible, but detail gated
        "case_detail": False,       # case detail pages gated
        "entity_profiles": True,    # entity directory visible
        "entity_detail": False,     # full profile gated
        "graph": True,              # graph visible
        "map": True,                # map visible
        "articles": 3,              # most recent N articles free
        "api_feed": False,          # pro-feed.json gated
        "digest": False,            # no email digest
    },
    "pro": {
        "label": "Pro Intelligence",
        "signals_limit": None,      # all signals
        "cases_visible": True,
        "case_detail": True,        # full case detail
        "entity_profiles": True,
        "entity_detail": True,      # full profiles
        "graph": True,
        "map": True,
        "articles": None,           # all articles
        "api_feed": True,           # pro-feed.json available
        "digest": True,             # daily/weekly digest
    },
}

# ── Provider switches (no-op until configured) ──────────────────────────
PAYMENT_PROVIDER = None             # None | "lemonsqueezy" | "stripe" | "kofi"
PAYMENT_PRODUCT_ID = None           # Product ID from payment provider
DIGEST_PROVIDER = None              # None | "buttondown" | "mailchimp" | "resend"
DIGEST_API_KEY = None               # API key for digest provider
SPONSOR_SLOTS = []                  # List of {"text": "...", "url": "...", "label": "Sponsored"}
MEMBERSHIP_URL = None               # Ko-fi / Buy Me a Coffee URL (rendered as button)
```

---

## Module 1: Tiered Publisher

**What it does:** `publish.py` accepts a `--tier` argument. It generates two separate builds — `dist-free/` and `dist-pro/` — from the same database, with content gated according to tier rules.

**Hook point:** `_build_dist()` in `publish.py`

**How the tier gate works (static, no server):**

The free tier build simply **doesn't generate** gated pages. Instead of a case detail page, it generates a gate page:

```html
<!-- cases/operation-magaqa.html (FREE tier) -->
<div class="gate-page">
  <div class="gate-icon">&#9711;</div>
  <h2>Case Detail — Pro Access</h2>
  <p>Full case evidence chains, actor dossiers, and analyst briefs
     are available to Pro subscribers.</p>
  <a href="/subscribe.html" class="gate-cta">Subscribe — $X/month</a>
  {% if not REVENUE_LIVE %}
  <div class="gate-sim-banner">
    SIMULATION MODE — <a href="../dist-pro/cases/operation-magaqa.html">View content</a>
  </div>
  {% endif %}
</div>
```

The pro tier build generates everything as it does today.

**Implementation:**

```
publish.py changes:
  --tier free|pro|both (default: both)

  if tier == "both":
      _build_dist(env, conn, signals, articles, now_str, tier="free")
      _build_dist(env, conn, signals, articles, now_str, tier="pro")
  else:
      _build_dist(env, conn, signals, articles, now_str, tier=tier)

  _build_dist() gains a `tier` parameter:
      - DIST = ROOT / f"dist-{tier}"
      - signals are sliced to tier["signals_limit"]
      - articles are sliced to tier["articles"]
      - case detail pages: generated (pro) or gate page (free)
      - entity detail pages: generated (pro) or gate page (free)
```

**Files:**
- `revenue/config.py` (new) — tier definitions + provider switches
- `tools/publish.py` (modify) — `--tier` argument, tier-aware build
- `publisher/templates/gate.html` (new) — generic gate page template

**Simulation mode:** When `REVENUE_LIVE = False`, the gate page includes a bypass link to the pro version. When live, the bypass is removed and the CTA links to the payment provider.

---

## Module 2: Membership Button

**What it does:** Renders a "Support this work" button in the site footer and article pages. Links to Ko-fi / Buy Me a Coffee / custom URL.

**Hook point:** `base.html` footer, `article.html` footer

**Implementation:**

```html
<!-- In base.html footer, rendered only if MEMBERSHIP_URL is set -->
{% if membership_url %}
<a href="{{ membership_url }}" target="_blank" rel="noopener"
   class="membership-btn">
  Support Independent OSINT
</a>
{% endif %}
```

When `MEMBERSHIP_URL = None`, nothing renders. When set, the button appears. Zero external dependencies — it's just a link.

**Simulation mode:** When `REVENUE_LIVE = False` and `MEMBERSHIP_URL` is set, the button renders with `[SIM]` badge and `href="#"` instead of the real URL.

**Files:**
- `publisher/templates/base.html` (modify) — add membership button to footer
- `publisher/templates/article.html` (modify) — add membership CTA after methodology section
- `publisher/static/css/site.css` (modify) — membership button styles
- `tools/publish.py` (modify) — pass `membership_url` to template context

---

## Module 3: Sponsor Ticker Slot

**What it does:** Inserts a labeled sponsor message into the bottom ticker marquee, visually distinct from signal headlines.

**Hook point:** `map.html` ticker, `timeline.html` (if ticker is added later)

**Implementation:**

In `revenue/config.py`:
```python
SPONSOR_SLOTS = [
    {
        "text": "Protect your credentials with Bitwarden",
        "url": "https://bitwarden.com",
        "label": "SPONSORED",
    },
]
```

In the ticker template:
```html
{% for sponsor in sponsor_slots %}
<span class="md-ticker__sponsor">
  <span class="md-ticker__sponsor-label">{{ sponsor.label }}</span>
  <a href="{{ sponsor.url }}" target="_blank" rel="noopener sponsored">{{ sponsor.text }}</a>
</span>
<span class="md-ticker__sep">&bull;</span>
{% endfor %}
```

Sponsor items are visually distinct: different color, `[SPONSORED]` label prefix, `rel="sponsored"` on the link.

**Simulation mode:** When `REVENUE_LIVE = False`, sponsor slots render with `[SIM]` prefix and `href="#"`. When live, real URLs are used.

**Files:**
- `publisher/templates/map.html` (modify) — sponsor slot in ticker
- `publisher/static/css/site.css` (modify) — sponsor item styles
- `tools/publish.py` (modify) — pass `sponsor_slots` to template context

---

## Module 4: Intelligence Digest Generator

**What it does:** `publish.py --digest` generates an HTML email template (`dist/digest.html`) containing the latest signals, articles, and case updates since the last digest. Optionally sends via configured email provider.

**Hook point:** New function in `publish.py`, triggered by `--digest` flag

**Implementation:**

```
publish.py --digest [--send]
  1. Query signals WHERE published_at > last_digest_timestamp
  2. Render publisher/templates/digest.html with new signals + articles
  3. Write to dist/digest.html (always — preview/archive)
  4. If --send AND DIGEST_PROVIDER is configured AND REVENUE_LIVE:
       Call provider API to send to subscriber list
  5. Update last_digest_timestamp in DB (new column or config table)
```

**Digest template** (`publisher/templates/digest.html`):
- Self-contained HTML email (inline CSS, no external deps)
- Header: ZA-DIVERGENT logo text + date range
- Body: signal cards (title, stream badge, significance band, source)
- Footer: unsubscribe link (provider-managed), methodology note
- Matches site design language (dark background, monospace metadata)

**Provider abstraction:**

```python
# revenue/digest_provider.py
class DigestProvider:
    def send(self, html: str, subject: str) -> bool:
        raise NotImplementedError

class SimulatedProvider(DigestProvider):
    def send(self, html, subject):
        print(f"[digest-sim] Would send: {subject} ({len(html)} bytes)")
        return True

class ButtondownProvider(DigestProvider):
    def __init__(self, api_key):
        self.api_key = api_key
    def send(self, html, subject):
        # POST to Buttondown API
        ...

class ResendProvider(DigestProvider):
    def __init__(self, api_key):
        self.api_key = api_key
    def send(self, html, subject):
        # POST to Resend API
        ...

def get_provider():
    from revenue.config import DIGEST_PROVIDER, DIGEST_API_KEY, REVENUE_LIVE
    if not REVENUE_LIVE or not DIGEST_PROVIDER:
        return SimulatedProvider()
    if DIGEST_PROVIDER == "buttondown":
        return ButtondownProvider(DIGEST_API_KEY)
    if DIGEST_PROVIDER == "resend":
        return ResendProvider(DIGEST_API_KEY)
    return SimulatedProvider()
```

**Simulation mode:** `SimulatedProvider` logs to console. `dist/digest.html` is always generated regardless — can be opened in browser to preview.

**Files:**
- `revenue/digest_provider.py` (new) — provider abstraction
- `publisher/templates/digest.html` (new) — email template
- `tools/publish.py` (modify) — `--digest` and `--send` flags
- Schema: `last_digest_at` column in a config/metadata table (or flat file `revenue/.last_digest`)

---

## Module 5: API Feed (Pro)

**What it does:** `publish.py` generates a `pro-feed.json` containing full signal data with gravity scores, entity relationships, and case linkages. The free `feed.json` contains titles and slugs only.

**Hook point:** `_build_dist()` in `publish.py`, after existing `feed.json` generation

**Implementation:**

```python
# Free feed (existing, unchanged)
feed.json = [{ title, stream, published_at, url }]

# Pro feed (new)
pro-feed.json = [{
    signal_id, title, content, stream, source,
    gravity_score, significance_band,
    lat, lng, published_at,
    linked_cases: [{ case_id, name, coe }],
    linked_actors: [{ actor_id, name, type }],
    article_slug,
}]
```

**Gate mechanism (static, no server):**

In simulation mode, `pro-feed.json` is generated and accessible. When live, the pro feed is either:
- Not generated in the free build (simplest)
- Or generated but served behind a Vercel edge function that checks an API key header (requires Vercel Pro — deferred)

For v1: the gate is simply that `pro-feed.json` only exists in `dist-pro/`, not `dist-free/`.

**Files:**
- `tools/publish.py` (modify) — generate `pro-feed.json` in pro tier only

---

## Module 6: Subscribe Page

**What it does:** `publish.py` generates a `subscribe.html` page with pricing tiers, feature comparison, and payment links. In simulation mode, the payment buttons show `[SIM]` and don't redirect.

**Implementation:**

Static template. No external JS. Payment links point to provider checkout URLs when configured.

```html
<div class="pricing-grid">
  <div class="pricing-card pricing-card--free">
    <h3>Public Bulletin</h3>
    <div class="pricing-price">Free</div>
    <ul>
      <li>Latest 20 signals</li>
      <li>Map + Graph</li>
      <li>3 most recent articles</li>
      <li>Entity directory</li>
    </ul>
    <span class="pricing-current">Current plan</span>
  </div>

  <div class="pricing-card pricing-card--pro">
    <h3>Pro Intelligence</h3>
    <div class="pricing-price">$X/month</div>
    <ul>
      <li>All signals + full archive</li>
      <li>Case detail + evidence chains</li>
      <li>Full entity profiles + dossiers</li>
      <li>API feed (pro-feed.json)</li>
      <li>Daily intelligence digest</li>
    </ul>
    <a href="{{ payment_url or '#' }}" class="pricing-cta">
      {% if REVENUE_LIVE %}Subscribe{% else %}[SIM] Subscribe{% endif %}
    </a>
  </div>
</div>
```

**Files:**
- `publisher/templates/subscribe.html` (new) — pricing page
- `tools/publish.py` (modify) — generate subscribe.html in all tiers

---

## Implementation Order

Each phase is independent. Skipping any phase does not break the system.

| Phase | Module | Effort | Dependencies |
|---|---|---|---|
| **1** | `revenue/config.py` + simulation scaffold | 1 hour | None |
| **2** | Module 2: Membership button | 30 min | Phase 1 |
| **3** | Module 3: Sponsor ticker slot | 30 min | Phase 1 |
| **4** | Module 6: Subscribe page | 1 hour | Phase 1 |
| **5** | Module 1: Tiered publisher | 3 hours | Phase 1 |
| **6** | Module 5: API feed (pro) | 1 hour | Phase 5 |
| **7** | Module 4: Digest generator | 3 hours | Phase 1 |

**Total estimated:** ~10 hours across 7 phases.

**Go-live sequence:** When ready to accept payments:
1. Create account with payment provider (LemonSqueezy recommended — no Stripe complexity, supports digital products, handles VAT)
2. Set product IDs in `revenue/config.py`
3. Set `REVENUE_LIVE = True`
4. Run `publish.py --tier both --deploy`
5. Free site deploys to primary domain, pro site deploys to pro subdomain or gated path

---

## File Tree (final state)

```
revenue/
  config.py              ← tier definitions, provider switches, REVENUE_LIVE flag
  digest_provider.py     ← email provider abstraction (sim + buttondown + resend)
  __init__.py

publisher/templates/
  gate.html              ← generic content gate page (free tier)
  subscribe.html         ← pricing/feature comparison page
  digest.html            ← email digest template (inline CSS)
  base.html              ← modified: membership button in footer
  map.html               ← modified: sponsor slot in ticker
  article.html           ← modified: membership CTA after methodology

tools/
  publish.py             ← modified: --tier, --digest, --send flags

dist-free/               ← free tier build output
dist-pro/                ← pro tier build output (or dist/ if single tier)
```

---

## Simulation vs Live Behavior

| Component | REVENUE_LIVE = False | REVENUE_LIVE = True |
|---|---|---|
| Gate pages | Show bypass link to pro content | Show payment CTA only |
| Membership button | Renders with `[SIM]`, href="#" | Links to Ko-fi/BMC URL |
| Sponsor slots | Render with `[SIM]` prefix, href="#" | Real sponsor URLs |
| Subscribe page | Buttons show `[SIM]`, no redirect | Links to payment checkout |
| Digest --send | Logs to console, writes HTML file | Sends via configured provider |
| API feed | `pro-feed.json` generated in both tiers | Only in pro tier |

**The simulation mode IS the development environment.** Every revenue surface is visible and testable without any external account. Flip the switch when ready.

---

## Revenue Projections (for context, not promises)

Based on comparator data and conversion rates:

| Audience size | Membership (1% conv) | Pro sub (2% conv, $10/mo) | Sponsor (1 slot, $200/mo) | Monthly total |
|---|---|---|---|---|
| 1,000 readers | $50 | $200 | $200 | **$450** |
| 5,000 readers | $250 | $1,000 | $500 | **$1,750** |
| 20,000 readers | $1,000 | $4,000 | $1,000 | **$6,000** |
| 100,000 readers | $5,000 | $20,000 | $2,000 | **$27,000** |

World Monitor reached 4M users with zero marketing. ZA-DIVERGENT is niche (SA OSINT) but the niche is underserved.

---

## What This Does NOT Do

- Does NOT add Stripe, PayPal, or any payment SDK to the codebase
- Does NOT require a backend server — everything is static HTML
- Does NOT break existing `publish.py` behavior (`--tier` defaults to current single-build mode)
- Does NOT store user accounts, passwords, or payment data
- Does NOT add Node.js, npm, or build tools
- Does NOT require any external API key to develop against (simulation mode)
- Does NOT modify the FORGE intelligence pipeline — revenue modules only touch the publisher
