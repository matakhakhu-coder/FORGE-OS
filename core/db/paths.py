"""
One place that decides which SQLite file a FORGE process may open.

Why this exists
---------------
Every tool used to work out its own database path: FORGE_DB if set, else the
checkout's database.db. Five collectors ignored FORGE_DB entirely, and plain
sqlite3.connect() creates an empty file when the path is wrong. That is fine
on a laptop, but it means an isolated ingest run cannot be trusted to stay on
its own database.

Roles
-----
FORGE_ROLE is read from the process environment only (never from .env).

    unset / "default" / "production"
        Behaviour is unchanged: FORGE_DB if set (expanded and resolved), else
        the default path the caller passes in (the checkout's database.db).

    "staging"
        - FORGE_DB is required. There is no fallback to database.db.
        - The path must be absolute, with no ".." and no symlink anywhere in it.
        - The file must already exist and be a regular file with one name
          (st_nlink == 1). Nothing here ever creates a database.
        - Its -wal, -shm and -journal companions, when present, must also be
          regular single-name files.
        - A deny list (FORGE_DB_DENY_FILE, default /etc/forge-staging/deny.json)
          is required. A file whose (device, inode) matches a deny entry, or a
          path that equals or sits under a denied path, is refused.
        - The checkout's own database.db is refused.
        - Connections are opened with mode=rw, so a vanished file is an error,
          never a new empty database.

    anything else
        Refused. A typo such as "stagng" must not quietly mean production.

This module is only the code half of the isolation. It does not create users,
folders or databases, and the agent account that starts a staging run can
still bypass it. See docs/approvals/pins/README.md ("Rollout") for the rest.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import urllib.parse
from pathlib import Path
from typing import Iterable, Mapping

ROLE_ENV = "FORGE_ROLE"
DB_ENV = "FORGE_DB"
DENY_ENV = "FORGE_DB_DENY_FILE"
DEFAULT_DENY_FILE = "/etc/forge-staging/deny.json"
DENY_SCHEMA = "forge.staging.deny"
DENY_VERSIONS = {1}

STAGING = "staging"
DEFAULT_ROLES = {"", "default", "production"}
KNOWN_ROLES = DEFAULT_ROLES | {STAGING}

SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# The checkout's default database file. Staging refuses it outright.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPO_DEFAULT_DB = REPO_ROOT / "database.db"


class DBPathError(RuntimeError):
    """A database path this process must not open."""


# ── Role ──────────────────────────────────────────────────────────────────────

def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def role(environ: Mapping[str, str] | None = None) -> str:
    """The normalised FORGE_ROLE. Raises DBPathError on an unknown value."""
    raw = _env(environ).get(ROLE_ENV, "")
    value = (raw or "").strip().lower()
    if value not in KNOWN_ROLES:
        raise DBPathError(
            f"{ROLE_ENV}={raw!r} is not a known role "
            f"(use 'staging', or leave it unset for the default)")
    return STAGING if value == STAGING else "default"


def is_staging(environ: Mapping[str, str] | None = None) -> bool:
    return role(environ) == STAGING


# ── Deny list ─────────────────────────────────────────────────────────────────

def load_deny(deny_file: str | os.PathLike | None = None,
              environ: Mapping[str, str] | None = None) -> dict:
    """Read and check the staging deny list. Fails closed on any problem."""
    env = _env(environ)
    path = Path(deny_file if deny_file is not None
                else (env.get(DENY_ENV, "").strip() or DEFAULT_DENY_FILE))
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise DBPathError(
            f"staging deny list {path} does not exist; staging refuses to run "
            f"without the authority's identity on record") from None
    except OSError as exc:
        raise DBPathError(f"staging deny list {path} cannot be read: {exc}") from None
    if not stat.S_ISREG(st.st_mode):
        raise DBPathError(f"staging deny list {path} is not a regular file")
    if st.st_mode & 0o022:
        raise DBPathError(f"staging deny list {path} is writable by group or others")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DBPathError(f"staging deny list {path} is not readable JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != DENY_SCHEMA \
            or data.get("version") not in DENY_VERSIONS:
        raise DBPathError(f"staging deny list {path} is not a {DENY_SCHEMA} v1 file")
    entries = data.get("entries")
    paths = data.get("paths", [])
    if not isinstance(entries, list) or not entries:
        raise DBPathError(f"staging deny list {path} has no (device, inode) entries")
    ids: set[tuple[int, int]] = set()
    for e in entries:
        dev, ino = (e.get("dev"), e.get("ino")) if isinstance(e, dict) else (None, None)
        if not (isinstance(dev, int) and isinstance(ino, int)) or isinstance(dev, bool) \
                or isinstance(ino, bool):
            raise DBPathError(f"staging deny list {path}: each entry needs integer dev and ino")
        ids.add((dev, ino))
    if not isinstance(paths, list) or not all(isinstance(p, str) and os.path.isabs(p) for p in paths):
        raise DBPathError(f"staging deny list {path}: paths must be a list of absolute paths")
    return {"file": str(path), "ids": ids,
            "paths": [os.path.normpath(p) for p in paths]}


def _under(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


# ── Path checks ───────────────────────────────────────────────────────────────

def _refuse_symlink_components(p: Path) -> None:
    """Walk the path from the root and refuse any symlink along the way."""
    current = Path(p.anchor)
    for part in p.parts[1:]:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            raise DBPathError(f"{current} does not exist; staging never creates a database") from None
        except OSError as exc:
            raise DBPathError(f"{current} cannot be checked: {exc}") from None
        if stat.S_ISLNK(st.st_mode):
            raise DBPathError(f"{current} is a symlink; staging refuses symlinked database paths")


def _check_file(p: Path, deny: dict, what: str) -> None:
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode):
        raise DBPathError(f"{what} {p} is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise DBPathError(f"{what} {p} is not a regular file")
    if st.st_nlink != 1:
        raise DBPathError(f"{what} {p} has {st.st_nlink} names on disk (hard links); staging needs exactly one")
    if (st.st_dev, st.st_ino) in deny["ids"]:
        raise DBPathError(f"{what} {p} is a denied file (matches a deny-list device and inode)")


def check_staging_path(path: str | os.PathLike,
                       environ: Mapping[str, str] | None = None,
                       deny_file: str | os.PathLike | None = None) -> Path:
    """Every staging rule for one database path. Returns it unchanged if it passes."""
    raw = os.fspath(path)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise DBPathError(f"staging database path {raw!r} must be absolute")
    if ".." in p.parts:
        raise DBPathError(f"staging database path {raw!r} must not contain '..'")
    p = Path(os.path.normpath(p))
    deny = load_deny(deny_file, environ)
    sp = str(p)
    for denied in deny["paths"]:
        if sp == denied or _under(sp, denied):
            raise DBPathError(f"{p} is on the staging deny list ({denied})")
    if sp == os.path.normpath(str(REPO_DEFAULT_DB)):
        raise DBPathError(f"{p} is the checkout's own database.db; staging needs its own file")
    _refuse_symlink_components(p)
    _check_file(p, deny, "staging database")
    for suffix in SIDECAR_SUFFIXES:
        side = Path(sp + suffix)
        try:
            os.lstat(side)
        except FileNotFoundError:
            continue
        _check_file(side, deny, "staging database companion")
    return p


def resolve_db_path(default: str | os.PathLike,
                    environ: Mapping[str, str] | None = None,
                    deny_file: str | os.PathLike | None = None) -> Path:
    """The database path for this process.

    Default role: exactly the old behaviour (FORGE_DB expanded and resolved, else
    `default`). Staging: FORGE_DB required and every check in check_staging_path.
    """
    env = _env(environ)
    raw = (env.get(DB_ENV) or "").strip()
    if not is_staging(env):
        return Path(raw).expanduser().resolve() if raw else Path(default)
    if not raw:
        raise DBPathError(
            f"{ROLE_ENV}=staging requires {DB_ENV}; there is no fallback to database.db")
    return check_staging_path(raw, env, deny_file)


def checked_path(path: str | os.PathLike,
                 environ: Mapping[str, str] | None = None,
                 deny_file: str | os.PathLike | None = None) -> Path:
    """An explicit path (a --db argument): checked in staging, unchanged otherwise."""
    if is_staging(environ):
        return check_staging_path(path, environ, deny_file)
    return Path(path)


# ── Connections ───────────────────────────────────────────────────────────────

def sqlite_uri(path: str | os.PathLike, mode: str = "rw") -> str:
    uri_path = os.fspath(path).replace("\\", "/")
    return f"file:{urllib.parse.quote(uri_path, safe='/:')}?mode={mode}"


def connect_db(path: str | os.PathLike, timeout: float = 60.0,
               environ: Mapping[str, str] | None = None,
               deny_file: str | os.PathLike | None = None, **kwargs) -> sqlite3.Connection:
    """Open a database.

    Staging: re-check the path, then open with mode=rw (never creates a file).
    Default role: plain sqlite3.connect(path), as before.
    """
    if is_staging(environ):
        p = check_staging_path(path, environ, deny_file)
        return sqlite3.connect(sqlite_uri(p, "rw"), uri=True, timeout=timeout, **kwargs)
    return sqlite3.connect(os.fspath(path), timeout=timeout, **kwargs)


# ── Publishing and .env ───────────────────────────────────────────────────────

def refuse_publish_if_staging(what: str = "publishing",
                              environ: Mapping[str, str] | None = None) -> None:
    """Staging never publishes, whatever FORGE_ALLOW_PUBLISH says."""
    if is_staging(environ):
        raise DBPathError(
            f"{what} refused: {ROLE_ENV}=staging never builds, publishes or deploys "
            f"(FORGE_ALLOW_PUBLISH is ignored in staging)")


def dotenv_files(start_dirs: Iterable[str | os.PathLike], filename: str = ".env") -> list[Path]:
    """Every .env that python-dotenv's upward search could find from these folders."""
    found: list[Path] = []
    for start in start_dirs:
        d = Path(os.path.abspath(start))
        for folder in (d, *d.parents):
            candidate = folder / filename
            if (candidate.is_file() or candidate.is_fifo()) and candidate not in found:
                found.append(candidate)
    return found
