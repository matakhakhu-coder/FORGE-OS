# FORGE Substrate Audit Report

**Generated:** 2026-06-21 13:36 UTC
**Auditor:** FORGE Automated Sanitizer + Substrate Interrogation
**Database:** database.db (28.62 MB)
**Version:** Stable 1.2.0 (Post-Monolith Extraction)

---

## Integrity Verdict: FIELD READY

| Check | Result |
|---|---|
| PRAGMA integrity_check | **PASS** |
| PRAGMA foreign_key_check | **PASS** (0 violations) |
| Orphan records (32 FK relationships scanned) | **0 orphans** |
| Index count | 86 |
| Freelist pages | 0 (0.0% waste) |

---

## Core Table Counts

| Table | Rows | Purpose |
|---|---|---|
| `signals` | 24,578 | Atomic intelligence units |
| `actors` | 75 | Named entities in the graph |
| `events` | 2 | Escalated intelligence events |
| `cases` | 12 | Analyst case workspaces |
| `artifacts` | 291 | Documents, PDFs, media files |

## Junction & Relationship Tables

| Table | Rows | Purpose |
|---|---|---|
| `signal_actors` | 4,581 | Signal-to-actor links |
| `event_actors` | 0 | Event-to-actor links |
| `actor_events` | 2 | Actor-to-event links |
| `case_signals` | 151 | Pinned signals per case |
| `case_actors` | 40 | Pinned actors per case |
| `entity_relationships` | 43 | Directed actor-actor triples |

## Graph Substrate

| Table | Rows | Purpose |
|---|---|---|
| `graph_nodes` | 2,501 | Network graph vertices |
| `graph_edges` | 4,435 | Network graph edges |
| `actor_network_metrics` | 27 | Centrality scores (betweenness, PageRank) |
| `actor_coalitions` | 0 | Detected co-occurrence groups |
| `network_emergence` | 0 | Growth rate tracking |

## Intelligence Tables

| Table | Rows | Purpose |
|---|---|---|
| `signal_entities` | 450 | spaCy NER extractions |
| `signal_flags` | 0 | Counterintel flags |
| `sentinel_alerts` | 28 | Anomaly/threat alerts |
| `correlated_incidents` | 697 | Spatiotemporal signal clusters |
| `discovery_targets` | 25 | Evolution-suggested targets |
| `signal_baselines` | 1,633 | Anomaly detection baselines |

## FLUX / SOCINT Tables

| Table | Rows | Purpose |
|---|---|---|
| `socint_signals` | 130 | X/Twitter posts |
| `socint_resonance` | 10 | Stylometric similarity pairs |

## Pipeline Telemetry

| Table | Rows |
|---|---|
| `pipeline_runs` | 55 |
| `pipeline_jobs` | 34 |

---

## Signal Stream Distribution

| Stream | Count | Percentage |
|---|---|---|
| CRIME_INTEL | 21,660 | 88.1% |
| PRIORITY | 1,194 | 4.9% |
| INFRASTRUCTURE | 930 | 3.8% |
| GLOBAL | 794 | 3.2% |

## Top 10 Signal Sources

| Source | Count |
|---|---|
| ofac_sdn | 17,582 |
| dork | 1,385 |
| disease_outbreak_collector | 1,253 |
| news24_crime | 555 |
| timeslive_corruption | 476 |
| GDACS | 399 |
| saps_media | 363 |
| dailymaverick_corruption | 363 |
| groundup | 303 |
| municipal_infrastructure | 256 |

---

## Collector Fleet

**Active collectors:** 23 (20 OSINT in `forage/collectors/`, 2 SOCINT in `flux/collectors/`, 3 severed noise sources)
**Decommissioned:** 2 (ACLED, GDELT — underscore-prefixed, excluded from AST scanner)
**Concurrency governor:** `asyncio.Semaphore(4)` — max 4 simultaneous collectors

## FMS Modules

**Active modules:** 7 (coalition_detector, counterintel, emergence_engine, flux, geo_enrichment, graph_sync, signal_enrichment)
**Hooks registered:** on_signal (2), on_ingest (2)
**Engines registered:** 6

---

## Conclusion

Database structural integrity is verified clean across all 32 foreign key relationships. Zero orphan records, zero freelist waste, all 86 indexes current. The substrate is certified **FIELD READY** for production operations and external handoff.
