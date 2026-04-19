from pathlib import Path
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
    ensure_actor_artifact_link,
)


def main():
    parser = argparse.ArgumentParser(description='Anchor NPA data into FORGE DB.')
    parser.add_argument('--db', default=str(Path(__file__).resolve().parent.parent / "database.db"), help='Path to FORGE SQLite database')
    parser.add_argument('--actor', default='National Prosecuting Authority', help='Actor name')
    parser.add_argument('--artifact-title', default='Access to Information Manual', help='Artifact title')
    parser.add_argument('--artifact-description', default='Official PAIA manual describing how to request information from the NPA', help='Artifact description')
    parser.add_argument('--external-id', default='npa-paia-01', help='Signal external_id')
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
    linked = ensure_actor_artifact_link(cur, actor_id, artifact_id)

    signal_name = f"npa-signal-{datetime.now(UTC).isoformat()}"
    signal_id, signal_created = ensure_signal(
        cur,
        source='npa.gov.za',
        external_id=args.external_id,
        title='NPA PAIA Manual observed',
        content='NPA PAIA Manual defines public access pathways to prosecutorial information',
        status='reviewed',
        stream='GLOBAL',
        source_type='live',
        artifact_id=artifact_id,
        signal_id=signal_name
    )

    conn.commit()
    conn.close()

    print(json.dumps({
        'db_path': args.db,
        'actor_id': actor_id,
        'artifact_id': artifact_id,
        'signal_id': signal_id,
        'actor_created_or_exists': True,
        'artifact_created_or_exists': True,
        'actor_artifact_linked': linked,
        'signal_created': signal_created
    }, indent=2))


if __name__ == '__main__':
    main()