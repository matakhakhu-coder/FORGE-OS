# FORGE — INTELLIGENCE OPERATING SYSTEM
### Feature Manifest & System Reference
### Classification: INTERNAL — OPERATIONAL BRIEF
### Build: Phase 32+ | Stack: Python / Flask / SQLite / Leaflet / D3.js

---

> *"You are being watched. The question is: by whom?"*

---

## SYSTEM IDENTITY

FORGE is a **local-first, analyst-grade intelligence operating system** built for the real-time collection, synthesis, and visual analysis of open-source intelligence (OSINT). It is not a log. It is not a dashboard. It is a system that builds a living, evolving intelligence graph from perception — ingesting raw signals from the world and converting them into structured knowledge: actors, events, cases, relationships.

Designed and operated out of South Africa. Primary focus: domestic and regional open-source intelligence, with global signal ingestion capability.

**Runtime:** Python 3.13 / Flask / SQLite (WAL mode)  
**Frontend:** Jinja2 templates / Leaflet.js / D3.js / Chart.js  
**Database:** Single-file SQLite — `database.db`  
**Aesthetics:** THE MACHINE / SAMARITAN / STANDARD (POV toggle)

---

## INTELLIGENCE LAYERS

FORGE operates across three data layers, toggled via the `lens` parameter on every route:

| Lens | Description |
|---|---|
| `LIVE` | Signals ingested from live collectors (GDELT, FIRMS, USGS, RSS, Civic Intel) |
| `ARCHIVE` (seed) | Manually seeded, curated intelligence from structured sources |
| `ALL` | Combined view across both layers |

---

## COLLECTION LAYER — FLUX (SOCINT ROOT)

FLUX is the Social Intelligence (SOCINT) parallel root of FORGE. It operates independently of FORAGE — separate collector, separate processors, separate schema tables — integrating with the core pipeline via the FMS `on_ingest` hook only. No changes to `core/pipeline/ingest.py`.

### X (Twitter) Dual-Mode Collector — `flux/collectors/x_pulse.py`

| Mode | Transport | Description |
|---|---|---|
| Primary | Nitter RSS (`xml.etree.ElementTree`) | Scrapes Nitter instances for target account tweets |
| Fallback | X API v2 → v1.1 | Guest token endpoint; falls back to v1.1 on 403 |

- **Deduplication:** `INSERT OR IGNORE` on `external_id` (tweet ID from URL)
- **Dual-write:** Every tweet writes to `signals` (global pipeline) AND `socint_signals` (FLUX-specific)
- **Manifest:** `id = "x_pulse"` — autodiscovered by `_load_collector_registry()` alongside FORAGE collectors
- **Target rotation:** `NITTER_INSTANCES` list rotated on failure; configurable via `FLUX_NITTER_INSTANCES` env var

### Stylometric Engine — `flux/processors/stylometric.py`

Zero external dependencies. Runs on Python stdlib only.

**Resonance Formula:**
```
R = 0.35 × sequence_similarity   (difflib on normalized text)
  + 0.25 × cashtag_jaccard        ($TICKER set overlap)
  + 0.20 × emoji_bigram_cosine    (ordered emoji-pair vector cosine)
  + 0.10 × caps_proximity         (ALL-CAPS density alignment)
  + 0.10 × leet_proximity         (leetspeak substitution density)
```

**Corpus gate:** >= 7 tweet samples AND >= 2000 total characters before any score is emitted.

**Thresholds:**
| Constant | Value | Purpose |
|---|---|---|
| `RESONANCE_THRESHOLD` | 0.65 | Minimum score to write `socint_resonance` row |
| `GRAPH_INJECT_THRESHOLD` | 0.70 | Minimum score to inject `stylometric_match` edge into `entity_relationships` |

### FMS Module — `forge_modules/flux/`

| File | Role |
|---|---|
| `manifest.json` | Module declaration — name, version, engines, hooks, capabilities |
| `engine.py` | `flux_socint_engine` — returns `AnalysisResult` for x_pulse signals; gravity capped at 0.55 (behavioural signals must not inflate OSINT escalation rates) |
| `module.py` | `register(conclave)` — attaches engine + `on_ingest` hook; all imports inside function per FMS contract |

**`on_ingest` hook (post-Conclave, x_pulse only):**
1. Resolves linked actors from `signal_actors`
2. Extracts full stylometric fingerprint
3. Appends tweet content to each actor's rolling corpus (`actors.socint_profile`)
4. Scores against corpus when gate passes
5. Writes `socint_resonance` score + `socint_tags` (cashtags, hashtags, emojis, leet/aggression density) back to `signals` row

### Resonance Batch Engine — `flux/processors/resonance.py`

O(n²) pairwise actor comparison run on-demand or scheduled.

| Phase | Description |
|---|---|
| 1 — Load | `_load_actor_fingerprints()` — loads only actors with corpus-ready profiles |
| 2 — Compare | `_run_pairwise()` — all actor pairs; writes to `socint_resonance`; injects `stylometric_match` edges above threshold |
| 3 — Community | `_run_socint_communities()` — NetworkX `greedy_modularity_communities` on stylometric subgraph → `community_id_socint` in `actor_network_metrics` |

`_ordered_pair(a, b) = (min(a,b), max(a,b))` enforces `actor_a < actor_b` at application layer (DB CHECK constraint is backstop).

---

## COLLECTION LAYER — FORAGE

The automated collection subsystem. Runs on schedule or via `mega_ingest.py`.

### Active Collectors

| Collector | Source | Type | Priority Detection |
|---|---|---|---|
| `gdelt_collector.py` | GDELT Project | News events (global) | Keyword-based |
| `civic_intel_collector.py` | amaBhungane, Daily Maverick, GroundUp, MyBroadband + 8 others | South African civic media | Keyword + source tier |
| `firms_collector.py` | NASA FIRMS MODIS NRT | Wildfire / thermal anomaly | FRP threshold + keywords |
| `rss_collector.py` | GDACS Combined RSS | Disaster/crisis alerts | 14-keyword priority list |
| `earthquake_collector.py` | USGS GeoJSON (1hr, M2.5+) | Seismic events | Magnitude threshold |
| `usgs_collector.py` | USGS GeoJSON (1day, M2.5+) | Seismic events (extended) | M6.0+ priority |
| `ndbc_collector.py` | NOAA NDBC buoy network | Marine meteorology (wave height, wind, SST, pressure) | WVHT ≥ 4.0 m or WSPD ≥ 15.0 m/s |

All collectors are idempotent — `INSERT OR IGNORE` on `external_id`. Safe to run repeatedly.

**NDBC collector — dual-mode:**
- First run per station → `realtime2/{id}.txt` (up to 45 days of hourly observations, backfill)
- Subsequent runs → RSS feed (last ~5 observations, low overhead)
- Station IDs configured via `NDBC_STATIONS` env var (comma-separated). Browse: ndbc.noaa.gov/obs.shtml
- Stream: `INFRASTRUCTURE` — slowest decay rate (0.006 λ), appropriate for persistent marine conditions
- CLI: `python forage/collectors/ndbc_collector.py --stations 41049,13008 [--backfill] [--list-meta]`

### Collection Orchestration — `tools/mega_ingest.py`

Three-phase runner:

**Phase 1 — Async Collection:** All collectors run concurrently in a single event loop via `asyncio.gather()` with `nest_asyncio` for nested loop compatibility.

**Phase 2 — Sync Engines:** Heuristic analysis engines run sequentially after collection — artifact processing, NER, clustering, anomaly detection, correlation, decay, evolution, graph, sentinel.

**Phase 3 — Conclave Ingestion:** All signals in the database are passed through the full synthesis pipeline. Produces actors, events, cases, and relationship links.

---

## SYNTHESIS LAYER — THE CONCLAVE

The autonomous intelligence synthesis system. Converts raw signals into structured knowledge.

### Pipeline (per signal, in order)

```
SignalInterpreter    → extracts severity, actors, event_type, credibility
EntityResolver      → resolves actor names against existing actor records
ProcessorManager    → creates artifact record from signal
EventConstructor    → constructs preliminary event structure
GravityEngine       → scores signal urgency (0.0–1.0)
CaseEngine          → evaluates whether signal warrants a case
FeedbackEngine      → adjusts scores based on actor history
run_conclave()      → merges all analysis into a single AnalysisResult
materialize_entities() → creates/updates Actor records if confidence ≥ 0.2
handle_escalation() → creates Events (gravity ≥ 0.35) or Cases (gravity ≥ 0.55)
link_signal_actors() → writes signal → actor relationships
link_event_actors()  → writes event → actor relationships
```

### Gravity Scoring

```
gravity = (0.35 × severity +
           0.25 × actor_importance +
           0.15 × frequency +
           0.15 × urgency_sentiment +
           0.10 × source_credibility) × momentum

momentum = 0.8 + 0.2 × frequency
```

Current calibrated range: `0.0 – 0.65` (keyword-based NLP)  
MONITOR threshold: `0.35` | ESCALATE threshold: `0.55`

### Confidence Gate
Actors are only created when `conclusion.confidence ≥ 0.2`. This prevents low-quality signals from polluting the actor registry.

### Rate Limiting
Maximum 5 auto-generated cases per hour. Prevents runaway case creation during high-velocity ingestion windows.

---

## PROCESSING LAYER — FORAGE PROCESSORS

Post-collection enrichment pipeline.

| Processor | Function |
|---|---|
| `artifact_processor.py` | PDF text extraction (PyMuPDF), OCR (Tesseract), NER (spaCy). Manages `processing_status` lifecycle: `pending → processing → done/failed/skipped` |
| `ner_processor.py` | Standalone spaCy NER pass on signal titles/content. Extracts PERSON, ORG, GPE into `signal_entities` table |
| `sentinel.py` | Alert monitoring. Watches for threshold breaches and generates `sentinel_alerts` |
| `signal_interpreter.py` | Keyword-based severity scoring, actor extraction, event type classification |
| `entity_resolver.py` | Matches extracted entity names against existing actor records (fuzzy dedup) |
| `event_constructor.py` | Constructs preliminary event objects from interpreted signals |

---

## ANALYSIS ENGINES — FORAGE ENGINES

| Engine | Function | Schedule |
|---|---|---|
| `anomaly_engine.py` | Detects statistical deviations from rolling baselines per stream | On-demand / scheduled |
| `cluster_engine.py` | Groups geographically and thematically related signals | Per ingest cycle |
| `correlation_engine.py` | Scores signal-to-signal correlation by entity, location, time | Per ingest cycle |
| `decay_engine.py` | Applies exponential relevance decay `score × e^(−λ × hours)`. λ varies by stream: CRIME_INTEL=0.020, INFRASTRUCTURE=0.006, PRIORITY=0.003, GLOBAL=0.012 | Every 6 hours |
| `evolution_engine.py` | Identifies emerging actor/entity pairs from co-occurrence patterns | On-demand |
| `graph_engine.py` | Computes network metrics: betweenness, eigenvector, PageRank, community detection | On-demand |
| `gravity_engine.py` | Multi-factor urgency scoring engine | Per signal, inline |
| `escalation_engine.py` | Creates events and cases from high-gravity signals | Per signal, inline |
| `entity_engine.py` | Idempotent actor materialisation from Conclave output | Per signal, inline |
| `relationship_engine.py` | Links actors to signals and events in `signal_actors` / `event_actors` | Per signal, inline |
| `case_engine.py` | Evaluates case worthiness from gravity + actor + event data | Per signal, inline |
| `feedback_engine.py` | Adjusts gravity based on actor influence history | Per signal, inline |

---

## DATABASE SCHEMA

### Core Tables

| Table | Purpose |
|---|---|
| `signals` | Raw intelligence signals from all collectors. Central fact table. |
| `actors` | People, institutions, movements, governments identified across signals |
| `events` | Structured intelligence events constructed from signals |
| `cases` | Analyst workspaces grouping related signals, events, and actors |
| `artifacts` | Document/media evidence linked to events |
| `priorities` | Named priority watchlist items |
| `sentinel_alerts` | Automated threshold breach alerts |

### Relationship Tables

| Table | Purpose |
|---|---|
| `actor_events` | Manual analyst links: actor ↔ event |
| `signal_actors` | Automated pipeline links: signal ↔ actor (via relationship_engine) |
| `event_actors` | Automated pipeline links: event ↔ actor (via relationship_engine) |
| `entity_relationships` | Named relationships between actors (Phase 22). `relation_type='stylometric_match'` rows written by FLUX |
| `actor_network_metrics` | Computed graph metrics per actor. Includes `community_id_socint` (C-SOCINT pass) |
| `case_actors` | Actors pinned to case workspaces |

### FLUX / SOCINT Tables

| Table | Purpose |
|---|---|
| `socint_signals` | FLUX-specific signal store. FK to `signals.signal_id`. Stores x_handle, cashtags, hashtags, emoji_count, leet_density |
| `socint_resonance` | Pairwise actor stylometric scores. `actor_a < actor_b` CHECK constraint. `resonance_score REAL [0.0–1.0]` |

### FLUX Columns on Core Tables

| Table | Column | Type | Description |
|---|---|---|---|
| `signals` | `socint_tags` | TEXT (JSON) | Cashtags, hashtags, emojis, leet/aggression density extracted at ingest |
| `signals` | `socint_resonance` | REAL | Best resonance score across all linked actors for this signal |
| `actors` | `socint_profile` | TEXT (JSON) | Rolling tweet corpus + x_handles + x_display_names |
| `actor_network_metrics` | `community_id_socint` | INTEGER | C-SOCINT community assignment (stylometric subgraph) |

### Signal Fields of Note

| Column | Type | Description |
|---|---|---|
| `signal_id` | TEXT PK | UUID — primary key for all pipeline references |
| `external_id` | TEXT UNIQUE | Source-native ID — idempotency key |
| `stream` | TEXT | CRIME_INTEL / INFRASTRUCTURE / PRIORITY / GLOBAL |
| `source_type` | TEXT | live / seed — lens classification |
| `is_priority` | INTEGER | 1 if keyword-matched as priority on ingest |
| `gravity_score` | REAL | Conclave-assigned urgency score |
| `relevance_score` | REAL | Decay-adjusted relevance (floor: 0.05) |
| `processing_status` | TEXT | pending / processing / done / failed / skipped |
| `conclave_meta` | TEXT | JSON — stores gravity, actors, event_id, case_id from Conclave |

---

## WEB INTERFACE

### Pages

| Route | Description |
|---|---|
| `/` | Dashboard — signal pulse, heatmap, top actors, correlations, sentinel alerts |
| `/feed` | Ranked analyst intelligence feed (Phase 29) |
| `/signals` | Signal monitor — full signal table with stream/priority filters |
| `/events` | Event registry |
| `/actors` | Actor registry with signal intelligence targeting overlay |
| `/actor/<id>` | Actor detail — events, artifacts, co-actors, network position, signal intel |
| `/cases` | Case workspace list |
| `/cases/<id>` | Case detail — pinned signals, events, actors, briefing |
| `/map` | Leaflet geographic explorer — signal clusters, correlations, case pins |
| `/graph` | D3.js intelligence graph — actor-event network with community detection |
| `/timeline` | Chronological event analysis |
| `/artifacts` | Evidence gallery |
| `/artifact/<id>` | Artifact detail with forensic analysis panel |
| `/search` | FTS5 full-text search across signals, events, actors, artifacts |
| `/wiki/` | Intelligence wiki — auto-generated article layer |
| `/discovery` | Evolution engine candidates — emerging entities awaiting approval |
| `/evolution` | Parsed intelligence from Google News RSS sources |
| `/diagnostics` | System Control Room — pipeline health, run logs, engine status |
| `/dossier/actor/<id>` | Printable actor dossier — print-first, A4, minimal chrome |
| `/dossier/event/<id>` | Printable event dossier |
| `/document/actor/<id>` | Arkadia-style rich intelligence brief — dark theme, Chart.js charts, flow pipeline diagram, evidence grid |
| `/document/event/<id>` | Rich event intelligence brief |
| `/document/signals` | Signal stream brief — case-independent aggregate (`?stream=CRIME_INTEL&days=14` params). Reads raw `signals` table directly; no case pinning required |

### Document Brief Engine — `templates/document_brief.html`

A case-independent, rich-output document layer. Generates self-contained HTML intelligence briefs for any subject (actor, event, or signal stream) without requiring the subject to be pinned to a case.

**Route builders** (all inside `create_app()` closure in `app.py`):

| Function | Input | Output |
|---|---|---|
| `_build_actor_document(actor, ctx)` | `_dossier_actor_data()` output | Polar-area role distribution, artifact-type doughnut, event timeline grid |
| `_build_event_document(event, ctx)` | `_dossier_event_data()` output | Source distribution polar-area, actor-type doughnut, evidence inventory grid |
| `_document_signals_data(db, stream, days)` | Raw `signals` table | Stream distribution, gravity tier breakdown, top signals by gravity |
| `_build_signals_document(data)` | Above | Full document_brief context dict |

**Navigation entry points:**
- Actor detail page → "✦ Rich Brief" button (next to "Generate Dossier")
- Event detail page → "✦ Rich Brief" button (next to "Generate Dossier")
- Signal Monitor page → "✦ Rich Brief" button in actions bar

**No schema changes required.** Queries existing `signals`, `actors`, `events`, `artifacts`, `actor_events`, `source_breakdown` tables only.

### API Surface (selected)

| Endpoint | Purpose |
|---|---|
| `/api/pulse` | Signal frequency time-series for Chart.js |
| `/api/heatmap` | `[lat, lng, intensity]` for Leaflet.heat |
| `/api/feed` | Ranked intelligence feed with FIRMS noise reduction |
| `/api/graph_data` | D3.js force graph data filtered by actor type + event category |
| `/api/geo` | GeoJSON signal/event points for map |
| `/api/signals/geojson` | Live signal layer for Leaflet |
| `/api/clusters/geojson` | Cluster centroid layer for Leaflet |
| `/api/correlations/geojson` | Correlation arc layer for Leaflet |
| `/api/decay/run` | Trigger on-demand relevance decay pass |
| `/api/anomaly/run` | Trigger anomaly detection pass |
| `/api/graph/recalculate` | Trigger graph metric recalculation |
| `/api/evolution/run` | Trigger evolution engine scan |
| `/api/sentinel/run` | Trigger sentinel alert pass |
| `/api/diagnostics` | Full pipeline health JSON |
| `/api/actor/<id>/socint` | FLUX SOCINT dossier — corpus stats + top 3 stylometric matches for one actor |

---

## AESTHETIC SYSTEM

FORGE includes a POV toggle that transforms the visual language of the entire interface.

| Mode | Identity | Accent | Style |
|---|---|---|---|
| STANDARD | Neutral analyst | System default | Clean, minimal |
| THE MACHINE | Asset identification | `#ffcc00` yellow | Dot-grid, bracket corners, scan animations |
| SAMARITAN | Threat classification | `#ff0000` red | Uppercase, aggressive minimalism, red borders |

POV state persists across page navigation via `localStorage`. Toggle is available in the topbar on every page.

### Targeting Component
Actors with `gravity_score ≥ 0.55` or any linked `is_priority = 1` signal receive visual targeting overlays:

- **THE MACHINE:** Yellow animated bracket box, `ASSET IDENTIFIED` label, scan-line sweep on page load, gravity score and signal count displayed
- **SAMARITAN:** Red pulse border, `THREAT CLASSIFIED` label, uppercase styling

Threat levels: `NONE / MONITORED / ELEVATED / CRITICAL`

---

## SIGNAL DECAY MODEL

```
relevance_score = initial_score × e^(−λ × hours_elapsed)

Stream          λ         Half-life
CRIME_INTEL     0.020     ~35 hours
INFRASTRUCTURE  0.006     ~5 days
PRIORITY        0.003     ~10 days
GLOBAL          0.012     ~58 hours

Floor: 0.05 (signals never fully disappear)
Priority signals start at 1.5 × initial_score
```

---

## DIRECTORY STRUCTURE

```
FORGE/
├── app.py                        ← Primary web application entry point (Flask)
├── FORGE_OS_MANIFEST.md          ← This document
├── README.md                     ← Project overview & quickstart
├── requirements.txt              ← Python dependencies
├── env.example                   ← Environment variable template
├── .env                          ← Runtime secrets (not committed)
├── .gitignore
├── database.db                   ← SQLite database (WAL mode, ~650MB)
│
├── core/                         ← Synthesis & orchestration layer
│   ├── gravity.py
│   ├── api/                      ← Flask route definitions
│   ├── conclave/                 ← Context, engine, registry
│   ├── db/                       ← DB connection & wiki helpers
│   ├── diagnostics/              ← Health check endpoints
│   ├── fms/                      ← Forge Module System bootstrap
│   └── pipeline/                 ← Ingest, intelligence, synthesizer
│
├── forage/                       ← Collection & processing layer
│   ├── collectors/               ← 9 live data collectors (GDELT, FIRMS, USGS, RSS…)
│   ├── engines/                  ← 14 analysis engines (decay, graph, gravity…)
│   ├── processors/               ← 10 enrichment processors (NER, entity, sentinel…)
│   └── utils/                    ← Pipeline logging & admiralty helpers
│
├── flux/                         ← FLUX SOCINT root (parallel to forage/)
│   ├── __init__.py               ← Package root, __version__ = "0.1.0"
│   ├── collectors/
│   │   └── x_pulse.py            ← X dual-mode collector (Nitter RSS + guest API)
│   └── processors/
│       ├── stylometric.py        ← Fingerprint engine + corpus management (stdlib only)
│       └── resonance.py          ← O(n²) batch resonance engine + C-SOCINT community pass
│
├── forge_modules/                ← Analytical capability modules (FMS auto-discovered)
│   ├── flux/                     ← FLUX FMS module
│   │   ├── manifest.json         ← Module contract declaration
│   │   ├── engine.py             ← flux_socint_engine (AnalysisResult for x_pulse)
│   │   └── module.py             ← register(conclave) — engine + on_ingest hook
│   ├── coalition_detector/
│   ├── counterintel/
│   ├── emergence_engine/
│   ├── geo_enrichment/
│   ├── graph_sync/
│   └── signal_enrichment/
│
├── forge_security/               ← Input sanitisation & security layer
│
├── tools/                        ← Standalone utility & runner scripts
│   ├── mega_ingest.py            ← Four-phase pipeline runner (main operator tool)
│   ├── nexus_bridge.py           ← Actor–signal relationship bridge
│   ├── coalition_interceptor.py  ← Coalition pattern interceptor
│   ├── anchor_npa.py             ← NPA actor anchor utility
│   ├── capture_npa_signal.py     ← NPA signal capture
│   ├── check_coalitions.py       ← Coalition debug checker
│   ├── sentinel_signal_simulator.py
│   ├── run_coalition_debug.py
│   ├── seed_data.py / seed_cases.py / seed_cache.py
│   ├── flight_log_matcher.py
│   ├── backfill_streams.py
│   ├── purge_signals.py
│   ├── schema_diag.py
│   ├── vec_a_direct_ocr.py
│   ├── test_ocr.py
│   └── compile_red_folder.py
│
├── migrations/                   ← Schema migrations & database repair scripts
│   ├── schema.sql                ← Canonical schema definition
│   ├── add_socint_columns.py     ← Phase A FLUX migration: socint_signals, socint_resonance, FLUX columns
│   ├── migrate_archive.py
│   ├── migrate_graph.py
│   ├── migrate_layer_separation.py
│   ├── fix_schema.py
│   ├── fix_wiki_links.py
│   ├── repair_db.py
│   └── repair_nexus_graph.py
│
├── maintenance/                  ← Idempotent housekeeping scripts
│   ├── cleanup_actors.py
│   ├── cleanup_firms.py
│   └── system_decontamination.py
│
├── bin/                          ← Windows batch worker launchers
│   ├── decay_worker.bat          ← Runs decay_engine every 6 hours
│   └── wiki_worker.bat           ← Runs full wiki synthesis pipeline
│
├── docs/                         ← Supplementary documentation
│   ├── FORGE_DEBUG_SECURITY.md
│   ├── FORGE_PIPELINE_CONTRACTS.md
│   ├── ROADMAP.md
│   └── structure.txt
│
├── .archive/                     ← Superseded versioned files (reference only)
│   └── nexus_bridge_v0.py
│
├── tests/                        ← Unit & integration tests
├── wiki/                         ← Auto-generated knowledge base articles
├── surface/                      ← Pattern & emergence detection layer
├── scripts/                      ← Ancillary shell/batch scripts
├── static/                       ← CSS, JS, media assets
├── templates/                    ← Jinja2 HTML templates
├── logs/                         ← Runtime logs
└── media/                        ← Ingested documents & media files
```

---

## DEPLOYMENT NOTES

**Run sequence (clean start):**
```powershell
python app.py --init-db              # initialise schema
python migrations\fix_schema.py      # apply column patches + relationship tables
python tools\mega_ingest.py          # collect + synthesise
python app.py                        # serve
```

**Scheduled operations:**
```
tools\mega_ingest.py    → run every 15–30 minutes for live collection
forage\engines\decay_engine.py   → run every 6 hours (bin\decay_worker.bat)
```

**Database:** Single file at `database.db` in project root. WAL mode enabled. Timeout: 60s. Do not open with external SQLite tools while `tools\mega_ingest.py` is running.

---

## COPYRIGHT & ATTRIBUTION NOTICE

FORGE is proprietary software. All source code, pipeline architecture, schema design, aesthetic system, and intelligence methodology described in this document are the intellectual property of the project owner.

Third-party data sources used under their respective open-access licences:
- **GDELT Project** — gdeltproject.org (open data, academic/research use)
- **NASA FIRMS** — firms.modaps.eosdis.nasa.gov (public domain, US Government)
- **USGS Earthquake Hazards** — earthquake.usgs.gov (public domain, US Government)
- **GDACS** — gdacs.org (open access, UN-OCHA)
- **Civic media sources** — accessed via public RSS feeds under fair use for non-commercial intelligence research

Third-party libraries:
- Flask (BSD), SQLite (public domain), Leaflet.js (BSD-2), D3.js (ISC), Chart.js (MIT), spaCy (MIT), PyMuPDF (AGPL), pytesseract (Apache 2.0), nest_asyncio (BSD)

---

*Document generated from live system analysis — FORGE Stable 1.1.2 + Project FLUX*  
*Build date: 2026-05-02*