from pathlib import Path
import sqlite3



def setup_wiki_schema():

    conn = sqlite3.connect(str(Path(__file__).resolve().parent.parent / "database.db"))

    cursor = conn.cursor()



    print("[?] Initializing FORGE Wiki Schema...")



    # 1. Create the Articles Table with the correct column naming

    cursor.execute('''

        CREATE TABLE IF NOT EXISTS wiki_articles (

            article_id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            slug TEXT UNIQUE NOT NULL,

            summary TEXT,

            content TEXT,

            content_html TEXT,

            tags TEXT,

            source_type TEXT DEFAULT 'live',

            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP

        )

    ''')



    # 2. Check if we need to add columns to an existing table (Safety fallback)

    try:

        cursor.execute("ALTER TABLE wiki_articles ADD COLUMN content_html TEXT")

        print("[!] Added missing 'content_html' column to existing table.")

    except sqlite3.OperationalError:

        # Column already exists, skip

        pass

    try:
        cursor.execute("ALTER TABLE wiki_articles ADD COLUMN tags TEXT")
        print("[!] Added missing 'tags' column to existing table.")
    except sqlite3.OperationalError:
        # Column already exists, skip
        pass



    # 3. Create the Relationship Graph Table (Links)

    cursor.execute('''

        CREATE TABLE IF NOT EXISTS wiki_links (

            link_id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_id INTEGER,

            target_id INTEGER,

            FOREIGN KEY (source_id) REFERENCES wiki_articles (article_id),

            FOREIGN KEY (target_id) REFERENCES wiki_articles (article_id)

        )

    ''')



    conn.commit()

    conn.close()

    print("[OK] Wiki tables initialized successfully.")



if __name__ == "__main__":

    setup_wiki_schema()

