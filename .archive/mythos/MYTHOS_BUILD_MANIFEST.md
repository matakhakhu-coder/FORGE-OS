# MYTHOS_BUILD_MANIFEST.md — Mythos Anthology Absolute Source of Truth

> Mythology-focused media project · Atomic Node Network · Recursive Rebuild Architecture  
> Document Authority: This manifest supersedes all in-code comments regarding schema, node contracts, and rebuild chain rules.  
> FORGE Integration: Installed as FMS module `mythos` — lives in `forge_modules/mythos/` + `mythos/`  
> Last Updated: 2026-05-28

---

## Phase Status Matrix

| Phase | Name | Scope | Status |
|-------|------|-------|--------|
| **Phase 0** | Substrate Initialisation | FMS module scaffold, schema.sql, rebuild queue, edge table, `__init__.py` files | `[x] Complete` |
| **Phase 1** | Rebuild Engine | `mythos/engines/rebuild_engine.py` — six operations, queue runner, edge writer | `[x] Complete` |
| **Phase 2** | FMS Integration | `forge_modules/mythos/module.py` — `on_signal` + `on_actor_create` hooks, `mythos_rebuild_engine` | `[x] Complete` |
| **Phase 3** | Source Collection | `mythos/collectors/mythology_collector.py` — Wikipedia REST, public-domain targets | `[x] Complete` |
| **Phase 4** | Character Extraction | `mythos/processors/character_extractor.py` — trait/power/symbol NER, archetype inference | `[x] Complete` |
| **Phase 5** | spaCy Enhancement | Custom `EntityRuler` patterns for mythology NER; replace keyword heuristics in extractor | `[ ] Pending` |
| **Phase 6** | Dialogue Activation | Wire dialogue nodes to Claude API (Haiku); expose `/mythos/chat/<character_id>` route | `[ ] Pending` |
| **Phase 7** | Media Pipeline | Connect `mythos_media` status transitions to real production workflow (RSS, YouTube API) | `[ ] Pending` |
| **Phase 8** | FORGE Surface Integration | Add Mythos tab to FORGE surface; character cards, rebuild queue status, edge graph view | `[ ] Pending` |
| **Phase 9** | Scheduled Rebuild Worker | `bin/mythos_worker.bat` — runs `process_queue(batch_size=20)` on a cron tick | `[ ] Pending` |

---

## Atomic Node Dictionary

> These are the seven atomic node types. Every piece of content in Mythos Anthology is one of these nodes, and every relationship between them is an edge in `mythos_edges`.

### Node: `source` → Table: `mythos_sources`

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| `source_id` | `TEXT` | `PRIMARY KEY` | `hex(randomblob(8))` — 16-char hex |
| `created_at` | `TEXT` | `NOT NULL DEFAULT now()` | UTC ISO timestamp |
| `title` | `TEXT` | `NOT NULL` | Human-readable title of the source |
| `source_type` | `TEXT` | `CHECK(...)` | `folklore · academic · public_domain · digital · oral_tradition · archaeological · other` |
| `culture` | `TEXT` | `NULLABLE` | Cultural origin — `Greek`, `Norse`, `Yoruba`, `Shona`, etc. |
| `era` | `TEXT` | `NULLABLE` | `Ancient · Medieval · Modern · Unknown` |
| `url` | `TEXT` | `NULLABLE` | Origin URL if digital |
| `content_hash` | `TEXT` | `UNIQUE` | SHA256 of `raw_text` — deduplication key |
| `raw_text` | `TEXT` | `NULLABLE` | Full extracted plain text |
| `metadata_json` | `TEXT` | `DEFAULT '{}'` | Arbitrary extension bag |

**Source Safety Rules:**
- `content_hash` uniqueness enforces idempotent ingestion — re-running a collector is always safe.
- `raw_text` being `NULL` is valid for a placeholder row; the extractor must handle it gracefully.
- No `DELETE` on sources — use `source_type = 'other'` and a note in `metadata_json` to retire.

---

### Node: `character` → Table: `mythos_characters`

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| `character_id` | `TEXT` | `PRIMARY KEY` | `hex(randomblob(8))` |
| `canonical_name` | `TEXT` | `NOT NULL UNIQUE` | The authoritative name used as join key |
| `aliases_json` | `TEXT` | `DEFAULT '[]'` | JSON array of alternate names / spellings |
| `culture` | `TEXT` | `NOT NULL` | Cultural origin |
| `era` | `TEXT` | `NULLABLE` | Time period |
| `archetype` | `TEXT` | `CHECK(...)` | `deity · hero · monster · trickster · spirit · creature · mortal · other` |
| `origin_text` | `TEXT` | `NULLABLE` | One-paragraph canonical origin statement |
| `traits_json` | `TEXT` | `DEFAULT '[]'` | Personality / behavioural traits |
| `powers_json` | `TEXT` | `DEFAULT '[]'` | Abilities, domains, magical powers |
| `symbols_json` | `TEXT` | `DEFAULT '[]'` | Associated objects, animals, plants |
| `variants_json` | `TEXT` | `DEFAULT '[]'` | Cross-cultural parallel figures |
| `confidence_score` | `REAL` | `DEFAULT 0.0` | Research completeness `[0.0–1.0]` — see scoring weights below |
| `status` | `TEXT` | `CHECK(...)` | `stub → draft → canonical → published` |
| `forge_actor_id` | `TEXT` | `NULLABLE` | Bridge to FORGE `actors.actor_id` when signal cross-reference fires |

**Confidence Score Weights** (must sum to 1.0):
```
has_narrative      0.30
has_dialogue       0.20
has_media          0.15
has_traits         0.15
has_powers         0.10
has_symbols        0.05
has_variants       0.05
```
Scored by `rebuild_engine.score_confidence()`. Automatically enqueued after every `spawn_*` operation.

**Status Lifecycle:** `stub` → `draft` → `canonical` → `published` (one-directional).

---

### Node: `narrative` → Table: `mythos_narratives`

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| `narrative_id` | `TEXT` | `PRIMARY KEY` | `hex(randomblob(8))` |
| `character_id` | `TEXT` | `FK → mythos_characters` | Owning character |
| `source_id` | `TEXT` | `FK → mythos_sources NULLABLE` | Source the narrative was drawn from |
| `narrative_type` | `TEXT` | `CHECK(...)` | `origin · myth · legend · retelling · modern_interpretation · summary` |
| `title` | `TEXT` | `NOT NULL` | Narrative title |
| `body_text` | `TEXT` | `NOT NULL` | Full narrative body |
| `cultural_context` | `TEXT` | `NULLABLE` | Era / region context note |
| `themes_json` | `TEXT` | `DEFAULT '[]'` | Thematic tags |
| `status` | `TEXT` | `CHECK(...)` | `draft → reviewed → published` |

---

### Node: `dialogue` → Table: `mythos_dialogues`

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| `dialogue_id` | `TEXT` | `PRIMARY KEY` | `hex(randomblob(8))` |
| `character_id` | `TEXT` | `FK → mythos_characters` | Character this persona embodies |
| `persona_prompt` | `TEXT` | `NOT NULL` | System prompt for AI character embodiment |
| `voice_traits_json` | `TEXT` | `DEFAULT '{}'` | Tone, vocabulary style, speech patterns |
| `knowledge_scope_json` | `TEXT` | `DEFAULT '[]'` | What the character knows |
| `forbidden_json` | `TEXT` | `DEFAULT '[]'` | Out-of-character topics to refuse |
| `last_interaction_at` | `TEXT` | `NULLABLE` | UTC timestamp of most recent chat |
| `interaction_count` | `INTEGER` | `DEFAULT 0` | Total conversation turns |
| `status` | `TEXT` | `CHECK(...)` | `inactive → testing → live` |

**Dialogue Rules:**
- Only one `status='live'` dialogue per character at any time (enforce at application layer).
- `persona_prompt` is regenerated by `spawn_dialogue()` whenever new narratives are published.
- Claude model target: `claude-haiku-4-5-20251001` (fast, cost-efficient for interactive use).

---

### Node: `media` → Table: `mythos_media`

| Column | Type | Constraint | Description |
|--------|------|-----------|-------------|
| `media_id` | `TEXT` | `PRIMARY KEY` | `hex(randomblob(8))` |
| `character_id` | `TEXT` | `FK → mythos_characters NULLABLE` | Associated character |
| `narrative_id` | `TEXT` | `FK → mythos_narratives NULLABLE` | Source narrative |
| `media_type` | `TEXT` | `CHECK(...)` | `podcast_episode · youtube_video · interactive_page · article · short_clip · image · other` |
| `title` | `TEXT` | `NOT NULL` | Output title |
| `url` | `TEXT` | `NULLABLE` | Published URL |
| `platform` | `TEXT` | `NULLABLE` | `spotify · youtube · site · anchor · etc.` |
| `duration_sec` | `INTEGER` | `NULLABLE` | Runtime for audio/video |
| `published_at` | `TEXT` | `NULLABLE` | UTC publish timestamp |
| `status` | `TEXT` | `CHECK(...)` | `planned → in_production → published → archived` |

**Default spawn outputs per character** (from `spawn_media()`):
- `podcast_episode` on `spotify`
- `article` on `site`

---

## Edge Dictionary → Table: `mythos_edges`

> Every relationship between nodes is an edge. The edge table IS the recursive rebuild graph.

| Edge Type | Meaning | Example |
|-----------|---------|---------|
| `spawned_from` | This node was generated from the target | `narrative → spawned_from → character` |
| `references` | This node cites / draws from the target | `narrative → references → source` |
| `derived_from` | Soft derivation (paraphrase, adaptation) | `retelling → derived_from → origin_myth` |
| `feeds_back_to` | Media/dialogue output refreshed a canon node | `published_video → feeds_back_to → character` |
| `parallel_to` | Cross-cultural equivalence | `Persephone → parallel_to → Inanna` |
| `contradicts` | Conflicting account / alternate tradition | `Roman_narrative → contradicts → Greek_narrative` |

---

## Rebuild Queue → Table: `mythos_rebuild_queue`

> The heartbeat of the recursive mechanism. Every spawn operation enqueues the next.

| Operation | Trigger | Next enqueues |
|-----------|---------|---------------|
| `extract_character` | New source ingested | `spawn_narrative` |
| `spawn_narrative` | Character has source link | `spawn_dialogue` |
| `spawn_dialogue` | Narrative exists | `spawn_media` + `score_confidence` |
| `spawn_media` | Dialogue exists | *(terminal — awaits human production)* |
| `score_confidence` | Any node updated | *(terminal — updates score only)* |
| `refresh_canon` | Media published / signal hit | `score_confidence` |

**Priority scale:** 1 (immediate) → 10 (background). Default assignments:
- `extract_character` → 3
- `score_confidence` → 4
- `spawn_narrative` → 5
- `spawn_dialogue` → 6
- `spawn_media` → 7
- `refresh_canon` → 8

---

## Recursive Rebuild Loop

```
[SOURCE INGEST]
     │
     ▼
extract_character()  ─── writes edge: character →spawned_from→ source
     │
     ▼
spawn_narrative()    ─── writes edge: narrative →spawned_from→ character
     │
     ▼
spawn_dialogue()     ─── writes edge: dialogue  →spawned_from→ character
     │                   enqueues: spawn_media + score_confidence
     ▼
spawn_media()        ─── writes edges: media →spawned_from→ character
     │
     ▼
[HUMAN PRODUCTION: publish podcast / article]
     │
     ▼
refresh_canon()      ─── writes edge: media →feeds_back_to→ character
     │                   enqueues: score_confidence
     ▼
score_confidence()   ─── updates character.confidence_score [0.0–1.0]
     │
     └──► [LOOP CLOSES: canon updated, ready for next source ingest]
```

---

## FORGE Integration Points

| Integration | Mechanism | Status |
|-------------|-----------|--------|
| Signal cross-reference | `on_signal` hook — scans every FORGE signal for character name hits | `[x] Active` |
| Actor bridging | `on_actor_create` hook — links `forge_actor_id` when actor name matches character | `[x] Active` |
| Rebuild engine | `mythos_rebuild_engine` — registered in Conclave, fires per signal | `[x] Active` |
| FORGE actor graph | `mythos_characters.forge_actor_id` FK bridge to `actors.actor_id` | `[x] Schema ready` |
| FORGE surface tab | `/mythos` route — character grid, rebuild queue status, edge graph | `[ ] Pending Phase 8` |

---

## Commands

```powershell
# Apply schema (first run)
python app.py --migrate
# or directly:
sqlite3 database.db < forge_modules/mythos/schema.sql

# Collect sources from Wikipedia
python mythos/collectors/mythology_collector.py
python mythos/collectors/mythology_collector.py --culture Greek --limit 10

# Extract character data from stubs
python mythos/processors/character_extractor.py
python mythos/processors/character_extractor.py --dry-run

# Process rebuild queue (up to 20 items)
python -c "from mythos.engines.rebuild_engine import process_queue; process_queue(20)"
```
