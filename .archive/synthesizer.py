import re
import json
from core.db.connection import get_connection
from core.pipeline.wiki_logger import WikiLogger
from core.pipeline.intelligence import compute_temporal_features, classify_behavior, detect_patterns

wiki_logger = WikiLogger()


def _slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug or 'unnamed'


class DossierSynthesizer:
    def run(self, raw_signal: dict):
        title = raw_signal.get('title', 'untitled')
        raw_data = raw_signal.get('raw_data', '')
        tags = raw_signal.get('tags', '')

        slug = _slugify(title)
        content_html = f"<h1>{title}</h1><p>{str(raw_data).replace('\n', '<br/>')}</p>"
        summary = str(raw_data)[:150]

        with get_connection() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO wiki_articles (slug, title, summary, content_html, tags, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (slug, title, summary, content_html, tags))
            conn.commit()

        wiki_logger.log(actor_id='SYNTHESIZER', event_id=None, artifact=None, narrative=title, context={'slug': slug})

        return {'slug': slug, 'title': title, 'tags': tags, 'summary': summary}


def synthesize_dossier(signal_data: dict):
    return DossierSynthesizer().run(signal_data)


def generate_brief(actor_id, pulse_count, max_pulse, last_5, anchors, brief_json):
    if max_pulse >= 0.8:
        status_label, status_color = "CRITICAL", "var(--red, #ef4444)"
    elif max_pulse >= 0.5:
        status_label, status_color = "ELEVATED", "var(--amber, #f59e0b)"
    else:
        status_label, status_color = "NOMINAL", "var(--green, #10b981)"

    chronicle_html = "".join([
        f"<li style='margin-bottom: 0.5rem;'><span class='mono' style='color:var(--text-dim);'>DETECTED:</span> {eid}</li>"
        for eid in last_5
    ])

    if anchors:
        anchors_html = "".join([
            f"<a href='/wiki/article/{anchor}' class='tag-pill'>{anchor}</a>"
            for anchor in anchors
        ])
    else:
        anchors_html = "<span style='color:var(--text-dim);'>No known connections</span>"

    behavioral_features = brief_json.get('behavioral_features', {})
    behavioral_classification = brief_json.get('behavioral_classification', 'Unknown')
    behavioral_patterns = brief_json.get('behavioral_patterns', [])

    pattern_list_html = "" if not behavioral_patterns else "".join([
        f"<li style='margin-bottom: 0.25rem;'>{p}</li>" for p in behavioral_patterns
    ])

    brief_html = f"""
    <div class=\"intelligence-brief\" style=\"border: 1px solid var(--border-color); border-radius: 8px; padding: 1.5rem; background: var(--surface-bg);\"> 
        <div style=\"display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1rem;\">
            <h3 style=\"margin: 0; font-family: monospace;\">TACTICAL SUMMARY</h3>
            <div style=\"color: {status_color}; font-weight: bold; font-family: monospace; border: 1px solid {status_color}; padding: 0.25rem 0.75rem; border-radius: 4px;\">STATUS: {status_label}</div>
        </div>

        <div class=\"stats-grid\" style=\"display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 2rem;\">
            <div style=\"background: var(--bg-color); padding: 1rem; border-radius: 4px;\">
                <div style=\"font-size: 0.8rem; color: var(--text-dim); text-transform: uppercase;\">Total Pulses</div>
                <div style=\"font-size: 1.5rem; font-weight: bold;\">{pulse_count}</div>
            </div>
            <div style=\"background: var(--bg-color); padding: 1rem; border-radius: 4px;\">
                <div style=\"font-size: 0.8rem; color: var(--text-dim); text-transform: uppercase;\">Peak Intensity</div>
                <div style=\"font-size: 1.5rem; font-weight: bold;\">{max_pulse:.2f}</div>
            </div>
        </div>

        <h4 style=\"margin-bottom: 0.5rem; font-family: monospace; color: var(--text-ghost);\">RECENT BEHAVIORAL CHRONICLE</h4>
        <ul style=\"list-style: none; padding-left: 0; margin-bottom: 2rem;\">{chronicle_html}</ul>

        <h4 style=\"margin-bottom: 0.5rem; font-family: monospace; color: var(--text-ghost);\">RELATIONAL FOOTPRINT</h4>
        <div class=\"tag-cloud\" style=\"display: flex; gap: 0.5rem; flex-wrap: wrap;\">{anchors_html}</div>

        <h4 style=\"margin-top: 1.5rem; margin-bottom: 0.5rem; font-family: monospace; color: var(--text-ghost);\">BEHAVIORAL ANALYSIS</h4>
        <div style=\"display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin-bottom: 1rem;\">
            <div style=\"background: var(--bg-color); padding: 0.75rem; border-radius: 4px;\">
                <div style=\"font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase;\">Classification</div>
                <div style=\"font-weight: bold;\">{behavioral_classification}</div>
            </div>
            <div style=\"background: var(--bg-color); padding: 0.75rem; border-radius: 4px;\">
                <div style=\"font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase;\">Trend</div>
                <div style=\"font-weight: bold;\">{behavioral_features.get('trend', 0.0):.3f}</div>
            </div>
            <div style=\"background: var(--bg-color); padding: 0.75rem; border-radius: 4px;\">
                <div style=\"font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase;\">Velocity</div>
                <div style=\"font-weight: bold;\">{behavioral_features.get('velocity', 0.0):.3f}</div>
            </div>
            <div style=\"background: var(--bg-color); padding: 0.75rem; border-radius: 4px;\">
                <div style=\"font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase;\">Volatility</div>
                <div style=\"font-weight: bold;\">{behavioral_features.get('volatility', 0.0):.3f}</div>
            </div>
            <div style=\"background: var(--bg-color); padding: 0.75rem; border-radius: 4px;\">
                <div style=\"font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase;\">Stability</div>
                <div style=\"font-weight: bold;\">{behavioral_features.get('stability_index', 0.0):.3f}</div>
            </div>
        </div>

        <h4 style=\"font-size: 0.9rem; margin-bottom: 0.5rem; color: var(--text-dim);\">Detected Patterns</h4>
        <ul style=\"margin-top: 0; list-style: disc; margin-left: 1.25rem; margin-bottom: 2rem;\">{pattern_list_html}</ul>

        <details style=\"margin-top: 2rem; border-top: 1px dashed var(--border-color); padding-top: 1rem;\">
            <summary style=\"cursor: pointer; color: var(--text-dim); font-family: monospace;\">[+] VIEW RAW TELEMETRY</summary>
            <pre style=\"margin-top: 1rem; font-size: 0.85rem;\">{json.dumps(brief_json, indent=2)}</pre>
        </details>
    </div>
    """

    return brief_html


def run_synthesis():
    with get_connection() as conn:
        c = conn.cursor()
        # Ensure new intelligence columns exist for compatibility with old DB schema
        existing_cols = {r[1] for r in c.execute("PRAGMA table_info('wiki_articles')").fetchall()}
        if 'behavior' not in existing_cols:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN behavior TEXT")
            existing_cols.add('behavior')
        if 'features' not in existing_cols:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN features TEXT")
            existing_cols.add('features')
        if 'max_pulse_strength' not in existing_cols:
            c.execute("ALTER TABLE wiki_articles ADD COLUMN max_pulse_strength REAL")
            existing_cols.add('max_pulse_strength')

        rows = c.execute('SELECT actor_id, context, timestamp, event_id FROM wiki_entries WHERE actor_id IS NOT NULL').fetchall()

        if not rows:
            return {'status': 'empty', 'processed': 0}

        actors = {}
        links = set()

        for row in rows:
            actor_id = row['actor_id']
            context_val = row['context']
            actors.setdefault(actor_id, []).append(row)

            try:
                context_json = json.loads(context_val) if context_val else {}
            except Exception:
                context_json = {}

            graph_node = context_json.get('graph_node')
            if graph_node:
                source_slug = _slugify(actor_id)
                target_slug = _slugify(str(graph_node))
                links.add((source_slug, target_slug))

        # Ensure graph nodes exist and write dossier articles
        for actor_id, entries in actors.items():
            slug = _slugify(actor_id)
            count = len(entries)

            pulse_strengths = []
            event_ids = []
            nodes = set()

            for e in entries:
                event_ids.append(e['event_id'] or '')
                try:
                    context_json = json.loads(e['context']) if e['context'] else {}
                except Exception:
                    context_json = {}

                if 'pulse_strength' in context_json:
                    try:
                        pulse_strengths.append(float(context_json['pulse_strength']))
                    except Exception:
                        pass

                graph_node = context_json.get('graph_node')
                if graph_node:
                    nodes.add(graph_node)

            avg_pulse = round(sum(pulse_strengths) / len(pulse_strengths), 3) if pulse_strengths else 0.0
            last_5 = event_ids[-5:]
            anchors = sorted(nodes)

            behavioral_features = compute_temporal_features(pulse_strengths)
            behavioral_classification = classify_behavior(behavioral_features)
            behavioral_patterns = detect_patterns(pulse_strengths, event_ids)

            brief_json = {
                'actor_id': actor_id,
                'pulse_count': count,
                'avg_pulse_strength': avg_pulse,
                'max_pulse_strength': max(pulse_strengths) if pulse_strengths else 0.0,
                'last_5_events': last_5,
                'anchors': anchors,
                'behavioral_features': behavioral_features,
                'behavioral_classification': behavioral_classification,
                'behavioral_patterns': behavioral_patterns,
            }

            content_html = generate_brief(
                actor_id=actor_id,
                pulse_count=count,
                max_pulse=brief_json['max_pulse_strength'],
                last_5=last_5,
                anchors=anchors,
                brief_json=brief_json
            )
            summary = f"{actor_id} exhibits high-frequency pulses across {len(nodes)} separate node(s)."

            c.execute('''
                INSERT OR REPLACE INTO wiki_articles (slug, title, summary, content_html, tags, behavior, features, max_pulse_strength, source_type, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'live', CURRENT_TIMESTAMP)
            ''', (
                slug,
                actor_id,
                summary,
                content_html,
                'actor',
                behavioral_classification,
                json.dumps(behavioral_features),
                brief_json['max_pulse_strength'],
            ))

        # keep nodes in wiki_articles for graph nodes as well
        for _, target_slug in links:
            existing = c.execute('SELECT 1 FROM wiki_articles WHERE slug = ?', (target_slug,)).fetchone()
            if not existing:
                c.execute('''
                    INSERT OR REPLACE INTO wiki_articles (slug, title, summary, content_html, tags, behavior, features, max_pulse_strength, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (
                    target_slug,
                    target_slug,
                    'Graph node generated from signal context',
                    '<p>Auto-generated graph node.</p>',
                    'graph_node',
                    'Unknown',
                    json.dumps({'trend': 0.0, 'velocity': 0.0, 'volatility':0.0, 'stability_index':1.0}),
                    0.0,
                ))

        # Insert wiki_links in normalized form (avoid duplicate entries)
        for source_slug, target_slug in links:
            # exists? -> skip if present
            exists = c.execute('SELECT 1 FROM wiki_links WHERE source_slug = ? AND target_slug = ?', (source_slug, target_slug)).fetchone()
            if not exists:
                c.execute('''
                    INSERT INTO wiki_links (source_slug, target_slug, connection_type) VALUES (?, ?, ?)
                ''', (source_slug, target_slug, 'related'))

        conn.commit()

    wiki_logger.log(actor_id='SYNTHESIZER', event_id=None, artifact=None,
                    narrative='run_synthesis', context={'actors': len(actors), 'links': len(links)})

    return {'status': 'ok', 'processed_actors': len(actors), 'created_links': len(links)}
