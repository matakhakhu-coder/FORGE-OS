#!/usr/bin/env python3
"""
Phase 64 — Reprocess: NER + Conclave on body-text-enriched Oxpeckers signals
1. Delete old artifacts & signal_entities for Oxpeckers signals
2. Recreate artifacts from enriched content via signal_to_artifact()
3. Run process_all() for NER extraction
4. Run Conclave ingest_signal() on all 14 enriched signals
5. Post-run gravity and actor assessment
"""
import sys, sqlite3, time
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = r'C:\Users\matam\Projects\FORGE\database.db'

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%SZ')

def log(msg):
    print(f'[{ts()}] {msg}', flush=True)

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Clear old artifacts and signal_entities for Oxpeckers
# ════════════════════════════════════════════════════════════════════════════
log("PHASE 1 — Clearing stale artifacts and NER entities for Oxpeckers signals")

# Get Oxpeckers signal IDs and their old artifact IDs
old_artifacts = conn.execute("""
    SELECT signal_id, source_artifact_id FROM signals
    WHERE source='oxpeckers' AND source_artifact_id IS NOT NULL
""").fetchall()
old_art_ids = [r['source_artifact_id'] for r in old_artifacts if r['source_artifact_id']]
log(f"  Found {len(old_art_ids)} stale artifact IDs to clear")

conn.execute("BEGIN")
# Clear signal_entities for all Oxpeckers signals
conn.execute("""
    DELETE FROM signal_entities
    WHERE signal_id IN (SELECT signal_id FROM signals WHERE source='oxpeckers')
""")
# Clear old artifacts
if old_art_ids:
    ph = ','.join('?' * len(old_art_ids))
    conn.execute(f"DELETE FROM artifacts WHERE artifact_id IN ({ph})", old_art_ids)
# Reset source_artifact_id and processed_at on signals
conn.execute("""
    UPDATE signals SET source_artifact_id=NULL, processed_at=NULL
    WHERE source='oxpeckers'
""")
conn.execute("COMMIT")
log("  Cleared stale artifacts, signal_entities, and reset source_artifact_id")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Recreate artifacts from enriched content
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 2 — Creating new artifacts from full article bodies")

from forage.processors.artifact_processor import ProcessorManager
pm = ProcessorManager(db_path=Path(DB_PATH))

oxp_signals = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()

artifact_ids = []
skipped = []

for row in oxp_signals:
    sig = dict(row)
    combined = (sig.get('title') or '') + '. ' + (sig.get('content') or '')
    sig['content'] = combined
    try:
        art_id = pm.signal_to_artifact(sig)
        if art_id == -1:
            skipped.append(sig['signal_id'])
            log(f"  SKIP (content too short): {sig['title'][:55]}")
        elif art_id:
            artifact_ids.append(art_id)
            conn.execute(
                "UPDATE signals SET source_artifact_id=? WHERE signal_id=?",
                (art_id, sig['signal_id'])
            )
            conn.commit()
            log(f"  artifact_id={art_id:>7}  {sig['title'][:65]}")
        else:
            skipped.append(sig['signal_id'])
    except Exception as e:
        log(f"  ERROR: {e} | {sig['title'][:40]}")
        skipped.append(sig['signal_id'])

log(f"\n  Created {len(artifact_ids)} artifacts | {len(skipped)} skipped")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3 — NER extraction via process_all()
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 3 — Running NER via process_all()")

from forage.processors.artifact_processor import process_all as _run_pipeline
_run_pipeline()
log("  process_all() complete")

ner_counts = conn.execute("""
    SELECT COUNT(DISTINCT signal_id) as sigs, COUNT(*) as entities
    FROM signal_entities
    WHERE signal_id IN (SELECT signal_id FROM signals WHERE source='oxpeckers')
""").fetchone()
log(f"  NER output: {ner_counts['entities']} entities across {ner_counts['sigs']} signals")

ner_rows = conn.execute("""
    SELECT se.text, se.label, s.title
    FROM signal_entities se
    JOIN signals s ON s.signal_id = se.signal_id
    WHERE s.source='oxpeckers'
      AND se.label IN ('PERSON','ORG','GPE')
    ORDER BY se.label, se.text
""").fetchall()
if ner_rows:
    log(f"  Extracted {len(ner_rows)} PERSON/ORG/GPE entities:")
    for r in ner_rows:
        log(f"    [{r['label']:6}] {r['text']:<38} | {r['title'][:40]}")
else:
    log("  No NER entities extracted")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Conclave gravity re-score on all 14 signals
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 4 — Conclave ingest_signal() on body-text-enriched signals")

from core.pipeline.ingest import ingest_signal

oxp_fresh = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()

processed = errors = escalated = flagged = 0

for row in oxp_fresh:
    sig = dict(row)
    try:
        result = ingest_signal(sig)
        processed += 1
        decision = result.get('case', {}).get('decision', 'unknown')
        gravity  = result.get('gravity_signal', {}).get('gravity_score', 0)
        if decision in ('CREATE CASE', 'create_case'):
            escalated += 1
            log(f"  [CREATE CASE] grav={gravity:.3f}  {sig['title'][:65]}")
        elif decision in ('FLAG MONITOR', 'flag_monitor') or gravity >= 0.6:
            flagged += 1
            log(f"  [FLAG MONITOR] grav={gravity:.3f}  {sig['title'][:65]}")
        else:
            log(f"  [{decision:<12}] grav={gravity:.3f}  {sig['title'][:55]}")
    except Exception as e:
        errors += 1
        log(f"  ERROR: {e} | {sig['title'][:40]}")

log(f"\n  Conclave: {processed} processed | {errors} errors | {flagged} flagged | {escalated} escalated")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 5 — Post-run assessment
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 5 — Post-run assessment")

log("\n  === GRAVITY SCORES (post-enrichment Conclave) ===")
final = conn.execute("""
    SELECT title, gravity_score, relevance_score
    FROM signals WHERE source='oxpeckers'
    ORDER BY gravity_score DESC NULLS LAST
""").fetchall()
for r in final:
    bar = '#' * int((r['gravity_score'] or 0) * 20)
    flag = ' *** FLAG' if (r['gravity_score'] or 0) >= 0.6 else ''
    log(f"  {r['gravity_score'] or 0:.3f} {bar:<14}{flag}  {r['title'][:65]}")

log("\n  === ACTORS materialised from Oxpeckers NER (body-text enriched) ===")
new_actors = conn.execute("""
    SELECT a.actor_id, a.name, a.type,
           COUNT(DISTINCT sa.signal_id) as sig_count
    FROM actors a
    JOIN signal_actors sa ON sa.actor_id = a.actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
    GROUP BY a.actor_id
    ORDER BY sig_count DESC
    LIMIT 30
""").fetchall()
for a in new_actors:
    log(f"  [{a['actor_id']:>4}] {a['name']:<40} type={a['type']:<15} signals={a['sig_count']}")

if not new_actors:
    log("  No actors yet — check NER blocklist")

log("\n  === ENTITY RELATIONSHIPS involving Oxpeckers-context actors ===")
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
        log("  No edges yet — needs triple_extractor run on enriched artifacts")

log("\n  === ENFORCEMENT CROSS-REFERENCE (Hawks/NPA/ANC) ===")
enforcement_ids = [24, 60, 39, 43, 63]
for eid in enforcement_ids:
    ename = conn.execute("SELECT name FROM actors WHERE actor_id=?", (eid,)).fetchone()
    if not ename or not oxp_actor_ids: continue
    ph2 = ','.join('?' * len(oxp_actor_ids))
    shared = conn.execute(f"""
        SELECT COUNT(DISTINCT s.signal_id) as c
        FROM signals s
        JOIN signal_actors sa1 ON sa1.signal_id = s.signal_id
        JOIN signal_actors sa2 ON sa2.signal_id = s.signal_id
        WHERE sa1.actor_id IN ({ph2}) AND sa2.actor_id = ?
    """, oxp_actor_ids + [eid]).fetchone()
    if shared and shared['c'] > 0:
        log(f"  LINK: Oxpeckers actors share {shared['c']} signal(s) with [{eid}] {ename['name']}")

conn.close()
log("\nPhase 64 Reprocess complete.")
