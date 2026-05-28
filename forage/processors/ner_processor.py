#!/usr/bin/env python3
"""
FORAGE — NER Processor (Named-Entity Recognition)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scans FORGE signal titles + descriptions and extracts named entities
using spaCy's en_core_web_sm model (free, local, zero API cost).

Extracted entity types:
  PERSON  — people, real or fictional
  ORG     — companies, agencies, institutions
  GPE     — geopolitical entities (countries, cities, states)

Entities are stored in the `signal_entities` table, linked by signal_id.
Re-running is fully idempotent: INSERT OR IGNORE on (signal_id, text, label).

Setup (one-time):
    pip install spacy
    python -m spacy download en_core_web_sm

Usage
─────
    # Process all unprocessed signals (default)
    python forage/processors/ner_processor.py

    # Re-process everything (ignore already-processed flag)
    python forage/processors/ner_processor.py --reprocess

    # Process a single signal by ID
    python forage/processors/ner_processor.py --signal-id <uuid>

    # Dry run — print entities without writing to DB
    python forage/processors/ner_processor.py --dry-run

    # Override DB path
    python forage/processors/ner_processor.py --db /path/to/database.db

Database schema (created automatically):
    signal_entities (
        entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
        text        TEXT    NOT NULL,   -- normalised entity surface form
        label       TEXT    NOT NULL,   -- PERSON | ORG | GPE
        count       INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (signal_id, text, label)
    )
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Dependency guards ─────────────────────────────────────────────────────────

def _require_spacy():
    try:
        import spacy
        return spacy
    except ImportError:
        print(
            "[ner_processor] ERROR: spaCy is not installed.\n"
            "  Run:  pip install spacy\n"
            "        python -m spacy download en_core_web_sm",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_model(spacy_mod):
    model = "en_core_web_sm"
    try:
        nlp = spacy_mod.load(model)
        # Disable components we don't need for speed
        disabled = [p for p in nlp.pipe_names if p not in ("tok2vec", "ner")]
        nlp = spacy_mod.load(model, disable=disabled)
    except OSError:
        print(
            f"[ner_processor] ERROR: spaCy model '{model}' not found.\n"
            f"  Run:  python -m spacy download {model}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Inject SA EntityRuler before statistical NER so government abbreviations
    # (SIU, NPA, Hawks, DPCI, Treasury…) are correctly tagged ORG — matching
    # the same pattern used by triple_extractor.py (Phase 44).
    try:
        from forage.processors.sa_entity_ruler import build_sa_ruler
        nlp = build_sa_ruler(nlp)
    except Exception as exc:
        warn(f"SA EntityRuler failed to load (non-fatal): {exc}")

    return nlp

# ── Config ────────────────────────────────────────────────────────────────────

ENTITY_LABELS   = {"PERSON", "ORG", "GPE"}
BATCH_SIZE      = 50       # signals processed per DB transaction
MAX_TEXT_LEN    = 2000     # truncate combined title+content before NLP

# ── DB path resolution ────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [ner_processor] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [ner_processor] WARN  {msg}", file=sys.stderr, flush=True)

# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_ENTITIES_TABLE = """
CREATE TABLE IF NOT EXISTS signal_entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   TEXT    NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    text        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (signal_id, text, label)
)
"""

# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_ENTITIES_TABLE)
    # Phase 18: confidence_score column on signals (idempotent)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "confidence_score" not in existing:
        log("Adding confidence_score column to signals table…")
        conn.execute("ALTER TABLE signals ADD COLUMN confidence_score REAL")
    conn.commit()

# ── Entity extraction ─────────────────────────────────────────────────────────

def extract_entities(nlp, title: str, content: str) -> list[dict]:
    """
    Run spaCy NER on combined title + content.
    Returns list of {text, label, count} dicts — one per unique (text, label).
    """
    combined = f"{title}. {content or ''}"
    combined = combined[:MAX_TEXT_LEN]

    doc = nlp(combined)

    # Aggregate: count occurrences of each unique (surface_text, label) pair
    seen: dict[tuple, int] = {}
    for ent in doc.ents:
        if ent.label_ not in ENTITY_LABELS:
            continue
        # Normalise: strip possessives and excessive whitespace
        text = ent.text.strip().rstrip("'s").strip()
        if len(text) < 2:
            continue
        key = (text, ent.label_)
        seen[key] = seen.get(key, 0) + 1

    return [
        {"text": text, "label": label, "count": count}
        for (text, label), count in seen.items()
    ]


def insert_entities(conn: sqlite3.Connection,
                    signal_id: str,
                    entities: list[dict]) -> int:
    """
    INSERT OR IGNORE — idempotent.
    Returns number of newly inserted rows.
    """
    inserted = 0
    for ent in entities:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO signal_entities (signal_id, text, label, count)
            VALUES (?, ?, ?, ?)
            """,
            (signal_id, ent["text"], ent["label"], ent["count"]),
        )
        inserted += cur.rowcount
    return inserted

# ── Confidence scoring ────────────────────────────────────────────────────────

# Weight map: (keyword → score_delta)
CONFIDENCE_WEIGHTS: list[tuple[str, float]] = [
    # Strong verification signals
    ("confirmed",    0.3),
    ("official",     0.3),
    ("verified",     0.3),
    # Urgency signals
    ("breaking",     0.2),
    ("urgent",       0.2),
    # Proximity to priority content
    ("tsunami",      0.1),
    ("earthquake",   0.1),
    ("explosion",    0.1),
    ("attack",       0.1),
    ("crisis",       0.1),
    ("nuclear",      0.1),
    ("casualt",      0.1),   # casualty / casualties
    ("alert",        0.1),
    ("warning",      0.1),
    ("emergency",    0.1),
]

BASE_CONFIDENCE = 0.2   # floor for any ingested signal


def compute_confidence(title: str, content: str) -> float:
    """
    Keyword-weighting heuristic → confidence_score in [0.0, 1.0].
    Starts at BASE_CONFIDENCE, adds weights for matching keywords.
    Capped at 1.0.
    """
    combined = (title + " " + (content or "")).lower()
    score = BASE_CONFIDENCE
    for keyword, weight in CONFIDENCE_WEIGHTS:
        if keyword in combined:
            score += weight
    return min(round(score, 3), 1.0)


def update_confidence(conn: sqlite3.Connection, signal_id: str,
                      title: str, content: str) -> float:
    """Compute and persist confidence_score for one signal. Returns the score."""
    score = compute_confidence(title, content)
    conn.execute(
        "UPDATE signals SET confidence_score = ? WHERE signal_id = ?",
        (score, signal_id),
    )
    return score

# ── Main processing loop ──────────────────────────────────────────────────────

def run(db_path: Path | None = None,
        reprocess: bool = False,
        signal_id: str | None = None,
        dry_run: bool = False) -> int:

    resolved_db = _resolve_db(str(db_path) if db_path else None)
    log(f"Database : {resolved_db}")
    log(f"Reprocess: {reprocess} | Dry run: {dry_run}")

    spacy_mod = _require_spacy()
    log("Loading spaCy en_core_web_sm…")
    nlp = _load_model(spacy_mod)
    log("Model loaded.")

    try:
        conn = _open_db(resolved_db)
    except FileNotFoundError as exc:
        print(f"[ner_processor] ERROR: {exc}", file=sys.stderr)
        return 1

    if not dry_run:
        ensure_schema(conn)

    # Build query for signals to process
    if signal_id:
        rows = conn.execute(
            "SELECT signal_id, title, content FROM signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchall()
    elif reprocess:
        rows = conn.execute(
            "SELECT signal_id, title, content FROM signals ORDER BY timestamp DESC"
        ).fetchall()
    else:
        # Only signals that have no entities yet
        rows = conn.execute(
            """
            SELECT s.signal_id, s.title, s.content
            FROM   signals s
            LEFT   JOIN signal_entities se ON se.signal_id = s.signal_id
            WHERE  se.signal_id IS NULL
            ORDER  BY s.timestamp DESC
            """
        ).fetchall()

    total      = len(rows)
    processed  = 0
    ents_added = 0

    log(f"Signals to process: {total}")
    if total == 0:
        log("Nothing to do.")
        conn.close()
        return 0

    for i, row in enumerate(rows):
        sid     = row["signal_id"]
        title   = row["title"]   or ""
        content = row["content"] or ""

        entities   = extract_entities(nlp, title, content)
        confidence = compute_confidence(title, content)

        if dry_run:
            if entities or i < 5:
                log(f"  [DRY] {sid[:8]}… | conf={confidence:.2f} | "
                    f"{len(entities)} entities: "
                    f"{[(e['text'], e['label']) for e in entities[:3]]}")
            processed += 1
            ents_added += len(entities)
            continue

        n = insert_entities(conn, sid, entities)
        update_confidence(conn, sid, title, content)
        ents_added += n
        processed  += 1

        # Commit in batches
        if processed % BATCH_SIZE == 0:
            conn.commit()
            log(f"  Progress: {processed}/{total} signals "
                f"({ents_added} new entities so far)")

    if not dry_run:
        conn.commit()

    conn.close()
    log(f"Complete — {processed} signals processed, {ents_added} entities inserted.")
    if dry_run:
        log("Dry run — no writes made.")
    return 0

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORAGE NER Processor — extract PERSON/ORG/GPE from signals"
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Override path to database.db"
    )
    parser.add_argument(
        "--reprocess", action="store_true",
        help="Re-run NER on all signals, not just unprocessed ones"
    )
    parser.add_argument(
        "--signal-id", dest="signal_id", default=None,
        help="Process a single signal by its signal_id UUID"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print extracted entities without writing to the database"
    )
    args = parser.parse_args()
    sys.exit(run(
        db_path=args.db,
        reprocess=args.reprocess,
        signal_id=args.signal_id,
        dry_run=args.dry_run,
    ))

# --- MEGA RUNNER ADAPTER ---
def process_all():
    print("[NER Processor] Executing...")
    # Add your actual function call here if it exists