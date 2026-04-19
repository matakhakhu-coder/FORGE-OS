from pathlib import Path
import sqlite3
import json
from datetime import datetime

# DB path from your current project
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")

# ----- Lightweight entity extraction -----
def extract_entities(text):
    """Very simple real-world extractor: capitalized words and known NPA keywords"""
    keywords = ["NPA", "Police", "Court", "Investigation", "Crime", "FIR", "Suspect"]
    words = set(text.replace(",", " ").replace(".", " ").split())
    entities = [w for w in words if w.istitle() and len(w) > 3]
    entities.extend([k for k in keywords if k in text])
    return list(set(entities))

# ----- Minimal Conclave stub -----
def conclave_stub(signal_text):
    """Lightweight gravity/conclusion without full pipeline"""
    entities = extract_entities(signal_text)
    gravity = min(1.0, max(0.0, len(entities) / 10))  # naive gravity score
    return {
        "entities": entities,
        "gravity": gravity,
        "recommendation": "ESCALATE" if gravity > 0.6 else "MONITOR",
        "provenance": ["seed_cache"],
        "confidence": 0.5 + gravity/2
    }

# ----- Materialize actors -----
def get_or_create_actor(name, db_conn):
    cur = db_conn.cursor()
    cur.execute("SELECT actor_id FROM actors WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO actors (name, automated) VALUES (?, 1)", (name,))
    db_conn.commit()
    return cur.lastrowid

def materialize_entities(conclusion, signal_id, db_conn):
    actor_ids = []
    for e in conclusion["entities"]:
        try:
            aid = get_or_create_actor(e, db_conn)
            actor_ids.append(aid)
        except:
            continue
    # update signal with actor references
    cur = db_conn.cursor()
    cur.execute("SELECT conclave_meta FROM signals WHERE signal_id = ?", (signal_id,))
    meta = cur.fetchone()
    meta_dict = json.loads(meta[0]) if meta and meta[0] else {}
    meta_dict["actor_ids"] = actor_ids
    cur.execute("UPDATE signals SET conclave_meta=? WHERE signal_id=?",
                (json.dumps(meta_dict), signal_id))
    db_conn.commit()
    return actor_ids

# ----- Main seed runner -----
def run_seed(limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Pull recent signals with content
    signals = cur.execute(
        "SELECT signal_id, content FROM signals WHERE content IS NOT NULL ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    print(f"[+] Found {len(signals)} signals to seed")

    for row in signals:
        sid = row["signal_id"]
        text = row["content"]
        if not text or len(text) < 20:
            continue

        try:
            conclusion = conclave_stub(text)
            # Update signals table
            cur.execute("UPDATE signals SET gravity_score=?, processed_at=?, conclave_meta=? WHERE signal_id=?",
                        (conclusion["gravity"], datetime.utcnow(), json.dumps(conclusion), sid))
            conn.commit()
            # Materialize actors
            actor_ids = materialize_entities(conclusion, sid, conn)
            print(f"Signal {sid}: {len(actor_ids)} actors")
        except Exception as e:
            print(f"[Seed Error] {e}")

    conn.close()
    print("[✓] Seed cache generation complete")

# ----- Run -----
if __name__ == "__main__":
    run_seed(limit=100)