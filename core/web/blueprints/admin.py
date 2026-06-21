#!/usr/bin/env python3
from __future__ import annotations

"""
Admin CRUD blueprint — extracted from app.py.

Routes: /admin, /admin/event/new, /artifact/*, /event/*, /actor/*,
        /api/artifacts/*, /api/correlations/*, /api/alerts/*,
        /api/sentinel/*, /dossier/*, /document/*
"""

import re as _re
import sqlite3

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from core.pipeline.ingest import process_artifact_upload, IMAGE_EXTENSIONS
from core.web.helpers import (
    get_db,
    BASE_DIR,
    DB_PATH,
    MEDIA_DIR,
    ADMIN_PASSWORD,
    ACTOR_PHOTO_EXTENSIONS,
    _VALID_ACTOR_TYPES,
)

admin_bp = Blueprint("admin", __name__)


# ── Smart promotion helpers ───────────────────────────────────────────────

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


# ---------------------------------------------------------------------------
# Route: /admin/event/new — smart promotion from signal
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/event/new")
def admin_event_new():
    """
    Pre-filled event creation form — smart mode.

    When a signal_id is provided, fetches the full signal from the DB
    and applies intelligent inference:
      * title   -- stripped of collector prefixes, normalised
      * category -- inferred from content keywords, stream, source
      * summary  -- enriched with source metadata and SOCINT tags
      * date     -- extracted from signal timestamp
      * coords   -- pulled directly from signal lat/lng

    Query-string params serve as fallback when no signal_id is given.
    """
    import json as _json
    db = get_db()

    signal_id   = request.args.get("signal_id", "")
    source_signal: dict | None = None

    if signal_id:
        row = db.execute(
            """
            SELECT signal_id, title, content, source, stream,
                   relevance_score, timestamp, lat, lng,
                   metadata_json, status, is_priority
            FROM   signals WHERE signal_id = ?
            """,
            (signal_id,),
        ).fetchone()
        if row:
            source_signal = dict(row)

    if source_signal:
        # -- Smart inference from full signal record
        prefill = {
            "title":    _clean_title(source_signal.get("title", "")),
            "summary":  _build_summary(source_signal),
            "date":     (source_signal.get("timestamp") or "")[:10],
            "location": request.args.get("location", ""),
            "latitude":  str(source_signal["lat"])  if source_signal.get("lat")  else "",
            "longitude": str(source_signal["lng"])  if source_signal.get("lng")  else "",
            "category": _infer_category(
                source_signal.get("title",   ""),
                source_signal.get("content", ""),
                source_signal.get("stream",  ""),
                source_signal.get("source",  ""),
            ),
        }
    else:
        # -- Fallback: honour raw query-string params
        prefill = {
            "title":     request.args.get("title",     ""),
            "summary":   request.args.get("summary",   ""),
            "date":      request.args.get("date",      ""),
            "location":  request.args.get("location",  ""),
            "latitude":  request.args.get("latitude",  ""),
            "longitude": request.args.get("longitude", ""),
            "category":  request.args.get("category",  "Other"),
        }

    event_categories = [
        "Election", "Security", "Civil Unrest", "Legislative",
        "Economic", "Diplomatic", "Military", "Social", "Other",
    ]

    return render_template(
        "admin_event_new.html",
        prefill=prefill,
        signal_id=signal_id,
        source_signal=source_signal,
        event_categories=event_categories,
        admin_password=ADMIN_PASSWORD,
    )


@admin_bp.route("/admin/event/new", methods=["POST"])
def admin_event_new_post():
    """
    Handles submission of the pre-filled event creation form.
    On success, marks the originating signal as 'promoted' if a
    signal_id was provided, then redirects to the new event.
    """
    db = get_db()

    if request.form.get("password") != ADMIN_PASSWORD:
        flash("Incorrect password — event not saved.", "error")
        return redirect(url_for("admin.admin_event_new"))

    title    = request.form.get("ev_title",    "").strip()
    summary  = request.form.get("ev_summary",  "").strip() or None
    date     = request.form.get("ev_date",     "").strip() or None
    location = request.form.get("ev_location", "").strip() or None
    category = request.form.get("ev_category", "Other")
    signal_id= request.form.get("signal_id",   "").strip() or None

    raw_lat = request.form.get("ev_latitude",  "").strip()
    raw_lon = request.form.get("ev_longitude", "").strip()

    if not title:
        flash("Event title is required.", "error")
        return redirect(url_for("admin.admin_event_new"))

    try:
        lat = float(raw_lat) if raw_lat else None
        lon = float(raw_lon) if raw_lon else None
    except ValueError:
        lat = lon = None

    cur = db.execute("""
        INSERT INTO events
            (title, summary, date, location, latitude, longitude, category, source_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'live')
    """, (title, summary, date, location, lat, lon, category))
    db.commit()
    new_event_id = cur.lastrowid

    # Mark the originating signal as promoted
    if signal_id:
        db.execute(
            "UPDATE signals SET status = 'promoted' WHERE signal_id = ?",
            (signal_id,),
        )
        db.commit()

    flash(f"Event '{title}' created successfully.", "success")
    return redirect(url_for("admin.event_detail", event_id=new_event_id))


# ---------------------------------------------------------------------------
# Route: /admin — main admin panel (GET + POST)
# ---------------------------------------------------------------------------

@admin_bp.route("/admin", methods=["GET", "POST"])
def admin():
    db = get_db()

    if request.method == "POST":
        # -- simple password gate
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("admin.admin"))

        action = request.form.get("action", "")

        # -- ingest artifact
        if action == "add_artifact":
            title       = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            atype       = request.form.get("type", "document")
            date        = request.form.get("date", "").strip() or None
            location    = request.form.get("location", "").strip() or None
            latitude    = request.form.get("latitude", "").strip() or None
            longitude   = request.form.get("longitude", "").strip() or None
            tags        = request.form.get("tags", "").strip() or None
            source      = request.form.get("source", "unverified")
            event_id    = request.form.get("event_id", "").strip() or None

            if not title:
                flash("Artifact title is required.", "error")
                return redirect(url_for("admin.admin"))

            # -- file upload
            file_path = None
            thumbnail = None
            uploaded  = request.files.get("file")

            if uploaded and uploaded.filename:
                try:
                    upload_info = process_artifact_upload(uploaded, metadata={
                        "media_dir": str(MEDIA_DIR)
                    })
                    file_path = upload_info.get("file_path")
                    thumbnail = upload_info.get("thumbnail")
                    ext = upload_info.get("extension")
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("admin.admin"))
                except Exception as exc:
                    flash("Failed to process uploaded file.", "error")
                    return redirect(url_for("admin.admin"))

            # -- Phase 19 rev.2: live extraction on ingest
            # OCR (pytesseract) and PDF (PyMuPDF) are now live.
            # Audio/video remain 'pending' until Whisper is installed.
            raw_text_cache    = None
            processing_status = "pending"

            if file_path:
                abs_path = BASE_DIR / file_path
                if ext == "txt":
                    try:
                        raw_text_cache    = abs_path.read_text(
                            encoding="utf-8", errors="replace")[:50_000]
                        processing_status = "done"
                    except Exception:
                        processing_status = "failed"

                elif ext == "pdf":
                    try:
                        from forage.processors.artifact_processor import (
                            PDFPipeline, OCRPipeline,
                        )
                        _pdf = PDFPipeline()
                        _ocr = OCRPipeline() if OCRPipeline().available() else None
                        raw_text_cache = _pdf.extract(abs_path, ocr_pipeline=_ocr)
                        if raw_text_cache:
                            raw_text_cache    = raw_text_cache[:50_000]
                            processing_status = "done"
                        else:
                            processing_status = "failed"
                    except Exception:
                        processing_status = "failed"

                elif ext in IMAGE_EXTENSIONS or atype in ("photo", "capture"):
                    try:
                        from forage.processors.artifact_processor import OCRPipeline
                        _ocr = OCRPipeline()
                        if _ocr.available():
                            raw_text_cache = _ocr.extract(abs_path)
                            if raw_text_cache:
                                raw_text_cache    = raw_text_cache[:50_000]
                                processing_status = "done"
                            else:
                                processing_status = "skipped"
                        else:
                            processing_status = "pending"
                    except Exception:
                        processing_status = "failed"

                elif ext in {"mp3","wav","ogg","m4a","mp4","mov","avi","mkv"}:
                    processing_status = "pending"   # future: Whisper
                else:
                    processing_status = "skipped"
            else:
                if description:
                    raw_text_cache    = description
                    processing_status = "done"
                else:
                    processing_status = "skipped"

            # Try full Phase 19 INSERT; fall back to base INSERT if
            # columns don't exist yet (pre-migration safety net)
            try:
                cur = db.execute("""
                    INSERT INTO artifacts
                        (title, description, type, date, location,
                         latitude, longitude, tags, source,
                         file_path, thumbnail, event_id,
                         raw_text_cache, processing_status, source_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')
                """, (
                    title, description, atype, date, location,
                    float(latitude)  if latitude  else None,
                    float(longitude) if longitude else None,
                    tags, source, file_path, thumbnail,
                    int(event_id) if event_id else None,
                    raw_text_cache, processing_status,
                ))
            except Exception:
                cur = db.execute("""
                    INSERT INTO artifacts
                        (title, description, type, date, location,
                         latitude, longitude, tags, source,
                         file_path, thumbnail, event_id, source_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')
                """, (
                    title, description, atype, date, location,
                    float(latitude)  if latitude  else None,
                    float(longitude) if longitude else None,
                    tags, source, file_path, thumbnail,
                    int(event_id) if event_id else None,
                ))
            new_artifact_id = cur.lastrowid
            db.commit()

            # Trigger inline NER if text is ready
            if raw_text_cache and processing_status == "done":
                try:
                    from forage.processors.artifact_processor import ProcessorManager
                    ProcessorManager(db_path=DB_PATH).process_artifact(
                        artifact_id=new_artifact_id,
                        raw_text=raw_text_cache,
                        artifact_type=atype,
                    )
                except Exception:
                    pass  # NER failure never blocks ingest

            # -- Phase 20: Forensic extraction on ingest
            if file_path:
                try:
                    from forage.processors.forensic_processor import hash_file, extract_exif
                    import json as _fj
                    _abs = BASE_DIR / file_path
                    _sha, _md5 = hash_file(_abs)
                    _sz   = _abs.stat().st_size if _abs.exists() else None
                    _exif = extract_exif(_abs)
                    _ej   = _fj.dumps(_exif, ensure_ascii=False) if _exif else None
                    _glat = _exif.get("gps_lat")           if _exif else None
                    _glng = _exif.get("gps_lng")           if _exif else None
                    _make = _exif.get("make")              if _exif else None
                    _mod  = _exif.get("model")             if _exif else None
                    _edt  = _exif.get("datetime_original") if _exif else None
                    try:
                        db.execute(
                            "UPDATE artifacts SET file_hash_sha256=?,file_hash_md5=?,"
                            "file_size_bytes=?,exif_json=?,gps_lat=?,gps_lng=?,"
                            "device_make=?,device_model=?,exif_datetime=? "
                            "WHERE artifact_id=?",
                            (_sha,_md5,_sz,_ej,_glat,_glng,_make,_mod,_edt,cur.lastrowid))
                        if _glat and _glng and not latitude:
                            db.execute(
                                "UPDATE artifacts SET latitude=?,longitude=? WHERE artifact_id=?",
                                (_glat, _glng, cur.lastrowid))
                        if _sha:
                            _dups = db.execute(
                                "SELECT artifact_id FROM artifacts "
                                "WHERE file_hash_sha256=? AND artifact_id!=?",
                                (_sha, cur.lastrowid)).fetchall()
                            for _d in _dups:
                                db.execute(
                                    "INSERT OR IGNORE INTO artifact_duplicates "
                                    "(artifact_id,duplicate_of_id,hash_sha256) VALUES(?,?,?)",
                                    (cur.lastrowid, _d["artifact_id"], _sha))
                        db.commit()
                    except Exception:
                        pass
                except Exception:
                    pass

            flash(f"Artifact '{title}' ingested successfully.", "success")
            return redirect(url_for("admin.admin"))

        # -- add event
        if action == "add_event":
            title    = request.form.get("ev_title", "").strip()
            summary  = request.form.get("ev_summary", "").strip() or None
            date     = request.form.get("ev_date", "").strip() or None
            location = request.form.get("ev_location", "").strip() or None
            lat      = request.form.get("ev_latitude", "").strip() or None
            lon      = request.form.get("ev_longitude", "").strip() or None
            category = request.form.get("ev_category", "Other")

            if not title:
                flash("Event title is required.", "error")
                return redirect(url_for("admin.admin"))

            db.execute("""
                INSERT INTO events
                    (title, summary, date, location, latitude, longitude, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (title, summary, date, location,
                  float(lat) if lat else None,
                  float(lon) if lon else None,
                  category))
            db.commit()
            flash(f"Event '{title}' added successfully.", "success")
            return redirect(url_for("admin.admin"))

        # -- add actor
        if action == "add_actor":
            name        = request.form.get("ac_name", "").strip()
            atype       = request.form.get("ac_type", "unknown").strip().lower()
            description = request.form.get("ac_description", "").strip() or None

            _VALID_ACTOR_TYPES_SET = frozenset([
                "person", "institution", "media", "movement",
                "government", "location", "political_party",
                "organization", "unknown", "other", "paramilitary",
            ])

            if not name:
                flash("Actor name is required.", "error")
                return redirect(url_for("admin.admin"))

            if atype not in _VALID_ACTOR_TYPES_SET:
                flash(
                    f"Invalid actor type '{atype}'. "
                    f"Allowed: {', '.join(sorted(_VALID_ACTOR_TYPES_SET))}",
                    "error",
                )
                return redirect(url_for("admin.admin"))

            try:
                db.execute(
                    "INSERT INTO actors (name, type, description, source_type) VALUES (?, ?, ?, 'live')",
                    (name, atype, description),
                )
                db.commit()
                flash(f"Actor '{name}' added successfully.", "success")
            except Exception as _e:
                db.rollback()
                flash(f"Could not add actor: {_e}", "error")
            return redirect(url_for("admin.admin"))

    # -- GET — build admin dashboard data
    events_list = db.execute("""
        SELECT event_id, title, date, category FROM events ORDER BY date DESC
    """).fetchall()

    recent_artifacts = db.execute("""
        SELECT a.artifact_id, a.title, a.type, a.source, a.date,
               e.title AS event_title
        FROM   artifacts a
        LEFT   JOIN events e ON e.event_id = a.event_id
        ORDER  BY a.created_at DESC
        LIMIT  10
    """).fetchall()

    stats = {
        "artifacts": db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
        "events":    db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "actors":    db.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
    }

    event_categories = [
        "Election","Security","Civil Unrest","Legislative",
        "Economic","Diplomatic","Military","Social","Other",
    ]

    actor_types_list = [
        "person", "institution", "government", "organization",
        "movement", "media", "political_party", "location",
        "other", "paramilitary", "unknown",
    ]

    actors_list = db.execute(
        "SELECT actor_id, name, type FROM actors ORDER BY name"
    ).fetchall()

    return render_template(
        "admin.html",
        events=events_list,
        recent_artifacts=recent_artifacts,
        stats=stats,
        event_categories=event_categories,
        actor_types_list=actor_types_list,
        actors_list=actors_list,
        admin_password=ADMIN_PASSWORD,
    )


# ---------------------------------------------------------------------------
# Route: Artifact detail — Phase 4: Circle of Evidence
# ---------------------------------------------------------------------------

@admin_bp.route("/artifact/<int:artifact_id>")
def artifact_detail(artifact_id: int):
    db  = get_db()

    # Core artifact + its parent event in one query
    artifact = db.execute("""
        SELECT a.*,
               e.title      AS event_title,
               e.event_id   AS linked_event_id,
               e.date       AS event_date,
               e.category   AS event_category,
               e.location   AS event_location,
               e.summary    AS event_summary
        FROM   artifacts a
        LEFT   JOIN events e ON e.event_id = a.event_id
        WHERE  a.artifact_id = ?
    """, (artifact_id,)).fetchone()

    if not artifact:
        return render_template("archive.html", page="404"), 404

    # Circle of Evidence: all actors involved in the parent event
    circle_of_evidence = []
    if artifact["linked_event_id"]:
        circle_of_evidence = db.execute("""
            SELECT ac.actor_id, ac.name, ac.type, ac.description,
                   ae.role
            FROM   actor_events ae
            JOIN   actors ac ON ac.actor_id = ae.actor_id
            WHERE  ae.event_id = ?
            ORDER  BY ac.type, ac.name
        """, (artifact["linked_event_id"],)).fetchall()

    # Sibling artifacts: other artifacts in the same event
    siblings = []
    if artifact["linked_event_id"]:
        siblings = db.execute("""
            SELECT artifact_id, title, type, source, date, thumbnail
            FROM   artifacts
            WHERE  event_id = ?
              AND  artifact_id != ?
            ORDER  BY date
        """, (artifact["linked_event_id"], artifact_id)).fetchall()

    # Tag-based related artifacts (shares at least one tag, different event)
    tag_related = []
    if artifact["tags"]:
        tags = [t.strip() for t in artifact["tags"].split(",") if t.strip()]
        if tags:
            # Build LIKE conditions for each tag
            like_clauses = " OR ".join(
                ["a.tags LIKE ?"] * len(tags)
            )
            like_params  = [f"%{t}%" for t in tags]
            tag_related  = db.execute(f"""
                SELECT DISTINCT a.artifact_id, a.title, a.type,
                                a.source, a.date,
                                e.title AS event_title, e.event_id
                FROM   artifacts a
                LEFT   JOIN events e ON e.event_id = a.event_id
                WHERE  ({like_clauses})
                  AND  a.artifact_id != ?
                  AND  (a.event_id != ? OR a.event_id IS NULL)
                ORDER  BY a.date DESC
                LIMIT  6
            """, (*like_params, artifact_id,
                  artifact["linked_event_id"] or -1)).fetchall()

    # Phase 20: forensic context
    forensic_exif = {}
    if artifact["exif_json"]:
        try:
            import json as _fj; forensic_exif = _fj.loads(artifact["exif_json"])
        except Exception: pass
    duplicates = []
    if artifact["file_hash_sha256"]:
        try:
            duplicates = db.execute(
                "SELECT a.artifact_id, a.title, a.type, a.source, "
                "a.date, a.thumbnail, ad.detected_at "
                "FROM artifact_duplicates ad "
                "JOIN artifacts a ON a.artifact_id = ad.duplicate_of_id "
                "WHERE ad.artifact_id = ? "
                "UNION "
                "SELECT a.artifact_id, a.title, a.type, a.source, "
                "a.date, a.thumbnail, ad.detected_at "
                "FROM artifact_duplicates ad "
                "JOIN artifacts a ON a.artifact_id = ad.artifact_id "
                "WHERE ad.duplicate_of_id = ? AND ad.artifact_id != ? "
                "ORDER BY detected_at DESC",
                (artifact_id, artifact_id, artifact_id),
            ).fetchall()
        except Exception: pass

    return render_template(
        "asset.html",
        artifact=artifact,
        circle_of_evidence=circle_of_evidence,
        siblings=siblings,
        tag_related=tag_related,
        forensic_exif=forensic_exif,
        duplicates=duplicates,
    )


# ---------------------------------------------------------------------------
# Phase 20/21/22: Forensic + Graph + Relationship API routes
# ---------------------------------------------------------------------------

@admin_bp.route("/api/artifacts/<int:artifact_id>/process", methods=["POST"])
def api_artifact_process(artifact_id: int):
    from flask import jsonify
    db  = get_db()
    row = db.execute(
        "SELECT artifact_id, type, raw_text_cache, title, description "
        "FROM artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    raw_text = (row["raw_text_cache"] if "raw_text_cache" in row.keys()
                else None) or row["description"] or ""
    if not raw_text.strip():
        try:
            db.execute("UPDATE artifacts SET processing_status='skipped' "
                       "WHERE artifact_id=?", (artifact_id,))
            db.commit()
        except Exception:
            pass
        return jsonify({"status": "skipped", "entities": 0})
    try:
        from forage.processors.artifact_processor import ProcessorManager
        pm     = ProcessorManager(db_path=DB_PATH)
        result = pm.process_artifact(artifact_id=artifact_id, raw_text=raw_text,
                                     artifact_type=row["type"])
        try:
            db.execute("UPDATE artifacts SET processing_status='done' "
                       "WHERE artifact_id=?", (artifact_id,))
            db.commit()
        except Exception:
            pass
        return jsonify({"status": "done", "entities": result.get("entities", 0)})
    except Exception as exc:
        try:
            db.execute("UPDATE artifacts SET processing_status='failed' "
                       "WHERE artifact_id=?", (artifact_id,))
            db.commit()
        except Exception:
            pass
        return jsonify({"status": "failed", "error": str(exc)}), 500


@admin_bp.route("/api/artifacts/<int:artifact_id>/signal", methods=["POST"])
def api_artifact_to_signal(artifact_id: int):
    from flask import jsonify
    import json as _json
    db  = get_db()
    row = db.execute("SELECT * FROM artifacts WHERE artifact_id=?",
                     (artifact_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    body       = request.get_json(silent=True) or {}
    title      = body.get("title") or row["title"]
    content    = body.get("content") or row["description"] or ""
    lat        = body.get("lat")  or row["latitude"]
    lng        = body.get("lng")  or row["longitude"]
    is_priority= int(body.get("is_priority", 0))
    ext_id     = f"artifact:{artifact_id}:{(row['title'] or '')[:40]}"
    existing   = db.execute("SELECT signal_id FROM signals WHERE external_id=?",
                            (ext_id,)).fetchone()
    if existing:
        return jsonify({"status": "exists", "signal_id": existing["signal_id"]})
    sid = str(__import__("uuid").uuid4())
    try:
        db.execute("""
            INSERT INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, is_priority, source_artifact_id, source_type)
            VALUES (?,?,?,?,?,?,?,datetime('now'),'raw',?,?, 'live')
        """, (sid, row["source"] or "artifact", ext_id, title, content[:1000],
              float(lat) if lat else None, float(lng) if lng else None,
              is_priority, artifact_id))
    except Exception:
        # source_artifact_id column may not exist yet pre-migration
        db.execute("""
            INSERT INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, is_priority, source_type)
            VALUES (?,?,?,?,?,?,?,datetime('now'),'raw',?,'live')
        """, (sid, row["source"] or "artifact", ext_id, title, content[:1000],
              float(lat) if lat else None, float(lng) if lng else None, is_priority))
    db.commit()
    return jsonify({"status": "created", "signal_id": sid})


@admin_bp.route("/api/artifacts/<int:artifact_id>/forensic")
def api_artifact_forensic(artifact_id: int):
    from flask import jsonify
    import json as _fj
    db  = get_db()
    row = db.execute(
        "SELECT artifact_id, title, file_path, file_hash_sha256, file_hash_md5, "
        "file_size_bytes, exif_json, gps_lat, gps_lng, "
        "device_make, device_model, exif_datetime "
        "FROM artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if not row: return jsonify({"error": "Not found"}), 404
    exif = {}
    if row["exif_json"]:
        try: exif = _fj.loads(row["exif_json"])
        except Exception: pass
    try:
        dups = db.execute(
            "SELECT a.artifact_id, a.title, a.type, ad.detected_at "
            "FROM artifact_duplicates ad "
            "JOIN artifacts a ON a.artifact_id = ad.duplicate_of_id "
            "WHERE ad.artifact_id = ? "
            "UNION "
            "SELECT a.artifact_id, a.title, a.type, ad.detected_at "
            "FROM artifact_duplicates ad "
            "JOIN artifacts a ON a.artifact_id = ad.artifact_id "
            "WHERE ad.duplicate_of_id = ? AND ad.artifact_id != ?",
            (artifact_id, artifact_id, artifact_id)
        ).fetchall()
        duplicates = [dict(d) for d in dups]
    except Exception:
        duplicates = []
    return jsonify({
        "artifact_id": artifact_id, "title": row["title"],
        "hashes": {"sha256": row["file_hash_sha256"], "md5": row["file_hash_md5"]},
        "file_size_bytes": row["file_size_bytes"], "exif": exif,
        "gps": {"lat": row["gps_lat"], "lng": row["gps_lng"]} if row["gps_lat"] else None,
        "device": {"make": row["device_make"], "model": row["device_model"]} if row["device_make"] else None,
        "exif_datetime": row["exif_datetime"],
        "duplicates": duplicates, "duplicate_count": len(duplicates),
    })


@admin_bp.route("/api/artifacts/<int:artifact_id>/forensic-process", methods=["POST"])
def api_artifact_forensic_process(artifact_id: int):
    from flask import jsonify
    db  = get_db()
    row = db.execute(
        "SELECT artifact_id, file_path, latitude FROM artifacts WHERE artifact_id=?",
        (artifact_id,)
    ).fetchone()
    if not row: return jsonify({"error": "Not found"}), 404
    try:
        from forage.processors.forensic_processor import ForensicProcessor
        fp = ForensicProcessor(db_path=DB_PATH)
        result = fp.process_artifact(row)
        fp.close()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/artifacts/duplicates")
def api_artifact_duplicates():
    from flask import jsonify
    db = get_db()
    try:
        rows = db.execute(
            "SELECT ad.hash_sha256, "
            "COUNT(DISTINCT ad.artifact_id)+COUNT(DISTINCT ad.duplicate_of_id) AS total_copies, "
            "MIN(ad.detected_at) AS first_detected "
            "FROM artifact_duplicates ad GROUP BY ad.hash_sha256 ORDER BY total_copies DESC"
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"groups": [dict(r) for r in rows], "total": len(rows)})


# ---------------------------------------------------------------------------
# Correlation API routes
# ---------------------------------------------------------------------------

@admin_bp.route("/api/correlations/geojson")
def api_correlations_geojson():
    """
    Returns correlated pairs as GeoJSON LineString features.
    Each feature is a line connecting signal_a to signal_b.
    Properties carry correlation_score, distance_km, time_diff.
    Used by map.html to draw L.polyline connections.
    """
    import json as _j
    from flask import Response
    db = get_db()
    try:
        rows = db.execute(
            "SELECT ci.correlation_score, ci.distance_km, "
            "ci.time_difference_hours, ci.detected_at, "
            "sa.title AS title_a, sa.source AS src_a, "
            "sa.lat AS lat_a, sa.lng AS lng_a, "
            "sb.title AS title_b, sb.source AS src_b, "
            "sb.lat AS lat_b, sb.lng AS lng_b "
            "FROM correlated_incidents ci "
            "JOIN signals sa ON sa.signal_id = ci.signal_a "
            "JOIN signals sb ON sb.signal_id = ci.signal_b "
            "WHERE ci.correlation_score >= 0.7 "
            "ORDER BY ci.correlation_score DESC LIMIT 200"
        ).fetchall()
    except Exception:
        rows = []
    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(r["lng_a"], 6), round(r["lat_a"], 6)],
                    [round(r["lng_b"], 6), round(r["lat_b"], 6)],
                ],
            },
            "properties": {
                "score":     r["correlation_score"],
                "dist_km":   r["distance_km"],
                "time_diff": r["time_difference_hours"],
                "title_a":   r["title_a"] or "",
                "src_a":     r["src_a"]   or "",
                "title_b":   r["title_b"] or "",
                "src_b":     r["src_b"]   or "",
                "detected_at": r["detected_at"] or "",
            },
        })
    return Response(
        _j.dumps({"type": "FeatureCollection", "features": features},
                 ensure_ascii=False),
        mimetype="application/geo+json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@admin_bp.route("/api/correlations/recalculate", methods=["POST"])
def api_correlations_recalculate():
    from flask import jsonify
    try:
        from forage.engines.correlation_engine import CorrelationEngine
        result = CorrelationEngine(db_path=DB_PATH).run()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Phase 25: Sentinel API routes
# ---------------------------------------------------------------------------

@admin_bp.route("/api/alerts")
def api_alerts():
    """Return new sentinel alerts, optionally filtered by type."""
    from flask import jsonify
    db     = get_db()
    status = request.args.get("status", "new")
    limit  = min(request.args.get("limit", 50, type=int), 200)
    try:
        rows = db.execute(
            "SELECT id, alert_type, confidence_score, signal_count, "
            "summary, location_lat, location_lon, status, created_at "
            "FROM sentinel_alerts WHERE status = ? "
            "ORDER BY confidence_score DESC, created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    except Exception:
        rows = []
    return jsonify({"alerts": [dict(r) for r in rows], "total": len(rows)})


@admin_bp.route("/api/alerts/<int:alert_id>/acknowledge", methods=["POST"])
def api_alert_acknowledge(alert_id: int):
    from flask import jsonify
    db = get_db()
    try:
        db.execute(
            "UPDATE sentinel_alerts SET status='acknowledged' WHERE id=?",
            (alert_id,)
        )
        db.commit()
        return jsonify({"status": "acknowledged", "id": alert_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/alerts/<int:alert_id>/dismiss", methods=["POST"])
def api_alert_dismiss(alert_id: int):
    from flask import jsonify
    db = get_db()
    try:
        db.execute(
            "UPDATE sentinel_alerts SET status='dismissed' WHERE id=?",
            (alert_id,)
        )
        db.commit()
        return jsonify({"status": "dismissed", "id": alert_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/alerts/<int:alert_id>/promote-case", methods=["POST"])
def api_alert_promote_case(alert_id: int):
    """Promote a sentinel alert to a new Case workspace."""
    from flask import jsonify, request as req
    db   = get_db()
    alert = db.execute(
        "SELECT * FROM sentinel_alerts WHERE id=?", (alert_id,)
    ).fetchone()
    if not alert:
        return jsonify({"error": "Alert not found"}), 404
    data       = req.get_json(silent=True) or {}
    case_title = data.get("title") or f"SENTINEL: {alert['alert_type']} ({alert['created_at'][:10]})"
    hypothesis = f"Pattern: {alert['summary'][:200]}"
    try:
        cur = db.execute(
            "INSERT INTO cases (name, description, hypothesis, status, case_type, source_type) "
            "VALUES (?, ?, ?, 'active', 'general', 'live') ",
            (case_title,
             f"Auto-generated from Sentinel alert #{alert_id}. "
             f"Type: {alert['alert_type']} | "
             f"Confidence: {alert['confidence_score']:.0%}",
             hypothesis)
        )
        case_id = cur.lastrowid
        db.execute(
            "UPDATE sentinel_alerts SET status='acknowledged' WHERE id=?",
            (alert_id,)
        )
        db.commit()
        return jsonify({"case_id": case_id, "status": "promoted"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_bp.route("/api/sentinel/run", methods=["POST"])
def api_sentinel_run():
    """Trigger a Sentinel analysis run. Option B: on-demand."""
    from flask import jsonify
    try:
        from forage.processors.sentinel import Sentinel
        result = Sentinel(db_path=DB_PATH).run()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Event detail — Phase 4
# ---------------------------------------------------------------------------

@admin_bp.route("/event/<int:event_id>")
def event_detail(event_id: int):
    db  = get_db()

    event = db.execute(
        "SELECT * FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if not event:
        return render_template("archive.html", page="404"), 404

    # All artifacts for this event, with source metadata
    artifacts = db.execute("""
        SELECT artifact_id, title, type, source, date, thumbnail, description
        FROM   artifacts
        WHERE  event_id = ?
        ORDER  BY date ASC, type
    """, (event_id,)).fetchall()

    # All actors with their role in this specific event
    actors = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type, ac.description,
               ae.role
        FROM   actor_events ae
        JOIN   actors ac ON ac.actor_id = ae.actor_id
        WHERE  ae.event_id = ?
        ORDER  BY ac.type, ac.name
    """, (event_id,)).fetchall()

    # Source breakdown for this event's evidence
    source_breakdown = db.execute("""
        SELECT source, COUNT(*) AS cnt
        FROM   artifacts
        WHERE  event_id = ? AND source IS NOT NULL
        GROUP  BY source
        ORDER  BY cnt DESC
    """, (event_id,)).fetchall()

    # Related events: share at least one actor (narrative chain)
    related_by_actor = db.execute("""
        SELECT DISTINCT e.event_id, e.title, e.date, e.category,
                        e.location,
                        COUNT(DISTINCT ae2.actor_id) AS shared_actors
        FROM   actor_events ae1
        JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                               AND ae2.event_id  != ae1.event_id
        JOIN   events e ON e.event_id = ae2.event_id
        WHERE  ae1.event_id = ?
        GROUP  BY e.event_id
        ORDER  BY shared_actors DESC, e.date
        LIMIT  5
    """, (event_id,)).fetchall()

    # Related events: share tags (category-level narrative proximity)
    related_by_category = db.execute("""
        SELECT event_id, title, date, category, location
        FROM   events
        WHERE  category = ?
          AND  event_id != ?
        ORDER  BY date
        LIMIT  4
    """, (event["category"], event_id)).fetchall() if event["category"] else []

    # Shared actor details for tooltip context
    # Build a map: event_id -> list of shared actor names
    shared_actor_map: dict[int, list[str]] = {}
    if related_by_actor:
        related_ids = [r["event_id"] for r in related_by_actor]
        actor_names = db.execute(f"""
            SELECT ae2.event_id, ac.name
            FROM   actor_events ae1
            JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                                   AND ae2.event_id IN ({','.join('?'*len(related_ids))})
            JOIN   actors ac ON ac.actor_id = ae1.actor_id
            WHERE  ae1.event_id = ?
            ORDER  BY ae2.event_id, ac.name
        """, (*related_ids, event_id)).fetchall()
        for row in actor_names:
            shared_actor_map.setdefault(row["event_id"], []).append(row["name"])

    return render_template(
        "event.html",
        event=event,
        artifacts=artifacts,
        actors=actors,
        source_breakdown=source_breakdown,
        related_by_actor=related_by_actor,
        related_by_category=related_by_category,
        shared_actor_map=shared_actor_map,
    )


# ---------------------------------------------------------------------------
# Actor detail — Phase 4: Full evidence footprint
# ---------------------------------------------------------------------------

@admin_bp.route("/actor/<int:actor_id>")
def actor_detail(actor_id: int):
    db  = get_db()

    actor = db.execute(
        "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
    ).fetchone()
    if not actor:
        return render_template("archive.html", page="404"), 404

    # All events this actor participated in — manual (actor_events) +
    # automated pipeline links (event_actors), deduplicated by event_id
    events = db.execute("""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               ae.role,
               COUNT(DISTINCT a.artifact_id) AS artifact_count
        FROM   actor_events ae
        JOIN   events e ON e.event_id = ae.event_id
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        WHERE  ae.actor_id = ?
        GROUP  BY e.event_id

        UNION

        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               ea.role,
               COUNT(DISTINCT a.artifact_id) AS artifact_count
        FROM   event_actors ea
        JOIN   events e ON e.event_id = ea.event_id
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        WHERE  ea.actor_id = ?
        GROUP  BY e.event_id

        ORDER  BY 3 ASC
    """, (actor_id, actor_id)).fetchall()

    # Full artifact footprint: all artifacts from every linked event
    artifact_footprint = db.execute("""
        SELECT DISTINCT a.artifact_id, a.title, a.type, a.source,
                        a.date, a.thumbnail, a.description,
                        e.title AS event_title, e.event_id
        FROM   actor_events ae
        JOIN   artifacts a ON a.event_id = ae.event_id
        JOIN   events e    ON e.event_id  = ae.event_id
        WHERE  ae.actor_id = ?
        ORDER  BY a.date ASC
    """, (actor_id,)).fetchall()

    # Co-actors: actors sharing events via both manual and automated links
    co_actors = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type,
               COUNT(DISTINCT e.event_id) AS shared_events,
               GROUP_CONCAT(DISTINCT e.title) AS shared_event_names
        FROM (
            SELECT event_id FROM actor_events WHERE actor_id = ?
            UNION
            SELECT event_id FROM event_actors WHERE actor_id = ?
        ) my_events
        JOIN (
            SELECT event_id, actor_id FROM actor_events
            UNION
            SELECT event_id, actor_id FROM event_actors
        ) all_links ON all_links.event_id = my_events.event_id
                   AND all_links.actor_id != ?
        JOIN actors ac ON ac.actor_id = all_links.actor_id
        JOIN events e  ON e.event_id  = my_events.event_id
        GROUP  BY ac.actor_id
        ORDER  BY shared_events DESC
        LIMIT  8
    """, (actor_id, actor_id, actor_id)).fetchall()

    # Role timeline: each role this actor has held across events
    role_timeline = db.execute("""
        SELECT ae.role, e.event_id, e.title, e.date, e.category
        FROM   actor_events ae
        JOIN   events e ON e.event_id = ae.event_id
        WHERE  ae.actor_id = ?
          AND  ae.role IS NOT NULL
        ORDER  BY e.date
    """, (actor_id,)).fetchall()

    # Phase 21: network metrics
    network_metrics = None
    try:
        network_metrics = db.execute(
            "SELECT betweenness, eigenvector, pagerank, community_id, "
            "node_count, edge_count, computed_at "
            "FROM actor_network_metrics WHERE actor_id=?",
            (actor_id,)
        ).fetchone()
    except Exception: pass
    network_top = []
    try:
        network_top = db.execute(
            "SELECT m.actor_id, a.name, a.type, m.pagerank, m.community_id "
            "FROM actor_network_metrics m "
            "JOIN actors a ON a.actor_id=m.actor_id "
            "ORDER BY m.pagerank DESC LIMIT 10"
        ).fetchall()
    except Exception: pass

    # Phase 22: named relationships for this actor
    relationships = []
    try:
        relationships = db.execute(
            "SELECT r.relationship_id, r.subject_actor_id, r.object_actor_id, "
            "r.relation_type, r.description, r.confidence, r.extraction_method, "
            "a1.name AS subject_name, a2.name AS object_name "
            "FROM entity_relationships r "
            "JOIN actors a1 ON a1.actor_id=r.subject_actor_id "
            "JOIN actors a2 ON a2.actor_id=r.object_actor_id "
            "WHERE r.subject_actor_id=? OR r.object_actor_id=? "
            "ORDER BY r.confidence DESC",
            (actor_id, actor_id)
        ).fetchall()
    except Exception: pass

    # All actors list for the relationship form
    all_actors = []
    try:
        all_actors = db.execute(
            "SELECT actor_id, name, type FROM actors "
            "WHERE actor_id != ? ORDER BY name",
            (actor_id,)
        ).fetchall()
    except Exception: pass

    # Targeting: signals linked via relationship engine
    targeting = {
        "is_targeted": False,
        "signal_count": 0,
        "max_gravity": 0.0,
        "has_priority_signal": False,
        "threat_level": "none",
    }
    try:
        t = db.execute("""
            SELECT COUNT(DISTINCT sa.signal_id)        AS signal_count,
                   MAX(COALESCE(s.gravity_score, 0))   AS max_gravity,
                   MAX(COALESCE(s.is_priority, 0))     AS has_priority_signal
            FROM   signal_actors sa
            JOIN   signals s ON s.signal_id = sa.signal_id
            WHERE  sa.actor_id = ?
        """, (actor_id,)).fetchone()
        if t:
            max_g     = float(t["max_gravity"] or 0)
            has_pri   = bool(t["has_priority_signal"])
            sig_count = int(t["signal_count"] or 0)
            is_targeted = max_g >= 0.55 or has_pri
            if max_g >= 0.75 or has_pri:
                threat_level = "critical"
            elif max_g >= 0.55:
                threat_level = "elevated"
            elif max_g >= 0.35:
                threat_level = "monitored"
            else:
                threat_level = "none"
            targeting = {
                "is_targeted":         is_targeted,
                "signal_count":        sig_count,
                "max_gravity":         round(max_g, 3),
                "has_priority_signal": has_pri,
                "threat_level":        threat_level,
            }
    except Exception: pass

    return render_template(
        "actor.html",
        actor=actor,
        events=events,
        artifact_footprint=artifact_footprint,
        co_actors=co_actors,
        role_timeline=role_timeline,
        network_metrics=network_metrics,
        network_top=network_top,
        relationships=relationships,
        all_actors=all_actors,
        targeting=targeting,
    )


# ---------------------------------------------------------------------------
# Edit / Delete: Artifacts
# ---------------------------------------------------------------------------

@admin_bp.route("/artifact/<int:artifact_id>/edit", methods=["GET", "POST"])
def artifact_edit(artifact_id: int):
    db = get_db()
    artifact = db.execute(
        "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if not artifact:
        flash("Artifact not found.", "error")
        return redirect(url_for("admin.admin"))

    events_list = db.execute(
        "SELECT event_id, title, date FROM events ORDER BY date DESC"
    ).fetchall()

    if request.method == "POST":
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("admin.artifact_edit", artifact_id=artifact_id))

        title       = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip() or None
        atype       = request.form.get("type", artifact["type"])
        date        = request.form.get("date", "").strip() or None
        location    = request.form.get("location", "").strip() or None
        latitude    = request.form.get("latitude", "").strip() or None
        longitude   = request.form.get("longitude", "").strip() or None
        tags        = request.form.get("tags", "").strip() or None
        source      = request.form.get("source", artifact["source"])
        event_id    = request.form.get("event_id", "").strip() or None

        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("admin.artifact_edit", artifact_id=artifact_id))

        db.execute("""
            UPDATE artifacts
            SET title=?, description=?, type=?, date=?, location=?,
                latitude=?, longitude=?, tags=?, source=?, event_id=?
            WHERE artifact_id=?
        """, (
            title, description, atype, date, location,
            float(latitude)  if latitude  else None,
            float(longitude) if longitude else None,
            tags, source,
            int(event_id) if event_id else None,
            artifact_id,
        ))
        db.commit()
        flash(f"Artifact '{title}' updated.", "success")
        return redirect(url_for("admin.artifact_detail", artifact_id=artifact_id))

    return render_template(
        "edit_artifact.html",
        artifact=artifact,
        events=events_list,
    )


@admin_bp.route("/artifact/<int:artifact_id>/delete", methods=["POST"])
def artifact_delete(artifact_id: int):
    if request.form.get("password") != ADMIN_PASSWORD:
        flash("Incorrect password.", "error")
        return redirect(url_for("admin.artifact_detail", artifact_id=artifact_id))
    db = get_db()
    artifact = db.execute(
        "SELECT title, file_path, thumbnail FROM artifacts WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if not artifact:
        flash("Artifact not found.", "error")
        return redirect(url_for("admin.admin"))

    # Optionally delete physical files
    for fpath in (artifact["file_path"], artifact["thumbnail"]):
        if fpath:
            try:
                full = BASE_DIR / fpath
                if full.exists():
                    full.unlink()
            except Exception:
                pass

    db.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
    db.commit()
    flash(f"Artifact '{artifact['title']}' deleted.", "success")
    return redirect(url_for("admin.admin"))


# ---------------------------------------------------------------------------
# Edit / Delete: Events
# ---------------------------------------------------------------------------

@admin_bp.route("/event/<int:event_id>/edit", methods=["GET", "POST"])
def event_edit(event_id: int):
    db = get_db()
    event = db.execute(
        "SELECT * FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("pages.events"))

    event_categories = [
        "Election","Security","Civil Unrest","Legislative",
        "Economic","Diplomatic","Military","Social","Other",
    ]

    if request.method == "POST":
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("admin.event_edit", event_id=event_id))

        title    = request.form.get("title", "").strip()
        summary  = request.form.get("summary", "").strip() or None
        date     = request.form.get("date", "").strip() or None
        location = request.form.get("location", "").strip() or None
        lat      = request.form.get("latitude", "").strip() or None
        lon      = request.form.get("longitude", "").strip() or None
        category = request.form.get("category", event["category"])

        if not title:
            flash("Title is required.", "error")
            return redirect(url_for("admin.event_edit", event_id=event_id))

        db.execute("""
            UPDATE events
            SET title=?, summary=?, date=?, location=?,
                latitude=?, longitude=?, category=?
            WHERE event_id=?
        """, (
            title, summary, date, location,
            float(lat) if lat else None,
            float(lon) if lon else None,
            category, event_id,
        ))
        db.commit()
        flash(f"Event '{title}' updated.", "success")
        return redirect(url_for("admin.event_detail", event_id=event_id))

    return render_template(
        "edit_event.html",
        event=event,
        event_categories=event_categories,
    )


@admin_bp.route("/event/<int:event_id>/delete", methods=["POST"])
def event_delete(event_id: int):
    if request.form.get("password") != ADMIN_PASSWORD:
        flash("Incorrect password.", "error")
        return redirect(url_for("admin.event_detail", event_id=event_id))
    db = get_db()
    event = db.execute(
        "SELECT title FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if not event:
        flash("Event not found.", "error")
        return redirect(url_for("pages.events"))
    db.execute("DELETE FROM events WHERE event_id=?", (event_id,))
    db.commit()
    flash(f"Event '{event['title']}' deleted.", "success")
    return redirect(url_for("pages.events"))


# ---------------------------------------------------------------------------
# Edit / Delete: Actors
# ---------------------------------------------------------------------------

@admin_bp.route("/actor/<int:actor_id>/edit", methods=["GET", "POST"])
def actor_edit(actor_id: int):
    db = get_db()
    actor = db.execute(
        "SELECT * FROM actors WHERE actor_id=?", (actor_id,)
    ).fetchone()
    if not actor:
        flash("Actor not found.", "error")
        return redirect(url_for("pages.actors"))

    actor_types_list = ["person","institution","government","movement",
                        "media","paramilitary","other"]

    if request.method == "POST":
        if request.form.get("password") != ADMIN_PASSWORD:
            flash("Incorrect password.", "error")
            return redirect(url_for("admin.actor_edit", actor_id=actor_id))

        name        = request.form.get("name", "").strip()
        atype       = request.form.get("type", actor["type"])
        description = request.form.get("description", "").strip() or None
        blacklisted = 1 if request.form.get("blacklisted") == "on" else 0
        blacklist_reason = request.form.get("blacklist_reason", "").strip() or None
        image_url = actor["image_url"]

        if request.form.get("remove_image") == "on":
            if image_url:
                old_path = MEDIA_DIR / image_url
                if old_path.exists():
                    old_path.unlink()
            image_url = None

        photo = request.files.get("image_file")
        if photo and photo.filename:
            ext = photo.filename.rsplit(".", 1)[-1].lower() if "." in photo.filename else ""
            if ext not in ACTOR_PHOTO_EXTENSIONS:
                flash("Photo must be PNG, JPG, JPEG, WEBP, or GIF.", "error")
                return redirect(url_for("admin.actor_edit", actor_id=actor_id))
            if image_url:
                old_path = MEDIA_DIR / image_url
                if old_path.exists():
                    old_path.unlink()
            filename = f"{actor_id}.{ext}"
            (MEDIA_DIR / "actors").mkdir(parents=True, exist_ok=True)
            photo.save(str(MEDIA_DIR / "actors" / filename))
            image_url = f"actors/{filename}"

        if not name:
            flash("Name is required.", "error")
            return redirect(url_for("admin.actor_edit", actor_id=actor_id))

        db.execute(
            """
            UPDATE actors
            SET name=?, type=?, description=?, image_url=?,
                blacklisted=?, blacklist_reason=?,
                blacklist_added_at = CASE
                    WHEN ? = 1 AND blacklisted = 0 AND blacklist_added_at IS NULL
                        THEN datetime('now')
                    WHEN ? = 0 THEN NULL
                    ELSE blacklist_added_at
                END
            WHERE actor_id=?
            """,
            (
                name, atype, description, image_url,
                blacklisted, blacklist_reason,
                blacklisted,
                blacklisted,
                actor_id,
            ),
        )
        db.commit()
        flash(f"Actor '{name}' updated.", "success")
        return redirect(url_for("admin.actor_detail", actor_id=actor_id))

    return render_template(
        "edit_actor.html",
        actor=actor,
        actor_types_list=actor_types_list,
    )


@admin_bp.route("/actor/<int:actor_id>/delete", methods=["POST"])
def actor_delete(actor_id: int):
    if request.form.get("password") != ADMIN_PASSWORD:
        flash("Incorrect password.", "error")
        return redirect(url_for("admin.actor_detail", actor_id=actor_id))
    db = get_db()
    actor = db.execute(
        "SELECT name FROM actors WHERE actor_id=?", (actor_id,)
    ).fetchone()
    if not actor:
        flash("Actor not found.", "error")
        return redirect(url_for("pages.actors"))
    db.execute("DELETE FROM actors WHERE actor_id=?", (actor_id,))
    db.commit()
    flash(f"Actor '{actor['name']}' deleted.", "success")
    return redirect(url_for("pages.actors"))


# ---------------------------------------------------------------------------
# Route: /dossier — Phase 7: Print-ready intelligence briefs
# ---------------------------------------------------------------------------

def _dossier_actor_data(db, actor_id: int):
    """
    Shared query function used by both the dossier route and any future
    PDF-export route.  Returns (actor_row, context_dict) or (None, {}).
    """
    actor = db.execute(
        "SELECT * FROM actors WHERE actor_id = ?", (actor_id,)
    ).fetchone()
    if not actor:
        return None, {}

    events = db.execute("""
        SELECT e.event_id, e.title, e.date, e.category,
               e.location, e.summary,
               ae.role,
               COUNT(a.artifact_id) AS artifact_count
        FROM   actor_events ae
        JOIN   events e ON e.event_id = ae.event_id
        LEFT   JOIN artifacts a ON a.event_id = e.event_id
        WHERE  ae.actor_id = ?
        GROUP  BY e.event_id
        ORDER  BY e.date ASC
    """, (actor_id,)).fetchall()

    artifact_footprint = db.execute("""
        SELECT DISTINCT a.artifact_id, a.title, a.type, a.source,
                        a.date, a.thumbnail, a.description,
                        e.title AS event_title, e.event_id
        FROM   actor_events ae
        JOIN   artifacts a ON a.event_id = ae.event_id
        JOIN   events e    ON e.event_id  = ae.event_id
        WHERE  ae.actor_id = ?
        ORDER  BY a.date ASC
    """, (actor_id,)).fetchall()

    co_actors = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type,
               COUNT(DISTINCT ae2.event_id) AS shared_events
        FROM   actor_events ae1
        JOIN   actor_events ae2 ON ae2.event_id  = ae1.event_id
                               AND ae2.actor_id != ae1.actor_id
        JOIN   actors ac ON ac.actor_id = ae2.actor_id
        WHERE  ae1.actor_id = ?
        GROUP  BY ac.actor_id
        ORDER  BY shared_events DESC
        LIMIT  12
    """, (actor_id,)).fetchall()

    role_timeline = db.execute("""
        SELECT ae.role, e.event_id, e.title, e.date, e.category
        FROM   actor_events ae
        JOIN   events e ON e.event_id = ae.event_id
        WHERE  ae.actor_id = ?
          AND  ae.role IS NOT NULL
        ORDER  BY e.date
    """, (actor_id,)).fetchall()

    return actor, {
        "events":             events,
        "artifact_footprint": artifact_footprint,
        "co_actors":          co_actors,
        "role_timeline":      role_timeline,
    }


def _dossier_event_data(db, event_id: int):
    """
    Shared query function for event dossiers.
    Returns (event_row, context_dict) or (None, {}).
    """
    event = db.execute(
        "SELECT * FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if not event:
        return None, {}

    artifacts = db.execute("""
        SELECT artifact_id, title, type, source, date, thumbnail, description
        FROM   artifacts
        WHERE  event_id = ?
        ORDER  BY date ASC, type
    """, (event_id,)).fetchall()

    actors = db.execute("""
        SELECT ac.actor_id, ac.name, ac.type, ac.description, ae.role
        FROM   actor_events ae
        JOIN   actors ac ON ac.actor_id = ae.actor_id
        WHERE  ae.event_id = ?
        ORDER  BY ac.type, ac.name
    """, (event_id,)).fetchall()

    source_breakdown = db.execute("""
        SELECT source, COUNT(*) AS cnt
        FROM   artifacts
        WHERE  event_id = ? AND source IS NOT NULL
        GROUP  BY source
        ORDER  BY cnt DESC
    """, (event_id,)).fetchall()

    related_by_actor = db.execute("""
        SELECT DISTINCT e.event_id, e.title, e.date, e.category,
                        e.location,
                        COUNT(DISTINCT ae2.actor_id) AS shared_actors
        FROM   actor_events ae1
        JOIN   actor_events ae2 ON ae2.actor_id = ae1.actor_id
                               AND ae2.event_id  != ae1.event_id
        JOIN   events e ON e.event_id = ae2.event_id
        WHERE  ae1.event_id = ?
        GROUP  BY e.event_id
        ORDER  BY shared_actors DESC, e.date
        LIMIT  5
    """, (event_id,)).fetchall()

    return event, {
        "artifacts":         artifacts,
        "actors":            actors,
        "source_breakdown":  source_breakdown,
        "related_by_actor":  related_by_actor,
    }


@admin_bp.route("/dossier/actor/<int:actor_id>")
def dossier_actor(actor_id: int):
    db = get_db()
    actor, ctx = _dossier_actor_data(db, actor_id)
    if not actor:
        return render_template("archive.html", page="404"), 404

    import datetime
    return render_template(
        "dossier.html",
        subject_type="actor",
        subject=actor,
        generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        back_url=url_for("admin.actor_detail", actor_id=actor_id),
        **ctx,
    )


@admin_bp.route("/dossier/event/<int:event_id>")
def dossier_event(event_id: int):
    db = get_db()
    event, ctx = _dossier_event_data(db, event_id)
    if not event:
        return render_template("archive.html", page="404"), 404

    import datetime
    return render_template(
        "dossier.html",
        subject_type="event",
        subject=event,
        generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        back_url=url_for("admin.event_detail", event_id=event_id),
        **ctx,
    )


# ---------------------------------------------------------------------------
# Route: /document — Arkadia-style rich document briefs (case-independent)
# ---------------------------------------------------------------------------

# Palette rotated across chart slices / entity cards
_DOC_PALETTE = [
    ("rgba(6,182,212,0.75)",   "#06b6d4"),   # cyan
    ("rgba(217,70,239,0.75)",  "#d946ef"),   # fuchsia
    ("rgba(139,92,246,0.75)",  "#8b5cf6"),   # violet
    ("rgba(245,158,11,0.75)",  "#f59e0b"),   # amber
    ("rgba(16,185,129,0.75)",  "#10b981"),   # emerald
    ("rgba(244,63,94,0.75)",   "#f43f5e"),   # rose
    ("rgba(59,130,246,0.75)",  "#3b82f6"),   # blue
    ("rgba(249,115,22,0.75)",  "#f97316"),   # orange
]


def _doc_rgba(i: int) -> str:
    return _DOC_PALETTE[i % len(_DOC_PALETTE)][0]


def _doc_hex(i: int) -> str:
    return _DOC_PALETTE[i % len(_DOC_PALETTE)][1]


def _build_actor_document(actor, ctx: dict) -> dict:
    """Convert _dossier_actor_data output into document_brief template context."""
    import collections, datetime as _dt

    events           = ctx.get("events", [])
    artifact_footprint = ctx.get("artifact_footprint", [])
    co_actors        = ctx.get("co_actors", [])
    role_timeline    = ctx.get("role_timeline", [])

    # -- Stats
    role_counts: dict = collections.Counter(r["role"] or "unspecified" for r in role_timeline)
    art_type_counts: dict = collections.Counter(
        (a["type"] or "unknown") for a in artifact_footprint
    )
    stats = [
        {"value": len(events),            "label": "Events Linked",    "sublabel": "Verified appearances",  "color": "#06b6d4"},
        {"value": len(artifact_footprint), "label": "Evidence Items",   "sublabel": "Artifact footprint",    "color": "#d946ef"},
        {"value": len(co_actors),          "label": "Co-Actors",        "sublabel": "Network connections",   "color": "#8b5cf6"},
        {"value": len(role_counts),        "label": "Distinct Roles",   "sublabel": "Operational profile",   "color": "#f59e0b"},
    ]

    # -- Primary section
    top_roles = sorted(role_counts.items(), key=lambda x: -x[1])[:8]
    role_tags = [
        {"label": role.title(), "value": f"{cnt} event{'s' if cnt != 1 else ''}", "color": _doc_hex(i)}
        for i, (role, cnt) in enumerate(top_roles)
    ]
    primary_section = {
        "title": "Activity & Role Profile",
        "body": (
            f"This actor has been identified across {len(events)} event{'s' if len(events) != 1 else ''} "
            f"in the intelligence corpus, accumulating {len(artifact_footprint)} linked evidence items. "
            f"Role distribution across those events reveals {len(role_counts)} distinct operational "
            f"context{'s' if len(role_counts) != 1 else ''}, providing a functional profile of how "
            f"this entity operates within documented incidents."
        ),
        "tags": role_tags,
    }

    # -- Primary chart: role distribution (polar area)
    if role_counts:
        labels_r, data_r = zip(*sorted(role_counts.items(), key=lambda x: -x[1]))
    else:
        labels_r, data_r = (["No roles recorded"],), ([1],)
    primary_chart = {
        "labels":        list(labels_r),
        "data":          list(data_r),
        "colors":        [_doc_rgba(i) for i in range(len(labels_r))],
        "dataset_label": "Events per Role",
    }

    # -- Flow section
    flow_section = {
        "title": "Intelligence Pipeline",
        "body": (
            "Raw signals ingested by FORGE collectors are scored, enriched via NER extraction, "
            "and materialised into the actor registry. Event linkages and artifact evidence form "
            "the dossier substrate below."
        ),
        "nodes": [
            {"label": "Raw Signals",    "sublabel": "Collector ingest",   "color_start": "#334155", "color_end": "#475569"},
            {"label": "Actor Identified","sublabel": "Confidence ≥ 0.2",  "color_start": "#0e7490", "color_end": "#1d4ed8"},
            {"label": "Event Network",  "sublabel": "Participation graph","color_start": "#c2410c", "color_end": "#d97706"},
            {"label": "Evidence Trail", "sublabel": "Artifact footprint", "color_start": "#a21caf", "color_end": "#7c3aed"},
            {"label": "Association Map","sublabel": "Co-actor linkage",   "color_start": "#065f46", "color_end": "#0f766e"},
        ],
        "cards": [
            {
                "color": "#06b6d4",
                "title": "Event Involvement (Gravity-Ranked)",
                "body": (
                    f"{len(events)} events linked via actor_events table. Roles include: "
                    f"{', '.join(list(role_counts.keys())[:5]) or 'none recorded'}. "
                    "Each event carries an independent gravity score from the ingest pipeline."
                ),
            },
            {
                "color": "#d946ef",
                "title": "Evidence Footprint",
                "body": (
                    f"{len(artifact_footprint)} evidence items across "
                    f"{len(art_type_counts)} type{'s' if len(art_type_counts) != 1 else ''}: "
                    f"{', '.join(list(art_type_counts.keys())[:5]) or 'none'}. "
                    "Artifacts are attached via event linkage, not direct actor assignment."
                ),
            },
        ],
    }

    # -- Entity grid: top events
    card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
    event_cards = []
    for i, ev in enumerate(events[:6]):
        summary = (ev["summary"] or "")[:160]
        if len(ev["summary"] or "") > 160:
            summary += "…"
        event_cards.append({
            "color":   card_colors[i % len(card_colors)],
            "eyebrow": f"{ev['category'] or 'EVENT'} · {ev['date'] or '—'}",
            "title":   ev["title"] or "Untitled Event",
            "body":    summary or "No summary recorded.",
            "meta":    f"ID #{ev['event_id']} · {ev['artifact_count']} artifact{'s' if ev['artifact_count'] != 1 else ''} · Role: {ev['role'] or 'unspecified'}",
            "wide":    False,
        })
    if not event_cards:
        event_cards.append({
            "color": "#475569", "eyebrow": "NO DATA",
            "title": "No Events Linked",
            "body":  "This actor has not yet been associated with any events in the corpus.",
            "meta":  "", "wide": False,
        })

    entity_section = {
        "title":    "Event Timeline",
        "subtitle": "Documented events in which this actor has been identified, ordered chronologically.",
        "cards":    event_cards,
    }

    # -- Secondary chart: artifact type distribution (doughnut)
    if art_type_counts:
        labels_a, data_a = zip(*sorted(art_type_counts.items(), key=lambda x: -x[1]))
        secondary_chart: dict | None = {
            "labels":        list(labels_a),
            "data":          list(data_a),
            "colors":        [_doc_rgba(i) for i in range(len(labels_a))],
            "dataset_label": "Artifacts by Type",
        }
    else:
        secondary_chart = None

    top_source_counts: dict = collections.Counter(
        a["source"] for a in artifact_footprint if a["source"]
    )
    top_src = top_source_counts.most_common(1)
    callout_body = (
        f"Primary evidence source: {top_src[0][0]} ({top_src[0][1]} items)."
        if top_src else "No artifact sources recorded."
    )

    secondary_section: dict | None = {
        "title": "Evidence Composition",
        "body": (
            f"The artifact footprint spans {len(art_type_counts)} evidence type{'s' if len(art_type_counts) != 1 else ''}. "
            "Distribution across categories reveals collection patterns and potential intelligence gaps."
        ),
        "callout": {
            "title": "Dominant Source",
            "body":  callout_body,
        },
    } if artifact_footprint else None

    return dict(
        subject_type="actor",
        doc_title=actor["name"],
        doc_subtitle=actor["description"] or None,
        back_url=url_for("admin.actor_detail", actor_id=actor["actor_id"]),
        stats=stats,
        primary_section=primary_section,
        primary_chart=primary_chart,
        flow_section=flow_section,
        entity_section=entity_section,
        secondary_section=secondary_section,
        secondary_chart=secondary_chart,
    )


def _build_event_document(event, ctx: dict) -> dict:
    """Convert _dossier_event_data output into document_brief template context."""
    import collections

    artifacts       = ctx.get("artifacts", [])
    actors          = ctx.get("actors", [])
    source_breakdown= ctx.get("source_breakdown", [])
    related_by_actor= ctx.get("related_by_actor", [])

    actor_type_counts: dict = collections.Counter(
        (a["type"] or "unknown") for a in actors
    )

    # -- Stats
    stats = [
        {"value": len(artifacts),        "label": "Evidence Items",  "sublabel": "Artifact records",     "color": "#06b6d4"},
        {"value": len(actors),            "label": "Actors Linked",   "sublabel": "Identified parties",   "color": "#d946ef"},
        {"value": len(source_breakdown),  "label": "Source Feeds",    "sublabel": "Distinct origins",     "color": "#f59e0b"},
        {"value": len(related_by_actor),  "label": "Related Events",  "sublabel": "Via shared actors",    "color": "#10b981"},
    ]

    # -- Primary section
    actor_tags = [
        {"label": a["name"], "value": f"{a['type'] or 'unknown'} · {a['role'] or 'no role'}", "color": _doc_hex(i)}
        for i, a in enumerate(actors[:8])
    ]
    primary_section = {
        "title": "Actor Involvement",
        "body": (
            f"{len(actors)} actor{'s' if len(actors) != 1 else ''} identified in connection with this event, "
            f"spanning {len(actor_type_counts)} entity type{'s' if len(actor_type_counts) != 1 else ''}. "
            f"The event is documented by {len(artifacts)} evidence item{'s' if len(artifacts) != 1 else ''} "
            f"drawn from {len(source_breakdown)} source{'s' if len(source_breakdown) != 1 else ''}."
        ),
        "tags": actor_tags,
    }

    # -- Primary chart: source distribution (polar area)
    if source_breakdown:
        src_labels = [row["source"] for row in source_breakdown[:8]]
        src_data   = [row["cnt"]    for row in source_breakdown[:8]]
    else:
        src_labels, src_data = ["No sources"], [1]
    primary_chart = {
        "labels":        src_labels,
        "data":          src_data,
        "colors":        [_doc_rgba(i) for i in range(len(src_labels))],
        "dataset_label": "Artifacts per Source",
    }

    # -- Flow section
    flow_section = {
        "title": "Evidence Chain",
        "body": (
            "Open-source collectors feed raw signals that are grouped into this event record "
            "by the FORGE event constructor. Actors are materialised via NER; artifacts are "
            "attached directly. Related events surface through shared actor traversal."
        ),
        "nodes": [
            {"label": "Intel Sources",  "sublabel": "OSINT collectors",  "color_start": "#334155", "color_end": "#475569"},
            {"label": "Event Record",   "sublabel": "Canonical entry",   "color_start": "#0e7490", "color_end": "#1d4ed8"},
            {"label": "Actor Web",      "sublabel": f"{len(actors)} parties",  "color_start": "#c2410c", "color_end": "#d97706"},
            {"label": "Artifacts",      "sublabel": f"{len(artifacts)} items", "color_start": "#a21caf", "color_end": "#7c3aed"},
            {"label": "Cross-Reference","sublabel": f"{len(related_by_actor)} related", "color_start": "#065f46", "color_end": "#0f766e"},
        ],
        "cards": [
            {
                "color": "#f59e0b",
                "title": "Source Distribution",
                "body": (
                    f"Top source{'s' if len(source_breakdown) > 1 else ''}: "
                    + ", ".join(f"{r['source']} ({r['cnt']})" for r in source_breakdown[:4])
                    + "." if source_breakdown else "No source data recorded."
                ),
            },
            {
                "color": "#8b5cf6",
                "title": "Actor Type Breakdown",
                "body": (
                    "; ".join(f"{t.title()}: {c}" for t, c in actor_type_counts.most_common(5))
                    or "No actors recorded."
                ),
            },
        ],
    }

    # -- Entity grid: artifacts
    card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
    artifact_cards = []
    for i, art in enumerate(artifacts[:6]):
        body = (art["description"] or "")[:160]
        if len(art["description"] or "") > 160:
            body += "…"
        artifact_cards.append({
            "color":   card_colors[i % len(card_colors)],
            "eyebrow": f"{art['type'] or 'ARTIFACT'} · {art['source'] or '—'}",
            "title":   art["title"] or "Untitled Artifact",
            "body":    body or "No description recorded.",
            "meta":    f"ID #{art['artifact_id']} · {art['date'] or 'No date'}",
            "wide":    False,
        })
    if not artifact_cards:
        artifact_cards.append({
            "color": "#475569", "eyebrow": "NO DATA",
            "title": "No Artifacts Recorded",
            "body":  "No evidence items have been attached to this event.",
            "meta":  "", "wide": False,
        })

    entity_section = {
        "title":    "Evidence Inventory",
        "subtitle": "Artifacts and source documents linked directly to this event record.",
        "cards":    artifact_cards,
    }

    # -- Secondary chart: actor type distribution (doughnut)
    secondary_chart: dict | None = None
    if actor_type_counts:
        labels_at, data_at = zip(*sorted(actor_type_counts.items(), key=lambda x: -x[1]))
        secondary_chart = {
            "labels":        list(labels_at),
            "data":          list(data_at),
            "colors":        [_doc_rgba(i) for i in range(len(labels_at))],
            "dataset_label": "Actors by Type",
        }

    secondary_section: dict | None = None
    if actors:
        dominant = actor_type_counts.most_common(1)
        secondary_section = {
            "title": "Actor Type Analysis",
            "body": (
                f"The {len(actors)} actor{'s' if len(actors) != 1 else ''} linked to this event span "
                f"{len(actor_type_counts)} entity classification{'s' if len(actor_type_counts) != 1 else ''}. "
                "The doughnut chart reveals the organisational composition of documented participation."
            ),
            "callout": {
                "title": "Dominant Actor Class",
                "body": (
                    f"{dominant[0][0].title()} entities account for the largest share "
                    f"({dominant[0][1]} of {len(actors)} actors). "
                    "Cross-reference with related events to assess whether this pattern holds network-wide."
                ) if dominant else "Actor types could not be determined.",
            },
        }

    return dict(
        subject_type="event",
        doc_title=event["title"],
        doc_subtitle=event["summary"] or None,
        back_url=url_for("admin.event_detail", event_id=event["event_id"]),
        stats=stats,
        primary_section=primary_section,
        primary_chart=primary_chart,
        flow_section=flow_section,
        entity_section=entity_section,
        secondary_section=secondary_section,
        secondary_chart=secondary_chart,
    )


def _document_signals_data(db, stream: str | None, days: int) -> dict:
    """Query aggregate signal data for a stream brief (no case required)."""
    period_clause = f"datetime('now', '-{days} days')"

    base_where = f"created_at >= {period_clause}"
    if stream:
        base_where += f" AND stream = '{stream}'"

    total = db.execute(
        f"SELECT COUNT(*) AS n FROM signals WHERE {base_where}"
    ).fetchone()["n"]

    high_gravity = db.execute(
        f"SELECT COUNT(*) AS n FROM signals WHERE {base_where} AND gravity_score >= 0.5"
    ).fetchone()["n"]

    stream_dist = db.execute(
        f"SELECT stream, COUNT(*) AS cnt FROM signals WHERE {base_where} "
        f"GROUP BY stream ORDER BY cnt DESC"
    ).fetchall()

    source_dist = db.execute(
        f"SELECT source, COUNT(*) AS cnt FROM signals WHERE {base_where} AND source IS NOT NULL "
        f"GROUP BY source ORDER BY cnt DESC LIMIT 8"
    ).fetchall()

    unique_sources = db.execute(
        f"SELECT COUNT(DISTINCT source) AS n FROM signals WHERE {base_where}"
    ).fetchone()["n"]

    gravity_tiers = db.execute(
        f"""SELECT
            SUM(CASE WHEN gravity_score >= 0.7 THEN 1 ELSE 0 END) AS high,
            SUM(CASE WHEN gravity_score >= 0.35 AND gravity_score < 0.7 THEN 1 ELSE 0 END) AS med,
            SUM(CASE WHEN gravity_score < 0.35 THEN 1 ELSE 0 END) AS low
        FROM signals WHERE {base_where}"""
    ).fetchone()

    top_signals = db.execute(
        f"SELECT signal_id, title, source, stream, gravity_score, created_at, content "
        f"FROM signals WHERE {base_where} "
        f"ORDER BY gravity_score DESC LIMIT 6"
    ).fetchall()

    return {
        "total":         total,
        "high_gravity":  high_gravity,
        "unique_sources":unique_sources,
        "days":          days,
        "stream":        stream,
        "stream_dist":   stream_dist,
        "source_dist":   source_dist,
        "gravity_tiers": gravity_tiers,
        "top_signals":   top_signals,
    }


def _build_signals_document(data: dict) -> dict:
    """Convert _document_signals_data output into document_brief template context."""
    stream  = data["stream"]
    total   = data["total"]
    days    = data["days"]

    doc_title = f"{stream} Stream Brief" if stream else "Signal Intelligence Brief"

    stats = [
        {"value": total,                 "label": "Total Signals",    "sublabel": f"Last {days} days",     "color": "#06b6d4"},
        {"value": data["high_gravity"],  "label": "High Gravity",     "sublabel": "Score ≥ 0.50",          "color": "#f43f5e"},
        {"value": data["unique_sources"],"label": "Unique Sources",   "sublabel": "Distinct origins",      "color": "#8b5cf6"},
        {"value": days,                  "label": "Day Window",       "sublabel": "Collection period",     "color": "#f59e0b"},
    ]

    # Stream distribution body
    stream_lines = ", ".join(
        f"{r['stream'] or 'unclassified'} ({r['cnt']})"
        for r in data["stream_dist"]
    ) or "No stream data."

    primary_section = {
        "title": "Stream Distribution",
        "body": (
            f"{total} signal{'s' if total != 1 else ''} ingested over the past {days} days "
            f"across {len(data['stream_dist'])} stream{'s' if len(data['stream_dist']) != 1 else ''}. "
            f"Breakdown: {stream_lines}. "
            f"{data['high_gravity']} signal{'s' if data['high_gravity'] != 1 else ''} "
            f"scored above the 0.50 gravity threshold, flagging elevated operational significance."
        ),
        "tags": [
            {"label": r["stream"] or "unclassified", "value": f"{r['cnt']} signals", "color": _doc_hex(i)}
            for i, r in enumerate(data["stream_dist"][:8])
        ],
    }

    # Primary chart: stream distribution (polar area)
    if data["stream_dist"]:
        sd_labels = [r["stream"] or "unclassified" for r in data["stream_dist"]]
        sd_data   = [r["cnt"] for r in data["stream_dist"]]
    else:
        sd_labels, sd_data = ["No data"], [1]
    primary_chart = {
        "labels":        sd_labels,
        "data":          sd_data,
        "colors":        [_doc_rgba(i) for i in range(len(sd_labels))],
        "dataset_label": "Signals per Stream",
    }

    # Flow section
    flow_section = {
        "title": "FORGE Ingest Pipeline",
        "body": (
            "Signals originate from open-source collectors and are scored via the gravity engine "
            "(urgency/importance model). NER extraction materialises entities; stream classification "
            "routes signals to the appropriate decay schedule."
        ),
        "nodes": [
            {"label": "Collectors",    "sublabel": "OSINT / FLUX",     "color_start": "#334155", "color_end": "#475569"},
            {"label": "Signal Ingest", "sublabel": "Dedup + normalise","color_start": "#0e7490", "color_end": "#1d4ed8"},
            {"label": "Gravity Score", "sublabel": "0.0 – 1.0",        "color_start": "#c2410c", "color_end": "#d97706"},
            {"label": "Entity Extract","sublabel": "NER / spaCy",      "color_start": "#a21caf", "color_end": "#7c3aed"},
            {"label": "Stream Route",  "sublabel": "Decay scheduling", "color_start": "#065f46", "color_end": "#0f766e"},
        ],
        "cards": [
            {
                "color": "#06b6d4",
                "title": "Top Sources",
                "body": (
                    ", ".join(f"{r['source']} ({r['cnt']})" for r in data["source_dist"][:5])
                    or "No source data available."
                ),
            },
            {
                "color": "#f59e0b",
                "title": "Gravity Tier Breakdown",
                "body": (
                    f"High (≥0.70): {data['gravity_tiers']['high'] or 0} · "
                    f"Medium (0.35–0.70): {data['gravity_tiers']['med'] or 0} · "
                    f"Low (<0.35): {data['gravity_tiers']['low'] or 0}"
                ),
            },
        ],
    }

    # Entity grid: top signals by gravity
    card_colors = ["#06b6d4","#d946ef","#8b5cf6","#f59e0b","#10b981","#f43f5e"]
    sig_cards = []
    for i, sig in enumerate(data["top_signals"]):
        body = (sig["content"] or sig["title"] or "")[:160]
        if len(sig["content"] or sig["title"] or "") > 160:
            body += "…"
        grav = sig["gravity_score"]
        grav_str = f"{grav:.2f}" if grav is not None else "—"
        sig_cards.append({
            "color":   card_colors[i % len(card_colors)],
            "eyebrow": f"{sig['stream'] or 'UNCLASSIFIED'} · Gravity {grav_str}",
            "title":   sig["title"] or f"Signal #{sig['signal_id']}",
            "body":    body or "No content available.",
            "meta":    f"ID #{sig['signal_id']} · Source: {sig['source'] or '—'} · {sig['created_at'] or '—'}",
            "wide":    False,
        })
    if not sig_cards:
        sig_cards.append({
            "color": "#475569", "eyebrow": "NO DATA",
            "title": "No Signals in Window",
            "body":  f"No signals recorded in the past {days} days for the selected filter.",
            "meta":  "", "wide": False,
        })

    entity_section = {
        "title":    "Top Signals by Gravity",
        "subtitle": f"Highest-scoring signals ingested in the last {days} days, ranked by gravity score.",
        "cards":    sig_cards,
    }

    # Secondary chart: gravity tier doughnut
    tiers = data["gravity_tiers"]
    secondary_chart: dict | None = {
        "labels": ["High ≥ 0.70", "Medium 0.35–0.70", "Low < 0.35"],
        "data":   [tiers["high"] or 0, tiers["med"] or 0, tiers["low"] or 0],
        "colors": ["rgba(244,63,94,0.8)", "rgba(245,158,11,0.8)", "rgba(148,163,184,0.6)"],
        "dataset_label": "Signals by Gravity Tier",
    } if total > 0 else None

    secondary_section: dict | None = {
        "title": "Gravity Tier Analysis",
        "body": (
            "Gravity score distribution reflects the operational weight of ingested signals. "
            "High-tier signals trigger ESCALATE flags; medium-tier triggers MONITOR. "
            "Low-tier signals enter passive decay."
        ),
        "callout": {
            "title": "Escalation Threshold",
            "body": (
                f"{data['high_gravity']} of {total} signals ({int(data['high_gravity']/total*100) if total else 0}%) "
                f"scored ≥ 0.50 (ESCALATE boundary). "
                "High concentrations in this tier indicate elevated threat tempo in the selected window."
            ),
        },
    } if total > 0 else None

    return dict(
        subject_type="signals",
        doc_title=doc_title,
        doc_subtitle=f"Aggregate signal intelligence across the last {days} days" + (f" · Stream: {stream}" if stream else ""),
        back_url=url_for("signals"),
        stats=stats,
        primary_section=primary_section,
        primary_chart=primary_chart,
        flow_section=flow_section,
        entity_section=entity_section,
        secondary_section=secondary_section,
        secondary_chart=secondary_chart,
    )


@admin_bp.route("/document/actor/<int:actor_id>")
def document_actor(actor_id: int):
    db = get_db()
    actor, ctx = _dossier_actor_data(db, actor_id)
    if not actor:
        return render_template("archive.html", page="404"), 404
    import datetime
    context = _build_actor_document(actor, ctx)
    context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template("document_brief.html", **context)


@admin_bp.route("/document/event/<int:event_id>")
def document_event(event_id: int):
    db = get_db()
    event, ctx = _dossier_event_data(db, event_id)
    if not event:
        return render_template("archive.html", page="404"), 404
    import datetime
    context = _build_event_document(event, ctx)
    context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template("document_brief.html", **context)


@admin_bp.route("/document/signals")
def document_signals():
    import datetime
    db     = get_db()
    stream = request.args.get("stream")
    try:
        days = max(1, min(int(request.args.get("days", 7)), 90))
    except (TypeError, ValueError):
        days = 7
    data    = _document_signals_data(db, stream or None, days)
    context = _build_signals_document(data)
    context["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return render_template("document_brief.html", **context)
