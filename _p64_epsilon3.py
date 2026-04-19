#!/usr/bin/env python3
"""
Phase 64 Epsilon-III: Case Alpha Activation
============================================
1. Materialise 136 NER entities → actors table + signal_actors links
2. Run triple_extractor on 14 enriched Oxpeckers artifacts
3. Create Case Alpha: Conservation Capture in cases table
4. Fix Oxpecker actor type (institution dedup)
5. Re-run Conclave with tuned scoring kernel
6. Samaritan Assessment: final gravity league table
"""
import sys, sqlite3, re
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

# NER noise filter — entity texts that are not valid actor candidates
NER_NOISE = {
    'south africa', "south africa'", 'southern africa', 'zimbabwe', 'namibia',
    'botswana', 'china', 'uk', 'us', 'qatar', 'doha', 'mpumalanga', 'middelburg',
    'eswatini', 'khutsong', 'carletonville', 'chibini', 'lubhuku', 'sebokeng',
    'mopane', 'serowe', 'tugela', 'oxpecker', 'coal', 'energy', 'parliament',
    'fmd', 'cbm', 'lng', 'alpr', 'cctv', 'cmore', 'broken', 'household',
    'bill', 'park', 'ge', 'vuvu', 'lavumisa', 'ngamiland', 'pongola',
    'kwaZulu-natal', 'rietspruit', 'isibonelo', 'amakhala emoyeni',
    'san communitie', 'mozambique', 'groenewal', 'sa-eswatini',
    'lavumisa-hluthi', 'the southern african territorie', 'mozambique',
    'nojoli wind farm', 'oxpecker', 'oxpeckers investigative environmental journalism',
    'seriti', 'the innovation/investigative journalism',
    "dawie groenewald's botswana",  # artefact — full name is 'Dawie Groenewald'
}

# Label → actor type mapping
LABEL_TYPE = {
    'PERSON': 'person',
    'ORG':    'institution',
    'GPE':    'location',
}

# Canonical type overrides for known entities
TYPE_OVERRIDES = {
    'botala energy':                    'institution',
    'dk superior (pty) ltd.':           'institution',
    'dk superior':                      'institution',
    'harloo private reserve':           'institution',
    'mitochondria energy':              'institution',
    'marks & spencer':                  'institution',
    'fairtrade':                        'institution',
    'seriti resource':                  'institution',
    'seriti resources':                 'institution',
    'namcor':                           'institution',
    'mongabay':                         'institution',
    'the tcheku community trust':       'institution',
    'the eswatini electricity company': 'institution',
    'the industrial development corporation': 'institution',
    'the national petroleum corporation of namibia': 'institution',
    'the national union of mineworker': 'institution',
    'the ace award':                    'institution',
    'project phoenix':                  'institution',
    'the hydrogen valley innovation hub': 'institution',
    'cookhouse windfarm':               'institution',
    'golden valley wind farm':          'institution',
    'nxuba wind farm':                  'institution',
    'the lubombo biosphere reserve':    'institution',
    'the southern african power pool':  'institution',
    'the united nations office on drugs and crime': 'institution',
    'shamukuni':                        'person',
    'chitagu':                          'person',
    'tatenda chitagu':                  'person',
    'mariah nkosi':                     'person',
    'francis flippie':                  'person',
    'vaal\'s hydrogen hub: big':        None,  # skip — artefact
}

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Materialise NER entities as actors + signal_actors links
# ════════════════════════════════════════════════════════════════════════════
log("PHASE 1 -- Materialising NER entities -> actors + signal_actors")

oxp_signal_ids = [
    r['signal_id'] for r in
    conn.execute("SELECT signal_id FROM signals WHERE source='oxpeckers'").fetchall()
]

ner_entities = conn.execute(f"""
    SELECT se.signal_id, se.text, se.label
    FROM signal_entities se
    WHERE se.signal_id IN ({','.join('?'*len(oxp_signal_ids))})
      AND se.label IN ('PERSON','ORG','GPE')
    ORDER BY se.label, se.text
""", oxp_signal_ids).fetchall()

log(f"  Found {len(ner_entities)} NER entities across Oxpeckers signals")

created_actors = 0
linked_signals = 0
skipped_noise  = 0

conn.execute("BEGIN")
for row in ner_entities:
    text  = (row['text'] or '').strip()
    label = row['label']
    sid   = row['signal_id']

    # Skip noise / artefact entities
    if text.lower() in NER_NOISE or len(text) < 4:
        skipped_noise += 1
        continue

    # Determine actor type
    actor_type = TYPE_OVERRIDES.get(text.lower(), LABEL_TYPE.get(label, 'person'))
    if actor_type is None:
        skipped_noise += 1
        continue

    # Upsert actor — INSERT OR IGNORE to avoid duplication
    conn.execute(
        "INSERT OR IGNORE INTO actors (name, type) VALUES (?, ?)",
        (text, actor_type)
    )
    actor_row = conn.execute(
        "SELECT actor_id FROM actors WHERE name=?", (text,)
    ).fetchone()
    if not actor_row:
        continue
    actor_id = actor_row['actor_id']
    if conn.execute(
        "SELECT changes()"
    ).fetchone()[0] > 0:
        created_actors += 1

    # Link signal → actor (INSERT OR IGNORE for idempotency)
    conn.execute(
        "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'mentioned')",
        (sid, actor_id)
    )
    if conn.execute("SELECT changes()").fetchone()[0] > 0:
        linked_signals += 1

conn.execute("COMMIT")
log(f"  Actors created: {created_actors} | Signal links created: {linked_signals} | Noise skipped: {skipped_noise}")

# Verify actor materialisation
materialised = conn.execute(f"""
    SELECT a.name, a.type, COUNT(DISTINCT sa.signal_id) as sigs
    FROM actors a
    JOIN signal_actors sa ON sa.actor_id = a.actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
    GROUP BY a.actor_id
    ORDER BY sigs DESC
    LIMIT 30
""").fetchall()
log(f"\n  Materialised actor roster ({len(materialised)} actors linked to Oxpeckers signals):")
for a in materialised:
    log(f"    {a['name']:<44} type={a['type']:<15} signals={a['sigs']}")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Fix Oxpecker actor type + deduplication
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 2 — Oxpecker actor deduplication and type correction")

conn.execute("BEGIN")
conn.execute("UPDATE actors SET type='institution' WHERE lower(name) LIKE '%oxpecker%'")
conn.execute("COMMIT")

oxp_actors = conn.execute(
    "SELECT actor_id, name, type FROM actors WHERE lower(name) LIKE '%oxpecker%'"
).fetchall()
for a in oxp_actors:
    log(f"  [{a['actor_id']}] {a['name']} -> type={a['type']}")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Triple extractor on enriched artifacts
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 3 -- Triple extractor on enriched Oxpeckers artifacts")

from forage.processors.triple_extractor import run_all as triple_run_all

log("  Calling triple_extractor.run_all() -- processes all relevance_score > 1.0 signals")
triple_run_all()
log("  triple_extractor.run_all() complete")

# Check edges created
er_count = conn.execute("""
    SELECT COUNT(*) FROM entity_relationships er
    JOIN actors a1 ON a1.actor_id = er.subject_actor_id
    JOIN actors a2 ON a2.actor_id = er.object_actor_id
    JOIN signal_actors sa ON sa.actor_id = er.subject_actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
""").fetchone()[0]
log(f"  Entity relationship edges involving Oxpeckers actors: {er_count}")

er_rows = conn.execute("""
    SELECT er.relation_type, er.confidence, a1.name as subject, a2.name as object
    FROM entity_relationships er
    JOIN actors a1 ON a1.actor_id = er.subject_actor_id
    JOIN actors a2 ON a2.actor_id = er.object_actor_id
    JOIN signal_actors sa ON sa.actor_id = er.subject_actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
    ORDER BY er.confidence DESC
    LIMIT 15
""").fetchall()
for r in er_rows:
    log(f"  [{r['relation_type']:<20}] conf={r['confidence']:.2f}  {r['subject'][:30]} <-> {r['object'][:30]}")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Create Case Alpha: Conservation Capture
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 4 — Initialising Case Alpha: Conservation Capture")

# Check if already exists
existing_case = conn.execute(
    "SELECT case_id FROM cases WHERE name LIKE '%Conservation Capture%'"
).fetchone()

if existing_case:
    case_id = existing_case['case_id']
    log(f"  Case Alpha already exists — case_id={case_id}")
else:
    conn.execute("BEGIN")
    cur = conn.execute("""
        INSERT INTO cases (name, description, status, hypothesis, case_type, auto_generated)
        VALUES (?, ?, 'active', ?, 'investigation', 0)
    """, (
        "Case Alpha: Conservation Capture",
        (
            "Multi-jurisdictional OSINT investigation into the alleged convergence of "
            "rhino trafficking networks (Dawie Groenewald, 1,600 charges SA), "
            "Botswana hunting concession capture via political interference (Machana Ronald "
            "Shamukuni, former justice minister), corporate opacity through DK Superior shell "
            "structure, and suspected judicial interference at the Maun High Court. "
            "Cross-referenced against NPA/Hawks (DPCI) enforcement institutions. "
            "Source: Oxpeckers Investigative Environmental Journalism (ACE Award 2025). "
            "Opened: Phase 64 Epsilon-III. Status: ACTIVE — pending triple_extractor edges "
            "and enforcement co-occurrence verification."
        ),
        (
            "Alpha Hypothesis: Groenewald's Botswana hunting concession (NG13/DK Superior) "
            "is an extension of his SA poaching/trafficking syndicate, enabled by ministerial "
            "capture of the Botswana justice system. Co-conspirators: Shamukuni (political "
            "access), DK Superior shell entities (financial opacity). Target institution: "
            "Tcheku Community Trust (victim). Cross-reference: NPA case backlog, Hawks/DPCI "
            "Groenewald docket (1,600 active charges)."
        )
    ))
    case_id = cur.lastrowid
    conn.execute("COMMIT")
    log(f"  Case Alpha created — case_id={case_id}")

# Link Oxpeckers signals to the case
groenewald_signal = conn.execute(
    "SELECT signal_id FROM signals WHERE source='oxpeckers' AND title LIKE '%Groenewald%'"
).fetchone()
zimbabwe_signal = conn.execute(
    "SELECT signal_id FROM signals WHERE source='oxpeckers' AND title LIKE '%lithium%'"
).fetchone()
panopticon_signal = conn.execute(
    "SELECT signal_id FROM signals WHERE source='oxpeckers' AND title LIKE '%panopticon%'"
).fetchone()

# Ensure case_signals table exists and link
conn.execute("""
    CREATE TABLE IF NOT EXISTS case_signals (
        case_id   INTEGER NOT NULL REFERENCES cases(case_id),
        signal_id TEXT    NOT NULL REFERENCES signals(signal_id),
        linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (case_id, signal_id)
    )
""")

priority_signals = [s for s in [groenewald_signal, zimbabwe_signal, panopticon_signal] if s]
conn.execute("BEGIN")
for sig in priority_signals:
    conn.execute(
        "INSERT OR IGNORE INTO case_signals (case_id, signal_id) VALUES (?,?)",
        (case_id, sig['signal_id'])
    )
conn.execute("COMMIT")
log(f"  Linked {len(priority_signals)} priority signals to Case Alpha")

# Link Groenewald actor to case (via case_actors)
groenewald_actor = conn.execute(
    "SELECT actor_id FROM actors WHERE name='Dawie Groenewald'"
).fetchone()
shamukuni_actor = conn.execute(
    "SELECT actor_id FROM actors WHERE name LIKE '%Shamukuni%'"
).fetchone()

conn.execute("BEGIN")
for actor in [groenewald_actor, shamukuni_actor]:
    if actor:
        conn.execute(
            "INSERT OR IGNORE INTO case_actors (case_id, actor_id) VALUES (?,?)",
            (case_id, actor['actor_id'])
        )
conn.execute("COMMIT")
log(f"  Key actors linked to Case Alpha: Groenewald={groenewald_actor['actor_id'] if groenewald_actor else 'NOT FOUND'}, Shamukuni={'found' if shamukuni_actor else 'NOT FOUND'}")

# Cross-reference Hawks/DPCI (actor_id=39)
hawks_actor = conn.execute("SELECT actor_id, name FROM actors WHERE actor_id=39").fetchone()
if hawks_actor:
    conn.execute("BEGIN")
    conn.execute(
        "INSERT OR IGNORE INTO case_actors (case_id, actor_id) VALUES (?,?)",
        (case_id, 39)
    )
    conn.execute("COMMIT")
    log(f"  Hawks/DPCI [{hawks_actor['actor_id']}] {hawks_actor['name']} cross-referenced to Case Alpha")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 5 — Re-run Conclave with tuned kernel
# ════════════════════════════════════════════════════════════════════════════
log("\nPHASE 5 — Conclave re-score with tuned investigative kernel")

from core.pipeline.ingest import ingest_signal

oxp_signals = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()

results_table = []
processed = errors = escalated = flagged = 0

for row in oxp_signals:
    sig = dict(row)
    try:
        result   = ingest_signal(sig)
        processed += 1
        decision = result.get('case', {}).get('decision', 'unknown')
        gravity  = result.get('gravity_signal', {}).get('gravity_score', 0)
        sev      = result.get('interpreted_signal', {}).get('severity', 0)
        cred     = result.get('interpreted_signal', {}).get('source_credibility', 0)
        results_table.append((gravity, decision, sev, cred, sig['title']))
        if gravity >= 0.8:
            escalated += 1
            log(f"  [CREATE CASE ] grav={gravity:.3f} sev={sev:.2f}  {sig['title'][:65]}")
        elif gravity >= 0.6:
            flagged += 1
            log(f"  [FLAG MONITOR] grav={gravity:.3f} sev={sev:.2f}  {sig['title'][:65]}")
        else:
            log(f"  [{decision:<12}] grav={gravity:.3f} sev={sev:.2f}  {sig['title'][:55]}")
    except Exception as e:
        errors += 1
        log(f"  ERROR: {e} | {sig['title'][:40]}")

log(f"\n  Conclave: {processed} processed | {errors} errors | {flagged} flagged | {escalated} escalated")


# ════════════════════════════════════════════════════════════════════════════
# PHASE 6 — Samaritan Assessment: Final Gravity League Table
# ════════════════════════════════════════════════════════════════════════════
log("\n" + "="*70)
log("SAMARITAN ASSESSMENT — Phase 64 Epsilon-III")
log("="*70)

log("\n  === GRAVITY LEAGUE TABLE (tuned investigative kernel) ===")
final = conn.execute("""
    SELECT title, gravity_score, relevance_score
    FROM signals WHERE source='oxpeckers'
    ORDER BY gravity_score DESC NULLS LAST
""").fetchall()

for r in final:
    g = r['gravity_score'] or 0
    bar = '#' * int(g * 20)
    if g >= 0.8:
        flag = ' <<< CREATE CASE'
    elif g >= 0.6:
        flag = ' <<< FLAG MONITOR'
    elif g >= 0.35:
        flag = ' [monitored]'
    else:
        flag = ''
    log(f"  {g:.3f} |{bar:<16}|{flag}  {r['title'][:60]}")

log("\n  === ACTOR GRAPH (post-materialisation) ===")
actor_graph = conn.execute("""
    SELECT a.actor_id, a.name, a.type,
           COUNT(DISTINCT sa.signal_id) as sigs
    FROM actors a
    JOIN signal_actors sa ON sa.actor_id = a.actor_id
    JOIN signals s ON s.signal_id = sa.signal_id
    WHERE s.source = 'oxpeckers'
    GROUP BY a.actor_id
    ORDER BY sigs DESC
    LIMIT 25
""").fetchall()
log(f"  {len(actor_graph)} actors linked to Oxpeckers investigation:")
for a in actor_graph:
    log(f"    [{a['actor_id']:>4}] {a['name']:<42} type={a['type']:<15} sigs={a['sigs']}")

log("\n  === ENTITY RELATIONSHIPS (post triple_extractor) ===")
all_actor_ids = [a['actor_id'] for a in actor_graph]
if all_actor_ids:
    ph = ','.join('?'*len(all_actor_ids))
    er_final = conn.execute(f"""
        SELECT er.relation_type, er.confidence,
               a1.name as subject, a2.name as object
        FROM entity_relationships er
        JOIN actors a1 ON a1.actor_id = er.subject_actor_id
        JOIN actors a2 ON a2.actor_id = er.object_actor_id
        WHERE er.subject_actor_id IN ({ph}) OR er.object_actor_id IN ({ph})
        ORDER BY er.confidence DESC LIMIT 20
    """, all_actor_ids + all_actor_ids).fetchall()
    if er_final:
        log(f"  {len(er_final)} edges found:")
        for r in er_final:
            log(f"    [{r['relation_type']:<20}] conf={r['confidence']:.2f}  "
                f"{r['subject'][:30]} <-> {r['object'][:30]}")
    else:
        log("  No edges yet — triple_extractor requires actor names to appear in signal text")

log("\n  === ENFORCEMENT CROSS-REFERENCE ===")
enforcement = {24: 'NPA', 39: 'Hawks/DPCI', 43: 'ANC', 60: 'NPA (abbrev)', 63: 'ANC (alt)'}
for eid, ename in enforcement.items():
    if not all_actor_ids:
        break
    ph = ','.join('?'*len(all_actor_ids))
    shared = conn.execute(f"""
        SELECT COUNT(DISTINCT s.signal_id) as c
        FROM signals s
        JOIN signal_actors sa1 ON sa1.signal_id = s.signal_id
        JOIN signal_actors sa2 ON sa2.signal_id = s.signal_id
        WHERE sa1.actor_id IN ({ph}) AND sa2.actor_id = ?
    """, all_actor_ids + [eid]).fetchone()
    if shared and shared['c'] > 0:
        log(f"  LINK FOUND: {shared['c']} signal(s) co-occur with [{eid}] {ename}")

log("\n  === CASE ALPHA STATUS ===")
case_row = conn.execute(
    "SELECT case_id, name, status, created_at FROM cases WHERE case_id=?", (case_id,)
).fetchone()
sig_count = conn.execute(
    "SELECT COUNT(*) FROM case_signals WHERE case_id=?", (case_id,)
).fetchone()[0]
actor_count = conn.execute(
    "SELECT COUNT(*) FROM case_actors WHERE case_id=?", (case_id,)
).fetchone()[0]
log(f"  case_id   : {case_row['case_id']}")
log(f"  name      : {case_row['name']}")
log(f"  status    : {case_row['status']}")
log(f"  created_at: {case_row['created_at']}")
log(f"  signals   : {sig_count} linked")
log(f"  actors    : {actor_count} linked (incl. Hawks/DPCI cross-reference)")

log("\n  === INVESTIGATIVE SCORING VERIFICATION ===")
# Show what the new interpreter extracts for the Groenewald signal
from forage.processors.signal_interpreter import SignalInterpreter
si = SignalInterpreter()
groe_sig = conn.execute(
    "SELECT * FROM signals WHERE source='oxpeckers' AND title LIKE '%Groenewald%'"
).fetchone()
if groe_sig:
    interpreted = si.interpret(dict(groe_sig))
    log(f"  Groenewald signal — tuned interpreter output:")
    log(f"    severity           : {interpreted['severity']}")
    log(f"    actor_importance   : {interpreted['actor_importance']}")
    log(f"    frequency          : {interpreted['frequency']}")
    log(f"    source_credibility : {interpreted['source_credibility']}")
    log(f"    event_type         : {interpreted['event_type']}")

conn.close()
log("\nPhase 64 Epsilon-III complete.")
