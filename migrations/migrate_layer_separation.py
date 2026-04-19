import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database.db"

TABLES = [
    'signals',
    'wiki_articles',
    'cases',
    'events',
    'artifacts',
]


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def add_source_type_column(conn: sqlite3.Connection, table: str):
    if column_exists(conn, table, 'source_type'):
        print(f"{table}: source_type already present")
        return

    print(f"{table}: adding source_type column")
    conn.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT DEFAULT 'live'")


def seed_source_type(conn: sqlite3.Connection, table: str):
    if not column_exists(conn, table, 'source_type'):
        raise ValueError(f"{table}: source_type column is missing, cannot seed")

    print(f"{table}: updating existing rows to source_type='seed'")
    conn.execute(f"UPDATE {table} SET source_type = 'seed' WHERE source_type IS NULL OR source_type = 'live'")


def run_migration(db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row

    try:
        with conn:
            for table in TABLES:
                try:
                    add_source_type_column(conn, table)
                    seed_source_type(conn, table)
                except sqlite3.OperationalError as ex:
                    # If table does not exist yet, skip gracefully.
                    if 'no such table' in str(ex).lower():
                        print(f"{table}: table not found, skipping")
                        continue
                    raise

        print("Migration complete. All existing records are now tagged as source_type='seed'.")

    finally:
        conn.close()


if __name__ == '__main__':
    run_migration(DB_PATH)
