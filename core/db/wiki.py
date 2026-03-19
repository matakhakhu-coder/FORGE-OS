from core.db.connection import get_connection
from datetime import datetime
import json

DB_PATH = "database.db"

def init_wiki_db():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS wiki_entries (
                id INTEGER PRIMARY KEY,
                actor_id TEXT,
                event_id TEXT,
                artifact TEXT,
                timestamp DATETIME,
                narrative TEXT,
                context TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS wiki_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE,
                title TEXT NOT NULL,
                summary TEXT,
                content_html TEXT,
                tags TEXT,
                behavior TEXT,
                features TEXT,
                max_pulse_strength REAL,
                source_type TEXT DEFAULT 'live',
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Ensure backward compatibility when columns are missing
        columns = {row[1] for row in c.execute("PRAGMA table_info('wiki_articles')").fetchall()}
        if 'behavior' not in columns:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN behavior TEXT")
        if 'features' not in columns:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN features TEXT")
        if 'max_pulse_strength' not in columns:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN max_pulse_strength REAL")

        c.execute('''
            CREATE TABLE IF NOT EXISTS wiki_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_slug TEXT,
                target_slug TEXT,
                connection_type TEXT DEFAULT 'related',
                FOREIGN KEY(source_slug) REFERENCES wiki_articles(slug),
                FOREIGN KEY(target_slug) REFERENCES wiki_articles(slug)
            )
        ''')

        conn.commit()


def normalize_links():
    with get_connection() as conn:
        c = conn.cursor()
        columns = {row[1] for row in c.execute("PRAGMA table_info('wiki_links')").fetchall()}

        if 'source_slug' in columns and 'target_slug' in columns:
            # already normalized
            return

        # if legacy id fields exist
        if 'source_id' in columns and 'target_id' in columns:
            article_cols = {row[1] for row in c.execute("PRAGMA table_info('wiki_articles')").fetchall()}
            id_column = 'article_id' if 'article_id' in article_cols else 'id'

            has_connection_type = 'connection_type' in columns
            link_pk = 'link_id' if 'link_id' in columns else 'id' if 'id' in columns else None

            c.execute('''
                CREATE TABLE IF NOT EXISTS wiki_links_normalized (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_slug TEXT,
                    target_slug TEXT,
                    connection_type TEXT DEFAULT 'related'
                )
            ''')

            row_query = 'SELECT %s, source_id, target_id%s FROM wiki_links' % (
                link_pk if link_pk else 'NULL',
                ', connection_type' if has_connection_type else ''
            )

            for row in c.execute(row_query).fetchall():
                source_slug = c.execute(f'SELECT slug FROM wiki_articles WHERE {id_column} = ?', (row[1],)).fetchone()
                target_slug = c.execute(f'SELECT slug FROM wiki_articles WHERE {id_column} = ?', (row[2],)).fetchone()
                if source_slug and target_slug:
                    connection_type_value = row[3] if has_connection_type else 'related'
                    c.execute('''
                        INSERT INTO wiki_links_normalized (source_slug, target_slug, connection_type)
                        VALUES (?, ?, ?)
                    ''', (source_slug[0], target_slug[0], connection_type_value))

            c.execute('DROP TABLE wiki_links')
            c.execute('ALTER TABLE wiki_links_normalized RENAME TO wiki_links')
            conn.commit()
