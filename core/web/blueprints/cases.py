#!/usr/bin/env python3
from __future__ import annotations

"""
Blueprint: Case Management Routes
==================================
Extracted from app.py — all /cases, /workbench, and case-related API routes.
"""

import datetime
import threading

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)

from core.web.helpers import get_db, BASE_DIR, DB_PATH, MEDIA_DIR
from core.web.helpers import create_job, update_job, finalize_job

cases_bp = Blueprint('cases', __name__)


# ---------------------------------------------------------------------------
# Helper: _compute_heat_score
# ---------------------------------------------------------------------------

def _compute_heat_score(db, case_id: int) -> dict:
    """
    Calculates three sub-scores and a composite Heat Score for a case.

    DENSITY  = artifact_count / max(actor_count, 1)
               Capped at 5.0, normalised to 0-100.
               Rationale: high artifact-per-actor ratio = rich evidentiary
               case; denominator floor prevents div-by-zero on empty cases.

    VOLATILITY = events within a 7-day sliding window / total events
                 We find the densest 7-day window by sorting event dates
                 and using a two-pointer over the sorted list.
                 Normalised to 0-100.  A score of 100 means ALL events
                 cluster within one week -- maximum temporal concentration.

    CONNECTIVITY = cross-linked ratio.
                   "Cross-linked" = an entity that appears in MORE THAN ONE
                   entity-type bucket (e.g. an event that both has pinned
                   actors AND pinned artifacts on this case).
                   Formula: cross_linked / max(total_pins, 1) x 100.

    COMPOSITE = 0.35 * density + 0.35 * volatility + 0.30 * connectivity
    These weights reflect the investigative priority order: evidence density
    and temporal clustering are equally primary signals; structural
    connectivity is a secondary quality check.
    """

    # -- Counts ---------------------------------------------------------------
    actor_count    = db.execute(
        "SELECT COUNT(*) FROM case_actors    WHERE case_id=?", (case_id,)
    ).fetchone()[0]
    event_count    = db.execute(
        "SELECT COUNT(*) FROM case_events    WHERE case_id=?", (case_id,)
    ).fetchone()[0]
    artifact_count = db.execute(
        "SELECT COUNT(*) FROM case_artifacts WHERE case_id=?", (case_id,)
    ).fetchone()[0]
    total_pins = actor_count + event_count + artifact_count

    # -- DENSITY --------------------------------------------------------------
    raw_density   = artifact_count / max(actor_count, 1)
    density_norm  = min(raw_density / 5.0, 1.0) * 100          # cap at 5:1

    # -- VOLATILITY (densest 7-day window) ------------------------------------
    volatility_norm = 0.0
    if event_count > 1:
        dated_events = db.execute("""
            SELECT e.date
            FROM   case_events ce
            JOIN   events e ON e.event_id = ce.event_id
            WHERE  ce.case_id = ? AND e.date IS NOT NULL
            ORDER  BY e.date ASC
        """, (case_id,)).fetchall()

        if len(dated_events) >= 2:
            dates = []
            for row in dated_events:
                try:
                    dates.append(datetime.date.fromisoformat(row["date"]))
                except (ValueError, TypeError):
                    pass

            if len(dates) >= 2:
                # Two-pointer: find maximum events within any 7-day window
                max_in_window = 1
                left = 0
                for right in range(len(dates)):
                    while (dates[right] - dates[left]).days > 7:
                        left += 1
                    max_in_window = max(max_in_window, right - left + 1)
                volatility_norm = (max_in_window / len(dates)) * 100

    # -- CONNECTIVITY (cross-linked ratio) ------------------------------------
    connectivity_norm = 0.0
    if total_pins > 0:
        # Events that have AT LEAST ONE pinned artifact AND one pinned actor
        cross_events = db.execute("""
            SELECT COUNT(DISTINCT ce.event_id)
            FROM   case_events ce
            JOIN   artifacts a   ON a.event_id = ce.event_id
            JOIN   case_artifacts ca ON ca.artifact_id = a.artifact_id
                                    AND ca.case_id = ce.case_id
            WHERE  ce.case_id = ?
        """, (case_id,)).fetchone()[0]

        # Actors that share at least one event with another pinned actor
        cross_actors = db.execute("""
            SELECT COUNT(DISTINCT cac1.actor_id)
            FROM   case_actors cac1
            JOIN   actor_events ae1 ON ae1.actor_id = cac1.actor_id
            JOIN   actor_events ae2 ON ae2.event_id = ae1.event_id
                                   AND ae2.actor_id != ae1.actor_id
            JOIN   case_actors cac2 ON cac2.actor_id = ae2.actor_id
                                   AND cac2.case_id = cac1.case_id
            WHERE  cac1.case_id = ?
        """, (case_id,)).fetchone()[0]

        cross_total    = cross_events + cross_actors
        connectivity_norm = min(cross_total / max(total_pins, 1), 1.0) * 100

    # -- Composite ------------------------------------------------------------
    composite = (
        0.35 * density_norm +
        0.35 * volatility_norm +
        0.30 * connectivity_norm
    )

    return {
        "composite":       round(composite, 1),
        "density":         round(density_norm, 1),
        "volatility":      round(volatility_norm, 1),
        "connectivity":    round(connectivity_norm, 1),
        "raw": {
            "actors":    actor_count,
            "events":    event_count,
            "artifacts": artifact_count,
            "total_pins": total_pins,
        },
    }


# ---------------------------------------------------------------------------
# Helper: _compute_suggestions
# ---------------------------------------------------------------------------

def _compute_suggestions(db, case_id: int) -> list:
    """
    Commonality Algorithm -- produces ranked "Suggested Leads":

    STEP 1 -- Hidden Events:
      For every actor pinned to the case, fetch all events that actor
      participates in. Exclude events already pinned to the case.
      Tally how many pinned actors link to each hidden event (co-occurrence
      count).

    STEP 2 -- Hidden Actors:
      For every event pinned to the case, fetch all actors in those events.
      Exclude actors already pinned. Tally co-occurrence across events.

    STEP 3 -- Artifact Trails:
      Fetch artifacts whose event_id matches a pinned event but which are
      not yet pinned. These are "dangling evidence" -- high confidence
      because the event is already known.

    RANKING:
      confidence_score = co_occurrence / max_possible_co_occurrence
      confidence label: >=0.67 -> HIGH, >=0.34 -> MEDIUM, else LOW
      Ties broken by artifact_count (more evidence = higher priority).

    Returns top 12 suggestions sorted by confidence desc.
    """
    # -- Fetch current case contents ------------------------------------------
    pinned_actor_ids = {
        r["actor_id"] for r in db.execute(
            "SELECT actor_id FROM case_actors WHERE case_id=?", (case_id,)
        ).fetchall()
    }
    pinned_event_ids = {
        r["event_id"] for r in db.execute(
            "SELECT event_id FROM case_events WHERE case_id=?", (case_id,)
        ).fetchall()
    }
    pinned_artifact_ids = {
        r["artifact_id"] for r in db.execute(
            "SELECT artifact_id FROM case_artifacts WHERE case_id=?", (case_id,)
        ).fetchall()
    }

    suggestions = []
    seen_ids: dict[str, bool] = {}   # dedup key: "event-3", "actor-5", etc.

    # -- STEP 1: Hidden Events via pinned actors ------------------------------
    if pinned_actor_ids:
        ph = ",".join("?" * len(pinned_actor_ids))
        hidden_events = db.execute(f"""
            SELECT e.event_id, e.title, e.date, e.category,
                   e.location, e.summary,
                   COUNT(DISTINCT ae.actor_id) AS co_occurrence,
                   COUNT(DISTINCT a.artifact_id) AS artifact_count
            FROM   actor_events ae
            JOIN   events e ON e.event_id = ae.event_id
            LEFT   JOIN artifacts a ON a.event_id = e.event_id
            WHERE  ae.actor_id IN ({ph})
              AND  e.event_id  NOT IN ({','.join('?' * len(pinned_event_ids)) or 'NULL'})
            GROUP  BY e.event_id
            ORDER  BY co_occurrence DESC, artifact_count DESC
            LIMIT  20
        """, (
            *list(pinned_actor_ids),
            *(list(pinned_event_ids) if pinned_event_ids else []),
        )).fetchall()

        max_co = pinned_actor_ids and max(
            (r["co_occurrence"] for r in hidden_events), default=1
        ) or 1

        for r in hidden_events:
            key = f"event-{r['event_id']}"
            if key in seen_ids:
                continue
            seen_ids[key] = True
            conf_score = r["co_occurrence"] / max_co
            # Find which pinned actors link to this event
            linked_actors = db.execute(f"""
                SELECT ac.name FROM actor_events ae
                JOIN actors ac ON ac.actor_id = ae.actor_id
                WHERE ae.event_id = ? AND ae.actor_id IN ({ph})
                LIMIT 4
            """, (r["event_id"], *list(pinned_actor_ids))).fetchall()

            suggestions.append({
                "type":          "event",
                "entity_id":     r["event_id"],
                "title":         r["title"],
                "date":          r["date"] or "",
                "category":      r["category"] or "Other",
                "location":      r["location"] or "",
                "summary":       (r["summary"] or "")[:160],
                "artifact_count": r["artifact_count"],
                "co_occurrence": r["co_occurrence"],
                "confidence":    round(conf_score * 100),
                "reason":        f"{r['co_occurrence']} pinned actor{'s' if r['co_occurrence'] != 1 else ''} linked: "
                                 + ", ".join(a["name"] for a in linked_actors),
                "url":           f"/event/{r['event_id']}",
            })

    # -- STEP 2: Hidden Actors via pinned events ------------------------------
    if pinned_event_ids:
        ph = ",".join("?" * len(pinned_event_ids))
        hidden_actors = db.execute(f"""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   COUNT(DISTINCT ae.event_id) AS co_occurrence
            FROM   actor_events ae
            JOIN   actors ac ON ac.actor_id = ae.actor_id
            WHERE  ae.event_id  IN ({ph})
              AND  ac.actor_id  NOT IN ({','.join('?' * len(pinned_actor_ids)) or 'NULL'})
            GROUP  BY ac.actor_id
            ORDER  BY co_occurrence DESC
            LIMIT  15
        """, (
            *list(pinned_event_ids),
            *(list(pinned_actor_ids) if pinned_actor_ids else []),
        )).fetchall()

        max_co = len(pinned_event_ids) or 1

        for r in hidden_actors:
            key = f"actor-{r['actor_id']}"
            if key in seen_ids:
                continue
            seen_ids[key] = True
            conf_score = r["co_occurrence"] / max_co

            linked_events = db.execute(f"""
                SELECT e.title FROM actor_events ae
                JOIN events e ON e.event_id = ae.event_id
                WHERE ae.actor_id = ? AND ae.event_id IN ({ph})
                LIMIT 3
            """, (r["actor_id"], *list(pinned_event_ids))).fetchall()

            suggestions.append({
                "type":          "actor",
                "entity_id":     r["actor_id"],
                "title":         r["name"],
                "subtype":       r["type"],
                "description":   (r["description"] or "")[:120],
                "co_occurrence": r["co_occurrence"],
                "confidence":    round((conf_score) * 100),
                "reason":        f"Active in {r['co_occurrence']} pinned event{'s' if r['co_occurrence'] != 1 else ''}: "
                                 + ", ".join(e["title"][:40] for e in linked_events),
                "url":           f"/actor/{r['actor_id']}",
            })

    # -- STEP 3: Dangling artifact trails -------------------------------------
    if pinned_event_ids:
        ph = ",".join("?" * len(pinned_event_ids))
        dangling_artifacts = db.execute(f"""
            SELECT a.artifact_id, a.title, a.type, a.source, a.date,
                   e.title AS event_title, e.event_id
            FROM   artifacts a
            JOIN   events e ON e.event_id = a.event_id
            WHERE  a.event_id IN ({ph})
              AND  a.artifact_id NOT IN (
                   SELECT artifact_id FROM case_artifacts WHERE case_id=?
              )
            ORDER  BY a.date DESC
            LIMIT  10
        """, (*list(pinned_event_ids), case_id)).fetchall()

        for r in dangling_artifacts:
            key = f"artifact-{r['artifact_id']}"
            if key in seen_ids:
                continue
            seen_ids[key] = True
            suggestions.append({
                "type":        "artifact",
                "entity_id":   r["artifact_id"],
                "title":       r["title"],
                "subtype":     r["type"],
                "source":      r["source"] or "unverified",
                "date":        r["date"] or "",
                "co_occurrence": 1,
                "confidence":  85,  # artifact on pinned event = high confidence
                "reason":      f"Evidence on pinned event: {r['event_title'][:50]}",
                "url":         f"/artifact/{r['artifact_id']}",
            })

    # -- Sort and label -------------------------------------------------------
    suggestions.sort(key=lambda s: (-s["confidence"], -s.get("co_occurrence", 0)))

    for s in suggestions:
        c = s["confidence"]
        if c >= 67:
            s["confidence_label"] = "HIGH"
        elif c >= 34:
            s["confidence_label"] = "MEDIUM"
        else:
            s["confidence_label"] = "LOW"

    return suggestions[:12]


# ===========================================================================
# Routes
# ===========================================================================


# -----------------------------------------------------------------------
# /cases — Phase 8: Case Workspaces
# -----------------------------------------------------------------------

@cases_bp.route("/cases")
def cases():
    db = get_db()

    lens = request.args.get('lens', 'all').lower()
    if lens not in ('live', 'seed', 'all'):
        lens = 'all'

    case_where = '' if lens == 'all' else 'WHERE c.source_type = ?'
    params = [] if lens == 'all' else [lens]

    cases_list = db.execute(f"""
        SELECT c.case_id, c.name, c.description, c.status, c.created_at,
               COUNT(DISTINCT ca.artifact_id) AS artifact_count,
               COUNT(DISTINCT ce.event_id)    AS event_count,
               COUNT(DISTINCT cac.actor_id)   AS actor_count
        FROM   cases c
        LEFT JOIN case_artifacts ca  ON ca.case_id = c.case_id
        LEFT JOIN case_events    ce  ON ce.case_id = c.case_id
        LEFT JOIN case_actors    cac ON cac.case_id = c.case_id
        {case_where}
        GROUP BY c.case_id
        ORDER BY c.created_at DESC
    """, params).fetchall()
    return render_template("cases.html", cases=cases_list, lens=lens)


@cases_bp.route("/cases/new", methods=["POST"])
def case_new():
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    hypothesis  = request.form.get("hypothesis", "").strip() or None
    case_type   = request.form.get("case_type", "general")
    status      = request.form.get("status", "active")
    if not title:
        flash("Case title is required.", "error")
        return redirect(url_for("cases.cases"))
    db = get_db()
    cur = db.execute(
        "INSERT INTO cases (name, description, hypothesis, case_type, status, source_type) VALUES (?, ?, ?, ?, ?, 'live')",
        (title, description, hypothesis, case_type, status),
    )
    new_case_id = cur.lastrowid
    db.commit()
    # Warm Start: auto-seed context_anchors from GAZETTEER scan of case text
    try:
        from core.gravity import extract_location_anchors
        import json as _json
        seed_text = " ".join(filter(None, [title, description, hypothesis]))
        anchors = extract_location_anchors(seed_text)
        if anchors:
            db.execute(
                "UPDATE cases SET context_anchors = ? WHERE case_id = ?",
                (_json.dumps(anchors), new_case_id),
            )
            db.commit()
    except Exception:
        pass
    flash(f"Case '{title}' created.", "success")
    return redirect(url_for("cases.case_detail", case_id=new_case_id))


@cases_bp.route("/cases/<int:case_id>")
def case_detail(case_id: int):
    db = get_db()
    case = db.execute(
        "SELECT case_id, name, description, status, created_at, "
        "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
        "FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    if not case:
        flash("Case not found.", "error")
        return redirect(url_for("cases.cases"))

    artifacts = db.execute("""
        SELECT a.artifact_id, a.title, a.type, a.source, a.date,
               a.description, a.thumbnail,
               e.title AS event_title, e.event_id,
               ca.pinned_at, ca.note, ca.sequence_order, ca.transition_note
        FROM   case_artifacts ca
        JOIN   artifacts a ON a.artifact_id = ca.artifact_id
        LEFT JOIN events e ON e.event_id = a.event_id
        WHERE  ca.case_id = ?
        ORDER  BY ca.pinned_at DESC
    """, (case_id,)).fetchall()

    events = db.execute("""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               COUNT(a.artifact_id) AS artifact_count,
               ce.pinned_at, ce.note, ce.sequence_order, ce.transition_note
        FROM   case_events ce
        JOIN   events e ON e.event_id = ce.event_id
        LEFT JOIN artifacts a ON a.event_id = e.event_id
        WHERE  ce.case_id = ?
        GROUP  BY e.event_id
        ORDER  BY ce.pinned_at DESC
    """, (case_id,)).fetchall()

    actors = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type, ac.description,
               COUNT(DISTINCT ae.event_id) AS event_count,
               cac.pinned_at, cac.note, cac.sequence_order, cac.transition_note
        FROM   case_actors cac
        JOIN   actors ac ON ac.actor_id = cac.actor_id
        LEFT JOIN actor_events ae ON ae.actor_id = ac.actor_id
        WHERE  cac.case_id = ?
        GROUP  BY ac.actor_id
        ORDER  BY cac.pinned_at DESC
    """, (case_id,)).fetchall()

    all_cases = db.execute(
        "SELECT case_id, name, status FROM cases ORDER BY created_at DESC"
    ).fetchall()

    # Phase 16: FORAGE signals pinned to this case, chronological.
    # Guard: case_signals may not exist if --init-db hasn't run yet.
    try:
        pinned_signals = db.execute("""
            SELECT s.signal_id,
                   s.source,
                   s.title,
                   s.content,
                   s.lat,
                   s.lng,
                   s.timestamp,
                   s.status,
                   s.is_priority,
                   s.cluster_id,
                   cs.note      AS pin_note,
                   cs.pinned_at
            FROM   case_signals cs
            JOIN   signals s ON s.signal_id = cs.signal_id
            WHERE  cs.case_id = ?
            ORDER  BY s.timestamp ASC
        """, (case_id,)).fetchall()
    except Exception:
        pinned_signals = []

    return render_template(
        "case_detail.html",
        case=case,
        artifacts=artifacts,
        events=events,
        actors=actors,
        pinned_signals=pinned_signals,
        all_cases=all_cases,
    )


# -----------------------------------------------------------------------
# Path B: Case Workbench -- /workbench/<case_id>
# Dedicated view: Evidence Timeline + Actor Roster + Overlap Panel + PDF.js
# -----------------------------------------------------------------------

@cases_bp.route("/workbench/<int:case_id>")
def case_workbench(case_id: int):
    db   = get_db()
    case = db.execute(
        "SELECT case_id, name, description, status, created_at, "
        "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
        "FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        flash("Case not found.", "error")
        return redirect(url_for("cases.cases"))

    # -- Roster: actors + influence scores + risk ----------------------------
    roster = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type, ac.description,
               COALESCE(m.influence_score, 0)  AS influence_score,
               COALESCE(m.community_id, NULL)  AS community_id,
               COALESCE(m.pagerank, 0)          AS pagerank,
               cac.note, cac.pinned_at,
               (SELECT COUNT(*) FROM entity_relationships er
                WHERE (er.subject_actor_id = ac.actor_id
                    OR er.object_actor_id  = ac.actor_id)
                  AND er.relation_type != 'co_occurrence') AS rel_count
        FROM   case_actors cac
        JOIN   actors ac ON ac.actor_id = cac.actor_id
        LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
        WHERE  cac.case_id = ?
        ORDER  BY COALESCE(m.influence_score, 0) DESC, ac.name
    """, (case_id,)).fetchall()

    # -- Timeline: signals + artifacts merged chronologically ----------------
    raw_signals = db.execute("""
        SELECT 'signal' AS kind,
               s.signal_id    AS item_id,
               s.title,
               s.content      AS body,
               s.timestamp    AS effective_ts,
               s.source,
               s.stream,
               COALESCE(s.relevance_score, 0) AS relevance_score,
               s.is_priority,
               NULL           AS file_path,
               NULL           AS artifact_type,
               cs.note        AS pin_note
        FROM   case_signals cs
        JOIN   signals s ON s.signal_id = cs.signal_id
        WHERE  cs.case_id = ?
    """, (case_id,)).fetchall()

    raw_artifacts = db.execute("""
        SELECT 'artifact'     AS kind,
               CAST(a.artifact_id AS TEXT) AS item_id,
               a.title,
               a.description  AS body,
               COALESCE(a.date, ca.pinned_at) AS effective_ts,
               a.source,
               NULL           AS stream,
               0.0            AS relevance_score,
               0              AS is_priority,
               a.file_path,
               a.type         AS artifact_type,
               ca.note        AS pin_note
        FROM   case_artifacts ca
        JOIN   artifacts a ON a.artifact_id = ca.artifact_id
        WHERE  ca.case_id = ?
    """, (case_id,)).fetchall()

    # Merge and sort -- items without timestamp go to the bottom
    def _sort_ts(row):
        ts = row["effective_ts"]
        return ts if ts else "9999-99-99"

    timeline = sorted(
        [dict(r) for r in raw_signals] + [dict(r) for r in raw_artifacts],
        key=_sort_ts
    )

    # Pre-compute media URL for document artifacts
    for item in timeline:
        fp = item.get("file_path") or ""
        if fp:
            # Strip leading media/ to get the path relative to MEDIA_DIR
            rel = fp.replace("\\", "/")
            if rel.startswith("media/"):
                rel = rel[len("media/"):]
            item["media_url"] = f"/media/{rel}"
        else:
            item["media_url"] = None

    # -- Overlap: actors in this case who appear in OTHER active cases -------
    overlap = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type,
               COALESCE(m.influence_score, 0) AS influence_score,
               COUNT(DISTINCT ca2.case_id)    AS overlap_count,
               GROUP_CONCAT(
                   c2.case_id || '||' || c2.name, ';;'
               ) AS other_cases_raw
        FROM   case_actors ca1
        JOIN   actors ac  ON ac.actor_id  = ca1.actor_id
        LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
        JOIN   case_actors ca2 ON ca2.actor_id = ca1.actor_id
                               AND ca2.case_id != ca1.case_id
        JOIN   cases c2   ON c2.case_id   = ca2.case_id
                           AND LOWER(c2.status) = 'active'
        WHERE  ca1.case_id = ?
        GROUP  BY ac.actor_id
        ORDER  BY overlap_count DESC, COALESCE(m.influence_score, 0) DESC
    """, (case_id,)).fetchall()

    # Parse the GROUP_CONCAT into structured lists
    overlap_parsed = []
    for row in overlap:
        actor_dict = dict(row)
        cases_raw = (row["other_cases_raw"] or "").split(";;")
        parsed_cases = []
        for raw in cases_raw:
            if "||" in raw:
                cid_str, ctitle = raw.split("||", 1)
                try:
                    parsed_cases.append({"case_id": int(cid_str), "title": ctitle})
                except ValueError:
                    pass
        actor_dict["other_cases"] = parsed_cases
        del actor_dict["other_cases_raw"]
        desc = db.execute(
            "SELECT description FROM actors WHERE actor_id=?",
            (actor_dict["actor_id"],)
        ).fetchone()
        actor_dict["is_high_risk"] = (
            "HIGH_RISK" in (desc["description"] or "") if desc else False
        )
        overlap_parsed.append(actor_dict)

    stats = {
        "signals":   len(raw_signals),
        "artifacts": len(raw_artifacts),
        "actors":    len(roster),
        "overlap":   len(overlap_parsed),
    }

    return render_template(
        "case_workbench.html",
        case=case,
        roster=roster,
        timeline=timeline,
        overlap=overlap_parsed,
        stats=stats,
    )


@cases_bp.route("/cases/<int:case_id>/edit", methods=["POST"])
def case_edit(case_id: int):
    title       = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    hypothesis  = request.form.get("hypothesis", "").strip() or None
    case_type   = request.form.get("case_type", "general")
    status      = request.form.get("status", "active")
    if not title:
        flash("Case title is required.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))
    db = get_db()
    # Warm Start: recompute context_anchors whenever case text changes
    anchors_json = None
    try:
        from core.gravity import extract_location_anchors
        import json as _json
        seed_text = " ".join(filter(None, [title, description, hypothesis]))
        anchors = extract_location_anchors(seed_text)
        anchors_json = _json.dumps(anchors) if anchors else None
    except Exception:
        pass
    db.execute(
        "UPDATE cases SET name=?, description=?, hypothesis=?, case_type=?, status=?, context_anchors=? WHERE case_id=?",
        (title, description, hypothesis, case_type, status, anchors_json, case_id),
    )
    db.commit()
    flash("Case updated.", "success")
    return redirect(url_for("cases.case_detail", case_id=case_id))


@cases_bp.route("/api/cases/<int:case_id>/seed", methods=["POST"])
def api_case_seed(case_id: int):
    """
    Warm Start seed endpoint -- manually trigger or override context_anchors.

    Body JSON (all fields optional):
      { "seed_text": "...", "anchors": [{"lat": 14.0, "lng": 108.3, "label": "vietnam"}] }

    If `anchors` is provided it is stored as-is (explicit override).
    If only `seed_text` is provided, the GAZETTEER is scanned and results stored.
    Both fields may be sent together; explicit `anchors` takes precedence.
    Returns JSON: { ok: true, anchors: [...], count: N }
    """
    import json as _json
    from core.gravity import extract_location_anchors

    body = request.get_json(silent=True) or {}
    db = get_db()

    case = db.execute("SELECT case_id FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if not case:
        return jsonify({"ok": False, "error": "Case not found"}), 404

    explicit = body.get("anchors")
    if explicit is not None:
        anchors = explicit
    else:
        seed_text = body.get("seed_text", "")
        anchors = extract_location_anchors(seed_text)

    db.execute(
        "UPDATE cases SET context_anchors = ? WHERE case_id = ?",
        (_json.dumps(anchors) if anchors else None, case_id),
    )
    db.commit()
    return jsonify({"ok": True, "anchors": anchors, "count": len(anchors)})


@cases_bp.route("/cases/<int:case_id>/delete", methods=["POST"])
def case_delete(case_id: int):
    db = get_db()
    case = db.execute(
        "SELECT name FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if case:
        db.execute("DELETE FROM cases WHERE case_id=?", (case_id,))
        db.commit()
        flash(f"Case '{case['name']}' deleted.", "success")
    return redirect(url_for("cases.cases"))


@cases_bp.route("/cases/<int:case_id>/briefing")
def case_briefing(case_id: int):
    """
    Intelligence Briefing view -- all sequenced pins in narrative order
    with transition notes as connective tissue.  Unsequenced items are
    appended at the end under a separator.
    """
    db   = get_db()
    case = db.execute(
        "SELECT case_id, name, description, status, created_at, "
        "hypothesis, case_type, source_type, auto_generated, trigger_signal_id "
        "FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        flash("Case not found.", "error")
        return redirect(url_for("cases.cases"))

    # -- Pull sequenced items from all three tables --------------------------
    #    We need a unified list ordered by sequence_order, then pinned_at.
    #    We union the three tables and join to their entity tables.

    raw_events = db.execute("""
        SELECT 'event' AS kind,
               e.event_id AS entity_id,
               e.title, e.date, e.category, e.location, e.summary,
               NULL AS type, NULL AS source, NULL AS description_extra,
               ce.note, ce.sequence_order, ce.transition_note, ce.pinned_at
        FROM case_events ce
        JOIN events e ON e.event_id = ce.event_id
        WHERE ce.case_id = ?
    """, (case_id,)).fetchall()

    raw_actors = db.execute("""
        SELECT 'actor' AS kind,
               ac.actor_id AS entity_id,
               ac.name AS title, NULL AS date, NULL AS category,
               NULL AS location, ac.description AS summary,
               ac.type, NULL AS source, NULL AS description_extra,
               cac.note, cac.sequence_order, cac.transition_note, cac.pinned_at
        FROM case_actors cac
        JOIN actors ac ON ac.actor_id = cac.actor_id
        WHERE cac.case_id = ?
    """, (case_id,)).fetchall()

    raw_artifacts = db.execute("""
        SELECT 'artifact' AS kind,
               a.artifact_id AS entity_id,
               a.title, a.date, NULL AS category, a.location,
               a.description AS summary,
               a.type, a.source, e.title AS description_extra,
               ca.note, ca.sequence_order, ca.transition_note, ca.pinned_at
        FROM case_artifacts ca
        JOIN artifacts a ON a.artifact_id = ca.artifact_id
        LEFT JOIN events e ON e.event_id = a.event_id
        WHERE ca.case_id = ?
    """, (case_id,)).fetchall()

    all_items = (
        [dict(r) for r in raw_events]
        + [dict(r) for r in raw_actors]
        + [dict(r) for r in raw_artifacts]
    )

    # Sort: sequenced items first (by sequence_order), then unsequenced
    # (by pinned_at).  None sorts after integers in Python so we use a
    # sentinel.
    def _sort_key(item):
        so = item["sequence_order"]
        return (0 if so is not None else 1, so if so is not None else 0, item["pinned_at"] or "")

    all_items.sort(key=_sort_key)

    sequenced   = [i for i in all_items if i["sequence_order"] is not None]
    unsequenced = [i for i in all_items if i["sequence_order"] is None]

    # -- Count stats for header ----------------------------------------------
    stats = {
        "events":    sum(1 for i in all_items if i["kind"] == "event"),
        "actors":    sum(1 for i in all_items if i["kind"] == "actor"),
        "artifacts": sum(1 for i in all_items if i["kind"] == "artifact"),
        "total":     len(all_items),
        "sequenced": len(sequenced),
    }

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return render_template(
        "briefing.html",
        case=case,
        sequenced=sequenced,
        unsequenced=unsequenced,
        stats=stats,
        generated_at=generated_at,
    )


# -----------------------------------------------------------------------
# API: Cases list (lightweight)
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases-list")
def api_cases_list():
    """Lightweight JSON list of all cases for the pin widget dropdown."""
    db = get_db()
    rows = db.execute(
        "SELECT case_id, name, status FROM cases ORDER BY created_at DESC"
    ).fetchall()
    return jsonify({"cases": [dict(r) for r in rows]})


# -----------------------------------------------------------------------
# CT-1: Case anchors
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/anchors")
def api_case_anchors(case_id: int):
    """
    CT-1: Return the gravity anchors for a case -- actors, location count,
    keywords, and signal stats. Used by the feed UI to display the CT banner.
    """
    db = get_db()
    case = db.execute(
        "SELECT case_id, name, status FROM cases WHERE case_id = ?",
        (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404

    try:
        from core.gravity import build_context
        ctx = build_context(db, case_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    actor_rows = db.execute("""
        SELECT a.actor_id, a.name, a.type
        FROM   case_actors ca
        JOIN   actors a ON a.actor_id = ca.actor_id
        WHERE  ca.case_id = ?
    """, (case_id,)).fetchall()

    return jsonify({
        "case_id":       case_id,
        "case_title":    case["name"],
        "case_status":   case["status"],
        "actors":        [dict(r) for r in actor_rows],
        "signal_count":  db.execute(
            "SELECT COUNT(*) FROM case_signals WHERE case_id = ?",
            (case_id,)
        ).fetchone()[0],
        "location_count": len(ctx["locations"]),
        "keyword_count":  len(ctx["keywords"]),
        "keywords_sample": sorted(ctx["keywords"])[:20],
    })


# -----------------------------------------------------------------------
# CT-1: Fetch suggestions (gravity-scored)
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/fetch-suggestions")
def api_case_fetch_suggestions(case_id: int):
    """
    CT-1: Return the top 20 signals NOT already pinned to this case,
    ranked by gravity_score against the case context. Helps the analyst
    discover evidence they may have missed.
    """
    db = get_db()
    if not db.execute(
        "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone():
        return jsonify({"error": "Case not found"}), 404

    try:
        from core.gravity import build_context, score_item
        ctx = build_context(db, case_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # Get signals not yet pinned to this case
    candidate_rows = db.execute("""
        SELECT s.signal_id, s.title, s.content, s.source, s.stream,
               s.timestamp, s.lat, s.lng, s.is_priority,
               COALESCE(s.relevance_score, 0.5) AS relevance_score
        FROM   signals s
        WHERE  s.status IN ('raw', 'promoted')
          AND  s.signal_id NOT IN (
                   SELECT signal_id FROM case_signals WHERE case_id = ?
               )
        ORDER  BY s.relevance_score DESC, s.timestamp DESC
        LIMIT  500
    """, (case_id,)).fetchall()

    scored = []
    for r in candidate_rows:
        item = {
            "item_type":      "SIGNAL",
            "signal_id":      r["signal_id"],
            "title":          r["title"] or "(untitled)",
            "summary":        (r["content"] or "")[:200],
            "source":         r["source"] or "",
            "stream":         r["stream"] or "GLOBAL",
            "timestamp":      r["timestamp"],
            "is_priority":    r["is_priority"],
            "relevance_score": float(r["relevance_score"] or 0),
            "lat":            r["lat"],
            "lng":            r["lng"],
        }
        item["gravity_score"] = score_item(item, ctx)
        scored.append(item)

    scored.sort(key=lambda x: x["gravity_score"], reverse=True)
    top = [s for s in scored if s["gravity_score"] > 0][:20]

    return jsonify({
        "case_id":    case_id,
        "total":      len(top),
        "suggestions": top,
    })


# -----------------------------------------------------------------------
# E-2: Case evidence graph
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/evidence-graph")
def api_case_evidence_graph(case_id: int):
    """
    E-2: Return nodes (actors) and edges (entity_relationships) scoped to
    the actors pinned to this case.

    Nodes: all actors in case_actors for this case, enriched with
           actor_network_metrics (influence, betweenness, community).
    Edges: entity_relationships where BOTH subject AND object are case actors.
           Also includes one-hop edges where only one end is a case actor
           (flag: 'bridging': true) -- surfaces connected actors not yet in case.

    Response is Cytoscape.js-compatible:
      { nodes: [{data: {...}}], edges: [{data: {...}}] }
    """
    db = get_db()

    if not db.execute(
        "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone():
        return jsonify({"error": "Case not found"}), 404

    # Case actors
    case_actor_rows = db.execute("""
        SELECT a.actor_id, a.name, a.type,
               m.influence_score, m.betweenness, m.pagerank, m.community_id
        FROM   case_actors ca
        JOIN   actors a ON a.actor_id = ca.actor_id
        LEFT JOIN actor_network_metrics m ON m.actor_id = a.actor_id
        WHERE  ca.case_id = ?
    """, (case_id,)).fetchall()

    case_actor_ids = {r["actor_id"] for r in case_actor_rows}

    if not case_actor_ids:
        return jsonify({"nodes": [], "edges": [], "case_id": case_id,
                        "total_actors": 0, "total_edges": 0})

    # All edges touching at least one case actor
    placeholders = ",".join("?" * len(case_actor_ids))
    id_list = list(case_actor_ids)

    edge_rows = db.execute(f"""
        SELECT er.subject_actor_id, er.object_actor_id,
               er.relation_type, er.confidence,
               ar.title AS artifact_title,
               ar.artifact_id
        FROM   entity_relationships er
        LEFT JOIN artifacts ar ON ar.artifact_id = er.source_artifact_id
        WHERE  er.subject_actor_id IN ({placeholders})
           OR  er.object_actor_id  IN ({placeholders})
    """, id_list + id_list).fetchall()

    # Collect bridging actor ids (one-hop neighbours not in case)
    bridging_ids = set()
    for e in edge_rows:
        if e["subject_actor_id"] not in case_actor_ids:
            bridging_ids.add(e["subject_actor_id"])
        if e["object_actor_id"] not in case_actor_ids:
            bridging_ids.add(e["object_actor_id"])

    # Fetch bridging actor data
    bridging_nodes = []
    if bridging_ids:
        bph = ",".join("?" * len(bridging_ids))
        bridging_rows = db.execute(f"""
            SELECT a.actor_id, a.name, a.type,
                   m.influence_score, m.betweenness, m.pagerank, m.community_id
            FROM   actors a
            LEFT JOIN actor_network_metrics m ON m.actor_id = a.actor_id
            WHERE  a.actor_id IN ({bph})
        """, list(bridging_ids)).fetchall()
        bridging_nodes = [dict(r) for r in bridging_rows]

    # Build Cytoscape node list
    nodes = []
    for r in case_actor_rows:
        nodes.append({"data": {
            "id":           str(r["actor_id"]),
            "label":        r["name"],
            "type":         r["type"] or "unknown",
            "influence":    round(float(r["influence_score"] or 0), 4),
            "betweenness":  round(float(r["betweenness"] or 0), 4),
            "pagerank":     round(float(r["pagerank"] or 0), 4),
            "community":    r["community_id"],
            "in_case":      True,
        }})
    for r in bridging_nodes:
        nodes.append({"data": {
            "id":           str(r["actor_id"]),
            "label":        r["name"],
            "type":         r["type"] or "unknown",
            "influence":    round(float(r["influence_score"] or 0), 4),
            "betweenness":  round(float(r["betweenness"] or 0), 4),
            "pagerank":     round(float(r["pagerank"] or 0), 4),
            "community":    r["community_id"],
            "in_case":      False,
        }})

    # Build Cytoscape edge list
    edges = []
    seen_edges: set = set()
    for e in edge_rows:
        key = (e["subject_actor_id"], e["object_actor_id"], e["relation_type"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        bridging = (
            e["subject_actor_id"] not in case_actor_ids or
            e["object_actor_id"]  not in case_actor_ids
        )
        edges.append({"data": {
            "id":             f"{e['subject_actor_id']}-{e['object_actor_id']}-{e['relation_type']}",
            "source":         str(e["subject_actor_id"]),
            "target":         str(e["object_actor_id"]),
            "relation":       e["relation_type"],
            "confidence":     round(float(e["confidence"] or 0), 3),
            "artifact_title": e["artifact_title"],
            "artifact_id":    e["artifact_id"],
            "bridging":       bridging,
        }})

    return jsonify({
        "case_id":     case_id,
        "total_actors": len(nodes),
        "total_edges":  len(edges),
        "nodes":        nodes,
        "edges":        edges,
    })


# -----------------------------------------------------------------------
# Case signals API
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/signals")
def api_case_signals(case_id: int):
    """Returns all signals pinned to a case as JSON, ordered chronologically."""
    db   = get_db()
    case = db.execute("SELECT case_id, name, status FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case: return jsonify({"error": "Case not found"}), 404
    rows = db.execute("""
        SELECT s.signal_id, s.source, s.external_id, s.title, s.content,
               s.lat, s.lng, s.timestamp, s.status, s.is_priority, s.cluster_id,
               cs.note, cs.pinned_at
        FROM   case_signals cs
        JOIN   signals s ON s.signal_id = cs.signal_id
        WHERE  cs.case_id = ?
        ORDER  BY s.timestamp ASC
    """, (case_id,)).fetchall()
    return jsonify({"case_id": case_id, "case_title": case["name"],
                    "total": len(rows), "signals": [dict(r) for r in rows]})


# -----------------------------------------------------------------------
# Case overlap API
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/overlap")
def api_case_overlap(case_id: int):
    """Cross-case actor overlap -- JSON for the overlap panel."""
    db   = get_db()
    case = db.execute("SELECT case_id FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404

    rows = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type,
               COALESCE(m.influence_score, 0) AS influence_score,
               COUNT(DISTINCT ca2.case_id)    AS overlap_count,
               GROUP_CONCAT(c2.case_id || '||' || c2.name, ';;') AS other_cases_raw
        FROM   case_actors ca1
        JOIN   actors ac  ON ac.actor_id  = ca1.actor_id
        LEFT   JOIN actor_network_metrics m ON m.actor_id = ac.actor_id
        JOIN   case_actors ca2 ON ca2.actor_id = ca1.actor_id
                               AND ca2.case_id != ca1.case_id
        JOIN   cases c2   ON c2.case_id = ca2.case_id AND LOWER(c2.status) = 'active'
        WHERE  ca1.case_id = ?
        GROUP  BY ac.actor_id
        ORDER  BY overlap_count DESC
    """, (case_id,)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        parsed = []
        for raw in (d.pop("other_cases_raw") or "").split(";;"):
            if "||" in raw:
                cid_str, ctitle = raw.split("||", 1)
                try:
                    parsed.append({"case_id": int(cid_str), "title": ctitle})
                except ValueError:
                    pass
        d["other_cases"] = parsed
        result.append(d)

    return jsonify({"case_id": case_id, "actors": result, "total": len(result)})


# -----------------------------------------------------------------------
# Stable 1.2: Case-Scoped Conclave (Context Tunnel)
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/run_conclave", methods=["POST"])
def api_case_run_conclave(case_id: int):
    """
    Context Tunnel Conclave -- synthesizes ONLY the intelligence belonging
    to this case.

    Sequence
    --------
    1. Fetch all signal_ids from case_signals WHERE case_id=N
    2. Run NER + triple extraction scoped to those signals
    3. Compile a case-scoped wiki from case_actors seed entities
    4. Returns a pipeline_jobs job_id for telemetry tracking

    The global Control Room engines are untouched -- this is additive.
    """
    import datetime as _dt

    db = get_db()
    if not db.execute(
        "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone():
        return jsonify({"error": "Case not found"}), 404

    # Snapshot of signal IDs scoped to this case
    sig_rows = db.execute(
        "SELECT signal_id FROM case_signals WHERE case_id = ?", (case_id,)
    ).fetchall()
    signal_ids = [r["signal_id"] for r in sig_rows]

    actor_rows = db.execute(
        "SELECT actor_id FROM case_actors WHERE case_id = ?", (case_id,)
    ).fetchall()
    actor_ids = [r["actor_id"] for r in actor_rows]

    if not signal_ids:
        return jsonify({
            "error": "No signals pinned to this case yet. "
                     "Run a collector from the Workbench first.",
            "signal_count": 0,
        }), 422

    job_id = create_job(
        f"conclave_case_{case_id}",
        f"Context Tunnel: {len(signal_ids)} signals, "
        f"{len(actor_ids)} actors"
    )

    def _tunnel_worker(jid: int, sids: list, aids: list, cid: int):
        """Background synthesis -- NER, triples, wiki scoped to this case."""
        try:
            from core.db.connection import get_connection as _gc
            conn = _gc()

            # -- 1. NER pass over case signals --------------------------------
            update_job(jid, message="Context Tunnel: running NER pass...")
            try:
                from forage.processors.ner_processor import process_all as _ner
                _ner(signal_ids=sids, db_path=str(DB_PATH))
                update_job(jid, message=f"NER complete ({len(sids)} signals)")
            except Exception as exc:
                update_job(jid, message=f"NER skipped: {exc}")

            # -- 2. Triple extraction scoped to case signals ------------------
            update_job(jid, message="Context Tunnel: extracting triples...")
            try:
                from forage.processors.triple_extractor import run as _triples
                triples_written = _triples(signal_ids=sids, db_path=str(DB_PATH))
                update_job(jid, message=(
                    f"Triples: {triples_written or 0} relationships extracted"
                ))
            except Exception as exc:
                update_job(jid, message=f"Triples skipped: {exc}")

            # -- 3. Case-scoped wiki compilation from seed actors -------------
            update_job(jid, message="Context Tunnel: compiling case wiki...")
            try:
                from forage.processors.wiki_compiler import compile_case as _wiki
                wiki_count = _wiki(
                    case_id=cid, actor_ids=aids, db_path=str(DB_PATH)
                )
                update_job(jid, message=(
                    f"Wiki: {wiki_count or 0} articles compiled for case {cid}"
                ))
            except Exception as exc:
                update_job(jid, message=f"Wiki skipped: {exc}")

            conn.close()
            finalize_job(
                jid, "completed",
                f"Context Tunnel complete -- {len(sids)} signals synthesized",
                records_out=len(sids),
                progress=1.0,
            )

        except Exception as exc:
            finalize_job(jid, "failed", f"Context Tunnel error: {exc}")

    threading.Thread(
        target=_tunnel_worker,
        args=(job_id, signal_ids, actor_ids, case_id),
        daemon=True,
    ).start()

    return jsonify({
        "status":       "started",
        "job_id":       job_id,
        "job_key":      f"conclave_case_{case_id}",
        "case_id":      case_id,
        "signal_count": len(signal_ids),
        "actor_count":  len(actor_ids),
    })


# -----------------------------------------------------------------------
# Pin / Unpin API
# -----------------------------------------------------------------------

@cases_bp.route("/api/pin", methods=["POST"])
def api_pin():
    """
    Toggle-pin an entity to/from a case.
    Body (form or JSON): case_id, entity_type (artifact|event|actor), entity_id, note?
    Returns JSON: { pinned: bool, case_id, entity_type, entity_id }
    """
    data = request.get_json(silent=True) or request.form
    try:
        case_id     = int(data.get("case_id", 0))
        entity_type = data.get("entity_type", "")
        entity_id   = int(data.get("entity_id", 0))
        note        = (data.get("note") or "").strip() or None
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameters"}), 400

    if entity_type not in ("artifact", "event", "actor"):
        return jsonify({"error": "Unknown entity type"}), 400

    db = get_db()

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }
    table, id_col = TABLE_MAP[entity_type]

    existing = db.execute(
        f"SELECT 1 FROM {table} WHERE case_id=? AND {id_col}=?",
        (case_id, entity_id),
    ).fetchone()

    if existing:
        db.execute(
            f"DELETE FROM {table} WHERE case_id=? AND {id_col}=?",
            (case_id, entity_id),
        )
        db.commit()
        return jsonify({"pinned": False, "case_id": case_id,
                        "entity_type": entity_type, "entity_id": entity_id})
    else:
        db.execute(
            f"INSERT INTO {table} (case_id, {id_col}, note) VALUES (?, ?, ?)",
            (case_id, entity_id, note),
        )
        db.commit()
        return jsonify({"pinned": True, "case_id": case_id,
                        "entity_type": entity_type, "entity_id": entity_id})


@cases_bp.route("/api/pin/status")
def api_pin_status():
    """
    Returns pinned status of an entity across all cases.
    Query params: entity_type, entity_id
    """
    entity_type = request.args.get("entity_type", "")
    try:
        entity_id = int(request.args.get("entity_id", 0))
    except ValueError:
        return jsonify({"error": "Invalid entity_id"}), 400

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }
    if entity_type not in TABLE_MAP:
        return jsonify({"error": "Unknown entity type"}), 400

    table, id_col = TABLE_MAP[entity_type]
    db = get_db()

    pinned_in = db.execute(
        f"""SELECT c.case_id, c.name, c.status
            FROM {table} j
            JOIN cases c ON c.case_id = j.case_id
            WHERE j.{id_col} = ?""",
        (entity_id,),
    ).fetchall()

    return jsonify({
        "entity_type": entity_type,
        "entity_id":   entity_id,
        "pinned_in":   [dict(r) for r in pinned_in],
    })


@cases_bp.route("/api/pin/note", methods=["POST"])
def api_pin_note():
    """Update the note on a pinned entity."""
    data      = request.get_json(silent=True) or request.form
    case_id   = int(data.get("case_id", 0))
    entity_type = data.get("entity_type", "")
    entity_id = int(data.get("entity_id", 0))
    note      = (data.get("note") or "").strip() or None

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }
    if entity_type not in TABLE_MAP:
        return jsonify({"error": "Unknown entity type"}), 400

    table, id_col = TABLE_MAP[entity_type]
    db = get_db()
    db.execute(
        f"UPDATE {table} SET note=? WHERE case_id=? AND {id_col}=?",
        (note, case_id, entity_id),
    )
    db.commit()
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# Narrative Threader -- sequence / transition APIs
# -----------------------------------------------------------------------

@cases_bp.route("/api/sequence", methods=["POST"])
def api_sequence():
    """
    Persist a sequence_order for one pinned entity.
    Body JSON: { case_id, entity_type, entity_id, sequence_order }
    Returns JSON: { ok: true }

    sequence_order=null clears the ordering (unthreaded state).
    Accepts a batch list via items=[{entity_type, entity_id, sequence_order}]
    for full-board drag-and-drop saves.
    """
    data = request.get_json(silent=True) or {}

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }

    db      = get_db()
    try:
        case_id = int(data.get("case_id", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid case_id"}), 400

    # -- batch mode ----------------------------------------------------------
    items = data.get("items")
    if items:
        for item in items:
            et = item.get("entity_type", "")
            ei = item.get("entity_id")
            so = item.get("sequence_order")          # None clears it
            if et not in TABLE_MAP or not ei:
                continue
            try:
                ei_int = int(ei)
            except (ValueError, TypeError):
                continue
            table, id_col = TABLE_MAP[et]
            db.execute(
                f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
                (so, case_id, ei_int),
            )
        db.commit()
        return jsonify({"ok": True, "updated": len(items)})

    # -- single mode ---------------------------------------------------------
    entity_type    = data.get("entity_type", "")
    try:
        entity_id  = int(data.get("entity_id", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid entity_id"}), 400
    sequence_order = data.get("sequence_order")      # None clears it

    if entity_type not in TABLE_MAP:
        return jsonify({"error": "Unknown entity type"}), 400

    table, id_col = TABLE_MAP[entity_type]
    db.execute(
        f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
        (sequence_order, case_id, entity_id),
    )
    db.commit()
    return jsonify({"ok": True})


@cases_bp.route("/api/transition", methods=["POST"])
def api_transition():
    """
    Persist the transition_note bridging text that follows a pinned entity.
    Body JSON: { case_id, entity_type, entity_id, transition_note }
    Returns JSON: { ok: true }
    """
    data = request.get_json(silent=True) or {}

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }

    entity_type     = data.get("entity_type", "")
    entity_id       = int(data.get("entity_id", 0))
    case_id         = int(data.get("case_id", 0))
    transition_note = (data.get("transition_note") or "").strip() or None

    if entity_type not in TABLE_MAP:
        return jsonify({"error": "Unknown entity type"}), 400

    table, id_col = TABLE_MAP[entity_type]
    db = get_db()
    db.execute(
        f"UPDATE {table} SET transition_note=? WHERE case_id=? AND {id_col}=?",
        (transition_note, case_id, entity_id),
    )
    db.commit()
    return jsonify({"ok": True})


@cases_bp.route("/api/auto-sequence/<int:case_id>", methods=["POST"])
def api_auto_sequence(case_id: int):
    """
    Algorithmic Sequencing Suggestion:
    Assigns sequence_order to ALL pinned entities based on the best
    available temporal signal, falling back to a stable heuristic.

    PRIORITY ORDER for ordering:
    1. Events: sorted by their own date (ISO string sort).  Events with
       no date are appended after dated ones.
    2. Artifacts: sorted by their date; if null, by the date of their
       parent event; otherwise appended last.
    3. Actors: no intrinsic date -- sorted by the earliest event date
       they participate in within this case; otherwise appended last.

    All three entity types are merged into one global sequence -- the
    chronological backbone is events, artifacts weave in around their
    event date, actors anchor to their earliest relevant event.

    Returns: { ok, items: [{entity_type, entity_id, sequence_order,
                            effective_date}] }
    """
    import datetime as _dt

    db   = get_db()
    case = db.execute(
        "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404

    SORT_NONE = "9999-99-99"   # sentinel -- pushes undated items to end

    # -- Events ---------------------------------------------------------------
    ev_rows = db.execute("""
        SELECT e.event_id, e.date
        FROM   case_events ce
        JOIN   events e ON e.event_id = ce.event_id
        WHERE  ce.case_id = ?
    """, (case_id,)).fetchall()

    events_seq = [
        ("event", r["event_id"], r["date"] or SORT_NONE)
        for r in ev_rows
    ]

    # -- Artifacts ------------------------------------------------------------
    ar_rows = db.execute("""
        SELECT a.artifact_id, a.date AS a_date, e.date AS e_date
        FROM   case_artifacts ca
        JOIN   artifacts a ON a.artifact_id = ca.artifact_id
        LEFT JOIN events e ON e.event_id = a.event_id
        WHERE  ca.case_id = ?
    """, (case_id,)).fetchall()

    artifacts_seq = []
    for r in ar_rows:
        eff_date = r["a_date"] or r["e_date"] or SORT_NONE
        artifacts_seq.append(("artifact", r["artifact_id"], eff_date))

    # -- Actors ---------------------------------------------------------------
    # Anchor each actor to the earliest event date they share with this case
    ac_rows = db.execute("""
        SELECT ac.actor_id,
               MIN(COALESCE(e.date, ?)) AS earliest_event_date
        FROM   case_actors cac
        JOIN   actors ac ON ac.actor_id = cac.actor_id
        LEFT JOIN actor_events ae ON ae.actor_id = ac.actor_id
        LEFT JOIN case_events  ce ON ce.event_id = ae.event_id
                                  AND ce.case_id = cac.case_id
        LEFT JOIN events e ON e.event_id = ae.event_id
                            AND ce.case_id = ?
        WHERE  cac.case_id = ?
        GROUP  BY ac.actor_id
    """, (SORT_NONE, case_id, case_id)).fetchall()

    actors_seq = [
        ("actor", r["actor_id"], r["earliest_event_date"] or SORT_NONE)
        for r in ac_rows
    ]

    # -- Merge + sort ---------------------------------------------------------
    all_items = events_seq + artifacts_seq + actors_seq
    all_items.sort(key=lambda x: (x[2], x[0], x[1]))   # date, type, id

    TABLE_MAP = {
        "artifact": ("case_artifacts", "artifact_id"),
        "event":    ("case_events",    "event_id"),
        "actor":    ("case_actors",    "actor_id"),
    }

    result = []
    for seq_num, (etype, eid, eff_date) in enumerate(all_items, start=1):
        table, id_col = TABLE_MAP[etype]
        db.execute(
            f"UPDATE {table} SET sequence_order=? WHERE case_id=? AND {id_col}=?",
            (seq_num, case_id, eid),
        )
        result.append({
            "entity_type":    etype,
            "entity_id":      eid,
            "sequence_order": seq_num,
            "effective_date": eff_date if eff_date != SORT_NONE else None,
        })

    db.commit()
    return jsonify({"ok": True, "items": result})


# -----------------------------------------------------------------------
# Suggestions + Heat Score API
# -----------------------------------------------------------------------

@cases_bp.route("/api/suggestions/<int:case_id>")
def api_suggestions(case_id: int):
    """
    Returns a JSON object with:
      - suggestions: ranked list of hidden entities (events, actors, artifacts)
      - heat:        the full Heat Score breakdown
      - meta:        counts and algorithm parameters
    """
    db = get_db()

    case = db.execute(
        "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404

    heat        = _compute_heat_score(db, case_id)
    suggestions = _compute_suggestions(db, case_id)

    return jsonify({
        "case_id":     case_id,
        "heat":        heat,
        "suggestions": suggestions,
        "meta": {
            "suggestion_count": len(suggestions),
            "types": {
                "events":    sum(1 for s in suggestions if s["type"] == "event"),
                "actors":    sum(1 for s in suggestions if s["type"] == "actor"),
                "artifacts": sum(1 for s in suggestions if s["type"] == "artifact"),
            },
        },
    })


# -----------------------------------------------------------------------
# Signal-to-Case pin routes
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/pin/<signal_id>", methods=["POST"])
def api_case_pin_signal(case_id: int, signal_id: str):
    """Toggle-pin a FORAGE signal into a FORGE case."""
    db     = get_db()
    case   = db.execute("SELECT case_id, name FROM cases WHERE case_id=?", (case_id,)).fetchone()
    signal = db.execute("SELECT signal_id FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
    if not case:   return jsonify({"error": "Case not found"}), 404
    if not signal: return jsonify({"error": "Signal not found"}), 404

    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip() or None

    existing = db.execute(
        "SELECT 1 FROM case_signals WHERE case_id=? AND signal_id=?",
        (case_id, signal_id),
    ).fetchone()

    if existing:
        db.execute("DELETE FROM case_signals WHERE case_id=? AND signal_id=?", (case_id, signal_id))
        db.commit()
        return jsonify({"pinned": False, "case_id": case_id, "signal_id": signal_id, "case_title": case["name"]})
    else:
        db.execute("INSERT INTO case_signals (case_id, signal_id, note) VALUES (?, ?, ?)", (case_id, signal_id, note))
        db.commit()
        return jsonify({"pinned": True, "case_id": case_id, "signal_id": signal_id, "case_title": case["name"]})


@cases_bp.route("/api/cases/<int:case_id>/pin-signal", methods=["POST"])
def api_case_pin_signal_inline(case_id: int):
    """
    Pins a signal by UUID to this case. Used by case_detail inline form.
    Body JSON: { signal_id: str, note?: str }
    Distinct from api_case_pin_signal (URL-param route at /api/cases/<id>/pin/<signal_id>).
    """
    from flask import request as req
    db   = get_db()
    data = req.get_json(silent=True) or {}
    sig_id = (data.get("signal_id") or "").strip()
    note   = (data.get("note")      or "").strip() or None
    if not sig_id:
        return jsonify({"error": "signal_id required"}), 400
    case = db.execute(
        "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404
    sig = db.execute(
        "SELECT signal_id FROM signals WHERE signal_id=?", (sig_id,)
    ).fetchone()
    if not sig:
        return jsonify({"error": "Signal not found"}), 404
    try:
        db.execute(
            "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
            (case_id, sig_id, note)
        )
        db.commit()
        return jsonify({"ok": True, "status": "pinned",
                        "case_id": case_id, "signal_id": sig_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cases_bp.route("/api/cases/<int:case_id>/signals/<signal_id>", methods=["DELETE"])
def api_case_unpin_signal(case_id: int, signal_id: str):
    """Unpins a signal from a case. Used by case_detail unpin button."""
    db = get_db()
    try:
        db.execute(
            "DELETE FROM case_signals WHERE case_id=? AND signal_id=?",
            (case_id, signal_id)
        )
        db.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cases_bp.route("/api/signals/<signal_id>/cases")
def api_signal_cases(signal_id: str):
    """Returns all cases a signal is pinned to."""
    db  = get_db()
    sig = db.execute("SELECT signal_id FROM signals WHERE signal_id=?", (signal_id,)).fetchone()
    if not sig: return jsonify({"error": "Signal not found"}), 404
    rows = db.execute("""
        SELECT c.case_id, c.name, c.status, cs.pinned_at, cs.note
        FROM   case_signals cs
        JOIN   cases c ON c.case_id = cs.case_id
        WHERE  cs.signal_id = ?
        ORDER  BY cs.pinned_at DESC
    """, (signal_id,)).fetchall()
    return jsonify({"signal_id": signal_id, "pinned_in": [dict(r) for r in rows]})


# -----------------------------------------------------------------------
# Case Correlation Intelligence
# -----------------------------------------------------------------------

@cases_bp.route("/api/cases/<int:case_id>/correlations")
def api_case_correlations(case_id: int):
    """
    Returns correlated_incidents pairs where BOTH signals are pinned
    to this case via case_signals. Surfaces VBS/Makhado 1.000 score
    and all other in-case patterns automatically.
    """
    db = get_db()
    case = db.execute(
        "SELECT case_id FROM cases WHERE case_id=?", (case_id,)
    ).fetchone()
    if not case:
        return jsonify({"error": "Case not found"}), 404
    try:
        rows = db.execute("""
            SELECT ci.id,
                   ci.correlation_score,
                   ci.distance_km,
                   ci.time_difference_hours,
                   ci.space_score,
                   ci.time_score,
                   ci.detected_at,
                   sa.signal_id AS signal_id_a,
                   sa.title     AS title_a,
                   sa.source    AS src_a,
                   sa.stream    AS stream_a,
                   sa.lat       AS lat_a,
                   sa.lng       AS lng_a,
                   sb.signal_id AS signal_id_b,
                   sb.title     AS title_b,
                   sb.source    AS src_b,
                   sb.stream    AS stream_b,
                   sb.lat       AS lat_b,
                   sb.lng       AS lng_b
            FROM   correlated_incidents ci
            JOIN   signals sa ON sa.signal_id = ci.signal_a
            JOIN   signals sb ON sb.signal_id = ci.signal_b
            WHERE  EXISTS (
                       SELECT 1 FROM case_signals cs
                       WHERE  cs.case_id   = :cid
                         AND  cs.signal_id = ci.signal_a
                   )
              AND  EXISTS (
                       SELECT 1 FROM case_signals cs
                       WHERE  cs.case_id   = :cid
                         AND  cs.signal_id = ci.signal_b
                   )
            ORDER  BY ci.correlation_score DESC
            LIMIT  200
        """, {"cid": case_id}).fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc), "pairs": []}), 500
    return jsonify({
        "case_id": case_id,
        "pairs":   [dict(r) for r in rows],
        "total":   len(rows),
    })


@cases_bp.route("/api/correlations/promote-case", methods=["POST"])
def api_correlation_promote_case():
    """
    Creates a new Case seeded with both signals from a correlated pair.
    Body JSON: { correlation_id: int, title?: str }
    Used by the feed.html CORRELATION card "Open as Case" button.
    """
    from flask import request as req
    db   = get_db()
    data = req.get_json(silent=True) or {}
    corr_id    = data.get("correlation_id")
    case_title = (data.get("title") or "").strip() or None
    if not corr_id:
        return jsonify({"error": "correlation_id required"}), 400
    row = db.execute(
        "SELECT ci.id, ci.signal_a, ci.signal_b, ci.correlation_score, "
        "ci.distance_km, ci.time_difference_hours, "
        "sa.title AS title_a, sa.source AS src_a, "
        "sb.title AS title_b, sb.source AS src_b "
        "FROM correlated_incidents ci "
        "JOIN signals sa ON sa.signal_id = ci.signal_a "
        "JOIN signals sb ON sb.signal_id = ci.signal_b "
        "WHERE ci.id = ?",
        (corr_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Correlation not found"}), 404
    auto_title = case_title or (
        f"Pattern: {(row['title_a'] or '')[:40]} ↔ {(row['title_b'] or '')[:40]}"
    )
    hypothesis = (
        f"Correlation score {row['correlation_score']:.3f} — "
        f"{row['distance_km']:.1f} km apart, "
        f"{row['time_difference_hours']:.2f} h apart. "
        f"Sources: {row['src_a']} ↔ {row['src_b']}."
    )
    description = (
        f"Auto-generated from correlated pair #{corr_id}. "
        f"Signals: [{row['signal_a'][:8]}…] and [{row['signal_b'][:8]}…]. "
        f"Score: {row['correlation_score']:.3f}."
    )
    try:
        cur = db.execute(
            "INSERT INTO cases (name, description, hypothesis, status, case_type) "
            "VALUES (?,?,?,'active','general')",
            (auto_title, description, hypothesis)
        )
        case_id = cur.lastrowid
        for sig_id in (row["signal_a"], row["signal_b"]):
            db.execute(
                "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note) VALUES (?,?,?)",
                (case_id, sig_id,
                 f"Auto-pinned from correlation #{corr_id} (score {row['correlation_score']:.3f})")
            )
        db.commit()
        return jsonify({
            "case_id":  case_id,
            "status":   "created",
            "score":    row["correlation_score"],
            "signal_a": row["signal_a"],
            "signal_b": row["signal_b"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
