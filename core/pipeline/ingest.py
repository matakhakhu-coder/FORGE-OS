import html
import logging
import re
import uuid
import hashlib
import datetime
from pathlib import Path
from urllib.parse import unquote_plus

_log = logging.getLogger("forge.ingest")

from PIL import Image
from werkzeug.utils import secure_filename

from core.db.connection import get_connection
from core.conclave.engine import run_conclave
from core.conclave.registry import AnalysisResult
import json

from forage.processors.signal_interpreter import SignalInterpreter
from forage.processors.entity_resolver import EntityResolver
from forage.processors.artifact_processor import ProcessorManager
from forage.processors.event_constructor import EventConstructor
from forage.engines.gravity_engine import score_signal
from forage.engines.case_engine import evaluate_case
from forage.engines.feedback_engine import apply_feedback
from forage.engines.escalation_engine import handle_escalation
from forage.engines.entity_engine import materialize_entities, stitch_entity_cooccurrence
from forage.engines.relationship_engine import link_signal_actors, link_event_actors

# Phase P3: Relationship extractor — imported once at module level per Pipeline Contract Rule 1.1
# (imports inside hot-path functions execute on every signal — O(n) sys.modules lookups)
try:
    from forage.processors.relationship_extractor import extract_from_ingest as _rel_extract
    _REL_EXTRACT_AVAILABLE = True
except ImportError:
    _REL_EXTRACT_AVAILABLE = False

# Phase P1: DB path for heartbeat + enrichment queue — resolved once at module load
import sqlite3 as _sqlite3
_FORGE_DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "database.db")

# Phase P2: Sources whose RSS content is typically stub-length and worth enriching
_ENRICHABLE_SOURCES = frozenset({
    "amabhungane", "dailymaverick_corruption", "dailymaverick",
    "timeslive_corruption", "news24_crime", "hawks_media",
    "groundup", "amabhungane_rss",
})

# Bleach Protocol — strips HTML residue before any downstream engine sees the text.
# Cap of 500 chars inside <> prevents catastrophic backtracking on malformed input.
_HTML_TAG_RE = re.compile(r'<[^>]{0,500}>', re.DOTALL)

# FMS — Forge Module System integration (graceful fallback if not yet installed)
try:
    from core.conclave.context import get_context as _get_fms_context
    from core.conclave.engine import run_conclave_with_modules as _run_conclave_modules
    _FMS_AVAILABLE = True
except ImportError:
    _FMS_AVAILABLE = False

# Keep same root behavior as app.py
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = BASE_DIR / "media"

ALLOWED_EXTENSIONS: dict[str, str] = {
    "jpg": "images", "jpeg": "images", "png": "images",
    "gif": "images", "webp": "images",
    "mp4": "videos", "mov": "videos", "avi": "videos", "mkv": "videos",
    "mp3": "audio",  "wav": "audio",  "ogg": "audio",  "m4a": "audio",
    "pdf": "documents", "doc": "documents", "docx": "documents",
    "txt": "documents", "csv": "documents",
}

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


def sanitize_text(text: str) -> str:
    """
    Stable 1.1 — Centralized text refinery.

    Cleans raw collector text before it enters the signal store.
    Two-pass sanitization:
      1. html.unescape()      — resolves &nbsp; &amp; &lt; &gt; &quot; etc.
      2. unquote_plus()       — decodes %20-style URL fragments that leak from
                                PDF filenames and scrape hrefs into signal text.
      3. Whitespace collapse  — normalizes runs of spaces/tabs left by both passes.

    Safe to call on any string — returns the input unchanged if it contains
    nothing to clean. Used by civic_intel and gdelt collectors at signal
    construction time so all downstream engines (NER, triple_extractor,
    evolution) operate on clean text from the very first ingestion.
    """
    if not text:
        return text
    cleaned = _HTML_TAG_RE.sub(' ', text)        # strip literal tags first
    cleaned = html.unescape(cleaned)             # decode &amp; &lt; etc.
    cleaned = _HTML_TAG_RE.sub(' ', cleaned)     # strip any tags exposed by unescape
    cleaned = unquote_plus(cleaned)              # decode %20-style URL fragments
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned


def get_media_subdir(filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ALLOWED_EXTENSIONS.get(ext)


def process_artifact_upload(file, metadata: dict | None = None) -> dict:
    """Stores an uploaded artifact file and generates a thumbnail for images."""
    if not file or not getattr(file, "filename", None):
        raise ValueError("No uploaded file provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '.{ext}' is not allowed.")

    subdir = ALLOWED_EXTENSIONS[ext]
    safe_name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    dest_dir = MEDIA_DIR / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / safe_name
    file.save(str(dest_path))

    file_path = f"media/{subdir}/{safe_name}"
    thumbnail = None

    if ext in IMAGE_EXTENSIONS:
        try:
            thumb_name = f"thumb_{safe_name}"
            thumb_path = dest_dir / thumb_name
            with Image.open(str(dest_path)) as img:
                img.thumbnail((400, 400))
                img.save(str(thumb_path))
            thumbnail = f"media/{subdir}/{thumb_name}"
        except Exception:
            thumbnail = file_path

    # Ensure connection path check (ensures DB is reachable.)
    conn = get_connection()
    conn.close()

    return {
        "file_path": file_path,
        "thumbnail": thumbnail,
        "extension": ext,
    }


def ingest_signal(signal: dict) -> dict:
    """Main ingestion pipeline orchestration for incoming structured signals."""
    if not isinstance(signal, dict):
        raise ValueError("Signal must be a dict")

    # FMS: fire on_signal hooks before interpretation
    if _FMS_AVAILABLE:
        try:
            _get_fms_context().fire_hook("on_signal", signal)
        except Exception as _e:
            _log.warning("[FMS Hook Error] on_signal: %s", _e)

    interpreted = SignalInterpreter().interpret(signal)

    conn = get_connection()
    conclusion = None
    try:
        resolved_entities = EntityResolver(conn).resolve_actors(interpreted.get("actors", []))

        artifact = ProcessorManager(db_path=None).signal_to_artifact(signal)

        event = EventConstructor().construct(signal, interpreted)

        gravity_signal = score_signal(interpreted, actors=resolved_entities)

        case_result = evaluate_case(gravity_signal, linked_actors=resolved_entities, linked_events=[event] if event else [])

        feedback_result = apply_feedback(gravity_signal, resolved_entities, case_result, conn=conn)

        # Build minimal Conclave result against existing local inference.
        rec = case_result.get("decision", "STORE ONLY")
        if rec == "CREATE CASE":
            rec_tgt = "ESCALATE"
        elif rec == "FLAG MONITOR":
            rec_tgt = "MONITOR"
        else:
            rec_tgt = "IGNORE"

        _core_result = AnalysisResult(
            entities=[x.get("name") for x in resolved_entities if x.get("name")],
            intent=interpreted.get("event_type", "unknown"),
            gravity=float(gravity_signal.get("gravity_score", 0.0)),
            recommendation=rec_tgt,
            confidence=float(feedback_result.get("actor_updates", [{}])[0].get("weight", 0.1) if feedback_result.get("actor_updates") else 0.1),
            provenance={"stage": "autonomous_conclave"},
        )
        # FMS: merge module engine results if available, else use core only
        if _FMS_AVAILABLE:
            conclusion = _run_conclave_modules([_core_result], signal)
        else:
            conclusion = run_conclave([_core_result])

        # 1. Store cognition first
        if signal.get("signal_id"):
            try:
                conn.execute(
                    """
                    UPDATE signals
                    SET gravity_score = ?, processed_at = ?, conclave_meta = ?
                    WHERE signal_id = ?
                    """,
                    (
                        conclusion.gravity,
                        datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
                        json.dumps(conclusion.provenance),
                        signal.get("signal_id"),
                    ),
                )
                conn.commit()
            except Exception as e:
                _log.error("[Conclave Persist Error] signal=%s: %s", signal.get("signal_id", "?"), e)

        # 2. Materialize entities
        actor_ids = []
        try:
            actor_ids = materialize_entities(conclusion, signal.get("signal_id"), conn) or []
        except Exception as e:
            _log.error("[Entity Error] signal=%s: %s", signal.get("signal_id", "?"), e)

        # Phase P3: Auto-extract candidate relationships after entity materialisation
        # Silently swallowed on failure — never blocks ingest.
        if _REL_EXTRACT_AVAILABLE:
            try:
                _rel_extract(
                    signal_id=signal.get("signal_id", ""),
                    title=signal.get("title", ""),
                    content=signal.get("content", ""),
                    conn=conn,
                )
            except Exception as _re:
                _log.debug("[RelExtract Hook] signal=%s: %s", signal.get("signal_id", "?"), _re)

        # CT-1: Contextual Tunneling — if Conclave confidence was too low to
        # materialize new actors (confidence < 0.4), fall back to pre-existing
        # signal→actor links from the NER bridge and backfill runs.
        # This ensures event_actors is populated even when Conclave is sparse.
        if not actor_ids and signal.get("signal_id"):
            try:
                rows = conn.execute(
                    "SELECT actor_id FROM signal_actors WHERE signal_id = ?",
                    (signal.get("signal_id"),)
                ).fetchall()
                actor_ids = [r[0] for r in rows]
            except Exception as e:
                _log.error("[CT-1 Fallback Error] signal=%s: %s", signal.get("signal_id", "?"), e)

        # 2a. Link signal → actors
        if actor_ids:
            try:
                link_signal_actors(signal.get("signal_id"), actor_ids, conn)
            except Exception as e:
                _log.error("[Relationship Error - Signal] signal=%s: %s", signal.get("signal_id", "?"), e)

        # 2b. Stitch NER co-occurrence edges (member_of) into graph_edges.
        # Only fires when signal_entities rows exist for this signal — i.e. the
        # sovereign_pipeline NER batch has already run.  Cold signals that have
        # not yet been through NER are silently skipped; edges are stitched on
        # the next ingest cycle that encounters a signal with entity rows.
        _sid = signal.get("signal_id")
        if _sid and actor_ids:
            try:
                _has_ner = conn.execute(
                    "SELECT 1 FROM signal_entities WHERE signal_id=? LIMIT 1",
                    (_sid,),
                ).fetchone()
                if _has_ner:
                    stitch_entity_cooccurrence(_sid, conn)
            except Exception as e:
                _log.debug("[Stitch Error] signal=%s: %s", _sid, e)

        # 3. Then escalate
        event_id = None
        try:
            event_id = handle_escalation(conclusion, signal.get("signal_id"), conn)
        except Exception as e:
            _log.error("[Escalation Error] signal=%s: %s", signal.get("signal_id", "?"), e)

        # 3a. Link event → actors
        if event_id and actor_ids:
            try:
                link_event_actors(event_id, actor_ids, conn)
            except Exception as e:
                _log.error("[Relationship Error - Event] signal=%s event=%s: %s", signal.get("signal_id", "?"), event_id, e)

        # 4. Patch conclave_meta with actor_ids + event_id for graph_sync
        if signal.get("signal_id") and (actor_ids or event_id):
            try:
                row = conn.execute(
                    "SELECT conclave_meta FROM signals WHERE signal_id=?",
                    (signal.get("signal_id"),)
                ).fetchone()
                existing_meta = {}
                if row and row[0]:
                    try:
                        existing_meta = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError):
                        existing_meta = {}
                existing_meta["actors"] = actor_ids
                if event_id:
                    existing_meta["event_id"] = event_id
                # Phase 68 — Record investigative uplift in conclave_meta for audit
                if interpreted.get("investigative_tier"):
                    existing_meta["investigative_uplift"] = interpreted.get("investigative_uplift", 0.0)
                    existing_meta["investigative_tier"]    = interpreted.get("investigative_tier", "")
                    existing_meta["matched_inv_keywords"]  = interpreted.get("matched_inv_keywords", [])
                conn.execute(
                    "UPDATE signals SET conclave_meta = ? WHERE signal_id = ?",
                    (json.dumps(existing_meta), signal.get("signal_id")),
                )
                conn.commit()
            except Exception as e:
                _log.error("[Meta Patch Error] signal=%s: %s", signal.get("signal_id", "?"), e)

    finally:
        conn.close()

    result = {
        "raw_signal": signal,
        "interpreted_signal": interpreted,
        "entities": resolved_entities,
        "artifact": artifact,
        "event": event,
        "gravity_signal": gravity_signal,
        "case": case_result,
        "feedback": feedback_result,
        "conclusion": conclusion,
    }

    # FMS: fire on_ingest hooks with completed result
    if _FMS_AVAILABLE:
        try:
            _get_fms_context().fire_hook("on_ingest", signal, result)
        except Exception as _e:
            _log.warning("[FMS Hook Error] on_ingest: %s", _e)

    return result




def ingest_and_persist(signal: dict) -> dict:
    """Higher-level helper that persists and returns ingestion results."""
    result = ingest_signal(signal)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z"

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_log (
                id TEXT PRIMARY KEY,
                processed_at TEXT,
                signal_hash TEXT,
                gravity_score REAL
            )
            """
        )

        cursor.execute(
            """
            INSERT OR IGNORE INTO ingestion_log (id, processed_at, signal_hash, gravity_score)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                now,
                hashlib.sha256(repr(result["raw_signal"]).encode("utf-8")).hexdigest(),
                float(result.get("gravity_score", 0)),
            ),
        )

        conn.commit()
    except Exception as e:
        _log.error("[Ingest Persist Error] signal=%s: %s", signal.get("signal_id", "?"), e)
    finally:
        conn.close()

    # ── Phase P1: Pipeline Heartbeat ─────────────────────────────────────────
    # Non-blocking. Uses module-level _sqlite3 and _FORGE_DB_PATH (resolved
    # once at import time). timeout=5 so a DB lock never stalls ingest.
    try:
        _hb = _sqlite3.connect(_FORGE_DB_PATH, timeout=5)
        _hb.execute(
            "INSERT INTO pipeline_health (event_type, signal_count, source, recorded_at)"
            " VALUES (?, 1, ?, datetime('now'))",
            ("ingest", signal.get("source", "unknown")),
        )
        _hb.commit()
        _hb.close()
    except Exception:
        pass   # heartbeat failure must never surface to caller

    # ── Phase P2: Enrichment Queue ───────────────────────────────────────────
    # If content is stub-length and source is enrichable, queue for full-text
    # fetch. Worker drains asynchronously — never inline with ingest.
    _sig_content = signal.get("content") or ""
    _sig_id      = signal.get("signal_id")
    _sig_source  = signal.get("source", "")
    if (
        _sig_id
        and len(_sig_content.strip()) < 200
        and _sig_source in _ENRICHABLE_SOURCES
    ):
        _article_url = signal.get("external_id") or signal.get("url") or ""
        if _article_url and (
            _article_url.startswith("http://") or _article_url.startswith("https://")
        ):
            try:
                _eq = _sqlite3.connect(_FORGE_DB_PATH, timeout=5)
                _eq.execute(
                    "INSERT OR IGNORE INTO enrichment_queue"
                    " (signal_id, url, source, queued_at, status)"
                    " VALUES (?, ?, ?, datetime('now'), 'pending')",
                    (_sig_id, _article_url, _sig_source),
                )
                _eq.commit()
                _eq.close()
            except Exception:
                pass   # enrichment queue failure is non-fatal

    return result