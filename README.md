# FORGE — Foundational Open Research & Graph Engine

> A local-first, analyst-grade OSINT intelligence operating system for monitoring real-world signals, building investigative cases, and mapping the relationships between actors, events, and evidence.

FORGE ingests publicly available information from 23 zero-dependency collectors, scores it through a multi-factor gravity engine, resolves entities through hybrid fuzzy matching, and surfaces patterns via automated coalition detection, anomaly alerting, and network emergence tracking — entirely on your local machine.

It is not a passive archive. It is an active investigative system.

---

## Who it's for

- **Investigative journalists** building and managing case files across multiple sources
- **Independent researchers** tracking real-world actors and events over time
- **Security and intelligence analysts** monitoring open-source signals for threat patterns
- **Developers** who want a self-hosted OSINT foundation to build on

## What FORGE does

- Collects signals from 23 sources: SA investigative news, parliamentary transcripts, court records, INTERPOL notices, OFAC sanctions, treasury procurement, disease surveillance, satellite data, and social media
- Scores every signal via a 5-factor gravity engine with stream-specific exponential decay
- Resolves actor names through a 3-tier hybrid lookup (exact, normalized, Jaro-Winkler fuzzy)
- Links signals to actors, events, and cases through an automated 10-stage ingestion pipeline
- Detects coalitions, counterintelligence anomalies, and emerging actor clusters via 7 pluggable forge modules
- Visualises the evidence network as a D3.js force-directed graph with centrality metrics
- Renders geographic intelligence on an interactive Leaflet.js map with multi-layer GeoJSON
- Generates static intelligence bulletins for public distribution via Vercel
- Operates entirely offline after setup — no cloud services, no paid APIs

---

## Tech stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.13 · Flask 3.1 |
| Database | SQLite (WAL mode) · FTS5 full-text search · 86 indexes |
| Frontend | Jinja2 · HTMX · Leaflet.js · D3.js · Chart.js |
| NLP | spaCy (en_core_web_sm) · custom SA EntityRuler (146 patterns) |
| Security | PDF detonation (pikepdf) · input sanitization · quarantine system |
| Publisher | Jinja2 static site generation · Vercel deployment |

---

## Getting started

### Prerequisites

- Python 3.9 or later
- pip
- A modern browser

### Installation

```bash
# Clone the repository
git clone https://github.com/matakhakhu-coder/FORGE-OS.git
cd FORGE-OS

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp env.example .env
# Edit .env — set FORGE_SECRET_KEY and FORGE_ADMIN_PASSWORD

# Initialise the database
python app.py --init-db
python app.py --migrate

# Run the collection pipeline (first run)
python tools/mega_ingest.py --collect-only

# Start the application
python app.py
```

Open your browser at `http://localhost:5000`

---

## Architecture

### Application structure

```
FORGE/
├── app.py                    ← Thin factory (1,207 lines) + schema + CLI
├── core/
│   ├── web/
│   │   ├── helpers.py        ← get_db(), telemetry, config constants
│   │   ├── state.py          ← Shared mutable registries
│   │   └── blueprints/       ← 8 domain-specific route modules
│   ├── pipeline/ingest.py    ← 10-stage signal processing orchestrator
│   ├── conclave/             ← FMS hook registry + engine runner
│   ├── fms/                  ← Module discovery, validation, activation
│   ├── db/connection.py      ← SQLite connection factory + FK enforcement
│   └── gravity.py            ← CT-1 Contextual Tunneling scorer
├── forage/
│   ├── collectors/ (23)      ← OSINT signal collectors
│   ├── engines/ (13)         ← Gravity, decay, correlation, graph, anomaly...
│   └── processors/           ← NER, entity resolution, triple extraction
├── flux/
│   ├── collectors/           ← SOCINT (X/Twitter) collectors
│   └── processors/           ← Stylometric analysis, resonance scoring
├── forge_modules/ (7)        ← Pluggable analytical modules
├── forge_security/           ← PDF detonation, input sanitization, audit
├── tools/
│   ├── mega_ingest.py        ← Pipeline runner (4 phases, Semaphore(4))
│   ├── publish.py            ← Static site generator → Vercel
│   └── sanitize_db.py        ← Database integrity checker + optimizer
└── publisher/                ← ZA-DIVERGENT static bulletin templates
```

### Route map — 151 endpoints across 10 blueprints

| Blueprint | Routes | Domain |
|---|---|---|
| `pages_bp` | 11 | Dashboard, feed, timeline, search, gallery |
| `signals_bp` | 10 | Signal triage, pulse, heatmap, decay |
| `cases_bp` | 28 | Case management, workbench, briefings |
| `admin_bp` | 29 | Entity CRUD, forensics, dossiers, alerts |
| `graph_bp` | 13 | D3/Vis-Network, relationships, coalitions |
| `map_bp` | 6 | Leaflet map, GeoJSON layers |
| `control_bp` | 24 | Collector dispatch, pipeline operations |
| `diagnostics_bp` | 11 | Health, FMS, discovery, evolution |
| `surface_bp` | 9 | Intelligence surface + FLUX discovery |
| `wiki_bp` | 5 | Wiki synthesis, graph data |

### Collector fleet — 23 active sources

| Category | Collectors |
|---|---|
| SA News | civic_intel (amaBhungane, Daily Maverick, News24, GroundUp, TimesLive, defenceWeb, The Citizen), civic_intel_us |
| Courts & Legal | saflii, courts_roll, fpb_enforcement |
| Government | pmg_parliamentary, bi196, treasury_tenders |
| Sanctions | ofac_sdn, sanctions_sa_fic, interpol_red_notices |
| Global Events | rss (GDACS), disease_outbreak |
| Documents | pdf_infiltrator, dork_collector |
| Social Media | x_pulse, x_search (FLUX/Nitter) |
| Actor Search | google_news_monitor |

All collectors are auto-discovered via AST manifest scanning. Drop a `.py` file with a `__manifest__` dict into `forage/collectors/` — it registers automatically on the next run.

### Data model

```
Signal ──── gravity_score ──── Actor
  │              │                │
  │         score_signal()        │
  │              │                │
  ├── case_signals ──── Case     ├── entity_relationships
  │                      │       │
  └── signal_entities    │       └── graph_edges
       (spaCy NER)       │            (co-occurrence)
                    case_actors
```

### Forge modules

| Module | Purpose |
|---|---|
| `coalition_detector` | Identifies actor groupings via co-occurrence analysis |
| `counterintel` | Flags narrative clusters, bot patterns, and information campaigns |
| `emergence_engine` | Detects early-stage network growth before events are visible |
| `signal_enrichment` | Enriches signal metadata during ingestion |
| `geo_enrichment` | Adds/corrects geographic coordinates |
| `graph_sync` | Syncs entity changes to the network graph |
| `flux` | Bridges SOCINT data into the main intelligence graph |

Modules are auto-attached at startup via the FMS (Forge Module System) and can be extended independently.

---

## Commands

```bash
# Server
python app.py                          # start on localhost:5000
python app.py --init-db                # create schema from scratch
python app.py --migrate                # apply column migrations

# Pipeline
python tools/mega_ingest.py            # full 4-phase pipeline run
python tools/mega_ingest.py --collect-only  # collection phase only

# Publishing
python tools/publish.py                # generate static site to dist/
python tools/publish.py --deploy       # generate + push to Vercel

# Maintenance
python tools/sanitize_db.py            # integrity check + orphan purge + VACUUM
python tools/sanitize_db.py --dry-run  # report only

# Individual collectors
python forage/collectors/<name>.py --dry-run
```

---

## Configuration

Copy `env.example` to `.env` and set:

| Variable | Required | Purpose |
|---|---|---|
| `FORGE_SECRET_KEY` | Yes | Flask session signing key |
| `FORGE_ADMIN_PASSWORD` | Yes | Admin panel authentication |
| `FLASK_ENV` | No | `development` (default) or `production` |
| `VERCEL_DEPLOY_HOOK_URL` | No | Vercel auto-deploy webhook |

See `env.example` for the full list of optional collector configuration variables.

---

## Project status

FORGE is at **Stable 1.2.1** — the monolith has been extracted into 8 Flask blueprints, the collector fleet expanded from 16 to 23 sources, and the database substrate is verified clean with 0 FK violations across 86 indexes.

Active development is focused on commercial transition: premium content gating, production automation, and dashboard integration. See `docs/COMMERCIAL_TRANSITION_SPRINTS.md` for the roadmap.

---

## Important

FORGE is built exclusively for open-source intelligence — publicly available information only. It is intended for lawful investigative, journalistic, and research use. Users are responsible for ensuring their use complies with applicable laws and ethical standards.

## License

FORGE uses a **hybrid open-core licensing model**:

- **Core framework** (app.py, core/, forage/, flux/, templates/, static/) — [GNU AGPLv3](LICENSE)
- **Analytical modules** (forge_modules/) — [Proprietary](forge_modules/LICENSE). Commercial license required for deployment.

The platform boots and operates fully without proprietary modules. See [docs/LICENSING.md](docs/LICENSING.md) for the complete policy.

For commercial licensing, enterprise deployment, or consulting: **matamelaramovha8@gmail.com**

---

FORGE — Matamela Ramovha
