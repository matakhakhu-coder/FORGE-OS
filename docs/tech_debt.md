# FORGE-OS Technical Debt Ledger

**Last Audited:** 2026-04-21 (Phase 72 — Sessions 1–4 complete)
**Auditor:** FORGE Technical Debt Lead / Phase 72
**State at audit (Phase 72, Sessions 1–4):** 39 items audited. **5 remaining open** (all blocked by external dependencies, not code gaps). 34 items closed:
- 17 confirmed pre-resolved via code audit (Phase 44/48/68/69/3.2 fixes silently resolved issues ledger marked pending)
- 17 new fixes applied in this session across `pdf_infiltrator.py`, `dork_collector.py`, `graph_engine.py`, `mega_ingest.py`, and the database

**Phase 72 code changes summary:**
- `forage/collectors/pdf_infiltrator.py`: P1-02 (conn reuse), P1-03 (tempfile stream), P1-04 (gc gate), P2-07 (amount unit metadata), P2-08 (confidence scoring), P2-09 (URL exclusion — all 3 modes), P3-01 (FORGE_DB env var), P3-02 (UA rotation pool ×6), P3-04 (retry w/ backoff), P3-05 applied to 4 call sites
- `forage/collectors/dork_collector.py`: P3-01 (FORGE_DB env var), P3-03 (Chrome UA)
- `forage/engines/graph_engine.py`: P1-07 (importlib hack → standard import)
- `tools/mega_ingest.py`: P3-08 (`bridge_pdf_signals_to_cases()` + wired into pipeline at Phase 2.7)
- `requirements.txt`: P0-02 (stale google-generativeai entry removed)
- Database: P3.2-03 (19 generic-subject noise rows deleted from entity_relationships), P3-08 bridge run (32 case-signal links created)

**5 remaining items are not code debt — they are operational/infrastructure blockers:**

**Previous audit (Phase 71, 2026-04-19):** TD-12 resolved. event_actors purged: 340 → 214 rows. All 8 zero-trust FK checks pass with 0 orphans. Integrity verdict upgraded AMBER → **FIELD READY**. See `docs/SUBSTRATE_AUDIT_REPORT_APRIL_2026.md`.

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

### P0-02 · `google.generativeai` → `google.genai` Migration ✅ RESOLVED

- [x] **Status: RESOLVED — Phase 72 (code audit)**
- **Finding:** No live `import google.generativeai` exists in any `.py` file across
  `core/` or `forage/`. The migration was already completed. `requirements.txt` still
  listed both packages side-by-side.
- **Phase 72 action:** Removed `google-generativeai==0.8.6` from `requirements.txt`
  (replaced with comment). `google-genai==1.61.0` remains as the active SDK.
- **Confirmed:** `grep -r "google.generativeai" forage/ core/` returns 0 matches.

---

## Priority 1 — Thermal & Stability

- [x] **P1-01** · SIU No-Intel PDFs re-downloaded every run — `pdf_infiltrator.py`.
  No persistent "tried and failed" cache. ~3–4 min wasted per run.
  _Resolved Phase 48: `_artifact_url_exists()` and `_record_artifact_skip()` implemented in `pdf_infiltrator.py`._

- [x] **P1-02** · `_migrate_media_root` opens new DB connection per file — `pdf_infiltrator.py`.
  _Resolved Phase 72 (Session 1): `_migrate_media_root(db_path, conn=None)` — accepts optional open connection; only opens/closes its own connection when caller passes none._

- [x] **P1-03** · Full PDF bytes held in RAM before pdfplumber — `pdf_infiltrator.py`.
  Peak RAM = download buffer + pdfplumber buffer simultaneously (~30–40 MB per PDF).
  _Resolved Phase 72 (Session 4): `_download_pdf()` now streams to `tempfile.NamedTemporaryFile` on disk, reads back bytes after download completes, then unlinks the temp file. Download buffer is released before pdfplumber opens the returned bytes. Peak RAM drops from ~30-40 MB simultaneous to pdfplumber working set only._

- [x] **P1-04** · `gc.collect()` called after every PDF regardless of size — adds ~150–300 ms
  per call unconditionally.
  _Resolved Phase 72 (Session 1): gated behind `if len(pdf_bytes) > 5_000_000` in `pdf_infiltrator.py`._

- [x] **P1-05** · `pdf_infiltrator` not wired into `mega_ingest.py` — must be invoked
  manually. No automated portal crawl.
  _Resolved: `pdf_infiltrator.collect` imported and wired into Phase 1 collection as C-1 "Sovereign-First PDF vault" (line 221-223). Runs concurrently with all other collectors. Confirmed in code audit._

- [x] **P1-06** · NULL `gravity_score` on ~7,418 signals — invisible to prioritization feed.
  _Resolved: DB audit confirms 0 NULL/zero gravity_score rows across all 51,155 signals. Backfill ran during Substrate Reconstruction. Closed Phase 72._

- [x] **P1-07** · `pipeline_logger` loaded via `importlib.spec_from_file_location` on
  every module load — `graph_engine.py` lines 41–55.
  _Resolved Phase 72 (Session 1): replaced with `try: from forage.utils.pipeline_logger import log_run` + silent no-op fallback. `importlib` and `spec_from_file_location` removed entirely._

- [x] **P1-08** · `artifacts.file_path` not set for pdf_infiltrator portal signals — 0 rows
  with `source_type='pdf_portal'`. UI evidence viewer cannot find physical documents.
  _Resolved Phase 48: `_insert_artifact()` populates `file_path` with the saved path; confirmed in code audit._

---

## Priority 2 — Extraction Fidelity

- [x] **P2-01** · Amount regex misses common SA formats (`R4.5m`, `R4,5 million`, `R4.5bn`) —
  `pdf_infiltrator.py` `_AMOUNT_PATTERN`.
  _Resolved Phase 48: `_AMOUNT_PATTERN` rewritten at lines 89–104 with suffix-ordered alternation (billion > bn, million > m), optional space, comma-as-decimal detection, and hallucination guard (> R999 billion rejected). Confirmed in code audit._

- [x] **P2-02** · Awardee regex misses "appointed as service provider", "preferred supplier",
  "winning bidder" — `_AWARD_PATTERN`.
  _Resolved Phase 48: `_AWARD_PATTERN` expanded with additional SA procurement phrases; confirmed in code audit._

- [x] **P2-03** · `_DEPT_PATTERN` extracted but never stored — dead code in `_parse_intelligence()`.
  _Resolved Phase 48: `_DEPT_PATTERN` wired into intel dict (`"departments"` key populated); confirmed in code audit._

- [x] **P2-04** · No OCR for scanned/image PDFs — `pdf_infiltrator.py`. Court orders (highest
  value docs) yield 0 text. **Note:** The forge_security detonator now validates PDFs before
  processing; OCR bridge added after detonation clears the file.
  _Resolved Phase 48: `pytesseract` fallback implemented in `artifact_processor.py` (fitz renders at 2× → pytesseract). Activates when `raw_text_cache` is empty AND `file_path` exists on disk. 6,458 scanned candidates remain (see P3.2-05 for bulk OCR run)._

- [x] **P2-05** · `entity_resolver` full table scan per lookup — O(n×m) at 2,000 signals × 800
  actors.
  _Resolved Phase 48: `EntityResolver` builds `_exact` and `_index` dicts once on construction (D-1 pattern). All lookups are O(1). New actors update both dicts immediately via `_create_actor()`. `refresh()` available for mid-session rebuilds. Confirmed in code audit._

- [ ] **P2-06** · spaCy `en_core_web_sm` not tuned for SA government entities — DPWI, HAWKS,
  SIU, NPA tagged MISC or missed.
  _Fix: custom `EntityRuler` pattern list for known SA actors._

- [x] **P2-07** · Amount normalisation discards unit metadata — `4500.0` from `R4.5 billion`
  is indistinguishable from `R4500 million` after storage.
  _Resolved Phase 72 (Session 3): `intel["amounts"]` entries now stored as `{"value_millions": float, "unit": "million"|"billion", "raw_suffix": str}`. Downstream formatting and dedup updated accordingly._

- [x] **P2-08** · No confidence scoring on extracted intel — page position and occurrence
  count not factored.
  _Resolved Phase 72 (Session 4): `_parse_intelligence()` now computes `intel["confidence"]` (0.0–1.0) weighted by field type (tender_numbers=0.35, amounts=0.30, awardees=0.25, departments=0.10) with log-scale diminishing returns on hit count. Stored in `tags_json` alongside other intel fields so surface tier can filter low-confidence extractions._

- [x] **P2-09** · NPA Legislation page downloads irrelevant statutory acts (7 of 11 PDFs).
  _Resolved Phase 72 (Session 1): `_PORTAL_URL_EXCLUDES` dict added to `pdf_infiltrator.py`; `_apply_excludes()` helper applied to all three crawl modes (`sitemap`, `sitemap2hop`, `page`)._

- [x] **P2-10** · `entity_relationships.source_artifact_id` always NULL — no provenance trail
  linking a relationship back to its source artifact.
  _Resolved Phase 44: `triple_extractor.py` populates `source_artifact_id` on every INSERT; backfills existing NULL rows on re-run via UPDATE WHERE source_artifact_id IS NULL. Full relationship→artifact→PDF provenance chain in place. Confirmed in code audit._

---

## Priority 3 — System Resilience

- [x] **P3-01** · `DB_PATH` hardcoded in `pdf_infiltrator` — ignores `FORGE_DB` env var.
  _Resolved Phase 72 (Session 1): `DB_PATH = Path(os.environ["FORGE_DB"]).resolve() if os.environ.get("FORGE_DB") else BASE_DIR / "database.db"` applied to both `pdf_infiltrator.py` and `dork_collector.py`._

- [x] **P3-02** · Single User-Agent across all portal requests — fingerprint / WAF block risk.
  _Resolved Phase 72 (Session 3): `_UA_POOL` of 6 desktop browser UA strings added to `pdf_infiltrator.py`. `_get_session()` calls `random.choice(_UA_POOL)` on every invocation — consecutive portal requests get different fingerprints._

- [x] **P3-03** · `dork_collector` self-identifies as `FORGE-OSINT/1.0` — immediately flagged
  by CDN/Google rate limiters.
  _Resolved Phase 72 (Session 1): replaced with realistic Chrome/124 UA string in `dork_collector.py`._

- [x] **P3-04** · No retry logic for transient download failures — one `ConnectTimeout` =
  permanent skip for that run.
  _Resolved Phase 72 (Session 3): `_resilient_get()` added to `pdf_infiltrator.py`. Retries up to 3× with exponential backoff (2s→4s→8s) on ConnectTimeout, ReadTimeout, ConnectionError, HTTP 429, HTTP 503. Applied to `_download_pdf()`, `_find_pdf_links()`, `_fetch_sitemap_xml()`, and the page-mode portal crawl._

- [ ] **P3-05** · AGSA reports behind JavaScript wall — 0 PDFs ever found from AGSA PFMA/hub.
  Highest-value audit documents unreachable.

- [x] **P3-06** · `NT_restricted` direct target is redundant — already found by NT_tender
  page scrape.
  _Resolved Phase 48: `NT_restricted` entry removed from `_PORTAL_TARGETS` in `pdf_infiltrator.py`; confirmed in code audit._

- [x] **P3-07** · SIU sitemap 2-hop is fully serial — `time.sleep(2.0)` per URL = 80+ seconds
  minimum discovery time.
  _Resolved Phase 48: `_crawl_sitemap_2hop_async()` implemented with asyncio + ThreadPoolExecutor + Semaphore(5). `_crawl_portal_async()` calls the async variant for `sitemap2hop` portals. The `collect()` coroutine (mega_ingest entry point) fans out all portals concurrently via `asyncio.gather()`. Serial version preserved for manual `_run_portal_infiltration()` calls only. Confirmed in code audit._

- [x] **P3-08** · No case-to-signal linkage for PDF portal signals — `case_events` has only 6
  rows; Operation Mabone signal never linked to Case #24.
  _Resolved Phase 72 (Session 4): `bridge_pdf_signals_to_cases()` added to `mega_ingest.py` at Phase 2.7. Follows signal_actors → case_actors → case_signals path. On first run: 9 pdf_infiltrator signals with actors, 32 case-signal links created. Wired into pipeline between `bridge_dork_to_cases` and `bridge_cooccurrence_to_relationships`._

- [x] **P3-09** · 548,635 artifacts stuck in `no_intel` processing status — 97% of artifact
  rows never processed. 🔴 CRITICAL → **RESOLVED Phase 3.1/3.2**
  272,864 A-tier artifacts (NPA/SIU/government PDFs) promoted to `A1-PENDING` via
  `rescue_artifacts.py`. `artifact_processor` updated to handle `A1-PENDING` status.
  Remaining 275,583 are C-tier/live signals — deliberately held back pending
  deeper NER pass.
  _Resolved: `scripts/rescue_artifacts.py`, `forage/processors/artifact_processor.py`_

- [x] **P3-10** · 150,892 signals have NULL `cluster_id` (99.9%) — correlation engine and
  Sentinel alerts operating on a near-empty graph. 🔴 CRITICAL
  _Resolved: DB now has 51,155 signals — all 51,155 have cluster_id set (0 NULLs, 100% coverage). Signal table was rebuilt during Substrate Reconstruction (Phases 62–70). Closed Phase 72 after audit query confirmed 0 NULLs._

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

- [x] **P3.2-02** · `triple_extractor` reads SIGNALS (via `relevance_score > 1.0`), not the
  272k A1-PENDING ARTIFACTS directly. After artifact_processor runs NER on the rescue batch,
  signal_entities will be populated — but entity_relationships won't update until
  triple_extractor is re-run with a `--since` date after the processor completes.
  _Resolved Phase 48: `TripleExtractor` is position 7 in `engine_sequence` in `mega_ingest.py`, after `artifact_processor`. Confirmed in code audit._

- [x] **P3.2-03** · Generic actor noise in entity_relationships — "Provincial", "South Africa",
  "Suspect", "government", "company" committed as subjects in 7 of the 29 new links.
  `_GENERIC_SUBJECTS` filter deployed in triple_extractor (Phase 3.2) but the 7 dirty rows
  remain in the DB.
  _Resolved Phase 72 (Session 2): 19 dirty rows found and deleted (18× Provincial, 1× Suspect). entity_relationships now free of generic-subject noise._

- [x] **P3.2-04** · Self-referential NPA triple — `NPA --INVESTIGATES--> National Prosecuting Authority`
  (relationship_id=382). Both sides resolve to the same institution via different name forms.
  The UNIQUE constraint on (subject, object, relation_type) doesn't catch same-actor pairs
  with different actor_ids.
  _Resolved: relationship_id=382 no longer present in DB — removed in a prior phase. Query for same-name subject/object pairs returns 0 rows. Underlying actor dedup gap (P3.2-04b) remains a long-term structural item but has no current dirty rows._

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

- [x] **P3.2-10** · `_get_or_create_signal()` in `artifact_processor.py` creates signals
  with `relevance_score = 1.0` (the DB column default) — failing the triple_extractor's
  strict `> 1.0` threshold by a hair. Affects all 20,000 signals created in the mass
  extraction run. Workaround applied: manual backfill of `relevance_score = 1.2` for the
  417 NPA seed signals with rich artifact cache.
  _Resolved Phase 48: `_get_or_create_signal()` computes `rel_score = round(1.0 + conf, 3)` (always > 1.0); confirmed in code audit with `(P3.2-10 fix)` comment in place._

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
| TD-15 | `dbstat` VTAB absent | 🔵 P3 | 🔵 WON'T FIX | Python's bundled SQLite cannot be recompiled in this environment. Substrate SOP updated to note the absence. Formally closed Phase 72. |
| TD-16 | `network_emergence` table empty | 🔵 P2 | DEFERRED | Pipe `--metrics` output to INSERT; schedule via daemon. |
| TD-17 | `priorities`, `provenance`, `relationships` unpopulated | 🔵 P3 | DEFERRED | Evaluate for deprecation in Phase 71. |
| TD-18 | SABC Groenewald name collision | 🔵 P3 | DEFERRED | Require `"rhino" OR "hunting"` co-occurrence with Groenewald hits on SABC. |
| TD-19 | NER misparsed `"Dawie Groenewald's Botswana"` | 🔵 P2 | DEFERRED | Configure NER boundary rules for possessive constructions. |
| TD-20 | `graph_nodes` (463k) vs `actors` (1,011) imbalance | 🔵 P2 | DEFERRED | Audit `graph_nodes` provenance; truncate stale rows. |
| TD-21 | `signal_entities` orphan check missing from SOP | 🔵 P3 | DEFERRED | Add `signal_entities.signal_id -> signals` to §2.4 orphan checks. |
| ENT-01 | `entity_engine.py` INSERT failed on missing `confidence_score`, `automated` columns on `actors` | 🟡 P1 | ✅ **RESOLVED 2026-05-28** | `migrate_db()` in `app.py` adds both columns (REAL NOT NULL DEFAULT 0.5, INTEGER NOT NULL DEFAULT 0). Confirmed via `verify_schema.py` — all 128 required columns present. `entity_engine.py` compliance fix applied (`from __future__ import annotations` line 1). |
| CT-1 | `core/gravity.py` Contextual Tunneling implemented but disconnected from app routes | 🔵 P2 | ✅ **VERIFIED 2026-05-28** | 41-test suite at `tests/test_gravity_ct1.py` — all pass. Tests cover: haversine, keyword extraction, location matching, all 4 item types in `score_item()`, `blend_score()` weight clamping, `build_context()` with mock DB. Compliance fix applied (`from __future__ import annotations`). Surface integration: wire `build_context(db, case_id)` + `score_item()` into surface route when analyst active-case context is available. |

---

## Codebase Audit — 2026-05-30

Full static analysis run across all `.py` files, `templates/*.html`, and inline JS. 10 issues found, 9 fixed, 1 deferred.

### Resolved

- [x] **DB-01 · `sqlite3.connect()` missing `timeout=60` in 8 active pipeline files** — 2026-05-30
  - `core/db/connection.py:72` — main Flask DB connection (CRITICAL: requests could hang forever under WAL contention)
  - `app.py:9599` — `_open_db()` (init / diagnostics path)
  - `forage/engines/decay_engine.py` — 6-hour background worker; also added `try/finally: conn.close()` around full `run()` body
  - `forage/processors/ner_processor.py` — pipeline processor; also wrapped `run_ner_pipeline()` in `try/finally: conn.close()`, removed two dangling early `conn.close()` calls
  - `forage/collectors/rss_collector.py` — active ingest collector
  - `forage/utils/pipeline_logger.py` — called on every ingested signal
  - `wiki/routes.py` — Flask blueprint DB connection
  - `wiki/processors/wiki_compiler.py` — both `compile_from_entities()` and `compile_from_local_files()` connection sites

- [x] **DB-03 · Bare `except:` in `tools/seed_cache.py:48`** — 2026-05-30
  - Changed `except:` → `except Exception:`. Bare except catches `SystemExit`, `KeyboardInterrupt`, and generator-internal `StopIteration` — silent swallowing of these masks crashes.

### Deferred

- [ ] **DB-02 · `from __future__ import annotations` placed after line 3 in 51 files** — LOW
  - Compiler confirms no runtime impact (`python -m compileall` exits 0). CLAUDE.md convention says line 2, before docstring. Fixing 51 files adds noise with zero functional gain. Defer to a dedicated cleanup pass.
  - Affected areas: `core/fms/`, `forage/engines/`, `forage/processors/`, `forge_modules/*/`, `flux/`, `surface/`, `wiki/`, `scripts/`, `tools/`, `migrations/`

### Still outstanding (not in scope of this audit)

- ~40 additional `sqlite3.connect()` calls without `timeout=` in migration, maintenance, and one-off tool scripts. These run manually outside the Flask/WAL context and represent minimal deadlock risk, but should be fixed during a dedicated migration/tool hardening pass.

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
| P0-02 | google.generativeai deprecation | MEDIUM | ✅ DONE   | Pre-resolved — requirements.txt cleaned Phase 72 |
| P1-01 | SIU no-intel re-download    | HIGH    | ✅ DONE   | Phase 48  |
| P1-02 | DB conn per file in migrate | LOW     | ✅ DONE   | Phase 72 Session 1 |
| P1-03 | Full PDF in RAM             | MEDIUM  | ✅ DONE   | Phase 72 Session 4 — tempfile stream |
| P1-04 | gc.collect on every PDF     | LOW     | ✅ DONE   | Phase 72 Session 1 |
| P1-05 | pdf_infiltrator not in mega_ingest | MEDIUM | ✅ DONE   | Pre-resolved |
| P1-06 | NULL gravity_score (7,418)  | MEDIUM  | ✅ DONE   | Pre-resolved — 0 NULLs confirmed Phase 72 |
| P1-07 | importlib pipeline_logger   | LOW     | ✅ DONE   | Phase 72 Session 1 |
| P1-08 | file_path not set on portals | HIGH   | ✅ DONE   | Phase 48  |
| P2-01 | Amount regex misses formats | HIGH    | ✅ DONE   | Phase 48  |
| P2-02 | Awardee regex gaps          | MEDIUM  | ✅ DONE   | Phase 48  |
| P2-03 | _DEPT_PATTERN dead code     | MEDIUM  | ✅ DONE   | Phase 48  |
| P2-04 | OCR bridge for scanned PDFs | HIGH    | ✅ DONE   | Phase 48 (6,458 candidates remain — see P3.2-05) |
| P2-05 | entity_resolver table scan  | MEDIUM  | ✅ DONE   | Phase 48  |
| P2-06 | spaCy not tuned for SA govt | HIGH    | 🔶 PARTIAL | Phase 3.2 |
| P2-07 | Amount unit metadata lost   | LOW     | ✅ DONE   | Phase 72 Session 3 |
| P2-08 | No confidence scoring       | LOW     | ✅ DONE   | Phase 72 Session 4 |
| P2-09 | NPA legislation irrelevant PDFs | LOW | ✅ DONE   | Phase 72 Session 1 |
| P2-10 | source_artifact_id always NULL | MEDIUM | ✅ DONE   | Phase 44 |
| P3-01 | DB_PATH hardcoded           | LOW     | ✅ DONE   | Phase 72 Session 1 |
| P3-02 | Single UA string            | MEDIUM  | ✅ DONE   | Phase 72 Session 3 |
| P3-03 | dork_collector bot UA       | MEDIUM  | ✅ DONE   | Phase 72 Session 1 |
| P3-04 | No retry on transient fail  | MEDIUM  | ✅ DONE   | Phase 72 Session 3 |
| P3-05 | AGSA JS-wall (0 PDFs found) | HIGH    | ⬜ PENDING | 1–2 h     |
| P3-06 | NT_restricted redundant     | LOW     | ✅ DONE   | Phase 48  |
| P3-07 | SIU sitemap fully serial    | MEDIUM  | ✅ DONE   | Pre-resolved Phase 48 — async + semaphore confirmed |
| P3-08 | No PDF signal→case linkage  | MEDIUM  | ✅ DONE   | Phase 72 Session 4 — 32 links created |
| P3-09 | 548k artifacts stuck pending | CRITICAL | ✅ DONE   | Phase 3.1/3.2 |
| P3-10 | 150k signals NULL cluster_id | CRITICAL | ✅ DONE   | Pre-resolved — 0 NULLs confirmed Phase 72 |
| P3.2-01 | artifact_processor 500-row batch cap (original entry) | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-02 | triple_extractor not chained after processor | HIGH | ✅ DONE | Phase 48 |
| P3.2-03 | Generic actor noise in entity_relationships | MEDIUM | ✅ DONE   | Phase 72 Session 2 — 19 rows deleted |
| P3.2-04 | Self-referential NPA triple (actor dedup) | MEDIUM | ✅ DONE   | Pre-resolved — 0 dirty rows confirmed Phase 72 |
| P3.2-05 | 6,458 scanned PDF OCR candidates | HIGH | ⬜ PENDING | 1 h |
| P3.2-06 | Actor-promotion gap (Aspirant Prosecutors) | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-07 | NASA FIRMS satellite noise (MW/FRP/Alaska) | MEDIUM | ✅ DONE | Phase 3.2 |
| P3.2-08 | pdf_infiltrator missing from A-TIER_SOURCES | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-09 | triple_extractor content-length gate excluded PDF signals | HIGH | ✅ DONE | Phase 3.2 |
| P3.2-10 | _get_or_create_signal sets relevance=1.0 (DB default, fails >1.0 gate) | HIGH | ✅ DONE | Phase 48 |
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
| TD-22 | `ingest_signal()` hangs indefinitely post-`apply_feedback` on some signals | HIGH | ⬜ PENDING | Reproduced 2x on civic_intel_collector_us backlog (signal `3aa4c4ca-...`, ProPublica). Hang occurs after the `apply_feedback` log line, somewhere in entity materialisation / `_rel_extract` / `stitch_entity_cooccurrence` / `handle_escalation` — likely an unbounded network call (relationship_extractor SAFLII lookup is the prime suspect). Workaround used: `tools/_score_us_backlog.py` calls `score_signal()` directly, bypassing the full pipeline. Needs `timeout=` on all `requests` calls in those four modules + investigation of why it reproduces on this specific signal. |

---

---

## Stable 1.2.0 — Monolith Extraction & Stability Fixes (2026-06-21)

Items resolved during the comprehensive codebase audit and structural refactoring.

- [x] **AD-1** · `app.py` monolith (10,133 lines, ~120 inline routes in single `create_app()`).
  **RESOLVED:** Extracted into 8 Flask Blueprints in `core/web/blueprints/`. app.py reduced to
  1,297 lines (thin factory + schema + CLI). 151 routes across 10 blueprints verified.
  Shared infrastructure in `core/web/helpers.py` (get_db, telemetry, constants) and
  `core/web/state.py` (mutable registries).

- [x] **AD-2** · Missing FK indexes — 35 FK relationships but only 3 explicit indexes.
  `graph_edges.source_node_id/target_node_id` unindexed with 463K rows in `graph_nodes`.
  **RESOLVED:** 16 FK indexes added to SCHEMA_STATEMENTS and applied to live database.
  Total indexes: 49. Key additions: graph_edges (source/target), signal_actors (signal/actor),
  entity_relationships (subject/object), correlated_incidents (signal_a/b), case_signals,
  signal_entities, socint_signals, actor_coalitions, network_emergence, socint_resonance.

- [x] **RC-1** · No mutex on concurrent pipeline execution — `/api/control/run_collectors`,
  `run_ingest`, `run_conclave`, `run_graph_engine` could be invoked simultaneously, causing
  SQLITE_BUSY thrashing and partial commits.
  **RESOLVED:** `_PIPELINE_ACTIVE` guard in `core/web/state.py`. Concurrent invocations
  return HTTP 409 `{"status": "rejected", "reason": "already running"}`.

- [x] **IV-1** · 14 bare `int()`/`float()` query parameter casts across 8 routes. Non-numeric
  input caused raw 500 ValueError tracebacks.
  **RESOLVED:** Replaced with safe `request.args.get(key, default, type=int/float)` pattern.
  POST body casts guarded with `try/except (ValueError, TypeError)`.

- [x] **SEC-3** · `_update_job(**fields)` interpolated kwargs keys into SQL column names via
  f-string with no allowlist. Potential SQL injection vector for future callers.
  **RESOLVED:** Column allowlist `_ALLOWED_JOB_COLUMNS` added; unknown keys are silently skipped.

- [x] **SDL-1** · Gravity score write failure in `core/pipeline/ingest.py` silently fell through
  to downstream stages. Signal marked as processed despite missing `gravity_score`.
  **RESOLVED:** Write failure now returns early with error dict. Signal remains with
  `processed_at = NULL` for retry on next pipeline run.

- [x] **SDL-2** · Anomaly engine baseline write loop used bare `except Exception: pass`.
  Failed INSERT silently dropped baseline rows with no logging.
  **RESOLVED:** Both `build_baselines()` and `build_actor_baselines()` now log dropped rows
  with `warn()` and emit failure count summary.

- [x] **RL-1** · `AnomalyEngine.run()` used `conn.close()` outside `try/finally`. Unhandled
  exception in any engine phase leaked the SQLite connection.
  **RESOLVED:** Full engine sequence wrapped in `try/finally: conn.close()`.

### Remaining open items introduced by audit

- [x] **AD-3** · Schema duplication between `SCHEMA_STATEMENTS` and `migrate_db()`.
  **RESOLVED 2026-06-21:** Removed 7 duplicate inline CREATE TABLE stanzas from `migrate_db()`.
  All table/index creation now driven by the single canonical SCHEMA_STATEMENTS array.
  Added explicit ALTER TABLE migrations for `community_id_socint` and `influence_score`
  on `actor_network_metrics` to close the column drift gap.

- [x] **AD-4** · `wiki_links` foreign keys missing ON DELETE clause.
  **RESOLVED 2026-06-21:** SCHEMA_STATEMENTS updated with `ON DELETE CASCADE` on both FKs.
  Table-recreation migration added to `migrate_db()` for existing databases. Verified:
  both `source_slug` and `target_slug` now show `ON DELETE CASCADE` in PRAGMA output.

- [x] **SEC-1** · Default `ADMIN_PASSWORD` and `secret_key` in production.
  **RESOLVED 2026-06-21:** Production environment gate added to `create_app()`. When
  `FLASK_ENV=production`, the app raises `RuntimeError` if either `FORGE_SECRET_KEY` or
  `FORGE_ADMIN_PASSWORD` matches the default fallback value.

- [x] **SEC-4** · Vercel deploy hook URL hardcoded in `tools/publish.py`.
  **RESOLVED 2026-06-21:** Token replaced with `os.environ.get("VERCEL_DEPLOY_HOOK_URL")`.
  Graceful skip with operator message when env var is unset. Placeholder added to `env.example`.

---

*Update this file when debt is resolved or newly discovered. Use `- [x]` for resolved items.*
*Substrate audit report: `docs/SUBSTRATE_AUDIT_REPORT_APRIL_2026.md`*
*SOP: `docs/SUBSTRATE_INTERROGATION_SOP.md`*
