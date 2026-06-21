#!/usr/bin/env python3
from __future__ import annotations

"""
Pages blueprint — extracted from app.py.

Routes: /, /events, /actors, /search, /timeline, /api/timeline,
        /feed, /api/feed, /artifacts, /intel, /media/<path:filepath>
"""

import sqlite3

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from core.web.helpers import get_db, MEDIA_DIR

pages_bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------------------
# Route: / — main dashboard
# ---------------------------------------------------------------------------

@pages_bp.route("/")
def index():
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    # Case-level filter -- when set, scope events + actors to one case
    case_id = request.args.get('case_id', type=int)

    artifact_where = ""
    event_where = ""
    artifact_params = []
    event_params = []
    if lens != 'all':
        artifact_where = "WHERE a.source_type = ?"
        artifact_params = [lens]
        event_where = "WHERE e.source_type = ?"
        event_params = [lens]

    actor_where = ""
    actor_params = []
    if lens != 'all':
        actor_where = "WHERE ac.source_type = ?"
        actor_params = [lens]

    stats = {
        "artifacts": db.execute(f"SELECT COUNT(*) FROM artifacts a {artifact_where}", artifact_params).fetchone()[0],
        "events":    db.execute(f"SELECT COUNT(*) FROM events e {event_where}", event_params).fetchone()[0],
        "actors":    db.execute(f"SELECT COUNT(*) FROM actors ac {actor_where}", actor_params).fetchone()[0],
    }

    recent_artifacts = db.execute(f"""
        SELECT a.artifact_id, a.title, a.type, a.date, a.source, a.thumbnail,
               e.title AS event_title, e.event_id
        FROM   artifacts a
        LEFT   JOIN events e ON e.event_id = a.event_id
        {artifact_where}
        ORDER  BY a.created_at DESC
        LIMIT  6
    """, artifact_params).fetchall()

    if case_id:
        recent_events = db.execute("""
            SELECT e.event_id, e.title, e.date, e.category, e.location
            FROM   events e
            JOIN   case_events ce ON ce.event_id = e.event_id
            WHERE  ce.case_id = ?
            ORDER  BY e.date DESC
            LIMIT  10
        """, (case_id,)).fetchall()
    else:
        recent_events = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category, e.location
            FROM   events e
            {event_where}
            ORDER  BY e.date DESC
            LIMIT  5
        """, event_params).fetchall()

    type_breakdown = db.execute(f"""
        SELECT type, COUNT(*) AS cnt
        FROM   artifacts a
        {artifact_where}
        GROUP  BY type
        ORDER  BY cnt DESC
    """, artifact_params).fetchall()

    # Phase 17: 48-hour signal pulse (hourly buckets for Chart.js)
    pulse_source_clause = ""
    pulse_params = []
    if lens != 'all':
        pulse_source_clause = "AND source_type = ?"
        pulse_params = [lens]

    try:
        pulse_rows = db.execute("""
            WITH RECURSIVE hours(n) AS (
                SELECT 0 UNION ALL SELECT n+1 FROM hours WHERE n < 47
            ),
            buckets AS (
                SELECT strftime('%Y-%m-%dT%H:00',
                       datetime('now', '-' || (47-n) || ' hours')) AS bucket
                FROM hours
            ),
            counts AS (
                SELECT strftime('%Y-%m-%dT%H:00', timestamp) AS bucket,
                       COUNT(*)       AS total,
                       SUM(is_priority) AS priority
                FROM signals
                WHERE timestamp >= datetime('now', '-48 hours')
                  """ + pulse_source_clause + """
                GROUP BY bucket
            )
            SELECT b.bucket,
                   COALESCE(c.total,    0) AS total,
                   COALESCE(c.priority, 0) AS priority
            FROM   buckets b
            LEFT   JOIN counts c ON c.bucket = b.bucket
            ORDER  BY b.bucket ASC
        """, pulse_params).fetchall()
        pulse_data = [dict(r) for r in pulse_rows]
    except Exception:
        pulse_data = []

    # Phase 17: signal summary stats for dashboard cards
    try:
        signal_stats = db.execute("""
            SELECT COUNT(*)                                               AS total,
                   SUM(CASE WHEN status='raw'   THEN 1 ELSE 0 END)       AS raw,
                   SUM(CASE WHEN is_priority=1  THEN 1 ELSE 0 END)       AS priority,
                   SUM(CASE WHEN source='usgs'  THEN 1 ELSE 0 END)       AS usgs,
                   SUM(CASE WHEN source='gdelt' THEN 1 ELSE 0 END)       AS gdelt,
                   SUM(CASE WHEN source='GDACS' THEN 1 ELSE 0 END)       AS gdacs,
                   SUM(CASE WHEN source='firms' THEN 1 ELSE 0 END)       AS firms
            FROM signals
        """).fetchone()
    except Exception:
        signal_stats = None

    # Phase 23: top correlated incidents for dashboard panel
    try:
        correlated = db.execute("""
            SELECT ci.correlation_score,
                   ci.distance_km,
                   ci.time_difference_hours,
                   ci.detected_at,
                   sa.signal_id AS sid_a, sa.title AS title_a,
                   sa.source AS src_a, sa.lat AS lat_a, sa.lng AS lng_a,
                   sb.signal_id AS sid_b, sb.title AS title_b,
                   sb.source AS src_b, sb.lat AS lat_b, sb.lng AS lng_b
            FROM   correlated_incidents ci
            JOIN   signals sa ON sa.signal_id = ci.signal_a
            JOIN   signals sb ON sb.signal_id = ci.signal_b
            ORDER  BY ci.correlation_score DESC
            LIMIT  8
        """).fetchall()
        correlated = [dict(r) for r in correlated]
    except Exception:
        correlated = []

    # Phase 24: top actors by global influence score (case-scoped when case_id set)
    intelligence_leads = []
    try:
        if case_id:
            leads_rows = db.execute("""
                SELECT m.actor_id, a.name, a.type,
                       m.influence_score, m.betweenness, m.pagerank,
                       m.community_id, m.computed_at
                FROM   actor_network_metrics m
                JOIN   actors a ON a.actor_id = m.actor_id
                WHERE  m.actor_id IN (
                    SELECT DISTINCT ea.actor_id
                    FROM   event_actors ea
                    JOIN   case_events ce ON ce.event_id = ea.event_id
                    WHERE  ce.case_id = ?
                )
                ORDER  BY m.influence_score DESC LIMIT 10
            """, (case_id,)).fetchall()
        else:
            leads_rows = db.execute(
                "SELECT m.actor_id, a.name, a.type, "
                "m.influence_score, m.betweenness, m.pagerank, "
                "m.community_id, m.computed_at "
                "FROM actor_network_metrics m "
                "JOIN actors a ON a.actor_id = m.actor_id "
                "ORDER BY m.influence_score DESC LIMIT 10"
            ).fetchall()
        intelligence_leads = [dict(r) for r in leads_rows]
    except Exception:
        pass

    # Phase 25: recent new Sentinel alerts for dashboard
    try:
        sentinel_alerts_dash = db.execute(
            "SELECT id, alert_type, confidence_score, signal_count, "
            "summary, location_lat, location_lon, created_at "
            "FROM sentinel_alerts "
            "WHERE status = 'new' "
            "ORDER BY confidence_score DESC, created_at DESC "
            "LIMIT 5"
        ).fetchall()
        sentinel_alerts_dash = [dict(r) for r in sentinel_alerts_dash]
    except Exception:
        sentinel_alerts_dash = []

    # Phase 27: stream counts for dashboard summary widget
    try:
        stream_counts = dict(
            db.execute(
                "SELECT stream, COUNT(*) FROM signals "
                "WHERE stream IS NOT NULL GROUP BY stream ORDER BY COUNT(*) DESC"
            ).fetchall()
        )
    except Exception:
        stream_counts = {}

    # Active cases for the case-filter selector
    active_cases = []
    selected_case = None
    try:
        active_cases = [dict(r) for r in db.execute(
            "SELECT case_id, name, status FROM cases "
            "WHERE LOWER(status)='active' ORDER BY case_id DESC"
        ).fetchall()]
        if case_id:
            row = db.execute(
                "SELECT case_id, name FROM cases WHERE case_id=?", (case_id,)
            ).fetchone()
            if row:
                selected_case = dict(row)
    except Exception:
        pass

    return render_template(
        "index.html",
        stats=stats,
        recent_artifacts=recent_artifacts,
        recent_events=recent_events,
        type_breakdown=type_breakdown,
        pulse_data=pulse_data,
        signal_stats=signal_stats,
        correlated=correlated,
        intelligence_leads=intelligence_leads,
        sentinel_alerts_dash=sentinel_alerts_dash,
        stream_counts=stream_counts,
        active_cases=active_cases,
        selected_case=selected_case,
        selected_case_id=case_id,
    )


# ---------------------------------------------------------------------------
# Route: /events — Event list
# ---------------------------------------------------------------------------

@pages_bp.route("/events")
def events():
    db       = get_db()
    lens     = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    category = request.args.get("category", "")
    sort     = request.args.get("sort", "date_desc")

    order_map = {
        "date_desc":  "e.date DESC",
        "date_asc":   "e.date ASC",
        "title_asc":  "e.title ASC",
    }
    order_clause = order_map.get(sort, "e.date DESC")

    where_clause = []
    params       = []
    if category:
        where_clause.append("e.category = ?")
        params.append(category)
    if lens != 'all':
        where_clause.append("e.source_type = ?")
        params.append(lens)

    where_sql = "WHERE " + " AND ".join(where_clause) if where_clause else ""

    events_rows = db.execute(f"""
        SELECT e.event_id, e.title, e.summary, e.date, e.category,
               e.location,
               COUNT(a.artifact_id) AS artifact_count
        FROM   events e
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        {where_sql}
        GROUP  BY e.event_id
        ORDER  BY {order_clause}
    """, params).fetchall()

    category_where = "WHERE category IS NOT NULL"
    category_params = []
    if lens != 'all':
        category_where += " AND source_type = ?"
        category_params.append(lens)

    categories = db.execute(f"""
        SELECT DISTINCT category FROM events
        {category_where}
        ORDER  BY category
    """, category_params).fetchall()

    return render_template(
        "events.html",
        events=events_rows,
        categories=[r["category"] for r in categories],
        current_category=category,
        current_sort=sort,
    )


# ---------------------------------------------------------------------------
# Route: /actors — Actor list
# ---------------------------------------------------------------------------

@pages_bp.route("/actors")
def actors():
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    actor_where = '' if lens == 'all' else f"WHERE ac.source_type = '{lens}'"

    actors_rows = db.execute(f"""
        SELECT ac.actor_id, ac.name, ac.type, ac.description, ac.blacklisted,
               COUNT(DISTINCT all_ev.event_id)    AS event_count,
               COUNT(DISTINCT a.artifact_id)      AS artifact_count,
               COUNT(DISTINCT sa.signal_id)       AS signal_count,
               MAX(COALESCE(s.gravity_score, 0))  AS max_gravity,
               MAX(COALESCE(s.is_priority, 0))    AS has_priority_signal,
               CASE WHEN MAX(COALESCE(s.gravity_score, 0)) >= 0.55
                         OR MAX(COALESCE(s.is_priority, 0)) = 1
                    THEN 1 ELSE 0 END              AS is_targeted
        FROM   actors ac
        LEFT   JOIN (
            SELECT actor_id, event_id FROM actor_events
            UNION
            SELECT actor_id, event_id FROM event_actors
        ) all_ev ON all_ev.actor_id = ac.actor_id
        LEFT   JOIN artifacts a      ON a.event_id   = all_ev.event_id
        LEFT   JOIN signal_actors sa ON sa.actor_id  = ac.actor_id
        LEFT   JOIN signals s        ON s.signal_id  = sa.signal_id
        {actor_where}
        GROUP  BY ac.actor_id
        ORDER  BY is_targeted DESC, signal_count DESC, ac.name
    """).fetchall()

    return render_template("actors.html", actors=actors_rows, lens=lens)


# ---------------------------------------------------------------------------
# Route: /search — FTS5 full-text search
# ---------------------------------------------------------------------------

@pages_bp.route("/search")
def search():
    db    = get_db()
    query = request.args.get("q", "").strip()

    artifact_results = []
    event_results    = []
    error            = None

    if query:
        try:
            # FTS5 MATCH requires the query to be in FTS syntax.
            # Wrap bare terms so they work as a prefix search.
            fts_query = query if any(
                c in query for c in ('"', '*', 'OR', 'AND', 'NOT')
            ) else f'"{query}"'

            # Note: snippet() is unavailable on content= FTS5 tables without
            # columnsize=0.  We fetch description/summary and build excerpts
            # in the template instead.
            artifact_results = db.execute("""
                SELECT a.artifact_id, a.title, a.type, a.date, a.source,
                       a.description, a.tags, a.thumbnail,
                       e.title AS event_title, e.event_id
                FROM   artifacts_fts f
                JOIN   artifacts a ON a.artifact_id = f.rowid
                LEFT   JOIN events e ON e.event_id = a.event_id
                WHERE  artifacts_fts MATCH ?
                ORDER  BY rank
            """, (fts_query,)).fetchall()

            event_results = db.execute("""
                SELECT e.event_id, e.title, e.date, e.category,
                       e.summary, e.location,
                       COUNT(a.artifact_id) AS artifact_count
                FROM   events_fts f
                JOIN   events e ON e.event_id = f.rowid
                LEFT   JOIN artifacts a ON a.event_id = e.event_id
                WHERE  events_fts MATCH ?
                GROUP  BY e.event_id
                ORDER  BY rank
            """, (fts_query,)).fetchall()

        except sqlite3.OperationalError as exc:
            error = f"Search syntax error: {exc}"

    total = len(artifact_results) + len(event_results)

    return render_template(
        "search.html",
        query=query,
        artifact_results=artifact_results,
        event_results=event_results,
        total=total,
        error=error,
    )


# ---------------------------------------------------------------------------
# Route: /timeline — Phase 5: Chronological analysis
# ---------------------------------------------------------------------------

@pages_bp.route("/timeline")
def timeline():
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    event_where = '' if lens == 'all' else 'WHERE e.source_type = ?'
    params = [] if lens == 'all' else [lens]

    # All events with artifact counts, sorted chronologically
    rows = db.execute(f"""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               e.latitude, e.longitude,
               COUNT(a.artifact_id) AS artifact_count
        FROM   events e
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        {event_where}
        GROUP  BY e.event_id
        ORDER  BY e.date ASC, e.title ASC
    """, params).fetchall()

    # Group by year -> month for template rendering
    from collections import OrderedDict
    grouped: dict = OrderedDict()
    undated = []
    for row in rows:
        if not row["date"]:
            undated.append(row)
            continue
        parts = row["date"].split("-")
        year  = parts[0]
        month = parts[1] if len(parts) > 1 else "01"
        grouped.setdefault(year, OrderedDict()).setdefault(month, []).append(row)

    # Archive span metadata
    dated = [r for r in rows if r["date"]]
    span = {
        "start": dated[0]["date"][:7]  if dated else None,
        "end":   dated[-1]["date"][:7] if dated else None,
        "years": len(grouped),
        "total": len(rows),
    }

    # Month name lookup for template
    MONTH_NAMES = {
        "01": "January", "02": "February", "03": "March",
        "04": "April",   "05": "May",       "06": "June",
        "07": "July",    "08": "August",    "09": "September",
        "10": "October", "11": "November",  "12": "December",
    }

    return render_template(
        "timeline.html",
        grouped=grouped,
        undated=undated,
        span=span,
        MONTH_NAMES=MONTH_NAMES,
    )


# ---------------------------------------------------------------------------
# API: /api/timeline — timeline data payload
# ---------------------------------------------------------------------------

@pages_bp.route("/api/timeline")
def api_timeline():
    from flask import jsonify
    db = get_db()

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    event_where = '' if lens == 'all' else 'WHERE e.source_type = ?'
    params = [] if lens == 'all' else [lens]

    rows = db.execute(f"""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               e.latitude, e.longitude,
               COUNT(a.artifact_id) AS artifact_count
        FROM   events e
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        {event_where}
        GROUP  BY e.event_id
        ORDER  BY e.date ASC, e.title ASC
    """, params).fetchall()

    return jsonify({
        'events': [dict(r) for r in rows],
    })


# ---------------------------------------------------------------------------
# Route: /artifacts — Phase 19: Artifact gallery
# ---------------------------------------------------------------------------

@pages_bp.route("/artifacts")
def artifact_gallery():
    db       = get_db()
    atype    = request.args.get("type",   "").strip()
    source   = request.args.get("source", "").strip()
    status   = request.args.get("status", "").strip()
    q        = request.args.get("q",      "").strip()
    lens     = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'
    page     = max(1, request.args.get("page", 1, type=int))
    per_page = 24

    clauses, params = [], []
    if atype:   clauses.append("a.type = ?");                params.append(atype)
    if source:  clauses.append("a.source = ?");              params.append(source)
    if status:  clauses.append("a.processing_status = ?");   params.append(status)
    if lens != 'all':
        clauses.append("a.source_type = ?");                 params.append(lens)
    if q:
        clauses.append("(a.title LIKE ? OR a.description LIKE ? OR a.tags LIKE ?)")
        like = f"%{q}%"; params += [like, like, like]

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    try:
        total = db.execute(
            f"SELECT COUNT(*) FROM artifacts a {where}", params
        ).fetchone()[0]
        rows = db.execute(f"""
            SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                   a.file_path, a.thumbnail, a.tags, a.location,
                   a.processing_status,
                   e.title AS event_title, e.event_id AS event_id,
                   CASE WHEN a.raw_text_cache IS NOT NULL THEN 1 ELSE 0 END AS has_text
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            {where}
            ORDER  BY a.created_at DESC
            LIMIT  ? OFFSET ?
        """, params + [per_page, (page-1)*per_page]).fetchall()
    except Exception:
        # raw_text_cache / processing_status columns may not exist yet
        # (pre-migration) -- fall back to base columns
        total = db.execute(
            f"SELECT COUNT(*) FROM artifacts a {where}", params
        ).fetchone()[0]
        rows = db.execute(f"""
            SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                   a.file_path, a.thumbnail, a.tags, a.location,
                   'pending' AS processing_status,
                   e.title AS event_title, e.event_id AS event_id,
                   0 AS has_text
            FROM   artifacts a
            LEFT   JOIN events e ON e.event_id = a.event_id
            {where}
            ORDER  BY a.created_at DESC
            LIMIT  ? OFFSET ?
        """, params + [per_page, (page-1)*per_page]).fetchall()

    def _facet(col):
        try:
            if lens == 'all':
                return {r[0]: r[1] for r in db.execute(
                    f"SELECT {col}, COUNT(*) FROM artifacts "
                    f"WHERE {col} IS NOT NULL GROUP BY {col}"
                ).fetchall()}
            return {r[0]: r[1] for r in db.execute(
                f"SELECT {col}, COUNT(*) FROM artifacts "
                f"WHERE {col} IS NOT NULL AND source_type = ? GROUP BY {col}",
                (lens,)
            ).fetchall()}
        except Exception:
            return {}

    return render_template(
        "gallery.html",
        artifacts=rows, total=total, page=page,
        total_pages=max(1, (total+per_page-1)//per_page),
        per_page=per_page, active_type=atype, active_source=source,
        active_status=status, q=q,
        type_counts=_facet("type"), source_counts=_facet("source"),
        status_counts=_facet("processing_status"),
    )


# ---------------------------------------------------------------------------
# Route: /intel — Phase 39: FMS Intelligence Dashboard
# ---------------------------------------------------------------------------

@pages_bp.route("/intel")
def intel():
    """Phase 39: Unified FMS Intelligence Dashboard."""
    return render_template("intel.html")


# ---------------------------------------------------------------------------
# Route: /feed and /api/feed — Phase 29: Intelligence Feed + CT-1
# ---------------------------------------------------------------------------

# Stream -> numeric weight used in feed_score formula
_STREAM_WEIGHTS = {
    "CRIME_INTEL":    1.0,
    "PRIORITY":       0.9,
    "INFRASTRUCTURE": 0.7,
    "GLOBAL":         0.3,
}
_STREAM_WEIGHT_DEFAULT = 0.3


@pages_bp.route("/feed")
def feed():
    """Phase 29: Analyst Intelligence Feed page."""
    return render_template("feed.html")


@pages_bp.route("/api/feed")
def api_feed():
    """
    CT-1 / Phase 29.3: Unified, ranked intelligence feed with optional
    gravity-based contextual tunneling.

    Query params
    ------------
      limit      (int, default 50, max 200)
      stream     (str)   -- filter to a single stream; omit for all
      offset     (int)   -- for infinite scroll pagination
      case_id    (int)   -- activate CT-1 gravity scoring against this case
      gravity    (int)   -- gravity weight 0-100 (default 50 when case_id set)
      lens       (str)   -- 'live' | 'seed' | 'all'

    Item types returned
    -------------------
      SENTINEL_ALERT    -- new sentinel escalations; FIRMS-sourced alerts excluded
      SIGNAL            -- ranked signals; FIRMS signals shown only if high-impact
      CORRELATION       -- non-FIRMS pairs with score >= 0.85; stream_weight = 1.0
      INTELLIGENCE_LEAD -- actors in the top 10% by influence_score (90th-pct subquery)

    CT-1 gravity blending (when case_id provided)
    -----------------------------------------------
      gravity_score  = actor_match*0.50 + location_match*0.30 + keyword_match*0.20
      final_score    = (1 - gw) * feed_score + gw * gravity_score
      where gw       = gravity / 100  (default 0.50)

    FIRMS noise-reduction (Phase 29.3)
    -----------------------------------
      SENTINEL_ALERT : exclude any alert whose summary references [firms]
      CORRELATION    : exclude any pair where either signal is source='firms'
      SIGNAL (FIRMS) : show only if is_priority=1 OR isolated, and not redundant
    """
    from flask import jsonify

    db     = get_db()
    limit  = min(request.args.get("limit",  50,  type=int), 200)
    offset = max(request.args.get("offset",  0,  type=int),   0)
    stream_filter = request.args.get("stream", "").strip().upper() or None

    lens = request.args.get('lens', 'live').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'live'

    if lens == 'all':
        source_type_clause = "1=1"
        source_type_param = None
        sentinel_allowed = True
    else:
        source_type_clause = "s.source_type = ?"
        source_type_param = lens
        sentinel_allowed = (lens == 'live')

    # -- CT-1: gravity params
    try:
        ct_case_id = int(request.args.get("case_id", 0)) or None
    except (ValueError, TypeError):
        ct_case_id = None
    try:
        ct_gravity = max(0, min(100, int(request.args.get("gravity", 50))))
    except (ValueError, TypeError):
        ct_gravity = 50
    gravity_weight = ct_gravity / 100.0  # 0.0-1.0

    items = []

    # -- 1. SENTINEL_ALERT items
    # Score: confidence-weighted 0.20-1.00 -- always floats above signals.
    #
    # Phase 29.4 -- Sentinel Density Gate
    # ------------------------------------
    # FIRMS data floods sentinel_alerts via two alert_type paths, each
    # requiring a different suppression strategy:
    #
    # 1. correlation_escalation + '[firms]' in summary
    #    -> Pure pixel-pair noise (0.4 km / 0.0 h). Always exclude.
    #    -> Identified by summary LIKE '%[firms]%'
    #
    # 2. cluster_spike + 'Sources: firms' in summary
    #    -> Regional fire cluster. May be genuine (Khuzestan, Nebraska).
    #    -> Density gate: exclude only if signal_count < 20.
    #    -> 20+ signals in a 100 km radius = major regional event.
    #
    # 3. cluster_spike without FIRMS source -> standard guard (>= 3).
    #
    # 4. All other alert types (actor_match etc.) -> standard guard (>= 3).
    #
    # Cap: max 5 SENTINEL_ALERT items per feed call so other item types
    # always get page-space regardless of alert volume.
    if sentinel_allowed:
        try:
            sa_rows = db.execute("""
                SELECT id, alert_type, confidence_score, signal_count,
                       summary, location_lat, location_lon,
                       status, created_at
                FROM   sentinel_alerts
                WHERE  status = 'new'
                  AND  (
                -- Rule 1: correlation_escalation -- exclude all FIRMS pixel-pairs
                (
                    alert_type = 'correlation_escalation'
                    AND summary NOT LIKE '%[firms]%'
                    AND signal_count >= 10
                )
                OR
                -- Rule 2: cluster_spike FIRMS -- density gate, major clusters only
                (
                    alert_type = 'cluster_spike'
                    AND summary LIKE '%Sources: firms%'
                    AND signal_count >= 20
                )
                OR
                -- Rule 3: cluster_spike non-FIRMS -- standard minimum guard
                (
                    alert_type = 'cluster_spike'
                    AND summary NOT LIKE '%Sources: firms%'
                    AND signal_count >= 3
                )
                OR
                -- Rule 4: all other types (actor_match etc.) -- standard guard
                (
                    alert_type NOT IN ('correlation_escalation', 'cluster_spike')
                    AND signal_count >= 3
                )
              )
            ORDER  BY confidence_score DESC, signal_count DESC, created_at DESC
            LIMIT  5
        """).fetchall()
            for r in sa_rows:
                items.append({
                    "item_type":        "SENTINEL_ALERT",
                    "id":               f"sa-{r['id']}",
                    "alert_id":         r["id"],
                    "feed_score":       round(0.20 + r["confidence_score"] * 0.80, 4),
                    "title":            r["summary"][:120],
                    "summary":          r["summary"],
                    "alert_type":       r["alert_type"],
                    "confidence_score": r["confidence_score"],
                    "signal_count":     r["signal_count"],
                    "location_lat":     r["location_lat"],
                    "location_lon":     r["location_lon"],
                    "timestamp":        r["created_at"],
                    "stream":           None,
                    "source":           "SENTINEL",
                    "is_priority":      0,
                })
        except Exception:
            pass

    # -- 2. SIGNAL items
    # sentinel_flag: 1 if a non-dismissed sentinel alert exists within
    # ~100 km (+/-0.9 deg) in the last 6 h -- bounding-box keeps it fast.
    #
    # FIRMS high-impact filter (Phase 29.3):
    # For source='firms' signals we only surface them if they are NOT
    # already covered by a sentinel/correlation AND meet one of:
    #   (a) is_priority = 1   -- intensity threshold hit by ingestion rules
    #   (b) isolated          -- no other firms signal within +/-0.45 deg / 24 h
    #                           (+/-0.45 deg ~ 50 km at mid-latitudes)
    # Non-FIRMS signals pass through without additional filtering.
    try:
        stream_clause = "AND s.stream = :stream" if stream_filter else ""
        params = {}
        if stream_filter:
            params["stream"] = stream_filter
        if source_type_param is not None:
            params["source_type"] = source_type_param

        sig_rows = db.execute(f"""
            SELECT
                s.signal_id,
                s.title,
                s.content,
                s.source,
                s.stream,
                s.timestamp,
                s.status,
                s.is_priority,
                s.lat,
                s.lng,
                COALESCE(s.relevance_score, 1.0) AS relevance_score,
                CASE
                    WHEN s.lat IS NOT NULL AND s.lng IS NOT NULL
                         AND EXISTS (
                             SELECT 1 FROM sentinel_alerts sa
                             WHERE  sa.status    != 'dismissed'
                               AND  sa.created_at >= datetime('now', '-6 hours')
                               AND  sa.location_lat BETWEEN s.lat - 0.9 AND s.lat + 0.9
                               AND  sa.location_lon BETWEEN s.lng - 0.9 AND s.lng + 0.9
                         )
                    THEN 1.0 ELSE 0.0
                END AS sentinel_flag,
                -- Isolation flag: 1 if NO other firms signal nearby in 24 h
                CASE
                    WHEN s.source = 'firms'
                         AND s.lat IS NOT NULL AND s.lng IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM signals nb
                             WHERE  nb.source    = 'firms'
                               AND  nb.signal_id != s.signal_id
                               AND  nb.timestamp >= datetime('now', '-24 hours')
                               AND  nb.lat BETWEEN s.lat - 0.45 AND s.lat + 0.45
                               AND  nb.lng BETWEEN s.lng - 0.45 AND s.lng + 0.45
                         )
                    THEN 1 ELSE 0
                END AS firms_isolated,
                -- Redundancy flag: 1 if already in a sentinel or correlation
                CASE
                    WHEN s.source = 'firms' AND (
                         EXISTS (
                             SELECT 1 FROM sentinel_alerts sa2
                             WHERE  sa2.status != 'dismissed'
                               AND  sa2.location_lat BETWEEN s.lat - 0.9 AND s.lat + 0.9
                               AND  sa2.location_lon BETWEEN s.lng - 0.9 AND s.lng + 0.9
                         )
                      OR EXISTS (
                             SELECT 1 FROM correlated_incidents ci
                             WHERE  ci.signal_a = s.signal_id
                                OR  ci.signal_b = s.signal_id
                         )
                    )
                    THEN 1 ELSE 0
                END AS firms_redundant
            FROM  signals s
            WHERE s.status IN ('raw', 'promoted')
              AND (""" + source_type_clause + """)
            """ + stream_clause + """
            ORDER BY s.is_priority DESC, s.relevance_score DESC
            LIMIT  500
        """, params).fetchall()

        for r in sig_rows:
            # FIRMS high-impact gate
            if r["source"] == "firms":
                # Suppress if already covered by sentinel/correlation
                if r["firms_redundant"]:
                    continue
                # Only pass through if priority OR isolated
                if not r["is_priority"] and not r["firms_isolated"]:
                    continue

            sw    = _STREAM_WEIGHTS.get(r["stream"] or "GLOBAL", _STREAM_WEIGHT_DEFAULT)
            rel   = float(r["relevance_score"] or 1.0)
            prio  = float(r["is_priority"]     or 0)
            sflag = float(r["sentinel_flag"]   or 0)
            score = round(rel * 0.40 + prio * 0.30 + sflag * 0.20 + sw * 0.10, 4)
            items.append({
                "item_type":       "SIGNAL",
                "id":              f"sig-{r['signal_id']}",
                "signal_id":       r["signal_id"],
                "feed_score":      score,
                "title":           r["title"]    or "(untitled)",
                "summary":         (r["content"] or "")[:200],
                "source":          r["source"]   or "",
                "stream":          r["stream"]   or "GLOBAL",
                "timestamp":       r["timestamp"],
                "is_priority":     r["is_priority"],
                "relevance_score": round(rel, 3),
                "sentinel_flag":   sflag,
                "stream_weight":   sw,
            })
    except Exception:
        pass

    # -- 3. CORRELATION items
    # Threshold: >= 0.85 (strong patterns only).
    # stream_weight: pinned to CRIME_INTEL (1.0) -- patterns always compete
    #   at the top of the feed regardless of constituent signal streams.
    # FIRMS exclusion (Phase 29.3): exclude any pair where either signal
    #   is source='firms'. Fire pixel-pairs are not investigative incidents.
    try:
        corr_params = {}
        corr_source_clause = ""
        if source_type_param is not None:
            corr_source_clause = "\n              AND  sa.source_type = :source_type\n              AND  sb.source_type = :source_type"
            corr_params['source_type'] = source_type_param

        corr_rows = db.execute("""
            SELECT ci.id,
                   ci.correlation_score,
                   ci.distance_km,
                   ci.time_difference_hours,
                   ci.detected_at,
                   sa.title          AS title_a,
                   sa.source         AS src_a,
                   sa.stream         AS stream_a,
                   sa.is_priority    AS prio_a,
                   COALESCE(sa.relevance_score, 1.0) AS rel_a,
                   sb.title          AS title_b,
                   sb.source         AS src_b,
                   sb.stream         AS stream_b,
                   sb.is_priority    AS prio_b,
                   COALESCE(sb.relevance_score, 1.0) AS rel_b
            FROM   correlated_incidents ci
            JOIN   signals sa ON sa.signal_id = ci.signal_a
            JOIN   signals sb ON sb.signal_id = ci.signal_b
            WHERE  ci.correlation_score >= 0.85
              AND  sa.source != 'firms'
              AND  sb.source != 'firms'""" + corr_source_clause + """
            ORDER  BY ci.correlation_score DESC
            LIMIT  100
        """, corr_params).fetchall()

        for r in corr_rows:
            rel   = (float(r["rel_a"]) + float(r["rel_b"])) / 2.0
            prio  = max(r["prio_a"] or 0, r["prio_b"] or 0)
            sw    = 1.0   # pinned: patterns are always CRIME_INTEL weight
            score = round(rel * 0.40 + prio * 0.30 + 0.0 * 0.20 + sw * 0.10, 4)
            items.append({
                "item_type":        "CORRELATION",
                "id":               f"corr-{r['id']}",
                "corr_db_id":       r["id"],
                "feed_score":       score,
                "title":            (
                    f"Pattern: {(r['title_a'] or '')[:50]} "
                    f"↔ {(r['title_b'] or '')[:50]}"
                ),
                "summary":          (
                    f"Correlation score {r['correlation_score']:.3f} · "
                    f"{r['distance_km']:.0f} km apart · "
                    f"{r['time_difference_hours']:.1f} h apart"
                ),
                "correlation_score": r["correlation_score"],
                "distance_km":      r["distance_km"],
                "time_diff_hours":  r["time_difference_hours"],
                "title_a":          r["title_a"] or "",
                "title_b":          r["title_b"] or "",
                "source":           r["src_a"]   or "",
                "stream":           r["stream_a"] or "GLOBAL",
                "timestamp":        r["detected_at"],
                "is_priority":      prio,
                "stream_weight":    sw,
            })
    except Exception:
        pass

    # -- 4. INTELLIGENCE_LEAD items
    # "Top 10%" = influence_score >= 90th-percentile value, computed once
    # via NTILE(10) window function (SQLite >= 3.25, ships with Win10+).
    # Fallback: threshold = 0.0 if table is empty or ntile unavailable.
    # p90_threshold is included in the item payload for UI transparency.
    try:
        p90_row = db.execute("""
            SELECT influence_score AS p90
            FROM (
                SELECT influence_score,
                       NTILE(10) OVER (ORDER BY influence_score ASC) AS decile
                FROM   actor_network_metrics
                WHERE  influence_score > 0
            )
            WHERE decile = 10
            ORDER BY influence_score ASC
            LIMIT  1
        """).fetchone()

        p90_threshold = float(p90_row["p90"]) if p90_row else 0.0

        lead_rows = db.execute("""
            SELECT m.actor_id, a.name, a.type,
                   m.influence_score, m.betweenness, m.pagerank,
                   m.community_id, m.computed_at
            FROM   actor_network_metrics m
            JOIN   actors a ON a.actor_id = m.actor_id
            WHERE  m.influence_score >= :threshold
            ORDER  BY m.influence_score DESC
            LIMIT  50
        """, {"threshold": p90_threshold}).fetchall()

        for r in lead_rows:
            inf = float(r["influence_score"] or 0)
            # Normalise to 0-1 for feed_score: inf / (inf + 1) is a
            # smooth sigmoid that never exceeds 1 regardless of raw value.
            rel_proxy = inf / (inf + 1.0) if inf > 0 else 0.0
            # stream_weight uses PRIORITY tier (0.9) -- actor leads are
            # strategic intelligence, not real-time operational signals.
            score = round(rel_proxy * 0.40 + 0.0 * 0.30 + 0.0 * 0.20 + 0.9 * 0.10, 4)
            items.append({
                "item_type":       "INTELLIGENCE_LEAD",
                "id":              f"lead-{r['actor_id']}",
                "actor_id":        r["actor_id"],
                "feed_score":      score,
                "title":           f"Actor: {r['name']}",
                "summary":         (
                    f"Type: {r['type']} · "
                    f"Influence {inf:.3f} · "
                    f"PageRank {(r['pagerank'] or 0):.4f}"
                ),
                "actor_name":      r["name"],
                "actor_type":      r["type"],
                "influence_score": inf,
                "p90_threshold":   p90_threshold,
                "community_id":    r["community_id"],
                "computed_at":     r["computed_at"],
                "source":          "GRAPH",
                "stream":          None,
                "timestamp":       r["computed_at"],
                "is_priority":     0,
            })
    except Exception:
        pass

    # -- CT-1: gravity scoring pass
    ct_context = None
    if ct_case_id:
        try:
            from core.gravity import build_context, score_item, blend_score
            ct_context = build_context(db, ct_case_id)
            for item in items:
                gs = score_item(item, ct_context)
                item["gravity_score"] = gs
                item["feed_score"]    = blend_score(
                    item["feed_score"], gs, gravity_weight
                )
        except Exception:
            ct_context = None  # degrade gracefully to Phase 29.3 behaviour

    # -- Sort: feed_score DESC, then timestamp DESC as tiebreaker
    def _sort_key(item):
        ts = item.get("timestamp") or "1970-01-01"
        return (item["feed_score"], ts)

    items.sort(key=_sort_key, reverse=True)

    # -- Paginate
    total = len(items)
    page  = items[offset : offset + limit]

    return jsonify({
        "total":      total,
        "offset":     offset,
        "limit":      limit,
        "has_more":   (offset + limit) < total,
        "items":      page,
        "ct_active":  ct_case_id is not None and ct_context is not None,
        "ct_case_id": ct_case_id,
        "gravity":    ct_gravity if ct_case_id else None,
    })


# ---------------------------------------------------------------------------
# Route: /media — static media file serving
# ---------------------------------------------------------------------------

@pages_bp.route("/media/<path:filepath>")
def serve_media(filepath):
    return send_from_directory(str(MEDIA_DIR), filepath)
