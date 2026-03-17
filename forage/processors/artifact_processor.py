#!/usr/bin/env python3
"""
FORAGE — Artifact Processor (Phase 19 rev.2: OCR + PDF Live)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ProcessorManager orchestrates extraction pipelines on FORGE artifacts.

Active pipelines
────────────────
  PDFPipeline
    PyMuPDF (fitz) preferred — handles text-layer PDFs and image-only
    pages (renders at 2x and OCRs via OCRPipeline).
    Falls back to pypdf for pure text-layer PDFs if fitz absent.
    Install: pip install pymupdf

  OCRPipeline  ← LIVE (pytesseract + Tesseract 5 verified)
    Preprocessing: greyscale + contrast enhancement before Tesseract.
    Handles multi-frame images (GIF, multi-page TIFF).
    Works on: jpg, jpeg, png, gif, webp, tiff, bmp
    Install: pip install pytesseract pillow

  NERPipeline (spaCy en_core_web_sm)
    Lazy-loaded once per ProcessorManager instance.
    Extracts PERSON, ORG, GPE from whatever text the above produce.

  TranscriptionPipeline  ← stub (future: openai-whisper)
    Install: pip install openai-whisper + ffmpeg on PATH.

Design
──────
• base_dir is resolved automatically from db_path, so Flask inline
  calls (which don't pass base_dir) work without any changes to app.py.

• processing_status lifecycle:
    pending → processing → done
                        → failed   (hard extraction error)
                        → skipped  (no extractable content)

Usage
─────
    python forage/processors/artifact_processor.py
    python forage/processors/artifact_processor.py --status pending
    python forage/processors/artifact_processor.py --artifact-id 42
    python forage/processors/artifact_processor.py --dry-run
    python forage/processors/artifact_processor.py --status failed
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

ENTITY_LABELS = {"PERSON", "ORG", "GPE"}
MAX_TEXT_LEN  = 50_000      # chars fed to spaCy per artifact
BATCH_SIZE    = 20          # artifacts per DB commit cycle
NER_MODEL     = "en_core_web_sm"

OCR_LANG      = "eng"       # Tesseract language code
OCR_CONFIG    = "--psm 3"   # Page segmentation: fully automatic
OCR_CONTRAST  = 1.8         # PIL contrast multiplier (1.0 = no change)
OCR_MIN_CHARS = 20          # discard results shorter than this

PDF_MAX_PAGES = 200         # cap to prevent runaway on huge documents

# ── Confidence heuristic ──────────────────────────────────────────────────────

_CONF_WEIGHTS: list[tuple[str, float]] = [
    ("confirmed", 0.3), ("official", 0.3), ("verified", 0.3),
    ("breaking",  0.2), ("urgent",   0.2),
    ("attack",    0.1), ("crisis",   0.1), ("nuclear",  0.1),
    ("casualt",   0.1), ("alert",    0.1), ("warning",  0.1),
    ("leaked",    0.1), ("intercept",0.1),
]
_BASE_CONF = 0.25

def _confidence(text: str) -> float:
    score = _BASE_CONF
    lo    = text.lower()
    for kw, w in _CONF_WEIGHTS:
        if kw in lo:
            score += w
    return min(round(score, 3), 1.0)

# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: str | None = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent.parent / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def log(msg: str)  -> None: print(f"[{_ts()}] [artifact_processor] {msg}", flush=True)
def warn(msg: str) -> None: print(f"[{_ts()}] [artifact_processor] WARN  {msg}",
                                   file=sys.stderr, flush=True)

# ── PDF pipeline ──────────────────────────────────────────────────────────────

class PDFPipeline:
    """
    Extract text from PDFs.

    Strategy (tried in order):
      1. PyMuPDF (fitz)  — handles text-layer AND image-only pages.
                           Image pages are rendered at 2x and OCR'd.
      2. pypdf            — fallback for pure text-layer PDFs.

    Install: pip install pymupdf
    """

    @staticmethod
    def _fitz_available() -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _pypdf_available() -> bool:
        try:
            import pypdf  # noqa: F401
            return True
        except ImportError:
            return False

    def available(self) -> bool:
        return self._fitz_available() or self._pypdf_available()

    def extract(self, file_path: Path,
                ocr_pipeline: "OCRPipeline | None" = None) -> str | None:
        if self._fitz_available():
            return self._extract_fitz(file_path, ocr_pipeline)
        if self._pypdf_available():
            return self._extract_pypdf(file_path)
        warn(f"PDF extraction unavailable — install pymupdf: pip install pymupdf")
        return None

    def _extract_fitz(self, file_path: Path,
                      ocr_pipeline: "OCRPipeline | None") -> str | None:
        try:
            import fitz
        except ImportError:
            return self._extract_pypdf(file_path)

        try:
            doc    = fitz.open(str(file_path))
            pages  = min(len(doc), PDF_MAX_PAGES)
            chunks: list[str] = []

            for i in range(pages):
                page = doc[i]
                text = page.get_text("text").strip()

                if len(text) < 20 and ocr_pipeline and ocr_pipeline.available():
                    # Image-only page — render at 2x greyscale and OCR
                    try:
                        import fitz as _fitz
                        from PIL import Image as PILImage
                        import io
                        mat       = _fitz.Matrix(2.0, 2.0)
                        pix       = page.get_pixmap(
                            matrix=mat,
                            colorspace=_fitz.csGRAY,
                        )
                        img_bytes = pix.tobytes("png")
                        img       = PILImage.open(io.BytesIO(img_bytes))
                        text      = ocr_pipeline.extract_image(img) or ""
                        if text:
                            log(f"  PDF p{i+1}: OCR yielded {len(text)} chars")
                    except Exception as exc:
                        warn(f"  PDF p{i+1} OCR render failed: {exc}")

                if text:
                    chunks.append(text)

            doc.close()
            result = "\n\n".join(chunks).strip()
            return result if result else None

        except Exception as exc:
            warn(f"PyMuPDF failed for {file_path.name}: {exc}")
            return self._extract_pypdf(file_path)

    def _extract_pypdf(self, file_path: Path) -> str | None:
        if not self._pypdf_available():
            return None
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            pages  = [p.extract_text() or "" for p in reader.pages[:PDF_MAX_PAGES]]
            result = "\n".join(pages).strip()
            return result if result else None
        except Exception as exc:
            warn(f"pypdf failed for {file_path.name}: {exc}")
            return None


# ── OCR pipeline ──────────────────────────────────────────────────────────────

class OCRPipeline:
    """
    Image → text via pytesseract + Tesseract 5.

    Preprocessing:
      1. Convert to greyscale
      2. Contrast enhancement (configurable via OCR_CONTRAST)

    Handles multi-frame images (GIF, multi-page TIFF).

    Windows PATH fix: Tesseract is auto-detected in its standard install
    locations if it's not on the system PATH.
    """

    # Standard Windows install locations Tesseract 5 uses
    _TESSERACT_WIN_PATHS = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\Public\Tesseract-OCR\tesseract.exe",
    ]

    @classmethod
    def _configure_tesseract(cls) -> bool:
        """
        Ensure pytesseract can find the Tesseract binary.
        On Windows, checks standard install paths if not on PATH.
        Returns True if tesseract is locatable.
        """
        import sys
        try:
            import pytesseract
        except ImportError:
            return False

        # Try calling it first — if it's on PATH, we're done
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            pass

        # Windows fallback: probe known install paths
        if sys.platform == "win32":
            from pathlib import Path as _Path
            import os
            # Also probe APPDATA and LOCALAPPDATA variants
            extra = []
            for env_var in ("LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
                base = os.environ.get(env_var)
                if base:
                    extra.append(
                        str(_Path(base) / "Tesseract-OCR" / "tesseract.exe")
                    )
            for candidate in cls._TESSERACT_WIN_PATHS + extra:
                if _Path(candidate).exists():
                    pytesseract.pytesseract.tesseract_cmd = candidate
                    try:
                        pytesseract.get_tesseract_version()
                        log(f"Tesseract found at: {candidate}")
                        return True
                    except Exception:
                        continue

        return False

    def available(self) -> bool:
        try:
            from PIL import Image  # noqa: F401
            return self._configure_tesseract()
        except ImportError:
            return False

    def _preprocess(self, img):
        """Greyscale + contrast boost."""
        from PIL import ImageEnhance
        img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(OCR_CONTRAST)
        return img

    def extract_image(self, img) -> str | None:
        """
        Run OCR on an already-open PIL Image.
        Used by PDFPipeline for rendered pages.
        """
        if not self.available():
            return None
        try:
            import pytesseract
            processed = self._preprocess(img)
            text      = pytesseract.image_to_string(
                processed, lang=OCR_LANG, config=OCR_CONFIG,
            ).strip()
            # Clean common Tesseract noise
            text = re.sub(r"(?m)^[\s\W]{0,3}$", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return text if len(text) >= OCR_MIN_CHARS else None
        except Exception as exc:
            warn(f"OCR image_to_string failed: {exc}")
            return None

    def extract(self, file_path: Path) -> str | None:
        """Run OCR on an image file path."""
        if not self.available():
            warn(f"OCR unavailable — install pytesseract + Tesseract binary")
            return None
        try:
            from PIL import Image
            with Image.open(str(file_path)) as img:
                try:
                    n_frames = getattr(img, "n_frames", 1)
                except Exception:
                    n_frames = 1

                if n_frames > 1:
                    frames = []
                    for i in range(min(n_frames, 10)):
                        img.seek(i)
                        t = self.extract_image(img.copy())
                        if t:
                            frames.append(t)
                    text = "\n\n".join(frames)
                else:
                    text = self.extract_image(img) or ""

            log(f"  OCR {file_path.name}: {len(text)} chars")
            return text if len(text) >= OCR_MIN_CHARS else None

        except Exception as exc:
            warn(f"OCR failed for {file_path.name}: {exc}")
            return None


# ── Transcription pipeline (stub) ─────────────────────────────────────────────

class TranscriptionPipeline:
    """Stub — activate by installing openai-whisper + ffmpeg."""

    def available(self) -> bool:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, file_path: Path, model_size: str = "base") -> str | None:
        if not self.available():
            log(f"Transcription skipped — install openai-whisper + ffmpeg")
            return None
        try:
            import whisper
            model  = whisper.load_model(model_size)
            result = model.transcribe(str(file_path))
            text   = (result.get("text") or "").strip()
            log(f"  Transcription {file_path.name}: {len(text)} chars")
            return text or None
        except Exception as exc:
            warn(f"Transcription failed for {file_path.name}: {exc}")
            return None


# ── NER pipeline ──────────────────────────────────────────────────────────────

class NERPipeline:
    """Lazy-loaded spaCy NER. Loaded once per ProcessorManager instance."""

    def __init__(self) -> None:
        self._nlp = None

    def _load(self):
        if self._nlp is not None:
            return self._nlp
        try:
            import spacy
        except ImportError:
            raise RuntimeError(
                "spaCy not installed.\n"
                "  pip install spacy && python -m spacy download en_core_web_sm"
            )
        try:
            full    = spacy.load(NER_MODEL)
            disable = [p for p in full.pipe_names if p not in ("tok2vec", "ner")]
            self._nlp = spacy.load(NER_MODEL, disable=disable)
        except OSError:
            raise RuntimeError(
                f"spaCy model '{NER_MODEL}' not found.\n"
                f"  python -m spacy download {NER_MODEL}"
            )
        return self._nlp

    def extract(self, text: str) -> list[dict]:
        nlp  = self._load()
        doc  = nlp(text[:MAX_TEXT_LEN])
        seen: dict[tuple, int] = {}
        for ent in doc.ents:
            if ent.label_ not in ENTITY_LABELS:
                continue
            surface = ent.text.strip().rstrip("'s").strip()
            if len(surface) < 2:
                continue
            key       = (surface, ent.label_)
            seen[key] = seen.get(key, 0) + 1
        return [{"text": t, "label": l, "count": c} for (t, l), c in seen.items()]


# ── ProcessorManager ──────────────────────────────────────────────────────────

class ProcessorManager:
    """
    Orchestrates all pipelines for a given artifact.

    Two usage modes:
      1. Flask inline: pass conn=g.db — no commit ownership
      2. Standalone:   pass db_path=... — opens and owns connection

    base_dir is resolved automatically from db_path so Flask inline
    calls don't need to pass it.
    """

    def __init__(self,
                 db_path: Path | None = None,
                 conn: sqlite3.Connection | None = None) -> None:
        self._db_path   = db_path
        self._ext_conn  = conn
        self._own_conn: sqlite3.Connection | None = None
        self._ner        = NERPipeline()
        self._ocr        = OCRPipeline()
        self._pdf        = PDFPipeline()
        self._transcribe = TranscriptionPipeline()

    def _conn(self) -> sqlite3.Connection:
        if self._ext_conn is not None:
            return self._ext_conn
        if self._own_conn is None:
            if self._db_path is None:
                self._db_path = _resolve_db()
            self._own_conn = _open_db(self._db_path)
        return self._own_conn

    def _commit(self) -> None:
        if self._ext_conn is None and self._own_conn is not None:
            self._own_conn.commit()

    def close(self) -> None:
        if self._own_conn is not None:
            self._own_conn.close()
            self._own_conn = None

    def _base_dir(self) -> Path:
        """Resolve FORGE project root (parent of database.db)."""
        if self._db_path:
            return self._db_path.resolve().parent
        return Path(__file__).resolve().parent.parent.parent

    def _store_entities(self, signal_id: str, entities: list[dict]) -> int:
        conn = self._conn()
        inserted = 0
        for ent in entities:
            cur = conn.execute(
                "INSERT OR IGNORE INTO signal_entities "
                "(signal_id, text, label, count) VALUES (?, ?, ?, ?)",
                (signal_id, ent["text"], ent["label"], ent["count"]),
            )
            inserted += cur.rowcount
        return inserted

    def _get_or_create_signal(self, artifact_id: int,
                               row: sqlite3.Row) -> str:
        conn     = self._conn()
        existing = conn.execute(
            "SELECT signal_id FROM signals WHERE source_artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if existing:
            return existing["signal_id"]

        sid    = str(uuid.uuid4())
        ext_id = f"artifact:{artifact_id}:{(row['title'] or '')[:40]}"
        conf   = _confidence(row["raw_text_cache"] or row["description"] or "")
        try:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority,
                     confidence_score, source_artifact_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'raw', 0, ?, ?)
            """, (sid, row["source"] or "artifact", ext_id,
                  row["title"], (row["description"] or "")[:1000],
                  row["latitude"], row["longitude"], conf, artifact_id))
        except Exception:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'raw', 0)
            """, (sid, row["source"] or "artifact", ext_id,
                  row["title"], (row["description"] or "")[:1000],
                  row["latitude"], row["longitude"]))
        return sid

    def _extract_text(self, abs_path: Path,
                      atype: str, ext: str) -> tuple[str | None, str]:
        """
        Dispatch to the correct extraction pipeline.
        Returns (text_or_None, processing_status_string).
        """
        ext = ext.lower().lstrip(".")

        if ext == "pdf":
            if not self._pdf.available():
                log("PDF pipeline unavailable — pip install pymupdf")
                return None, "pending"
            text = self._pdf.extract(abs_path, ocr_pipeline=self._ocr)
            return (text[:MAX_TEXT_LEN] if text else None,
                    "done" if text else "failed")

        if ext in {"jpg","jpeg","png","gif","webp","tiff","bmp"} \
                or atype in ("photo","capture"):
            if not self._ocr.available():
                log("OCR unavailable — pip install pytesseract pillow")
                return None, "pending"
            text = self._ocr.extract(abs_path)
            return (text[:MAX_TEXT_LEN] if text else None,
                    "done" if text else "skipped")

        if ext == "txt":
            try:
                text = abs_path.read_text(encoding="utf-8",
                                          errors="replace")[:MAX_TEXT_LEN]
                return (text if text.strip() else None,
                        "done" if text.strip() else "skipped")
            except Exception as exc:
                warn(f"Text read failed: {exc}")
                return None, "failed"

        if ext in {"mp3","wav","ogg","m4a","mp4","mov","avi","mkv"} \
                or atype in ("audio","video"):
            text = self._transcribe.extract(abs_path)
            return (text[:MAX_TEXT_LEN] if text else None,
                    "done" if text else "pending")

        return None, "skipped"

    def process_artifact(self,
                         artifact_id: int,
                         raw_text: str | None = None,
                         artifact_type: str | None = None,
                         base_dir: Path | None = None) -> dict:
        """
        Run all applicable pipelines for one artifact.
        Returns: { artifact_id, signal_id, entities, status, chars_extracted }
        """
        conn = self._conn()
        row  = conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if not row:
            return {"artifact_id": artifact_id, "status": "failed",
                    "error": "not found"}

        atype      = artifact_type or row["type"] or ""
        text       = raw_text or row["raw_text_cache"] or ""
        new_status = "done"

        # ── Step 1: extract text if we don't already have it ──────────────
        if not text.strip():
            file_path_str = row["file_path"] if "file_path" in row.keys() else None
            if file_path_str:
                resolved_base = base_dir or self._base_dir()
                abs_path      = resolved_base / file_path_str
                ext           = Path(file_path_str).suffix

                if abs_path.exists():
                    log(f"Extracting: {abs_path.name} (type={atype})")
                    conn.execute(
                        "UPDATE artifacts SET processing_status='processing' "
                        "WHERE artifact_id=?", (artifact_id,),
                    )
                    self._commit()
                    text, new_status = self._extract_text(abs_path, atype, ext)
                    text = text or ""
                    if text:
                        conn.execute(
                            "UPDATE artifacts SET raw_text_cache=? "
                            "WHERE artifact_id=?", (text, artifact_id),
                        )
                else:
                    warn(f"File missing on disk: {abs_path}")
                    new_status = "failed"
            else:
                # No file — seed from description
                text       = row["description"] or ""
                new_status = "done" if text.strip() else "skipped"

        # ── Step 2: bail if still empty ───────────────────────────────────
        if not text.strip():
            conn.execute(
                "UPDATE artifacts SET processing_status=? WHERE artifact_id=?",
                (new_status, artifact_id),
            )
            self._commit()
            return {"artifact_id": artifact_id, "status": new_status,
                    "entities": 0, "chars_extracted": 0}

        # ── Step 3: NER ───────────────────────────────────────────────────
        conn.execute(
            "UPDATE artifacts SET processing_status='processing' "
            "WHERE artifact_id=?", (artifact_id,),
        )
        self._commit()

        try:
            entities = self._ner.extract(text)
            log(f"  NER: {len(entities)} entities found")
        except RuntimeError as exc:
            warn(f"NER unavailable for artifact {artifact_id}: {exc}")
            conn.execute(
                "UPDATE artifacts SET processing_status='pending' "
                "WHERE artifact_id=?", (artifact_id,),
            )
            self._commit()
            return {"artifact_id": artifact_id, "status": "pending",
                    "entities": 0, "chars_extracted": len(text)}

        # ── Step 4: persist entities ──────────────────────────────────────
        signal_id  = self._get_or_create_signal(artifact_id, row)
        n_inserted = self._store_entities(signal_id, entities)

        # ── Step 5: update signal confidence ─────────────────────────────
        try:
            conn.execute(
                "UPDATE signals SET confidence_score=? WHERE signal_id=?",
                (_confidence(text), signal_id),
            )
        except Exception:
            pass

        # ── Step 6: mark done ─────────────────────────────────────────────
        conn.execute(
            "UPDATE artifacts SET processing_status='done' "
            "WHERE artifact_id=?", (artifact_id,),
        )
        self._commit()

        return {
            "artifact_id":    artifact_id,
            "signal_id":      signal_id,
            "entities":       n_inserted,
            "status":         "done",
            "chars_extracted": len(text),
        }

    def run_batch(self,
                  status_filter: str = "pending",
                  limit: int = 500,
                  dry_run: bool = False,
                  base_dir: Path | None = None) -> dict:
        conn = self._conn()
        rows = conn.execute(
            "SELECT artifact_id, type, file_path, raw_text_cache, title "
            "FROM artifacts WHERE processing_status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status_filter, limit),
        ).fetchall()

        log(f"Batch: {len(rows)} artifacts with status='{status_filter}'")
        summary = {"processed": 0, "entities": 0,
                   "done": 0, "failed": 0, "skipped": 0, "pending": 0}

        for i, row in enumerate(rows):
            aid = row["artifact_id"]
            if dry_run:
                log(f"  [DRY] {aid}: '{row['title'][:50]}' | "
                    f"type={row['type']} | file={row['file_path']} | "
                    f"has_text={bool(row['raw_text_cache'])}")
                summary["processed"] += 1
                continue

            result = self.process_artifact(artifact_id=aid, base_dir=base_dir)
            summary["processed"] += 1
            summary["entities"]  += result.get("entities", 0)
            s = result.get("status", "failed")
            summary[s] = summary.get(s, 0) + 1

            if (i + 1) % BATCH_SIZE == 0:
                log(f"  Progress {i+1}/{len(rows)} — "
                    f"{summary['done']} done, {summary['failed']} failed")

        log(f"Batch complete: {summary}")
        self.close()
        return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli_run(db_path: Path | None, status_filter: str,
             artifact_id: int | None, dry_run: bool) -> int:
    resolved = _resolve_db(str(db_path) if db_path else None)
    log(f"Database : {resolved}")
    log(f"OCR      : {'available ✓' if OCRPipeline().available() else 'NOT available'}")
    log(f"PDF      : {'fitz/PyMuPDF ✓' if PDFPipeline._fitz_available() else 'pypdf ✓' if PDFPipeline._pypdf_available() else 'NOT available'}")

    try:
        pm = ProcessorManager(db_path=resolved)
    except Exception as exc:
        print(f"[artifact_processor] ERROR: {exc}", file=sys.stderr)
        return 1

    if artifact_id is not None:
        log(f"Processing single artifact: {artifact_id}")
        result = pm.process_artifact(artifact_id=artifact_id)
        log(f"Result: {result}")
        pm.close()
        return 0 if result["status"] in ("done", "skipped") else 1

    summary = pm.run_batch(
        status_filter=status_filter,
        dry_run=dry_run,
        base_dir=resolved.parent,
    )
    return 0 if summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORAGE Artifact Processor — PDF + OCR + NER pipeline"
    )
    parser.add_argument("--status", default="pending",
                        choices=["pending","failed","done","skipped","processing"])
    parser.add_argument("--artifact-id", type=int, default=None, dest="artifact_id")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(_cli_run(
        db_path=args.db,
        status_filter=args.status,
        artifact_id=args.artifact_id,
        dry_run=args.dry_run,
    ))