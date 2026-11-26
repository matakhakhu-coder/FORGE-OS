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
 2b. Every pin has a valid "What each fact rests on" entry in
    docs/signals/fact-bases.json (tools/fact_bases.py, docs/signals/FACT_BASES.md):
    audited, six facts, each tied to one of the pin's own published sources
    with a word-for-word quote, honest bases, and an approximate map point
    said so in the Place fact. Any pin without one refuses the whole run, so
    no pin can go live with that section empty.
 3. Each pin gets its basis=standing approval record, written only if the
    machine checks pass (stream in scope, at least N different source sites,
    a check method, a new pin rather than a change to a published one).
 4. The ledger verifies, enforced, against origin/main (append-only).
 5. The pins load into the database copy (checked_signals import --write).
 6. The full test suite passes.
 7. The site builds: publish.py without --deploy and without
    FORGE_ALLOW_PUBLISH, so nothing can be pushed by it.
 8. --dry-run stops here, removes the records it wrote and restores dist/.
    Otherwise the batch and records are committed locally only, then
    publish.py --deploy runs with FORGE_ALLOW_PUBLISH=1 in its own environment
    and pushes that commit and the dist/ commit to main in one push.
 5b. Location photos (docs/signals/LOCATION_PHOTOS.md), before the tests and
    the build: every new pin gets a Commons search for licensed photos of its
    place within 2 km, written to docs/photos/candidates/ and committed with
    the batch, with one report line per pin. A place photo the sweep approved
    itself (approval.by: standing, after its own look at the photo, owner's
    choice 26 Nov 2026) must pass every rule and belong to one of this run's
    pins; otherwise it goes back to candidate and never shows. None of this
    ever blocks or delays a pin.
 9. The run checks origin/main holds the records commit. If it does not, the
    local commits are undone, the records removed and dist/ restored, so
    nothing reached origin and the pins stay retryable.

Approval records reach origin only together with a successful build, so a
failed build can never leave a pushed record that blocks a retry. The
one-record-per-new-pin rule in approvals.write_standing_record is unchanged:
a pin whose record is on origin still needs the owner.
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
from tools import fact_bases as fb  # noqa: E402

DATA_PATHS = ("docs/signals", "docs/approvals/pins", "docs/photos/candidates", "docs/photos/files")


def _check_run_photos(auth_id: str, pins: list[str], db: pathlib.Path) -> list[str]:
    """Run-approved photos (approval.by: standing) must pass every rule and
    belong to this run's pins; any that do not go back to candidate, so they
    never show. Never stops the run."""
    try:
        from tools import location_photos as lp
        reg = lp.load_registry()
        pin_data = lp._pins_from_db(str(db)) if db.exists() else {}
        bad = {ln.split(":", 1)[0]: ln for ln in lp.standing_photo_problems(reg, auth_id, pins)}
        ok = []
        for p in reg["photos"]:
            a = p.get("approval") or {}
            if p.get("status") != "approved" or a.get("by") != "standing" or p.get("id") in bad:
                continue
            if p.get("signal_id") not in pins:
                continue
            # The pin may not be published in the db yet: check against the batch's point.
            pin = pin_data.get(p["signal_id"]) or _pin_point(p["signal_id"], db)
            errs = lp.validate_photo(p, {p["signal_id"]: pin} if pin else None)
            if errs:
                bad[p["id"]] = errs[0]
            else:
                ok.append(f"location photos: {p['id']} approved by this run for {p['signal_id'][:8]}")
        if bad:
            lp.demote(lp.REGISTRY, set(bad))
        return ok + [f"location photos: {i} refused, back to candidate ({why}); pin publishes without it"
                     for i, why in bad.items()]
    except Exception as exc:   # noqa: BLE001
        return [f"location photos: run-approved photo check failed ({type(exc).__name__})"]


def _pin_point(sid: str, db: pathlib.Path) -> dict | None:
    import json
    import sqlite3
    try:
        con = sqlite3.connect(str(db))
        row = con.execute("SELECT title, metadata_json, lat, lng FROM signals WHERE signal_id=?", (sid,)).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    if not row or row[2] is None:
        return None
    chk = (json.loads(row[1] or "{}") or {}).get("checked") or {}
    return {"title": row[0], "lat": row[2], "lng": row[3], "location_note": chk.get("location")}


def _photo_research(pins: list[str], db: pathlib.Path) -> tuple[list[pathlib.Path], list[str]]:
    """Step 5b. Never raises: a pin always goes out, with or without a photo."""
    if os.environ.get("FORGE_SKIP_PHOTO_RESEARCH") == "1":
        return [], ["location photos: research skipped (FORGE_SKIP_PHOTO_RESEARCH=1)"]
    try:
        import json
        import sqlite3
        from tools import location_photos as lp
        con = sqlite3.connect(str(db))
        found = {}
        for sid, title, meta, lat, lng in con.execute(
                "SELECT signal_id, title, metadata_json, lat, lng FROM signals WHERE signal_id IN (%s)"
                % ",".join("?" * len(pins)), pins):
            if lat is None or lng is None:
                continue
            chk = (json.loads(meta or "{}") or {}).get("checked") or {}
            found[sid] = {"title": title, "lat": lat, "lng": lng, "location_note": chk.get("location")}
        con.close()
        written, lines = lp.research_new_pins(found)
        lines += [f"{s[:8]}: no map point in the database; no photo research" for s in pins if s not in found]
        return written, ["location photos: " + ln for ln in lines]
    except Exception as exc:   # noqa: BLE001
        return [], [f"location photos: research did not run ({type(exc).__name__}); pins publish without photos"]


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True).stdout


def _say(msg: str) -> None:
    print(f"[autopublish] {msg}", flush=True)


def _restore_dist() -> None:
    _git("checkout", "--", "dist", "vercel.json", check=False)
    _git("clean", "-fdq", "--", "dist/pins", "dist/archive", check=False)


def _remove(written: list[pathlib.Path]) -> None:
    for p in written:
        p.unlink(missing_ok=True)
        if p.parent.is_dir() and not any(p.parent.iterdir()):
            p.parent.rmdir()


def _on_origin(sha: str) -> bool:
    _git("fetch", "--quiet", "origin", "main", check=False)
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"],
                          cwd=ROOT, capture_output=True).returncode == 0


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

    fact_errs = fb.gate_problems(pins)
    if fact_errs:
        for e in fact_errs:
            _say(f"fact bases: {e}")
        _say("REFUSED: 'What each fact rests on' is missing or invalid for a pin; nothing written. "
             "Write its entry (docs/signals/FACT_BASES.md) and run again.")
        return 3
    _say(f"fact bases: {len(pins)} pin(s) have a valid 'What each fact rests on' entry")

    base = _git("rev-parse", "HEAD").strip()
    written: list[pathlib.Path] = []
    built = False
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
        photo_files, photo_lines = _photo_research(pins, db)
        written.extend(photo_files)
        for line in photo_lines + _check_run_photos(auth_id, pins, db):
            _say(line)
        r = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", "tests"],
                           cwd=ROOT, env=env)
        if r.returncode:
            raise RuntimeError(f"tests failed ({r.returncode})")
        built = True   # from here dist/ may be rewritten
        r = subprocess.run([python, "tools/publish.py"], cwd=ROOT, env=env)   # build only, no push
        if r.returncode:
            raise RuntimeError(f"site build failed ({r.returncode})")
    except (ap.ApprovalError, RuntimeError, sa.StandingError) as exc:
        _remove(written)
        if built:
            _restore_dist()
        _say(f"REFUSED: {exc}. Records written by this run removed; nothing committed or pushed; "
             f"the pins can be retried.")
        return 4

    if dry_run:
        _remove(written)
        _restore_dist()
        _say(f"DRY RUN OK in {time.monotonic() - t0:.0f}s: every check passed and the site built; "
             f"records removed, dist/ restored, nothing committed, pushed or published")
        return 0

    _git("add", "--", *[d for d in DATA_PATHS if (ROOT / d).exists()])
    _git("commit", "-q", "-m",
         f"Pins published under standing authorization {auth_id} (machine-checked; owner did not "
         f"review the text): " + ", ".join(p[:8] for p in pins))
    records_commit = _git("rev-parse", "HEAD").strip()
    _say("batch and approval records committed locally (not pushed yet)")
    env = dict(os.environ, FORGE_DB=str(db), FORGE_ALLOW_PUBLISH="1")   # this process only
    r = subprocess.run([python, "tools/publish.py", "--deploy"], cwd=ROOT, env=env)
    _say(f"publish.py --deploy exited {r.returncode} after {time.monotonic() - t0:.0f}s")
    if _on_origin(records_commit):
        _say("batch, approval records and dist/ are on origin/main")
        return r.returncode
    _git("reset", "-q", "--mixed", base)
    _remove(written)
    _restore_dist()
    _say("REFUSED: the deploy did not push; local commits undone, records removed, dist/ restored. "
         "Nothing reached origin; the pins can be retried.")
    return r.returncode or 5


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
