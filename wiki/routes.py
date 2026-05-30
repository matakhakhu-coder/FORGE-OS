#!/usr/bin/env python3

"""

FORGE Wiki — Routing Blueprint

==============================

Handles the Presentation Layer for the Local Knowledge Base.

Provides routes for the index, individual dossiers, and the relationship graph.



Author: FORGE x

"""



import sqlite3

from pathlib import Path

from flask import Blueprint, render_template, abort, current_app



wiki_bp = Blueprint('wiki', __name__)



def get_db_connection():

    # Maps directly to the existing DB_PATH logic in app.py

    db_path = Path(current_app.root_path) / 'database.db'

    conn = sqlite3.connect(db_path, timeout=60)

    conn.row_factory = sqlite3.Row

    return conn



@wiki_bp.route('/', endpoint='index')

def wiki_index():

    """Knowledge Base Landing Page."""

    conn = get_db_connection()

    

    # Fetch recently updated articles

    recent_articles = conn.execute("""

        SELECT title, slug, summary, last_updated, tags

        FROM wiki_articles

        ORDER BY last_updated DESC

        LIMIT 20

    """).fetchall()

    

    # Fetch top tags/categories for the sidebar

    tags = conn.execute("""

        SELECT tags, COUNT(*) as count 

        FROM wiki_articles 

        WHERE tags IS NOT NULL AND tags != '' 

        GROUP BY tags 

        ORDER BY count DESC 

        LIMIT 10

    """).fetchall()

    conn.close()

    

    return render_template("wiki_index.html", articles=recent_articles, tags=tags)



@wiki_bp.route('/<slug>')

def wiki_article(slug):

    """Render a specific Wiki Dossier."""

    conn = get_db_connection()

    article = conn.execute(

        "SELECT * FROM wiki_articles WHERE slug = ?", (slug,)

    ).fetchone()

    

    if article is None:

        conn.close()

        abort(404)

        

    # Get "What Links Here" (Inbound links pointing to this article)

    inbound = conn.execute("""

        SELECT wa.title, wa.slug

        FROM wiki_links wl

        JOIN wiki_articles wa ON wl.source_id = wa.article_id

        WHERE wl.target_id = ?

    """, (article['article_id'],)).fetchall()



    # Get Outbound links (Articles this page points to)

    outbound = conn.execute("""

        SELECT wa.title, wa.slug

        FROM wiki_links wl

        JOIN wiki_articles wa ON wl.target_id = wa.article_id

        WHERE wl.source_id = ?

    """, (article['article_id'],)).fetchall()

    

    conn.close()

    

    return render_template(
        "wiki_article.html",
        article=article,
        inbound=inbound,
        outbound=outbound,
    )



@wiki_bp.route('/graph')

def wiki_graph():

    """Data endpoint for the Network Graph Visualization."""

    conn = get_db_connection()

    nodes = conn.execute("""

        SELECT article_id as id, title as label, tags as group_name 

        FROM wiki_articles

    """).fetchall()

    edges = conn.execute("""

        SELECT source_id as source, target_id as target 

        FROM wiki_links

    """).fetchall()

    conn.close()

    

    return render_template(
        "wiki_graph.html",
        nodes=[dict(n) for n in nodes],
        edges=[dict(e) for e in edges],
    )
