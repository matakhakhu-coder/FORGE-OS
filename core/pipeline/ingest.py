import uuid
import hashlib
import datetime
from pathlib import Path

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
from forage.engines.entity_engine import materialize_entities
from forage.engines.relationship_engine import link_signal_actors, link_event_actors

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
        except Exception:
            pass

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
                        datetime.datetime.utcnow().isoformat() + "Z",
                        json.dumps(conclusion.provenance),
                        signal.get("signal_id"),
                    ),
                )
                conn.commit()
            except Exception as e:
                print(f"[Conclave Persist Error] {e}")

        # 2. Materialize entities
        actor_ids = []
        try:
            actor_ids = materialize_entities(conclusion, signal.get("signal_id"), conn) or []
        except Exception as e:
            print(f"[Entity Error] {e}")

        # 2a. Link signal → actors
        if actor_ids:
            try:
                link_signal_actors(signal.get("signal_id"), actor_ids, conn)
            except Exception as e:
                print(f"[Relationship Error - Signal] {e}")

        # 3. Then escalate
        event_id = None
        try:
            event_id = handle_escalation(conclusion, signal.get("signal_id"), conn)
        except Exception as e:
            print(f"[Escalation Error] {e}")

        # 3a. Link event → actors
        if event_id and actor_ids:
            try:
                link_event_actors(event_id, actor_ids, conn)
            except Exception as e:
                print(f"[Relationship Error - Event] {e}")

        # Legacy stub: preserve previous behavior without breaking
        if signal.get("signal_id"):
            apply_conclave_stub(signal.get("signal_id"), conn)

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
        except Exception:
            pass

    return result


def apply_conclave_stub(signal_id, db):
    """Temporary Conclave stub — does not change existing decision flow."""
    result = AnalysisResult(
        entities=[],
        intent="unknown",
        gravity=0.1,
        recommendation="IGNORE",
        confidence=0.1,
        provenance={"stage": "stub"},
    )

    try:
        db.execute(
            """
            UPDATE signals
            SET gravity_score = ?, processed_at = ?, conclave_meta = ?
            WHERE signal_id = ?
            """,
            (
                result.gravity,
                datetime.datetime.utcnow().isoformat() + "Z",
                json.dumps(result.provenance),
                signal_id,
            ),
        )
        db.commit()
    except Exception:
        # non-fatal: we don't want Conclave stub to break ingestion
        pass


def ingest_and_persist(signal: dict) -> dict:
    """Higher-level helper that persists and returns ingestion results."""
    result = ingest_signal(signal)

    # optionally persist summary into the FORGE database if storage is needed
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.datetime.utcnow().isoformat() + "Z"

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
    conn.close()

    return result