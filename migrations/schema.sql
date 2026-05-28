-- ════════════════════════════════════════════════════════════════════════════
-- FORGE Canonical Schema  (Stable 1.1.2 · post-Phase 72 · FLUX A–I complete)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Authority: this file is the single source of truth for FORGE's DB schema.
-- Generated from the live operational database on 2026-05-09.
--
-- Usage:
--   Fresh bootstrap  →  python app.py --init-db
--   Migration        →  python app.py --migrate
--   Verification     →  python migrations/verify_schema.py
--
-- Notes:
--   • All TEXT timestamps store ISO-8601 UTC strings.
--   • REAL score columns store floats in [0.0, 1.0] unless noted.
--   • FTS5 virtual tables and their internal shadow tables are created
--     automatically by SQLite when the VIRTUAL TABLE statement executes.
--   • Indexes are listed after all tables for dependency ordering.
--   • ENT-01 columns (confidence_score, automated, socint_profile on actors)
--     are inline here; existing DBs receive them via migrate_db().
-- ════════════════════════════════════════════════════════════════════════════

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ── Core Intelligence Tables ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT    PRIMARY KEY,
    source             TEXT    NOT NULL,
    external_id        TEXT    NOT NULL UNIQUE,
    title              TEXT    NOT NULL,
    content            TEXT,
    lat                REAL,
    lng                REAL,
    timestamp          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status             TEXT    NOT NULL DEFAULT 'raw'
                       CHECK(status IN ('raw','reviewed','promoted','dismissed')),
    metadata_json      TEXT,
    cluster_id         TEXT,
    is_priority        INTEGER NOT NULL DEFAULT 0,
    confidence_score   REAL,
    source_artifact_id INTEGER REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    stream             TEXT    NOT NULL DEFAULT 'GLOBAL'
                       CHECK(stream IN ('GLOBAL','CRIME_INTEL','INFRASTRUCTURE','PRIORITY')),
    relevance_score    REAL    NOT NULL DEFAULT 1.0,
    source_type        TEXT    NOT NULL DEFAULT 'live',
    gravity_score      REAL,
    processed_at       TEXT,
    conclave_meta      TEXT,
    duplicate_count    INTEGER NOT NULL DEFAULT 0,
    socint_tags        TEXT    DEFAULT NULL,
    socint_resonance   REAL    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS actors (
    actor_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    type             TEXT    NOT NULL
                     CHECK(type IN (
                         'person','institution','media','movement','government',
                         'location','political_party','organization','unknown',
                         'other','paramilitary'
                     )),
    description      TEXT,
    source_type      TEXT    NOT NULL DEFAULT 'live',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    confidence_score REAL    NOT NULL DEFAULT 0.5
                     CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
    automated        INTEGER NOT NULL DEFAULT 0,
    socint_profile   TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT    NOT NULL,
    summary          TEXT,
    description      TEXT,
    date             TEXT,
    location         TEXT,
    latitude         REAL,
    longitude        REAL,
    category         TEXT
                     CHECK(category IN (
                         'Election','Security','Civil Unrest','Legislative',
                         'Economic','Diplomatic','Military','Social','Other'
                     )),
    source_type      TEXT    NOT NULL DEFAULT 'live',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    confidence_score REAL,
    automated        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT    NOT NULL,
    description       TEXT,
    type              TEXT    NOT NULL
                      CHECK(type IN ('video','photo','document','audio','news','capture')),
    date              TEXT,
    location          TEXT,
    latitude          REAL,
    longitude         REAL,
    tags              TEXT,
    source            TEXT
                      CHECK(source IN (
                          'verified','unverified','government','leaked','citizen','media'
                      )),
    source_type       TEXT    NOT NULL DEFAULT 'live',
    file_path         TEXT,
    thumbnail         TEXT,
    event_id          INTEGER REFERENCES events(event_id) ON DELETE SET NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    raw_text_cache    TEXT,
    processing_status TEXT    NOT NULL DEFAULT 'pending'
                      CHECK(processing_status IN
                          ('pending','processing','done','failed','skipped')),
    file_hash_sha256  TEXT,
    file_hash_md5     TEXT,
    file_size_bytes   INTEGER,
    exif_json         TEXT,
    gps_lat           REAL,
    gps_lng           REAL,
    device_make       TEXT,
    device_model      TEXT,
    exif_datetime     TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    case_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    description       TEXT,
    hypothesis        TEXT,
    case_type         TEXT    DEFAULT 'general'
                      CHECK(case_type IN (
                          'general','financial','geopolitical','criminal',
                          'infrastructure','cyber','humanitarian','other'
                      )),
    status            TEXT    NOT NULL DEFAULT 'active'
                      CHECK(status IN ('active','closed','archived')),
    source_type       TEXT    NOT NULL DEFAULT 'live',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    auto_generated    INTEGER NOT NULL DEFAULT 0,
    trigger_signal_id TEXT,
    context_anchors   TEXT
);

-- ── Junction / Linkage Tables ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_actors (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    signal_id  TEXT     NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    actor_id   INTEGER  NOT NULL REFERENCES actors(actor_id)   ON DELETE CASCADE,
    role       TEXT     DEFAULT 'mentioned',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (signal_id, actor_id)
);

CREATE TABLE IF NOT EXISTS case_signals (
    case_id    INTEGER NOT NULL REFERENCES cases(case_id)    ON DELETE CASCADE,
    signal_id  TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    note       TEXT,
    pinned_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (case_id, signal_id)
);

CREATE TABLE IF NOT EXISTS case_actors (
    case_id        INTEGER NOT NULL REFERENCES cases(case_id)  ON DELETE CASCADE,
    actor_id       INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    note           TEXT,
    pinned_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    sequence_order INTEGER,
    transition_note TEXT,
    PRIMARY KEY (case_id, actor_id)
);

CREATE TABLE IF NOT EXISTS case_events (
    case_id        INTEGER NOT NULL REFERENCES cases(case_id)  ON DELETE CASCADE,
    event_id       INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    note           TEXT,
    pinned_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    sequence_order INTEGER,
    transition_note TEXT,
    PRIMARY KEY (case_id, event_id)
);

CREATE TABLE IF NOT EXISTS case_artifacts (
    case_id        INTEGER NOT NULL REFERENCES cases(case_id)        ON DELETE CASCADE,
    artifact_id    INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    note           TEXT,
    pinned_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    sequence_order INTEGER,
    transition_note TEXT,
    PRIMARY KEY (case_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS actor_events (
    actor_id INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    role     TEXT,
    PRIMARY KEY (actor_id, event_id)
);

CREATE TABLE IF NOT EXISTS event_actors (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER  NOT NULL REFERENCES events(event_id)  ON DELETE CASCADE,
    actor_id   INTEGER  NOT NULL REFERENCES actors(actor_id)  ON DELETE CASCADE,
    role       TEXT     DEFAULT 'involved',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, actor_id)
);

-- ── Intelligence Graph Tables ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entity_relationships (
    relationship_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_actor_id   INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    object_actor_id    INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    relation_type      TEXT    NOT NULL,
    description        TEXT,
    confidence         REAL    NOT NULL DEFAULT 1.0
                       CHECK(confidence >= 0.0 AND confidence <= 1.0),
    source_artifact_id INTEGER REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    source_event_id    INTEGER REFERENCES events(event_id)       ON DELETE SET NULL,
    extraction_method  TEXT    NOT NULL DEFAULT 'manual'
                       CHECK(extraction_method IN ('manual','spacy','llm')),
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subject_actor_id, object_actor_id, relation_type)
);

CREATE TABLE IF NOT EXISTS correlated_incidents (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_a              TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    signal_b              TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    correlation_score     REAL    NOT NULL,
    distance_km           REAL    NOT NULL,
    time_difference_hours REAL    NOT NULL,
    space_score           REAL    NOT NULL,
    time_score            REAL    NOT NULL,
    detected_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (signal_a, signal_b)
);

CREATE TABLE IF NOT EXISTS signal_entities (
    entity_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id  TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    text       TEXT    NOT NULL,
    label      TEXT    NOT NULL,
    count      INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (signal_id, text, label)
);

CREATE TABLE IF NOT EXISTS signal_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    flag_type   TEXT    NOT NULL,
    flag_label  TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 0.5
                CHECK(confidence >= 0.0 AND confidence <= 1.0),
    cluster_id  TEXT,
    detail_json TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (signal_id, flag_type)
);

-- ── Sentinel & Alert Tables ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sentinel_alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type       TEXT    NOT NULL,
    confidence_score REAL    NOT NULL DEFAULT 0.5
                     CHECK(confidence_score >= 0.0 AND confidence_score <= 1.0),
    location_lat     REAL,
    location_lon     REAL,
    signal_count     INTEGER NOT NULL DEFAULT 1,
    summary          TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'new'
                     CHECK(status IN ('new','acknowledged','dismissed')),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS priorities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','deprecated','disabled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Actor Graph / Network Tables ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS actor_network_metrics (
    actor_id             INTEGER PRIMARY KEY REFERENCES actors(actor_id) ON DELETE CASCADE,
    betweenness          REAL    NOT NULL DEFAULT 0,
    eigenvector          REAL    NOT NULL DEFAULT 0,
    pagerank             REAL    NOT NULL DEFAULT 0,
    community_id         INTEGER,
    community_id_socint  INTEGER DEFAULT NULL,
    node_count           INTEGER,
    edge_count           INTEGER,
    influence_score      REAL    NOT NULL DEFAULT 0,
    computed_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actor_coalitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    coalition_label TEXT    NOT NULL,
    co_occurrence   INTEGER NOT NULL DEFAULT 0,
    member_count    INTEGER NOT NULL DEFAULT 1,
    threshold_used  INTEGER NOT NULL DEFAULT 5,
    computed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (actor_id, coalition_label)
);

CREATE TABLE IF NOT EXISTS actor_weights (
    actor_id   INTEGER PRIMARY KEY,
    weight     REAL    NOT NULL DEFAULT 1.0,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS network_emergence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id            INTEGER NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    window_start        TEXT    NOT NULL,
    window_end          TEXT    NOT NULL,
    link_count          INTEGER NOT NULL DEFAULT 0,
    previous_link_count INTEGER NOT NULL DEFAULT 0,
    growth_rate         REAL    NOT NULL DEFAULT 0.0,
    emergence_score     REAL    NOT NULL DEFAULT 0.0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Graph Substrate Tables ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type     TEXT    NOT NULL,
    ref_id        TEXT    NOT NULL,
    label         TEXT,
    metadata_json TEXT,
    created_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE(node_type, ref_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id     INTEGER NOT NULL,
    target_node_id     INTEGER NOT NULL,
    relation_type      TEXT    NOT NULL,
    weight             REAL    DEFAULT 1.0,
    confidence         REAL    DEFAULT 1.0,
    source_event_id    INTEGER,
    source_signal_id   TEXT,
    source_artifact_id INTEGER,
    created_at         TEXT    DEFAULT (datetime('now')),
    UNIQUE(source_node_id, target_node_id, relation_type),
    FOREIGN KEY(source_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(target_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
);

-- ── Artifact Intelligence Tables ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS artifact_duplicates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id     INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    duplicate_of_id INTEGER NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    hash_sha256     TEXT    NOT NULL,
    detected_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (artifact_id, duplicate_of_id)
);

-- ── Signal Analysis Tables ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS signal_baselines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_date  TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    region_key   TEXT    NOT NULL,
    daily_count  INTEGER NOT NULL DEFAULT 0,
    computed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (bucket_date, source, region_key)
);

CREATE TABLE IF NOT EXISTS discovery_targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name     TEXT    NOT NULL UNIQUE,
    suggested_query TEXT    NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    evidence_json   TEXT,
    candidate_score REAL    NOT NULL DEFAULT 0.0,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','approved','ignored')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    actioned_at     TEXT
);

-- ── Pipeline Operations Tables ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    component   TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK(status IN ('success','error')),
    records_in  INTEGER,
    records_out INTEGER,
    duration_s  REAL,
    detail_json TEXT,
    run_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key      TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    stage        TEXT,
    progress     REAL    DEFAULT 0.0,
    message      TEXT,
    pid          INTEGER,
    records_in   INTEGER DEFAULT 0,
    records_out  INTEGER DEFAULT 0,
    started_at   TEXT,
    updated_at   TEXT,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS case_feedback (
    case_id      TEXT PRIMARY KEY,
    gravity_score REAL,
    decision     TEXT,
    assigned_at  TEXT
);

-- ── FLUX SOCINT Tables ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS socint_signals (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    source        TEXT     NOT NULL DEFAULT 'x_pulse',
    actor_id      INTEGER  REFERENCES actors(actor_id) ON DELETE SET NULL,
    signal_id     TEXT     REFERENCES signals(signal_id) ON DELETE SET NULL,
    content       TEXT     NOT NULL,
    metadata_json TEXT     DEFAULT NULL,
    timestamp     TEXT     NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS socint_resonance (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    actor_a       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    actor_b       INTEGER  NOT NULL REFERENCES actors(actor_id) ON DELETE CASCADE,
    score         REAL     NOT NULL DEFAULT 0.0,
    features_json TEXT     DEFAULT NULL,
    updated_at    TEXT     NOT NULL DEFAULT (datetime('now')),
    UNIQUE(actor_a, actor_b),
    CHECK(score >= 0.0 AND score <= 1.0),
    CHECK(actor_a < actor_b)
);

CREATE TABLE IF NOT EXISTS flux_latent_seeds (
    tag             TEXT    PRIMARY KEY,
    parent_seed     TEXT,
    discovery_depth INTEGER NOT NULL DEFAULT 1,
    jaccard_score   REAL    NOT NULL DEFAULT 0.0,
    velocity        REAL    NOT NULL DEFAULT 1.0,
    total_count     INTEGER NOT NULL DEFAULT 0,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS flux_tag_cooccurrence (
    pulse_id TEXT    NOT NULL,
    pulse_ts TEXT    NOT NULL,
    seed_tag TEXT    NOT NULL,
    co_tag   TEXT    NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pulse_id, seed_tag, co_tag)
);

-- ── Wiki Intelligence Tables ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS wiki_articles (
    id                 INTEGER  PRIMARY KEY AUTOINCREMENT,
    slug               TEXT     UNIQUE,
    title              TEXT     NOT NULL,
    summary            TEXT,
    content_html       TEXT,
    tags               TEXT,
    behavior           TEXT,
    features           TEXT,
    max_pulse_strength REAL,
    source_type        TEXT     DEFAULT 'live',
    last_updated       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wiki_entries (
    id        INTEGER  PRIMARY KEY,
    actor_id  TEXT,
    event_id  TEXT,
    artifact  TEXT,
    timestamp DATETIME,
    narrative TEXT,
    context   TEXT
);

CREATE TABLE IF NOT EXISTS wiki_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_slug     TEXT,
    target_slug     TEXT,
    connection_type TEXT DEFAULT 'related',
    FOREIGN KEY(source_slug) REFERENCES wiki_articles(slug),
    FOREIGN KEY(target_slug) REFERENCES wiki_articles(slug)
);

-- ── FTS5 Virtual Tables ───────────────────────────────────────────────────────

CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts
    USING fts5(
        title, description, tags, location,
        content='artifacts',
        content_rowid='artifact_id'
    );

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
    USING fts5(
        title, summary, location,
        content='events',
        content_rowid='event_id'
    );

-- ── FTS Sync Triggers ─────────────────────────────────────────────────────────

CREATE TRIGGER IF NOT EXISTS artifacts_ai AFTER INSERT ON artifacts BEGIN
    INSERT INTO artifacts_fts(rowid, title, description, tags, location)
    VALUES (new.artifact_id, new.title, new.description, new.tags, new.location);
END;

CREATE TRIGGER IF NOT EXISTS artifacts_au AFTER UPDATE ON artifacts BEGIN
    INSERT INTO artifacts_fts(artifacts_fts, rowid, title, description, tags, location)
    VALUES ('delete', old.artifact_id, old.title, old.description, old.tags, old.location);
    INSERT INTO artifacts_fts(rowid, title, description, tags, location)
    VALUES (new.artifact_id, new.title, new.description, new.tags, new.location);
END;

CREATE TRIGGER IF NOT EXISTS artifacts_ad AFTER DELETE ON artifacts BEGIN
    INSERT INTO artifacts_fts(artifacts_fts, rowid, title, description, tags, location)
    VALUES ('delete', old.artifact_id, old.title, old.description, old.tags, old.location);
END;

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, title, summary, location)
    VALUES (new.event_id, new.title, new.summary, new.location);
END;

CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, summary, location)
    VALUES ('delete', old.event_id, old.title, old.summary, old.location);
    INSERT INTO events_fts(rowid, title, summary, location)
    VALUES (new.event_id, new.title, new.summary, new.location);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, summary, location)
    VALUES ('delete', old.event_id, old.title, old.summary, old.location);
END;

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_signals_title_time
    ON signals(title, timestamp);

CREATE INDEX IF NOT EXISTS idx_signals_socint_resonance
    ON signals(socint_resonance)
    WHERE socint_resonance IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_signal_flags_signal
    ON signal_flags(signal_id);

CREATE INDEX IF NOT EXISTS idx_signal_flags_type
    ON signal_flags(flag_type, confidence DESC);

CREATE INDEX IF NOT EXISTS idx_baselines_lookup
    ON signal_baselines(source, region_key, bucket_date);

CREATE INDEX IF NOT EXISTS idx_actor_coalitions_actor
    ON actor_coalitions(actor_id);

CREATE INDEX IF NOT EXISTS idx_actor_coalitions_label
    ON actor_coalitions(coalition_label);

CREATE INDEX IF NOT EXISTS idx_emergence_actor_time
    ON network_emergence(actor_id, window_end);

CREATE INDEX IF NOT EXISTS idx_graph_nodes_type_ref
    ON graph_nodes(node_type, ref_id);

CREATE INDEX IF NOT EXISTS idx_graph_edges_source
    ON graph_edges(source_node_id);

CREATE INDEX IF NOT EXISTS idx_graph_edges_target
    ON graph_edges(target_node_id);

CREATE INDEX IF NOT EXISTS idx_graph_edges_relation
    ON graph_edges(relation_type);

CREATE INDEX IF NOT EXISTS idx_pj_key
    ON pipeline_jobs(job_key);

CREATE INDEX IF NOT EXISTS idx_pj_status
    ON pipeline_jobs(status);

CREATE INDEX IF NOT EXISTS idx_socint_signals_actor
    ON socint_signals(actor_id);

CREATE INDEX IF NOT EXISTS idx_socint_resonance_score
    ON socint_resonance(score DESC);

CREATE INDEX IF NOT EXISTS idx_ftc_seed
    ON flux_tag_cooccurrence(seed_tag);

CREATE INDEX IF NOT EXISTS idx_ftc_co
    ON flux_tag_cooccurrence(co_tag);
