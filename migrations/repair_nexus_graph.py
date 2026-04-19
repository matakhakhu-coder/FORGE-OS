from pathlib import Path
import sqlite3

def repair_graph():
    print("[OPERATOR] Initiating Graph Decontamination... (Surgical Excision)")
    conn = sqlite3.connect(str(Path(__file__).resolve().parent.parent / "database.db"))
    cursor = conn.cursor()

    try:
        # 1. Verify the blast radius before deletion
        cursor.execute("""
            SELECT relationship_id
            FROM entity_relationships
            WHERE relation_type = 'osint_match'
              AND object_actor_id NOT IN (SELECT actor_id FROM actors)
        """)
        orphan_count = len(cursor.fetchall())
        print(f"[AUDIT] Verified {orphan_count} ghost edges targeted for deletion.")

        # 2. Execute the Surgical Purge
        cursor.execute("""
            DELETE FROM entity_relationships
            WHERE relation_type = 'osint_match'
              AND object_actor_id NOT IN (SELECT actor_id FROM actors)
        """)
        deleted_count = cursor.rowcount

        # 3. Ensure the Case is Active for the UI
        cursor.execute("UPDATE cases SET status='active' WHERE case_id=36")

        conn.commit()
        print(f"[SUCCESS] Purged {deleted_count} broken edges. Graph integrity restored.")
        print("[ACTION] Refresh your browser at /cases/36. The network matrix should now render.")

    except Exception as e:
        print(f"[ERROR] Repair failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    repair_graph()