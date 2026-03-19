#!/usr/bin/env python3
"""
FORGE Wiki — Link Graph Engine
==============================
The "Intelligence" layer. Scans wiki articles for mentions of other 
wiki subjects and establishes bidirectional links in the database.

Logic:
1. Fetch all existing article titles and IDs.
2. For each article, scan its HTML/Text for the titles of other articles.
3. If a match is found, create an entry in the 'wiki_links' table.
4. This enables "What Links Here" and the Network Graph.

Author: FORGE x
"""

import sqlite3
import logging
import re
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "database.db"

logging.basicConfig(level=logging.INFO, format="[WIKI LINK ENGINE] %(message)s")

class WikiLinkEngine:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def run(self):
        logging.info("Starting Wiki Link Graph construction...")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. Map all articles: {Title: ID}
        articles = conn.execute("SELECT article_id, title, slug FROM wiki_articles").fetchall()
        title_map = {a['title']: a['article_id'] for a in articles}
        
        if not title_map:
            logging.info("No articles found to link.")
            conn.close()
            return

        links_created = 0
        
        # 2. Iterate through each article to find mentions of others
        for source_article in articles:
            source_id = source_article['article_id']
            content = conn.execute(
                "SELECT content_html FROM wiki_articles WHERE article_id = ?", 
                (source_id,)
            ).fetchone()['content_html']

            if not content:
                continue

            for target_title, target_id in title_map.items():
                # Avoid self-linking
                if source_id == target_id:
                    continue
                
                # Look for the target title as a whole word (case-insensitive)
                # We use a word-boundary regex to avoid partial matches (e.g., "NPA" matching in "Unpaved")
                pattern = re.compile(rf'\b{re.escape(target_title)}\b', re.IGNORECASE)
                
                if pattern.search(content):
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO wiki_links (source_id, target_id)
                            VALUES (?, ?)
                        """, (source_id, target_id))
                        links_created += 1
                    except sqlite3.Error as e:
                        logging.error(f"Link error: {e}")

        conn.commit()
        conn.close()
        logging.info(f"Link Graph update complete. {links_created} relationships identified.")

if __name__ == "__main__":
    engine = WikiLinkEngine(DB_PATH)
    engine.run()

