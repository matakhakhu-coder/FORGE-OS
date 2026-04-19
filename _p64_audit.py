import sqlite3, json, sys
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')

conn = sqlite3.connect(r'C:\Users\matam\Projects\FORGE\database.db')
conn.row_factory = sqlite3.Row

# Metadata and content of Oxpeckers signals
print("=== OXPECKERS SIGNAL CONTENT & METADATA ===")
rows = conn.execute(
    "SELECT signal_id, title, metadata_json, content, external_id, relevance_score "
    "FROM signals WHERE source='oxpeckers' ORDER BY relevance_score DESC"
).fetchall()
for r in rows:
    meta = {}
    try: meta = json.loads(r['metadata_json'] or '{}')
    except: pass
    print(f"\nTITLE: {r['title'][:80]}")
    print(f"EXT_ID: {r['external_id']}")
    print(f"RELEVANCE: {r['relevance_score']}")
    print(f"CONTENT: {str(r['content'] or '')[:250]}")
    print(f"META: {json.dumps(meta)[:300]}")

# Check if oxpeckers collector exists
import os
coll_path = r'C:\Users\matam\Projects\FORGE\forage\collectors'
print("\n\n=== COLLECTOR FILES ===")
for f in os.listdir(coll_path):
    print(f"  {f}")

# Check scraper tools
print("\n=== SCRAPER/INFILTRATOR TOOLS ===")
tool_path = r'C:\Users\matam\Projects\FORGE\tools'
for f in os.listdir(tool_path):
    if any(kw in f.lower() for kw in ['scrap', 'infiltr', 'fetch', 'web', 'oxpeck']):
        print(f"  {f}")

conn.close()
