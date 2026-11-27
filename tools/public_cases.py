"""Public case files (27 Nov 2026).

In FORGE OS a case lives in the local database: artifacts, events, photos,
PDFs and video links that the admin portal pins to it. That material stays
on the operator's machine. Nothing in the database is ever published by
this module.

A *public case file* is the deliberate, cloud copy of a case: a folder in
docs/cases/public/<slug>/ holding case.json and only the files the owner
chose to publish. It is tracked in git like the checked pins, so every
build (on any machine, from any database copy) renders the same pages:

    dist/cases/<slug>.html         one page per case file
    dist/case-files.html           the list of case files
    dist/static/cases/<slug>/...   its photos and PDFs

Rules (checked on every build; a bad case file stops the build):
  - "public": true must be set by hand. Without it the folder is skipped.
  - Files must sit in the case folder itself (no paths), with an allowed
    extension whose bytes match the type, under the size limit.
  - Photos need alt text and a caption, and must not carry GPS data.
  - Video and link artifacts are https only; videos come from a short list
    of hosts and are linked, never re-hosted.
  - A case.json may only use the keys below.

Commands:
    python tools/public_cases.py check      validate every case file
    python tools/public_cases.py list       one line per case file
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_CASES_DIR = ROOT / "docs" / "cases" / "public"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CASE_KEYS = {"public", "slug", "title", "summary", "case_type", "published_at",
             "publication", "artifacts"}
PUBLICATION_KEYS = {"approved_by", "approved_on", "basis"}
CASE_TYPES = {"general", "financial", "geopolitical", "criminal",
              "infrastructure", "cyber", "humanitarian", "other"}
KINDS = {
    "photo": {"keys": {"kind", "file", "title", "alt", "caption", "captured_at", "credit"},
              "required": {"file", "title", "alt", "caption", "credit"}},
    "pdf":   {"keys": {"kind", "file", "title", "caption", "captured_at", "credit"},
              "required": {"file", "title", "caption"}},
    "video": {"keys": {"kind", "url", "title", "caption", "captured_at", "credit"},
              "required": {"url", "title", "caption"}},
    "link":  {"keys": {"kind", "url", "title", "caption"},
              "required": {"url", "title"}},
}
PHOTO_EXT = {".webp", ".jpg", ".jpeg", ".png"}
PDF_EXT = {".pdf"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
               "vimeo.com", "www.vimeo.com", "facebook.com", "www.facebook.com",
               "fb.watch", "tiktok.com", "www.tiktok.com"}
GPS_IFD_TAG = 34853


class PublicCaseError(ValueError):
    pass


def _magic_ok(path: pathlib.Path) -> bool:
    head = path.read_bytes()[:16]
    ext = path.suffix.lower()
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def _photo_info(path: pathlib.Path) -> tuple[int, int]:
    """(width, height); refuses a photo that carries GPS data."""
    from PIL import Image
    with Image.open(path) as im:
        exif = im.getexif()
        if exif and GPS_IFD_TAG in exif:
            raise PublicCaseError(f"{path.name}: carries GPS data; strip it before publishing")
        return im.width, im.height


def _https(url: object, where: str) -> str:
    if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).netloc:
        raise PublicCaseError(f"{where}: url must be an https address")
    return url


def _text(d: dict, key: str, where: str, limit: int = 600) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise PublicCaseError(f"{where}: '{key}' is required")
    if len(v) > limit:
        raise PublicCaseError(f"{where}: '{key}' is longer than {limit} characters")
    return v.strip()


def load_case(folder: pathlib.Path) -> dict | None:
    """One checked case file, or None when it is not marked public."""
    path = folder / "case.json"
    if not path.is_file():
        raise PublicCaseError(f"{folder.name}: no case.json")
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublicCaseError(f"{folder.name}/case.json: {exc}") from exc
    if not isinstance(case, dict):
        raise PublicCaseError(f"{folder.name}/case.json: must be an object")
    if case.get("public") is not True:
        return None
    where = f"{folder.name}/case.json"
    extra = set(case) - CASE_KEYS
    if extra:
        raise PublicCaseError(f"{where}: unknown keys {sorted(extra)}")
    slug = case.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug) or slug != folder.name:
        raise PublicCaseError(f"{where}: slug must match the folder name")
    out = {
        "slug": slug,
        "title": _text(case, "title", where, 140),
        "summary": _text(case, "summary", where, 900),
        "case_type": case.get("case_type", "general"),
        "published_at": case.get("published_at"),
    }
    if out["case_type"] not in CASE_TYPES:
        raise PublicCaseError(f"{where}: case_type must be one of {sorted(CASE_TYPES)}")
    if not isinstance(out["published_at"], str) or not DATE_RE.match(out["published_at"]):
        raise PublicCaseError(f"{where}: published_at must be YYYY-MM-DD")
    pub = case.get("publication")
    if not isinstance(pub, dict) or set(pub) - PUBLICATION_KEYS:
        raise PublicCaseError(f"{where}: publication must hold only {sorted(PUBLICATION_KEYS)}")
    for k in PUBLICATION_KEYS:
        _text(pub, k, f"{where} publication")
    out["publication"] = dict(pub)

    arts = case.get("artifacts")
    if not isinstance(arts, list) or not arts:
        raise PublicCaseError(f"{where}: artifacts must be a non-empty list")
    seen_files: set[str] = set()
    out["artifacts"] = []
    for i, a in enumerate(arts, 1):
        aw = f"{where} artifact {i}"
        if not isinstance(a, dict) or a.get("kind") not in KINDS:
            raise PublicCaseError(f"{aw}: kind must be one of {sorted(KINDS)}")
        spec = KINDS[a["kind"]]
        if set(a) - spec["keys"]:
            raise PublicCaseError(f"{aw}: unknown keys {sorted(set(a) - spec['keys'])}")
        item = {"kind": a["kind"]}
        for k in spec["keys"] - {"kind"}:
            if k in spec["required"]:
                item[k] = _text(a, k, aw) if k not in ("file", "url") else a.get(k)
            elif k in a:
                item[k] = _text(a, k, aw)
        if "captured_at" in item and not DATE_RE.match(item["captured_at"]):
            raise PublicCaseError(f"{aw}: captured_at must be YYYY-MM-DD")
        if a["kind"] in ("photo", "pdf"):
            name = item["file"]
            if not isinstance(name, str) or not FILE_RE.match(name) or name == "case.json":
                raise PublicCaseError(f"{aw}: file must be a plain lower-case file name in the case folder")
            if name in seen_files:
                raise PublicCaseError(f"{aw}: {name} is listed twice")
            seen_files.add(name)
            fp = folder / name
            if not fp.is_file() or fp.is_symlink():
                raise PublicCaseError(f"{aw}: {name} is not in the case folder")
            ext = fp.suffix.lower()
            allowed, limit = (PHOTO_EXT, MAX_PHOTO_BYTES) if a["kind"] == "photo" else (PDF_EXT, MAX_PDF_BYTES)
            if ext not in allowed:
                raise PublicCaseError(f"{aw}: {name} must end in one of {sorted(allowed)}")
            if fp.stat().st_size > limit:
                raise PublicCaseError(f"{aw}: {name} is over {limit // (1024 * 1024)} MB")
            if not _magic_ok(fp):
                raise PublicCaseError(f"{aw}: {name} is not really a {ext} file")
            if a["kind"] == "photo":
                item["width"], item["height"] = _photo_info(fp)
            item["bytes"] = fp.stat().st_size
            item["path"] = fp
            item["src"] = f"static/cases/{slug}/{name}"
        else:
            item["url"] = _https(item["url"], aw)
            if a["kind"] == "video" and urlparse(item["url"]).netloc.lower() not in VIDEO_HOSTS:
                raise PublicCaseError(f"{aw}: video links must be on {sorted(VIDEO_HOSTS)}")
        out["artifacts"].append(item)
    # A file in the folder that case.json does not list is never published.
    out["unlisted"] = sorted(p.name for p in folder.iterdir()
                             if p.is_file() and p.name != "case.json" and p.name not in seen_files)
    out["photos"] = [a for a in out["artifacts"] if a["kind"] == "photo"]
    return out


def load_cases(base: pathlib.Path = PUBLIC_CASES_DIR) -> list[dict]:
    if not base.is_dir():
        return []
    cases = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        c = load_case(folder)
        if c:
            cases.append(c)
    return sorted(cases, key=lambda c: (c["published_at"], c["slug"]), reverse=True)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    try:
        cases = load_cases()
    except PublicCaseError as exc:
        print(f"[public_cases] REFUSED: {exc}")
        return 1
    for c in cases:
        kinds = {}
        for a in c["artifacts"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        line = f"{c['slug']}: {', '.join(f'{n} {k}' for k, n in kinds.items())}"
        if c["unlisted"]:
            line += f" (not published, not in case.json: {', '.join(c['unlisted'])})"
        print(line)
    if cmd == "check":
        print(f"[public_cases] {len(cases)} public case file(s) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
