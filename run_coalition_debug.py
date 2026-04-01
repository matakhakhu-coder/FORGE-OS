"""
Run coalition detection directly (bypassing Flask) and print full output.
Place in FORGE root and run: python run_coalition_debug.py
"""
import sqlite3
from collections import defaultdict
from itertools import combinations

DB = "database.db"
THRESHOLD = 2

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Step 1: load links using the fixed UNION ALL + DISTINCT query
rows = conn.execute("""
    SELECT DISTINCT actor_id, event_id FROM (
        SELECT actor_id, event_id FROM actor_events
        UNION ALL
        SELECT actor_id, event_id FROM event_actors
    )
""").fetchall()

print(f"Total actor-event links loaded: {len(rows)}")

# Step 2: build event->actors map
event_actors = defaultdict(set)
for r in rows:
    event_actors[r["event_id"]].add(r["actor_id"])

print(f"Events with at least 1 actor: {len(event_actors)}")
multi = {e: a for e, a in event_actors.items() if len(a) > 1}
print(f"Events with 2+ actors: {len(multi)}")
for eid, actors in list(multi.items())[:5]:
    print(f"  event {eid}: actors {sorted(actors)}")

# Step 3: count pair co-occurrences
pair_counts = defaultdict(int)
for actors_in_event in event_actors.values():
    for a, b in combinations(sorted(actors_in_event), 2):
        pair_counts[(a, b)] += 1

print(f"\nTotal pairs found: {len(pair_counts)}")
qualifying = {p: c for p, c in pair_counts.items() if c >= THRESHOLD}
print(f"Pairs above threshold={THRESHOLD}: {len(qualifying)}")
for pair, count in sorted(qualifying.items(), key=lambda x: -x[1])[:10]:
    print(f"  actors {pair[0]}+{pair[1]}: {count} shared events")

conn.close()

# Step 4: run the actual engine
print("\n--- Running engine ---")
import sys
sys.path.insert(0, ".")
from forge_modules.coalition_detector.engine import run
result = run(threshold=THRESHOLD)
print(f"Result: {result}")