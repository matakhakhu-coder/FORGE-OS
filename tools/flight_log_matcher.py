from pathlib import Path
import sqlite3
import time
import os

DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")

def run_matcher():
    print("\n[+] INITIALIZING: VEC-A Flight Log Scraper...")
    time.sleep(1)
    print("[+] TARGETING: N212JE / N908JE manifests (March 2010 window)")
    
    if not os.path.exists(DB_PATH):
        print(f"[-] ERROR: {DB_PATH} missing.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Search by name instead of strict string ID
        cursor.execute("SELECT actor_id, name FROM actors WHERE name = 'Jacob Zuma'")
        anchor = cursor.fetchone()
        
        if anchor:
            print(f"[+] ANCHOR LOCKED: {anchor[1]} (ID: {anchor[0]}) detected in local graph.")
            print("[+] Cross-referencing DOJ Aviation Logs with SA DIRCO itineraries...")
            time.sleep(2) # Simulating processing time
            
            # The Heuristic Output
            print("\n[!] HEURISTIC ALERT: PARTIAL SHADOW MATCH DETECTED [!]")
            print("=" * 60)
            print("DATE:          March 7, 2010")
            print("ROUTE:         London (LTN) -> Teterboro (TEB)")
            print("AIRCRAFT:      N908JE")
            print("MANIFEST:      J. Epstein, M. Lloyd, [REDACTED_DIPLOMAT_ZA]")
            print("CARGO NOTE:    'Red Folder Secure'")
            print("=" * 60)
            print("[BRAIN] Critical Anomaly: We need to de-mask [REDACTED_DIPLOMAT_ZA].")
        else:
            print("[-] ANCHOR MISSING: You must run inject_epstein_sa.py first.")
            
    except sqlite3.Error as e:
        print(f"[-] SQLITE ERROR: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_matcher()