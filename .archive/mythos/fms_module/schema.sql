-- =============================================================================
-- MYTHOS ANTHOLOGY — Atomic Node Network Schema
-- Apply via:  python app.py --migrate
--             or  sqlite3 database.db < forge_modules/mythos/schema.sql
-- All tables are CREATE IF NOT EXISTS — safe to re-run.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- SOURCE NODES — raw research inputs: texts, references, academic sources
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_sources (
    source_id    TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    title        TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK(source_type IN (
                     'folklore', 'academic', 'public_domain',
                     'digital', 'oral_tradition', 'archaeological', 'other'
                 )),
    culture      TEXT,                      -- 'Greek', 'Norse', 'Yoruba', 'Shona', etc.
    era          TEXT,                      -- 'Ancient', 'Medieval', 'Modern', 'Unknown'
    url          TEXT,
    content_hash TEXT UNIQUE,              -- SHA256 of raw_text — dedup key
    raw_text     TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_sources_culture
    ON mythos_sources(culture);
CREATE INDEX IF NOT EXISTS idx_mythos_sources_type
    ON mythos_sources(source_type);

-- -----------------------------------------------------------------------------
-- CHARACTER NODES — canonical mythic entities (one row per entity)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_characters (
    character_id        TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    canonical_name      TEXT NOT NULL UNIQUE,
    aliases_json        TEXT NOT NULL DEFAULT '[]',    -- alternate names / spellings
    culture             TEXT NOT NULL,
    era                 TEXT,
    archetype           TEXT CHECK(archetype IN (
                            'deity', 'hero', 'monster', 'trickster',
                            'spirit', 'creature', 'mortal', 'other'
                        )),
    origin_text         TEXT,
    traits_json         TEXT NOT NULL DEFAULT '[]',    -- personality / power traits
    symbols_json        TEXT NOT NULL DEFAULT '[]',    -- associated symbols / objects
    powers_json         TEXT NOT NULL DEFAULT '[]',    -- abilities / domains
    variants_json       TEXT NOT NULL DEFAULT '[]',    -- cross-cultural parallels
    confidence_score    REAL NOT NULL DEFAULT 0.0,     -- research completeness [0.0–1.0]
    status              TEXT NOT NULL DEFAULT 'stub' CHECK(status IN (
                            'stub', 'draft', 'canonical', 'published'
                        )),
    forge_actor_id      TEXT,              -- bridge to FORGE actors table (nullable)
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_chars_culture
    ON mythos_characters(culture);
CREATE INDEX IF NOT EXISTS idx_mythos_chars_status
    ON mythos_characters(status);
CREATE INDEX IF NOT EXISTS idx_mythos_chars_archetype
    ON mythos_characters(archetype);
CREATE INDEX IF NOT EXISTS idx_mythos_chars_forge_actor
    ON mythos_characters(forge_actor_id)
    WHERE forge_actor_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- NARRATIVE NODES — stories, retellings, interpretations
-- Each narrative is owned by one character, optionally sourced from a source.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_narratives (
    narrative_id     TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    character_id     TEXT NOT NULL REFERENCES mythos_characters(character_id),
    source_id        TEXT REFERENCES mythos_sources(source_id),
    narrative_type   TEXT NOT NULL CHECK(narrative_type IN (
                         'origin', 'myth', 'legend', 'retelling',
                         'modern_interpretation', 'summary'
                     )),
    title            TEXT NOT NULL,
    body_text        TEXT NOT NULL,
    cultural_context TEXT,
    themes_json      TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'draft' CHECK(status IN (
                         'draft', 'reviewed', 'published'
                     )),
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_narr_character
    ON mythos_narratives(character_id);
CREATE INDEX IF NOT EXISTS idx_mythos_narr_status
    ON mythos_narratives(status);

-- -----------------------------------------------------------------------------
-- DIALOGUE NODES — AI persona configs (one active config per character)
-- Persona prompt embodies the character for interactive dialogue.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_dialogues (
    dialogue_id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    character_id         TEXT NOT NULL REFERENCES mythos_characters(character_id),
    persona_prompt       TEXT NOT NULL,               -- system prompt for AI embodiment
    voice_traits_json    TEXT NOT NULL DEFAULT '{}',  -- tone, vocabulary, speech patterns
    knowledge_scope_json TEXT NOT NULL DEFAULT '[]',  -- what the character knows
    forbidden_json       TEXT NOT NULL DEFAULT '[]',  -- out-of-character topics
    last_interaction_at  TEXT,
    interaction_count    INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'inactive' CHECK(status IN (
                             'inactive', 'testing', 'live'
                         )),
    metadata_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_dial_character
    ON mythos_dialogues(character_id);
CREATE INDEX IF NOT EXISTS idx_mythos_dial_status
    ON mythos_dialogues(status);

-- -----------------------------------------------------------------------------
-- MEDIA NODES — output artifacts: podcast episodes, videos, articles, etc.
-- Optionally linked to a character and/or a specific narrative.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_media (
    media_id       TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    character_id   TEXT REFERENCES mythos_characters(character_id),
    narrative_id   TEXT REFERENCES mythos_narratives(narrative_id),
    media_type     TEXT NOT NULL CHECK(media_type IN (
                       'podcast_episode', 'youtube_video', 'interactive_page',
                       'article', 'short_clip', 'image', 'other'
                   )),
    title          TEXT NOT NULL,
    url            TEXT,
    platform       TEXT,                  -- 'spotify', 'youtube', 'site', 'anchor', etc.
    duration_sec   INTEGER,
    published_at   TEXT,
    status         TEXT NOT NULL DEFAULT 'planned' CHECK(status IN (
                       'planned', 'in_production', 'published', 'archived'
                   )),
    metadata_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_media_character
    ON mythos_media(character_id);
CREATE INDEX IF NOT EXISTS idx_mythos_media_type
    ON mythos_media(media_type);
CREATE INDEX IF NOT EXISTS idx_mythos_media_status
    ON mythos_media(status);

-- -----------------------------------------------------------------------------
-- EDGE TABLE — directed relationships between any two nodes
-- This is the recursive rebuild graph. Every spawn action writes an edge.
--
-- node_type values: 'source' | 'character' | 'narrative' | 'dialogue' | 'media'
-- edge_type meanings:
--   spawned_from   — this node was generated FROM the target node
--   references     — this node cites / draws from the target
--   derived_from   — soft derivation (paraphrase, adaptation)
--   feeds_back_to  — media/dialogue output that refreshed a source/character
--   parallel_to    — cross-cultural equivalent
--   contradicts    — conflicting account / alternate tradition
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_edges (
    edge_id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    source_node_type TEXT NOT NULL CHECK(source_node_type IN (
                         'source', 'character', 'narrative', 'dialogue', 'media'
                     )),
    source_node_id   TEXT NOT NULL,
    target_node_type TEXT NOT NULL CHECK(target_node_type IN (
                         'source', 'character', 'narrative', 'dialogue', 'media'
                     )),
    target_node_id   TEXT NOT NULL,
    edge_type        TEXT NOT NULL CHECK(edge_type IN (
                         'spawned_from', 'references', 'derived_from',
                         'feeds_back_to', 'parallel_to', 'contradicts'
                     )),
    weight           REAL NOT NULL DEFAULT 1.0,
    metadata_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mythos_edges_source
    ON mythos_edges(source_node_type, source_node_id);
CREATE INDEX IF NOT EXISTS idx_mythos_edges_target
    ON mythos_edges(target_node_type, target_node_id);
CREATE INDEX IF NOT EXISTS idx_mythos_edges_type
    ON mythos_edges(edge_type);

-- -----------------------------------------------------------------------------
-- REBUILD QUEUE — tracks recursive rebuild operations
-- Each step in the rebuild chain enqueues the next node to process.
-- This table is the heartbeat of the recursive mechanism.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mythos_rebuild_queue (
    queue_id      TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at  TEXT,
    node_type     TEXT NOT NULL CHECK(node_type IN (
                      'source', 'character', 'narrative', 'dialogue', 'media'
                  )),
    node_id       TEXT NOT NULL,
    operation     TEXT NOT NULL CHECK(operation IN (
                      'extract_character', 'spawn_narrative', 'spawn_dialogue',
                      'spawn_media', 'refresh_canon', 'score_confidence'
                  )),
    trigger_edge_id TEXT REFERENCES mythos_edges(edge_id),
    priority      INTEGER NOT NULL DEFAULT 5,          -- 1 (highest) → 10 (lowest)
    status        TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                      'pending', 'processing', 'complete', 'failed', 'skipped'
                  )),
    result_json   TEXT NOT NULL DEFAULT '{}',
    error_text    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mythos_queue_status
    ON mythos_rebuild_queue(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_mythos_queue_node
    ON mythos_rebuild_queue(node_type, node_id);
