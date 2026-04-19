# FORGE — Stack-Aware DB Interrogation & Presentation SOP
**Operator Edition — Phase 62: Substrate Transition**

> **Classification:** Internal | **Version:** 1.0 | **Stack:** SQLite

---

## Purpose

This SOP defines the structured process for entering a SQLite environment, interrogating a database substrate, and producing a canonical state report. It answers a single question:

> *"Is this a truth layer — or just accumulated data?"*

---

## Section 0 — Environment Setup

You cannot run SQL directly in Windows Command Prompt. You must first enter the SQLite shell.

### 0.1 — Verify SQLite is available

```sql
sqlite3
```

| Status | Condition | Action |
|:---:|---|---|
| ✅ | You see: `sqlite>` | You are inside SQLite. Proceed to Step 0.2. |
| ❌ | `'sqlite3' is not recognized` | **STOP.** SQLite CLI is not installed. Installation required before continuing. |

### 0.2 — Open the target database

```sql
sqlite3 database.db
```

> **NOTE:** Replace `database.db` with your actual file name. The prompt will change to `sqlite>` confirming you are inside the environment.

---

## Section 1 — Stack Detection

*The Handshake — confirm what the substrate is capable of before interrogating it.*

### 1.1 — Run the detection block

```sql
SELECT sqlite_version();
PRAGMA compile_options;
PRAGMA foreign_keys;
SELECT count(*) FROM sqlite_master WHERE name = 'dbstat';
```

#### How to Read the Output

| Output | Interpretation |
|---|---|
| `sqlite_version()` | Confirms SQLite is responding. Version determines available features. |
| `compile_options` | Look for `ENABLE_DBSTAT_VTAB`. If present, full size analysis is available. If missing, use estimation fallbacks. |
| `foreign_keys = 1` | Referential integrity is enforced at the database level. **Strong substrate.** |
| `foreign_keys = 0` | Integrity is handled in application code only. Weaker substrate — treat orphan checks as critical. |
| `dbstat = 1` | Advanced size inspection available. Use primary volumetric queries. |
| `dbstat = 0` | Use `COUNT(*)` estimation fallbacks for volumetric analysis. |

---

## Section 2 — Interrogation

*Four sequential phases. Run in order. Record all output.*

### Phase 2.1 — Structural Mapping

Determine what tables exist and how they are defined.

**Primary — Full schema dump**

```sql
.schema
```

| Status | Condition | Action |
|:---:|---|---|
| ✅ | Output lists table definitions | Record schema. Proceed to Phase 2.2. |
| ❌ | `.schema` fails or returns nothing | Run fallback query below. |

**Fallback A — Schema from `sqlite_master`**

```sql
SELECT type, name, sql
FROM sqlite_master
WHERE sql IS NOT NULL;
```

**Fallback B — Minimum: table names only**

```sql
SELECT name
FROM sqlite_master
WHERE type = 'table';
```

> **NOTE — Failure signals:** missing expected tables, inconsistent naming, heavy reliance on generic JSON columns.

---

### Phase 2.2 — Volumetric Analysis

Determine which tables carry operational weight.

**Primary — Size by table (requires `dbstat = 1`)**

```sql
SELECT name, sum(pgsize) / 1024.0 AS size_kb
FROM dbstat
GROUP BY name
ORDER BY size_kb DESC;
```

**Fallback — Row count estimation (when `dbstat = 0`)**

```sql
SELECT 'actors'    AS table_name, count(*) FROM actors
UNION ALL
SELECT 'events',                  count(*) FROM events
UNION ALL
SELECT 'artifacts',               count(*) FROM artifacts
UNION ALL
SELECT 'signals',                 count(*) FROM signals
UNION ALL
SELECT 'entity_relationships',    count(*) FROM entity_relationships
UNION ALL
SELECT 'signal_actors',           count(*) FROM signal_actors
UNION ALL
SELECT 'event_actors',            count(*) FROM event_actors
UNION ALL
SELECT 'case_actors',             count(*) FROM case_actors;
```

> **NOTE:** Extend the `UNION ALL` block for every table in your schema.
> What you are looking for: largest tables, unexpected heavy tables, imbalance
> (e.g. `events` huge while `relationships` is tiny).

---

### Phase 2.3 — Relationship Density

Measure connectivity between entities.

```sql
-- signal_actors density
SELECT count(*) AS total_links,
       count(DISTINCT actor_id) AS unique_actors
FROM signal_actors;

-- event_actors density
SELECT count(*) AS total_links,
       count(DISTINCT actor_id) AS unique_actors
FROM event_actors;

-- entity_relationships density
SELECT count(*) AS edges,
       count(DISTINCT subject_actor_id) AS distinct_subjects
FROM entity_relationships;
```

> **NOTE — Interpretation:**
> High links + low unique nodes = dense, well-connected graph.
> Low links = sparse substrate, likely under-populated.
> Replace junction table names with your actual schema names.

---

### Phase 2.4 — Integrity Check (Zero-Trust)

Detect orphaned records — the primary indicator of data decay.
Run on **every** junction table.

**Primary — Orphan detection**

```sql
-- signal_actors
SELECT count(*) FROM signal_actors
WHERE signal_id NOT IN (SELECT signal_id FROM signals);

SELECT count(*) FROM signal_actors
WHERE actor_id NOT IN (SELECT actor_id FROM actors);

-- event_actors
SELECT count(*) FROM event_actors
WHERE event_id NOT IN (SELECT event_id FROM events);

SELECT count(*) FROM event_actors
WHERE actor_id NOT IN (SELECT actor_id FROM actors);

-- entity_relationships
SELECT count(*) FROM entity_relationships
WHERE subject_actor_id NOT IN (SELECT actor_id FROM actors);

SELECT count(*) FROM entity_relationships
WHERE object_actor_id NOT IN (SELECT actor_id FROM actors);

-- case_actors
SELECT count(*) FROM case_actors
WHERE case_id NOT IN (SELECT case_id FROM cases);

SELECT count(*) FROM case_actors
WHERE actor_id NOT IN (SELECT actor_id FROM actors);
```

| Status | Condition | Action |
|:---:|---|---|
| ✅ | Result = `0` | No orphans. Substrate is intact. |
| ⚠️ | Result `> 0` | Broken references detected. Flag as **CORRUPTED SUBSTRATE**. |

**Slow-query fallback — Bounded orphan check**

```sql
SELECT count(*) FROM event_actors
WHERE actor_id NOT IN (
    SELECT actor_id FROM actors LIMIT 1000
);
```

> **NOTE:** Use this variant if the primary query times out on large tables.
> The `LIMIT 1000` bounds the subquery scan.

---

## Section 3 — Presentation

*Produce three outputs after completing Section 2. These are your deliverables.*

### Output 1 — Canonical State Summary

A table mapping each significant table to its row count and operational role.

| Table | Row Count | Operational Role |
|---|:---:|---|
| `actors` | X | Entity Registry |
| `signals` | X | Signal Reservoir |
| `artifacts` | X | Evidence Store |
| `events` | X | Event Layer |
| `entity_relationships` | X | Routing Layer |
| `signal_actors` | X | Signal→Actor Junction |
| `event_actors` | X | Event→Actor Junction |
| `case_actors` | X | Case→Actor Junction |

### Output 2 — Graph Projection (Edge List)

Extract a machine-readable edge list of entity relationships.

```sql
SELECT subject_actor_id || ',' || object_actor_id || ',' || relation_type
FROM entity_relationships
LIMIT 500;
```

### Output 3 — Integrity Verdict

Apply the following decision rules to produce a single substrate classification.

| Verdict | Trigger Condition |
|---|---|
| **CORRUPTED SUBSTRATE** | Orphan count `> 0` in any integrity check. Data references are broken. Do not trust query results. |
| **OPAQUE SUBSTRATE** | Database is large but `dbstat` is unavailable. Size analysis is estimation only. Proceed with caution. |
| **FIELD READY** | All integrity checks pass, schema is complete, `dbstat` available. Substrate is trustworthy. |
| **AMBER** | Orphans isolated to a single table with identified root cause; all other checks pass. Monitor and remediate. |

---

## Section 4 — Operator Flow

*The sequence you actually execute. End-to-end, in order.*

| Step | Action | Detail |
|:---:|---|---|
| 1 | **Open SQLite** | Run `sqlite3` then `sqlite3 database.db`. Confirm `sqlite>` prompt. |
| 2 | **Run detection block** | Execute all four statements in Section 1. Record version, compile_options, foreign_keys, dbstat. |
| 3 | **Run `.schema`** | Dump full schema or use fallbacks. Identify all tables and junction tables. |
| 4 | **Run size analysis** | Use `dbstat` query if available. Otherwise use `COUNT(*)` union block. |
| 5 | **Check relationship density** | Run density queries on all junction tables. Record `total_links` and `unique_actors`. |
| 6 | **Run integrity check** | Run orphan detection on every junction table. Record any non-zero results. |
| 7 | **Build summary** | Produce Output 1 (state table), Output 2 (edge list), Output 3 (verdict). |

---

> *"This is not about inspecting a database. This is about determining whether this is a truth layer — or just accumulated data."*

---

## Appendix — Automation Notes

This SOP is optimized for automated audit execution. Scripts should:

- Assert `PRAGMA foreign_keys = 1` before any check; abort if `= 0`
- Run all orphan checks in a single transaction with `BEGIN; ... ROLLBACK;` to prevent accidental mutations
- Emit structured JSON summary with keys: `version`, `foreign_keys`, `dbstat`, `table_counts`, `orphan_counts`, `verdict`
- Exit non-zero if any orphan count `> 0` (CI gate compatibility)
- Store report output to `docs/SUBSTRATE_AUDIT_REPORT_<YYYY-MM>.md`
