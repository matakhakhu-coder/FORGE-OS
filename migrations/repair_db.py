from pathlib import Path
import sqlite3
import os

def fix():
    db_path = str(Path(__file__).resolve().parent.parent / "database.db")

    if not os.path.exists(db_path):
        print("Database not found!")
        return

    tables = ['events', 'artifacts', 'actors', 'cases', 'signals', 'wiki_articles']
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for table in tables:
        try:
            # Check if source_type exists
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [c[1] for c in cursor.fetchall()]
            if 'source_type' not in columns:
                print(f"Adding source_type to {table}...")
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT DEFAULT 'live'")
            else:
                print(f"{table} already has source_type.")
        except Exception as e:
            print(f"Error on {table}: {e}")

    conn.commit()
    conn.close()
    print("Repair complete.")


if __name__ == "__main__":
    fix()