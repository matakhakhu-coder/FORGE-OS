# FORGE Substrate Audit Report — April 2026
**Executed:** 2026-04-19 | **Phase:** 70 — Engineering Validation & Documentation Lock
**Auditor:** FORGE Automated Interrogation (Claude / Phase 70)
**SOP Reference:** `docs/SUBSTRATE_INTERROGATION_SOP.md` v1.0

> **Phase 71 Amendment — 2026-04-19:** Surgical strike executed. All 127 `event_actors` orphans
> purged (126 event_id orphans + 1 actor_id orphan absorbed in Strike 1). Orphaned rows backed up
> to `orphaned_event_actors` quarantine table before deletion. Full zero-trust re-check returned
> **0 orphans across all 8 FK checks**. Integrity verdict upgraded from **AMBER → FIELD READY**.

---

## Section 1 — Stack Detection

| Parameter | Value | Status |
|---|---|:---:|
| `sqlite_version()` | **3.50.4** | ✅ |
| `ENABLE_DBSTAT_VTAB` | Not compiled | ⚠️ |
| `foreign_keys` | **1** | ✅ |
| `dbstat` virtual table | Unavailable | ⚠️ |

**Stack Profile:** SQLite 3.50.4 — modern, capable engine. `dbstat` VTAB absent; volumetric analysis performed via `COUNT(*)` fallback (SOP §2.2 Fallback). Foreign key enforcement is **active and process-wide** via `core/db/connection.py` monkey-patch (Phase 68).

---

## Section 2.1 — Structural Map

Full schema contains **52 user tables** (excluding FTS shadow tables and `sqlite_sequence`).

### Core Operational Tables

| Table | Classification |
|---|---|
| `actors` | Entity Registry |
| `signals` | Signal Reservoir |
| `artifacts` | Evidence Store |
| `events` | Event Layer |
| `entity_relationships` | Graph Routing Layer |
| `signal_actors` | Signal→Actor Junction |
| `event_actors` | Event→Actor Junction |
| `case_actors` | Case→Actor Junction |
| `cases` | Case Management |
| `sentinel_alerts` | Alert Layer |
| `discovery_targets` | Observer Seed Registry |

### Extended Tables

| Table | Classification |
|---|---|
| `actor_coalitions` | Graph Clustering |
| `actor_network_metrics` | Graph Metrics Cache |
| `actor_weights` | Gravity Weighting |
| `artifact_duplicates` | Deduplication Registry |
| `case_artifacts`, `case_events`, `case_signals`, `case_feedback` | Case Sub-Junctions |
| `correlated_incidents` | Cross-Signal Correlation |
| `graph_edges`, `graph_nodes` | Pre-computed Graph Projection |
| `network_emergence` | Coalition Matrix Cache |
| `observer_promotion_log` | NER Promotion Audit |
| `orphaned_entity_relationships`, `orphaned_event_actors` | Quarantine Tables |
| `pii_audits` | PII Compliance Log |
| `pipeline_runs` | Ingestion Audit Log |
| `priorities` | Signal Prioritisation |
| `provenance` | Source Provenance |
| `relationships` | Legacy Relationship Store |
| `signal_baselines`, `signal_entities`, `signal_flags` | Signal Enrichment Layer |
| `signals_archive`, `events_archive`, `artifacts_archive` | Archive Partitions |
| `wiki_articles`, `wiki_entries`, `wiki_links` | Knowledge Layer |
| FTS tables (`*_fts*`) | Full-Text Search Indexes |

---

## Section 2.2 — Volumetric Analysis

*`dbstat` unavailable — COUNT(*) fallback per SOP §2.2.*

### State Summary Table

| Table | Row Count | Operational Role |
|---|---:|---|
| `artifacts` | **564,953** | Evidence Store (dominant) |
| `artifacts_fts` | 564,953 | FTS mirror |
| `graph_nodes` | 463,160 | Pre-computed graph projection |
| `signal_entities` | 54,704 | NER entity index |
| `signals` | 51,155 | Signal Reservoir |
| `signal_baselines` | 31,641 | Gravity baseline snapshots |
| `graph_edges` | 26,049 | Pre-computed edge projection |
| `artifacts_fts_data` | 28,066 | FTS index data pages |
| `discovery_targets` | 168 | Observer seed registry |
| `actors` | 1,011 | Entity Registry |
| `actor_network_metrics` | 1,011 | Graph metrics cache (1:1 with actors) |
| `sentinel_alerts` | 863 | Alert layer |
| `pipeline_runs` | 417 | Ingestion audit log |
| `events` | 490 | Event layer |
| `events_fts` | 490 | FTS mirror |
| `case_signals` | 581 | Case→Signal junction |
| `signal_actors` | 3,643 | Signal→Actor junction |
| `signal_flags` | 241 | Signal annotation |
| `event_actors` | **214** *(was 340; 126 orphans purged Phase 71)* | Event→Actor junction |
| `entity_relationships` | 312 | Graph routing layer |
| `correlated_incidents` | 7,426 | Cross-signal correlation |
| `cases` | 22 | Case management |
| `case_actors` | 48 | Case→Actor junction |
| `case_artifacts` | 15 | Case→Artifact junction |
| `case_events` | 6 | Case→Event junction |
| `observer_promotion_log` | 77 | NER promotion audit |
| `wiki_articles` | 520 | Knowledge layer |
| `wiki_entries` | 290 | Knowledge entries |
| `actor_weights` | 13 | Gravity weights |
| `orphaned_event_actors` | **221** *(95 pre-existing + 126 quarantined Phase 71)* | Quarantine: event_actors |
| `orphaned_entity_relationships` | 66 | Quarantine: ER orphans |
| `actor_events` | 10 | Actor→Event log |
| `actor_signals` | 3 | Actor→Signal log |
| `actor_coalitions` | 1 | Graph cluster |
| `network_emergence` | 0 | Coalition matrix (empty) |
| `priorities`, `provenance`, `relationships` | 0 | Unused/legacy |
| `pii_audits`, `signals_archive`, `wiki_links` | 0 | Unpopulated |

**Dominant mass:** `artifacts` (564,953) + `graph_nodes` (463,160) + `signal_entities` (54,704) account for the bulk of storage.

---

## Section 2.3 — Relationship Density

| Junction Table | Total Links | Unique Actors | Density Ratio |
|---|---:|---:|---|
| `signal_actors` | 3,643 | 760 | 4.8 links/actor — well-connected |
| `event_actors` | **214** *(post-purge)* | 151 | 1.4 links/actor — clean |
| `entity_relationships` | 312 edges | 68 distinct subjects | 4.6 edges/subject — strong graph core |

**Graph Assessment:** Signal→Actor connectivity is healthy (4.8:1). The graph routing layer (`entity_relationships`) has a dense core of 68 active subjects. The event layer is proportionally thin relative to signal mass — expected given that not all signals escalate to events.

---

## Section 2.4 — Zero-Trust Integrity Check

| Check | Junction | FK Direction | Orphan Count | Result |
|---|---|---|---:|:---:|
| `signal_actors.signal_id` → `signals` | signal_actors | FK forward | 0 | ✅ PASS |
| `signal_actors.actor_id` → `actors` | signal_actors | FK forward | 0 | ✅ PASS |
| `event_actors.event_id` → `events` | event_actors | FK forward | ~~126~~ → **0** | ✅ PASS *(Phase 71)* |
| `event_actors.actor_id` → `actors` | event_actors | FK forward | ~~1~~ → **0** | ✅ PASS *(Phase 71)* |
| `entity_relationships.subject_actor_id` → `actors` | entity_relationships | FK forward | 0 | ✅ PASS |
| `entity_relationships.object_actor_id` → `actors` | entity_relationships | FK forward | 0 | ✅ PASS |
| `case_actors.case_id` → `cases` | case_actors | FK forward | 0 | ✅ PASS |
| `case_actors.actor_id` → `actors` | case_actors | FK forward | 0 | ✅ PASS |

**Total orphan count: ~~127~~ → 0** — Phase 71 surgical strike complete.

### Orphan Root Cause Analysis (Historical)

| Orphan Class | Original Count | Root Cause | Resolved |
|---|---:|---|---|
| `event_actors.event_id` → `events` | 126 | Large-integer event_ids (e.g. `1776276290`) from pre-migration era where event_id was generated as a Unix timestamp or synthetic integer. These event rows never existed in the `events` table. | Phase 71 — Strike 1 |
| `event_actors.actor_id` → `actors` | 1 | `actor_id=877` (noise company) deleted in Phase 67 before FK patch was active. Row also carried a large-int event_id; removed by Strike 1. | Phase 71 — Strike 1 |

**All orphan classes purged.** 126 rows quarantined to `orphaned_event_actors` with `detected_at` timestamp before deletion. The Phase 68 monkey-patch (`core/db/connection.py`) prevents all future orphan creation.

---

## Section 3 — Canonical Outputs

### Output 1 — State Summary Table (Condensed)

| Table | Row Count | Status |
|---|---:|:---:|
| actors | 1,011 | ✅ |
| signals | 51,155 | ✅ |
| artifacts | 564,953 | ✅ |
| events | 490 | ✅ |
| entity_relationships | 312 | ✅ |
| signal_actors | 3,643 | ✅ |
| event_actors | **214** | ✅ *(0 orphans — purged Phase 71)* |
| case_actors | 48 | ✅ |
| cases | 22 | ✅ |

### Output 2 — Edge List (Top 20)

```
16,260,INVESTIGATES
18,22,co_occurrence
22,313,INVESTIGATES
39,49,co_occurrence
39,92,co_occurrence
39,105,osint_match
39,193,co_occurrence
39,314,co_occurrence
39,552,co_occurrence
39,568,co_occurrence
39,644,co_occurrence
39,716,co_occurrence
39,737,co_occurrence
43,90,co_occurrence
43,101,co_occurrence
43,135,ACCUSED_OF
43,137,osint_match
43,171,co_occurrence
43,224,co_occurrence
43,231,co_occurrence
```

*Full edge list: 312 rows in `entity_relationships`. Export via: `SELECT subject_actor_id || ',' || object_actor_id || ',' || relation_type FROM entity_relationships LIMIT 500;`*

### Output 3 — Integrity Verdict

```
VERDICT: FIELD READY
```

> *Upgraded from AMBER → FIELD READY by Phase 71 surgical strike (2026-04-19).*

**Rationale:**

| Criterion | Phase 70 Status | Phase 71 Status |
|---|---|---|
| All checks pass with zero orphans | NO — event_actors had 127 | **YES — 0 orphans across all 8 checks** |
| Orphans isolated to a single table | YES — event_actors only | N/A — resolved |
| Root cause identified | YES | YES — confirmed pre-FK-era artifact |
| Quarantine backup taken before deletion | N/A | **YES — 126 rows in orphaned_event_actors** |
| All junction tables clean | NO | **YES — all 8 FK directions pass** |
| FK enforcement active (nervous system) | YES | YES |
| Schema complete | YES | YES |

**The substrate is FIELD READY.** All integrity checks pass. Foreign key enforcement is active process-wide. The event layer is clean. Signal intelligence, actor graph queries, case management, and the bridge hunt pipeline can all be trusted.

---

## Nervous System Status

*Foreign key enforcement verified across three independent connections.*

| Connection | `PRAGMA foreign_keys` | Status |
|:---:|:---:|:---:|
| 1 | **1** | ✅ ENFORCED |
| 2 | **1** | ✅ ENFORCED |
| 3 | **1** | ✅ ENFORCED |

**Mechanism:** Process-wide `sqlite3.connect` monkey-patch in `core/db/connection.py` (Phase 68). All connections — including ad-hoc scripts and test runners — inherit `PRAGMA foreign_keys = ON` before any caller code executes. URI `mode=rw` prevents silent database creation on bad paths.

---

## Phase 70 Recommended Actions — Execution Log

| Priority | Action | Status |
|---|---|:---:|
| P0 | `DELETE FROM event_actors WHERE event_id NOT IN (SELECT event_id FROM events)` — 126 rows | ✅ **Phase 71** |
| P0 | `DELETE FROM event_actors WHERE actor_id NOT IN (SELECT actor_id FROM actors)` — absorbed in Strike 1 | ✅ **Phase 71** |
| P1 | Quarantine orphan rows to `orphaned_event_actors` before deletion | ✅ **Phase 71** |
| P1 | Add SAFLII (`saflii.org`) as bridge hunt target for Case Alpha NG13 | ⬜ Phase 71+ |
| P2 | Populate `network_emergence` table via `scripts/network_emergence.py --metrics` | ⬜ Phase 71+ |
| P2 | Wire `process_artifact()` with NER triple extraction for `PROSECUTED_BY`/`ARRESTED_BY` edges | ⬜ Phase 71+ |
| P3 | Recompile SQLite with `ENABLE_DBSTAT_VTAB` | ⬜ Deferred |
