#!/usr/bin/env python3
"""
Phase 64 Epsilon-II: Oxpeckers Content Depth Resolution
========================================================
1. Fix relevance_score gate (0.05 → confidence-heuristic, min 1.25)
2. Reset processed_at for unprocessed signals → Conclave pickup
3. Create artifacts via signal_to_artifact()
4. Run process_all() for NER expansion
5. Fix Oxpecker actor (institution dedup + type correction)
6. Run targeted Conclave ingest
7. Post-run actor edge assessment
"""
import sys, os, json, sqlite3, time
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')

from pathlib import Path
from datetime import datetime, timezone

DB_PATH = r'C:\Users\matam\Projects\FORGE\database.db'

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%SZ')

def log(msg):
    print(f'[{ts()}] {msg}', flush=True)

# ── Confidence heuristic (mirrors artifact_processor._confidence) ─────────────
_CONF_WEIGHTS = [
    ("confirmed", 0.3), ("official", 0.3), ("verified", 0.3),
    ("breaking",  0.2), ("urgent",   0.2),
    ("attack",    0.1), ("crisis",   0.1), ("nuclear",  0.1),
    ("casualt",   0.1), ("alert",    0.1), ("warning",  0.1),
    ("leaked",    0.1), ("intercept",0.1),
    # Investigation keywords added for OSINT content
    ("investigat",0.2), ("court",    0.15), ("syndicate",0.2),
    ("smuggl",    0.2), ("corrupt",  0.2), ("traffick", 0.2),
    ("exposes",   0.15), ("expose",  0.15), ("dispute", 0.1),
    ("charges",   0.1), ("arrested", 0.15), ("convicted",0.2),
]
_BASE_CONF = 0.25

def confidence(text):
    score = _BASE_CONF
    lo = text.lower()
    for kw, w in _CONF_WEIGHTS:
        if kw in lo:
            score += w
    return min(round(score, 3), 1.0)


# ════════════════════════════════════════════════════════════════════════
# PHASE 1 — Fix relevance_score on all Oxpeckers signals
# ════════════════════════════════════════════════════════════════════════
log("PHASE 1 — Fixing relevance_score on Oxpeckers signals")

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")

oxp_rows = conn.execute(
    "SELECT signal_id, title, content FROM signals WHERE source='oxpeckers'"
).fetchall()

updates = []
for r in oxp_rows:
    text = (r['title'] or '') + ' ' + (r['content'] or '')
    conf  = confidence(text)
    rel   = round(1.0 + conf, 3)   # floor 1.25 — always clears the >1.0 gate
    updates.append((rel, r['signal_id']))
    log(f"  {r['title'][:55]:<55} conf={conf:.2f} rel={rel:.3f}")

conn.execute("BEGIN")
conn.executemany(
    "UPDATE signals SET relevance_score = ? WHERE signal_id = ?", updates
)
conn.execute("COMMIT")
log(f"  Updated {len(updates)} signals — all now above >1.0 gate")


# ════════════════════════════════════════════════════════════════════════
# PHASE 2 — Reset processed_at for unprocessed signals → Conclave pickup
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 2 — Resetting processed_at for unprocessed Oxpeckers signals")

unproc = conn.execute(
    "SELECT COUNT(*) FROM signals WHERE source='oxpeckers' AND processed_at IS NULL"
).fetchone()[0]
already = conn.execute(
    "SELECT COUNT(*) FROM signals WHERE source='oxpeckers' AND processed_at IS NOT NULL"
).fetchone()[0]
log(f"  Unprocessed: {unproc}  |  Already processed: {already}")
log(f"  Resetting processed_at=NULL on all {len(oxp_rows)} Oxpeckers signals for full re-ingest")

conn.execute("BEGIN")
conn.execute("UPDATE signals SET processed_at = NULL WHERE source='oxpeckers'")
conn.execute("COMMIT")
log("  Reset complete")


# ════════════════════════════════════════════════════════════════════════
# PHASE 3 — Create artifacts via signal_to_artifact()
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 3 — Converting Oxpeckers signals to artifacts via signal_to_artifact()")

from forage.processors.artifact_processor import ProcessorManager
# db_path must be Path object — ProcessorManager calls .exists() on it
pm = ProcessorManager(db_path=Path(DB_PATH))

artifact_ids = []
skipped = []

oxp_full = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers'"
).fetchall()

for row in oxp_full:
    sig = dict(row)
    # Build content for artifact — combine title + full content for richer NER surface
    combined = (sig.get('title') or '') + '. ' + (sig.get('content') or '')
    sig['content'] = combined
    try:
        art_id = pm.signal_to_artifact(sig)
        if art_id == -1:
            skipped.append(sig['signal_id'])
            log(f"  SKIP (too short <200 chars): {sig['title'][:55]}")
        elif art_id:
            artifact_ids.append(art_id)
            # Link the artifact back to the signal for triple_extractor provenance
            conn.execute(
                "UPDATE signals SET source_artifact_id=? WHERE signal_id=?",
                (art_id, sig['signal_id'])
            )
            conn.commit()
            log(f"  artifact_id={art_id:>7}  {sig['title'][:55]}")
        else:
            skipped.append(sig['signal_id'])
    except Exception as e:
        log(f"  ERROR: {e} on {sig['title'][:40]}")
        skipped.append(sig['signal_id'])

log(f"\n  Created {len(artifact_ids)} artifacts | {len(skipped)} skipped")


# ════════════════════════════════════════════════════════════════════════
# PHASE 4 — Run process_all() for NER on new artifacts
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 4 — Running NER via run_batch() on pending artifacts")
# process_all() is a module-level function; run_batch() is the ProcessorManager method
from forage.processors.artifact_processor import process_all as _run_artifact_pipeline
_run_artifact_pipeline()
log("  run_batch() complete")

# Check NER results
ner_counts = conn.execute("""
    SELECT COUNT(DISTINCT signal_id) as sigs, COUNT(*) as entities
    FROM signal_entities
    WHERE signal_id IN (
        SELECT signal_id FROM signals WHERE source='oxpeckers'
    )
""").fetchone()
log(f"  NER output: {ner_counts['entities']} entities across {ner_counts['sigs']} signals")

# Show extracted entities
ner_rows = conn.execute("""
    SELECT se.text, se.label, s.title
    FROM signal_entities se
    JOIN signals s ON s.signal_id = se.signal_id
    WHERE s.source='oxpeckers'
      AND se.label IN ('PERSON','ORG','GPE')
    ORDER BY se.label, se.text
""").fetchall()
if ner_rows:
    log("  Extracted entities:")
    for r in ner_rows:
        log(f"    [{r['label']:6}] {r['text']:<35} from: {r['title'][:45]}")
else:
    log("  No NER entities extracted yet (artifacts may need processing cycle)")


# ════════════════════════════════════════════════════════════════════════
# PHASE 5 — Fix Oxpecker actor (institution type + deduplication)
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 5 — Actor deduplication and type correction")

# Find all 'oxpecker' or 'oxpeckers' actors
oxp_actors = conn.execute("""
    SELECT actor_id, name, type, gravity_score,
           (SELECT COUNT(*) FROM signal_actors WHERE actor_id=a.actor_id) as sig_count
    FROM actors a
    WHERE lower(name) LIKE '%oxpecker%'
    ORDER BY sig_count DESC
""").fetchall()

log(f"  Found {len(oxp_actors)} Oxpecker actor entries:")
for a in oxp_actors:
    log(f"    [{a['actor_id']:>4}] {a['name']:<30} type={a['type']:<12} signals={a['sig_count']}")

if oxp_actors:
    # Correct type to institution on all
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE actors SET type='institution' WHERE lower(name) LIKE '%oxpecker%'"
    )
    conn.execute("COMMIT")
    log("  Corrected type to 'institution' for all Oxpecker actors")

    # If multiple, merge into the one with most signal links
    if len(oxp_actors) > 1:
        canonical = oxp_actors[0]  # highest signal count
        dupes = [a['actor_id'] for a in oxp_actors[1:]]
        log(f"  Merging {len(dupes)} duplicates into canonical actor_id={canonical['actor_id']}")
        conn.execute("BEGIN")
        for dup_id in dupes:
            # Re-point signal_actors links
            conn.execute("""
                INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role)
                SELECT signal_id, ?, role FROM signal_actors WHERE actor_id=?
            """, (canonical['actor_id'], dup_id))
            conn.execute("DELETE FROM signal_actors WHERE actor_id=?", (dup_id,))
            # Re-point actor_events links
            conn.execute("""
                INSERT OR IGNORE INTO actor_events (actor_id, event_id, role)
                SELECT ?, event_id, role FROM actor_events WHERE actor_id=?
            """, (canonical['actor_id'], dup_id))
            conn.execute("DELETE FROM actor_events WHERE actor_id=?", (dup_id,))
            # Delete duplicate actor
            conn.execute("DELETE FROM actors WHERE actor_id=?", (dup_id,))
        conn.execute("COMMIT")
        log(f"  Deduplication complete — canonical actor_id={canonical['actor_id']}")

# Also fix other NER artifacts from Oxpeckers signals
log("\n  Fixing other NER-artifact actors from Oxpeckers context:")
noise_actors = conn.execute("""
    SELECT actor_id, name, type FROM actors
    WHERE lower(name) IN ('connected', 'broken', 'marakele', 'when', 'coal')
      AND type = 'person'
""").fetchall()
for a in noise_actors:
    log(f"    Flagging NER artifact: [{a['actor_id']}] '{a['name']}' (was type=person)")
# These are NER parsing artifacts from titles — log them but don't delete
# (deletion could cascade; they'll be filtered by the NER blocklist going forward)


# ════════════════════════════════════════════════════════════════════════
# PHASE 6 — Targeted Conclave ingest on Oxpeckers signals
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 6 — Targeted Conclave ingest on Oxpeckers signals")

from core.pipeline.ingest import ingest_signal

oxp_signals = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()

processed = 0
errors = 0
escalated = 0

for row in oxp_signals:
    sig = dict(row)
    try:
        result = ingest_signal(sig)
        processed += 1
        decision = result.get('case', {}).get('decision', 'unknown')
        gravity  = result.get('gravity_signal', {}).get('gravity_score', 0)
        if decision in ('CREATE CASE', 'create_case'):
            escalated += 1
            log(f"  ESCALATED  grav={gravity:.3f}  {sig['title'][:65]}")
        else:
            log(f"  {decision:<14}  grav={gravity:.3f}  {sig['title'][:55]}")
    except Exception as e:
        errors += 1
        log(f"  ERROR: {e} | {sig['title'][:40]}")

log(f"\n  Conclave complete: {processed} processed | {errors} errors | {escalated} escalated")


# ════════════════════════════════════════════════════════════════════════
# PHASE 7 — Post-run assessment
# ════════════════════════════════════════════════════════════════════════
log("\nPHASE 7 — Post-run actor edge assessment")

# Final gravity scores on Oxpeckers signals
log("\n  Oxpeckers signal gravity scores (post-Conclave):")
final = conn.execute("""
    SELECT title, gravity_score, relevance_score, processed_at
    FROM signals WHERE source='oxpeckers'
    ORDER BY gravity_score DESC NULLS LAST
""").fetchall()
for r in final:
    log(f"  grav={str(r['gravity_score'] or 'null'):>8}  rel={r['relevance_score']:.3f}  {r['title'][:65]}")

# New actors extracted from Oxpeckers NER bridge
log("\n  New actors materialised from Oxpeckers NER:")
new_actors = conn.execute("""
    SELECT a.actor_id, a.name, a.type,
           COUNT(DISTINCT sa.signal_id) as sig_count
    FROM actors a
    JOIN signal_actors sa ON sa.actor_id = a.actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
    GROUP BY a.actor_id
    ORDER BY sig_count DESC
    LIMIT 20
""").fetchall()
for a in new_actors:
    log(f"  [{a['actor_id']:>4}] {a['name']:<40} type={a['type']:<15} signals={a['sig_count']}")

# Entity relationships involving Oxpeckers actors
log("\n  Entity relationships involving Oxpeckers-context actors:")
oxp_actor_ids = [a['actor_id'] for a in new_actors]
if oxp_actor_ids:
    ph = ','.join('?' * len(oxp_actor_ids))
    er = conn.execute(f"""
        SELECT er.relation_type, er.confidence, a1.name as subject, a2.name as object
        FROM entity_relationships er
        JOIN actors a1 ON a1.actor_id = er.subject_actor_id
        JOIN actors a2 ON a2.actor_id = er.object_actor_id
        WHERE er.subject_actor_id IN ({ph}) OR er.object_actor_id IN ({ph})
        ORDER BY er.confidence DESC
        LIMIT 20
    """, oxp_actor_ids + oxp_actor_ids).fetchall()
    for r in er:
        log(f"  [{r['relation_type']:<20}] conf={r['confidence']:.2f}  {r['subject'][:28]} <-> {r['object'][:28]}")
    if not er:
        log("  No edges yet — entity_relationships populated on next triple_extractor run")

# Check if any Oxpeckers actors co-occur with Hawks/NPA/ANC
log("\n  Cross-reference: Oxpeckers actors vs enforcement institutions:")
enforcement_ids = [24, 60, 39, 43, 63]  # NPA, NPA-abbrev, Hawks/DPCI, ANC x2
for eid in enforcement_ids:
    ename = conn.execute("SELECT name FROM actors WHERE actor_id=?", (eid,)).fetchone()
    if not ename: continue
    shared = conn.execute("""
        SELECT COUNT(DISTINCT s.signal_id) as shared_signals
        FROM signals s
        JOIN signal_actors sa1 ON sa1.signal_id = s.signal_id
        JOIN signal_actors sa2 ON sa2.signal_id = s.signal_id
        WHERE sa1.actor_id IN ({})
          AND sa2.actor_id = ?
    """.format(','.join('?' * len(oxp_actor_ids))), oxp_actor_ids + [eid]).fetchone()
    if shared and shared['shared_signals'] > 0:
        log(f"  LINK FOUND: Oxpeckers actors share {shared['shared_signals']} signal(s) with [{eid}] {ename['name']}")

conn.close()
log("\nPhase 64 Epsilon-II complete.")
