# FORGE — Analyst Agent Execution Chain
### Single-Run Execution Order
### Classification: INTERNAL — OPERATIONAL REFERENCE
### Applies to: Any Claude instance executing ANALYST_AGENT_PROMPT.md

---

## Overview

A single run is one continuous invocation: from cold-read to artifact delivery to memory
write. Every step is ordered. No step may be skipped. Branch points are explicit.
Tool calls are specified at each stage.

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 0 — COLD ORIENTATION                                         ║
║  Trigger: session open OR "execute" directive from operator         ║
╚══════════════════════════════════════════════════════════════════════╝

  0.1  Read  ANALYST_AGENT_PROMPT.md
             → Establishes: role identity, artifact standards, constraints,
               domain expertise, operational posture
             → Required before any DB query or artifact production

  0.2  Read  memory/MEMORY.md
             → Index of all persistent memory files
             → Determines which project files to pull next

  0.3  Read  memory/project_forge_current_state.md
             → Current stable version, open tech debt, schema state,
               last analyst session summary

  0.4  Read  memory/project_forge_analyst_threads.md
             → Open cases (IDs + signal counts), open threads,
               actors created, gravity deficiency map

  0.5  Read  memory/feedback_analyst_agent_mode.md
             → Operational constraints: encoding, FK rules, timeout=60,
               sys.stdout.reconfigure, commit-before-close

  0.6  Read  CLAUDE.md
             → Architectural decisions (locked), valid actor types,
               signal streams, decay model, pipeline data flow

  OUTPUT:    Internal context loaded. Agent is oriented.
  GATE:      If any read fails → flag missing memory, proceed with
             what is available. Never block on a missing file.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 1 — PIPELINE HEALTH CHECK                                    ║
║  Trigger: automatic, runs before Operational Status Check           ║
╚══════════════════════════════════════════════════════════════════════╝

  1.1  Bash  python -c "import sqlite3 ..."
             Query: SELECT COUNT(*) FROM signals WHERE gravity_score IS NULL
             → GATE: if n > 0, pipeline has a backlog. Record count.
             → If n > 100: pipeline stall is active. Flag for Stage 2.

  1.2  Bash  python -c "..."
             Query: SELECT MAX(timestamp) FROM signals WHERE gravity_score IS NOT NULL
             → Compare against current date
             → If gap > 72h: corpus is stale. Flag in every artifact header.

  1.3  Bash  python -c "..."
             Query: SELECT COUNT(*) FROM sentinel_alerts WHERE status = 'new'
             → Unreviewed alerts. Count recorded for Stage 2.

  OUTPUT:    pipeline_status = { backlog_n, stale_days, unreviewed_alerts }
  BRANCH:    if backlog_n > 0 → run Stage 1A before Stage 2
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 1A — PIPELINE UNBLOCK [conditional]                          ║
║  Trigger: backlog_n > 0 from Stage 1                                ║
╚══════════════════════════════════════════════════════════════════════╝

  1A.1 Bash  python -c "
             import sys; sys.path.insert(0, '.')
             from core.pipeline.ingest import ingest_signal
             import sqlite3
             conn = sqlite3.connect('database.db', timeout=60)
             ...
             "
             → Fetch all signals WHERE gravity_score IS NULL
             → Call ingest_signal(signal) for each
             → Track ok/err counts
             → Print: 'Done: {ok} ok, {err} errors'

  1A.2 Bash  python -c "..."
             → Spot-check: SELECT title, gravity_score FROM signals
               WHERE source IN ('amabhungane','dailymaverick_corruption')
               AND gravity_score < 0.25 ORDER BY timestamp DESC LIMIT 10
             → Apply manual gravity corrections for investigative
               journalism signals scoring < 0.25 with high-value titles
               (procurement fraud, political assassination, SOE irregularity)
             → Rule: UPDATE signals SET gravity_score = [0.38–0.45]
               WHERE signal_id = ?

  OUTPUT:    Backlog cleared. Gravity corrections applied.
  GATE:      If ingest_signal() errors > 10%: log errors, continue.
             Never abort Stage 2 due to pipeline errors.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 2 — OPERATIONAL STATUS CHECK                                 ║
║  Trigger: automatic after Stage 1 (or 1A)                           ║
╚══════════════════════════════════════════════════════════════════════╝

  2.1  Bash  Query: active cases with signal counts
             SELECT c.case_id, c.name, c.status, COUNT(cs.signal_id) as n
             FROM cases c LEFT JOIN case_signals cs ON c.case_id = cs.case_id
             WHERE c.status != 'closed'
             GROUP BY c.case_id ORDER BY n DESC

  2.2  Bash  Query: top 3 highest-gravity signals (scored, recent)
             SELECT signal_id, title, source, gravity_score, stream, timestamp
             FROM signals WHERE gravity_score IS NOT NULL
             ORDER BY gravity_score DESC LIMIT 3

  2.3  Bash  Query: actors at confidence >= 0.2, most recently created
             SELECT actor_id, name, type, confidence_score, created_at
             FROM actors WHERE confidence_score >= 0.2
             ORDER BY created_at DESC LIMIT 15

  2.4  Bash  Query: MONITOR/ESCALATE threshold crossings
             SELECT alert_type, confidence_score, signal_count, summary
             FROM sentinel_alerts WHERE status = 'new'
             ORDER BY confidence_score DESC LIMIT 10

  2.5  Bash  Query: stream stats
             SELECT stream, COUNT(*) as n,
                    ROUND(AVG(gravity_score),3) as avg_g,
                    ROUND(MAX(gravity_score),3) as max_g
             FROM signals WHERE gravity_score IS NOT NULL
             GROUP BY stream ORDER BY max_g DESC

  2.6  ANALYZE  (no tool call — pure synthesis)
             → Cross-reference cases against signal corpus
             → Identify which cases have sufficient signal mass
               for artifact production (n >= 3, gravity mean >= 0.3)
             → Identify which cases are shells (n = 0)
             → Identify highest-priority uncased thread from memory

  2.7  OUTPUT  Produce Operational Status Check:
             → Active cases (name, signal count, dominant stream)
             → Three highest-gravity signals
             → New actors above 0.2 gate
             → MONITOR/ESCALATE threshold crossings
             → Identified next artifact + priority rationale

  GATE:      If database is empty → produce [SIMULATED] artifact.
             If corpus is stale > 72h → flag in every subsequent output.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 3 — DIRECTIVE INTAKE                                         ║
║  Trigger: operator message OR self-selected from Stage 2 priority   ║
╚══════════════════════════════════════════════════════════════════════╝

  3.1  AUDIT
       → What does the current corpus actually support?
       → Does the directive require data that isn't in the DB?
       → Are there actors missing that the artifact needs?
       → Is the case corpus dense enough (n, gravity distribution)?

  3.2  WEIGH
       → Is there a higher-value artifact that serves the same
         intelligence need at lower analyst cost?
       → Is there a cheaper collection action that would
         significantly improve the artifact before producing it?

  3.3  FLAG DEFICITS
       → Name any gap: missing actor, thin corpus, stale signals,
         single-source assertions, coordinate artifacts
       → State confidence tier: HIGH / MEDIUM / LOW / UNVERIFIED
       → Recommend collection action if gap is closeable

  3.4  PROCEED
       → Route to the appropriate execution branch below

  BRANCHES:
    A. Produce intelligence artifact  → Stage 4
    B. Execute DB write operation     → Stage 5
    C. Execute collection action      → Stage 6
    D. Produce dossier / SOCINT report → Stage 4 (alternate template)
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 4 — ARTIFACT PRODUCTION                                      ║
║  Trigger: Stage 3 routes to artifact production                     ║
╚══════════════════════════════════════════════════════════════════════╝

  4.1  Bash  Pull full artifact corpus:
             → All signals pinned to case (case_signals JOIN signals)
             → Actor-signal links (signal_actors JOIN actors)
             → Entity relationships involving case actors
             → Sentinel alerts for case actors or signals
             → Gravity distribution: min, max, mean, n

  4.2  Bash  Pull signal content for top 8-10 signals:
             SELECT signal_id, title, content, source, gravity_score,
                    timestamp FROM signals WHERE signal_id IN (...)
             → content[:600] for each

  4.3  ANALYZE  (no tool call)
             → Temporal pattern: is signal density increasing or decaying?
             → Source triangulation: how many independent sources confirm
               each key assertion?
             → Actor centrality: which actor appears in the most signals?
             → Coordinate artifact check: are lat/lng values default
               centroids (-25.75, 28.23) or real geocodes?
             → Escalation threshold: is mean gravity >= 0.35 (MONITOR)?
               >= 0.55 (ESCALATE)? Is corpus n sufficient to trust the mean?
             → Knowledge gap rank: what would most change the CoE
               assessment if it were known?

  4.4  OUTPUT  Produce artifact to standard template:
             → FORGE INTELLIGENCE BRIEF / ACTOR DOSSIER /
               SOCINT RESONANCE REPORT / EVENT RECONSTRUCTION
             → Include: classification banner, situation summary,
               signal corpus stats, key actors table, escalation
               assessment, knowledge gaps, ANALYST RECOMMENDATION
             → Every claim traceable to a signal in the corpus
             → Confidence qualifiers on every assertion without
               multi-source confirmation

  GATE:      No signal data → state corpus is empty, recommend
             collection action. Never produce a brief on zero signals.
             Single source → qualify every assertion as UNVERIFIED.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 5 — DB WRITE OPERATIONS                                      ║
║  Trigger: Stage 3 routes to actor creation, relationship wiring,    ║
║           case management, gravity correction                       ║
╚══════════════════════════════════════════════════════════════════════╝

  5.1  PRE-WRITE CHECKS (always, before any write)
       → Verify target table schema: PRAGMA table_info(<table>)
       → Check CHECK constraints: extraction_method IN ('manual','spacy','llm')
       → Verify FKs exist before INSERT into case_signals or case_actors
         SELECT 1 FROM cases WHERE case_id=?
         SELECT 1 FROM signals WHERE signal_id=?
       → Use sys.stdout.reconfigure(encoding='utf-8') for all python -c calls

  5.2  ACTOR WRITE
       → INSERT INTO actors (name, type, description, source_type,
         created_at, confidence_score, automated) VALUES (...)
       → type must be one of the valid actor types in CLAUDE.md
       → automated = 0 for analyst-created, 1 for pipeline-created
       → confidence_score: 0.35 (single source, arrest),
         0.5 (public record), 0.65 (confirmed arrest, 2+ sources),
         0.8 (confirmed public figure, deceased/verified)

  5.3  RELATIONSHIP WRITE
       → INSERT INTO entity_relationships
         (subject_actor_id, object_actor_id, relation_type,
          description, confidence, extraction_method, created_at)
       → extraction_method = 'manual' for analyst-created
       → Verify both actor_ids exist before insert

  5.4  CASE WRITE
       → INSERT INTO cases (name, description, hypothesis,
         case_type, status, source_type, created_at)
       → Use 7-column INSERT (no auto_generated column unless confirmed)

  5.5  SIGNAL PIN
       → INSERT OR IGNORE INTO case_signals (case_id, signal_id)
       → Wrap in try/catch; FK failure = signal_id does not exist
       → Verify signal_id via SELECT 1 FROM signals WHERE signal_id=?
         before attempting pin

  5.6  GRAVITY CORRECTION
       → UPDATE signals SET gravity_score=? WHERE signal_id=?
       → Record correction reason in session output
       → Rule: apply when title indicates high-value content and
         scored gravity < 0.25 due to stripped RSS content

  5.7  COMMIT
       → conn.commit() before conn.close()
       → Always use try/finally: conn.close()
       → Print confirmation: rows affected per operation

  GATE:      If FK constraint fails → do not retry blindly.
             Diagnose (signal/case missing?), fix, then re-execute.
             If CHECK constraint fails → read table schema first.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 6 — COLLECTION ACTION                                        ║
║  Trigger: Stage 3 identifies corpus gap closeable by collection     ║
╚══════════════════════════════════════════════════════════════════════╝

  6.1  IDENTIFY TARGET
       → Which collector addresses the gap?
         SAFLII/dork → court records, case numbers
         amabhungane/RSS → investigative journalism
         hawks_media → enforcement actions
         CIPC/dork → company registration

  6.2  Bash  Run individual collector:
             python forage/collectors/<name>.py
             OR
             python tools/mega_ingest.py

  6.3  Bash  Verify collection result:
             SELECT COUNT(*) FROM signals WHERE source=? AND
             timestamp >= datetime('now', '-1 hour')

  6.4  ROUTE BACK → Stage 1A (unblock pipeline if new unscored signals)

  GATE:      If collector fails (network, 403, schema change) →
             document gap, recommend manual dork search as fallback,
             continue with artifact production on current corpus.
             Never block artifact production waiting for collection.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 7 — MEMORY WRITE                                             ║
║  Trigger: session produces significant new state                    ║
║  Condition: new cases opened, new actors created, new threads       ║
║             identified, architectural findings made                 ║
╚══════════════════════════════════════════════════════════════════════╝

  7.1  ASSESS  What changed this session that a cold-start agent
               would need to know?
               → New cases (ID, name, signal count)
               → New actors (name, type, confidence, relationships)
               → New intelligence threads (no case yet)
               → Gravity corrections applied
               → Architectural findings (new deficiencies, resolved debt)
               → Pipeline state changes

  7.2  Write  memory/project_forge_analyst_threads.md
              → Update active cases table
              → Update open threads
              → Update actor table
              → Record gravity corrections made

  7.3  Edit   memory/project_forge_current_state.md
              → Append session summary under "Analyst session state"
              → Record new tech debt items discovered
              → Record resolved items

  7.4  Edit   memory/MEMORY.md
              → Update index entries if new files created
              → Update descriptions if file content changed significantly

  GATE:      Only write if state actually changed.
             Do not create duplicate memory entries.
             One memory write per session, not per artifact.
```

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  STAGE 8 — LOOP / STANDBY                                           ║
╚══════════════════════════════════════════════════════════════════════╝

  8.1  OUTPUT  "Standing by for operator directive."
               OR surface the next recommended action from Stage 2.7

  8.2  AWAIT  Operator message

  8.3  RETURN TO STAGE 3  (Directive Intake)
       → Do NOT re-run Stage 0 or Stage 2 unless operator
         explicitly starts a new session
       → Context from prior stages is retained in-conversation
```

---

## Execution Map (condensed)

```
COLD START
  │
  ▼
[0] ORIENT ──────── Read: prompt + 4 memory files + CLAUDE.md
  │
  ▼
[1] HEALTH CHECK ── Query: unscored signals, corpus staleness, alerts
  │
  ├─ backlog > 0 ──► [1A] UNBLOCK ── ingest_signal() batch + gravity corrections
  │
  ▼
[2] STATUS CHECK ── Query: cases, top signals, actors, escalations, streams
  │                 Synthesize → produce Operational Status Check
  ▼
[3] DIRECTIVE ────── Audit → Weigh → Flag → Proceed
  │
  ├──► [4] ARTIFACT ── Pull corpus → Analyze → Produce to standard template
  │
  ├──► [5] DB WRITE ── Pre-check → Write → Commit → Confirm
  │
  └──► [6] COLLECT ── Run collector → Verify → Route back to [1A]
  │
  ▼
[7] MEMORY WRITE ─── If state changed: update analyst_threads + current_state
  │
  ▼
[8] STANDBY ──────── Await next directive → return to [3]
```

---

## Hard Constraints (apply at every stage)

```
NEVER                                    │ ALWAYS
─────────────────────────────────────────┼──────────────────────────────────────
Fabricate signal data                    │ timeout=60 on every sqlite3.connect()
Assert without signal reference          │ sys.stdout.reconfigure(encoding='utf-8')
Mark actor confirmed < 0.7 confidence   │ try/finally: conn.close()
Score single-source as HIGH CoE          │ Verify FKs before case_signals INSERT
Trust gravity on stripped RSS content    │ Qualify SOCINT resonance as indicative
Exceed OSINT mandate                     │ State n before interpreting any mean
Block artifact production on stall       │ Write memory only if state changed
```

---

## Failure Recovery

```
FAILURE                          │ RECOVERY
─────────────────────────────────┼──────────────────────────────────────────
DB is empty                      │ Produce [SIMULATED] artifact, recommend
                                 │ python tools/mega_ingest.py
─────────────────────────────────┼──────────────────────────────────────────
Pipeline stall (stale corpus)    │ Run Stage 1A. Flag stale dates in artifact.
                                 │ Proceed with available corpus.
─────────────────────────────────┼──────────────────────────────────────────
FK constraint on case_signals    │ Verify signal_id exists. Do not retry blind.
                                 │ Fix missing FK, then re-execute.
─────────────────────────────────┼──────────────────────────────────────────
CHECK constraint on relation     │ Read PRAGMA table_info. Valid values:
                                 │ 'manual', 'spacy', 'llm'
─────────────────────────────────┼──────────────────────────────────────────
UnicodeEncodeError               │ sys.stdout.reconfigure(encoding='utf-8')
                                 │ at top of every python -c block
─────────────────────────────────┼──────────────────────────────────────────
Gravity engine underscores       │ Manual correction:
investigative journalism         │ UPDATE signals SET gravity_score=?
                                 │ WHERE signal_id=?
                                 │ Apply when title indicates high value
                                 │ and score < 0.25
─────────────────────────────────┼──────────────────────────────────────────
Flask app inaccessible           │ All operations proceed via direct DB
                                 │ access and python -c calls.
                                 │ Artifacts produced as conversation output.
                                 │ This is the established operational mode.
```
