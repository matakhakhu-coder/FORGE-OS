"""
core/db/paths.py: the staging role and its database path rules.

Every test uses throwaway files under pytest's tmp_path. No real database,
no authority file, no network.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import paths  # noqa: E402

COLLECTORS = ("bi196_collector", "cipc_collector", "civic_intel_collector",
              "civic_intel_collector_us", "saflii_collector")


# ── fixtures ─────────────────────────────────────────────────────────────────────

def _make_db(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    return p


def _deny(tmp: Path, *denied: Path, extra_paths=(), name="deny.json", mode=0o644) -> Path:
    entries = [{"label": d.name, "dev": os.stat(d).st_dev, "ino": os.stat(d).st_ino} for d in denied]
    if not entries:   # an entry that matches nothing, so the list is valid
        entries = [{"label": "none", "dev": -1, "ino": -1}]
    f = tmp / name
    f.write_text(json.dumps({"schema": "forge.staging.deny", "version": 1,
                             "entries": entries,
                             "paths": [str(p) for p in extra_paths]}))
    os.chmod(f, mode)
    return f


@pytest.fixture
def box(tmp_path):
    """A dummy 'authority' and a dummy staging DB, plus a deny list for the former."""
    authority_dir = tmp_path / "authority"
    authority = _make_db(authority_dir / "authority.db")
    staging = _make_db(tmp_path / "staging" / "staging.db")
    deny = _deny(tmp_path, authority, extra_paths=[authority_dir])
    env = {"FORGE_ROLE": "staging", "FORGE_DB": str(staging), "FORGE_DB_DENY_FILE": str(deny)}
    return {"tmp": tmp_path, "authority": authority, "authority_dir": authority_dir,
            "staging": staging, "deny": deny, "env": env}


# ── roles ─────────────────────────────────────────────────────────────────────────

def test_roles():
    assert paths.role({}) == "default"
    assert paths.role({"FORGE_ROLE": "production"}) == "default"
    assert paths.role({"FORGE_ROLE": " Staging "}) == "staging"
    with pytest.raises(paths.DBPathError, match="not a known role"):
        paths.role({"FORGE_ROLE": "stagng"})


def test_default_role_is_unchanged(tmp_path):
    default = tmp_path / "database.db"
    assert paths.resolve_db_path(default, {}) == default
    assert paths.resolve_db_path(default, {"FORGE_DB": "  "}) == default
    other = tmp_path / "other.db"
    assert paths.resolve_db_path(default, {"FORGE_DB": str(other)}) == other.resolve()
    # Default role never requires the file to exist (old behaviour).
    assert not other.exists()


# ── staging path rules ──────────────────────────────────────────────────────────

def test_staging_accepts_its_own_db(box):
    assert paths.resolve_db_path(box["tmp"] / "x.db", box["env"]) == box["staging"]


def test_staging_requires_forge_db(box):
    env = dict(box["env"]); env.pop("FORGE_DB")
    with pytest.raises(paths.DBPathError, match="requires FORGE_DB"):
        paths.resolve_db_path(box["tmp"] / "database.db", env)


def test_staging_missing_file_is_refused_and_not_created(box):
    missing = box["tmp"] / "staging" / "nope.db"
    env = dict(box["env"], FORGE_DB=str(missing))
    with pytest.raises(paths.DBPathError, match="never creates"):
        paths.resolve_db_path(box["tmp"] / "database.db", env)
    with pytest.raises(paths.DBPathError):
        paths.connect_db(missing, environ=env)
    assert not missing.exists()


def test_staging_relative_and_dotdot_refused(box):
    for raw in ("staging.db", str(box["tmp"] / "staging" / ".." / "staging" / "staging.db")):
        with pytest.raises(paths.DBPathError, match="absolute|'\\.\\.'"):
            paths.resolve_db_path(box["tmp"] / "database.db", dict(box["env"], FORGE_DB=raw))


def test_staging_symlinked_file_refused(box):
    link = box["tmp"] / "staging" / "link.db"
    link.symlink_to(box["staging"])
    with pytest.raises(paths.DBPathError, match="symlink"):
        paths.check_staging_path(link, box["env"])


def test_staging_symlinked_folder_refused(box):
    linkdir = box["tmp"] / "linkdir"
    linkdir.symlink_to(box["staging"].parent, target_is_directory=True)
    with pytest.raises(paths.DBPathError, match="symlink"):
        paths.check_staging_path(linkdir / "staging.db", box["env"])


def test_staging_symlink_to_authority_refused(box):
    link = box["tmp"] / "staging" / "sneaky.db"
    link.symlink_to(box["authority"])
    with pytest.raises(paths.DBPathError, match="symlink"):
        paths.check_staging_path(link, box["env"])


def test_staging_hardlink_to_authority_refused(box):
    second = box["tmp"] / "staging" / "second-name.db"
    os.link(box["authority"], second)
    # Two names on disk, and its inode is on the deny list: refused either way.
    with pytest.raises(paths.DBPathError, match="hard links|denied"):
        paths.check_staging_path(second, box["env"])


def test_staging_inode_deny_entry_refused(box, tmp_path):
    # A deny list naming the staging file's own inode (one name on disk).
    deny = _deny(tmp_path, box["staging"], name="deny-staging.json")
    env = dict(box["env"], FORGE_DB_DENY_FILE=str(deny))
    with pytest.raises(paths.DBPathError, match="denied file"):
        paths.check_staging_path(box["staging"], env)


def test_staging_denied_path_refused(box):
    env = dict(box["env"], FORGE_DB=str(box["authority"]))
    with pytest.raises(paths.DBPathError, match="deny list"):
        paths.resolve_db_path(box["tmp"] / "database.db", env)


def test_staging_companion_symlink_refused(box):
    wal = Path(str(box["staging"]) + "-wal")
    wal.symlink_to(Path(str(box["authority"]) + "-wal"))
    with pytest.raises(paths.DBPathError, match="companion"):
        paths.check_staging_path(box["staging"], box["env"])


def test_staging_checkout_database_refused(box, monkeypatch, tmp_path):
    fake_default = _make_db(tmp_path / "checkout" / "database.db")
    monkeypatch.setattr(paths, "REPO_DEFAULT_DB", fake_default)
    with pytest.raises(paths.DBPathError, match="checkout's own database.db"):
        paths.check_staging_path(fake_default, box["env"])


@pytest.mark.parametrize("problem", ["missing", "writable", "empty", "badschema"])
def test_staging_deny_list_fails_closed(box, problem):
    deny = box["tmp"] / f"deny-{problem}.json"
    if problem == "writable":
        deny = _deny(box["tmp"], box["authority"], name=deny.name, mode=0o666)
    elif problem == "empty":
        deny.write_text(json.dumps({"schema": "forge.staging.deny", "version": 1, "entries": []}))
    elif problem == "badschema":
        deny.write_text(json.dumps({"schema": "other", "version": 1, "entries": [{"dev": 1, "ino": 1}]}))
    env = dict(box["env"], FORGE_DB_DENY_FILE=str(deny))
    with pytest.raises(paths.DBPathError, match="deny list"):
        paths.resolve_db_path(box["tmp"] / "database.db", env)


# ── connections ───────────────────────────────────────────────────────────────────

def test_staging_connect_is_rw_and_works(box):
    conn = paths.connect_db(box["staging"], environ=box["env"])
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_staging_connect_after_file_vanishes_creates_nothing(box):
    box["staging"].unlink()
    with pytest.raises(paths.DBPathError):
        paths.connect_db(box["staging"], environ=box["env"])
    assert not box["staging"].exists()


def test_sqlite_uri_mode_rw_never_creates(tmp_path):
    missing = tmp_path / "with space" / "x.db"
    missing.parent.mkdir()
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(paths.sqlite_uri(missing, "rw"), uri=True)
    assert not missing.exists()


def test_default_connect_unchanged(tmp_path):
    p = tmp_path / "new.db"
    paths.connect_db(p, environ={}).close()     # old behaviour: plain connect creates
    assert p.exists()


# ── publishing and .env ─────────────────────────────────────────────────────────

def test_publish_refused_in_staging_whatever_allow_publish_says():
    with pytest.raises(paths.DBPathError, match="FORGE_ALLOW_PUBLISH is ignored"):
        paths.refuse_publish_if_staging(environ={"FORGE_ROLE": "staging", "FORGE_ALLOW_PUBLISH": "1"})
    paths.refuse_publish_if_staging(environ={"FORGE_ALLOW_PUBLISH": "1"})   # default: no refusal


def test_dotenv_files_finds_parent_env(tmp_path):
    (tmp_path / ".env").write_text("FORGE_DB=/somewhere/else.db\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert paths.dotenv_files([deep]) == [tmp_path / ".env"]


# ── every fallback site goes through core.db.paths ────────────────────────────────────

SITES = ["core/db/connection.py", "core/pipeline/ingest.py", "core/web/helpers.py", "app.py",
         "tools/mega_ingest.py", "tools/checked_signals.py"] + \
        [f"forage/collectors/{c}.py" for c in COLLECTORS]


@pytest.mark.parametrize("rel", SITES)
def test_site_resolves_through_paths(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "resolve_db_path" in src, f"{rel} must resolve its DB path through core.db.paths"
    # No hand-rolled FORGE_DB fallback left behind.
    assert 'os.environ["FORGE_DB"]' not in src or rel == "tools/mega_ingest.py", rel


@pytest.mark.parametrize("name", COLLECTORS)
def test_collector_has_no_direct_connect(name):
    tree = ast.parse((ROOT / "forage" / "collectors" / f"{name}.py").read_text(encoding="utf-8"))
    direct = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "connect" and getattr(n.func.value, "id", "") == "sqlite3"]
    assert not direct, f"{name}: sqlite3.connect at lines {direct}; use connect_db"


def _run(code: str, env_extra: dict, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("FORGE_DB", "FORGE_ROLE", "FORGE_DB_DENY_FILE", "FORGE_ALLOW_PUBLISH")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("name", COLLECTORS)
def test_collector_follows_forge_db(name, box):
    """The five collectors used to ignore FORGE_DB. Import only: nothing runs."""
    code = (f"import importlib; m = importlib.import_module('forage.collectors.{name}'); "
            f"print(m.DB_PATH)")
    r = _run(code, box["env"])
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == str(box["staging"])
    r = _run(code, {"FORGE_ROLE": "staging", "FORGE_DB_DENY_FILE": str(box["deny"])})
    assert r.returncode != 0 and "requires FORGE_DB" in r.stderr


def test_mega_ingest_refuses_staging_without_db(box):
    before = (ROOT / "database.db").exists()
    r = subprocess.run([sys.executable, "tools/mega_ingest.py", "--help"], cwd=str(ROOT),
                       env={**{k: v for k, v in os.environ.items() if k != "FORGE_DB"},
                            "FORGE_ROLE": "staging", "FORGE_DB_DENY_FILE": str(box["deny"])},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "refused" in (r.stderr + r.stdout) and "requires FORGE_DB" in (r.stderr + r.stdout)
    assert (ROOT / "database.db").exists() == before


def _mega_function(name: str, staging: bool):
    """Load one mega_ingest function without importing the module (no FMS, no collectors)."""
    src = (ROOT / "tools" / "mega_ingest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    calls = []

    class _Log:
        def __getattr__(self, _):
            return lambda *a, **k: calls.append(a)

    ns = {"STAGING": staging, "log": _Log(), "__name__": "mega_probe"}
    exec(compile(ast.Module([node], []), "mega_ingest_probe", "exec"), ns)
    return ns[name]


def test_mega_ingest_staging_skips_pin_import():
    assert _mega_function("import_checked_signals", True)() == {"status": "skipped", "detail": "staging"}


def test_mega_ingest_staging_refuses_publish(monkeypatch):
    monkeypatch.setenv("FORGE_ALLOW_PUBLISH", "1")
    assert _mega_function("run_publish_deploy", True)() == {"status": "refused", "detail": "staging"}


def test_publish_main_refuses_staging(monkeypatch, capsys):
    import tools.publish as publish
    monkeypatch.setenv("FORGE_ROLE", "staging")
    monkeypatch.setenv("FORGE_ALLOW_PUBLISH", "1")
    monkeypatch.setattr(sys, "argv", ["publish.py", "--deploy"])
    with pytest.raises(SystemExit) as exc:
        publish.main()
    assert exc.value.code == 2
    assert "FORGE_ROLE=staging" in capsys.readouterr().out


def test_app_staging_refuses_dotenv(tmp_path, box):
    """A staging app.py stops if python-dotenv could find a .env (would repoint FORGE_DB)."""
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / ".env").write_text("FORGE_DB=/elsewhere.db\n")
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import app"
    r = _run(code, box["env"], cwd=tmp_path / "work")
    assert r.returncode != 0
    assert ".env file exists" in r.stderr


def test_app_dotenv_cannot_change_role(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / ".env").write_text("FORGE_ROLE=staging\n")
    code = (f"import os, sys; sys.path.insert(0, {str(ROOT)!r}); os.chdir({str(tmp_path / 'work')!r}); "
            f"from dotenv import load_dotenv; import app; print('ROLE=' + repr(os.environ.get('FORGE_ROLE')))")
    r = _run(code, {"FORGE_DB": str(tmp_path / "unused.db")}, cwd=tmp_path / "work")
    # Under `python -c`, python-dotenv searches from the working folder, so this
    # .env is read (override on). app.py must put the role back.
    assert r.returncode == 0, r.stderr[-2000:]
    assert "ROLE=None" in r.stdout
    assert "FORGE_ROLE in .env is ignored" in r.stderr
