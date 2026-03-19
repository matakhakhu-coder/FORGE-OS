import sqlite3
import os
from pathlib import Path

# Absolute Path Logic
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "database.db"

# The core tables required for the dashboard to function
REQUIRED_TABLES = [
    'priorities',
    'artifacts',
    'events',
    'actors',
    'signals',
    'sentinel_alerts',
    'discovery_targets',
    'pipeline_runs',
    'signal_actors',
    'event_actors',
]

def get_connection():
    db_abs_path = str(DB_PATH.absolute())
    
    if not os.path.exists(db_abs_path):
        raise FileNotFoundError(f"[FORGE ERROR] Database missing at: {db_abs_path}. Run 'python app.py --init-db'")

    conn = sqlite3.connect(db_abs_path)
    conn.row_factory = sqlite3.Row
    
    # --- SCHEMA VALIDATION ---
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = [row['name'] for row in cursor.fetchall()]
        
        missing = [t for t in REQUIRED_TABLES if t not in existing_tables]
        
        if missing:
            conn.close()
            # This error will show up in the Flask console to tell you exactly what to do
            print(f"\n[!] DATABASE SCHEMA INCOMPLETE")
            print(f"[!] Missing tables: {', '.join(missing)}")
            print(f"[!] FIX: Run 'python app.py --migrate' in your terminal.\n")
            raise sqlite3.OperationalError(f"Missing tables: {missing}")
            
    except sqlite3.Error as e:
        conn.close()
        raise e

    return conn