import argparse
import json
from datetime import datetime, UTC


# ── FORGE path bootstrap (added by refactor) ──────────────────────────
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from utils.forge_db import (
    open_database,
    ensure_actor,
    ensure_artifact,
    ensure_signal,
    ensure_actor_signal_link,
)


def main():
    parser = argparse.ArgumentParser(description='Capture NPA signal into FORGE DB.')
    parser.add_argument('--db', default='c:\\Users\\matam\\Projects\\FORGE\\database.db', help='Path to FORGE SQLite database')
    parser.add_argument('--actor', default='National Prosecuting Authority', help='Actor name')
    parser.add_argument('--artifact-title', default='ACCESS TO INFORMATION MANUAL', help='Artifact title')
    parser.add_argument('--artifact-description', default='Official guide from NPA website', help='Artifact description')
    parser.add_argument('--external-id', default='npa-access-info-manual', help='Signal external_id')
    args = parser.parse_args()

    conn = open_database(args.db)
    cur = conn.cursor()

    actor_id = ensure_actor(cur, args.actor, 'government')

    artifact_id = ensure_artifact(
        cur,
        args.artifact_title,
        args.artifact_description,
        'document',
        'government'
    )

    signal_id, signal_created = ensure_signal(
        cur,
        'NPA document',
        args.external_id,
        'NPA Access to Information Manual',
        'Official document content',
        'raw',
        'npa_feed',
        'document',
        artifact_id=artifact_id
    )

    link_created = ensure_actor_signal_link(cur, actor_id, signal_id)

    conn.commit()

    actor_row = cur.execute('SELECT * FROM actors WHERE actor_id = ?', (actor_id,)).fetchall()
    artifact_row = cur.execute('SELECT * FROM artifacts WHERE artifact_id = ?', (artifact_id,)).fetchall()
    signal_row = cur.execute('SELECT * FROM signals WHERE signal_id = ?', (signal_id,)).fetchall()
    link_row = cur.execute('SELECT * FROM actor_signals WHERE actor_id = ? AND signal_id = ?', (actor_id, signal_id)).fetchall()

    conn.close()

    print(json.dumps({
        'db_path': args.db,
        'actor_id': actor_id,
        'artifact_id': artifact_id,
        'signal_id': signal_id,
        'actor_exists': bool(actor_row),
        'artifact_created': bool(artifact_row),
        'signal_created': bool(signal_row),
        'link_created': bool(link_row),
        'signal_was_new': signal_created
    }, indent=2))


if __name__ == '__main__':
    main()