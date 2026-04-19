import sqlite3
import re
import os
import logging

# PRO-MODE: Operational Logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

DB_PATH = 'database.db'
BATCH_SIZE = 100 # Safe memory chunking
CONTEXT_WINDOW = 75 # Characters to capture before and after a match

def build_actor_matrix(cursor):
    """Fetches local actors and builds a high-speed unified regex pattern."""
    cursor.execute("SELECT actor_id, name FROM actors WHERE name IS NOT NULL AND name != ''")
    local_actors = cursor.fetchall()
    
    # Map names to IDs for fast lookup after a regex match
    # Sorting by length descending ensures "Bradley Smith" matches before "Smith"
    actor_map = {name.lower(): actor_id for actor_id, name in local_actors}
    sorted_names = sorted(actor_map.keys(), key=len, reverse=True)
    
    # Build a single unified boundary regex: \b(name1|name2|name3)\b
    escaped_names = [re.escape(n) for n in sorted_names]
    pattern_string = r'\b(' + '|'.join(escaped_names) + r')\b'
    compiled_pattern = re.compile(pattern_string, re.IGNORECASE)
    
    return actor_map, compiled_pattern

def ignite_pro_bridge():
    logging.info("Igniting Pro-Mode Intelligence Bridge: Streaming Architecture Active...")
    
    if not os.path.exists(DB_PATH):
        logging.error(f"FATAL: {DB_PATH} missing.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. Compile the High-Speed Matrix
        actor_map, compiled_pattern = build_actor_matrix(cursor)
        logging.info(f"Local Matrix Compiled: {len(actor_map)} actors loaded into unified regex.")

        # 2. Setup Streaming from Global Artifacts
        cursor.execute("SELECT artifact_id, title, raw_text_cache FROM artifacts WHERE raw_text_cache IS NOT NULL")
        
        matches_found = 0
        relationships_to_insert = []

        # 3. Stream and Scan (Memory Safe)
        while True:
            batch = cursor.fetchmany(BATCH_SIZE)
            if not batch:
                break
                
            for art_id, title, text in batch:
                # Find all matches in the document in one pass
                for match in compiled_pattern.finditer(text):
                    matched_name = match.group(1).lower()
                    actor_id = actor_map.get(matched_name)
                    
                    if actor_id:
                        # Extract Forensic Context Window
                        start, end = match.span()
                        window_start = max(0, start - CONTEXT_WINDOW)
                        window_end = min(len(text), end + CONTEXT_WINDOW)
                        snippet = text[window_start:window_end].replace('\n', ' ').strip()
                        
                        desc = f"OSINT Match [{title}]: '...{snippet}...'"
                        
                        # Queue the relationship
                        relationships_to_insert.append((
                            actor_id, 
                            art_id, 
                            'osint_document_match', 
                            0.95, 
                            desc
                        ))
                        matches_found += 1
                        logging.info(f"MATCH LOCKED: '{matched_name.title()}' found in {title}")

        # 4. Batch Commit to Database
        if relationships_to_insert:
            logging.info("Executing Batch Relational Mesh Insert...")
            cursor.executemany("""
                INSERT OR IGNORE INTO entity_relationships 
                (subject_actor_id, object_actor_id, relation_type, confidence, description)
                VALUES (?, ?, ?, ?, ?)
            """, relationships_to_insert)
            conn.commit()

        logging.info(f"Bridge Complete. {matches_found} context-rich shadow links grafted to the graph.")

    except sqlite3.Error as e:
        logging.error(f"DATABASE FAULT: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    ignite_pro_bridge()