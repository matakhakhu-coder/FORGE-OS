"""
tools/approvals.py: the pin approval ledger.

Each test builds a throwaway git repo under tmp_path holding a copy of
docs/signals and an approvals folder. No real database, no network, and the
real docs/signals and docs/approvals are only read.

The `repo` fixture copies only the ledger's rules (README, schema, example)
and writes its own policy.json with "enforce": false, so the mechanics are
tested from an empty, unenforced ledger with synthetic records whatever the
committed policy says. Tests of enforced behaviour turn it on explicitly
(enforce=True, the environment, or by writing the fixture's policy). The
`real_repo` fixture copies the committed ledger as it stands, records and
policy included, and the tests on it check what the real ledger holds.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import approvals as ap  # noqa: E402
from tools import checked_signals as cs  # noqa: E402

REAL_SIGNALS = ROOT / "docs" / "signals"
REAL_LEDGER = ROOT / "docs" / "approvals" / "pins"


# ── helpers ───────────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo.parent)})
    assert r.returncode == 0, r.stderr
    return r.stdout


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", msg)


def _publish_base(repo: Path) -> None:
    """Pretend HEAD is what origin/main holds."""
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


LEDGER_RULES = ("README.md", "schema", "_examples")   # policy.json: see _write_policy


def _write_policy(ledger: Path, *, enforce: bool) -> None:
    """The committed policy.json with an explicit enforce flag, for a fixture ledger."""
    data = json.loads((REAL_LEDGER / "policy.json").read_text())
    data["enforce"] = enforce
    (ledger / "policy.json").write_text(json.dumps(data, indent=2))


def _real_record_files() -> list[Path]:
    return sorted(p for p in REAL_LEDGER.glob("*/*.json")
                  if p.parent.name not in ("schema", "_examples"))


def _make_repo(tmp_path: Path, *, with_records: bool) -> Path:
    r = tmp_path / "repo"
    shutil.copytree(REAL_SIGNALS, r / "docs" / "signals")
    ledger = r / "docs" / "approvals" / "pins"
    if with_records:
        shutil.copytree(REAL_LEDGER, ledger)
    else:
        ledger.mkdir(parents=True)
        for name in LEDGER_RULES:
            src = REAL_LEDGER / name
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, ledger / name)
        _write_policy(ledger, enforce=False)
    _git(r.parent, "init", "-q", str(r))
    _commit(r, "base")
    _publish_base(r)
    return r


@pytest.fixture
def repo(tmp_path):
    """docs/signals plus the ledger's rules only: no approval records, policy enforce=false."""
    return _make_repo(tmp_path, with_records=False)


@pytest.fixture
def real_repo(tmp_path):
    """docs/signals plus the committed ledger, records and policy included."""
    return _make_repo(tmp_path, with_records=True)


def _index(repo: Path) -> Path:
    return repo / "docs" / "signals" / "index.json"


def _ledger(repo: Path) -> Path:
    return repo / "docs" / "approvals" / "pins"


def _pins(repo: Path):
    loaded, by_id = ap._batch_pins(_index(repo))
    return [(p["signal_id"], by_id[p["signal_id"]][1]) for p in loaded.active]


def _pin(repo: Path, sid: str) -> tuple[dict, str]:
    return ap._batch_pins(_index(repo))[1][sid]


def _record(repo: Path, sid: str, *, version=1, action="approve", supersedes=None,
            approval_id="20261021T013000Z-0000000a", basis="explicit", payload=None,
            approved_at="2026-10-21T03:30:00+02:00", diff=None) -> dict:
    pin, batch = _pin(repo, sid)
    data = action != "revoke"
    body = ap.approved_payload(payload if payload is not None else pin)
    return {
        "schema": ap.RECORD_SCHEMA,
        "approval_id": approval_id,
        "signal_id": sid,
        "action": action,
        "pin_version": version,
        "supersedes": supersedes,
        "batch_file": batch if data else None,
        "approved_payload": body if data else None,
        "payload_sha256": ap.payload_sha256(body) if data else None,
        "source_hashes": [{"url": "https://example.org/source", "retrieved_at": None,
                           "content_sha256": None}] if data else [],
        "diff_from_previous": diff,
        "owner": "test-owner",
        "approved_at": approved_at,
        "recorded_at": "2026-10-21T03:31:00+02:00",
        "recorded_by": "test-agent",
        "approval_reference": {"id": "test-0001", "summary": "Test approval."},
        "basis": basis,
        "scope": "data-only",
    }


def _write(repo: Path, rec: dict) -> Path:
    d = _ledger(repo) / rec["signal_id"]
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{rec['approval_id']}.json"
    f.write_text(json.dumps(rec, indent=2))
    return f


def _verify(repo: Path, **kw) -> ap.Report:
    return ap.verify(_index(repo), _ledger(repo), repo, **kw)


def _codes(report: ap.Report, kind: str) -> set[str]:
    return {f.code for f in report.findings if f.kind == kind}


def _edit_pin(repo: Path, sid: str, **changes) -> None:
    _, batch = _pin(repo, sid)
    f = repo / "docs" / "signals" / batch
    data = json.loads(f.read_text())
    for p in data["signals"]:
        if p["signal_id"] == sid:
            p.update(changes)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    cs.stamp_files(_index(repo))


@pytest.fixture(autouse=True)
def _no_env_enforce(monkeypatch):
    monkeypatch.delenv(ap.ENFORCE_ENV, raising=False)


# ── the real repo files ───────────────────────────────────────────────────────────────

def test_real_ledger_approves_every_active_pin_and_is_enforced():
    report = ap.verify(check_git=False)
    assert report.enforce                              # policy.json says true
    assert report.records == len(_real_record_files()) == 40
    assert report.pins_checked == 39
    assert report.approved_pins == report.pins_checked
    assert not report.violations and not report.gaps, [f.line() for f in report.findings]
    assert report.ok


def test_committed_policy_enforces_and_env_cannot_loosen(monkeypatch):
    findings: list = []
    assert ap.load_policy(REAL_LEDGER, findings) is True
    assert findings == []                              # a well-formed policy, not a broken one
    monkeypatch.setenv(ap.ENFORCE_ENV, "0")
    assert ap.verify(check_git=False).enforce


def test_committed_policy_refuses_a_pin_without_a_record(repo, monkeypatch):
    """The committed policy, dropped into an empty ledger, refuses every gap."""
    shutil.copy2(REAL_LEDGER / "policy.json", _ledger(repo) / "policy.json")
    monkeypatch.setenv(ap.ENFORCE_ENV, "0")
    r = _verify(repo, check_git=False)
    assert r.enforce and not r.violations
    assert len(r.gaps) == r.pins_checked and not r.ok
    assert "gap(s) with enforcement on" in ap.refusal_reason(r)


def test_real_legacy_records_match_the_owner_confirmation():
    """One legacy version-1 record per active pin, as the owner confirmed on 21 Oct 2026."""
    loaded, by_id = ap._batch_pins(REAL_SIGNALS / "index.json")
    active = {p["signal_id"] for p in loaded.active}
    first, later = {}, {}
    for f in _real_record_files():
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec["pin_version"] > 1:
            later.setdefault(rec["signal_id"], []).append(rec)
        if rec["pin_version"] == 1:
            assert rec["signal_id"] not in first, f"two version-1 records for {rec['signal_id']}"
            first[rec["signal_id"]] = rec
    new_pins = {sid for sid, rec in first.items() if rec["basis"] == "explicit"}
    assert new_pins == {"b842a3b5-eda7-5b97-bfe1-0448fe0272a5",   # Gqeberha, approved in chat 21 Oct 2026
                        "048440e0-0be5-53d8-934a-1ba6233a0dfa"}   # Steenberg, approved in chat 21 Oct 2026
    assert set(first) == active
    for sid, rec in first.items():
        if sid in new_pins:
            continue
        pin, batch = by_id[sid]
        assert rec["action"] == "approve" and rec["supersedes"] is None
        assert rec["basis"] == "legacy" and rec["scope"] == "data-only"
        assert rec["owner"] == "Riley Bishop" and rec["recorded_by"] == "Maya"
        assert rec["approval_reference"]["id"] == "confirmed-by-user-in-session-2026-10-21"
        assert rec["approval_reference"]["summary"].startswith(
            "confirmed by user in session on October 21")
        assert "approximate" in rec["approval_reference"]["summary"]
        # The owner gave the time as about 03:40 SAST; the record was made later.
        assert rec["approved_at"] == "2026-10-21T03:40:00+02:00"
        assert ap._iso_tz(rec["recorded_at"]) > ap._iso_tz(rec["approved_at"])
        assert rec["batch_file"] == batch
        # Unknown retrieval times and page hashes stay null.
        assert all(s["retrieved_at"] is None and s["content_sha256"] is None
                   for s in rec["source_hashes"])
        if sid in later:
            continue        # the pin changed after this approval; a later record covers it
        # Otherwise the record approves exactly the committed pin, with its own URLs.
        assert rec["payload_sha256"] == ap.payload_sha256(pin)
        assert rec["approved_payload"] == ap.approved_payload(pin)
        urls = [s["url"] for s in pin["metadata"]["checked"]["sources"]]
        assert [s["url"] for s in rec["source_hashes"]] == list(dict.fromkeys(urls))


CAFDA = "7aa4eea1-c1f4-55af-b761-d55ff2df3a51"
CAFDA_IOL = ("https://iol.co.za/capetimes/2026-10-19-retreat-community-health-centre-"
             "reopens-after-fatal-shooting-nearby2/")


def test_cafda_source_change_is_approved_as_version_2():
    """The owner approved adding the IOL Cape Times article after the legacy push."""
    recs = sorted((json.loads(f.read_text(encoding="utf-8"))
                   for f in (REAL_LEDGER / CAFDA).glob("*.json")),
                  key=lambda r: r["pin_version"])
    assert [r["pin_version"] for r in recs] == [1, 2]
    v1, v2 = recs
    assert v1["basis"] == "legacy" and v1["approved_payload"]["content"].endswith("One report only.")
    assert v2["action"] == "supersede" and v2["supersedes"] == v1["approval_id"]
    assert v2["basis"] == "explicit" and v2["scope"] == "data-only"
    assert v2["owner"] == "Riley Bishop" and v2["recorded_by"] == "Maya"
    assert ap._iso_tz(v2["approved_at"]) > ap._iso_tz(v1["recorded_at"])
    loaded, by_id = ap._batch_pins(REAL_SIGNALS / "index.json")
    pin, _ = by_id[CAFDA]
    assert v2["payload_sha256"] == ap.payload_sha256(pin)
    urls = [s["url"] for s in pin["metadata"]["checked"]["sources"]]
    assert urls == [v1["source_hashes"][0]["url"], CAFDA_IOL]
    assert "One report only." not in pin["content"]
    assert v2["source_hashes"][1]["content_sha256"].startswith("sha256:d80bb94e")
    prov = json.loads((REAL_SIGNALS / "provenance.json").read_text(encoding="utf-8"))
    assert prov["pins"][CAFDA]["independence"] == "same_statement"
    assert prov["pins"][CAFDA]["spokesperson"] == ["Wesley Twigg"]      # no rank


def test_real_records_catch_an_edited_pin(real_repo):
    assert _verify(real_repo, enforce=True).ok
    sid, _ = _pins(real_repo)[0]
    _edit_pin(real_repo, sid, title="Edited after approval")
    r = _verify(real_repo)
    assert not r.ok and "hash-mismatch" in _codes(r, "violation")


def test_real_records_are_append_only_once_on_main(real_repo):
    f = sorted(p for p in _ledger(real_repo).glob("*/*.json")
               if p.parent.name not in ("schema", "_examples"))[0]
    rec = json.loads(f.read_text()); rec["owner"] = "someone-else"
    f.write_text(json.dumps(rec, indent=2))
    r = _verify(real_repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_example_record_is_valid_and_skipped(repo):
    ex = json.loads((REAL_LEDGER / "_examples" / "example.json").read_text())
    assert ap.validate_record(ex, signal_id=ex["signal_id"], approval_id=ex["approval_id"]) == []
    assert _verify(repo, check_git=False).records == 0       # the rules-only ledger
    assert ex["signal_id"] not in {p.parent.name for p in _real_record_files()}


def test_schema_file_matches_validator():
    schema = json.loads((REAL_LEDGER / "schema" / "pin-approval.v1.json").read_text())
    assert tuple(schema["required"]) == ap.RECORD_FIELDS
    assert set(schema["properties"]) == set(ap.RECORD_FIELDS)
    assert schema["properties"]["scope"]["const"] == "data-only"
    assert set(schema["properties"]["approved_payload"]["properties"]) == set(cs.FIELDS)
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema)
    ex = json.loads((REAL_LEDGER / "_examples" / "example.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(ex)


def test_payload_hash_is_the_pin_fingerprint(repo):
    sid, _ = _pins(repo)[0]
    pin, _ = _pin(repo, sid)
    body = ap.approved_payload(pin)
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    import hashlib
    assert ap.payload_sha256(pin) == "sha256:" + hashlib.sha256(raw).hexdigest() == cs.fingerprint(pin)


# ── policy and rollout ─────────────────────────────────────────────────────────────────

def test_gaps_refuse_only_when_enforced(repo, monkeypatch):
    r = _verify(repo)
    assert len(r.gaps) == r.pins_checked     # empty ledger: every pin is a gap
    assert r.ok
    assert not _verify(repo, enforce=True).ok
    monkeypatch.setenv(ap.ENFORCE_ENV, "1")
    assert not _verify(repo).ok


def test_policy_enforce_true_and_env_cannot_loosen(repo, monkeypatch):
    pol = _ledger(repo) / "policy.json"
    data = json.loads(pol.read_text()); data["enforce"] = True
    pol.write_text(json.dumps(data))
    monkeypatch.setenv(ap.ENFORCE_ENV, "0")
    r = _verify(repo)
    assert r.enforce and not r.ok
    assert "append-only" not in _codes(r, "violation")     # policy.json is editable


@pytest.mark.parametrize("broken", ["missing", "garbage", "no-flag"])
def test_broken_policy_is_a_violation_and_enforces(repo, broken):
    pol = _ledger(repo) / "policy.json"
    if broken == "missing":
        pol.unlink()
    elif broken == "garbage":
        pol.write_text("{not json")
    else:
        pol.write_text(json.dumps({"schema": ap.POLICY_SCHEMA, "version": 1}))
    r = _verify(repo, check_git=False)
    assert r.enforce and not r.ok
    assert _codes(r, "violation") & {"policy-missing", "policy-invalid"}


# ── records and chains ─────────────────────────────────────────────────────────────────

def test_valid_approval_counts(repo):
    sid, _ = _pins(repo)[0]
    _write(repo, _record(repo, sid))
    r = _verify(repo)
    assert r.ok and r.records == 1 and r.approved_pins == 1
    assert not any(f.signal_id == sid for f in r.gaps)


def test_every_pin_approved_passes_enforcement(repo):
    for i, (sid, _) in enumerate(_pins(repo)):
        _write(repo, _record(repo, sid, basis="legacy", approved_at=None,
                             approval_id=f"20261021T013000Z-{i:08x}"))
    r = _verify(repo, enforce=True)
    assert r.ok, [f.line() for f in r.findings]
    assert r.approved_pins == r.pins_checked


def test_hash_mismatch_is_a_violation_even_unenforced(repo):
    sid, _ = _pins(repo)[0]
    _write(repo, _record(repo, sid))
    _edit_pin(repo, sid, title="Edited after approval")
    r = _verify(repo)
    assert not r.ok and "hash-mismatch" in _codes(r, "violation")


def test_record_with_tampered_hash_is_invalid(repo):
    sid, _ = _pins(repo)[0]
    rec = _record(repo, sid)
    rec["payload_sha256"] = "sha256:" + "0" * 64
    _write(repo, rec)
    r = _verify(repo)
    assert not r.ok and "record-invalid" in _codes(r, "violation")


def test_supersede_chain(repo):
    sid, _ = _pins(repo)[0]
    pin, _ = _pin(repo, sid)
    v1 = _record(repo, sid)
    _write(repo, v1)
    new_pin = dict(copy.deepcopy(pin), title="Corrected title")
    _edit_pin(repo, sid, title="Corrected title")
    v2 = _record(repo, sid, version=2, action="supersede", supersedes=v1["approval_id"],
                 approval_id="20261021T020000Z-0000000b", payload=new_pin,
                 diff={"title": {"from": pin["title"], "to": "Corrected title"}})
    _write(repo, v2)
    r = _verify(repo)
    assert r.ok and r.approved_pins == 1, [f.line() for f in r.findings]


@pytest.mark.parametrize("fault", ["skip-version", "wrong-parent", "approve-over-live",
                                   "earlier-id", "first-not-approve"])
def test_chain_faults(repo, fault):
    sid, _ = _pins(repo)[0]
    v1 = _record(repo, sid)
    later = dict(version=2, action="supersede", supersedes=v1["approval_id"],
                 approval_id="20261021T020000Z-0000000b", diff={"note": "same data"})
    if fault == "skip-version":
        later["version"] = 3
    elif fault == "wrong-parent":
        later["supersedes"] = "20261021T010000Z-deadbeef"
    elif fault == "approve-over-live":
        later["action"] = "approve"
    elif fault == "earlier-id":
        later["approval_id"] = "20261021T000000Z-0000000b"
    if fault == "first-not-approve":
        v1 = _record(repo, sid, action="supersede", diff=None)
        _write(repo, v1)
    else:
        _write(repo, v1)
        _write(repo, _record(repo, sid, **later))
    r = _verify(repo)
    assert not r.ok
    assert _codes(r, "violation") & {"chain-broken", "record-invalid"}


def test_revoked_pin_still_active_is_refused(repo):
    sid, _ = _pins(repo)[0]
    v1 = _record(repo, sid)
    _write(repo, v1)
    _write(repo, _record(repo, sid, version=2, action="revoke", supersedes=v1["approval_id"],
                         approval_id="20261021T020000Z-0000000b"))
    r = _verify(repo)
    assert not r.ok and "revoked-active" in _codes(r, "violation")


def test_revoked_and_retracted_is_fine(repo):
    sid, batch = _pins(repo)[0]
    v1 = _record(repo, sid)
    _write(repo, v1)
    _write(repo, _record(repo, sid, version=2, action="revoke", supersedes=v1["approval_id"],
                         approval_id="20261021T020000Z-0000000b"))
    f = repo / "docs" / "signals" / batch
    data = json.loads(f.read_text())
    data.setdefault("retractions", []).append(
        {"signal_id": sid, "retracted_at": "2026-10-21T03:40:00+02:00", "reason": "test retraction"})
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    cs.stamp_files(_index(repo))
    r = _verify(repo)
    assert "revoked-active" not in _codes(r, "violation"), [f.line() for f in r.findings]
    assert r.ok


def test_reapprove_after_revoke(repo):
    sid, _ = _pins(repo)[0]
    v1 = _record(repo, sid)
    v2 = _record(repo, sid, version=2, action="revoke", supersedes=v1["approval_id"],
                 approval_id="20261021T020000Z-0000000b")
    v3 = _record(repo, sid, version=3, action="approve", supersedes=v2["approval_id"],
                 approval_id="20261021T030000Z-0000000c", diff={"note": "re-approved"})
    for rec in (v1, v2, v3):
        _write(repo, rec)
    r = _verify(repo)
    assert r.ok and r.approved_pins == 1, [f.line() for f in r.findings]


def test_absent_pin_is_flagged_as_warning(repo):
    sid, _ = _pins(repo)[0]
    rec = _record(repo, sid)
    ghost = "11111111-2222-4333-8444-555555555555"
    rec["signal_id"] = ghost
    rec["approved_payload"]["signal_id"] = ghost
    rec["payload_sha256"] = ap.payload_sha256(rec["approved_payload"])
    _write(repo, rec)
    r = _verify(repo)
    assert "absent-pin" in _codes(r, "warning")
    assert r.ok


@pytest.mark.parametrize("field,value,expect", [
    ("scope", "publish", "scope must be"),
    ("owner", "someone@example.com", "pseudonym"),
    ("approved_at", None, "legacy"),
    ("approved_at", "2026-10-21T03:30:00", "zone"),
    ("approval_reference", {"id": "x", "summary": "line one\nline two"}, "one line"),
    ("source_hashes", [], "sources"),
])
def test_field_rules(repo, field, value, expect):
    sid, _ = _pins(repo)[0]
    rec = _record(repo, sid)
    rec[field] = value
    errs = ap.validate_record(rec, signal_id=sid, approval_id=rec["approval_id"])
    assert any(expect in e for e in errs), errs


def test_legacy_rules(repo):
    sid, _ = _pins(repo)[0]
    legacy = _record(repo, sid, basis="legacy", approved_at=None)
    assert ap.validate_record(legacy) == []          # no date is invented
    bad = _record(repo, sid, version=2, action="supersede", basis="legacy",
                  supersedes=legacy["approval_id"], approval_id="20261021T020000Z-0000000b",
                  diff={"x": 1})
    assert any("legacy" in e for e in ap.validate_record(bad))


def test_unknown_and_missing_fields(repo):
    sid, _ = _pins(repo)[0]
    rec = _record(repo, sid)
    rec["chat_transcript"] = "..."
    assert any("unknown fields" in e for e in ap.validate_record(rec))
    rec = _record(repo, sid)
    del rec["payload_sha256"]
    assert any("missing fields" in e for e in ap.validate_record(rec))


def test_misplaced_files_are_violations(repo):
    sid, _ = _pins(repo)[0]
    rec = _record(repo, sid)
    (_ledger(repo) / "stray.json").write_text("{}")
    wrong = _ledger(repo) / sid
    wrong.mkdir()
    (wrong / "not-an-id.json").write_text(json.dumps(rec))
    r = _verify(repo)
    assert not r.ok and "unexpected-file" in _codes(r, "violation")


def test_record_in_wrong_folder(repo):
    a, b = _pins(repo)[0][0], _pins(repo)[1][0]
    rec = _record(repo, a)
    d = _ledger(repo) / b
    d.mkdir()
    (d / f"{rec['approval_id']}.json").write_text(json.dumps(rec))
    r = _verify(repo)
    assert not r.ok and "record-invalid" in _codes(r, "violation")


# ── append-only ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def pushed(repo):
    """A repo whose origin/main already holds one approval record."""
    sid, _ = _pins(repo)[0]
    f = _write(repo, _record(repo, sid))
    _commit(repo, "approval")
    _publish_base(repo)
    assert _verify(repo).ok
    return repo, f


def test_new_records_are_fine(pushed):
    repo, _ = pushed
    sid, _ = _pins(repo)[1]
    _write(repo, _record(repo, sid, approval_id="20261021T020000Z-0000000b"))
    assert _verify(repo).ok


def test_edited_record_is_refused(pushed):
    repo, f = pushed
    rec = json.loads(f.read_text()); rec["owner"] = "someone-else"
    f.write_text(json.dumps(rec, indent=2))
    r = _verify(repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_deleted_record_is_refused(pushed):
    repo, f = pushed
    f.unlink()
    r = _verify(repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_renamed_record_is_refused(pushed):
    repo, f = pushed
    f.rename(f.with_name("20261021T013000Z-ffffffff.json"))
    r = _verify(repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_edit_already_pushed_is_caught_by_history(pushed):
    repo, f = pushed
    rec = json.loads(f.read_text()); rec["recorded_by"] = "rewriter"
    f.write_text(json.dumps(rec, indent=2))
    _commit(repo, "quiet edit")
    _publish_base(repo)            # the edit is now on origin/main too
    r = _verify(repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_schema_edit_is_refused_readme_edit_is_not(pushed):
    repo, _ = pushed
    readme = _ledger(repo) / "README.md"
    readme.write_text(readme.read_text() + "\nmore\n")
    assert _verify(repo).ok
    schema = _ledger(repo) / "schema" / "pin-approval.v1.json"
    schema.write_text(schema.read_text().replace("version 1", "version one"))
    r = _verify(repo)
    assert not r.ok and "append-only" in _codes(r, "violation")


def test_missing_base_is_a_gap(repo):
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    r = _verify(repo)
    assert "append-only-unchecked" in _codes(r, "gap")
    assert r.ok and not _verify(repo, enforce=True).ok


# ── CLI ────────────────────────────────────────────────────────────────────────────────

def test_cli_exit_codes(repo):
    args = ["verify", "--index", str(_index(repo)), "--approvals", str(_ledger(repo)), "--no-git"]
    assert ap.main(args) == 0
    assert ap.main(args + ["--enforce"]) == 1


# ── import --write and publish guards ───────────────────────────────────────────────────────

def _fresh_db(path: Path) -> Path:
    """A throwaway database with the real schema (init + migrate), as in test_checked_signals."""
    import app
    old = app.DB_PATH
    app.DB_PATH = path
    try:
        app.init_db()
        app.migrate_db()
    finally:
        app.DB_PATH = old
    return path


def _signal_count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT count(*) FROM signals").fetchone()[0]
    finally:
        conn.close()


def test_import_write_proceeds_with_gaps_when_unenforced(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_DIR", _ledger(repo))
    monkeypatch.setattr(ap, "ROOT", repo)
    db = _fresh_db(tmp_path / "dummy.db")
    lines: list[str] = []
    cs.run_import(db, _index(repo), write=True, out=lines.append)
    assert _signal_count(db) == len(_pins(repo))
    assert any("NOT enforced" in l for l in lines)


def test_import_write_refused_on_violation(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_DIR", _ledger(repo))
    monkeypatch.setattr(ap, "ROOT", repo)
    sid, _ = _pins(repo)[0]
    _write(repo, _record(repo, sid))
    _edit_pin(repo, sid, title="Edited after approval")
    db = _fresh_db(tmp_path / "dummy.db")
    with pytest.raises(cs.CheckedSignalError, match="approval ledger refused"):
        cs.run_import(db, _index(repo), write=True, out=lambda *_: None)
    assert _signal_count(db) == 0
    # A dry run reports and never refuses.
    cs.run_import(db, _index(repo), write=False, out=lambda *_: None)


def test_import_write_refused_when_enforced(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_DIR", _ledger(repo))
    monkeypatch.setattr(ap, "ROOT", repo)
    monkeypatch.setenv(ap.ENFORCE_ENV, "1")
    db = _fresh_db(tmp_path / "dummy.db")
    with pytest.raises(cs.CheckedSignalError, match="approval ledger refused"):
        cs.run_import(db, _index(repo), write=True, out=lambda *_: None)
    assert _signal_count(db) == 0


def test_import_write_refused_in_staging(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ROLE", "staging")
    with pytest.raises(cs.CheckedSignalError, match="FORGE_ROLE=staging"):
        cs.run_import(tmp_path / "never-opened.db", _index(repo), write=True, out=lambda *_: None)
    assert not (tmp_path / "never-opened.db").exists()


def test_connect_never_creates_a_missing_db(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(cs.CheckedSignalError, match="does not exist"):
        cs._connect(missing)
    assert not missing.exists()


def test_publish_guard_refuses_on_violation(repo, tmp_path, monkeypatch, capsys):
    import tools.publish as publish
    monkeypatch.setattr(ap, "APPROVALS_DIR", _ledger(repo))
    monkeypatch.setattr(ap, "ROOT", repo)
    monkeypatch.setattr(publish, "CHECKED_INDEX", _index(repo))
    db = _fresh_db(tmp_path / "dummy.db")
    cs.run_import(db, _index(repo), write=True, out=lambda *_: None)
    conn = sqlite3.connect(str(db))
    try:
        publish._checked_guard_before(conn)       # gaps only, not enforced: passes
        sid, _ = _pins(repo)[0]
        v1 = _record(repo, sid)
        _write(repo, v1)
        _write(repo, _record(repo, sid, version=2, action="revoke", supersedes=v1["approval_id"],
                             approval_id="20261021T020000Z-0000000b"))
        with pytest.raises(publish.CheckedGuardError):
            publish._checked_guard_before(conn)
    finally:
        conn.close()
    assert "revoked-active" in capsys.readouterr().out
