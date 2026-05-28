# FORGE — Roadmap

## CT-1 — Contextual Tunneling  [COMPLETE]

> Gravity-based feed and signal filtering anchored to an active case context.
> All changes are additive — no DB schema changes, no existing routes removed.

All steps verified live in the codebase as of 2026-05-09:

- [x] `core/gravity.py` — `build_context()`, `score_item()`, `blend_score()` implemented
- [x] `/api/feed` — CT-1 gravity scoring wired (case_id + gravity params)
- [x] `/api/cases/<id>/anchors` — returns actor/location/keyword context for CT banner
- [x] `/api/cases/<id>/fetch-suggestions` — top 20 unlinked signals ranked by gravity
- [x] `/api/surface/signals/context` — signal monitor with gravity_score column
- [x] `feed.html` — CT banner, gravity slider, exitContextTunnel(), JS state machine
- [x] `case_detail.html` — "Context Feed" and "Context Signals" buttons, setFocusCase()
- [x] `base.html` — focus-case pill in topbar, setFocusCase/getFocusCase/clearFocusCase
- [x] `static/css/main.css` — ct-banner, ct-gravity-slider, topbar__focus-pill styles

---

## Current Phase: Execution Injection 02

### Objective A — CT-1  [COMPLETE — see above]

### Objective B — Schema Authority

- [x] `migrations/schema.sql` — canonical schema generated from live DB (2026-05-09)
- [x] `migrations/verify_schema.py` — schema verification utility (37 tables, 128 required columns)

Run verification at any time:
```
python migrations/verify_schema.py
```

### Objective C — FLUX Wave Integration

- [x] `run_flux_wave()` added to `tools/sovereign_pipeline.py` as Phase 5B
- [x] Corpus preflight guard prevents O(n²) explosion on sparse corpus
- [x] `--no-flux` flag added to CLI (mirrors `--no-dork` pattern)
- [x] FLUX stats included in final pipeline summary

**Phase 5B execution order (after Phase 6 Bridge Pass B):**
```
5B.1  Corpus preflight  — checks actors_ready >= 2, pairs <= 50,000
5B.2  corpus_builder    — bridges socint_signals → actors.socint_profile
5B.3  resonance         — O(n²) pairwise stylometric scoring
5B.4  discovery         — Jaccard + velocity → flux_latent_seeds
```

**Graceful skip conditions:**
- No `socint_signals` rows (x_pulse has never run)
- Fewer than 2 actors with corpus-ready profiles after corpus_builder
- Pair count exceeds 50,000 safety cap

---

## Next Priorities

### Rank 1 — Actor Naming Quality
`signal_interpreter.py _extract_actors()` inserts type-key strings (`"location"`) as
actor names instead of matched text. Fix: change `actors.add(name)` to extract actual
matched substring from the regex group.

### Rank 2 — Confidence Gate Calibration
`entity_engine.materialize_entities()` gates at `confidence >= 0.4` but
`feedback_engine` defaults produce 0.1–0.2. Gate should be reviewed downward to 0.25
or the feedback engine weights normalized upward.

### Rank 3 — P3.2-05 OCR Run
6,458 scanned PDFs with `< 100 chars` in `raw_text_cache`.
Run: `python forage/collectors/pdf_infiltrator.py --status A1-PENDING`

### Rank 4 — TD-13 SAFLII Bridge
Case Alpha institutional bridge gap (CoE = 0.28).
SAFLII bridge hunt needed for the legal accountability thread.

### Rank 5 — Test Foundation
0% coverage on Conclave critical path. Need pytest fixtures and 15–20 tests
covering: `ingest_signal()`, `get_or_create_actor()`, CT-1 gravity scoring.

---

_Last updated: 2026-05-09 — Execution Injection 02 complete._
