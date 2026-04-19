from pathlib import Path
import sqlite3
import os
import argparse
import sys

# CONFIGURATION
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")

def get_args():
    parser = argparse.ArgumentParser(description="FORGE Phase 50 - Production-Grade Node Injection")
    parser.add_argument('--dry-run', action='store_true', help="Show SQL actions without committing")
    return parser.parse_args()

def run_injection(dry_run=False):
    mode = "DRY RUN (Simulated)" if dry_run else "ACTIVE INFILTRATION"
    print(f"[BRAIN] Mode: {mode}")
    
    if not os.path.exists(DB_PATH):
        print(f"[-] ERROR: {DB_PATH} missing.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # THE PAYLOAD: Explicitly handling the NOT NULL 'type' column
        targets = [
            {"name": "Jacob Zuma",      "type": "person", "desc": "Former President of South Africa; PEP"},
            {"name": "Jeffrey Epstein", "type": "person", "desc": "Global financier; DOJ Target"},
            {"name": "Mark Lloyd",      "type": "person", "desc": "Intermediary; Flight Log Match"}
        ]

        print(f"[+] Injecting {len(targets)} nodes into 'actors'...")
        
        # Idempotent Insert Logic
        for t in targets:
            sql = "INSERT INTO actors (name, type, description) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM actors WHERE name = ?)"
            params = (t['name'], t['type'], t['desc'], t['name'])
            
            if dry_run:
                print(f"    [DRY] Would insert: {t['name']} (type: {t['type']})")
            else:
                cursor.execute(sql, params)

        if dry_run:
            print("[DRY] Skipping relationship mapping and commit.")
            return

        # Commit actors to get IDs
        conn.commit()

        # RECOVERY: Fetch Integer IDs for the Relational Mesh
        cursor.execute("SELECT actor_id FROM actors WHERE name = 'Jeffrey Epstein'")
        epstein_id = cursor.fetchone()[0]
        cursor.execute("SELECT actor_id FROM actors WHERE name = 'Jacob Zuma'")
        zuma_id = cursor.fetchone()[0]

        print(f"[+] Relationships: Linking Epstein (ID:{epstein_id}) -> Zuma (ID:{zuma_id})")

        # SCHEMA-AWARE: Using 'relation_type' and 'description' columns
        rel_sql = """
            INSERT INTO entity_relationships (subject_actor_id, object_actor_id, relation_type, description, confidence)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM entity_relationships 
                WHERE subject_actor_id = ? AND object_actor_id = ? AND relation_type = ?
            )
        """
        rel_params = (
            epstein_id, zuma_id, 'indirect_association', 
            'Source: DOJ_RELEASE_JAN30_2026_EMAILS_RITZ', 0.85,
            epstein_id, zuma_id, 'indirect_association'
        )
        cursor.execute(rel_sql, rel_params)
        
        conn.commit()
        print("[SUCCESS] Phase 50: Shadow links secured in database.db.")

        # VALIDATION
        print("\n[+] VERIFICATION:")
        cursor.execute("SELECT actor_id, name, type FROM actors WHERE name IN ('Jeffrey Epstein', 'Jacob Zuma')")
        for row in cursor.fetchall():
            print(f"    Node Found: ID {row[0]} | Name: {row[1]} | Type: {row[2]}")

    except Exception as e:
        print(f"[-] CRITICAL ENGINE FAILURE: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    run_injection(dry_run=get_args().dry_run)