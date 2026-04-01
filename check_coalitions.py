import sqlite3

conn = sqlite3.connect('database.db')

print('--- actor_events: events with 2+ actors ---')
rows = conn.execute(
    'SELECT event_id, COUNT(*) as c FROM actor_events '
    'GROUP BY event_id HAVING c > 1 ORDER BY c DESC'
).fetchall()
print(f'Count: {len(rows)}')
for r in rows[:10]:
    print(f'  event {r[0]}: {r[1]} actors')

print()
print('--- event_actors: events with 2+ actors ---')
rows2 = conn.execute(
    'SELECT event_id, COUNT(*) as c FROM event_actors '
    'GROUP BY event_id HAVING c > 1 ORDER BY c DESC'
).fetchall()
print(f'Count: {len(rows2)}')
for r in rows2[:10]:
    print(f'  event {r[0]}: {r[1]} actors')

print()
print('--- actor_events: actors with 2+ events ---')
rows3 = conn.execute(
    'SELECT actor_id, COUNT(*) as c FROM actor_events '
    'GROUP BY actor_id HAVING c > 1 ORDER BY c DESC'
).fetchall()
print(f'Count: {len(rows3)}')
for r in rows3[:10]:
    print(f'  actor {r[0]}: {r[1]} events')

conn.close()