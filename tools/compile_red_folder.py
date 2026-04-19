from pathlib import Path
import sqlite3
import os
import logging
import argparse
import re
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")
CONFIDENCE_THRESHOLD = 0.85

# 🔴 GAP 1 FIXED: Advanced OWASP Fallback Sanitizer & 500-char cap
try:
    from forge_security.sanitizer import sanitize_signal_text
except ImportError:
    logging.warning("forge_security off-grid. Initializing tactical regex sanitizer.")
    def sanitize_signal_text(text):
        text = str(text)
        text = re.sub(r'<[^>]*>', '', text) 
        # Expanded SQLi vectors
        sqli_pattern = r'(--|;|\/\*|\*\/|@@|@|UNION|SELECT|INSERT|DROP|UPDATE|DELETE|1\s*=\s*1|OR\s+\'[^\']+\'\s*=\s*\'[^\']+\'|0x[0-9a-fA-F]+)'
        text = re.sub(sqli_pattern, '', text, flags=re.IGNORECASE) 
        text = re.sub(r'[^\x20-\x7E\n\r\t]', '', text) 
        return text.strip()[:500]

def dynamic_insert(cursor, table, payload_dict, dry_run=False):
    cursor.execute(f"PRAGMA table_info({table})")
    valid_cols = {row[1] for row in cursor.fetchall()}
    
    if not valid_cols:
        logging.warning(f"Table '{table}' missing. Bypassing insert.")
        return None

    clean_payload = {k: v for k, v in payload_dict.items() if k in valid_cols}
    if not clean_payload:
        return None

    cols = list(clean_payload.keys())
    vals = list(clean_payload.values())
    placeholders = ",".join(["?"] * len(cols))
    
    if not dry_run:
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        cursor.execute(sql, vals)
        return cursor.lastrowid
    return None

def get_table_counts(cursor, tables):
    counts = {}
    for t in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            counts[t] = 0
    return counts

def compile_syndicate(dry_run=False):
    logging.info(f"Igniting V6.3 Junction-Only Compiler {'[DRY RUN]' if dry_run else '[LIVE EXECUTION]'}...")
    
    if not os.path.exists(DB_PATH):
        logging.error("FATAL: database.db missing.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if not dry_run: cursor.execute("BEGIN IMMEDIATE")

        # 🟡 GAP 2 FIXED: Multi-Table Pre-Validation
        target_tables = ['cases', 'case_events', 'event_actors']
        pre_counts = get_table_counts(cursor, target_tables)
        if not dry_run:
            logging.info(f"Pre-Flight Checks: {pre_counts}")

        # 1. Establish Gravity Well (Case) + Inject Hypothesis
        cursor.execute("SELECT case_id, name FROM cases WHERE name LIKE '%Dark Badge%' OR name LIKE '%Red Folder%' LIMIT 1")
        row = cursor.fetchone()
        
        narrative = sanitize_signal_text("Red Folder Fusion: Epoch-linked Epstein-Zuma-Matlala nexus bridging 2010 manifests to 2026 Madlanga Commission anomalies.")
        cases_inserted = 0
        
        if row:
            case_id, case_name = row[0], row[1]
            if not dry_run:
                cursor.execute("UPDATE cases SET hypothesis = ? WHERE case_id = ?", (narrative, case_id))
                logging.info(f"Updated Hypothesis for existing case: {case_id}")
        else:
            case_name = sanitize_signal_text("OP: DARK BADGE / RED FOLDER (Syndicate Fusion)")
            case_id = dynamic_insert(cursor, 'cases', {
                "name": case_name,
                "description": sanitize_signal_text("Parallel Intel Funnel: Phase 56 Fusion."),
                "hypothesis": narrative,
                "status": "active",
                "created_at": datetime.now().isoformat()
            }, dry_run)
            cases_inserted = 1
            
        if dry_run: case_id = 9999

        # 2. Extract Syndicate Matrix
        cursor.execute(f"""
            SELECT DISTINCT a.actor_id 
            FROM actors a
            JOIN entity_relationships r ON (a.actor_id = r.subject_actor_id OR a.actor_id = r.object_actor_id)
            WHERE r.relation_type IN ('co_occurrence', 'osint_match') AND r.confidence >= {CONFIDENCE_THRESHOLD}
        """)
        actors = [row[0] for row in cursor.fetchall()]

        if not actors:
            logging.warning("No actors met threshold. Disengaging.")
            if not dry_run: conn.rollback()
            return

        # 3. Dynamic Epoch Junction Routing
        epoch_event_id = int(datetime.now().timestamp()) if not dry_run else 1741999999

        # 4. Link Event to Case (Junction Table)
        dynamic_insert(cursor, 'case_events', {
            "case_id": case_id, 
            "event_id": epoch_event_id, 
            "note": "Phase 56: Junction Funnel Anchor",
            "pinned_at": datetime.now().isoformat()
        }, dry_run)
        case_events_inserted = 1

        # 5. Funnel Actors into the Epoch Event
        inserted, suppressed = 0, 0
        for actor_id in actors:
            cursor.execute("SELECT 1 FROM event_actors WHERE event_id=? AND actor_id=?", (epoch_event_id, actor_id))
            if cursor.fetchone():
                suppressed += 1
                continue
                
            dynamic_insert(cursor, 'event_actors', {
                "event_id": epoch_event_id, 
                "actor_id": actor_id, 
                "role": "syndicate_node"
            }, dry_run)
            inserted += 1

        # 🟡 GAP 2 FIXED: Post-Validation
        if not dry_run:
            conn.commit()
            cursor.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            assert integrity == "ok", f"Integrity Check Failed: {integrity}"

            post_counts = get_table_counts(cursor, target_tables)
            logging.info(f"Post-Flight Checks: {post_counts}")
            
            assert post_counts['cases'] == pre_counts['cases'] + cases_inserted, "Cases validation mismatch."
            assert post_counts['case_events'] == pre_counts['case_events'] + case_events_inserted, "Case Events validation mismatch."
            assert post_counts['event_actors'] == pre_counts['event_actors'] + inserted, "Event Actor validation mismatch."

        logging.info(f"V6.3 Complete. Genesis Case: ID={case_id} | Name='{case_name}'")
        logging.info(f"Epoch Event ID: {epoch_event_id}")
        logging.info(f"Actors Grafted: {inserted} | Suppressed: {suppressed}")

    except Exception as e:
        logging.error(f"RUNTIME FAULT: {e}")
        if not dry_run: conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE V6.3 Hardened Junction-Only Syndicate Compiler")
    parser.add_argument("--dry-run", action="store_true", help="Simulate case creation")
    args = parser.parse_args()
    compile_syndicate(dry_run=args.dry_run)