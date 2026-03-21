import sqlite3

def patch_forge_schema():
    db_path = 'database.db' # Ensure this matches your actual db filename
    tables_to_fix = ['events', 'artifacts', 'actors', 'cases', 'signals', 'wiki_articles']
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    for table in tables_to_fix:
        try:
            # Check if column exists
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'source_type' not in columns:
                print(f"[*] Patching table '{table}': Adding source_type...")
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT DEFAULT 'live'")
            else:
                print(f"[✓] Table '{table}' already has source_type.")
                
        except sqlite3.OperationalError as e:
            print(f"[!] Error checking table {table}: {e}")
            
    conn.commit()
    conn.close()
    print("\n[SUCCESS] Schema aligned. Restarting FORGE is now safe.")


def apply_conclave_schema_patch(db_path="database.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    def column_exists(table, column):
        cursor.execute(f"PRAGMA table_info({table})")
        return column in [row[1] for row in cursor.fetchall()]

    patches = [
        ("signals", "gravity_score", "FLOAT"),
        ("signals", "processed_at", "DATETIME"),
        ("signals", "conclave_meta", "TEXT"),

        ("actors", "confidence_score", "FLOAT"),
        ("actors", "automated", "BOOLEAN DEFAULT 0"),

        ("events", "confidence_score", "FLOAT"),
        ("events", "automated", "BOOLEAN DEFAULT 0"),

        ("cases", "auto_generated", "BOOLEAN DEFAULT 0"),
        ("cases", "trigger_signal_id", "TEXT"),
    ]

    for table, column, definition in patches:
        if not column_exists(table, column):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                print(f"[✓] Added {column} to {table}")
            except Exception as e:
                print(f"[!] Failed {table}.{column}: {e}")

    conn.commit()
    conn.close()




def apply_relationship_schema_patch(db_path="database.db"):
    """
    Creates signal_actors and event_actors relationship tables.
    Safe to run multiple times — CREATE TABLE IF NOT EXISTS + UNIQUE constraints.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_actors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id  TEXT    NOT NULL,
            actor_id   INTEGER NOT NULL,
            role       TEXT    DEFAULT 'mentioned',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(signal_id, actor_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS event_actors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER NOT NULL,
            actor_id   INTEGER NOT NULL,
            role       TEXT    DEFAULT 'involved',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_id, actor_id)
        )
    """)

    conn.commit()
    conn.close()
    print("[✓] Relationship tables: signal_actors, event_actors — ready.")



def apply_graph_schema_patch(db_path="database.db"):
    """
    Creates the FORGE Graph Core tables:
        graph_nodes  — universal node registry
        graph_edges  — universal edge table

    Deliberately excludes graph_embeddings (no embedding model yet)
    and graph_intelligence (actor_network_metrics already covers this).

    Safe to run multiple times — all CREATE TABLE IF NOT EXISTS.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            node_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type    TEXT    NOT NULL,
            ref_id       TEXT    NOT NULL,
            label        TEXT,
            metadata_json TEXT,
            created_at   TEXT    DEFAULT (datetime('now')),
            UNIQUE(node_type, ref_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_edges (
            edge_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_node_id    INTEGER NOT NULL,
            target_node_id    INTEGER NOT NULL,
            relation_type     TEXT    NOT NULL,
            weight            REAL    DEFAULT 1.0,
            confidence        REAL    DEFAULT 1.0,
            source_event_id   INTEGER,
            source_signal_id  TEXT,
            source_artifact_id INTEGER,
            created_at        TEXT    DEFAULT (datetime('now')),
            UNIQUE(source_node_id, target_node_id, relation_type),
            FOREIGN KEY(source_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
            FOREIGN KEY(target_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
        )
    """)

    # Index for fast traversal
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_graph_edges_source
        ON graph_edges(source_node_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_graph_edges_target
        ON graph_edges(target_node_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_graph_edges_relation
        ON graph_edges(relation_type)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_graph_nodes_type_ref
        ON graph_nodes(node_type, ref_id)
    """)

    conn.commit()
    conn.close()
    print("[✓] Graph Core tables: graph_nodes, graph_edges — ready.")

if __name__ == "__main__":
    patch_forge_schema()
    apply_conclave_schema_patch()
    apply_relationship_schema_patch()
    apply_graph_schema_patch()