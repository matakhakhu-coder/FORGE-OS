from pathlib import Path
import sqlite3

def patch_wiki_links():
    db_path = str(Path(__file__).resolve().parent.parent / "database.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"[*] Connecting to {db_path}...")
    
    try:
        # 1. Check if wiki_links table exists and what columns it has
        cursor.execute("PRAGMA table_info(wiki_links)")
        columns = [c[1] for c in cursor.fetchall()]
        
        # 2. Add source_id if missing
        if 'source_id' not in columns:
            print("[+] Adding missing 'source_id' column to wiki_links...")
            cursor.execute("ALTER TABLE wiki_links ADD COLUMN source_id TEXT DEFAULT 'seed'")
        
        # 3. Add source_type if missing (good for future-proofing)
        if 'source_type' not in columns:
            print("[+] Adding missing 'source_type' column to wiki_links...")
            cursor.execute("ALTER TABLE wiki_links ADD COLUMN source_type TEXT DEFAULT 'seed'")
            
        conn.commit()
        print("[SUCCESS] wiki_links table updated.")
        
    except Exception as e:
        print(f"[!] Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    patch_wiki_links()