import sqlite3
import os
import urllib.parse
from pathlib import Path

# Absolute Path Logic
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "database.db"

# ── Phase 68 Hard-Lock — Process-level FK enforcement ────────────────────────
# Python's stdlib sqlite3 module does NOT honour `_foreign_keys` as a URI
# parameter (that is a Go/Rust driver extension).  The only reliable way to
# enforce FK constraints for EVERY connection opened anywhere in the process —
# including ad-hoc scripts that call sqlite3.connect() directly — is to
# monkey-patch the connect function at module-import time.
#
# Mechanism:
#   1. Wrap the real sqlite3.connect with _hardened_connect.
#   2. _hardened_connect executes PRAGMA foreign_keys = ON immediately after
#      the driver returns the handle, before any caller code runs.
#   3. sqlite3.connect is replaced by the wrapper so all call-sites get it
#      automatically without needing to know about this module.
#
# Safety notes:
#   • The wrapper is idempotent — importing connection.py twice is harmless.
#   • The PRAGMA has no effect on read-only connections (PRAGMA is a no-op on
#     them), so read-only URI connections (?mode=ro) are unaffected.
#   • This does NOT bypass WAL mode — WAL is set per-connection by get_connection.

_real_sqlite3_connect = sqlite3.connect   # stash the original

def _hardened_connect(database, *args, **kwargs):
    """Wrapper that enforces PRAGMA foreign_keys = ON on every connection."""
    conn = _real_sqlite3_connect(database, *args, **kwargs)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass   # read-only or in-memory edge cases — never crash the caller
    return conn

sqlite3.connect = _hardened_connect   # process-wide replacement
# ─────────────────────────────────────────────────────────────────────────────

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

    # URI format: file:<path>?mode=rw
    # `mode=rw` — refuses to create a new DB file if the path is wrong,
    # closing the silent-creation footgun on bad paths.
    # FK enforcement is now handled process-wide by _hardened_connect above.
    uri_path = db_abs_path.replace("\\", "/")
    encoded  = urllib.parse.quote(uri_path, safe="/:")
    db_uri   = f"file:{encoded}?mode=rw"

    conn = sqlite3.connect(db_uri, uri=True)   # triggers _hardened_connect
    conn.row_factory = sqlite3.Row

    # WAL mode persists on disk; safe to re-assert on every connection.
    conn.execute("PRAGMA journal_mode=WAL;")

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