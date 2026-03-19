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

All collectors are idempotent — `INSERT OR IGNORE` on `external_id`. Safe to run repeatedly.

### Collection Orchestration — `mega_ingest.py`

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
| `entity_relationships` | Named relationships between actors (Phase 22) |
| `actor_network_metrics` | Computed graph metrics per actor (Phase 21) |
| `case_actors` | Actors pinned to case workspaces |

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
| `/dossier/actor/<id>` | Printable actor dossier |

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

## DEPLOYMENT NOTES

**Run sequence (clean start):**
```powershell
python app.py --init-db       # initialise schema
python fix_schema.py          # apply column patches + relationship tables
python mega_ingest.py         # collect + synthesise
python app.py                 # serve
```

**Scheduled operations:**
```
mega_ingest.py    → run every 15–30 minutes for live collection
decay_engine.py   → run every 6 hours (decay_worker.bat)
```

**Database:** Single file at `database.db` in project root. WAL mode enabled. Timeout: 60s. Do not open with external SQLite tools while `mega_ingest.py` is running.

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

*Document generated from live system analysis — FORGE Phase 32+*  
*Build date: 2026-03-19*