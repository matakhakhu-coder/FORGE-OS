# FORGE-OS Technical Debt Ledger

**Last Audited:** 2026-04-19 (Phase 71 — Surgical Strike & Surface Transition)
**Auditor:** FORGE Technical Debt Lead / Phase 71
**State at audit (Phase 71):** TD-12 resolved. event_actors purged: 340 → 214 rows. All 8 zero-trust FK checks pass with 0 orphans. Integrity verdict upgraded AMBER → **FIELD READY**. See `docs/SUBSTRATE_AUDIT_REPORT_APRIL_2026.md`.

**Previous audit (Phase 70, 2026-04-19):** Substrate interrogation complete per `docs/SUBSTRATE_INTERROGATION_SOP.md`. SQLite 3.50.4. FK enforcement: ACTIVE (process-wide monkey-patch, Phase 68). Actors: 1,011. Signals: 51,155. Artifacts: 564,953. Events: 490. entity_relationships: 312. signal_actors: 3,643. event_actors: 340 (127 pre-FK orphans). Integrity verdict: AMBER.

**Previous audit:** 2026-04-14 — Phase 3.2 complete (Mass Extraction + P3.2-01 Resolved). 20,000 A1-PENDING artifacts processed via loop mechanic (6.8/s, 2,935s total). signal_entities: 11,697 → 35,112 (+23,415 entities). 156 entity_relationships (103 co_occurrence, 36 INVESTIGATES, 9 ACCUSED_OF, 5 CONTRACTED, 3 MEMBER_OF). 7 A-tier nexus signals. All 5 Aspirant Prosecutors indexed as actors; 0 relational triples (PAIA Manual is contributors list — no relational prose in current corpus). signals table: 187,971 total; 1,021 triple-extractor eligible. 833 actors.

---

## Security Debt — STATUS: RESOLVED ✅

The items below were implicit risks in the previous architecture. All are resolved as of
Phase 3 / forge_security v1.0.

- [x] **XSS via scraped signal text** — All collector output now passes through
  `forge_security.sanitizer.sanitize_signal_text()` (bleach-backed allowlist strip).
  SQL injection probes are detected, logged, and scrubbed before any DB write.
  _Resolved: `forge_security/sanitizer.py`_

- [x] **XSS via rich HTML fragments** — `sanitize_html_fragment()` enforces a strict
  tag/attribute allowlist and forces `rel="noopener noreferrer"` on all `<a>` tags.
  _Resolved: `forge_security/sanitizer.py`_

- [x] **Malicious PDF payloads (embedded JS, launch actions, XFA forms)** — Every PDF
  now enters through `detonate_pdf()`: magic-byte verification → pikepdf re-serialization
  (strips `/OpenAction`, `/Names/JavaScript`, per-page URI/launch annotations) → metadata
  wipe → size and page-count gates. Non-conforming files are quarantined before any text
  extraction occurs.
  _Resolved: `forge_security/detonator.py` + `pikepdf`_

- [x] **No quarantine / audit trail for rejected artifacts** — The `/quarantine`
  Contagion Ward UI lists all air-lock rejections with failure classification, sidecar
  metadata (reason, size, timestamp), Force Delete overlay, and Purge All. The sidebar
  badge shows live quarantine count on every page.
  _Resolved: `templates/quarantine.html`, `forge_security/detonator.py` sidecar writer_

- [x] **Unsafe subprocess calls (shell=True, heredoc piping)** — All external process
  invocations now route through `forge_security.pipeline_wrapper.run_safe()` which
  enforces list-based args, an executable allowlist, timeout escalation, and env
  sanitization. The AMSI-triggering heredoc pattern has been eliminated.
  _Resolved: `forge_security/pipeline_wrapper.py`_

- [x] **No supply-chain CVE visibility** — `forge_security.audit.run_pip_audit()` shells
  to `pip-audit` with a safe list-based call and writes a timestamped JSON report to
  `logs/`. PowerShell history forensics read the file directly (no PS process spawned).
  _Resolved: `forge_security/audit.py`_

---

## Priority 0 — Schema & API Alignment (NEW DEBT)

Issues introduced by the `title → name` DB migration that must be completed before the
SQL alias scaffolding (`SELECT name AS title`) can be removed.

---

### P0-01 · Complete Schema Alignment (`title` → `name` Hard Transition) ✅ RESOLVED

- [x] **Status: RESOLVED — Phase 3.1 (2026-04-13)**
- All `name AS title` SQL aliases removed from `app.py`, `core/gravity.py`,
  `forage/engines/archive_engine.py`.
- Templates updated: `cases.html`, `case_detail.html`, `case_workbench.html`,
  `briefing.html`, `index.html`, `_pin_widget_js.html`, `base.html` (CT-1 focus pill),
  `diagnostics.html`.
- `setFocusCase` localStorage key updated from `title` → `name`.
- `actors` briefing alias (`ac.name AS title`) intentionally preserved — actors
  have no native `title` column; this alias feeds the unified briefing template.
- Note: `c.title_a` / `c.title_b` in `index.html` are correlation signal fields
  (not cases) — correctly left unchanged.

---

### P0-02 · `google.generativeai` → `google.genai` Migration 🟡 MEDIUM

- [ ] **Status: PENDING**
- **Context:** The synthesis engine (wiki logger, briefing generator) imports
  `google.generativeai`. Google deprecated this package in favour of `google.genai`
  (the new unified SDK). The old package will stop receiving updates and may be removed
  from PyPI.
- **Affected files:** Grep for `import google.generativeai` across `core/` and
  `forage/` — expected hits in `core/pipeline/synthesizer.py` and
  `core/pipeline/wiki_logger.py`.
- **Required work:**
  1. `pip install google-genai` (replaces `google-generativeai`).
  2. Replace `import google.generativeai as genai` with `from google import genai`.
  3. Update `GenerativeModel(...)` → `genai.Client()` / `genai.models.generate_content()`
     per the new SDK surface.
  4. Update `requirements.txt`.
- **Effort:** ~1–2 hours (API surface change is small but requires smoke-testing the
  briefing and wiki synthesis output).
- **Risk if deferred:** Silent breakage if `google-generativeai` is pulled; deprecation
  warnings in logs currently polluting pipeline output.

---

## Priority 1 — Thermal & Stability

- [ ] **P1-01** · SIU No-Intel PDFs re-downloaded every run — `pdf_infiltrator.py`.
  No persistent "tried and failed" cache. ~3–4 min wasted per run.
  _Fix: insert `artifacts` row with `processing_status='no_intel'` on zero-intel exit._

- [ ] **P1-02** · `_migrate_media_root` opens new DB connection per file — `pdf_infiltrator.py`.
  _Fix: accept optional `conn` parameter._

- [ ] **P1-03** · Full PDF bytes held in RAM before pdfplumber — `pdf_infiltrator.py`.
  Peak RAM = download buffer + pdfplumber buffer simultaneously (~30–40 MB per PDF).
  _Fix: stream to temp file on disk._

- [ ] **P1-04** · `gc.collect()` called after every PDF regardless of size — adds ~150–300 ms
  per call unconditionally.
  _Fix: only call when `len(pdf_bytes) > 5_000_000`._

- [ ] **P1-05** · `pdf_infiltrator` not wired into `mega_ingest.py` — must be invoked
  manually. No automated portal crawl.
  _Fix: add behind `--include-portals` flag in Phase 1 collector list._

- [ ] **P1-06** · NULL `gravity_score` on ~7,418 signals — invisible to prioritization feed.
  _Fix: add backfill step after each collection run._

- [ ] **P1-07** · `pipeline_logger` loaded via `importlib.spec_from_file_location` on
  every module load — `graph_engine.py` lines 41–55.
  _Fix: standard relative import or single `sys.path` mutation at package init._

- [ ] **P1-08** · `artifacts.file_path` not set for pdf_infiltrator portal signals — 0 rows
  with `source_type='pdf_portal'`. UI evidence viewer cannot find physical documents.
  _Fix: audit `_insert_artifact()`, assert `file_path` is populated._

---

## Priority 2 — Extraction Fidelity

- [ ] **P2-01** · Amount regex misses common SA formats (`R4.5m`, `R4,5 million`, `R4.5bn`) —
  `pdf_infiltrator.py` `_AMOUNT_PATTERN`.

- [ ] **P2-02** · Awardee regex misses "appointed as service provider", "preferred supplier",
  "winning bidder" — `_AWARD_PATTERN`.

- [ ] **P2-03** · `_DEPT_PATTERN` extracted but never stored — dead code in `_parse_intelligence()`.
  _Fix: add `"departments": []` to intel dict and populate._

- [ ] **P2-04** · No OCR for scanned/image PDFs — `pdf_infiltrator.py`. Court orders (highest
  value docs) yield 0 text. **Note:** The forge_security detonator now validates PDFs before
  processing; the next step is adding `pytesseract` fallback after detonation clears the file.
  _Fix: fallback to pytesseract after `detonate_pdf()` succeeds on a text-empty PDF._

- [ ] **P2-05** · `entity_resolver` full table scan per lookup — O(n×m) at 2,000 signals × 800
  actors. _(Partially mitigated by the in-memory dict cache added in the O(1) resolver sprint;
  confirm cache is active in all call paths.)_

- [ ] **P2-06** · spaCy `en_core_web_sm` not tuned for SA government entities — DPWI, HAWKS,
  SIU, NPA tagged MISC or missed.
  _Fix: custom `EntityRuler` pattern list for known SA actors._

- [ ] **P2-07** · Amount normalisation discards unit metadata — `4500.0` from `R4.5 billion`
  is indistinguishable from `R4500 million` after storage.

- [ ] **P2-08** · No confidence scoring on extracted intel — page position and occurrence
  count not factored.

- [ ] **P2-09** · NPA Legislation page downloads irrelevant statutory acts (7 of 11 PDFs).
  _Fix: exclude URLs matching `justice.gov.za/legislation/acts/`._

- [ ] **P2-10** · `entity_relationships.source_artifact_id` always NULL — no provenance trail
  linking a relationship back to its source artifact.

---

## Priority 3 — System Resilience

- [ ] **P3-01** · `DB_PATH` hardcoded in `pdf_infiltrator` — ignores `FORGE_DB` env var.

- [ ] **P3-02** · Single User-Agent across all portal requests — fingerprint / WAF block risk.
  _Fix: rotate pool of 4–6 realistic Chrome/Firefox UA strings._

- [ ] **P3-03** · `dork_collector` self-identifies as `FORGE-OSINT/1.0` — immediately flagged
  by CDN/Google rate limiters.

- [ ] **P3-04** · No retry logic for transient download failures — one `ConnectTimeout` =
  permanent skip for that run.

- [ ] **P3-05** · AGSA reports behind JavaScript wall — 0 PDFs ever found from AGSA PFMA/hub.
  Highest-value audit documents unreachable.

- [ ] **P3-06** · `NT_restricted` direct target is redundant — already found by NT_tender
  page scrape.

- [ ] **P3-07** · SIU sitemap 2-hop is fully serial — `time.sleep(2.0)` per URL = 80+ seconds
  minimum discovery time.
  _Fix: `asyncio` + semaphore (max 3 concurrent)._

- [ ] **P3-08** · No case-to-signal linkage for PDF portal signals — `case_events` has only 6
  rows; Operation Mabone signal never linked to Case #24.

- [x] **P3-09** · 548,635 artifacts stuck in `no_intel` processing status — 97% of artifact
  rows never processed. 🔴 CRITICAL → **RESOLVED Phase 3.1/3.2**
  272,864 A-tier artifacts (NPA/SIU/government PDFs) promoted to `A1-PENDING` via
  `rescue_artifacts.py`. `artifact_processor` updated to handle `A1-PENDING` status.
  Remaining 275,583 are C-tier/live signals — deliberately held back pending
  deeper NER pass.
  _Resolved: `scripts/rescue_artifacts.py`, `forage/processors/artifact_processor.py`_

- [ ] **P3-10** · 150,892 signals have NULL `cluster_id` (99.9%) — correlation engine and
  Sentinel alerts operating on a near-empty graph. 🔴 CRITICAL

---

## Priority 3.2 — Deep Extraction (NEW DEBT, discovered Phase 3.2 audit)

- [x] **P3.2-01** · `artifact_processor` limit=500 per batch — 272,864 A1-PENDING artifacts
  required ~546 sequential batch runs with no loop or continuation logic.
  **RESOLVED Phase 3.2 (Mass Extraction Sprint):** Added `run_loop()` method to
  `ProcessorManager` and CLI flags `--all`, `--batch-size`, `--max-artifacts`, `--mem-limit`.
  Loop drains the queue at 6.8 artifacts/sec until exhausted or capped. 20,000 artifacts
  processed in one run (2,935s, 0 failures). Windows cp1252 stdout issue fixed by wrapping
  stdout/stderr with UTF-8 TextIOWrapper at module load.
  **Additional fix:** Query now orders by `raw_text_cache > 200 chars DESC, artifact_id ASC`
  to process content-rich artifacts first (NPA PDFs before FIRMS noise).
  _Resolved: `forage/processors/artifact_processor.py`_

- [ ] **P3.2-02** · `triple_extractor` reads SIGNALS (via `relevance_score > 1.0`), not the
  272k A1-PENDING ARTIFACTS directly. After artifact_processor runs NER on the rescue batch,
  signal_entities will be populated — but entity_relationships won't update until
  triple_extractor is re-run with a `--since` date after the processor completes.
  _Fix: schedule triple_extractor after artifact_processor in mega_ingest phase order._

- [ ] **P3.2-03** · Generic actor noise in entity_relationships — "Provincial", "South Africa",
  "Suspect", "government", "company" committed as subjects in 7 of the 29 new links.
  `_GENERIC_SUBJECTS` filter deployed in triple_extractor (Phase 3.2) but the 7 dirty rows
  remain in the DB.
  _Fix: one-off DELETE WHERE subject_actor_id IN (SELECT actor_id FROM actors WHERE LOWER(name) IN (...))_

- [ ] **P3.2-04** · Self-referential NPA triple — `NPA --INVESTIGATES--> National Prosecuting Authority`
  (relationship_id=382). Both sides resolve to the same institution via different name forms.
  The UNIQUE constraint on (subject, object, relation_type) doesn't catch same-actor pairs
  with different actor_ids.
  _Fix: add actor dedup / alias table, or pre-merge NPA ↔ National Prosecuting Authority actor records._

- [ ] **P3.2-05** · 6,458 A1-PENDING PDFs have < 100 chars in raw_text_cache — likely
  scanned image PDFs (court orders, signed agreements). OCR pipeline exists in
  `artifact_processor.py` (fitz renders at 2x → pytesseract) but only activates when
  `raw_text_cache` is empty AND `file_path` exists on disk.
  _Fix: audit these 6,458 for file_path presence, then run processor with --status A1-PENDING._

- [x] **P3.2-06** · Actor-promotion gap — Real Aspirant Prosecutor names (`Bradley Smith`,
  `Mosalanyane Mosala`, `Refilwe Motshwane`, `George M Maphutuma`, `Theodore Leeuwschut`)
  were in `signal_entities` but NOT in the `actors` table.
  **RESOLVED:** `scripts/promote_staged_entities.py` created and executed. 10 actors promoted
  from `pdf_infiltrator` signal_entities (all 5 Aspirant Prosecutors + Gerhard Nel, David Mandaha,
  Thulani Dlamini, Dr Dlamini, Miss Lukhope). Multi-pass noise filter rejects equipment codes,
  building names, document headers, geographic noise. OCR newline artefacts handled
  (`'Refilwe\nMotshwane'` → `'Refilwe Motshwane'`). Actors are now match-ready in the
  triple_extractor actor_index.
  **Remaining gap:** The PAIA Manual signal that names all 5 Prosecutors contains a
  `Contributors:` list (no relational verbs), so no triples were extracted. Links will be
  created when A-tier signals contain relational prose naming them (e.g. "X was charged by
  NPA Prosecutor Bradley Smith").
  _Resolved: `scripts/promote_staged_entities.py`_

- [ ] **P3.2-10** · `_get_or_create_signal()` in `artifact_processor.py` creates signals
  with `relevance_score = 1.0` (the DB column default) — failing the triple_extractor's
  strict `> 1.0` threshold by a hair. Affects all 20,000 signals created in the mass
  extraction run. Workaround applied: manual backfill of `relevance_score = 1.2` for the
  417 NPA seed signals with rich artifact cache.
  _Fix: in `_get_or_create_signal()`, compute relevance_score based on source tier
  (A-tier=1.4, B-tier=1.2, C-tier=1.0) and pass it into the INSERT._

- [ ] **P3.2-11** · `triple_extractor` artifact-cache branch was restricted to
  `source = 'pdf_infiltrator'` — excluding signals from NPA/SIU seed artifacts whose
  signals carry `source = 'unverified'` (the original artifact source column). Workaround:
  broadened the OR branch to all artifact-linked signals with `raw_text_cache IS NOT NULL`.
  _Fix: in `artifact_processor._get_or_create_signal()`, set `source = 'pdf_infiltrator'`
  (or a proper A-tier source string) when creating signals from A-tier artifacts, rather
  than inheriting the raw `source` column from the artifact record._

- [ ] **P3.2-12** · Aspirant Prosecutor triples still 0 — all 5 Aspirant Prosecutors
  (Bradley Smith, Mosalanyane Mosala, Refilwe Motshwane, George M Maphutuma, Theodore
  Leeuwschut) are confirmed actors in the graph but appear only in a PAIA Manual contributors
  list with no relational verbs. The 20k mass-processed artifacts do not contain sentences
  of the form "Prosecutor X charged / investigated / appeared in case Y". To extract these
  triples requires: (a) NPA case newsletter PDFs with named prosecutor + case mentions, OR
  (b) court roll PDFs with "State vs. [accused]" + appearing counsel lines.
  _Fix: target NPA newsletter PDFs (Khasho) and court roll artifacts in the next A1-PENDING
  run; or use lower `relevance_score >= 0.8` threshold for seed artifacts specifically._

- [x] **P3.2-08** · `pdf_infiltrator` missing from `_A_TIER_SOURCES` in admiralty.py — signals
  sourced from SIU/NPA PDF portal (`source='pdf_infiltrator'`) were not recognised as A-tier
  by `check_nexus.py`, causing 0 results on the default A-tier run despite all 7 nexus signals
  having `source=pdf_infiltrator`.
  **RESOLVED:** Added `"pdf_infiltrator": "A"` to `_DOMAIN_SOURCE_GRADES` in
  `forage/utils/admiralty.py`. Default A-tier nexus run now returns 7 signals.
  _Resolved: `forage/utils/admiralty.py`_

- [x] **P3.2-09** · `triple_extractor` content-length gate excluded PDF signals with short
  `signals.content` but populated `artifacts.raw_text_cache`. The `WHERE length(trim(content)) > 50`
  filter eliminated valid PDF signals (e.g. PAIA Manual, NPA newsletter PDFs) whose `content`
  field is a URL slug (~25 chars) while the actual text lives in the artifact cache. This blocked
  the PAIA Manual (containing all 5 Aspirant Prosecutor names) from ever being processed.
  **RESOLVED:** Added OR branch: `OR (source='pdf_infiltrator' AND raw_text_cache IS NOT NULL
  AND length(raw_text_cache) > 50)`. Candidate signals increased from 633 → 659; 10 new triples
  written (152 total).
  _Resolved: `forage/processors/triple_extractor.py`_

- [x] **P3.2-07** · NASA FIRMS satellite noise in entity_relationships — thermal anomaly feed
  headers (`MW`=Megawatt, `FRP`=Fire Radiative Power) and geographic constants (`Alaska`,
  `Myanmar`, `actor2`, wind compass bearings) were being tagged as ORG/PERSON by spaCy and
  committed as actors. Caused 223,517+ noise entries in `signal_entities` and polluted
  `entity_relationships` with 16 nonsense triples.
  **RESOLVED Phase 3.2 (Satellite Purge):** Extended `_GENERIC_SUBJECTS` frozenset in
  `triple_extractor.py` with `mw`, `frp`, `alaska`, `myanmar`, `actor2`, `firms`, `viirs`,
  `modis`, `acq`, `satellite`, `confidence` + compass bearings. 16 pre-guardrail noise rows
  deleted. Re-run confirmed 0 new satellite triples. `signal_entities` reduced to 11,697 rows.
  _Resolved: `forage/processors/triple_extractor.py`_

---

## Phase 62–70 Substrate & Architecture Debt (Phase 70 Reconciliation)

Items introduced or resolved during the Substrate Transition, Reconstruction, and Regional Infiltration phases (62–70). Full details: `docs/SUBSTRATE_AUDIT_REPORT_APRIL_2026.md`.

### Resolved in Phases 62–70

| ID | Title | Closed | Notes |
|---|---|:---:|---|
| TD-01 | FK enforcement absent (all connections) | **68** | `core/db/connection.py` monkey-patch — `PRAGMA foreign_keys = ON` on every connection. |
| TD-02 | Silent DB creation on bad path | **68** | URI `mode=rw` format — refuses to create DB on bad path. |
| TD-03 | 241k orphan rows in `correlated_incidents` | **68** | Pre-FK stress-test artifacts. Deleted; FK check returns 0 violations. |
| TD-04 | Hub poisoning in graph traversal | **67** | Deleted noise company `[877]` (degree=90) via `actor_maintenance.py`. |
| TD-05 | ANC actor duplication (`[63]` vs `[43]`) | **67** | Merged via `merge_actors()`. All links migrated. |
| TD-06 | Observer quality gate — acronym rejection | **68** | `KNOWN_ACRONYMS` whitelist bypassing Gate 1 and Gate 4. |
| TD-07 | Observer fuzzy dedup absent | **68** | 3-tier dedup: exact → acronym expansion → 75% substring ratio. |
| TD-08 | `observer_promotion_log` column name wrong | **68** | `promoted_actor_id` → `actor_id`. Fixed in `actor_maintenance.py`. |
| TD-09 | `_score_severity()` not auditable | **68** | Replaced with `score_severity_detailed()` + `conclave_meta` audit trail. |
| TD-10 | URI `_foreign_keys` param silently ignored | **68** | Python `sqlite3` doesn't honour URI FK param. Fixed by monkey-patch. |
| TD-11 | DFFE scraper false positives | **69** | `require_primary=True` guard on DFFE archive in `bridge_hunt.py`. |
| TD-12 | `event_actors` pre-FK orphan rows (127) | **71** | Surgical strike: Strike 1 deleted 126 event_id orphans; actor_id=877 row absorbed in Strike 1 (also had large-int event_id). All quarantined to `orphaned_event_actors` before deletion. Zero-trust re-check: 0 orphans across all 8 FK checks. Verdict upgraded AMBER → FIELD READY. |

### Open — Phases 62–70

| ID | Title | Priority | Status | Remediation |
|---|---|:---:|---|---|
| ~~TD-12~~ | ~~`event_actors` pre-FK orphan rows (127)~~ | ~~🟡 P0~~ | ✅ **RESOLVED Phase 71** | Surgical strike complete. See Resolved section above. |
| TD-13 | Case Alpha institutional bridge gap (CoE=0.28) | 🟡 P0 | OPEN | SAFLII bridge hunt (Case No. NG13). NER triple extraction for `PROSECUTED_BY`/`ARRESTED_BY` edges. |
| TD-14 | Oxpeckers.org WAF-blocked | 🟡 P1 | OPEN | Manual PDF/DOCX download of 3–5 Trackers articles → `process_artifact()`. |
| TD-15 | `dbstat` VTAB absent | 🔵 P3 | DEFERRED | Recompile SQLite with `ENABLE_DBSTAT_VTAB`. Low urgency. |
| TD-16 | `network_emergence` table empty | 🔵 P2 | DEFERRED | Pipe `--metrics` output to INSERT; schedule via daemon. |
| TD-17 | `priorities`, `provenance`, `relationships` unpopulated | 🔵 P3 | DEFERRED | Evaluate for deprecation in Phase 71. |
| TD-18 | SABC Groenewald name collision | 🔵 P3 | DEFERRED | Require `"rhino" OR "hunting"` co-occurrence with Groenewald hits on SABC. |
| TD-19 | NER misparsed `"Dawie Groenewald's Botswana"` | 🔵 P2 | DEFERRED | Configure NER boundary rules for possessive constructions. |
| TD-20 | `graph_nodes` (463k) vs `actors` (1,011) imbalance | 🔵 P2 | DEFERRED | Audit `graph_nodes` provenance; truncate stale rows. |
| TD-21 | `signal_entities` orphan check missing from SOP | 🔵 P3 | DEFERRED | Add `signal_entities.signal_id -> signals` to §2.4 orphan checks. |

---

## Debt Summary Table

| ID    | Area                      | Severity | Status  | Effort        |
|-------|---------------------------|----------|---------|---------------|
| SEC-01 | XSS / signal sanitization | HIGH     | ✅ DONE | forge_security |
| SEC-02 | PDF malicious payload      | HIGH     | ✅ DONE | forge_security |
| SEC-03 | Quarantine / audit trail   | HIGH     | ✅ DONE | forge_security |
| SEC-04 | Unsafe subprocess / AMSI   | HIGH     | ✅ DONE | forge_security |
| SEC-05 | Supply-chain CVE visibility | MEDIUM  | ✅ DONE | forge_security |
| P0-01 | Schema alignment (title→name) | HIGH  | ✅ DONE   | Phase 3.1 |
| P0-02 | google.generativeai deprecation | MEDIUM | ⬜ PENDING | 1–2 h  |
| P1-01 | SIU no-intel re-download    | HIGH    | ⬜ PENDING | 1–2 h     |
| P1-02 | DB conn per file in migrate | LOW     | ⬜ PENDING | 30 min    |
| P1-03 | Full PDF in RAM             | MEDIUM  | ⬜ PENDING | 2 h       |
| P1-04 | gc.collect on every PDF     | LOW     | ⬜ PENDING | 15 min    |
| P1-05 | pdf_infiltrator not in mega_ingest | MEDIUM | ⬜ PENDING | 1 h  |
| P1-06 | NULL gravity_score (7,418)  | MEDIUM  | ⬜ PENDING | 1 h       |
| P1-07 | importlib pipeline_logger   | LOW     | ⬜ PENDING | 30 min    |
| P1-08 | file_path not set on portals | HIGH   | ⬜ PENDING | 1–2 h     |
| P2-01 | Amount regex misses formats | HIGH    | ⬜ PENDING | 1 h       |
| P2-02 | Awardee regex gaps          | MEDIUM  | ⬜ PENDING | 30 min    |
| P2-03 | _DEPT_PATTERN dead code     | MEDIUM  | ⬜ PENDING | 30 min    |
| P2-04 | No OCR for scanned PDFs     | HIGH    | 🔶 PARTIAL | 6,458 identified |
| P2-05 | entity_resolver table scan  | MEDIUM  | ⬜ PENDING | 1 h       |
| P2-06 | spaCy not tuned for SA govt | HIGH    | 🔶 PARTIAL | Phase 3.2 |
| P2-07 | Amount unit metadata lost   | LOW     | ⬜ PENDING | 1 h       |
| P2-08 | No confidence scoring       | LOW     | ⬜ PENDING | 2 h       |
| P2-09 | NPA legislation irrelevant PDFs | LOW | ⬜ PENDING | 15 min    |
| P2-10 | source_artifact_id always NULL | MEDIUM | ⬜ PENDING | 1 h     |
| P3-01 | DB_PATH hardcoded           | LOW     | ⬜ PENDING | 15 min    |
| P3-02 | Single UA string            | MEDIUM  | ⬜ PENDING | 1 h       |
| P3-03 | dork_collector bot UA       | MEDIUM  | ⬜ PENDING | 15 min    |
| P3-04 | No retry on transient fail  | MEDIUM  | ⬜ PENDING | 1 h       |
| P3-05 | AGSA JS-wall (0 PDFs found) | HIGH    | ⬜ PENDING | 1–2 h     |
| P3-06 | NT_restricted redundant     | LOW     | ⬜ PENDING | 5 min     |
| P3-07 | SIU sitemap fully serial    | MEDIUM  | ⬜ PENDING | 3–4 h     |
| P3-08 | No PDF signal→case linkage  | MEDIUM  | ⬜ PENDING | 2 h       |
| P3-09 | 548k artifacts stuck pending | CRITICAL | ✅ DONE   | Phase 3.1/3.2 |
| P3-10 | 150k signals NULL cluster_id | CRITICAL | ⬜ PENDING | Investigate |
| P3.2-01 | artifact_processor 500-row batch cap (original entry) | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-02 | triple_extractor not chained after processor | HIGH | ⬜ PENDING | 30 min |
| P3.2-03 | Generic actor noise in entity_relationships | MEDIUM | ⬜ PENDING | 15 min |
| P3.2-04 | Self-referential NPA triple (actor dedup) | MEDIUM | ⬜ PENDING | 1–2 h |
| P3.2-05 | 6,458 scanned PDF OCR candidates | HIGH | ⬜ PENDING | 1 h |
| P3.2-06 | Actor-promotion gap (Aspirant Prosecutors) | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-07 | NASA FIRMS satellite noise (MW/FRP/Alaska) | MEDIUM | ✅ DONE | Phase 3.2 |
| P3.2-08 | pdf_infiltrator missing from A-TIER_SOURCES | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-09 | triple_extractor content-length gate excluded PDF signals | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-10 | _get_or_create_signal sets relevance=1.0 (DB default, fails >1.0 gate) | HIGH | ⬜ PENDING | 30 min |
| P3.2-11 | artifact cache branch restricted to pdf_infiltrator source | MEDIUM | ✅ DONE | Phase 3.2 |
| P3.2-12 | Aspirant Prosecutor triples 0 (no relational prose in corpus) | HIGH | ⬜ PENDING | Requires corpus expansion |
| P3.2-01 | artifact_processor 500-row batch cap | HIGH | ✅ DONE | Phase 3.2 |
| TD-01 | FK enforcement absent (all connections) | CRITICAL | ✅ DONE | Phase 68 |
| TD-02 | Silent DB creation on bad path | HIGH | ✅ DONE | Phase 68 |
| TD-03 | 241k orphans in correlated_incidents | HIGH | ✅ DONE | Phase 68 |
| TD-04 | Hub poisoning — actor [877] | HIGH | ✅ DONE | Phase 67 |
| TD-05 | ANC actor duplication [63]/[43] | MEDIUM | ✅ DONE | Phase 67 |
| TD-06 | Observer acronym rejection | MEDIUM | ✅ DONE | Phase 68 |
| TD-07 | Observer fuzzy dedup absent | MEDIUM | ✅ DONE | Phase 68 |
| TD-08 | observer_promotion_log wrong column | LOW | ✅ DONE | Phase 68 |
| TD-09 | score_severity not auditable | MEDIUM | ✅ DONE | Phase 68 |
| TD-10 | URI _foreign_keys param ignored | HIGH | ✅ DONE | Phase 68 |
| TD-11 | DFFE scraper false positives | MEDIUM | ✅ DONE | Phase 69 |
| TD-12 | event_actors pre-FK orphans (127) | HIGH | ✅ DONE | Phase 71 — surgical strike, 0 orphans |
| TD-13 | Case Alpha institutional bridge gap | HIGH | ⬜ PENDING | SAFLII + NER triple wiring |
| TD-14 | Oxpeckers WAF-blocked | MEDIUM | ⬜ PENDING | Manual PDF ingest |
| TD-15 | dbstat VTAB absent | LOW | 🔵 DEFERRED | Recompile SQLite |
| TD-16 | network_emergence table empty | LOW | 🔵 DEFERRED | Persist metrics |
| TD-17 | Empty legacy tables | LOW | 🔵 DEFERRED | Deprecation review |
| TD-18 | SABC Groenewald name collision | LOW | 🔵 DEFERRED | Disambiguation filter |
| TD-19 | NER possessive entity misparsing | MEDIUM | 🔵 DEFERRED | NER boundary config |
| TD-20 | graph_nodes vs actors imbalance | MEDIUM | 🔵 DEFERRED | Provenance audit |
| TD-21 | signal_entities orphan check missing from SOP | LOW | 🔵 DEFERRED | SOP §2.4 update |

---

*Update this file when debt is resolved or newly discovered. Use `- [x]` for resolved items.*
*Substrate audit report: `docs/SUBSTRATE_AUDIT_REPORT_APRIL_2026.md`*
*SOP: `docs/SUBSTRATE_INTERROGATION_SOP.md`*
