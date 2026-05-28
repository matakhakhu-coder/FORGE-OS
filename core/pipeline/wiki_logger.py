from core.db.connection import get_connection
import json
from datetime import datetime, timezone

class WikiLogger:
    def __init__(self):
        pass

    def log(self, actor_id, event_id, artifact=None, narrative="", context=None):
        with get_connection() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO wiki_entries (actor_id, event_id, artifact, timestamp, narrative, context)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                actor_id,
                event_id,
                artifact,
                datetime.now(timezone.utc),
                narrative,
                json.dumps(context) if context else "{}"
            ))
            conn.commit()
