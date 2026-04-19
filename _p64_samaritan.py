#!/usr/bin/env python3
"""
Phase 64 Epsilon-III — Samaritan Assessment
============================================
Step 1: Actor deduplication (merge duplicate Groenewald/Shamukuni rows)
Step 2: Fix Tatenda Chitagu type (institution -> person)
Step 3: Conclave rescore with tuned investigative kernel
Step 4: Gravity League Table + enforcement cross-reference
"""
import sys, sqlite3
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')
from datetime import datetime, timezone

DB_PATH = r'C:\Users\matam\Projects\FORGE\database.db'

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%SZ')
def log(msg):
    print(f'[{ts()}] {msg}', flush=True)

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = OFF")  # OFF for safe FK migration
conn.execute("PRAGMA journal_mode = WAL")


# ============================================================================
# STEP 1: Actor deduplication — merge all variant rows into canonical ID
# ============================================================================
log("STEP 1 -- Actor deduplication")

# Canonical actor definitions: (canonical_name, canonical_type, list_of_aliases_to_merge)
CANONICAL_ACTORS = [
    {
        "name":    "Dawie Groenewald",
        "type":    "person",
        "aliases": [
            "Groenewald",
            "Dawie Groenewald\x92s Botswana",   # cp1252 curly apostrophe artefact
            "Dawie Groenewald's Botswana",
            "Dawie Groenewald\u2019s Botswana",
        ],
    },
    {
        "name":    "Machana Ronald Shamukuni",
        "type":    "person",
        "aliases": ["Shamukuni"],
    },
    {
        "name":    "Tatenda Chitagu",
        "type":    "person",
        "aliases": ["Chitagu"],
    },
]

for canon in CANONICAL_ACTORS:
    cname = canon["name"]
    ctype = canon["type"]

    # Ensure canonical actor exists
    conn.execute("INSERT OR IGNORE INTO actors (name, type) VALUES (?,?)", (cname, ctype))
    conn.execute("UPDATE actors SET type=? WHERE name=?", (ctype, cname))
    conn.commit()

    canonical_row = conn.execute("SELECT actor_id FROM actors WHERE name=?", (cname,)).fetchone()
    if not canonical_row:
        log(f"  WARNING: Could not find/create canonical actor: {cname}")
        continue
    can_id = canonical_row["actor_id"]
    log(f"  Canonical [{can_id}] {cname} (type={ctype})")

    # Find all duplicate rows (same name, or aliases)
    all_names = [cname] + canon["aliases"]
    placeholders = ','.join('?' * len(all_names))
    dupes = conn.execute(
        f"SELECT actor_id FROM actors WHERE name IN ({placeholders}) AND actor_id != ?",
        all_names + [can_id]
    ).fetchall()

    if not dupes:
        log(f"    No duplicates found.")
        continue

    dupe_ids = [d["actor_id"] for d in dupes]
    log(f"    Merging {len(dupe_ids)} duplicate IDs -> [{can_id}]: {dupe_ids}")

    conn.execute("BEGIN")
    for old_id in dupe_ids:
        # Re-point signal_actors rows
        conn.execute("""
            INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role)
            SELECT signal_id, ?, role FROM signal_actors WHERE actor_id=?
        """, (can_id, old_id))
        conn.execute("DELETE FROM signal_actors WHERE actor_id=?", (old_id,))

        # Re-point case_actors rows
        conn.execute("""
            INSERT OR IGNORE INTO case_actors (case_id, actor_id)
            SELECT case_id, ? FROM case_actors WHERE actor_id=?
        """, (can_id, old_id))
        conn.execute("DELETE FROM case_actors WHERE actor_id=?", (old_id,))

        # Re-point entity_relationships (subject + object)
        conn.execute("UPDATE OR IGNORE entity_relationships SET subject_actor_id=? WHERE subject_actor_id=?", (can_id, old_id))
        conn.execute("UPDATE OR IGNORE entity_relationships SET object_actor_id=?  WHERE object_actor_id=?",  (can_id, old_id))
        conn.execute("DELETE FROM entity_relationships WHERE subject_actor_id=? OR object_actor_id=?", (old_id, old_id))

        # Delete the duplicate actor row
        conn.execute("DELETE FROM actors WHERE actor_id=?", (old_id,))

    conn.execute("COMMIT")
    log(f"    Done. [{can_id}] {cname} is now canonical.")


# Fix any remaining type issues
log("\n  Fixing residual type issues...")
conn.execute("BEGIN")
conn.execute("UPDATE actors SET type='institution' WHERE lower(name) LIKE '%oxpecker%'")
conn.execute("UPDATE actors SET type='person'      WHERE lower(name) LIKE '%groenewald%'")
conn.execute("UPDATE actors SET type='person'      WHERE lower(name) LIKE '%shamukuni%'")
conn.execute("UPDATE actors SET type='person'      WHERE lower(name) LIKE '%chitagu%'")
conn.execute("COMMIT")

# Link Case Alpha to Groenewald + Shamukuni canonical actors
case = conn.execute("SELECT case_id FROM cases WHERE name LIKE '%Conservation Capture%'").fetchone()
if case:
    case_id = case["case_id"]
    for cname in ["Dawie Groenewald", "Machana Ronald Shamukuni"]:
        actor = conn.execute("SELECT actor_id FROM actors WHERE name=?", (cname,)).fetchone()
        if actor:
            conn.execute("INSERT OR IGNORE INTO case_actors (case_id, actor_id) VALUES (?,?)", (case_id, actor["actor_id"]))
    conn.commit()

# Verification
log("\n  Post-dedup actor roster (oxpeckers-linked):")
roster = conn.execute("""
    SELECT a.actor_id, a.name, a.type, COUNT(DISTINCT sa.signal_id) as sigs
    FROM actors a
    JOIN signal_actors sa ON sa.actor_id=a.actor_id
    JOIN signals s ON s.signal_id=sa.signal_id
    WHERE s.source='oxpeckers'
    GROUP BY a.actor_id ORDER BY sigs DESC
    LIMIT 30
""").fetchall()
for a in roster:
    log(f"    [{a['actor_id']:>5}] {a['name']:<42} type={a['type']:<15} sigs={a['sigs']}")


# ============================================================================
# STEP 2: Conclave rescore with tuned investigative kernel
# ============================================================================
log("\n" + "="*70)
log("STEP 2 -- Conclave rescore (tuned investigative kernel)")
log("="*70)

from core.pipeline.ingest import ingest_signal
from forage.processors.signal_interpreter import SignalInterpreter, _score_severity

# First show what the kernel produces for key signals manually
si = SignalInterpreter()

key_signals_check = conn.execute("""
    SELECT signal_id, title, content, source, is_priority
    FROM signals WHERE source='oxpeckers'
    ORDER BY relevance_score DESC NULLS LAST
""").fetchall()

log("\n  Pre-flight: SignalInterpreter outputs (tuned kernel):")
for row in key_signals_check:
    sig = dict(row)
    interp = si.interpret(sig)
    log(f"    [{row['signal_id']}] sev={interp['severity']:.2f}  cred={interp['source_credibility']:.2f}  "
        f"ai={interp['actor_importance']:.2f}  freq={interp['frequency']:.2f}  "
        f"  | {row['title'][:55]}")

# Full ingest_signal loop — updates gravity_score in DB
log("\n  Running ingest_signal() for all 14 Oxpeckers signals...")
results_table = []
processed = errors = escalated = flagged = 0

oxp_sigs = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC NULLS LAST"
).fetchall()

for row in oxp_sigs:
    sig = dict(row)
    try:
        result   = ingest_signal(sig)
        processed += 1
        decision = result.get('case', {}).get('decision', '?')
        gravity  = result.get('gravity_signal', {}).get('gravity_score', 0)
        sev      = result.get('interpreted_signal', {}).get('severity', 0)
        cred     = result.get('interpreted_signal', {}).get('source_credibility', 0)
        freq     = result.get('interpreted_signal', {}).get('frequency', 0)
        ai       = result.get('interpreted_signal', {}).get('actor_importance', 0)
        results_table.append((gravity, decision, sev, cred, freq, ai, sig['title']))
        if gravity >= 0.8:
            escalated += 1
            tag = 'CREATE CASE >>>'
        elif gravity >= 0.6:
            flagged += 1
            tag = 'FLAG MONITOR >>>'
        else:
            tag = decision
        log(f"    {tag:<20} grav={gravity:.3f}  sev={sev:.2f}  cred={cred:.2f}  | {sig['title'][:55]}")
    except Exception as e:
        errors += 1
        log(f"    ERROR: {e}")

log(f"\n  Result: {processed} processed | {errors} errors | {flagged} flagged | {escalated} escalated")


# ============================================================================
# STEP 3: Samaritan Assessment — Gravity League Table
# ============================================================================
log("\n" + "="*70)
log("SAMARITAN ASSESSMENT -- Phase 64 Epsilon-III Final Report")
log("="*70)

log("\n  *** GRAVITY LEAGUE TABLE ***")
log(f"  {'SCORE':<7}  {'BAR':<20}  {'TIER':<20}  TITLE")
log(f"  {'-'*7}  {'-'*20}  {'-'*20}  {'-'*55}")

# Pull fresh scores from DB (updated by ingest_signal above)
final = conn.execute("""
    SELECT title, gravity_score, relevance_score
    FROM signals WHERE source='oxpeckers'
    ORDER BY gravity_score DESC NULLS LAST
""").fetchall()

for r in final:
    g = r['gravity_score'] or 0
    bar = '#' * int(g * 24)
    if g >= 0.8:
        tier = 'CREATE CASE      <<<'
    elif g >= 0.6:
        tier = 'FLAG MONITOR     <<<'
    elif g >= 0.35:
        tier = '[MONITORED]'
    elif g >= 0.20:
        tier = '[low]'
    else:
        tier = ''
    log(f"  {g:.3f}  |{bar:<24}|  {tier:<20}  {r['title'][:58]}")

log(f"\n  Thresholds: CREATE CASE >= 0.80 | FLAG MONITOR >= 0.60 | MONITORED >= 0.35")


log("\n  *** INVESTIGATIVE SCORING BREAKDOWN — KEY SIGNALS ***")
log("  (showing severity component detail for Groenewald + Shamukuni signals)")

for title_frag, label in [('%Groenewald%', 'Groenewald'), ('%Shamukuni%', 'Shamukuni'),
                           ('%lithium%', 'Zimbabwe lithium'), ('%panopticon%', 'Panopticon'),
                           ('%Orangut%', 'Orangutan political')]:
    sig_row = conn.execute(
        "SELECT * FROM signals WHERE source='oxpeckers' AND title LIKE ?", (title_frag,)
    ).fetchone()
    if not sig_row:
        continue
    sig = dict(sig_row)
    interp = si.interpret(sig)
    content_lower = (str(sig.get('title','')) + ' ' + str(sig.get('content',''))).lower()

    # Show which investigative keywords triggered
    from forage.processors.signal_interpreter import SEVERITY_WEIGHTS
    inv_hits = []
    for tier in ['inv_critical', 'inv_high', 'inv_medium']:
        for kw in SEVERITY_WEIGHTS.get(tier, []):
            if kw in content_lower:
                inv_hits.append(f"{kw}({tier})")

    log(f"\n  [{label}]  grav={sig.get('gravity_score', 0):.3f}  sev={interp['severity']:.2f}")
    log(f"    Investigative keywords hit: {', '.join(inv_hits) if inv_hits else 'NONE'}")
    log(f"    Content length: {len(sig.get('content',''))} chars")


log("\n  *** ACTOR GRAPH STATUS ***")
actor_count = conn.execute("""
    SELECT COUNT(DISTINCT a.actor_id)
    FROM actors a JOIN signal_actors sa ON sa.actor_id=a.actor_id
    JOIN signals s ON s.signal_id=sa.signal_id WHERE s.source='oxpeckers'
""").fetchone()[0]
log(f"  Total unique actors linked to Oxpeckers signals: {actor_count}")

log("\n  Top actors by signal co-occurrence:")
top_actors = conn.execute("""
    SELECT a.actor_id, a.name, a.type, COUNT(DISTINCT sa.signal_id) as sigs
    FROM actors a JOIN signal_actors sa ON sa.actor_id=a.actor_id
    JOIN signals s ON s.signal_id=sa.signal_id
    WHERE s.source='oxpeckers'
    GROUP BY a.actor_id ORDER BY sigs DESC LIMIT 15
""").fetchall()
for a in top_actors:
    marker = ' <<< KEY' if a['name'] in ('Dawie Groenewald','Machana Ronald Shamukuni','Tatenda Chitagu') else ''
    log(f"    [{a['actor_id']:>5}] {a['name']:<42} ({a['type']:<15}) sigs={a['sigs']}{marker}")


log("\n  *** ENTITY RELATIONSHIPS (post triple_extractor) ***")
er_count = conn.execute("SELECT COUNT(*) FROM entity_relationships").fetchone()[0]
log(f"  Total edges in entity_relationships: {er_count}")

all_actor_ids = [a['actor_id'] for a in top_actors]
if all_actor_ids:
    ph = ','.join('?'*len(all_actor_ids))
    er_rows = conn.execute(f"""
        SELECT er.relation_type, er.confidence, a1.name as subject, a2.name as object
        FROM entity_relationships er
        JOIN actors a1 ON a1.actor_id=er.subject_actor_id
        JOIN actors a2 ON a2.actor_id=er.object_actor_id
        WHERE er.subject_actor_id IN ({ph}) OR er.object_actor_id IN ({ph})
        ORDER BY er.confidence DESC LIMIT 20
    """, all_actor_ids + all_actor_ids).fetchall()
    if er_rows:
        for r in er_rows:
            log(f"    [{r['relation_type']:<22}] conf={r['confidence']:.2f}  "
                f"{r['subject'][:30]} <-> {r['object'][:30]}")
    else:
        log("  No edges involving top actors (triple_extractor may need actor names in signal text)")


log("\n  *** ENFORCEMENT CROSS-REFERENCE ***")
enforcement_actors = conn.execute("""
    SELECT actor_id, name, type FROM actors
    WHERE lower(name) IN ('hawks','dpci','npa','south african police service','saps')
       OR lower(name) LIKE '%hawks%' OR lower(name) LIKE '%dpci%' OR lower(name) LIKE '%npa%'
""").fetchall()
log(f"  Enforcement actors in DB: {[(a['actor_id'], a['name']) for a in enforcement_actors]}")

if all_actor_ids and enforcement_actors:
    for ea in enforcement_actors:
        ph = ','.join('?'*len(all_actor_ids))
        shared = conn.execute(f"""
            SELECT COUNT(DISTINCT s.signal_id) as c
            FROM signals s
            JOIN signal_actors sa1 ON sa1.signal_id=s.signal_id
            JOIN signal_actors sa2 ON sa2.signal_id=s.signal_id
            WHERE sa1.actor_id IN ({ph}) AND sa2.actor_id=?
        """, all_actor_ids + [ea['actor_id']]).fetchone()
        if shared and shared['c'] > 0:
            log(f"  CO-OCCURRENCE LINK: [{ea['actor_id']}] {ea['name']} shares {shared['c']} signal(s) with Oxpeckers actors")


log("\n  *** CASE ALPHA STATUS ***")
case_row = conn.execute("SELECT * FROM cases WHERE name LIKE '%Conservation Capture%'").fetchone()
sig_count   = conn.execute("SELECT COUNT(*) FROM case_signals WHERE case_id=?", (case_row['case_id'],)).fetchone()[0]
actor_count2 = conn.execute("SELECT COUNT(*) FROM case_actors WHERE case_id=?", (case_row['case_id'],)).fetchone()[0]
case_actor_names = conn.execute("""
    SELECT a.name, a.type FROM case_actors ca JOIN actors a ON a.actor_id=ca.actor_id
    WHERE ca.case_id=?
""", (case_row['case_id'],)).fetchall()
log(f"  case_id   : {case_row['case_id']}")
log(f"  name      : {case_row['name']}")
log(f"  status    : {case_row['status']}")
log(f"  type      : {case_row['case_type']}")
log(f"  signals   : {sig_count} linked")
log(f"  actors    : {actor_count2} linked")
log(f"  Actor list:")
for a in case_actor_names:
    log(f"    - {a['name']} ({a['type']})")

log("\n" + "="*70)
log("Phase 64 Epsilon-III Samaritan Assessment COMPLETE.")
log("="*70)

conn.execute("PRAGMA foreign_keys = ON")
conn.close()
