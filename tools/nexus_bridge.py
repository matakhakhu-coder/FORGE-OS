from pathlib import Path
import sqlite3
import re
import os
import logging
import argparse
from itertools import combinations

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# --- CONFIGURATION & SECURITY ---
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")
BATCH_SIZE = 50
FLUSH_LIMIT = 500
MAX_TEXT_SLICE = 100000  # RAM Safety: 100KB per doc limit BEFORE regex

# Attempt to load FORGE security, fallback to basic sanitization if offline
try:
    from forge_security.sanitizer import sanitize_signal_text
except ImportError:
    logging.warning("forge_security module not found. Using local fallback sanitizer.")
    def sanitize_signal_text(text):
        """Strips non-printable chars and dangerous script tags."""
        clean = re.sub(r'[^\x20-\x7E\n\r\t]', '', text)
        return re.sub(r'<[^>]*>', '', clean)

BLACKLIST = {'magnitude', 'usgs', 'review', 'state', 'government', 'south africa', 'gauteng', 'durban', 'joburg', 'daily maverick', 'news24', 'timeslive', 'labubu', 'cgi', 'walker', 'exxon', 'ferrari', 'court'}
PROTECTED = {'npa', 'ssa', 'eff', 'anc', 'siu', 'fbi', 'doj', 'zim'}

def extract_safe_sentence(text, match_start, match_end):
    start_bound = text.rfind('.', 0, match_start)
    start_bound = 0 if start_bound == -1 else start_bound + 1
    end_bound = text.find('.', match_end)
    end_bound = len(text) if end_bound == -1 else end_bound + 1
    snippet = text[start_bound:end_bound].replace('\n', ' ').strip()
    return sanitize_signal_text(snippet)[:250]

def build_refined_matrix(cursor):
    cursor.execute("SELECT actor_id, name FROM actors WHERE name IS NOT NULL")
    raw_actors = cursor.fetchall()
    refined_map = {}
    for actor_id, name in raw_actors:
        n_low = name.lower()
        if n_low in BLACKLIST or (len(n_low) < 4 and n_low not in PROTECTED): continue
        refined_map[n_low] = actor_id
    sorted_names = sorted(refined_map.keys(), key=len, reverse=True)
    pattern_string = r'\b(' + '|'.join([re.escape(n) for n in sorted_names]) + r')\b'
    return refined_map, re.compile(pattern_string, re.IGNORECASE)

def get_dynamic_schema(cursor, table_name):
    """PRAGMA check to map columns dynamically."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]

def flush_to_db(cursor, relationships, dry_run):
    if not relationships: return 0, 0
    
    # Dynamic schema alignment check
    schema_cols = get_dynamic_schema(cursor, 'entity_relationships')
    required_cols = ['subject_actor_id', 'object_actor_id', 'relation_type', 'confidence', 'description']
    if not all(col in schema_cols for col in required_cols):
        raise ValueError(f"Schema Mismatch: entity_relationships missing required columns. Found: {schema_cols}")

    if dry_run:
        logging.info(f"[DRY RUN] Simulated write of {len(relationships)} relationships.")
        relationships.clear()
        return len(relationships), 0

    cursor.executemany("""
        INSERT OR IGNORE INTO entity_relationships 
        (subject_actor_id, object_actor_id, relation_type, confidence, description) 
        VALUES (?, ?, ?, ?, ?)
    """, relationships)
    
    inserted = cursor.rowcount
    suppressed = len(relationships) - inserted
    relationships.clear()
    return inserted, suppressed

def ignite_v5(dry_run=False):
    logging.info(f"Igniting V5 Bridge {'[DRY RUN]' if dry_run else '[LIVE EXECUTION]'}...")
    if not os.path.exists(DB_PATH):
        logging.error(f"FATAL: {DB_PATH} missing.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Pre-Execution Integrity Check
        cursor.execute("PRAGMA integrity_check")
        if cursor.fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("Pre-flight PRAGMA integrity_check failed.")
        
        cursor.execute("SELECT COUNT(*) FROM entity_relationships")
        pre_count = cursor.fetchone()[0]
        logging.info(f"Pre-Flight Check Passed. DB Integrity OK. Current Links: {pre_count}")

        actor_map, compiled_pattern = build_refined_matrix(cursor)
        cursor.execute("SELECT artifact_id, title, raw_text_cache FROM artifacts WHERE raw_text_cache IS NOT NULL")
        
        total_inserted, total_suppressed = 0, 0
        relationships = []

        while True:
            batch = cursor.fetchmany(BATCH_SIZE)
            if not batch: break
                
            for art_id, title, raw_text in batch:
                # RAM Safe Slice
                safe_text = raw_text[:MAX_TEXT_SLICE]
                actors_in_doc = set()
                
                for match in compiled_pattern.finditer(safe_text):
                    m_name = match.group(1).lower()
                    actor_id = actor_map[m_name]
                    snippet = extract_safe_sentence(safe_text, *match.span())
                    
                    relationships.append((actor_id, art_id, 'osint_match', 0.95, f"[{title[:30]}]: {snippet}"))
                    actors_in_doc.add((actor_id, m_name.title()))

                    if len(relationships) >= FLUSH_LIMIT:
                        ins, sup = flush_to_db(cursor, relationships, dry_run)
                        total_inserted += ins
                        total_suppressed += sup

                if len(actors_in_doc) > 1:
                    for (act1_id, name1), (act2_id, name2) in combinations(actors_in_doc, 2):
                        relationships.append((act1_id, act2_id, 'co_occurrence', 0.85, sanitize_signal_text(f"Joint appearance in: {title}")[:250]))

        ins, sup = flush_to_db(cursor, relationships, dry_run)
        total_inserted += ins
        total_suppressed += sup

        if not dry_run: conn.commit()

        # Post-Execution Validation
        cursor.execute("SELECT COUNT(*) FROM entity_relationships")
        post_count = cursor.fetchone()[0]
        logging.info(f"V5 Complete. Inserted: {total_inserted} | Suppressed (Duplicates): {total_suppressed}")
        if not dry_run:
            assert post_count == pre_count + total_inserted, "Post-flight rowcount validation failed!"

    except Exception as e:
        logging.error(f"RUNTIME FAULT: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE OSINT Correlator V5")
    parser.add_argument("--dry-run", action="store_true", help="Simulate writes without modifying DB")
    args = parser.parse_args()
    ignite_v5(dry_run=args.dry_run)