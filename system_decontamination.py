import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "database.db"
TABLES = [
    "signals",
    "events",
    "actors",
    "cases",
    "artifacts",
    "wiki_articles",
]


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def add_source_type_column(conn: sqlite3.Connection, table: str):
    if column_exists(conn, table, "source_type"):
        print(f"{table}: source_type already present")
        return

    print(f"{table}: adding source_type column")
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT DEFAULT 'seed'")
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "duplicate column name" in message or "already exists" in message:
            print(f"{table}: source_type exists (race condition)")
            return
        raise


def seed_source_type(conn: sqlite3.Connection, table: str):
    if not column_exists(conn, table, "source_type"):
        raise RuntimeError(f"{table}: source_type column is missing, cannot seed")

    print(f"{table}: marking existing rows as source_type='seed'")
    cur = conn.execute(
        f"UPDATE {table} SET source_type = 'seed' WHERE source_type IS NULL OR source_type != 'seed'"
    )
    print(f"{table}: updated {cur.rowcount} rows")


def run_migration(db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        with conn:
            for table in TABLES:
                try:
                    add_source_type_column(conn, table)
                    seed_source_type(conn, table)
                except sqlite3.OperationalError as ex:
                    if "no such table" in str(ex).lower():
                        print(f"{table}: table not found, skipping")
                        continue
                    raise

            # Final data reclassification per Task 1
            print("signals: applying sentinel/live/seed classification (signal_id and source)")
            conn.execute("UPDATE signals SET source_type = 'live' WHERE source = 'SENTINEL' OR signal_id LIKE 'sa-%'")
            conn.execute("UPDATE signals SET source_type = 'seed' WHERE source_type IS NULL OR source_type NOT IN ('live', 'seed')")

            print("events: default to seed + promote linked live events")
            conn.execute("UPDATE events SET source_type = 'seed'")
            conn.execute(
                "UPDATE events SET source_type='live' WHERE event_id IN ("
                "SELECT DISTINCT a.event_id FROM artifacts a "
                "JOIN signals s ON s.source_artifact_id = a.artifact_id WHERE s.source_type = 'live'"
                ")"
            )

            print("actors: default to seed + promote linked live actors")
            conn.execute("UPDATE actors SET source_type = 'seed'")
            conn.execute(
                "UPDATE actors SET source_type='live' WHERE actor_id IN ("
                "SELECT DISTINCT ae.actor_id FROM actor_events ae "
                "JOIN events e ON e.event_id = ae.event_id WHERE e.source_type = 'live'"
                ")"
            )

            print("wiki_articles: default to seed (no explicit mapping available)")
            conn.execute("UPDATE wiki_articles SET source_type = 'seed'")

        print("system_decontamination: Migration complete. Existing rows tagged as source_type according to deterministic rules.")

        print("\nsource_type summary:")
        for table in TABLES:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE source_type = 'seed'").fetchone()[0]
                total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                live = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE source_type = 'live'").fetchone()[0]
                print(f"  {table}: {row}/{total} seed, {live}/{total} live")
            except Exception as ex:
                print(f"  {table}: error getting count: {ex}")
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration(DB_PATH)
