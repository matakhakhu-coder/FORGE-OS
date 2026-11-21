"""autopublish pushes approval records only together with a successful build: a failed
build or deploy leaves nothing on origin and the pin retryable (21 Nov 2026 regression:
the Madiba Park pin's record was pushed before a failed build and locked it out)."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import autopublish as apub  # noqa: E402

FAKE_PY = """#!/bin/sh
# stands in for python: checked_signals and pytest pass; publish.py obeys FAKE_BUILD / FAKE_DEPLOY
case "$1" in
  tools/publish.py)
    [ "$FAKE_BUILD" = fail ] && exit 3
    date +built%s%N > dist/index.html
    if [ "$2" = "--deploy" ]; then
      [ "$FORGE_ALLOW_PUBLISH" = 1 ] || exit 0
      git add -f dist && git commit -qm publish || exit 1
      [ "$FAKE_DEPLOY" = fail ] && exit 1
      git push -q origin HEAD:main || exit 1
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
"""


def _g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _g(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _g(tmp_path, "clone", "-q", str(origin), str(work))
    _g(work, "config", "user.email", "t@t")
    _g(work, "config", "user.name", "t")
    _g(work, "checkout", "-q", "-B", "main")
    for d in ("dist", "docs/signals", "docs/approvals/pins"):
        (work / d).mkdir(parents=True)
    (work / "dist/index.html").write_text("old\n")
    (work / "vercel.json").write_text("{}\n")
    (work / "docs/signals/index.json").write_text("[]\n")
    (work / "docs/approvals/pins/.keep").write_text("")
    _g(work, "add", "-f", ".")
    _g(work, "commit", "-qm", "init")
    _g(work, "push", "-q", "origin", "main")
    # the sweep's new batch, uncommitted, as in a real run
    (work / "docs/signals/index.json").write_text('["new-batch"]\n')
    fake = tmp_path / "fakepy"
    fake.write_text(FAKE_PY)
    fake.chmod(0o755)

    monkeypatch.setattr(apub, "ROOT", work)
    monkeypatch.setattr(apub.sa, "active_now", lambda *a, **k: ({"expires_at": "x", "owner": "o"}, None))

    def write(sid, auth_id):   # the real duplicate rule: one record per new pin
        folder = work / "docs/approvals/pins" / sid
        if folder.exists() and any(folder.glob("*.json")):
            raise apub.ap.ApprovalError(f"{sid} already has approval records")
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / "rec.json"
        p.write_text("{}\n")
        return p

    monkeypatch.setattr(apub.ap, "write_standing_record", write)
    monkeypatch.setattr(apub.ap, "verify", lambda enforce: types.SimpleNamespace(ok=True))
    monkeypatch.setattr(apub.ap, "report_lines", lambda r: [])
    monkeypatch.delenv("FAKE_BUILD", raising=False)
    monkeypatch.delenv("FAKE_DEPLOY", raising=False)
    return types.SimpleNamespace(work=work, py=str(fake), db=tmp_path / "db")


def _run(r, dry=False):
    return apub.run("standing-x", ["pin1"], r.db, dry, r.py)


@pytest.mark.parametrize("stage", ["FAKE_BUILD", "FAKE_DEPLOY"])
def test_failure_pushes_nothing_and_retry_publishes(repo, monkeypatch, stage):
    base = _g(repo.work, "rev-parse", "HEAD")
    monkeypatch.setenv(stage, "fail")
    assert _run(repo) != 0
    _g(repo.work, "fetch", "-q", "origin")
    assert _g(repo.work, "rev-parse", "origin/main") == base
    assert _g(repo.work, "rev-parse", "HEAD") == base
    assert not (repo.work / "docs/approvals/pins/pin1").exists()
    assert (repo.work / "dist/index.html").read_text() == "old\n"
    assert (repo.work / "docs/signals/index.json").read_text() == '["new-batch"]\n'

    monkeypatch.delenv(stage)
    assert _run(repo) == 0
    assert _g(repo.work, "ls-tree", "-r", "--name-only", "origin/main", "docs/approvals/pins/pin1")
    assert _g(repo.work, "show", "origin/main:dist/index.html").startswith("built")
    assert _g(repo.work, "show", "origin/main:docs/signals/index.json") == '["new-batch"]'


def test_published_pin_still_refused(repo):
    assert _run(repo) == 0
    assert _run(repo) == 4   # genuine duplicate protection unchanged


def test_dry_run_restores_everything(repo):
    base = _g(repo.work, "rev-parse", "HEAD")
    assert _run(repo, dry=True) == 0
    assert _g(repo.work, "rev-parse", "HEAD") == base
    assert not (repo.work / "docs/approvals/pins/pin1").exists()
    assert (repo.work / "dist/index.html").read_text() == "old\n"
