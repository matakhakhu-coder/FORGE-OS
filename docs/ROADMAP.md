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
- [x] ~~`--no-flux` flag added to CLI (mirrors `--no-dork` pattern)~~ **superseded
      Sprint 2.5 (2026-07-23):** flag flipped from opt-out `--no-flux` to opt-in
      `--with-flux` when FLUX was pulled out of the default pipeline path — see note
      below and Sprint 2.
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

**Note (2026-07-23):** FLUX/SOCINT is being pulled out of the default pipeline
path per the scope-trim audit below — the code stays (real design work,
~3,600 lines), but it no longer runs or shows in nav until there's an actual
use case pulling it forward. See Sprint 2.

---

## Launch-Readiness Sprints  (added 2026-07-23)

Source: full-repo bug diagnosis (5-pass triage: symptom triage, data
integrity, feature correctness, code quality, ops readiness) + a
scope-trimming audit (inventory of every subsystem against real usage
evidence, biased toward cutting). Supersedes the old "Next Priorities" list
below — see the superseded notes there for what already turned out to be
fixed or moot. This section is the execution checklist; full diagnostic
detail lives in session history, not reproduced here.

**Explicitly out of scope for these sprints:** gravity escalation
recalibration (needs real post-launch usage data before touching
thresholds), the wiki system (stays unexposed, not broken), auth/access
control (separate track).

### Sprint 0 — Contain the risk  (folded into the same pass as Sprint 2, 2026-07-23)

- [x] Delete `tools/inject_epstein_sa.py` — fabricates a named real-world
      "indirect_association" with a fake-looking source citation, no
      `source_type` tag, writes directly to live `actors`/`entity_relationships`.
      **Turned out to be a 3-script coordinated chain, not one file** —
      found while checking cross-references before deleting:
        - `tools/flight_log_matcher.py` — fires off the real "Jacob Zuma"
          name alone (no dependency on the other script), prints a fully
          scripted fake "HEURISTIC ALERT" flight manifest naming Epstein,
          Zuma, and a "[REDACTED_DIPLOMAT_ZA]". Zero real data source —
          pure print-to-stdout theater, but would look like genuine system
          output to anyone running it.
        - `tools/compile_red_folder.py` — the most dangerous of the three.
          Production-grade (transactions, integrity checks, sanitization),
          creates a case literally named "OP: DARK BADGE / RED FOLDER" with
          a fabricated hypothesis naming Epstein/Zuma/Matlala, then queries
          **real** high-confidence `entity_relationships` and grafts those
          real actors into the fabricated case as `event_actors` with
          `role='syndicate_node'` — i.e. it would pull genuinely-collected
          people into a fictional conspiracy narrative.
      All three confirmed never executed against the live DB (no matching
      cases, no `syndicate_node` rows, no injected actors) — deleted before
      any of them could be. `bridge_hunt.py` and `coalition_interceptor.py`
      were checked too (name/keyword collision) and are unrelated — real
      pipeline code, not part of this chain.
- [x] Spike: `counterintel` — see note below.
- [ ] Sort remaining `tools/` into keep vs. archive — not done this pass,
      still open for Sprint 2 wrap-up.

**Exit criteria met:** nothing in the repo can silently contaminate the
live DB.

### Sprint 1 — Data integrity  (highest stakes — COMPLETE 2026-07-23)

- [x] Fix `forage/collectors/civic_intel_collector.py` hardcoded
      `default_lat`/`default_lng` (→ removed from all 14 sources).
      One-time cleanup done: 4,200 signals with fake coords nulled out
      (verified all matched exactly the 4 known fake pairs, nothing else),
      `correlated_incidents` purged (697 → 0) and `correlation_engine.py`
      rerun clean (0 signals in the 48h window — expected, pipeline is
      stale, see the still-open pipeline-freshness item below).
- [x] Thread real entity types through `signal_interpreter.py` /
      `entity_resolver.py` / `core/conclave/engine.py` / `entity_engine.py`
      so places stop defaulting to `institution`. Root cause went deeper
      than expected: `EntityResolver._create_actor()` (the actual primary
      actor-creation path, running before entity_engine ever sees the
      signal) hardcoded `"institution"` with zero type awareness, and
      separately `run_conclave()`'s provenance merge nested per-engine
      `entity_types` under `sources[i]` instead of flattening to the top
      level `materialize_entities()` reads — so even geo_enrichment's
      already-correct typing was getting silently discarded. Both fixed.
      Backfilled 17 existing mistyped actors (Gauteng, Pretoria, South
      Africa, Cape Town, etc.) against geo_enrichment's own province/city
      list.
- [x] Fix `is_targeted` scoring in `core/web/blueprints/pages.py` +
      `core/web/blueprints/admin.py` (both had the same bug). A flat ratio
      threshold alone was not enough — tested against live data, corpus-wide
      "hot" rate is ~17%, so any high-volume actor tracking the corpus
      average still passed a flat 10% bar. Fixed with two parts: ratio vs.
      corpus baseline (2x), plus excluding `actor_type='location'` entirely
      — a city can't meaningfully be an investigative target regardless of
      how much crime news is geotagged there.
- [x] Swap `description`/`hypothesis` fields in
      `api_correlation_promote_case()` (`core/web/blueprints/cases.py`).
- [x] `tests/test_sprint1_fixes.py` — 13 tests, all passing, covering all
      four fixes above.

**Follow-up finding, fixed same day:** one pre-existing actor literally
named `"location"` (not mistyped — the *name* itself, `actor_id=4`,
312 linked signals) — `entity_resolver.py`'s creation path had no
`_BLOCKED_ACTOR_NAMES` guard, unlike `entity_engine.py`. Row + its
graph_node (312 cascaded signal_actors/graph_edges rows) deleted; the two
paths now share one blocklist import so they can't drift apart again.
Test added (14 tests total, all passing).

**Exit criteria met:** every number and label on screen reflects something
real; regression test exists for each fix.

### Sprint 2 — Cut the dead weight  (COMPLETE 2026-07-23 — verdicts revised mid-sprint on real evidence)

- [x] Remove THE_MACHINE/SAMARITAN theming, "Cycle UI aesthetic" button,
      targeting-label badges. Done in `templates/base.html`,
      `static/css/main.css`, `templates/actors.html`, `templates/actor.html`.
      Kept the underlying signal (plain `THREAT LEVEL: ELEVATED` line,
      `.targeting-dot`/`.targeting-level--*` colour coding) — only the
      costume (banners, scan animations, POV toggle) is gone.
      `FORGE_OS_MANIFEST.md` updated to match.
- [x] **coalition_detector / emergence_engine / counterintel — verdict
      changed from the original scope-trim audit.** That audit's "0 rows,
      ever" reasoning turned out to be incomplete: all three have genuine
      Control Room buttons (`templates/diagnostics.html`) wired to real
      `/api/control/run_*` triggers — they'd simply never been clicked, and
      two of the three are time-windowed engines with nothing recent to
      analyse (pipeline stale since 2026-07-11). Ran a fresh full pipeline
      (`tools/mega_ingest.py`, 13,997 new signals, actors 89→1,165) then
      triggered all three directly to get a real answer instead of a
      guess:
        - **counterintel: KEEP.** Found a real narrative cluster (3
          signals, 2 sources, 0.82+ similarity) on the first real run.
          Proven working, not dead code.
        - **coalition_detector: DEFER, not cut.** Ran correctly — "1 pairs
          found, 0 above threshold=5". The corpus genuinely lacks dense
          multi-actor co-occurrence right now (most signals link 0-1
          actors). Not broken, just data-starved. Revisit as the corpus
          matures.
        - **emergence_engine: DEFER, not cut.** "No actor-event links in
          current window — skipping." Hard-blocked by `events` having only
          2 rows, which traces directly to the gravity-escalation issue
          already deferred elsewhere in this roadmap (Sprint 1's High #4).
          Structurally cannot produce anything until that's addressed —
          revisit together, not separately.
      No code removed for any of the three; this supersedes Sprint 2.2/2.3
      as originally scoped.
- [x] Pull FLUX/SOCINT out of the default pipeline path and nav.
      `tools/mega_ingest.py`: `x_pulse`/`x_search` added to `_SEVERED_IDS`.
      `tools/sovereign_pipeline.py`: `--no-flux` (opt-out) flipped to
      `--with-flux` (opt-in, off by default). `templates/base.html`: FLUX
      sidebar section ("Centrifugal Net") removed. Routes/code untouched —
      still reachable directly, just not auto-run or nav-exposed.
- [x] Update `CLAUDE.md`'s "Active modules" list to match reality — see
      below.

**Exit criteria met, revised:** theming is gone. `forge_modules/` modules
were evidence-checked rather than assumption-cut — all seven stay active
(none proven dead once actually exercised against real data), with
coalition_detector/emergence_engine flagged for revisit once the corpus/
escalation pipeline catches up.

### Sprint 3 — Feed & search correctness  (COMPLETE 2026-08-02)

- [x] Drop correlation's permanent `stream_weight=1.0` pin in `api_feed()` —
      now derived from `max()` of the two constituent signals' own stream
      weights, same formula as SIGNAL items.
- [x] Replace bare `except Exception: pass` in `api_feed()`'s four branches
      with logged exceptions (`_log.exception(...)`).
- [x] **Found and fixed a real bug the logging immediately surfaced:** the
      SIGNAL branch was mixing a positional `?` placeholder
      (`source_type_clause = "s.source_type = ?"`) with a named-params dict
      (`:stream`/`:source_type`) in the same `execute()` call — SQLite
      rejects that outright (`Binding 1 has no name, but you supplied a
      dictionary`). This had been silently swallowed by the old bare
      `except: pass` on every single `/api/feed` call where `lens != 'all'`
      (i.e. the default state) — the SIGNAL item type had likely been
      failing 100% of the time, not merely losing a ranking contest to
      correlations as originally suspected. Fixed by switching the clause
      to `:source_type`. Verified live: Signals now show 26/30 loaded items
      on a fresh feed load, versus 0 before.
- [x] Fix feed's two-contradictory-totals labeling — `templates/feed.html`
      status bar. "Total" (ambiguous) split into "Matched" (this feed
      view's candidate count, pre-pagination) and "Loaded so far:" (prefix
      on the Alerts/Signals/Correlations/Leads breakdown, which only counts
      items actually rendered). The separate per-pill archive-wide counts
      ("All 37,964" etc.) were already correct on their own — just visually
      adjacent to the status bar with nothing distinguishing the two
      questions they answer.
- [x] Add a source-type facet to `/search` so OFAC/global-reference data can
      be excluded. Turned out bigger than scoped: **91.7% of all artifacts
      (7,040 of 7,678) are bulk OFAC SDN sanctions entries**, and no
      structured column distinguishes them — `artifacts.source` holds a
      verification tier ("unverified"/"government"), not collector
      identity, and the OFAC collector writes `artifacts`/`signals`
      independently with no reliable link between them
      (`source_artifact_id` is NULL on genuine OFAC signals). Title-pattern
      match (`NOT LIKE '%OFAC SDN%'`) was the only signal available without
      a schema migration. Excluded by default, opt back in via a checkbox
      (`include_sanctions=1`). Verified live: "election" now correctly
      returns "no results" instead of 51 false-relevant OFAC hits; "SAPS"
      returns 20 genuine artifacts with an "(OFAC/sanctions entries
      excluded)" note.
- [x] Fix empty-title case-form silent failure — `templates/cases.html`.
      `novalidate` + explicit JS validation with an on-brand inline error
      state, replacing reliance on the native browser bubble. Verified
      live: blocked submission (no case created across repeated attempts),
      visible "⚠ Case title is required." message on empty submit, clears
      once typing starts.

**Exit criteria met.** Bonus finds along the way, both confirmed fixed:
- The SIGNAL branch of `/api/feed` was mixing a positional `?` with a
  named-params dict — SQLite rejects that outright. Silently swallowed by
  the old bare `except: pass` on every call where `lens != 'all'` (the
  default). The SIGNAL item type had likely been failing 100% of the time,
  not merely losing a ranking contest to correlations as first suspected.
  Caught immediately once the Sprint 3.3 logging landed. Verified live:
  Signals went from 0/30 to 26/30 loaded items on a fresh feed load.
- A copy-paste typo introduced while editing the search route's event-join
  (`ON e.event_id = e.event_id`, self-referential) was caught and fixed
  before shipping via the same read-after-edit discipline used throughout.

### Sprint 4 — Nav & workbench simplification  (COMPLETE 2026-08-02)

- [x] Trim nav 13 → 7 (Feed, Signals, Search, Actors, Map, Cases, Timeline).
      **The real nav was 21 items, not 13** — the original UX walkthrough
      got truncated mid-read and never saw the full sidebar. Confirmed with
      the user before touching anything: trimmed only the originally-scoped
      content-browsing sections (Intelligence/Explore/Cases/Signals) down to
      the agreed 7. Left untouched on the user's explicit instruction:
      Gallery, Sentinel, and the whole System section (Quarantine, Control
      Room, Discovery, Admin, System Status) — operator tooling, never
      analyzed, not part of this trim. Cut from nav (routes still reachable
      directly by URL, just unlinked): Surface, Archive Home (redundant
      with the FORGE wordmark link), Intel Dashboard (see below), Events
      (redundant with Timeline), Archive Intelligence/wiki, Graph, Intel
      Graph. "Workspaces" renamed to "Cases" per the original plan.
- [x] **`/intel` dashboard — verdict reversed from "fold or cut."** Read the
      actual template before touching it: it's a well-built, self-
      configuring dashboard (dynamic module discovery via `/api/fms/ui`,
      independent per-panel loading, table/list renderers) — not "3 dead
      module panels" as assumed when this was scoped. Checked live: only 3
      modules declare a `"ui"` block (coalition_detector, counterintel,
      emergence_engine) — exactly the three evidence-checked in Sprint 2.
      counterintel's panel now shows real content (the narrative-cluster
      find); the other two show honest empty states, not errors or broken
      UI. **Kept, not cut or folded.** Removed from primary nav (matches
      the trim above) but linked directly from Control Room — added a
      "⊞ View Results →" link right after the Detect Coalitions/CounterIntel
      /Emergence Engine trigger buttons in `templates/diagnostics.html`, so
      triggering a scan and viewing its output are one click apart.
- [x] Collapse the two search boxes into one — reconsidered the original
      framing: the topbar search box is global (every page), the body box
      only exists on `/search`. Removing the topbar one entirely would
      have lost real functionality (search-from-any-page without
      navigating first). The actual redundancy only existed on `/search`
      itself, where both appeared together. Fixed by hiding the topbar box
      specifically on that page (`request.endpoint != 'pages.search'`),
      keeping it everywhere else.
      **Found and fixed a bigger, adjacent bug while implementing this:**
      `request.endpoint` for a Flask blueprint route is prefixed with the
      blueprint name (e.g. `pages.search`, not bare `search`) — every
      sidebar "active" highlight check in `templates/base.html` (13 of
      them) compared against the bare, unprefixed name and had likely
      never matched, meaning the nav had probably never shown which page
      you were on. One check (`diagnostics.status_dashboard`) was already
      correctly prefixed, which is what surfaced the inconsistency. Fixed
      all 13. Verified live: Actors page now correctly highlights "Actors"
      in the sidebar.
- [x] **Case workbench view toggles — verdict reversed, same pattern as
      `/intel` above.** Read the actual implementation before deciding
      instead of assuming from the names:
        - **Narrative Mode** is a real, distinct capability (~150 lines,
          "Phase 10 — Narrative Threader") — drag-and-drop manual
          re-sequencing of actors/events/artifacts with per-transition
          annotation notes. Fundamentally different from Timeline, which
          only shows strict chronological order and has no concept of an
          analyst-authored narrative sequence or transition notes. Not
          something a deep-link could replace.
        - **Tactical Map** ("Phase 11") turned out to be the *only*
          currently-reachable way to see a single case's events on a
          focused map. `/api/geo/case/<case_id>` exists and would support
          a case-filtered view, but `templates/map.html` only uses
          `case_id` for marker colour and popup links — there's no case-
          filter control on the main Map page itself. Deep-linking to Map
          would have been a functionality *loss*, not a simplification.
      **Kept all six view modes** (Workbench, Context Feed, Context
      Signals, Narrative Mode, Tactical Map, Intelligence Briefing) — none
      proved redundant on inspection. Verified live on case #12: all six
      present, correctly wired, zero console/server errors after the
      full Sprint 1–4 change set.
- [x] Fixed hardcoded internal phase-number labels — broader than
      originally scoped. "FORAGE · PHASE 31" (feed.html) was one instance
      of a repeated pattern; found and fixed six: `feed.html` ("Intelligence
      · Feed"), `intel.html` ("System · Module Output"), `diagnostics.html`
      ("System · Control Room"), `discovery.html` and `evolution.html`
      (both "System · Evolution" — they're the same feature area), and
      `flux_discovery.html` ("FLUX · SOCINT", dropped just the "PHASE I").
      Verified live: feed.html now shows "INTELLIGENCE · FEED".
- [x] Fixed backend enum casing (`CRIME_INTEL` → readable). The main
      high-visibility spots (`feed.html`, `diagnostics.html`'s Stream
      Distribution) already had `.replace('_', ' ')` — found and fixed
      three more raw, unreplaced spots: `admin_event_new.html`,
      `case_workbench.html` (both via Jinja's `replace` filter), and
      `status.html`. Note: the site-wide CSS already forces
      `text-transform: uppercase` on these labels, so full title-casing
      wouldn't render any differently than the underscore-stripped
      version — kept the fix consistent with the pattern already
      established elsewhere rather than inventing a heavier one.
- **New finding while verifying, not fixed (out of this sprint's scope):**
  `templates/status.html` uses HTMX (`hx-get`, `hx-trigger="load, every
  30s"`) to auto-load its metrics panel, but confirmed via `git diff` that
  no `<script src=".../htmx...">` include exists anywhere in `templates/`
  — not removed by any edit this sprint, never present. `status.html` is
  the *only* template using `hx-*` attributes. The System Status page has
  likely never auto-loaded its metrics, ever. Flagged for Sprint 5, not
  fixed here — unrelated to nav/label work, deserves its own attention
  (add the script include, verify nothing else depends on its absence).

**Exit criteria met:** nav is 7 items, not 13; a first-time user can tell
what each one does without clicking in.

### Sprint 5 — Ops hardening  (COMPLETE 2026-08-02)

- [x] Audit `sqlite3.connect()` for missing `timeout=`. Bigger than the ~15
      files estimated — **38 bare calls across 34 files** (collectors,
      engines, processors, and nearly all of `tools/`/`scripts/`
      /`maintenance/`/`migrations/`). Fixed with a paren-depth-aware script
      (several calls have nested parens, e.g.
      `sqlite3.connect(str(Path(__file__).resolve()...))`, which a naive
      regex would have broken on) rather than 38 manual edits — dry-run
      previewed first, applied, then every touched file syntax-checked
      (`py_compile`, all 34 clean) and spot-checked for correct insertion
      at the true closing paren. `flux/` already had `timeout=` on all 9 of
      its calls — confirmed, not touched. Zero bare calls remain anywhere
      in the active codebase. Full test suite (20/20) and a live app
      restart both clean afterward.
- [x] Implement auto-pin for `case_events`/`case_artifacts` (mirroring the
      existing signal/actor auto-pin). Added `bridge_events_to_cases()` and
      `bridge_artifacts_to_cases()` to `tools/mega_ingest.py`, wired into the
      pipeline right after the existing PDF bridge call (Phase 2.8), same
      gating (`not args.collect_only and not args.engines_only`). Both
      tables were confirmed genuinely empty before this — `case_events`/
      `case_artifacts` had 0 rows regardless of corpus size, an asymmetry
      with `case_signals` (which does get auto-pinned). `case_actors`
      itself is never auto-populated by any pipeline code — it's analyst-
      curated, and both new bridges (like the existing ones) read it as the
      seed rather than writing to it.
      - `bridge_events_to_cases()`: event → `event_actors` (fallback
        `actor_events`) → actor → `case_actors` → case → `INSERT OR IGNORE
        INTO case_events`.
      - `bridge_artifacts_to_cases()`: artifact → its *originating signal*
        (`signals.source_artifact_id` → `signal_actors`) → actor → same
        case lookup → `INSERT OR IGNORE INTO case_artifacts`, falling back
        to the artifact's linked event's actors when no originating signal
        has actor links.
      - **Performance bug found and fixed before landing:** the first draft
        of `bridge_artifacts_to_cases()` mirrored `bridge_pdf_signals_to_cases()`'s
        per-row query idiom exactly — one query per artifact against
        `signals` filtered on `source_artifact_id`. That column has no
        index, `signals` has 38,575 rows, `artifacts` has 7,678 — a per-row
        query is a full table scan × every artifact. First live smoke-test
        run hung past 120s; traced to this, not to a locked/contended DB.
        Rewrote to three single bulk queries (`signals⋈signal_actors`,
        `event_actors`, `case_actors`) building in-memory dict maps, then
        joining per-artifact in Python. Re-run: 7,678 artifacts in 1.44s.
        (The existing `case_actors WHERE actor_id IN (...)` per-row pattern
        in the older bridges is fine as-is — `case_actors` is only 41 rows,
        full-scanning it per call is trivial; the bug was specific to
        joining against the 38k-row `signals` table with no supporting
        index.)
      - Live smoke test against the real DB (14 cases, 41 case_actors, 2
        events, 7,678 artifacts): `bridge_events_to_cases` → 1 event pinned,
        1 skipped (no actor overlap). `bridge_artifacts_to_cases` → 32
        artifacts pinned, 7,648 skipped (no actor overlap — expected, most
        artifacts have no NER-extracted actors on their originating
        signal). Re-ran both a second time — row counts unchanged (1 / 32),
        confirming `INSERT OR IGNORE` idempotency holds.
      - Full regression suite (`test_sprint1_fixes` + `test_sprint3_fixes`,
        20 tests) clean after the change.
- [x] Audit and correct `tech_debt.md`/`ROADMAP.md` against actual code state.
      Found and fixed four stale items:
      - `docs/tech_debt.md` P3.2-05 (and its P2-04 cross-reference, and the
        Debt Summary Table row) still marked the 6,458-scanned-PDF OCR
        backlog as `⬜ PENDING`/HIGH — this was already established moot in
        this same session (see "Next Priorities" Rank 3 above), just never
        propagated back into `tech_debt.md` itself. Re-verified live against
        the current DB (0 `A1-PENDING` rows exist at all; the 395 `pending`
        rows have 0 with thin cache and 0 with a `file_path`) and corrected.
      - `CLAUDE.md`'s "Known Tech Debt" table carried the same stale P3.2-05
        claim — struck through to match.
      - `docs/tech_debt.md`'s DB-01 "Still outstanding" note (~40 additional
        bare `sqlite3.connect()` calls) was superseded by Sprint 5.1 — marked
        resolved with a pointer back to this file.
      - TD-20 (`graph_nodes` vs `actors` imbalance) cited stale counts —
        463k/1,011 — in both `CLAUDE.md` and `tech_debt.md`. Live count is
        35,271/1,167: the imbalance narrowed from ~458:1 to ~30:1 at some
        point (likely Substrate Reconstruction) without the ledger being
        updated. Corrected the figures in both files; left status DEFERRED
        since the ratio, though much smaller, is still real.
      - "Objective C — FLUX Wave Integration" (top of this file) still
        documented the `--no-flux` CLI flag, which Sprint 2.5 flipped to
        `--with-flux` (opt-in). Struck through and cross-referenced.
      - Added `docs/tech_debt.md` **P3-11** documenting the Sprint 5.2
        case_events/case_artifacts fix (new debt item + resolution, so the
        ledger has a record of the asymmetry that used to exist).
- [x] Fill remaining Conclave-critical-path test coverage beyond what rode
      along with Sprint 1's fixes. Sprint 1's tests covered one specific bug
      (entity_types merging) — the surrounding merge arithmetic and the
      escalation decision gate had zero coverage. Added
      `tests/test_sprint5_conclave.py` (16 tests, isolated in-memory DB or
      pure data structures, no live DB touched):
      - `TestRunConclaveMerging` (6 tests) — `core/conclave/engine.py`'s
        `run_conclave()`: gravity/confidence are averaged not summed,
        recommendation is majority vote, entities are unioned+deduped,
        intent comes from the first result, empty input returns the
        IGNORE default, sources list has one entry per input result.
      - `TestRunConclaveWithModules` (4 tests) — `run_conclave_with_modules()`.
        The most valuable find here: CLAUDE.md documents "Module failures
        are isolated — a crashing module cannot kill Flask or block
        ingestion" as an architectural guarantee, but nothing actually
        verified it. Added a test with a module engine that always raises
        — confirmed it's caught, logged, and skipped, while a second
        well-behaved module's result still merges in correctly alongside
        the core result. Also covered: a module returning the wrong type
        is silently skipped, and an `FMS` context-lookup failure (module
        system not initialized) falls back to core-only results rather
        than raising.
      - `TestEscalationThresholds` (6 tests) — `forage/engines/escalation_engine.py`'s
        `handle_escalation()`, previously fully untested: gravity ≥
        ESCALATE_THRESHOLD (0.55) + `ESCALATE` rec creates a case (not an
        event); gravity ≥ MONITOR_THRESHOLD (0.35) + `MONITOR`/`ESCALATE`
        creates an event (not a case); below MONITOR does nothing; high
        gravity with an `IGNORE` recommendation does nothing (both
        conditions are required, not gravity alone); an existing case for
        the same `trigger_signal_id` blocks re-escalation (idempotency);
        and a non-obvious one — when ESCALATE-eligible but blocked by the
        5-cases-per-hour rate limit, `handle_escalation()` returns `None`
        outright, it does **not** downgrade to creating an event via the
        MONITOR branch.
      Full suite (`test_sprint1_fixes` + `test_sprint3_fixes` +
      `test_sprint5_conclave`, 36 tests) passes clean.
- [x] Fix missing HTMX include on `status.html` (flagged in Sprint 4).
      `status.html` is the only template in the codebase using `hx-*`
      attributes (`hx-get="/api/status/metrics"`, `hx-trigger="load, every
      30s"`), but no `<script src=".../htmx...">` include existed anywhere
      in `templates/` — confirmed via `git diff` in Sprint 4, not removed by
      any edit this project, never present. The metrics panel had likely
      never auto-loaded, ever. Fixed by adding a CDN script tag scoped to
      `status.html` itself (`<script src="https://unpkg.com/htmx.org@1.9.12"
      crossorigin="anonymous">`), matching this codebase's existing
      per-template CDN pattern for Leaflet/Chart.js/D3/Cytoscape/Sortable —
      none of those are vendored locally or included globally in
      `base.html` either. (First draft added a `integrity=` SRI hash
      recalled from memory, not verified — caught before landing: this
      codebase's existing CDN includes never use SRI, and a wrong hash
      would have silently broken the script with a browser-level failure
      that's easy to miss. Dropped it to match the established, working
      pattern instead of introducing an unverified claim.)
      Verified live: restarted the Flask preview (template changes need a
      restart, not just a reload — same caching behavior noted in earlier
      sprints), navigated to `/status`, confirmed via `get_page_text` that
      real metric values now render (Signals 38,575 · Actors 1,167 · Graph
      Nodes 35,271 — consistent with the TD-20 figures corrected in Sprint
      5.3) and via the network log that `GET /api/status/metrics` fires and
      returns 200 — it was never being called at all before this fix.

**Exit criteria:** the app survives concurrent load and a schema change
doesn't fail silently.

---

## Next Priorities  [SUPERSEDED 2026-07-23 — see Launch-Readiness Sprints above]

### ~~Rank 1 — Actor Naming Quality~~ — already fixed, doc was stale
`signal_interpreter.py _extract_actors()` already uses `findall()` to
capture matched text, not the dict key (confirmed via code read 2026-07-23).
The actor-*type* defaulting bug this was adjacent to is a separate, still-open
issue — see Sprint 1.

### ~~Rank 2 — Confidence Gate Calibration~~ — already fixed, doc was stale
`entity_engine.materialize_entities()` gates at `confidence >= 0.20`
already (comment says "calibrated to 0.25" — minor doc/code drift, not a
real gap). Confirmed 2026-07-23.

### ~~Rank 3 — P3.2-05 OCR Run~~ — moot, doc was stale
The 6,458-PDF backlog belonged to the pre-Substrate-Reconstruction dataset
(April 2026 audit: 564,953 artifacts). Current DB, post-reconstruction: 0
rows match the A1-PENDING + thin-cache criteria. Confirmed 2026-07-23 —
don't re-chase this.

### Rank 4 — TD-13 SAFLII Bridge — still open
Case Alpha institutional bridge gap (CoE = 0.28). Not covered by the
launch-readiness sprints above; revisit post-launch.

### ~~Rank 5 — Test Foundation~~ — folded into Sprint 1 + Sprint 5
0% coverage on Conclave critical path, still true. Addressed incrementally
per-fix in Sprint 1 rather than as one big separate effort, with remaining
gaps swept in Sprint 5.

---

_Last updated: 2026-07-23 — Launch-readiness sprint plan added (5-pass bug
diagnosis + scope-trim audit). Execution Injection 02 complete as of
2026-05-09._
