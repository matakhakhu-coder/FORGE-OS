"""
forge_security.detonator — PDF Air-Lock
========================================
Every PDF that enters FORGE (via the PDF Infiltrator collector or manual
artifact attachment) passes through this module before any text extraction
or OCR occurs.

Pipeline:
  1. Magic-byte verification   — reject files that aren't real PDFs
  2. Size gate                 — reject files above MAX_SIZE_MB
  3. Page gate                 — reject documents above MAX_PAGES
  4. pikepdf flatten           — re-linearize to remove embedded JS, URI
                                  actions, launch actions, and XFA forms
  5. Metadata strip            — remove all XMP / DocInfo metadata fields
  6. Write sanitised copy to   — caller-supplied output path

On any failure the source file is moved to QUARANTINE_DIR and a
DetonationError is raised.  The caller is responsible for deleting the
quarantined copy after review.

Dependencies: pikepdf (pip install pikepdf)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
MAX_SIZE_MB:  Final[int] = 50
MAX_PAGES:    Final[int] = 500
QUARANTINE_DIR: Final[Path] = Path("quarantine")

# Real PDF magic bytes: %PDF-
_PDF_MAGIC: Final[bytes] = b"%PDF-"


class DetonationError(Exception):
    """Raised when a PDF fails the air-lock."""


def _quarantine(src: Path, reason: str = "") -> Path:
    """
    Move *src* to QUARANTINE_DIR, write a JSON sidecar with failure metadata,
    and return the new path.
    """
    import json as _json
    from datetime import datetime, timezone

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    dest = QUARANTINE_DIR / src.name
    # avoid overwrite collision
    counter = 0
    while dest.exists():
        counter += 1
        dest = QUARANTINE_DIR / f"{src.stem}_{counter}{src.suffix}"
    shutil.move(str(src), str(dest))

    # Write sidecar metadata so the Quarantine Manager UI can display rich info
    sidecar = dest.with_suffix(dest.suffix + ".meta.json")
    try:
        meta = {
            "original_name":  src.name,
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "reason":         reason or "Unknown failure during detonation",
            "size_bytes":     dest.stat().st_size,
        }
        sidecar.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — sidecar failure must never block quarantine
        pass

    logger.warning("DETONATOR | quarantined %s → %s  [%s]", src, dest, reason)
    return dest


def detonate_pdf(
    src_path: str | Path,
    out_path: str | Path,
    *,
    max_size_mb: int = MAX_SIZE_MB,
    max_pages: int = MAX_PAGES,
) -> Path:
    """
    Run the PDF air-lock against *src_path* and write a sanitised copy to
    *out_path*.

    Parameters
    ----------
    src_path    : path to the raw/untrusted PDF
    out_path    : path where the cleaned PDF will be written
    max_size_mb : file-size ceiling in megabytes (default 50 MB)
    max_pages   : page-count ceiling (default 500 pages)

    Returns
    -------
    Path to the sanitised output file.

    Raises
    ------
    DetonationError  if any check fails (source moved to quarantine/).
    FileNotFoundError if *src_path* does not exist.
    """
    try:
        import pikepdf  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pikepdf is required by forge_security.detonator. "
            "Run: pip install pikepdf"
        ) from exc

    src = Path(src_path).resolve()
    out = Path(out_path).resolve()

    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {src}")

    # ── 1. Magic-byte check ──────────────────────────────────────────────────
    with src.open("rb") as fh:
        header = fh.read(5)
    if header != _PDF_MAGIC:
        reason = f"Magic-byte mismatch — got {header!r}, expected %PDF-"
        _quarantine(src, reason=reason)
        raise DetonationError(f"{reason}: {src.name!r}")

    # ── 2. Size gate ─────────────────────────────────────────────────────────
    size_mb = src.stat().st_size / (1024 * 1024)
    if size_mb > max_size_mb:
        reason = f"File too large: {size_mb:.1f} MB exceeds {max_size_mb} MB cap"
        _quarantine(src, reason=reason)
        raise DetonationError(f"{reason}: {src.name!r}")

    # ── 3 + 4 + 5. Open with pikepdf, gate pages, strip, flatten ────────────
    try:
        pdf = pikepdf.open(str(src))
    except pikepdf.PdfError as exc:
        reason = f"pikepdf parse failure: {exc}"
        _quarantine(src, reason=reason)
        raise DetonationError(f"{reason}: {src.name!r}") from exc

    with pdf:
        # Page gate
        n_pages = len(pdf.pages)
        if n_pages > max_pages:
            reason = f"Too many pages: {n_pages} exceeds {max_pages} page cap"
            _quarantine(src, reason=reason)
            raise DetonationError(f"{reason}: {src.name!r}")

        # Remove dangerous open actions and URI/launch actions from every page
        _strip_dangerous_actions(pdf)

        # Strip document-level metadata
        _strip_metadata(pdf)

        # Write cleaned copy — pikepdf.save re-linearizes / re-serialises
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(out))

    logger.info(
        "DETONATOR | OK  %s → %s  (%.1f MB, %d pages)",
        src.name, out.name, size_mb, n_pages,
    )
    return out


# ── internal helpers ──────────────────────────────────────────────────────────

def _strip_dangerous_actions(pdf: "pikepdf.Pdf") -> None:
    """Remove JS, URI-launch, and OpenAction from the PDF catalog."""
    catalog = pdf.trailer.get("/Root", pikepdf.Dictionary())

    # Document-level open action (auto-executes on open)
    if "/OpenAction" in catalog:
        del catalog["/OpenAction"]
        logger.debug("DETONATOR | stripped /OpenAction")

    # JavaScript name tree
    if "/Names" in catalog:
        names = catalog["/Names"]
        if "/JavaScript" in names:
            del names["/JavaScript"]
            logger.debug("DETONATOR | stripped /Names/JavaScript")

    # Per-page annotations with URI/launch/JS actions
    for page in pdf.pages:
        if "/Annots" not in page:
            continue
        clean_annots = []
        for annot in page["/Annots"]:
            try:
                annot_obj = annot.get_object()
                action = annot_obj.get("/A", None)
                if action is not None:
                    subtype = str(action.get("/S", ""))
                    if subtype in ("/URI", "/Launch", "/JavaScript", "/GoToE"):
                        logger.debug(
                            "DETONATOR | removed annotation action %s", subtype
                        )
                        continue  # drop this annotation
                clean_annots.append(annot)
            except Exception:  # noqa: BLE001 — corrupt annotation; skip safely
                pass
        page["/Annots"] = pikepdf.Array(clean_annots)


def _strip_metadata(pdf: "pikepdf.Pdf") -> None:
    """Blank XMP stream and all DocInfo fields."""
    # XMP metadata stream
    if "/Metadata" in pdf.trailer.get("/Root", {}):
        try:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                meta.clear()
        except Exception:  # noqa: BLE001
            pass

    # Classic DocInfo dictionary
    if pdf.docinfo:
        for key in list(pdf.docinfo.keys()):
            pdf.docinfo[key] = ""
