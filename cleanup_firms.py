import sqlite3
conn = sqlite3.connect('database.db')
r = conn.execute(
    "DELETE FROM sentinel_alerts "
    "WHERE alert_type='correlation_escalation' "
    "AND summary LIKE '%[firms]%' "
    "AND status='new'"
)
print('Deleted', r.rowcount, 'stale FIRMS alerts')
conn.commit()
conn.close()