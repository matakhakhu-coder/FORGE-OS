# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Collaboration Protocol

**Before executing any directive, Claude must:**

1. **Audit** — Check the directive against the manifest constraints, phase history, architectural decisions, and downstream effects on the pipeline and schema.
2. **Weigh** — If a cheaper or cleaner path exists that achieves the same goal with less future cost, name it before proceeding.
3. **Flag deficits** — If the directive has a gap (missing constraint, unintended consequence, schema regression risk, tech-debt amplification), surface it briefly and clearly.
4. **Proceed** — If the directive is sound (or after flagging deficits), execute without unnecessary friction.

---

## Project Identity

- **Name:** FORGE (Foundational Open Research & Graph Engine)
- **Domain:** Local-first, analyst-grade OSINT intelligence operating system. Primary focus: South African domestic & regional open-source intelligence.
- **Stack:** Python 3.13 · Flask · SQLite (WAL mode) · Jinja2 · Leaflet.js · D3.js · Chart.js · HTMX
- **Constraint:** Zero Node.js at build or runtime. Ever. No npm. No webpack.
- **Anchor document:** `FORGE_OS_MANIFEST.md` — if code conflicts with the manifest, the code is wrong.
- **Tech debt ledger:** `docs/tech_debt.md` — update when debt is resolved or discovered.
- **Current stable:** `Stable 1.2.0` (Post-Monolith Extraction). app.py reduced from 10,133 to 1,297 lines. 8 Flask Blueprints in `core/web/blueprints/`. 151 routes across 10 blueprints. FLUX phases A–I complete.

---

## Commands

```powershell
# First-time setup
python app.py --init-db              # create schema
python migrations\fix_schema.py     # apply column patches + relationship tables
python migrations\add_socint_columns.py  # FLUX schema (idempotent)

# Run the app
python app.py                        # serves on localhost:5000

# After any SCHEMA_STATEMENTS change in app.py
python app.py --migrate

# Full pipeline
python tools\mega_ingest.py          # collect + synthesise (all phases)

# Single collector test
python forage\collectors\<name>.py

# FLUX SOCINT
python flux\collectors\x_pulse.py --targets "@handle1,@handle2"
python flux\processors\resonance.py --dry-run   # audit without writing
python flux\processors\discovery.py --dry-run   # Jaccard + velocity candidates

# Scheduled workers (via bin\)
bin\decay_worker.bat                 # exponential decay every 6 hours
bin\wiki_worker.bat                  # wiki synthesis pipeline
```

**Never** open `database.db` with an external SQLite tool while `tools\mega_ingest.py` is running — WAL mode will deadlock.

---

## Signal Lifecycle (data flow across files)

This is the critical cross-file architecture that requires reading multiple modules to understand:

```
Collector (forage/collectors/*.py)
  │  writes raw text + metadata_json → signals table (INSERT OR IGNORE on external_id)
  │
  ▼
core/pipeline/ingest.py  ingest_signal()
  ├── FMS hook: context.fire_hook("on_signal", signal)  ← forge_modules intercept here
  ├── SignalInterpreter   → extracts keywords, stream classification
  ├── NER processor       → spaCy entity extraction
  ├── gravity_engine.score_signal()  → gravity_score ∈ [0.0, 1.0]
  ├── EventConstructor    → groups signals into events
  ├── EntityEngine.materialize_entities()  → creates actors (gate: confidence ≥ 0.2)
  ├── RelationshipEngine  → link_signal_actors(), link_event_actors()
  ├── CaseEngine.evaluate_case()   → auto-pin to matching cases
  ├── EscalationEngine    → MONITOR ≥ 0.35 · ESCALATE ≥ 0.55
  └── FeedbackEngine      → actor_influence() multiplier applied post-score
```

**Two gravity scorers exist — they do different things:**
- `forage/engines/gravity_engine.py` → `score_signal()`: urgency/importance score written to `signals.gravity_score`. Two paths: ACLED (structured metadata) and Standard (five-factor model).
- `core/gravity.py` → `build_context()` + `score_item()`: CT-1 Contextual Tunneling. Measures relevance to an *analyst's active case* (actor ×0.50 · location ×0.30 · keyword ×0.20). **Verified offline asset — 41-test suite at `tests/test_gravity_ct1.py` passes clean. Surface route integration pending.**

**FLUX SOCINT bypass:** X posts collected by `flux/collectors/x_pulse.py` land in `socint_signals` (not `signals`). The FMS `on_ingest` hook in `forge_modules/flux/module.py` bridges FLUX data into the main graph without touching `core/pipeline/ingest.py`.

---

## FMS — Forge Module System

Modules live in `forge_modules/<name>/` and must contain `manifest.json` + `module.py`.

**Boot sequence** (called from `app.py`):
```
core/fms/loader.py  load_modules(context)
  → scans forge_modules/ for manifest.json
  → validates via core/fms/validator.py
  → calls module.register(conclave)
  → conclave context stores hook lists

core/conclave/context.py  get_context()  — singleton FMS context
core/conclave/engine.py   run_conclave() — fires hooks during ingest
```

**Available hook names:** `on_signal`, `on_ingest`, `on_actor_create`

Active modules: `signal_enrichment` · `geo_enrichment` · `graph_sync` · `coalition_detector` · `counterintel` · `emergence_engine` · `flux`

Module failures are isolated — a crashing module cannot kill Flask or block ingestion.

---

## Architectural Decisions (locked — do not re-litigate)

| Decision | Choice | Rationale |
|---|---|---|
| Database engine | SQLite WAL (`database.db`) | Local-first; WAL allows concurrent reads during collection |
| DB timeout | 60 s | Prevents lock starvation during heavy ingestion |
| FK enforcement | Monkey-patch in `core/db/connection.py` | Python's sqlite3 ignores URI `_foreign_keys`; monkey-patch is the only reliable hook |
| Flask DB pattern | `get_db()` → `g._database`; `@teardown_appcontext` closes | Background workers use raw `sqlite3.connect()` with `try/finally` |
| Score storage | REAL floats `0.0–1.0` | Round only at template layer |
| Signal deduplication | `INSERT OR IGNORE` on `external_id` | Idempotent — collectors safe to re-run |
| Collector discovery | `__manifest__` dict; AST-parsed at boot | No config files; manifest is the contract |
| Collector dispatch | `subprocess.Popen` per collector | A crashing collector cannot kill Flask |
| Background jobs | `pipeline_jobs` table + daemon `threading.Thread` | No Celery; DB-backed queue if reliability needed later |
| Actor confidence gate | `conclusion.confidence ≥ 0.2` | Prevents low-quality signals from polluting actor registry |
| Signal streams | `CRIME_INTEL · INFRASTRUCTURE · PRIORITY · GLOBAL` | Four discrete decay rates |
| Decay model | `score × e^(−λ × hours)`, floor `0.05` | Exponential half-life; priority signals start at 1.5× |
| Source layers | `source_type = 'live'` or `'seed'`; `lens` param on routes | Analyst can inspect live vs. curated layers independently |
| Actor types (valid) | `person · institution · media · movement · government · location · political_party · organization · other · paramilitary · unknown` | Full CHECK constraint; `/admin` dropdown and `_VALID_ACTOR_TYPES` must stay in sync |
| Frontend | Leaflet.js · D3.js · Chart.js · HTMX | No SPA framework; server-rendered Jinja2 + progressive enhancement |
| Route architecture | 8 Flask Blueprints in `core/web/blueprints/` | app.py is thin factory; routes live in domain-specific modules |

---

## Blueprint Architecture (Stable 1.2)

`app.py` is a **thin factory** (1,297 lines): config, collector scanner, `create_app()` with blueprint registrations, schema, migrations, and CLI.

All routes live in `core/web/blueprints/`:

| Blueprint | File | Routes | Domain |
|---|---|---|---|
| `pages_bp` | `pages.py` | 11 | Dashboard, feed, timeline, search, gallery, intel |
| `signals_bp` | `signals.py` | 10 | Signal triage, pulse, heatmap, decay/anomaly |
| `cases_bp` | `cases.py` | 28 | Case CRUD, workbench, pinning, briefings, CT-1 |
| `admin_bp` | `admin.py` | 29 | Entity CRUD, forensics, dossiers, documents, alerts |
| `graph_bp` | `graph.py` | 13 | D3/Vis-Network, relationships, coalitions |
| `map_bp` | `map_routes.py` | 6 | Leaflet map, GeoJSON layers |
| `control_bp` | `control.py` | 24 | Collector dispatch, pipeline ops, quarantine |
| `diagnostics_bp` | `diagnostics.py` | 11 | Health, FMS, discovery, evolution |

**Shared infrastructure:**
- `core/web/helpers.py` — `get_db()`, telemetry functions, config constants
- `core/web/state.py` — `_COLLECTOR_REGISTRY`, `_DEAD_NODES`, `_KILL_FLAGS`, `_PIPELINE_ACTIVE`

**Rules for adding routes:**
- New routes go into the appropriate blueprint, NOT into `app.py`
- Import `get_db` from `core.web.helpers`
- Import mutable state from `core.web.state`
- Cross-blueprint `url_for()` uses `blueprint.endpoint` format

---

## Critical Code Rules

These have caused pipeline crashes when violated.

### 1 · `from __future__ import annotations` — always line 2

```python
#!/usr/bin/env python3              # line 1
from __future__ import annotations  # line 2 — ALWAYS HERE, before docstring and __manifest__
"""Module docstring"""
__manifest__ = { ... }
```

Never place it after `__manifest__` or any other code.

### 2 · No `datetime.utcnow()`

```python
# WRONG
datetime.utcnow()

# CORRECT (from datetime import datetime, timezone)
datetime.now(timezone.utc)

# CORRECT (import datetime)
datetime.datetime.now(datetime.timezone.utc)
```

### 3 · Schema changes → run `--migrate`

`CREATE TABLE IF NOT EXISTS` does **not** update existing tables. CHECK constraint changes (e.g. actor types) require the full table-recreation pattern: `PRAGMA foreign_keys OFF → CREATE new → INSERT SELECT → DROP old → RENAME → PRAGMA foreign_keys ON`.

### 4 · DB connections in background threads

```python
conn = sqlite3.connect(str(DB_PATH), timeout=10)
try:
    # work
    conn.commit()
finally:
    conn.close()
```

`timeout=` is mandatory. Never leave a bare `sqlite3.connect()` without `finally: conn.close()`.

---

## Collector Manifest Contract

Every file in `forage/collectors/` must declare `__manifest__` at module level:

```python
__manifest__ = {
    "id":          "collector_name",  # must match signals.source — join key for _auto_pin_to_case()
    "name":        "Human Readable Name",
    "description": "One-line description.",
    "icon":        "emoji",
    "entry":       "forage/collectors/collector_name.py",
    "args":        [],
    "job_key":     "collector_name",
    "version":     "1.0.0",
}
```

---

## FLUX Protocol

FLUX is the SOCINT (Social Intelligence) root, parallel to `forage/` (OSINT).

- **Root:** `flux/` — sibling to `forage/`, never a subdirectory of it.
- **Integration:** FMS `on_ingest` hook only. `core/pipeline/ingest.py` is never modified.
- **Stylometric processor** (`flux/processors/stylometric.py`): stdlib only — `re`, `collections`, `difflib`, `statistics`, `json`. No spaCy, no NLTK, no transformers.
- Resonance scoring is O(n²); never run inline with ingestion. Run as scheduled batch.
- Stylometric edges use `relation_type = 'stylometric_match'` in `entity_relationships`.

**FLUX schema additions:**

| Table / Column | Purpose |
|---|---|
| `socint_signals` | X posts per-author |
| `socint_resonance` | Pairwise stylometric similarity scores |
| `actors.socint_profile` | JSON: X handles, aliases, rolling text corpus (max 100 samples) |
| `signals.socint_tags` | JSON: SOCINT-derived behavioural tags |
| `signals.socint_resonance` | Highest resonance score for this signal |

**Stylometric weights** (must sum to 1.0 — change constants only, not logic):

```python
W_SIM=0.35  W_CASH=0.25  W_EMOJI=0.20  W_CAPS=0.10  W_LEET=0.10
CORPUS_MIN_ITEMS=7  CORPUS_MIN_CHARS=2000  RESONANCE_THRESHOLD=0.65
```

**Environment variables:**

| Variable | Default | Purpose |
|---|---|---|
| `X_PULSE_MODE` | `"nitter"` | `"nitter"` (RSS) or `"guest_api"` (GraphQL) |
| `X_BEARER_TOKEN` | — | Required for `guest_api` mode |
| `X_PULSE_TARGETS` | — | `handle1,handle2,#hashtag1,$CASHTAG1` |
| `NDBC_STATIONS` | — | Comma-separated NDBC station IDs, e.g. `"41049,13008"`. Browse stations at ndbc.noaa.gov/obs.shtml |

---

## Known Tech Debt

Full ledger in `docs/tech_debt.md`. Active high-priority items:

| ID | Area | Severity | Notes |
|---|---|:---:|---|
| ENT-01 | ~~`entity_engine.py` missing `confidence_score`, `automated` columns~~ | ~~MEDIUM~~ | **RESOLVED 2026-05-28** |
| CT-1 | `core/gravity.py` implemented, partially wired (feed route only) | MEDIUM | 41-test suite passes. Full route integration pending. |
| P2-06 | spaCy `en_core_web_sm` not tuned for SA govt entities | HIGH | DPWI, HAWKS, SIU, NPA tagged MISC or missed. Fix: custom `EntityRuler` |
| P3.2-05 | 6,458 scanned PDFs with `< 100 chars` in `raw_text_cache` | HIGH | OCR pipeline exists; needs `--status A1-PENDING` run |
| TD-13 | Case Alpha institutional bridge gap (CoE = 0.28) | HIGH | SAFLII bridge hunt needed |
| TD-20 | `graph_nodes` (463k rows) vs `actors` (1,011) imbalance | MEDIUM | Provenance audit; prune stale rows |
| ~~AD-3~~ | ~~Schema duplication: SCHEMA_STATEMENTS vs migrate_db()~~ | ~~MEDIUM~~ | **RESOLVED 2026-06-21** — duplicate CREATE TABLE stanzas removed; column drift patched |
| DB-01 | ~~`sqlite3.connect()` missing `timeout=60` in 8 active files~~ | ~~HIGH~~ | **RESOLVED 2026-05-30** |
| DB-02 | `__future__` placement after line 3 in 51 files | LOW | No runtime impact. Defer to cleanup pass. |
| ~~AD-1~~ | ~~10,133-line app.py monolith~~ | ~~HIGH~~ | **RESOLVED 2026-06-21** — Stable 1.2.0 extraction: 8 blueprints, app.py → 1,297 lines |
| ~~AD-2~~ | ~~Missing FK indexes (35 FKs, 3 indexes)~~ | ~~HIGH~~ | **RESOLVED 2026-06-21** — 16 FK indexes added; total 49 |
| ~~RC-1~~ | ~~No mutex on concurrent pipeline runs~~ | ~~HIGH~~ | **RESOLVED 2026-06-21** — `_PIPELINE_ACTIVE` guard, HTTP 409 rejection |
| ~~IV-1~~ | ~~14 bare int()/float() casts crash on bad input~~ | ~~MEDIUM~~ | **RESOLVED 2026-06-21** — Safe `type=int/float` pattern |
| ~~SDL-1~~ | ~~Gravity write failure silently drops signals~~ | ~~CRITICAL~~ | **RESOLVED 2026-06-21** — Early return on persistence failure |
