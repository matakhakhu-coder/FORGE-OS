from pathlib import Path
import sqlite3
import re
import os
import logging
import argparse
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")
KEYWORDS = ['Tshwane', 'ActionSA', 'EFF', 'Madlanga', 'Matlala', 'Mkhwanazi', 'Sibiya']
RECENCY_THRESHOLD = '2026-01-01'

# 🔴 GAP 2 FIXED: Word-Boundary Patched OWASP Sanitizer
try:
    from forge_security.sanitizer import sanitize_signal_text
except ImportError:
    logging.warning("forge_security off-grid. Initializing boundary-locked OWASP sanitizer.")
    def sanitize_signal_text(text):
        if not isinstance(text, str):
            text = str(text)
        text = text.encode('utf-8', 'ignore').decode('utf-8')
        text = re.sub(r'<[^>]*>', '', text) 
        
        # Word boundaries prevent partial match bypasses (e.g., 'Sleeping')
        sqli_pattern = (
            r'\b(SLEEP|BENCHMARK|WAITFOR|UNION|SELECT|INSERT|DROP|UPDATE|DELETE)\b|'
            r'(--|;|\/\*|\*\/|@@|@|1\s*=\s*1|OR\s+\'[^\']+\'\s*=\s*\'[^\']+\'|0x[0-9a-fA-F]+)'
        )
        text = re.sub(sqli_pattern, '', text, flags=re.IGNORECASE) 
        text = re.sub(r'[^\x20-\x7E\n\r\t]', '', text) 
        return text.strip()[:250]

def dynamic_insert(cursor, table, payload_dict, dry_run=False):
    cursor.execute("PRAGMA table_info(" + table + ")")
    valid_cols = {row[1] for row in cursor.fetchall()}
    
    if not valid_cols:
        logging.warning(f"Table '{table}' missing. Bypassing insert.")
        return None

    clean_payload = {k: v for k, v in payload_dict.items() if k in valid_cols}
    if not clean_payload: return None

    cols = list(clean_payload.keys())
    vals = list(clean_payload.values())
    placeholders = ",".join(["?"] * len(cols))
    
    if not dry_run:
        sql = "INSERT INTO " + table + " (" + ",".join(cols) + ") VALUES (" + placeholders + ")"
        cursor.execute(sql, vals)
        return cursor.lastrowid
    return None

def get_table_counts(cursor, tables):
    counts = {}
    for t in tables:
        try:
            cursor.execute("SELECT COUNT(*) FROM " + t)
            counts[t] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            counts[t] = 0
    return counts

def run_live_intercept(dry_run=False):
    logging.info(f"Igniting V7.3 Coalition Interceptor {'[DRY RUN]' if dry_run else '[LIVE EXECUTION]'}...")
    if not os.path.exists(DB_PATH):
        logging.error("FATAL: database.db missing.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if not dry_run: cursor.execute("BEGIN IMMEDIATE")

        target_tables = ['case_events', 'event_actors']
        pre_counts = get_table_counts(cursor, target_tables)

        cursor.execute("SELECT case_id FROM cases WHERE name LIKE '%Dark Badge%' OR name LIKE '%Red Folder%' LIMIT 1")
        case_row = cursor.fetchone()
        if not case_row:
            logging.warning("Dark Badge Case not found. Run Phase 56 compiler first. Aborting.")
            if not dry_run: conn.rollback()
            return
        case_id = case_row[0]

        cursor.execute("""
            SELECT actor_id, name FROM actors 
            WHERE name IS NOT NULL AND length(name) > 3 
            ORDER BY COALESCE(gravity_score, 0.0) DESC, actor_id DESC LIMIT 100
        """)
        actor_map = {name.lower(): actor_id for actor_id, name in cursor.fetchall()}
        
        if not actor_map:
            logging.warning("No actors available for mapping. Aborting.")
            if not dry_run: conn.rollback()
            return
            
        pattern = re.compile(r'\b(' + '|'.join([re.escape(n) for n in actor_map.keys()]) + r')\b', re.IGNORECASE)

        keyword_placeholders = " OR ".join(["raw_text_cache LIKE ?"] * len(KEYWORDS))
        params = [RECENCY_THRESHOLD] + [f"%{kw}%" for kw in KEYWORDS]
        
        base_query = (
            "SELECT artifact_id, title, raw_text_cache FROM artifacts "
            "WHERE raw_text_cache IS NOT NULL "
            "AND created_at >= ? "
            "AND (" + keyword_placeholders + ")"
        )
        
        cursor.execute(base_query, params)
        live_hits = cursor.fetchall()
        
        logging.info(f"Scanner hit {len(live_hits)} recent (2026+) artifacts with coalition signatures.")
        if not live_hits:
            if not dry_run: conn.rollback()
            return

        epoch_event_id = int(datetime.now().timestamp()) if not dry_run else 1777777777
        
        dynamic_insert(cursor, 'case_events', {
            "case_id": case_id, 
            "event_id": epoch_event_id, 
            "note": "LIVE INTERCEPT: 2026 Coalition Anomalies",
            "pinned_at": datetime.now().isoformat()
        }, dry_run)
        
        case_events_inserted = 1
        inserted, suppressed = 0, 0
        actors_found = set()

        for art_id, title, text in live_hits:
            clean_title = sanitize_signal_text(title)
            for match in pattern.finditer(text):
                actor_name_matched = match.group(1).lower()
                actor_id = actor_map.get(actor_name_matched)
                
                if actor_id and actor_id not in actors_found:
                    cursor.execute("SELECT 1 FROM event_actors WHERE event_id=? AND actor_id=?", (epoch_event_id, actor_id))
                    if cursor.fetchone():
                        suppressed += 1
                        continue
                        
                    dynamic_insert(cursor, 'event_actors', {
                        "event_id": epoch_event_id, 
                        "actor_id": actor_id, 
                        "role": "coalition_target"
                    }, dry_run)
                    
                    actors_found.add(actor_id)
                    inserted += 1
                    logging.info(f"LIVE TARGET: [{actor_name_matched.title()}] extracted via '{clean_title[:40]}...'")

        if not dry_run:
            conn.commit()
            cursor.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            assert integrity == "ok", f"Integrity Check Failed: {integrity}"

            post_counts = get_table_counts(cursor, target_tables)
            assert post_counts['case_events'] == pre_counts['case_events'] + case_events_inserted, "Case Events validation mismatch."
            assert post_counts['event_actors'] == pre_counts['event_actors'] + inserted, "Event Actor validation mismatch."

        logging.info(f"V7.3 Intercept Complete. Funneled to Case ID: {case_id}")
        logging.info(f"Event ID: {epoch_event_id} | Targets Grafted: {inserted} | Suppressed: {suppressed}")

    except Exception as e:
        logging.error(f"RUNTIME FAULT: {e}")
        if not dry_run: conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE V7.3 Live Coalition Interceptor")
    parser.add_argument("--dry-run", action="store_true", help="Simulate intercept without writing")
    args = parser.parse_args()
    run_live_intercept(dry_run=args.dry_run)