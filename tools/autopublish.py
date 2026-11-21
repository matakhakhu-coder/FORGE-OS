"""Publish new checked pins under the owner's standing authorization, unattended.

This is the one run path that turns FORGE_ALLOW_PUBLISH on, and only for the
publish.py process it starts. Nothing here sets it globally.

    python tools/autopublish.py --authorization standing-<id> --db <copy.db> \
        --pins <signal_id> [<signal_id> ...] [--dry-run]

The new pins must already be in docs/signals (a checked batch listed in
docs/signals/index.json, written and checked against docs/SOURCING_POLICY.md by
the sweep that found them). In order, each step refusing stops the run:

 1. On main, not behind origin/main, nothing else staged.
 2. The standing authorization is active now (has an end date, not expired,
    not revoked). See tools/standing_auth.py.
 3. Each pin gets its basis=standing approval record, written only if the
    machine checks pass (stream in scope, at least N different source sites,
    a check method, a new pin rather than a change to a published one).
 4. The ledger verifies, enforced, against origin/main (append-only).
 5. The pins load into the database copy (checked_signals import --write).
 6. The full test suite passes.
 7. --dry-run stops here and removes the records it wrote. Otherwise the
    batch and records are committed and pushed, then publish.py --deploy runs
    with FORGE_ALLOW_PUBLISH=1 in its own environment; Vercel deploys main.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import approvals as ap  # noqa: E402
from tools import standing_auth as sa  # noqa: E402

DATA_PATHS = ("docs/signals", "docs/approvals/pins")


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True).stdout


def _say(msg: str) -> None:
    print(f"[autopublish] {msg}", flush=True)


def run(auth_id: str, pins: list[str], db: pathlib.Path, dry_run: bool, python: str) -> int:
    t0 = time.monotonic()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main" and not dry_run:
        _say(f"REFUSED: on {branch!r}; publish only from main")
        return 3
    _git("fetch", "--quiet", "origin", "main")
    if subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"], cwd=ROOT).returncode:
        _say("REFUSED: origin/main has commits this copy does not; pull first")
        return 3
    staged = [p for p in _git("diff", "--cached", "--name-only").splitlines() if p]
    if staged:
        _say("REFUSED: other changes are staged: " + ", ".join(staged[:10]))
        return 3

    auth, why = sa.active_now(auth_id, ap.standing_dir_for(ap.APPROVALS_DIR))
    if why:
        _say(f"REFUSED: standing authorization {auth_id}: {why}")
        return 3
    _say(f"authorization {auth_id} active until {auth['expires_at']} (owner {auth['owner']})")

    written: list[pathlib.Path] = []
    try:
        for sid in pins:
            written.append(ap.write_standing_record(sid, auth_id))
            _say(f"record {written[-1].relative_to(ROOT)}")
        report = ap.verify(enforce=True)
        for line in ap.report_lines(report):
            _say(line)
        if not report.ok:
            raise RuntimeError("ledger refused: " + ap.refusal_reason(report))

        env = dict(os.environ, FORGE_DB=str(db))
        env.pop("FORGE_ALLOW_PUBLISH", None)
        r = subprocess.run([python, "tools/checked_signals.py", "import", "--write"], cwd=ROOT, env=env)
        if r.returncode:
            raise RuntimeError(f"checked_signals import failed ({r.returncode})")
        r = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", "tests"],
                           cwd=ROOT, env=env)
        if r.returncode:
            raise RuntimeError(f"tests failed ({r.returncode})")
    except (ap.ApprovalError, RuntimeError, sa.StandingError) as exc:
        for p in written:
            p.unlink(missing_ok=True)
            if p.parent.is_dir() and not any(p.parent.iterdir()):
                p.parent.rmdir()
        _say(f"REFUSED: {exc}. Records written by this run removed; nothing committed or pushed.")
        return 4

    if dry_run:
        for p in written:
            p.unlink(missing_ok=True)
            if p.parent.is_dir() and not any(p.parent.iterdir()):
                p.parent.rmdir()
        _say(f"DRY RUN OK in {time.monotonic() - t0:.0f}s: every check passed; records removed, "
             f"nothing committed, pushed or published")
        return 0

    _git("add", "--", *DATA_PATHS)
    _git("commit", "-q", "-m",
         f"Pins published under standing authorization {auth_id} (machine-checked; owner did not "
         f"review the text): " + ", ".join(p[:8] for p in pins))
    _git("push", "--quiet", "origin", "HEAD:main")
    _say("batch and approval records pushed")
    env = dict(os.environ, FORGE_DB=str(db), FORGE_ALLOW_PUBLISH="1")   # this process only
    r = subprocess.run([python, "tools/publish.py", "--deploy"], cwd=ROOT, env=env)
    _say(f"publish.py --deploy exited {r.returncode} after {time.monotonic() - t0:.0f}s")
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--authorization", required=True)
    p.add_argument("--pins", nargs="+", required=True, help="signal_ids of the new pins")
    p.add_argument("--db", type=pathlib.Path, required=True, help="database copy to load and publish from")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    return run(a.authorization, a.pins, a.db.resolve(), a.dry_run, sys.executable)


if __name__ == "__main__":
    sys.exit(main())
