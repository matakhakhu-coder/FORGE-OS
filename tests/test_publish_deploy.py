"""The push step of tools/publish.py (_git_deploy).

Plain `git add dist/` failed on a machine whose global git ignore lists
dist/, so a normal publish built the site and then could not push it.
These tests run the deploy against a throwaway repo and a local bare
remote, with a global ignore that lists dist/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.publish as pub  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A working copy on main with a pushed first publish, then a machine-wide
    ignore list that includes dist/ (like the one that broke the push)."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "none.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    w = tmp_path / "w"
    _git(tmp_path, "init", "-q", "-b", "main", str(w))
    _git(w, "config", "user.email", "t@example.org")
    _git(w, "config", "user.name", "t")
    (w / ".gitignore").write_text("*.log\n.env\n")
    (w / "dist").mkdir()
    (w / "dist" / "index.html").write_text("v1")
    (w / "dist" / "old.html").write_text("old")
    (w / "vercel.json").write_text("{}")
    (w / "README.md").write_text("r")
    _git(w, "add", ".")
    _git(w, "commit", "-qm", "init")
    _git(w, "remote", "add", "origin", str(remote))
    _git(w, "push", "-q", "origin", "main")
    # now the machine-wide ignore that lists dist/
    gignore = tmp_path / "global-ignore"
    gignore.write_text("dist/\n*.tmp\n")
    (tmp_path / "none.gitconfig").write_text(f"[core]\n\texcludesFile = {gignore.as_posix()}\n")
    monkeypatch.setattr(pub, "ROOT", w)
    monkeypatch.setattr(pub, "VERCEL_DEPLOY_HOOK", "")
    monkeypatch.setenv("FORGE_ALLOW_PUBLISH", "1")
    monkeypatch.delenv("FORGE_ROLE", raising=False)
    return w, remote


def _build_v2(w):
    (w / "dist" / "index.html").write_text("v2")
    (w / "dist" / "old.html").unlink()
    (w / "dist" / "cases").mkdir()
    (w / "dist" / "cases" / "new.html").write_text("new")
    (w / "dist" / "debug.log").write_text("noise")       # ignored by the repo's own .gitignore


def test_plain_git_add_fails_here(repo):
    """The failure being fixed, reproduced."""
    w, _ = repo
    _build_v2(w)
    r = subprocess.run(["git", "add", "dist/", "vercel.json"], cwd=w, capture_output=True, text=True)
    assert r.returncode != 0 and "ignored" in r.stderr


def test_deploy_pushes_new_changed_and_deleted_files(repo):
    w, remote = repo
    _build_v2(w)
    pub._git_deploy("2026-10-24 01:00 UTC")
    pushed = set(_git(remote, "ls-tree", "-r", "--name-only", "main").split())
    assert {"dist/index.html", "dist/cases/new.html", "vercel.json"} <= pushed
    assert "dist/old.html" not in pushed
    assert "dist/debug.log" not in pushed                    # the repo's .gitignore still decides
    assert _git(remote, "show", "main:dist/index.html") == "v2"
    assert _git(remote, "log", "-1", "--format=%s", "main").strip() == "publish: 2026-10-24 01:00"
    # only site files are in the publish commit
    changed = _git(w, "show", "--name-only", "--format=", "HEAD").split()
    assert all(p.startswith("dist/") or p == "vercel.json" for p in changed)


def test_deploy_leaves_other_unstaged_work_alone(repo):
    w, remote = repo
    _build_v2(w)
    (w / "README.md").write_text("edited, not staged")
    (w / "notes.txt").write_text("untracked")
    pub._git_deploy("2026-10-24 01:00 UTC")
    pushed = set(_git(remote, "ls-tree", "-r", "--name-only", "main").split())
    assert "notes.txt" not in pushed
    assert _git(remote, "show", "main:README.md") == "r"


def test_deploy_refuses_when_other_changes_are_staged(repo, capsys):
    w, remote = repo
    _build_v2(w)
    (w / "README.md").write_text("staged")
    _git(w, "add", "README.md")
    before = _git(remote, "rev-parse", "main")
    with pytest.raises(SystemExit) as exc:
        pub._git_deploy("2026-10-24 01:00 UTC")
    assert exc.value.code == 4
    assert "README.md" in capsys.readouterr().out
    assert _git(remote, "rev-parse", "main") == before


def test_deploy_refuses_off_main(repo):
    w, remote = repo
    _git(w, "switch", "-q", "-c", "feature")
    _build_v2(w)
    before = _git(remote, "rev-parse", "main")
    with pytest.raises(SystemExit) as exc:
        pub._git_deploy("2026-10-24 01:00 UTC")
    assert exc.value.code == 4
    assert _git(remote, "rev-parse", "main") == before


def test_deploy_refuses_when_behind_origin(repo, tmp_path):
    w, remote = repo
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "o@example.org")
    _git(other, "config", "user.name", "o")
    (other / "README.md").write_text("someone else's work")
    _git(other, "commit", "-qam", "other work")
    _git(other, "push", "-q", "origin", "main")
    theirs = _git(remote, "rev-parse", "main")
    _build_v2(w)
    with pytest.raises(SystemExit) as exc:
        pub._git_deploy("2026-10-24 01:00 UTC")
    assert exc.value.code == 4
    assert _git(remote, "rev-parse", "main") == theirs


def test_no_change_means_no_commit(repo):
    w, remote = repo
    before = _git(remote, "rev-parse", "main")
    pub._git_deploy("2026-10-24 01:00 UTC")
    assert _git(remote, "rev-parse", "main") == before


def test_deploy_stays_blocked_without_permission(repo, monkeypatch):
    w, remote = repo
    monkeypatch.delenv("FORGE_ALLOW_PUBLISH")
    _build_v2(w)
    before = _git(remote, "rev-parse", "main")
    pub._git_deploy("2026-10-24 01:00 UTC")
    assert _git(remote, "rev-parse", "main") == before
    assert _git(w, "diff", "--cached", "--name-only") == ""
