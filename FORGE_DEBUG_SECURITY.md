# FORGE — SECURITY & DEBUG BRIEF
### Classification: INTERNAL — SYSTEM INTEGRITY
### Issued by: FORGE Autonomous Pipeline
### Revision: 1.0 — Phase 32+

---

> *"The Machine sees everything. The question is whether you've taught it what to look for."*

---

## INCIDENT REPORT: THE BLIND CONCLAVE
**Status:** Resolved (Partial) — Monitoring Required  
**Severity:** CRITICAL — System-wide synthesis failure  
**Affected Runs:** All ingestion cycles prior to Phase 32+ patch  
**Signals Affected:** ~18,835 signals processed with 0 actors, 0 events, 0 cases produced

---

### EXECUTIVE SUMMARY

FORGE's ingestion pipeline was operating in a state of **phantom productivity** — 18,000+ signals were being collected, stored, and cycled through the Conclave engine, but the synthesis layer was completely blind. Every signal was processed. Nothing was seen.

The system was not broken. It was **miscalibrated at three independent layers simultaneously**, each masking the others.

---

## ROOT CAUSE ANALYSIS

### CAUSE 1 — The Gravity Floor (Silent, Pre-existing)
**File:** `forage/engines/gravity_engine.py`  
**Nature:** Structural design gap  

The gravity formula requires five inputs: `severity`, `actor_importance`, `frequency`, `sentiment`, `source_credibility`. Of these, `actor_importance` (weight: 25%) and `frequency` (weight: 15%) were **never populated** by the signal interpreter. They were hardcoded to `0.0` on every signal.

Additionally, the `momentum` multiplier had a floor of `0.6`, meaning every gravity score was cut by 40% before output regardless of signal quality.

**Mathematical result:**
```
Max achievable gravity = (0.35×1.0 + 0 + 0 + 0.15×0.5 + 0.10×0.5) × 0.6
                       = 0.475 × 0.6
                       = 0.285
```

The `MONITOR` threshold was `0.45`. **No signal in any dataset could ever exceed 0.285.**

**Fix applied:**
- `signal_interpreter.py` now computes `actor_importance` from actor count, `frequency` from source tier and `is_priority` flag, and `source_credibility` from a known-source map
- `momentum` floor raised from `0.6` to `0.8`

---

### CAUSE 2 — The Stub Assassin (Active, Destructive)
**File:** `core/pipeline/ingest.py`  
**Nature:** Legacy code executing after real output  

`apply_conclave_stub()` was called as the **last step** of every signal ingestion. Its job was to be a safe placeholder. Instead it was actively overwriting the real Conclave's output with:
```python
gravity=0.1, recommendation="IGNORE", confidence=0.1
```

The real Conclave ran. Produced real output. The stub silently erased it.

**Cascading effect:**
- `materialize_entities()` requires `confidence >= 0.4` — stub set it to `0.1` → no actors ever created
- `handle_escalation()` requires `gravity >= 0.45` — stub set it to `0.1` → no events or cases ever created

**Fix applied:** Stub call commented out. Function definition retained for reference.

---

### CAUSE 3 — The Threshold Mismatch (Configuration)
**File:** `forage/engines/escalation_engine.py`  
**Nature:** Thresholds calibrated for a richer NLP pipeline than exists

```python
ESCALATE_THRESHOLD = 0.75   # original
MONITOR_THRESHOLD  = 0.45   # original
```

These values assume a full spaCy/transformer NLP stack producing rich semantic gravity scores. FORGE currently uses keyword-based scoring. Even with Causes 1 and 2 fixed, the maximum achievable gravity from keyword scoring is approximately `0.65`.

**Fix applied:**
```python
ESCALATE_THRESHOLD = 0.55
MONITOR_THRESHOLD  = 0.35
```

---

### CAUSE 4 — Wrong Primary Key in Escalation Updates (Silent, Pre-existing)
**File:** `forage/engines/escalation_engine.py`  
**Nature:** SQL targeting wrong column  

Both `create_event()` and `create_case()` contained:
```sql
UPDATE signals SET conclave_meta = ... WHERE id = ?
```

The `signals` table primary key is `signal_id` (TEXT), not `id`. Every `json_patch` write silently matched zero rows. `event_id` and `case_id` were never written back to `conclave_meta`.

**Fix applied:** Both UPDATE statements changed to `WHERE signal_id = ?`

---

### CAUSE 5 — Database Lock Contention (Operational)
**File:** `mega_ingest.py`  
**Nature:** Two concurrent SQLite connections writing simultaneously  

`run_full_ingest()` held a connection open for the full 18k-signal loop while `ingest_signal()` opened its own connection via `get_connection()` on every iteration. SQLite's WAL mode tolerates multiple readers but only one writer — the result was `OperationalError: database is locked` on every signal.

**Fix applied:** All rows fetched and converted to plain dicts upfront, connection closed before the loop begins. `ingest_signal()` then has exclusive write access per iteration.

---

## SECURITY CONSIDERATIONS

### Undesired System Behaviour vs. Bug
The stub issue (Cause 2) sits in a grey area. It was intentionally written as a "preserve previous behavior" safeguard. It became destructive when the real Conclave was wired in. **This is classified as undesired system behaviour, not a bug** — it did exactly what it was coded to do, in a context it was never designed for.

The gravity floor (Cause 1) is a pure design gap. The formula expected fields that no upstream component was providing. No error was thrown because `0.0` is a valid float. **This is a silent contract violation between modules.**

### Future Injection Points to Watch

| Location | Risk | Mitigation |
|---|---|---|
| `apply_conclave_stub()` | Any re-enabling will zero-out Conclave output | Keep commented, add `# DO NOT RE-ENABLE` comment |
| `entity_engine.py` confidence gate | If raised above actual pipeline output, actors silently stop | Gate is now `0.2` — document this is pipeline-relative |
| `escalation_engine.py` thresholds | If raised above gravity ceiling, no events ever fire | Thresholds must be recalibrated whenever NLP pipeline changes |
| `signal_interpreter.py` actor patterns | Only matches hardcoded SA entities — new actors invisible | Extend `ACTOR_PATTERNS` as new entities are identified |
| `mega_ingest.py` connection lifecycle | Re-introducing conn passing will cause lock contention | `ingest_signal()` must always manage its own connection |

### Schema Validation Gap
`connection.py` validates required tables on every connection. When `fix_schema.py` adds new tables (`signal_actors`, `event_actors`), those tables must be added to `REQUIRED_TABLES` simultaneously or `get_connection()` will reject the database. **Any future `fix_schema.py` addition must have a matching `REQUIRED_TABLES` entry.**

---

## DEBUG CHECKLIST — ZERO ACTORS / ZERO EVENTS

If a future run produces all-zero Conclave summaries, check in this order:

```
1. Is apply_conclave_stub() uncommented?           → ingest.py line ~178
2. What is the max gravity score in the database?  → SELECT MAX(gravity_score) FROM signals
3. Is that above MONITOR_THRESHOLD?                → escalation_engine.py
4. Is signal_interpreter returning actor_importance > 0?  → add print() to interpret()
5. Is entity confidence above the gate?            → entity_engine.py line ~38
6. Are there database lock errors in the output?   → look for "database is locked"
7. Is the signal_actors table populated?           → SELECT COUNT(*) FROM signal_actors
```

---

## RECOMMENDED FUTURE DOCUMENTS

1. **`FORGE_PIPELINE_CONTRACTS.md`** — Formal input/output contracts for every engine, specifying what fields each module expects and guarantees. Prevents silent zero-value propagation.

2. **`FORGE_THRESHOLD_REGISTRY.md`** — Central registry of all numeric thresholds across the system (gravity, confidence, decay λ, anomaly baselines) with their calibration rationale and the NLP pipeline tier they assume.

3. **`FORGE_SCHEMA_CHANGELOG.md`** — Every `ALTER TABLE` and `CREATE TABLE` addition across all phases, with the phase number, date, and which `REQUIRED_TABLES` entry it corresponds to.

4. **`FORGE_COLLECTOR_HEALTH.md`** — Per-collector expected output ranges, known failure modes, and rate limit behaviour for GDELT, FIRMS, USGS, GDACS, and civic intel sources.

---

*Document generated from live session analysis — FORGE Phase 32+*  
*All line numbers reference the codebase as of 2026-03-19*