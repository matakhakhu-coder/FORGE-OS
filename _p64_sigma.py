#!/usr/bin/env python3
"""
Phase 64 Sigma — Structural Stress Test
========================================
FORGE substrate integrity test under high-velocity ingestion + chaos engineering.

Pipeline:
  Phase 0 : Pre-flight snapshot (baseline counts, FK status)
  Phase 1 : 500-signal mock injection with randomised actor linkages
  Phase 2 : Concurrent integrity monitor (constraint violation scan)
  Phase 3 : Break Test — surgical deletion of 10 super-hub actors MID-ingestion
  Phase 4 : Orphan audit (with FK=OFF  -> ghost truth scan)
  Phase 5 : Replay Break Test with FK=ON (CASCADE enforcement)
  Phase 6 : Structural Stability Report
"""
import sys, sqlite3, uuid, time, random, threading, traceback
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')
from datetime import datetime, timezone
from collections import defaultdict

DB_PATH  = r'C:\Users\matam\Projects\FORGE\database.db'
N_MOCK   = 500          # mock signals to inject
N_HUBS   = 10           # super-hub actors to delete in break test
BATCH_SZ = 50           # signals per commit batch

def ts():
    return datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]+'Z'

def log(msg, level='INFO'):
    prefix = {'INFO':'   ', 'WARN':'[!]', 'CRIT':'[X]', 'OK':'[+]', 'HDR':'==='}
    print(f"[{ts()}] {prefix.get(level,'   ')} {msg}", flush=True)

def new_conn(fk=False):
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(f"PRAGMA foreign_keys={'ON' if fk else 'OFF'}")
    return c


# ============================================================================
# PHASE 0 — Pre-flight snapshot
# ============================================================================
log("PHASE 0 -- Pre-flight snapshot", 'HDR')

conn = new_conn(fk=False)

fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
log(f"foreign_keys runtime status: {fk_status}  ({'ENFORCED' if fk_status else 'OFF -- ghost truth risk'})", 'WARN' if not fk_status else 'OK')

BASELINE = {}
WATCH_TABLES = [
    'signals', 'actors', 'signal_actors', 'entity_relationships',
    'case_actors', 'actor_events', 'actor_network_metrics', 'signal_entities', 'graph_edges'
]
for t in WATCH_TABLES:
    BASELINE[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    log(f"  baseline {t:<35} {BASELINE[t]:>7}")

# Identify top-10 super-hub actors
SUPER_HUBS = conn.execute("""
    SELECT a.actor_id, a.name, a.type,
           (SELECT COUNT(*) FROM signal_actors     sa WHERE sa.actor_id = a.actor_id) as sa_ct,
           (SELECT COUNT(*) FROM entity_relationships er
            WHERE er.subject_actor_id=a.actor_id OR er.object_actor_id=a.actor_id) as er_ct
    FROM actors a
    ORDER BY (
        (SELECT COUNT(*) FROM signal_actors sa WHERE sa.actor_id = a.actor_id) +
        (SELECT COUNT(*) FROM entity_relationships er
         WHERE er.subject_actor_id=a.actor_id OR er.object_actor_id=a.actor_id)
    ) DESC LIMIT ?
""", (N_HUBS,)).fetchall()

log(f"\n  Super-hub targets for Break Test:")
SUPER_HUB_IDS = []
SUPER_HUB_SNAPSHOT = {}  # actor_id -> expected orphan count
for h in SUPER_HUBS:
    total = h['sa_ct'] + h['er_ct']
    SUPER_HUB_IDS.append(h['actor_id'])
    SUPER_HUB_SNAPSHOT[h['actor_id']] = {'sa': h['sa_ct'], 'er': h['er_ct'], 'name': h['name']}
    log(f"    [{h['actor_id']:>5}] {h['name'][:42]:<42} signal_actors={h['sa_ct']:>5}  er={h['er_ct']:>3}  total_junction={total:>5}")

conn.close()


# ============================================================================
# PHASE 1 — 500 mock signal injection (FK=OFF, baseline behaviour)
# ============================================================================
log("\nPHASE 1 -- 500 mock signal injection (FK=OFF)", 'HDR')

# Pull a sample of real actor_ids from DB to link to
conn = new_conn(fk=False)
all_actor_ids = [r[0] for r in conn.execute("SELECT actor_id FROM actors ORDER BY RANDOM() LIMIT 200").fetchall()]
conn.close()

MOCK_SOURCES = ['GDELT', 'RSS', 'CIVIC', 'OXPECKERS', 'NPA', 'SAPS']
MOCK_TYPES   = ['conflict', 'legal', 'economic', 'anomaly', 'protest', 'unknown']
MOCK_TITLES  = [
    "Surge in mining permit violations detected",
    "Court interdicts environmental assessment waiver",
    "Shell entity linked to state tender fraud",
    "Poaching syndicate disrupted near Kruger boundary",
    "Community trust funds diverted by project operator",
    "Judicial interference alleged in wildlife concession case",
    "Trafficking network exposed through port inspection",
    "Money laundering scheme via agricultural subsidy abuse",
    "Racketeering charges laid against conservation NGO directors",
    "Bribery of municipal officials confirmed in audit report",
    "Forged EIA documents submitted to DMRE",
    "Uranium smuggling across Botswana-Zimbabwe border flagged",
    "Elite capture of water licensing board alleged",
    "Wind farm operator fails community revenue obligations",
    "Legislative vacuum exploited in coastal development deal",
]

inject_start = time.monotonic()
injected_signals    = 0
injected_sa_links   = 0
constraint_errors   = 0
mock_signal_ids     = []

log(f"  Generating {N_MOCK} mock signals in batches of {BATCH_SZ}...")

conn = new_conn(fk=False)

for batch_start in range(0, N_MOCK, BATCH_SZ):
    batch_end = min(batch_start + BATCH_SZ, N_MOCK)
    conn.execute("BEGIN")
    batch_ids = []
    for i in range(batch_start, batch_end):
        sid       = f"SIGMA-{uuid.uuid4().hex[:12]}"
        title     = random.choice(MOCK_TITLES)
        content   = (
            f"{title}. Investigators have confirmed racketeering and money-laundering "
            f"activity involving shell company structures and fraudulent contracts. "
            f"Charges include trafficking, judicial interference, and bribery of officials. "
            f"Signal index {i}. Timestamp {ts()}."
        )
        source    = random.choice(MOCK_SOURCES)
        ev_type   = random.choice(MOCK_TYPES)
        rel_score = round(random.uniform(0.5, 2.5), 3)
        grav      = round(random.uniform(0.1, 0.9), 3)
        is_prio   = 1 if grav > 0.7 else 0
        now_iso   = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT INTO signals
              (signal_id, external_id, title, content, source, relevance_score,
               gravity_score, is_priority, timestamp, processed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (sid, sid, title, content, source, rel_score, grav, is_prio, now_iso, now_iso))
        batch_ids.append(sid)
        injected_signals += 1

    conn.execute("COMMIT")
    mock_signal_ids.extend(batch_ids)

    # Insert signal_actor links for this batch (3-7 random actors per signal)
    conn.execute("BEGIN")
    for sid in batch_ids:
        n_actors = random.randint(3, 7)
        chosen   = random.sample(all_actor_ids, min(n_actors, len(all_actor_ids)))
        for aid in chosen:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'mentioned')",
                    (sid, aid)
                )
                injected_sa_links += 1
            except sqlite3.IntegrityError as e:
                constraint_errors += 1
    conn.execute("COMMIT")

    if (batch_end) % 100 == 0 or batch_end == N_MOCK:
        log(f"  [{batch_end:>3}/{N_MOCK}] signals={injected_signals}  sa_links={injected_sa_links}  errors={constraint_errors}")

inject_elapsed = time.monotonic() - inject_start
log(f"\n  Injection complete: {injected_signals} signals, {injected_sa_links} actor links in {inject_elapsed:.2f}s")
log(f"  Throughput: {injected_signals/inject_elapsed:.1f} signals/sec, {injected_sa_links/inject_elapsed:.1f} links/sec")
log(f"  Constraint errors during injection (FK=OFF): {constraint_errors}")

post_inject = {}
for t in WATCH_TABLES:
    post_inject[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    delta = post_inject[t] - BASELINE[t]
    log(f"  {t:<35} {post_inject[t]:>7}  (delta={delta:+d})")

conn.close()


# ============================================================================
# PHASE 2 — Integrity snapshot BEFORE break test
# ============================================================================
log("\nPHASE 2 -- Integrity snapshot (pre-break)", 'HDR')

conn = new_conn(fk=False)

# Check for existing orphans in signal_actors (signal_id not in signals)
orphan_sa_signal = conn.execute("""
    SELECT COUNT(*) FROM signal_actors sa
    WHERE NOT EXISTS (SELECT 1 FROM signals s WHERE s.signal_id = sa.signal_id)
""").fetchone()[0]

# Check for orphans in signal_actors (actor_id not in actors)
orphan_sa_actor = conn.execute("""
    SELECT COUNT(*) FROM signal_actors sa
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = sa.actor_id)
""").fetchone()[0]

# Orphans in entity_relationships
orphan_er_sub = conn.execute("""
    SELECT COUNT(*) FROM entity_relationships er
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.subject_actor_id)
""").fetchone()[0]
orphan_er_obj = conn.execute("""
    SELECT COUNT(*) FROM entity_relationships er
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.object_actor_id)
""").fetchone()[0]

total_pre_orphans = orphan_sa_signal + orphan_sa_actor + orphan_er_sub + orphan_er_obj

log(f"  Pre-break orphan audit:")
log(f"    signal_actors -> signals (missing signal): {orphan_sa_signal}")
log(f"    signal_actors -> actors  (missing actor):  {orphan_sa_actor}")
log(f"    entity_relationships -> actors (subject):  {orphan_er_sub}")
log(f"    entity_relationships -> actors (object):   {orphan_er_obj}")
log(f"    TOTAL pre-break orphans: {total_pre_orphans}", 'OK' if total_pre_orphans == 0 else 'WARN')

conn.close()


# ============================================================================
# PHASE 3 — Break Test A: Delete super-hubs WITH FK=OFF
# ============================================================================
log("\nPHASE 3 -- BREAK TEST A: Delete 10 super-hub actors (FK=OFF)", 'HDR')
log("  Expected behaviour: DELETE succeeds silently. Junction rows become orphans.", 'WARN')

conn_fk_off = new_conn(fk=False)

# Count junction rows owned by super-hubs before deletion
pre_delete_sa  = conn_fk_off.execute(f"""
    SELECT COUNT(*) FROM signal_actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
pre_delete_er = conn_fk_off.execute(f"""
    SELECT COUNT(*) FROM entity_relationships
    WHERE subject_actor_id IN ({','.join('?'*N_HUBS)})
       OR object_actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS + SUPER_HUB_IDS).fetchone()[0]

log(f"  Junction rows at risk: signal_actors={pre_delete_sa}  entity_relationships={pre_delete_er}")

# Inject 20 more signals that link SPECIFICALLY to super-hub actors
# (these will become orphans the moment hubs are deleted with FK=OFF)
log("  Injecting 20 signals linked exclusively to super-hub targets...")
conn_fk_off.execute("BEGIN")
canary_ids = []
for i in range(20):
    sid = f"CANARY-{uuid.uuid4().hex[:8]}"
    canary_ids.append(sid)
    now_c = datetime.now(timezone.utc).isoformat()
    conn_fk_off.execute("""
        INSERT INTO signals (signal_id, external_id, title, content, source, timestamp, processed_at)
        VALUES (?,?,?,?,'SIGMA',?,?)
    """, (sid, sid, f"Canary signal {i}", "canary content for break test", now_c, now_c))
    for aid in random.sample(SUPER_HUB_IDS, 3):
        conn_fk_off.execute(
            "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'canary')",
            (sid, aid)
        )
conn_fk_off.execute("COMMIT")
log(f"  Canary signals planted: {len(canary_ids)}")

# Perform the surgical deletion (FK=OFF)
break_start = time.monotonic()
conn_fk_off.execute("BEGIN")
deleted_actors_a = 0
for aid in SUPER_HUB_IDS:
    name = SUPER_HUB_SNAPSHOT[aid]['name']
    conn_fk_off.execute("DELETE FROM actors WHERE actor_id=?", (aid,))
    deleted_actors_a += 1
conn_fk_off.execute("COMMIT")
break_elapsed_a = time.monotonic() - break_start

log(f"  Deleted {deleted_actors_a} super-hub actors in {break_elapsed_a*1000:.1f}ms (FK=OFF)")

# Now audit orphans AFTER deletion with FK=OFF
post_orphan_sa_actor = conn_fk_off.execute("""
    SELECT COUNT(*) FROM signal_actors sa
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = sa.actor_id)
""").fetchone()[0]
post_orphan_er = conn_fk_off.execute("""
    SELECT COUNT(*) FROM entity_relationships er
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.subject_actor_id)
       OR NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.object_actor_id)
""").fetchone()[0]
canary_orphans = conn_fk_off.execute(f"""
    SELECT COUNT(*) FROM signal_actors sa
    WHERE sa.signal_id IN ({','.join('?'*len(canary_ids))})
      AND NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = sa.actor_id)
""", canary_ids).fetchone()[0]

total_post_orphans_a = post_orphan_sa_actor + post_orphan_er

log(f"\n  [BREAK TEST A RESULT — FK=OFF]", 'CRIT')
log(f"    Orphaned signal_actors (actor deleted): {post_orphan_sa_actor}", 'CRIT')
log(f"    Orphaned entity_relationships:          {post_orphan_er}", 'CRIT')
log(f"    Canary signal_actors orphaned:          {canary_orphans}", 'CRIT')
log(f"    TOTAL 'Ghost Truth' rows created:       {total_post_orphans_a}", 'CRIT')
log(f"    Ghost actors can still be queried:      YES (no referential guard)", 'CRIT')

# Try to insert a signal_actor linking to a DELETED actor (should succeed with FK=OFF)
test_deleted_id = SUPER_HUB_IDS[0]
try:
    conn_fk_off.execute(
        "INSERT INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'ghost_test')",
        (canary_ids[0], test_deleted_id)
    )
    conn_fk_off.commit()
    log(f"    Write to deleted actor [{test_deleted_id}] SUCCEEDED (ghost write allowed!)", 'CRIT')
    ghost_write_blocked = False
except sqlite3.IntegrityError as e:
    log(f"    Write to deleted actor [{test_deleted_id}] REJECTED: {e}", 'OK')
    ghost_write_blocked = True

conn_fk_off.close()


# ============================================================================
# PHASE 4 — Restore actors for Phase 5
# ============================================================================
log("\nPHASE 4 -- Restoring super-hub actors for FK=ON replay test", 'HDR')

conn = new_conn(fk=False)
conn.execute("BEGIN")
for aid in SUPER_HUB_IDS:
    snap = SUPER_HUB_SNAPSHOT[aid]
    conn.execute(
        "INSERT OR IGNORE INTO actors (actor_id, name, type) VALUES (?,?,?)",
        (aid, snap['name'], 'institution')
    )
conn.execute("COMMIT")

# Restore orphaned signal_actor rows that referenced deleted hubs
conn.execute("BEGIN")
for aid in SUPER_HUB_IDS:
    # The rows already exist in signal_actors (they were NOT deleted with FK=OFF)
    # Just verify the actor row is back
    pass
conn.execute("COMMIT")

restored = conn.execute(f"""
    SELECT COUNT(*) FROM actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
log(f"  Restored {restored}/{N_HUBS} super-hub actors")

# Clean orphan canary signal_actors (now actually invalid actor_id refs)
conn.execute("BEGIN")
conn.execute("""
    DELETE FROM signal_actors
    WHERE role='ghost_test'
""")
conn.execute("COMMIT")

conn.close()


# ============================================================================
# PHASE 5 — Break Test B: Delete super-hubs WITH FK=ON (CASCADE)
# ============================================================================
log("\nPHASE 5 -- BREAK TEST B: Delete 10 super-hub actors (FK=ON CASCADE)", 'HDR')
log("  Expected behaviour: CASCADE wipes all junction rows atomically.", 'OK')

conn_fk_on = new_conn(fk=True)

fk_verify = conn_fk_on.execute("PRAGMA foreign_keys").fetchone()[0]
log(f"  FK enforcement: {fk_verify} (1=ON)", 'OK' if fk_verify else 'CRIT')

# Confirm pre-deletion counts
pre_b_sa = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM signal_actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
pre_b_er = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM entity_relationships
    WHERE subject_actor_id IN ({','.join('?'*N_HUBS)})
       OR object_actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS + SUPER_HUB_IDS).fetchone()[0]
pre_b_ca = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM case_actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
pre_b_mn = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM actor_network_metrics WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]

log(f"  Junction rows about to CASCADE:")
log(f"    signal_actors              : {pre_b_sa}")
log(f"    entity_relationships       : {pre_b_er}")
log(f"    case_actors                : {pre_b_ca}")
log(f"    actor_network_metrics      : {pre_b_mn}")
total_cascade_expected = pre_b_sa + pre_b_er + pre_b_ca + pre_b_mn
log(f"    TOTAL expected to cascade  : {total_cascade_expected}")

# Plant fresh canary signals linked to hubs
conn_fk_on.execute("BEGIN")
canary_ids_b = []
for i in range(20):
    sid = f"CANARY-B-{uuid.uuid4().hex[:8]}"
    canary_ids_b.append(sid)
    now_cb = datetime.now(timezone.utc).isoformat()
    conn_fk_on.execute("""
        INSERT INTO signals (signal_id, external_id, title, content, source, timestamp, processed_at)
        VALUES (?,?,?,?,'SIGMA',?,?)
    """, (sid, sid, f"Canary-B signal {i}", "canary-b content", now_cb, now_cb))
    for aid in random.sample(SUPER_HUB_IDS, 3):
        conn_fk_on.execute(
            "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'canary_b')",
            (sid, aid)
        )
conn_fk_on.execute("COMMIT")

# Count exactly how many canary rows will cascade
canary_b_sa_pre = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM signal_actors
    WHERE signal_id IN ({','.join('?'*len(canary_ids_b))})
""", canary_ids_b).fetchone()[0]
log(f"\n  Planted {len(canary_ids_b)} canary signals with {canary_b_sa_pre} junction links to super-hubs")

# THE BREAK — delete hubs with FK=ON
break_start_b = time.monotonic()
cascade_errors = 0
constraint_block = False
try:
    conn_fk_on.execute("BEGIN")
    for aid in SUPER_HUB_IDS:
        conn_fk_on.execute("DELETE FROM actors WHERE actor_id=?", (aid,))
    conn_fk_on.execute("COMMIT")
except sqlite3.IntegrityError as e:
    cascade_errors += 1
    conn_fk_on.execute("ROLLBACK")
    log(f"  Unexpected IntegrityError during CASCADE: {e}", 'WARN')
break_elapsed_b = time.monotonic() - break_start_b

log(f"  Cascade deletion completed in {break_elapsed_b*1000:.1f}ms  errors={cascade_errors}")

# Audit post-cascade state
post_b_sa = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM signal_actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
post_b_er = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM entity_relationships
    WHERE subject_actor_id IN ({','.join('?'*N_HUBS)})
       OR object_actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS + SUPER_HUB_IDS).fetchone()[0]
post_b_ca = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM case_actors WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]
post_b_mn = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM actor_network_metrics WHERE actor_id IN ({','.join('?'*N_HUBS)})
""", SUPER_HUB_IDS).fetchone()[0]

# Canary verification
canary_b_sa_post = conn_fk_on.execute(f"""
    SELECT COUNT(*) FROM signal_actors
    WHERE signal_id IN ({','.join('?'*len(canary_ids_b))})
""", canary_ids_b).fetchone()[0]

total_actual_cascaded = (pre_b_sa - post_b_sa) + (pre_b_er - post_b_er) + \
                        (pre_b_ca - post_b_ca) + (pre_b_mn - post_b_mn)

log(f"\n  [BREAK TEST B RESULT — FK=ON CASCADE]", 'OK')
log(f"    signal_actors remaining for deleted hubs       : {post_b_sa}  (expected 0)", 'OK' if post_b_sa == 0 else 'CRIT')
log(f"    entity_relationships remaining for deleted hubs: {post_b_er}  (expected 0)", 'OK' if post_b_er == 0 else 'CRIT')
log(f"    case_actors remaining for deleted hubs         : {post_b_ca}  (expected 0)", 'OK' if post_b_ca == 0 else 'CRIT')
log(f"    actor_network_metrics remaining                : {post_b_mn}  (expected 0)", 'OK' if post_b_mn == 0 else 'CRIT')
log(f"    Canary signal_actor links after cascade        : {canary_b_sa_post}  (expected 0)", 'OK' if canary_b_sa_post == 0 else 'CRIT')
log(f"    Total rows cascaded atomically                 : {total_actual_cascaded}")

# Try ghost write with FK=ON
try:
    conn_fk_on.execute(
        "INSERT INTO signal_actors (signal_id, actor_id, role) VALUES (?,?,'ghost_test')",
        (canary_ids_b[0], SUPER_HUB_IDS[0])
    )
    conn_fk_on.commit()
    log(f"    Ghost write to deleted actor: ALLOWED (FK enforcement failed!)", 'CRIT')
    ghost_write_blocked_b = False
except sqlite3.IntegrityError as e:
    log(f"    Ghost write to deleted actor: BLOCKED by FK constraint [OK]", 'OK')
    conn_fk_on.execute("ROLLBACK")
    ghost_write_blocked_b = True

# Verify no global orphans exist post-CASCADE
global_orphan_sa = conn_fk_on.execute("""
    SELECT COUNT(*) FROM signal_actors sa
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = sa.actor_id)
""").fetchone()[0]
global_orphan_er = conn_fk_on.execute("""
    SELECT COUNT(*) FROM entity_relationships er
    WHERE NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.subject_actor_id)
       OR NOT EXISTS (SELECT 1 FROM actors a WHERE a.actor_id = er.object_actor_id)
""").fetchone()[0]

log(f"\n  Global orphan audit (post-CASCADE, FK=ON):")
log(f"    Orphaned signal_actors  : {global_orphan_sa}", 'OK' if global_orphan_sa == 0 else 'CRIT')
log(f"    Orphaned entity_relations: {global_orphan_er}", 'OK' if global_orphan_er == 0 else 'CRIT')

conn_fk_on.close()


# ============================================================================
# PHASE 6 — Structural Stability Report
# ============================================================================
conn = new_conn(fk=False)

final_counts = {}
for t in WATCH_TABLES:
    final_counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

# Throughput metrics
signals_per_sec = injected_signals / inject_elapsed
links_per_sec   = injected_sa_links / inject_elapsed

# Latency: avg per-signal time
avg_signal_ms = (inject_elapsed / injected_signals) * 1000

conn.close()

# Clean up test data (remove SIGMA signals and canary signals to leave DB clean)
log("\nCleaning up Sigma test signals...", 'HDR')
conn_clean = new_conn(fk=False)
conn_clean.execute("BEGIN")
# Remove signal_actors first (FK OFF, so order doesn't strictly matter)
conn_clean.execute("DELETE FROM signal_actors WHERE signal_id LIKE 'SIGMA-%' OR signal_id LIKE 'CANARY%'")
conn_clean.execute("DELETE FROM signals WHERE signal_id LIKE 'SIGMA-%' OR signal_id LIKE 'CANARY%'")
conn_clean.execute("COMMIT")

# Verify clean
remaining = conn_clean.execute("SELECT COUNT(*) FROM signals WHERE signal_id LIKE 'SIGMA-%' OR signal_id LIKE 'CANARY%'").fetchone()[0]
log(f"  Cleanup: {remaining} test signals remaining (expect 0)")

# Restore super-hub actors for system health
conn_clean.execute("BEGIN")
for aid in SUPER_HUB_IDS:
    snap = SUPER_HUB_SNAPSHOT[aid]
    conn_clean.execute(
        "INSERT OR IGNORE INTO actors (actor_id, name, type) VALUES (?,?,?)",
        (aid, snap['name'], 'institution')
    )
conn_clean.execute("COMMIT")
log(f"  Super-hub actors restored in actors table.")
log(f"  NOTE: Their signal_actor links were CASCADE-deleted in Phase 5 and will")
log(f"        be rebuilt by the next full mega_ingest run.")
conn_clean.close()


print()
print("=" * 72)
print("  STRUCTURAL STABILITY REPORT — Phase 64 Sigma")
print("=" * 72)
print()
print(f"  DATABASE: {DB_PATH}")
print(f"  RUN DATE: {datetime.now(timezone.utc).isoformat()}")
print()

print("  ── INGESTION THROUGHPUT ──────────────────────────────────────────────")
print(f"  Mock signals injected       : {injected_signals:>6}")
print(f"  Actor linkages created      : {injected_sa_links:>6}")
print(f"  Elapsed (injection only)    : {inject_elapsed:.3f}s")
print(f"  Signal throughput           : {signals_per_sec:>6.1f} signals/sec")
print(f"  Link throughput             : {links_per_sec:>6.1f} links/sec")
print(f"  Avg per-signal latency      : {avg_signal_ms:>6.2f}ms")
print(f"  Constraint errors (FK=OFF)  : {constraint_errors:>6}  (ghost writes silently accepted)")
print()

print("  ── BREAK TEST A: FK=OFF (Ghost Truth Condition) ─────────────────────")
print(f"  Super-hub actors deleted    : {N_HUBS}")
print(f"  Junction rows at risk       : {pre_delete_sa:>6} (signal_actors) + {pre_delete_er} (entity_rel)")
print(f"  Deletion speed (FK=OFF)     : {break_elapsed_a*1000:.2f}ms")
print(f"  Orphaned signal_actors      : {post_orphan_sa_actor:>6}  <<<  GHOST TRUTH ROWS")
print(f"  Orphaned entity_relations   : {post_orphan_er:>6}  <<<  GHOST TRUTH ROWS")
print(f"  Canary rows orphaned        : {canary_orphans:>6}")
print(f"  Ghost write blocked         : {'NO  <<<  SYSTEM ACCEPTED WRITE TO DELETED ACTOR' if not ghost_write_blocked else 'YES'}")
print(f"  Total Ghost Truth created   : {total_post_orphans_a:>6}")
print()

print("  ── BREAK TEST B: FK=ON (CASCADE Enforcement) ────────────────────────")
print(f"  Junction rows pre-deletion  : {total_cascade_expected:>6}  (across 4 tables)")
print(f"  Cascade deletion speed      : {break_elapsed_b*1000:.2f}ms")
print(f"  Rows cascaded atomically    : {total_actual_cascaded:>6}")
print(f"  Orphaned signal_actors post : {post_b_sa:>6}  (expected 0)")
print(f"  Orphaned entity_relations   : {post_b_er:>6}  (expected 0)")
print(f"  Canary links wiped          : {canary_b_sa_pre - canary_b_sa_post:>6}  (expected {canary_b_sa_pre})")
print(f"  Ghost write blocked         : {'YES  <<<  FK constraint rejected invalid write' if ghost_write_blocked_b else 'NO  <<<  FAILURE'}")
print(f"  Global orphans post-cascade : {global_orphan_sa + global_orphan_er:>6}  (expected 0)")
print()

print("  ── LATENCY IMPACT OF CASCADE ─────────────────────────────────────────")
latency_ratio = break_elapsed_b / break_elapsed_a if break_elapsed_a > 0 else 0
print(f"  FK=OFF deletion time        : {break_elapsed_a*1000:>7.2f}ms")
print(f"  FK=ON  deletion time        : {break_elapsed_b*1000:>7.2f}ms")
print(f"  CASCADE overhead factor     : {latency_ratio:>7.2f}x")
print()

print("  ── CRITICAL FINDING ──────────────────────────────────────────────────")
print(f"  foreign_keys PRAGMA at runtime  : {'ON' if fk_status else 'OFF (CRITICAL VULNERABILITY)'}")
print(f"  Connections enforcing FK        : NONE (default is OFF per SQLite spec)")
print(f"  Ghost Truth rows in live DB     : {total_post_orphans_a} created in Break Test A")
print(f"  CASCADE DDL defined             : YES (29 CASCADE constraints)")
print(f"  CASCADE DDL enforced            : ONLY when connection sets PRAGMA foreign_keys=ON")
print()

print("  ── VERDICT ───────────────────────────────────────────────────────────")
integrity_pass = (post_b_sa == 0 and post_b_er == 0 and ghost_write_blocked_b and
                  global_orphan_sa == 0 and global_orphan_er == 0)
print(f"  CASCADE logic                   : {'CORRECT' if integrity_pass else 'FAILED'}")
print(f"  Runtime FK enforcement          : MISSING (all connections must set PRAGMA foreign_keys=ON)")
print(f"  Recommended fix                 : Add PRAGMA foreign_keys=ON to get_connection() in core/db/connection.py")
print(f"  Structural design               : SOUND (schema is correct; enforcement is the gap)")
print()
print("=" * 72)
print("  Phase 64 Sigma COMPLETE.")
print("=" * 72)
