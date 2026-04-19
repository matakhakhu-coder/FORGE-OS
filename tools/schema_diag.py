from pathlib import Path
import sqlite3
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")
TARGET_TABLES = ['cases', 'case_events', 'event_actors', 'entity_relationships', 'actors']

def run_diagnostic():
    logging.info("Executing PRAGMA Schema Diagnostic...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for table in TARGET_TABLES:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            if not columns:
                logging.warning(f"Table '{table}' not found or empty.")
                continue
            logging.info(f"--- SCHEMA: {table.upper()} ---")
            for col in columns:
                # Format: cid, name, type, notnull, dflt_value, pk
                logging.info(f"  [{col[5]}] {col[1]} ({col[2]}) | NOT NULL: {col[3]} | DEFAULT: {col[4]}")
    except sqlite3.Error as e:
        logging.error(f"DIAGNOSTIC FAULT: {e}")
    finally:
        if 'conn' in locals(): conn.close()

if __name__ == "__main__":
    run_diagnostic()