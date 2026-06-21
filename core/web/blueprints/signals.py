#!/usr/bin/env python3
from __future__ import annotations

"""
Blueprint: Signal Routes
========================
Extracted from app.py — /signals page, /api/pulse, /api/heatmap,
signal entity endpoints, dismiss, streams, decay, and anomaly routes.
"""

import re as _re

from flask import (
    Blueprint, render_template, request, jsonify,
)

from core.web.helpers import get_db, BASE_DIR, DB_PATH, MEDIA_DIR

signals_bp = Blueprint('signals', __name__)


# ---------------------------------------------------------------------------
# Smart promotion helpers
# ---------------------------------------------------------------------------

_TITLE_STRIP = _re.compile(
    r'^(?:'
    r'@\w[\w.]*\s*:\s*'       # @handle: tweet text
    r'|GDELT\s*[—–-]\s*'      # GDELT — headline
    r'|USGS\s*[—–-]\s*'       # USGS — M4.5 earthquake
    r'|RSS\s*[—–-]\s*'        # RSS — article title
    r'|FIRMS\s*[—–-]\s*'      # FIRMS — fire activity
    r'|ACLED\s*[—–-]\s*'      # ACLED — event
    r'|x_pulse\s*[—–-]\s*'   # x_pulse — tweet
    r')',
    _re.IGNORECASE,
)

# Ordered: first match wins. Keywords are lowercased for comparison.
_CATEGORY_RULES: list[tuple[list[str], str]] = [
    (["election", "vote", "voter", "ballot", "anc policy", "da policy",
      "eff policy", "party manifesto", "general election", "by-election",
      "iec", "electoral commission"], "Election"),
    (["parliament", "national assembly", "national council of provinces",
      "ncop", "legislature", "legislation", "bill passed",
      "portfolio committee", "standing committee"], "Legislative"),
    (["sandf", "south african national defence", "saaf", "sa navy",
      "military", "defence force", "army", "air force"], "Military"),
    (["protest", "strike", "shutdown", "riot", "looting", "civil unrest",
      "demonstration", "march", "picket", "stay-away", "stayaway",
      "community uprising"], "Civil Unrest"),
    (["murder", "killed", "crime", "robbery", "hijack", "hijacking",
      "arrested", "arrested for", "drug", "gang", "gang-related",
      "shooting", "attacked", "cash-in-transit", "cit heist", "police operation",
      "corruption", "bribery", "fraud"], "Security"),
    (["rand", "rands", "economy", "inflation", "budget", "reserve bank",
      "sarb", "national treasury", "load shedding", "load-shedding",
      "eskom", "stage 4", "stage 6", "tax", "gdp", "economic growth",
      "unemployment", "investment", "forex", "jse", "interest rate",
      "repo rate", "cpi"], "Economic"),
    (["diplomatic", "diplomat", "embassy", "foreign minister", "bilateral",
      "summit", "un security council", "brics", "au summit",
      "geopolitical", "sanctions", "trade deal"], "Diplomatic"),
    (["social grant", "sassa", "welfare", "poverty", "healthcare",
      "education", "community", "housing", "rncs", "social development"], "Social"),
]


def _infer_category(title: str, content: str, stream: str, source: str) -> str:
    """Return the best-fit event category from signal metadata."""
    text = (title + " " + (content or "")).lower()
    for keywords, category in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    # Stream fallback
    if stream == "CRIME_INTEL":
        return "Security"
    return "Other"


def _clean_title(raw: str) -> str:
    """Strip collector prefixes and normalise the event title."""
    cleaned = _TITLE_STRIP.sub("", (raw or "").strip())
    # If stripping consumed everything, fall back to original
    if not cleaned:
        cleaned = raw or ""
    # Sentence-case if the title is ALL CAPS
    if cleaned == cleaned.upper() and len(cleaned) > 6:
        cleaned = cleaned.capitalize()
    return cleaned[:200]


def _build_summary(signal: dict) -> str:
    """
    Build an enriched summary combining signal content, source context,
    and SOCINT metadata (hashtags, cashtags) where available.
    """
    import json as _json
    lines: list[str] = []

    content = (signal.get("content") or "").strip()
    if content:
        lines.append(content)

    # Parse metadata_json for extra context
    try:
        meta = _json.loads(signal.get("metadata_json") or "{}")
    except Exception:
        meta = {}

    # x_pulse: show handle + tags
    x_handle = meta.get("x_handle", "")
    hashtags  = meta.get("hashtags",  [])
    cashtags  = meta.get("cashtags",  [])

    if x_handle:
        tag_str = " ".join(f"#{h}" for h in hashtags[:8])
        cash_str = " ".join(cashtags[:4])
        context_parts = [f"via {x_handle}"]
        if tag_str:
            context_parts.append(tag_str)
        if cash_str:
            context_parts.append(cash_str)
        lines.append("[" + " · ".join(context_parts) + "]")

    # Source + stream badge for non-FLUX signals
    source = signal.get("source", "")
    stream = signal.get("stream", "")
    score  = signal.get("relevance_score")
    if source and not x_handle:
        meta_parts = [f"Source: {source.upper()}"]
        if stream:
            meta_parts.append(f"Stream: {stream}")
        if score is not None:
            meta_parts.append(f"Relevance: {round(float(score), 2)}")
        lines.append("[" + " · ".join(meta_parts) + "]")

    return "\n".join(lines)[:1000]


# ===========================================================================
# Routes
# ===========================================================================


# -----------------------------------------------------------------------
# /signals — Signal Monitor
# -----------------------------------------------------------------------

@signals_bp.route("/signals")
def signals():
    """
    FORAGE signal monitor -- lists the 50 most recent ingested signals.
    Phase 14: includes cluster_id, is_priority columns.
    Phase 15.5: source_counts for triage badges.
    Phase 16: pinned_case_count per signal + active_cases for Pin-to-Case.
    """
    db = get_db()

    try:
        rows = db.execute("""
            SELECT s.signal_id,
                   s.source,
                   s.external_id,
                   s.title,
                   s.content,
                   s.lat,
                   s.lng,
                   s.timestamp,
                   s.status,
                   s.metadata_json,
                   s.cluster_id,
                   s.is_priority,
                   s.source_artifact_id,
                   a.title      AS artifact_title,
                   COUNT(cs.case_id) AS pinned_case_count
            FROM   signals s
            LEFT   JOIN case_signals cs ON cs.signal_id = s.signal_id
            LEFT   JOIN artifacts a ON a.artifact_id = s.source_artifact_id
            GROUP  BY s.signal_id
            ORDER  BY s.is_priority DESC, s.timestamp DESC
            LIMIT  50
        """).fetchall()
    except Exception:
        # case_signals / source_artifact_id may not exist pre-migration
        rows = db.execute("""
            SELECT signal_id, source, external_id, title, content,
                   lat, lng, timestamp, status, metadata_json,
                   cluster_id, is_priority,
                   NULL AS source_artifact_id,
                   NULL AS artifact_title,
                   0    AS pinned_case_count
            FROM   signals
            ORDER  BY is_priority DESC, timestamp DESC
            LIMIT  50
        """).fetchall()

    # Summary counts for the header bar
    counts = db.execute("""
        SELECT
            COUNT(*)                                              AS total,
            SUM(CASE WHEN status = 'raw'      THEN 1 ELSE 0 END) AS raw,
            SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed,
            SUM(CASE WHEN status = 'promoted' THEN 1 ELSE 0 END) AS promoted,
            SUM(CASE WHEN status = 'dismissed'THEN 1 ELSE 0 END) AS dismissed,
            SUM(CASE WHEN is_priority = 1     THEN 1 ELSE 0 END) AS priority,
            COUNT(DISTINCT CASE WHEN cluster_id IS NOT NULL
                                THEN cluster_id END)              AS clusters
        FROM signals
    """).fetchone()

    # Phase 15.5 -- per-source counts for triage badges
    source_counts_raw = db.execute("""
        SELECT source, COUNT(*) AS cnt
        FROM   signals
        WHERE  source IS NOT NULL
        GROUP  BY source
        ORDER  BY cnt DESC
    """).fetchall()
    source_counts = {r["source"]: r["cnt"] for r in source_counts_raw}

    # Phase 16 -- active cases for the "Pin to Case" dropdown
    active_cases = db.execute("""
        SELECT case_id, name, status
        FROM   cases
        WHERE  status = 'active'
        ORDER  BY created_at DESC
    """).fetchall()

    # Phase 27 -- stream filter + stream counts for pills
    active_stream = request.args.get("stream", "").strip().upper()
    try:
        stream_counts_raw = db.execute(
            "SELECT stream, COUNT(*) AS cnt FROM signals "
            "WHERE stream IS NOT NULL GROUP BY stream ORDER BY cnt DESC"
        ).fetchall()
        stream_counts = {r["stream"]: r["cnt"] for r in stream_counts_raw}
    except Exception:
        stream_counts = {}
        active_stream = ""

    if active_stream and active_stream != "ALL":
        try:
            filtered = db.execute(
                "SELECT s.signal_id, s.source, s.external_id, s.title, "
                "s.content, s.lat, s.lng, s.timestamp, s.status, "
                "s.metadata_json, s.cluster_id, s.is_priority, "
                "s.source_artifact_id, s.stream, "
                "a.title AS artifact_title, "
                "COUNT(cs.case_id) AS pinned_case_count "
                "FROM signals s "
                "LEFT JOIN case_signals cs ON cs.signal_id = s.signal_id "
                "LEFT JOIN artifacts a "
                "  ON a.artifact_id = s.source_artifact_id "
                "WHERE s.stream = ? "
                "GROUP BY s.signal_id "
                "ORDER BY s.is_priority DESC, s.timestamp DESC LIMIT 50",
                (active_stream,)
            ).fetchall()
            rows = filtered
        except Exception:
            pass

    return render_template(
        "signals.html",
        signals=rows,
        counts=counts,
        source_counts=source_counts,
        stream_counts=stream_counts,
        active_stream=active_stream or "ALL",
        active_cases=[dict(r) for r in active_cases],
    )


# -----------------------------------------------------------------------
# /api/pulse -- signal frequency for Chart.js pulse graph
# -----------------------------------------------------------------------

@signals_bp.route("/api/pulse")
def api_pulse():
    db     = get_db()
    window = min(request.args.get("hours", 48, type=int), 168)
    source = request.args.get("source", "").strip()
    source_clause = "AND source = :source" if source else ""
    try:
        rows = db.execute(f"""
            WITH RECURSIVE hours(n) AS (
                SELECT 0 UNION ALL SELECT n+1 FROM hours WHERE n < :window - 1
            ),
            buckets AS (
                SELECT strftime('%Y-%m-%dT%H:00',
                    datetime('now', '-' || (:window-1-n) || ' hours')) AS bucket
                FROM hours
            ),
            counts AS (
                SELECT strftime('%Y-%m-%dT%H:00', timestamp) AS bucket,
                       COUNT(*)         AS total,
                       SUM(is_priority) AS priority
                FROM  signals
                WHERE timestamp >= datetime('now', '-' || :window || ' hours')
                {source_clause}
                GROUP BY bucket
            )
            SELECT b.bucket,
                   COALESCE(c.total,    0) AS total,
                   COALESCE(c.priority, 0) AS priority
            FROM   buckets b
            LEFT   JOIN counts c ON c.bucket = b.bucket
            ORDER  BY b.bucket ASC
        """, {"window": window, "source": source or None}).fetchall()
        return jsonify({"hours": window, "source": source or None,
                        "buckets": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# -----------------------------------------------------------------------
# /api/heatmap -- [lat, lng, intensity] for Leaflet.heat
# -----------------------------------------------------------------------

@signals_bp.route("/api/heatmap")
def api_heatmap():
    """
    [lat, lng, intensity] triples for Leaflet.heat.
    Phase 20: coords pruned to 4dp, hard cap 5000 points.
    """
    import json as _json
    db     = get_db()
    source = request.args.get("source", "").strip()
    hours  = request.args.get("hours",  type=int)
    status = request.args.get("status", "raw,promoted")
    allowed = [s.strip() for s in status.split(",")]

    clauses = [
        "lat IS NOT NULL", "lng IS NOT NULL",
        f"status IN ({','.join('?' for _ in allowed)})",
    ]
    params = list(allowed)
    if source:
        clauses.append("source = ?");                      params.append(source)
    if hours:
        clauses.append("timestamp >= datetime('now', ?)"); params.append(f"-{hours} hours")

    try:
        rows = db.execute(
            "SELECT ROUND(lat,4) AS lat, ROUND(lng,4) AS lng, "
            "source, is_priority, metadata_json "
            f"FROM signals WHERE {' AND '.join(clauses)} "
            "ORDER BY is_priority DESC, timestamp DESC LIMIT 5000",
            params,
        ).fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    points = []
    for r in rows:
        src  = (r["source"] or "").lower()
        prio = r["is_priority"] or 0
        meta = {}
        if r["metadata_json"]:
            try:    meta = _json.loads(r["metadata_json"])
            except: pass
        if src == "usgs":
            mag = meta.get("mag")
            intensity = min(float(mag) / 9.0, 1.0) if mag is not None else 0.5
        elif src == "firms":
            frp = meta.get("frp")
            intensity = min(float(frp) / 500.0, 1.0) if frp is not None else 0.4
        else:
            intensity = 0.5
        if prio:
            intensity = max(intensity, 0.7)
        points.append([r["lat"], r["lng"], round(intensity, 3)])

    return jsonify({"points": points, "count": len(points), "source": source or None})


# -----------------------------------------------------------------------
# /api/signals/<id>/entities  &  /api/entities/top
# -----------------------------------------------------------------------

@signals_bp.route("/api/signals/<signal_id>/entities")
def api_signal_entities(signal_id: str):
    db  = get_db()
    sig = db.execute("SELECT signal_id, title FROM signals WHERE signal_id=?",
                     (signal_id,)).fetchone()
    if not sig:
        return jsonify({"error": "Signal not found"}), 404
    try:
        rows = db.execute(
            "SELECT text, label, count FROM signal_entities "
            "WHERE signal_id=? ORDER BY label, count DESC, text",
            (signal_id,),
        ).fetchall()
    except Exception:
        rows = []
    grouped = {"PERSON": [], "ORG": [], "GPE": []}
    for r in rows:
        if r["label"] in grouped:
            grouped[r["label"]].append({"text": r["text"], "count": r["count"]})
    return jsonify({"signal_id": signal_id, "title": sig["title"],
                    "entities": grouped, "total": len(rows)})


@signals_bp.route("/api/entities/top")
def api_entities_top():
    db     = get_db()
    label  = request.args.get("label", "").strip().upper()
    limit  = min(request.args.get("limit", 20, type=int), 100)
    source = request.args.get("source", "").strip()
    lc     = "AND se.label = ?"  if label  else ""
    sc     = "AND s.source = ?"  if source else ""
    params = ([label] if label else []) + ([source] if source else []) + [limit]
    try:
        rows = db.execute(
            f"SELECT se.text, se.label, SUM(se.count) AS total_count, "
            f"COUNT(DISTINCT se.signal_id) AS signal_count "
            f"FROM signal_entities se JOIN signals s ON s.signal_id=se.signal_id "
            f"WHERE 1=1 {lc} {sc} GROUP BY se.text, se.label "
            f"ORDER BY total_count DESC LIMIT ?", params,
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"label": label or None, "source": source or None,
                    "entities": [dict(r) for r in rows], "total": len(rows)})


# -----------------------------------------------------------------------
# Signal dismiss
# -----------------------------------------------------------------------

@signals_bp.route("/api/signals/<signal_id>/dismiss", methods=["POST"])
def api_signal_dismiss(signal_id: str):
    """Mark a signal as dismissed (no further action needed)."""
    import json as _json
    from flask import Response
    db = get_db()
    row = db.execute(
        "SELECT signal_id FROM signals WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if not row:
        return Response(
            _json.dumps({"error": "Signal not found"}),
            status=404, mimetype="application/json"
        )
    db.execute(
        "UPDATE signals SET status = 'dismissed' WHERE signal_id = ?",
        (signal_id,),
    )
    db.commit()
    return Response(
        _json.dumps({"ok": True, "signal_id": signal_id, "status": "dismissed"}),
        mimetype="application/json",
    )


# -----------------------------------------------------------------------
# Signal streams
# -----------------------------------------------------------------------

@signals_bp.route("/api/signals/streams")
def api_signals_streams():
    """Phase 27: stream counts summary used by dashboard and feed."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT stream, COUNT(*) AS total, "
            "SUM(CASE WHEN status='raw' THEN 1 ELSE 0 END) AS raw, "
            "SUM(CASE WHEN is_priority=1 THEN 1 ELSE 0 END) AS priority, "
            "MAX(timestamp) AS latest "
            "FROM signals WHERE stream IS NOT NULL AND source_type = 'live' "
            "GROUP BY stream ORDER BY total DESC"
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"streams": [dict(r) for r in rows]})


# -----------------------------------------------------------------------
# Decay engine trigger
# -----------------------------------------------------------------------

@signals_bp.route("/api/decay/run", methods=["POST"])
def api_decay_run():
    """Phase 28: Trigger a decay pass on all signals. Option B: on-demand."""
    try:
        from forage.engines.decay_engine import DecayEngine
        result = DecayEngine(db_path=DB_PATH).run()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# -----------------------------------------------------------------------
# Anomaly engine trigger + baselines
# -----------------------------------------------------------------------

@signals_bp.route("/api/anomaly/run", methods=["POST"])
def api_anomaly_run():
    """Trigger a full anomaly detection pass (Option B: on-demand)."""
    try:
        from forage.engines.anomaly_engine import AnomalyEngine
        result = AnomalyEngine(db_path=DB_PATH).run()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@signals_bp.route("/api/anomaly/baselines")
def api_anomaly_baselines():
    """Return baseline coverage stats -- useful for the admin panel."""
    db = get_db()
    try:
        meta = db.execute(
            "SELECT COUNT(*) AS rows, "
            "COUNT(DISTINCT source) AS sources, "
            "COUNT(DISTINCT region_key) AS regions, "
            "MIN(bucket_date) AS earliest, "
            "MAX(bucket_date) AS latest "
            "FROM signal_baselines"
        ).fetchone()
        breakdown = db.execute(
            "SELECT source, COUNT(*) AS rows, "
            "COUNT(DISTINCT region_key) AS regions "
            "FROM signal_baselines "
            "GROUP BY source ORDER BY rows DESC"
        ).fetchall()
    except Exception:
        return jsonify({"rows": 0, "sources": 0, "regions": 0})
    return jsonify({
        "rows":     meta["rows"],
        "sources":  meta["sources"],
        "regions":  meta["regions"],
        "earliest": meta["earliest"],
        "latest":   meta["latest"],
        "breakdown": [dict(r) for r in breakdown],
    })
