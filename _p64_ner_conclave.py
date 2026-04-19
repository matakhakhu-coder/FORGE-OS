#!/usr/bin/env python3
"""
Phase 64 — Targeted NER + Conclave on Oxpeckers artifacts (564906-564919)
Calls process_artifact() directly on each of the 14 enriched artifact IDs.
"""
import sys, sqlite3
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = r'C:\Users\matam\Projects\FORGE\database.db'
OXP_ARTIFACT_IDS = list(range(564906, 564920))  # 564906..564919

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%SZ')

def log(msg):
    print(f'[{ts()}] {msg}', flush=True)

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")

# ── Phase A — Targeted NER via process_artifact() per artifact ────────────────
log("PHASE A — Targeted NER: process_artifact() on 14 Oxpeckers artifacts")

from forage.processors.artifact_processor import ProcessorManager
pm = ProcessorManager(db_path=Path(DB_PATH))

total_entities = 0
ner_results = []

for art_id in OXP_ARTIFACT_IDS:
    try:
        result = pm.process_artifact(art_id)
        entities = result.get('entities', 0)
        status   = result.get('status', 'unknown')
        total_entities += entities
        ner_results.append((art_id, status, entities))
        log(f"  artifact={art_id}  status={status}  entities={entities}")
    except Exception as e:
        log(f"  ERROR artifact={art_id}: {e}")
        ner_results.append((art_id, 'error', 0))

log(f"\n  NER complete: {total_entities} total entities across {len(OXP_ARTIFACT_IDS)} artifacts")

# Verify in DB
ner_db = conn.execute("""
    SELECT COUNT(DISTINCT signal_id) as sigs, COUNT(*) as entities
    FROM signal_entities
    WHERE signal_id IN (SELECT signal_id FROM signals WHERE source='oxpeckers')
""").fetchone()
log(f"  DB check: {ner_db['entities']} entities across {ner_db['sigs']} signals")

ner_rows = conn.execute("""
    SELECT se.text, se.label, s.title
    FROM signal_entities se
    JOIN signals s ON s.signal_id = se.signal_id
    WHERE s.source='oxpeckers'
      AND se.label IN ('PERSON','ORG','GPE')
    ORDER BY se.label, se.text
""").fetchall()
if ner_rows:
    log(f"  Entities extracted ({len(ner_rows)}):")
    for r in ner_rows:
        log(f"    [{r['label']:6}] {r['text']:<38} | {r['title'][:40]}")


# ── Phase B — Conclave gravity re-score ───────────────────────────────────────
log("\nPHASE B — Conclave ingest_signal() on body-text-enriched signals")

from core.pipeline.ingest import ingest_signal

oxp_signals = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()

processed = errors = escalated = flagged = 0

for row in oxp_signals:
    sig = dict(row)
    try:
        result   = ingest_signal(sig)
        processed += 1
        decision = result.get('case', {}).get('decision', 'unknown')
        gravity  = result.get('gravity_signal', {}).get('gravity_score', 0)
        if decision in ('CREATE CASE', 'create_case') or gravity >= 0.8:
            escalated += 1
            log(f"  [CREATE CASE ] grav={gravity:.3f}  {sig['title'][:65]}")
        elif gravity >= 0.6:
            flagged += 1
            log(f"  [FLAG MONITOR] grav={gravity:.3f}  {sig['title'][:65]}")
        else:
            log(f"  [{decision:<12}] grav={gravity:.3f}  {sig['title'][:55]}")
    except Exception as e:
        errors += 1
        log(f"  ERROR: {e} | {sig['title'][:40]}")

log(f"\n  Conclave: {processed} processed | {errors} errors | {flagged} flagged | {escalated} escalated")


# ── Phase C — Intelligence Assessment ────────────────────────────────────────
log("\nPHASE C — Intelligence Assessment")

log("\n  === GRAVITY SCORES (post body-text enrichment) ===")
final = conn.execute("""
    SELECT title, gravity_score, relevance_score
    FROM signals WHERE source='oxpeckers'
    ORDER BY gravity_score DESC NULLS LAST
""").fetchall()
for r in final:
    g = r['gravity_score'] or 0
    bar = '#' * int(g * 20)
    flag = ' *** FLAG MONITOR' if g >= 0.6 else ''
    flag = ' *** CREATE CASE'  if g >= 0.8 else flag
    log(f"  {g:.3f} |{bar:<16}|{flag}  {r['title'][:60]}")

log("\n  === ACTORS materialised from Oxpeckers (post-enrichment) ===")
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
    log(f"  [{a['actor_id']:>4}] {a['name']:<42} type={a['type']:<15} signals={a['sig_count']}")

if not new_actors:
    log("  No actors — NER→actor bridge not yet populated")

log("\n  === ENFORCEMENT CROSS-REFERENCE (Hawks/NPA/ANC) ===")
oxp_actor_ids = [a['actor_id'] for a in new_actors]
enforcement = {24: 'NPA', 60: 'NPA (abbrev)', 39: 'Hawks/DPCI', 43: 'ANC', 63: 'ANC (alt)'}
for eid, ename in enforcement.items():
    if not oxp_actor_ids:
        break
    ph = ','.join('?' * len(oxp_actor_ids))
    shared = conn.execute(f"""
        SELECT COUNT(DISTINCT s.signal_id) as c
        FROM signals s
        JOIN signal_actors sa1 ON sa1.signal_id = s.signal_id
        JOIN signal_actors sa2 ON sa2.signal_id = s.signal_id
        WHERE sa1.actor_id IN ({ph}) AND sa2.actor_id = ?
    """, oxp_actor_ids + [eid]).fetchone()
    if shared and shared['c'] > 0:
        log(f"  LINK FOUND: {shared['c']} signal(s) shared with [{eid}] {ename}")

log("\n  === ENTITY RELATIONSHIPS (triple_extractor edges) ===")
if oxp_actor_ids:
    ph = ','.join('?' * len(oxp_actor_ids))
    er = conn.execute(f"""
        SELECT er.relation_type, er.confidence, a1.name as subject, a2.name as object
        FROM entity_relationships er
        JOIN actors a1 ON a1.actor_id = er.subject_actor_id
        JOIN actors a2 ON a2.actor_id = er.object_actor_id
        WHERE er.subject_actor_id IN ({ph}) OR er.object_actor_id IN ({ph})
        ORDER BY er.confidence DESC LIMIT 20
    """, oxp_actor_ids + oxp_actor_ids).fetchall()
    for r in er:
        log(f"  [{r['relation_type']:<20}] conf={r['confidence']:.2f}  "
            f"{r['subject'][:28]} <-> {r['object'][:28]}")
    if not er:
        log("  No edges yet — needs triple_extractor cycle on enriched artifacts")

conn.close()
log("\nPhase 64 NER+Conclave complete.")
