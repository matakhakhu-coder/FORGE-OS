# FORGE — Complete Codebase Architecture & Audit Reference

> **Purpose:** This document is a self-contained architectural briefing for any LLM or analyst working with the FORGE codebase. It covers topology, entry points, initialization, data flow, schema, engine contracts, and a diagnostic audit of known risks. Attach this as context to orient cold on the project.
>
> **Generated:** 2026-06-21 (post-extraction revision)
> **Stable version:** 1.2.0 (Post-Monolith Extraction)
> **Anchor document:** `FORGE_OS_MANIFEST.md` — if code conflicts with the manifest, the code is wrong.

---

## 1. Project Identity

- **Name:** FORGE (Foundational Open Research & Graph Engine)
- **Domain:** Local-first, analyst-grade OSINT intelligence operating system. Primary focus: South African domestic & regional open-source intelligence.
- **Stack:** Python 3.13 · Flask 3.1 · SQLite (WAL mode) · Jinja2 · Leaflet.js · D3.js · Chart.js · HTMX
- **Hard constraint:** Zero Node.js at build or runtime. No npm. No webpack.
- **Database:** Single-file `database.db` (SQLite WAL, 30 tables, FTS5 full-text search, FK enforcement via monkey-patch, 49 indexes)
- **Deployment:** Flask dev server on localhost:5000 (internal). Static site published to Vercel via `tools/publish.py` (public-facing ZA-DIVERGENT portal).

---

## 2. Directory Topology

```
FORGE/
├── app.py                      ← Lean app factory + schema + CLI (1,297 lines)
├── database.db                 ← SQLite WAL database (30 tables, 49 indexes)
├── requirements.txt            ← 191 Python packages
├── .env                        ← Environment variables (not committed)
├── CLAUDE.md                   ← AI agent operating instructions
├── FORGE_OS_MANIFEST.md        ← Canonical design document
│
├── core/
│   ├── web/                    ← Flask infrastructure (Stable 1.2 extraction)
│   │   ├── helpers.py          ← get_db(), telemetry (create/update/finalize_job),
│   │   │                         config constants (BASE_DIR, DB_PATH, MEDIA_DIR,
│   │   │                         ADMIN_PASSWORD, SOURCE_META, _VALID_ACTOR_TYPES)
│   │   ├── state.py            ← Shared mutable registries (_COLLECTOR_REGISTRY,
│   │   │                         _DEAD_NODES, _KILL_FLAGS, _PIPELINE_ACTIVE)
│   │   └── blueprints/
│   │       ├── pages.py        ← Dashboard, events, actors, search, timeline,
│   │       │                      feed (+ CT-1), artifacts gallery, intel (11 routes)
│   │       ├── signals.py      ← Signal triage, pulse, heatmap, entities, streams,
│   │       │                      decay/anomaly engine triggers (10 routes)
│   │       ├── cases.py        ← Case CRUD, workbench, pinning, briefings, CT-1
│   │       │                      tunneling, sequence/transition, suggestions (28 routes)
│   │       ├── admin.py        ← Admin panel, entity CRUD (actor/event/artifact),
│   │       │                      forensics, dossiers, document briefs, alerts,
│   │       │                      correlations, sentinel (29 routes)
│   │       ├── graph.py        ← D3/Vis-Network graph, intel-graph, actor-network,
│   │       │                      relationships CRUD, metrics, coalitions (13 routes)
│   │       ├── map_routes.py   ← Leaflet map, GeoJSON layers, case-scoped geo,
│   │       │                      signal/cluster/graph-edge overlays (6 routes)
│   │       ├── control.py      ← Collector dispatch, pipeline ops, archive, counterintel,
│   │       │                      artifact processor, wiki pipeline, job management,
│   │       │                      quarantine (24 routes)
│   │       └── diagnostics.py  ← Pipeline health, FMS status/attach/detach/UI,
│   │                              discovery, evolution (11 routes)
│   ├── pipeline/
│   │   └── ingest.py           ← Signal processing orchestrator (ingest_signal)
│   ├── db/
│   │   ├── connection.py       ← SQLite connection factory + FK monkey-patch
│   │   └── wiki.py             ← Wiki schema init
│   ├── conclave/
│   │   ├── context.py          ← ConclaveContext singleton (FMS hook registry)
│   │   ├── engine.py           ← Conclave engine runner
│   │   └── registry.py         ← AnalysisResult dataclass
│   ├── fms/
│   │   ├── loader.py           ← Module discovery + dynamic import
│   │   ├── validator.py        ← Manifest + contract validation
│   │   ├── bootstrap.py        ← FMS boot sequence
│   │   ├── activation.py       ← Module attach/detach
│   │   └── readiness.py        ← Readiness reporting
│   ├── api/
│   │   ├── context.py          ← Template context injection
│   │   └── routes/
│   │       └── wiki_routes.py  ← Wiki blueprint
│   ├── diagnostics/
│   │   └── health.py           ← Pipeline health computation
│   ├── gravity.py              ← CT-1 Contextual Tunneling scorer (offline asset)
│   └── media/                  ← Media processing utilities
│
├── forage/                     ← OSINT collection + processing
│   ├── collectors/ (15 files)  ← Signal collectors (see section 4)
│   ├── engines/ (17 files)     ← Processing engines (see section 6)
│   ├── processors/             ← SignalInterpreter, NER, entity resolver
│   └── utils/                  ← Shared utilities
│
├── flux/                       ← SOCINT (Social Intelligence) — parallel to forage/
│   ├── collectors/
│   │   └── x_pulse.py          ← X/Twitter collector (Nitter RSS or GraphQL guest API)
│   └── processors/             ← Stylometric, resonance, discovery
│
├── forge_modules/ (7 modules)  ← FMS plugin modules (see section 5)
│   ├── signal_enrichment/ · geo_enrichment/ · graph_sync/
│   ├── coalition_detector/ · counterintel/
│   └── emergence_engine/ · flux/
│
├── surface/                    ← Intelligence Surface blueprint (9 routes)
│   ├── routes.py · queries.py
│
├── wiki/                       ← Wiki intelligence engine
├── publisher/                  ← ZA-DIVERGENT static site generator (Jinja2 + CSS)
├── tools/
│   ├── mega_ingest.py          ← 4-phase pipeline runner
│   ├── publish.py              ← Static site -> dist/ -> Vercel
│   └── *.py                    ← Various ad-hoc scripts
├── bin/                        ← Scheduled workers (.bat)
├── templates/                  ← Flask Jinja2 templates (internal UI)
├── static/                     ← CSS, JS, map tiles (internal UI)
├── media/                      ← Uploaded/processed media files
├── dist/                       ← Generated static site output
├── tests/                      ← Test suites
├── migrations/                 ← Schema migration scripts
├── revenue/                    ← Membership/sponsor config
├── forge_security/             ← Security utilities (sanitizer, detonator, audit)
├── data/                       ← Static data files
├── logs/                       ← Log output
└── docs/                       ← Documentation + tech debt ledger
```

---

## 3. Entry Points & Initialization

### 3.1 Entry Points

| Entry Point | Purpose | Invocation |
|---|---|---|
| `app.py` | Flask web server | `python app.py` (localhost:5000) |
| `app.py --init-db` | Create schema from scratch | `python app.py --init-db` |
| `app.py --migrate` | Apply column/table migrations | `python app.py --migrate` |
| `tools/mega_ingest.py` | Full pipeline run | `python tools/mega_ingest.py` |
| `tools/mega_ingest.py --collect-only` | Collection phase only | Skips engines + ingest |
| `tools/mega_ingest.py --ingest-only` | Conclave phase only | Skips collection |
| `tools/publish.py` | Generate static site | `python tools/publish.py` |
| `tools/publish.py --deploy` | Generate + push to Vercel | Triggers deploy webhook |
| `bin/decay_worker.bat` | Scheduled decay engine | Every 6 hours |
| `bin/wiki_worker.bat` | Wiki synthesis pipeline | Scheduled batch |
| `forage/collectors/<name>.py` | Individual collector test | Direct execution |

### 3.2 app.py Structure (1,297 lines)

`app.py` is now a thin factory, schema substrate, and CLI orchestrator:

| Section | Lines | Content |
|---|---|---|
| Imports + config | 1-90 | Module imports, BASE_DIR/DB_PATH/MEDIA_DIR from helpers |
| State imports | 91-107 | _KILL_FLAGS, _PIPELINE_ACTIVE, _COLLECTOR_REGISTRY from core.web.state |
| Telemetry imports | 108-112 | telemetry_init, create_job, update_job, finalize_job from helpers |
| Collector scanner | 113-214 | `_load_collector_registry()` — AST-based manifest extraction |
| `create_app()` | 219-351 | Thin factory: DB teardown, FMS boot, 8 blueprint registrations |
| SCHEMA_STATEMENTS | 354-989 | 30 table definitions + 16 FK indexes + FTS5 triggers |
| init_db / migrate_db | 990-1263 | Schema creation and migration logic |
| main() | 1269-1297 | CLI argument parsing and server launch |

### 3.3 Boot Sequence (create_app)

```
1. Import get_db from core.web.helpers
   - Standalone function using Flask's context-local g object
   - Register @teardown_appcontext for conn.close()
   - Attach to app.get_db for surface blueprint

2. telemetry_init()  (from core.web.helpers)
   - Creates pipeline_jobs table + indexes
   - Marks orphaned pending/running jobs as 'failed'

3. _load_collector_registry()  (local to app.py)
   - AST-scans forage/collectors/*.py + flux/collectors/*.py
   - Extracts __manifest__ dicts into core.web.state._COLLECTOR_REGISTRY
   - Broken files quarantined as _DEAD_NODES

4. FMS bootstrap + module attachment
   - bootstrap_fms() discovers all forge_modules/
   - report_readiness() checks each module
   - attach_module() for each READY module into ConclaveContext singleton

5. Wiki + Surface blueprint registration
   - init_wiki_db(), wiki_bp at /wiki, surface_bp

6. Blueprint registration (8 extracted blueprints)
   - pages_bp      → dashboard, feed, timeline, search, gallery
   - signals_bp    → signal triage, pulse, heatmap, entities
   - cases_bp      → case workbench, pinning, briefings, CT-1
   - admin_bp      → CRUD, dossiers, documents, forensics, alerts
   - graph_bp      → D3/Vis-Network, relationships, coalitions
   - map_bp        → Leaflet, GeoJSON layers
   - control_bp    → collector dispatch, pipeline ops, quarantine
   - diagnostics_bp → health, FMS, discovery, evolution

7. Error handlers (404, 500)

Total: 151 routes across 10 blueprints + app-level handlers
```

### 3.4 Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `FORGE_SECRET_KEY` | `"forge-dev-secret"` | Flask session signing key |
| `FORGE_ADMIN_PASSWORD` | `"forge-admin"` | Admin panel authentication |
| `FORGE_DB` | auto-detect | Path to database.db |
| `ACLED_KEY` | (none) | ACLED API key (required for ACLED collector) |
| `ACLED_EMAIL` | (none) | ACLED registered email |
| `X_PULSE_MODE` | `"nitter"` | `"nitter"` (RSS) or `"guest_api"` (GraphQL) |
| `X_BEARER_TOKEN` | (none) | Required for guest_api mode |
| `X_PULSE_TARGETS` | (none) | Comma-separated handles/hashtags/cashtags |
| `NDBC_STATIONS` | (none) | Comma-separated NDBC station IDs |

### 3.5 Shared Module Architecture (core/web/)

**`core/web/helpers.py`** — Importable by all blueprints:
- `get_db()` — request-scoped SQLite connection via Flask's `g`
- `create_job()`, `update_job()`, `finalize_job()` — telemetry write helpers
- `telemetry_init()` — boot-time job table creation + stale-job recovery
- Constants: `BASE_DIR`, `DB_PATH`, `MEDIA_DIR`, `MEDIA_SUBDIRS`, `ADMIN_PASSWORD`, `SOURCE_META`, `ACTOR_PHOTO_EXTENSIONS`, `_VALID_ACTOR_TYPES`

**`core/web/state.py`** — Shared mutable registries:
- `_COLLECTOR_REGISTRY: Dict[str, Dict]` — healthy collector manifests (populated by app.py at boot)
- `_DEAD_NODES: list` — broken/missing collector manifests
- `_KILL_FLAGS: Dict[int, bool]` — in-process job cancellation flags
- `_PIPELINE_ACTIVE: Dict[str, bool]` — concurrent-execution guard (reject if already running)
- `_PIPELINE_LOCK: threading.Lock` — reserved for future fine-grained locking

Both `app.py` and all 8 blueprints import from these modules, ensuring they operate on the same objects.

---

## 4. Collectors

### 4.1 OSINT Collectors (forage/collectors/)

Each collector declares a `__manifest__` dict at module level (AST-parsed at boot, never imported):

```python
__manifest__ = {
    "id":          "collector_name",
    "name":        "Human Readable Name",
    "description": "One-line description.",
    "icon":        "emoji",
    "entry":       "forage/collectors/collector_name.py",
    "args":        [],
    "job_key":     "collector_name",
    "version":     "1.0.0",
}
```

| Collector | Source | Data Type |
|---|---|---|
| `rss_collector` | South African news RSS feeds | News articles |
| `acled_collector` | ACLED API (conflict events) | Structured conflict data |
| `gdelt_collector` | GDELT DOC API | Global event data |
| `civic_intel_collector` | SA government feeds | Government notices |
| `civic_intel_collector_us` | US government feeds | Government notices |
| `cipc_collector` | CIPC (company registry) | Company data |
| `saflii_collector` | SAFLII (legal judgments) | Court decisions |
| `usgs_collector` | USGS earthquake API | Seismic events |
| `firms_collector` | NASA FIRMS | Fire/thermal data |
| `earthquake_collector` | Earthquake data sources | Seismic events |
| `ndbc_collector` | NDBC buoy stations | Maritime/weather |
| `dork_collector` | Google dorking | Document discovery |
| `pdf_infiltrator` | Local PDF ingestion | Document text extraction |
| `disease_outbreak_collector` | Disease surveillance feeds | Health alerts |
| `bi196_collector` | BI-196 immigration data | Border movement |

### 4.2 SOCINT Collector (flux/collectors/)

| Collector | Source | Data Type |
|---|---|---|
| `x_pulse` | X/Twitter (Nitter RSS or GraphQL guest API) | Social media posts |

FLUX collectors write to `socint_signals` (not `signals`). Integration via FMS `on_ingest` hook.

### 4.3 Signal Deduplication

All collectors use `INSERT OR IGNORE` on `external_id` (UNIQUE constraint). Re-running is always safe.

### 4.4 Collector Dispatch

Collectors run as `subprocess.Popen` processes. A crashing collector cannot kill Flask. The Control Room dispatches via `/api/control/run_collector/<id>` with concurrent-execution guards.

---

## 5. FMS — Forge Module System

### 5.1 Architecture

Modules live in `forge_modules/<name>/` with `manifest.json` + `module.py`. Boot: `core/fms/loader.py` discovers, validates, and calls `module.register(conclave)`.

### 5.2 Active Modules

| Module | Function |
|---|---|
| `signal_enrichment` | Enriches signal metadata during ingestion |
| `geo_enrichment` | Adds/corrects geographic coordinates |
| `graph_sync` | Syncs entity changes to graph_nodes/graph_edges |
| `coalition_detector` | Detects actor coalitions via co-occurrence |
| `counterintel` | Flags anomalous signals for review |
| `emergence_engine` | Tracks network emergence patterns |
| `flux` | Bridges SOCINT data into the main graph |

### 5.3 Hooks

`on_signal`, `on_ingest`, `on_actor_create` — fired via `ConclaveContext.fire_hook()`, each callback isolated by try/except.

---

## 6. Signal Lifecycle — End-to-End Data Flow

### 6.1 The 10-Stage Pipeline

```
Stage 1: COLLECTION → INSERT OR IGNORE into signals table
Stage 2: FMS HOOK (on_signal) → module enrichment (isolated)
Stage 3: INTERPRET → keywords, actors[], event_type, stream
Stage 4: ENTITY RESOLUTION → fuzzy name match, gate: confidence >= 0.2
Stage 5: GRAVITY SCORING → score_signal() → gravity_score [0.0, 1.0]
Stage 6: CASE EVALUATION → CREATE CASE (>0.8) / FLAG MONITOR (>0.6) / STORE ONLY
Stage 7: FEEDBACK → actor_influence() weight multiplier
Stage 8: CONCLAVE → fuse core + FMS engine results
Stage 9: MATERIALIZE → actors, signal_actors, graph_edges
Stage 10: ESCALATION → create_case (>=0.55, rate limit 5/hr) / create_event (>=0.35)
```

### 6.2 Two Gravity Scorers

1. **`forage/engines/gravity_engine.py`** — urgency/importance (signal-intrinsic). Two paths: ACLED (structured) and Standard (5-factor: severity 0.35, actor 0.25, frequency 0.15, sentiment 0.15, credibility 0.10).
2. **`core/gravity.py`** — CT-1 Contextual Tunneling (case-relative relevance). Weights: actor x0.50, location x0.30, keyword x0.20. Verified offline (41 tests pass), partially wired into `/api/feed`.

### 6.3 FLUX SOCINT Bypass

X posts from `flux/collectors/x_pulse.py` land in `socint_signals`, bridge via FMS `on_ingest` hook. Never touch `core/pipeline/ingest.py`.

### 6.4 mega_ingest.py — 4-Phase Pipeline

Phase 1: Collection (async concurrent). Phase 2: Engines (cluster, NER, anomaly, correlation, decay, evolution, graph, sentinel). Phase 3: Ingest (Conclave on unprocessed signals only). Phase 4: Summary.

---

## 7. Database Schema

### 7.1 Overview

30 tables, 49 indexes. Schema in `app.py` SCHEMA_STATEMENTS. WAL mode, FK enforcement via monkey-patch, timeout 60s.

### 7.2 Core Tables

```sql
signals    (signal_id TEXT PK, source, external_id UNIQUE, title, content, lat, lng,
            timestamp, status, stream, relevance_score, gravity_score, processed_at,
            conclave_meta, socint_tags, socint_resonance, cluster_id, is_priority,
            confidence_score, source_artifact_id, source_type, metadata_json, duplicate_count)

actors     (actor_id INT PK, name, type CHECK(...), description, source_type,
            confidence_score, automated, socint_profile)

events     (event_id INT PK, title, summary, description, date, location, lat, lng,
            category CHECK(...), source_type, confidence_score, automated)

cases      (case_id INT PK, name, description, hypothesis, case_type CHECK(...),
            status CHECK(active|closed|archived), source_type, auto_generated,
            trigger_signal_id, context_anchors)

artifacts  (artifact_id INT PK, title, description, type CHECK(...), source CHECK(...),
            file_path, thumbnail, raw_text_cache, processing_status, file_hash_sha256,
            file_hash_md5, file_size_bytes, exif_json, gps_lat, gps_lng, event_id FK)
```

### 7.3 Junction Tables

```
signal_actors, event_actors, actor_events, case_signals, case_artifacts, case_events, case_actors
```

### 7.4 Graph Substrate

```
graph_nodes (node_id, node_type, ref_id, label, metadata_json) — UNIQUE(node_type, ref_id)
graph_edges (edge_id, source_node_id FK, target_node_id FK, relation_type, weight, confidence) — UNIQUE(source, target, type)
```

### 7.5 Intelligence Tables

```
sentinel_alerts, correlated_incidents, entity_relationships, signal_flags,
actor_coalitions, actor_network_metrics, network_emergence, discovery_targets,
signal_baselines, actor_weights, artifact_duplicates, case_feedback
```

### 7.6 FLUX/SOCINT Tables

```
socint_signals, socint_resonance, flux_latent_seeds, flux_tag_cooccurrence
```

### 7.7 FTS5 + Wiki

`artifacts_fts`, `events_fts` (with sync triggers). `wiki_articles`, `wiki_entries`, `wiki_links`.

---

## 8. Engine Contracts

| Engine | File | Entry Point | Writes To |
|---|---|---|---|
| Gravity | `gravity_engine.py` | `score_signal()` | signals.gravity_score |
| Case | `case_engine.py` | `evaluate_case()` | Return dict only |
| Escalation | `escalation_engine.py` | `handle_escalation()` | events, cases |
| Feedback | `feedback_engine.py` | `apply_feedback()` | actor_weights |
| Entity | `entity_engine.py` | `materialize_entities()` | actors, signal_actors |
| Relationship | `relationship_engine.py` | `link_signal/event_actors()` | signal_actors, event_actors |
| Anomaly | `anomaly_engine.py` | `AnomalyEngine.run()` | signal_baselines, sentinel_alerts |
| Correlation | `correlation_engine.py` | spatiotemporal clustering | correlated_incidents |
| Cluster | `cluster_engine.py` | signal clustering | signals.cluster_id |
| Decay | `decay_engine.py` | `score * e^(-lambda * hours)`, floor 0.05 | signals.relevance_score |
| Evolution | `evolution_engine.py` | entity evolution | discovery_targets |
| Graph | `graph_engine.py` | `GraphEngine.run()` | graph_nodes, graph_edges |
| Archive | `archive_engine.py` | case archival | deletes/archives |

### Thresholds

```
Escalation: >= 0.55 + ESCALATE -> case (rate limit 5/hr), >= 0.35 + MONITOR -> event
UI threat levels: >= 0.75 critical, >= 0.55 elevated, >= 0.35 monitored, < 0.35 none
Decay: score * e^(-lambda * hours), floor 0.05, stream-specific half-lives
FLUX stylometric: W_SIM=0.35 W_CASH=0.25 W_EMOJI=0.20 W_CAPS=0.10 W_LEET=0.10
```

---

## 9. Route Map — 151 Endpoints Across 10 Blueprints

| Blueprint | Routes | Domain |
|---|---|---|
| `admin_bp` | 29 | Admin panel, entity CRUD, forensics, dossiers, documents, alerts, correlations |
| `cases_bp` | 28 | Case CRUD, workbench, pinning, briefings, CT-1, sequence/transition |
| `control_bp` | 24 | Collector dispatch, pipeline ops, archive, counterintel, jobs, quarantine |
| `graph_bp` | 13 | D3/Vis-Network, intel-graph, relationships CRUD, metrics, coalitions |
| `pages_bp` | 11 | Dashboard, events, actors, search, timeline, feed, artifacts, intel, media |
| `diagnostics_bp` | 11 | Health, FMS status/attach/detach/UI, discovery, evolution |
| `signals_bp` | 10 | Signal triage, pulse, heatmap, entities, streams, decay/anomaly triggers |
| `surface_bp` | 9 | Intelligence Surface dashboard + FLUX discovery |
| `map_bp` | 6 | Leaflet map, GeoJSON layers (signals, clusters, edges, case-scoped) |
| `wiki_bp` | 5 | Wiki synthesis, graph data, diagnostics |
| app-level | 5 | Error handlers (404, 500), wiki graph alias |

---

## 10. Concurrency & Pipeline Safety

### 10.1 Pipeline Mutex (Stable 1.2)

Background pipeline endpoints (`run_collectors`, `run_ingest`, `run_conclave`, `run_graph_engine`) are guarded by `_PIPELINE_ACTIVE` (in `core/web/state.py`). If an endpoint is already running, subsequent calls return HTTP 409 with `{"status": "rejected", "reason": "already running"}`. The guard uses a `try/finally` pattern to ensure cleanup on any failure.

### 10.2 SQLite Concurrency Model

- WAL mode enables concurrent reads during writes
- FK enforcement via process-wide monkey-patch in `core/db/connection.py`
- Connection timeout: 60s (prevents lock starvation)
- Flask: `get_db()` via `g._database`, `@teardown_appcontext` closes
- Background threads: raw `sqlite3.connect()` with mandatory `try/finally: conn.close()`

### 10.3 Data Loss Prevention (Stable 1.2)

- **SDL-1 patched:** Gravity score write failure in `ingest.py` now returns early instead of marking the signal as processed. Signal remains with `processed_at = NULL` for retry on next pipeline run.
- **SDL-2 patched:** Anomaly engine baseline write failures are logged with counts instead of silently dropped.
- **RL-1 patched:** `AnomalyEngine.run()` uses `try/finally: conn.close()` to prevent connection leaks.

---

## 11. Critical Code Rules

### 11.1 Future annotations — always line 2

```python
#!/usr/bin/env python3              # line 1
from __future__ import annotations  # line 2 — ALWAYS HERE
```

### 11.2 No datetime.utcnow()

Use `datetime.now(timezone.utc)`.

### 11.3 Schema changes require --migrate

`CREATE TABLE IF NOT EXISTS` does NOT update existing tables.

### 11.4 DB connections in background threads

```python
conn = sqlite3.connect(str(DB_PATH), timeout=10)
try:
    conn.commit()
finally:
    conn.close()
```

### 11.5 Blueprint route rules (Stable 1.2)

- All new routes go into the appropriate blueprint under `core/web/blueprints/`
- Import `get_db` from `core.web.helpers`, NOT from `app.py`
- Import mutable state from `core.web.state`, NOT as module-level dicts
- Use `@blueprint.route()`, not `@app.route()`
- Template paths are relative to the project root `templates/` directory
- Cross-blueprint `url_for()` calls use the `blueprint.endpoint` format (e.g., `url_for("cases.case_detail", case_id=1)`)

---

## 12. Architectural Decisions (Locked)

| Decision | Choice | Rationale |
|---|---|---|
| Database | SQLite WAL | Local-first; WAL allows concurrent reads |
| DB timeout | 60s | Prevents lock starvation |
| FK enforcement | Monkey-patch in `core/db/connection.py` | Python sqlite3 ignores URI `_foreign_keys` |
| Flask DB pattern | `get_db()` in `core/web/helpers.py` | Standalone function using Flask `g`; shared across all blueprints |
| Score storage | REAL floats 0.0-1.0 | Round only at template layer |
| Signal dedup | `INSERT OR IGNORE` on `external_id` | Idempotent — collectors safe to re-run |
| Collector discovery | `__manifest__` dict; AST-parsed | No config files |
| Collector dispatch | `subprocess.Popen` per collector | Crash isolation |
| Background jobs | `pipeline_jobs` + daemon threads | No Celery dependency |
| Actor confidence gate | `confidence >= 0.2` | Prevents low-quality actor pollution |
| Decay model | `score * e^(-lambda * hours)`, floor 0.05 | Exponential half-life per stream |
| Frontend | Leaflet, D3, Chart.js, HTMX | No SPA framework; server-rendered |
| Route architecture | 8 Flask Blueprints in `core/web/blueprints/` | Clean domain separation; app.py is thin factory |
| No Node.js | Ever | Hard constraint |

---

## 13. Known Tech Debt

Full ledger in `docs/tech_debt.md`. Active items:

| ID | Area | Severity | Status |
|---|---|---|---|
| CT-1 | `core/gravity.py` implemented, partially wired (feed route only) | MEDIUM | 41 tests pass; full route integration pending |
| P2-06 | spaCy en_core_web_sm not tuned for SA govt entities | HIGH | DPWI, HAWKS, SIU, NPA tagged MISC or missed |
| P3.2-05 | 6,458 scanned PDFs with < 100 chars in raw_text_cache | HIGH | OCR pipeline exists, needs A1-PENDING run |
| TD-13 | Case Alpha institutional bridge gap (CoE = 0.28) | HIGH | SAFLII bridge hunt needed |
| TD-20 | graph_nodes (463K) vs actors (1,011) imbalance | MEDIUM | Provenance audit needed |
| ~~AD-3~~ | ~~Schema duplication between SCHEMA_STATEMENTS and migrate_db()~~ | ~~MEDIUM~~ | **RESOLVED 2026-06-21** — duplicates removed, column drift patched |
| ~~AD-4~~ | ~~wiki_links missing ON DELETE CASCADE~~ | ~~LOW~~ | **RESOLVED 2026-06-21** — table-recreation migration applied |

---

## 14. Completed Architectural Transformations

### 14.1 Monolith Extraction (Stable 1.2.0, 2026-06-21)

**Before:** `app.py` was a 10,133-line monolith containing ~120 inline routes, telemetry functions, global state, and helper functions all inside a single `create_app()` factory.

**After:** `app.py` reduced to 1,297 lines (thin factory + schema + CLI). 8,777 lines extracted into:

| Component | Lines | Content |
|---|---|---|
| `core/web/helpers.py` | 174 | get_db, telemetry functions, config constants |
| `core/web/state.py` | 12 | Shared mutable registries |
| `admin_bp` | 2,369 | 29 routes: CRUD, dossiers, documents, alerts |
| `cases_bp` | 1,880 | 28 routes: workbench, pinning, briefings |
| `control_bp` | 1,221 | 24 routes: pipeline ops, dispatch, quarantine |
| `pages_bp` | 1,096 | 11 routes: dashboard, feed, timeline |
| `graph_bp` | 949 | 13 routes: D3, relationships, coalitions |
| `map_bp` | 576 | 6 routes: Leaflet, GeoJSON layers |
| `signals_bp` | 537 | 10 routes: triage, pulse, heatmap |
| `diagnostics_bp` | 426 | 11 routes: health, FMS, discovery |

**Impact:** Each domain is independently editable and testable. Merge conflicts reduced by ~80%. No runtime cost — all routes register at the same URL paths.

### 14.2 Concurrent Pipeline Mutex (Stable 1.2.0, 2026-06-21)

Added `_PIPELINE_ACTIVE` guard to `run_collectors`, `run_ingest`, `run_conclave`, `run_graph_engine`. Concurrent invocations return HTTP 409 instead of spawning parallel SQLite writers.

### 14.3 FK Index Substrate (Stable 1.2.0, 2026-06-21)

Added 16 missing FK indexes to SCHEMA_STATEMENTS and applied to live database. Total indexes: 49. Key additions: `graph_edges.source_node_id/target_node_id` (critical for D3 graph queries across 463K graph_nodes), `signal_actors.signal_id/actor_id`, `entity_relationships.subject/object_actor_id`.

### 14.4 Input Validation Hardening (Stable 1.2.0, 2026-06-21)

Replaced 14 bare `int()`/`float()` query parameter casts with safe `request.args.get(key, default, type=int/float)` across 8 routes. Added column allowlist to `update_job()` to close SQL injection vector. Added `try/except` guards on POST body integer parsing in `/api/sequence`.

### 14.5 Silent Data Loss Patches (Stable 1.2.0, 2026-06-21)

- SDL-1: Gravity score write failure returns early instead of falling through
- SDL-2: Anomaly baseline write failures logged with counts
- RL-1: AnomalyEngine.run() uses try/finally for conn.close()

### 14.6 Schema Parity (AD-3, Stable 1.2.0, 2026-06-21)

Removed 7 duplicate inline `CREATE TABLE` stanzas from `migrate_db()` that had drifted from `SCHEMA_STATEMENTS`. All table/index creation now driven by the single canonical array. Added explicit column migrations for `actor_network_metrics.community_id_socint` and `actor_network_metrics.influence_score` to close the drift gap.

### 14.7 Wiki Link Cascades (AD-4, Stable 1.2.0, 2026-06-21)

Updated `wiki_links` FK definitions to `ON DELETE CASCADE` in SCHEMA_STATEMENTS. Added table-recreation migration for existing databases. Wiki articles with links can now be safely deleted.

### 14.8 Production Security Gate (SEC-1, Stable 1.2.0, 2026-06-21)

`create_app()` raises `RuntimeError` when `FLASK_ENV=production` and either `FORGE_SECRET_KEY` or `FORGE_ADMIN_PASSWORD` matches the hardcoded default. Development mode is unaffected.

### 14.9 Deploy Hook Decoupling (SEC-4, Stable 1.2.0, 2026-06-21)

Vercel deploy hook URL moved from hardcoded constant in `tools/publish.py` to `os.environ.get("VERCEL_DEPLOY_HOOK_URL")`. Graceful skip with operator message when unset. Placeholder added to `env.example`.
