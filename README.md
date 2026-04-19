# FORGE — Foundational Open Research & Graph Engine

> *A local-first, open-source intelligence platform for monitoring real-world signals, building investigative cases, and mapping the relationships between actors, events, and evidence.*

FORGE is built on the analytical structures used in intelligence analysis and investigative journalism. It ingests publicly available information, organises it into a connected evidence network, and surfaces patterns across actors, events, and signals — entirely on your local machine, with no cloud dependencies and no external services.

It is not a passive archive. It is an active investigative system.

---

## Who It's For

- **Investigative journalists** building and managing case files across multiple sources
- **Independent researchers** tracking real-world actors and events over time
- **Security and intelligence analysts** monitoring open-source signals for threat patterns
- **Developers** who want a self-hosted OSINT foundation to build on top of

---

## What FORGE Does

- Ingest and archive OSINT artifacts — documents, images, video, audio, news screenshots, and web captures
- Link evidence to real-world events and the actors involved in them
- Full-text search across all metadata using SQLite FTS5
- Monitor live and scraped signals through the Signal Monitor and contextual feed
- Detect coalitions, counterintelligence patterns, and emerging actor clusters via forge modules
- Visualise events geographically on an interactive Leaflet.js map
- Render the full evidence network as a D3.js force-directed intelligence graph
- Tag every artifact with source authenticity indicators
- Operate entirely offline — no cloud services, no paid APIs, no external dependencies after setup

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+ / Flask |
| Database | SQLite with FTS5 full-text search |
| Frontend | HTML / CSS / Vanilla JavaScript |
| Map Engine | Leaflet.js |
| Graph Engine | D3.js |
| Media Storage | Local filesystem |

---

## Getting Started

### Prerequisites
- Python 3.9 or later
- pip
- A modern browser (Chrome, Firefox, or Edge)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/matakhakhu-coder/FORGE-OS.git
cd FORGE-OS

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your environment variables
cp env.example .env
# Edit .env and fill in your secret key and admin password

# 4. Initialise the database schema
python app.py --init-db
python migrations/fix_schema.py   # apply column patches + relationship tables

# 5. Seed the pipeline (first run)
python tools/mega_ingest.py       # collect + synthesise (runs all 4 phases)

# 6. Start the application
python app.py
```

Then open your browser at: **http://localhost:5000**

---

## Interface Modules

| Route | Module |
|---|---|
| `/` | Archive Home — search, recent artifacts, navigation |
| `/search` | Full-text search across all artifact metadata |
| `/timeline` | Chronological browse by year and month |
| `/map` | Interactive Leaflet map — click events to explore |
| `/actors` | Actor profiles with linked events and artifact counts |
| `/events` | Filterable event list with artifacts and actors inline |
| `/graph` | D3.js intelligence graph — the full evidence network |
| `/feed` | Contextual signal feed — gravity-filtered by active case |
| `/signals` | Signal Monitor — real-time and scraped signal tracking |
| `/surface` | Surface layer — pattern and emergence detection |
| `/admin` | Password-protected artifact ingestion and management |

---

## Data Model

```
Artifact ──────► Event ──────► Actor
    │                               ▲
    └───────────────────────────────┘
```

- **Artifacts** — individual evidence items (documents, photos, video, audio, news, web captures)
- **Events** — real-world moments that give artifacts context and causality
- **Actors** — people, institutions, movements, and organisations that appear across events

---

## Directory Structure

```
FORGE/
├── app.py                  ← Flask entry point
├── requirements.txt        ← Python dependencies
├── .env                    ← Runtime secrets (not committed)
├── database.db             ← SQLite database (~650MB, WAL mode)
│
├── core/                   ← Conclave synthesis engine, FMS, API routes
├── forage/                 ← Collectors, engines, processors
├── forge_modules/          ← Analytical capability modules
├── forge_security/         ← Input sanitisation layer
│
├── tools/                  ← Standalone operator scripts (mega_ingest, nexus_bridge…)
├── migrations/             ← Schema migrations & DB repair (fix_schema, repair_db…)
├── maintenance/            ← Housekeeping (cleanup_actors, system_decontamination…)
├── bin/                    ← Windows batch workers (decay_worker.bat, wiki_worker.bat)
│
├── docs/                   ← Supplementary docs (ROADMAP, pipeline contracts, debug)
├── .archive/               ← Superseded versioned files
├── tests/                  ← Unit & integration tests
├── wiki/                   ← Auto-generated intelligence knowledge base
└── static/ templates/ surface/ logs/ media/
```

---

## Forge Modules

FORGE uses a modular architecture for analytical capabilities. Active modules:

| Module | Purpose |
|---|---|
| `coalition_detector` | Identifies actor groupings and coordination patterns |
| `counterintel` | Flags anomalous actor behaviour and potential deception signals |
| `emergence_engine` | Detects early-stage patterns before they become visible events |
| `archive_engine` | Long-term evidence archiving and retrieval |

Modules are auto-attached at startup via the FMS (Forge Module System) and can be extended independently.

---

## Current Development Phase

See [ROADMAP.md](./docs/ROADMAP.md) for the active phase and task checklist.

Currently at **Phase 41** with **CT-1: Contextual Tunneling** in progress — gravity-based feed and signal filtering anchored to an active case context, so analysts see what is relevant rather than the full firehose.

---

## Project Status

FORGE is in active development and has been made public to invite collaboration. The core archive and intelligence engine are functional. Modules and surface layers are being added phase by phase.

If you are a developer interested in contributing, start with the [ROADMAP](./ROADMAP.md) and open a GitHub Issue to discuss.

---

## Important

FORGE is built exclusively for **open-source intelligence** — publicly available information only. It is intended for lawful investigative, journalistic, and research use. Users are responsible for ensuring their use complies with applicable laws and ethical standards.

---

## License

MIT License — free to use, modify, and distribute.

---

*FORGE — Matamela Ramovha*
