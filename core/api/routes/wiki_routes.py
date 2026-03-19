import sqlite3
import json
from flask import Blueprint, request, jsonify, render_template, abort
from core.pipeline.wiki_logger import WikiLogger
from core.db.connection import get_connection

wiki_logger = WikiLogger()
wiki_bp = Blueprint('wiki', __name__)

def register_wiki_routes(app):
    @app.route("/api/wiki/log", methods=["POST"])
    def log_entry():
        data = request.json or {}
        actor_id = data.get("actor_id")
        narrative = data.get("narrative")

        if not actor_id or narrative is None:
            return jsonify({"error": "actor_id and narrative are required"}), 400

        try:
            wiki_logger.log(
                actor_id=actor_id,
                event_id=data.get("event_id"),
                artifact=data.get("artifact"),
                narrative=narrative,
                context=data.get("context")
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        return jsonify({"status": "ok"})

    @app.route('/api/wiki/pulse_simulator', methods=['GET'])
    def pulse_simulator():
        import random, uuid, time

        actors = [f"Actor-{i+1}" for i in range(5)]
        events = ["pulse_detected", "artifact_updated", "state_change", "signal_emitted", "context_shift"]
        num_signals = int(request.args.get('num_signals', 50))

        entries = []
        for _ in range(num_signals):
            actor_id = random.choice(actors)
            event_id = random.choice(events)
            artifact = f"Artifact-{uuid.uuid4().hex[:6]}"
            narrative = f"Simulated event for {actor_id} -> {artifact} at {time.time()}"
            context = {
                "pulse_strength": round(random.uniform(0, 1), 3),
                "sentinel_flags": random.sample(["alpha", "beta", "gamma", "delta"], 2),
                "graph_node": f"Node-{random.randint(1,20)}"
            }

            wiki_logger.log(actor_id=actor_id, event_id=event_id, artifact=artifact, narrative=narrative, context=context)
            entries.append({"actor_id": actor_id, "event_id": event_id, "artifact": artifact, "narrative": narrative, "context": context})

        return jsonify({"status": "ok", "generated": len(entries), "entries": entries})

    @app.route('/api/wiki/synthesize', methods=['GET', 'POST'])
    def synthesize():
        from core.pipeline.synthesizer import run_synthesis
        result = run_synthesis()
        return jsonify(result)

    @app.route("/api/wiki/get/<actor_id>", methods=["GET"])
    def get_entries(actor_id):
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM wiki_entries WHERE actor_id=?", (actor_id,))
            rows = c.fetchall()
            return jsonify(rows)

@wiki_bp.route('/api/wiki/graph_data', methods=['GET'])
def graph_data():
    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'
    where_clause = '1=1' if lens == 'all' else f"source_type = '{lens}'"

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        nodes = conn.execute(f"""
            SELECT slug AS id, slug AS slug, title, tags, behavior, max_pulse_strength, features
            FROM wiki_articles
            WHERE {where_clause}
        """).fetchall()

        link_columns = {r[1] for r in conn.execute("PRAGMA table_info('wiki_links')").fetchall()}
        article_columns = {r[1] for r in conn.execute("PRAGMA table_info('wiki_articles')").fetchall()}

        if 'source_slug' in link_columns and 'target_slug' in link_columns:
            links = conn.execute('SELECT source_slug AS source, target_slug AS target, connection_type FROM wiki_links').fetchall()
        elif 'source_id' in link_columns and 'target_id' in link_columns:
            id_col = 'article_id' if 'article_id' in article_columns else 'id'
            select_expr = 'wl.connection_type' if 'connection_type' in link_columns else "'related' AS connection_type"
            links = conn.execute(f"""
                SELECT wa.slug AS source, wb.slug AS target, {select_expr}
                FROM wiki_links wl
                JOIN wiki_articles wa ON wl.source_id = wa.{id_col}
                JOIN wiki_articles wb ON wl.target_id = wb.{id_col}
            """).fetchall()
        else:
            links = []

    return jsonify({
        'nodes': [dict(n) for n in nodes],
        'links': [dict(l) for l in links],
    })


@wiki_bp.route('/')
def wiki_index():
    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'
    where_clause = '1=1' if lens == 'all' else f"source_type = '{lens}'"

    with get_connection() as conn:
        articles = conn.execute(f"SELECT * FROM wiki_articles WHERE {where_clause} ORDER BY last_updated DESC").fetchall()
        tags = conn.execute(f"SELECT tags, COUNT(*) as count FROM wiki_articles WHERE {where_clause} GROUP BY tags").fetchall()
    return render_template('wiki_index.html', articles=articles, tags=tags, lens=lens)


@wiki_bp.route('/diagnostics')
def wiki_diagnostics():
    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'
    where_clause = '1=1' if lens == 'all' else f"source_type = '{lens}'"

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT slug, title, behavior, max_pulse_strength, features FROM wiki_articles WHERE {where_clause}").fetchall()

    actors = []
    for row in rows:
        features = {}
        try:
            if row['features']:
                features = json.loads(row['features'])
        except Exception:
            features = {}

        actors.append({
            'slug': row['slug'],
            'title': row['title'],
            'behavior': row['behavior'] or 'Unknown',
            'max_pulse_strength': float(row['max_pulse_strength'] or 0.0),
            'trend': float(features.get('trend', 0.0)),
            'velocity': float(features.get('velocity', 0.0)),
            'volatility': float(features.get('volatility', 0.0)),
            'stability': float(features.get('stability_index', 0.0))
        })

    if actors:
        most_volatile = max(actors, key=lambda x: x['volatility'])
        fastest_escalating = max(actors, key=lambda x: x['velocity'])
        global_stability = sum(x['stability'] for x in actors) / len(actors)
    else:
        most_volatile = None
        fastest_escalating = None
        global_stability = 0.0

    return render_template(
        'diagnostics.html',
        most_volatile=most_volatile,
        fastest_escalating=fastest_escalating,
        global_stability=global_stability
    )


@wiki_bp.route('/article/<slug>')
def wiki_article(slug):
    with get_connection() as conn:
        article = conn.execute("SELECT * FROM wiki_articles WHERE slug = ?", (slug,)).fetchone()
        if article is None:
            abort(404)

        link_columns = {r[1] for r in conn.execute("PRAGMA table_info('wiki_links')").fetchall()}
        article_columns = {r[1] for r in conn.execute("PRAGMA table_info('wiki_articles')").fetchall()}

        if 'source_slug' in link_columns and 'target_slug' in link_columns:
            inbound = conn.execute("SELECT * FROM wiki_links WHERE target_slug = ?", (slug,)).fetchall()
            outbound = conn.execute("SELECT * FROM wiki_links WHERE source_slug = ?", (slug,)).fetchall()
        elif 'source_id' in link_columns and 'target_id' in link_columns:
            article_key = 'article_id' if 'article_id' in article_columns else 'id'
            source_id = article[article_key]
            inbound = conn.execute("SELECT * FROM wiki_links WHERE target_id = ?", (source_id,)).fetchall()
            outbound = conn.execute("SELECT * FROM wiki_links WHERE source_id = ?", (source_id,)).fetchall()
        else:
            inbound, outbound = [], []

    return render_template('wiki_article.html', article=article, inbound=inbound, outbound=outbound)


@wiki_bp.route('/graph')
def wiki_graph():
    # Placeholder for the D3/Graph visualization page
    return render_template('wiki_graph.html')

