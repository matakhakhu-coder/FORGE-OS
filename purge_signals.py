import sqlite3

def wipe_signals():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    # These tables hold the high-volume telemetry causing the lag
    tables = ["pulses", "signals", "wiki_articles", "wiki_links"]
    
    print("[FORGE] Wiping high-volume telemetry...")
    for table in tables:
        try:
            cur.execute(f"DELETE FROM {table}")
            print(f"  - {table} cleared.")
        except sqlite3.OperationalError:
            print(f"  - {table} not found, skipping.")
            
    conn.commit()
    conn.close()
    print("[SUCCESS] Signal noise eliminated.")

if __name__ == "__main__":
    wipe_signals()