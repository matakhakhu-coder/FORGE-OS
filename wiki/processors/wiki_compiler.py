#!/usr/bin/env python3
"""
FORGE Wiki — Wiki Compiler (Phase x)
====================================
The synthesis engine that transforms raw signals and entities into 
persistent, interlinked wiki articles (Dossiers).

Logic:
1. Source: Entities. Extract top entities from signal_entities.
2. Source: Local Files. Scan 'media/documents/wiki' for .md files.
3. Compilation: For each entity, generate a chronological signal timeline.
4. Storage: Upsert the content into the local database (wiki_articles).

Author: FORGE x
"""

import sqlite3
import logging
import re
from pathlib import Path
from datetime import datetime

# Configuration
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "database.db"
WIKI_SRC_DIR = BASE_DIR / "media" / "documents" / "wiki"

logging.basicConfig(level=logging.INFO, format="[WIKI COMPILER] %(message)s")

class WikiCompiler:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Ensure the manual ingest directory exists
        WIKI_SRC_DIR.mkdir(parents=True, exist_ok=True)

    def slugify(self, text):
        """Creates a URL-friendly slug."""
        return re.sub(r'[\W_]+', '-', text.lower()).strip('-')

    def compile_from_entities(self, min_mentions=3):
        """Turn high-frequency entities into Auto-Wiki pages."""
        logging.info(f"Synthesizing articles for entities with >= {min_mentions} mentions...")
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row

        # 1. Find entities that deserve a page
        entities = conn.execute("""
            SELECT text, label, SUM(count) as total_mentions
            FROM signal_entities
            GROUP BY text, label
            HAVING total_mentions >= ?
            ORDER BY total_mentions DESC
        """, (min_mentions,)).fetchall()

        updated_count = 0

        for ent in entities:
            title = ent['text']
            slug = self.slugify(title)
            
            # 2. Gather all signals for this entity
            signals = conn.execute("""
                SELECT s.title, s.timestamp, s.source, s.content, s.signal_id
                FROM signals s
                JOIN signal_entities se ON se.signal_id = s.signal_id
                WHERE se.text = ?
                ORDER BY s.timestamp DESC
            """, (title,)).fetchall()

            # 3. Build the "Dossier" HTML (The Timeline)
            content_html = f'<div class="wiki-timeline">\n'
            content_html += f"<h3>Chronological Intelligence Feed</h3>\n<ul class='timeline-list'>\n"
            content = "Chronological Intelligence Feed\n"
            
            for sig in signals:
                date_str = sig['timestamp'][:10] if sig['timestamp'] else "Unknown Date"
                source_str = sig['source'] or "Unknown Source"
                snippet = (sig['content'] or "")[:250] + "..." if sig['content'] else "No content available."
                content += f"- {date_str} | {source_str} | {sig['title'] or ''} ({sig['signal_id']})\n  {snippet}\n"
                
                content_html += f"""
                    <li class="timeline-item">
                        <div class="timeline-meta">
                            <strong>{date_str}</strong> | <span class="badge">{source_str}</span>
                        </div>
                        <div class="timeline-content">
                            <h4><a href="/signals#{sig['signal_id']}">{sig['title']}</a></h4>
                            <p>{snippet}</p>
                        </div>
                    </li>
                """
            content_html += "</ul>\n</div>"

            # 4. Upsert into wiki_articles
            summary = f"Auto-generated intelligence dossier for {ent['label']}: {title}. Compiled from {len(signals)} signals."
            
            try:
                conn.execute("""
                    INSERT INTO wiki_articles (slug, title, summary, content, content_html, tags, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(slug) DO UPDATE SET
                        summary = excluded.summary,
                        content = excluded.content,
                        content_html = excluded.content_html,
                        last_updated = excluded.last_updated,
                        tags = excluded.tags
                """, (slug, title, summary, content, content_html, ent['label']))
                updated_count += 1
            except sqlite3.Error as e:
                logging.error(f"Failed to upsert {title}: {e}")

        conn.commit()
        conn.close()
        logging.info(f"Entity synthesis complete. Updated/Created {updated_count} dossiers.")

    def compile_from_local_files(self):
        """Ingest manual Markdown dossiers from media/documents/wiki/"""
        logging.info(f"Checking for manual wiki entries in {WIKI_SRC_DIR}...")
        conn = sqlite3.connect(self.db_path, timeout=60)

        updated_count = 0
        for md_file in WIKI_SRC_DIR.glob("*.md"):
            title = md_file.stem.replace('-', ' ').replace('_', ' ').title()
            slug = self.slugify(title)
            
            with open(md_file, "r", encoding="utf-8") as f:
                raw_text = f.read()
            
            # Simple Markdown-to-HTML shim (paragraph focused)
            html = ""
            for line in raw_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    html += f"<h1>{line[2:]}</h1>\n"
                elif line.startswith("## "):
                    html += f"<h2>{line[3:]}</h2>\n"
                else:
                    html += f"<p>{line}</p>\n"
            
            try:
                conn.execute("""
                    INSERT INTO wiki_articles (slug, title, summary, content, content_html, tags, last_updated)
                    VALUES (?, ?, ?, ?, ?, 'Manual, Dossier', datetime('now'))
                    ON CONFLICT(slug) DO UPDATE SET
                        summary = excluded.summary,
                        content = excluded.content,
                        content_html = excluded.content_html,
                        tags = excluded.tags,
                        last_updated = excluded.last_updated
                """, (slug, title, f"Manual research dossier: {title}", raw_text, html))
                updated_count += 1
            except sqlite3.Error as e:
                logging.error(f"Failed to ingest local file {md_file.name}: {e}")
            
        conn.commit()
        conn.close()
        logging.info(f"Local file ingestion complete. Processed {updated_count} files.")

    def run(self):
        logging.info("Starting Wiki Compilation Cycle...")
        self.compile_from_entities()
        self.compile_from_local_files()
        logging.info("Wiki Compilation Cycle finished.")

if __name__ == "__main__":
    compiler = WikiCompiler(DB_PATH)
    compiler.run()

