"""
Time-aware pins, Batch 2: date fields and the material-update flag.

The plan's acceptance tests:
  * a routine check that changes anything except last_checked_at is refused;
  * a material update without a material approval is refused;
  * an update that changes incident_start is refused;
  * all 83 current pins load with no material update;
plus the parts that make those hold: the pin-approval v2 schema, the field
checks in metadata.checked, docs/signals/pin-clocks.json and the four new
open-records columns.

Every ledger test works on a throwaway copy of docs/signals and the ledger's
rules under tmp_path. The real files are only read.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import approvals as ap  # noqa: E402
from tools import checked_signals as cs  # noqa: E402
from tools import pin_clocks as pc  # noqa: E402
from tools import records_export as rx  # noqa: E402

REAL_SIGNALS = ROOT / "docs" / "signals"
REAL_LEDGER = ROOT / "docs" / "approvals" / "pins"

UPDATE = {
    "last_material_update_at": "2026-10-24T12:00:00+02:00",
    "update_note": "Police confirmed an arrest in a follow-up statement.",
    "update_sources": [{"name": "Example News", "url": "https://example.org/follow-up"}],
}


# ── fixtures and helpers ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_env_enforce(monkeypatch):
    monkeypatch.delenv(ap.ENFORCE_ENV, raising=False)


@pytest.fixture
def repo(tmp_path):
    """docs/signals plus the ledger's rules (no records), enforcement off."""
    r = tmp_path / "repo"
    shutil.copytree(REAL_SIGNALS, r / "docs" / "signals")
    ledger = r / "docs" / "approvals" / "pins"
    ledger.mkdir(parents=True)
    for name in ("README.md", "schema", "_examples"):
        src = REAL_LEDGER / name
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, ledger / name)
    policy = json.loads((REAL_LEDGER / "policy.json").read_text())
    policy["enforce"] = False
    (ledger / "policy.json").write_text(json.dumps(policy, indent=2))
    return r


def _index(repo: Path) -> Path:
    return repo / "docs" / "signals" / "index.json"


def _ledger(repo: Path) -> Path:
    return repo / "docs" / "approvals" / "pins"


def _pin(repo: Path, sid: str) -> tuple[dict, str]:
    return ap._batch_pins(_index(repo))[1][sid]


def _a_pin(repo: Path) -> str:
    loaded, _ = ap._batch_pins(_index(repo))
    return loaded.active[0]["signal_id"]


def _edit_checked(repo: Path, sid: str, top: dict | None = None, **checked) -> dict:
    """Change a pin's metadata.checked (and top-level fields), restamp, return the pin."""
    _, batch = _pin(repo, sid)
    f = repo / "docs" / "signals" / batch
    data = json.loads(f.read_text())
    for p in data["signals"]:
        if p["signal_id"] == sid:
            p["metadata"]["checked"].update(checked)
            p.update(top or {})
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    cs.stamp_files(_index(repo))
    return _pin(repo, sid)[0]


def _record(repo: Path, sid: str, *, version=1, action="approve", supersedes=None,
            approval_id="20261021T013000Z-0000000a", approved_at="2026-10-21T03:30:00+02:00",
            schema=None, material=None, payload=None) -> dict:
    pin, batch = _pin(repo, sid)
    data = action != "revoke"
    body = ap.approved_payload(payload if payload is not None else pin)
    rec = {
        "schema": schema or ap.RECORD_SCHEMA,
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
        "diff_from_previous": None if version == 1 else {"changed": "test"},
        "owner": "test-owner",
        "approved_at": approved_at,
        "recorded_at": "2026-10-24T13:31:00+02:00",
        "recorded_by": "test-agent",
        "approval_reference": {"id": "test-0001", "summary": "Test approval."},
        "basis": "explicit",
        "scope": "data-only",
    }
    if rec["schema"] == ap.RECORD_SCHEMA_V2:
        rec["material"] = bool(material)
    return rec


def _write(repo: Path, rec: dict) -> None:
    d = _ledger(repo) / rec["signal_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['approval_id']}.json").write_text(json.dumps(rec, indent=2))


def _verify(repo: Path) -> ap.Report:
    return ap.verify(_index(repo), _ledger(repo), repo, check_git=False)


def _codes(report: ap.Report) -> set[str]:
    return {f.code for f in report.findings if f.kind == "violation"}


V2_ID = "20261024T110000Z-0000000b"
V2_AT = "2026-10-24T13:00:00+02:00"


def _approved_then_updated(repo: Path, *, material: bool, schema=ap.RECORD_SCHEMA_V2,
                           top: dict | None = None, **checked) -> tuple[str, ap.Report]:
    """Version 1 approves the pin as it is; version 2 approves an edited pin."""
    sid = _a_pin(repo)
    v1 = _record(repo, sid)
    _write(repo, v1)
    assert _verify(repo).ok
    pin = _edit_checked(repo, sid, top=top, **checked)
    v2 = _record(repo, sid, version=2, action="supersede", supersedes=v1["approval_id"],
                 approval_id=V2_ID, approved_at=V2_AT, schema=schema, material=material, payload=pin)
    _write(repo, v2)
    return sid, _verify(repo)


# ── all 83 current pins load with no material update ─────────────────────────────

def test_all_current_pins_load_with_no_material_update():
    loaded = cs.load_index()
    assert len(loaded.signals) == 83 and len(loaded.active) == 83
    for s in loaded.signals:
        chk = s["metadata"]["checked"]
        assert cs.update_fields(chk) == {}, s["signal_id"]
        assert cs.update_fields_problem(chk, s["published_at"]) is None
        assert "last_checked_at" not in chk
    report = ap.verify(check_git=False)
    assert report.ok and report.approved_pins == 83
    assert not any(f.code in ("unflagged-material-update", "material-update-invalid",
                              "update-changed-clock", "retracted-update") for f in report.findings)
    # No real record is a material update yet, and every one stays valid.
    records = ap.load_records(REAL_LEDGER, [])
    assert not any(ap.is_material(r) for recs in records.values() for r in recs)


def test_real_pin_clocks_file_is_valid_and_starts_empty():
    data = pc.read_file()
    assert data["schema"] == pc.SCHEMA and data["version"] == pc.VERSION
    assert data["pins"] == {}                         # nothing back-filled or invented
    assert pc.problems(cs.load_index().signals) == []


def test_fingerprints_unchanged_by_batch2():
    """No pin file changed: every fingerprint and approval hash still matches."""
    assert cs.stamp_files(check=True) == []


# ── the approval schema ──────────────────────────────────────────────────────────

def test_v2_schema_file_matches_validator_and_v1_is_untouched():
    v1 = json.loads((REAL_LEDGER / "schema" / "pin-approval.v1.json").read_text())
    v2 = json.loads((REAL_LEDGER / "schema" / "pin-approval.v2.json").read_text())
    assert tuple(v1["required"]) == ap.RECORD_FIELDS
    assert tuple(v2["required"]) == ap.RECORD_FIELDS_V2 == ap.RECORD_FIELDS + ("material",)
    assert v2["properties"]["schema"]["const"] == ap.RECORD_SCHEMA_V2
    assert v2["properties"]["material"]["type"] == "boolean"
    assert v2["additionalProperties"] is False


def test_material_update_example_is_valid():
    base = json.loads((REAL_LEDGER / "_examples" / "example.json").read_text())
    upd = json.loads((REAL_LEDGER / "_examples" / "example-material-update.json").read_text())
    assert ap.validate_record(upd, signal_id=upd["signal_id"], approval_id=upd["approval_id"]) == []
    assert ap.is_material(upd) and not ap.is_material(base)
    assert ap.update_problems([base, upd]) == []


def test_v2_record_field_rules(repo):
    sid = _a_pin(repo)
    ok = _record(repo, sid, schema=ap.RECORD_SCHEMA_V2, material=False)
    assert ap.validate_record(ok) == []
    no_flag = dict(ok)
    del no_flag["material"]
    assert any("missing fields: material" in e for e in ap.validate_record(no_flag))
    v1_with_flag = dict(_record(repo, sid), material=False)
    assert any("unknown fields: material" in e for e in ap.validate_record(v1_with_flag))
    assert any("true or false" in e for e in ap.validate_record(dict(ok, material="yes")))
    # material: true only on a supersede
    assert any("needs action supersede" in e for e in ap.validate_record(dict(ok, material=True)))
    revoke = _record(repo, sid, version=2, action="revoke", supersedes="20261021T013000Z-0000000a",
                     approval_id=V2_ID, schema=ap.RECORD_SCHEMA_V2, material=True)
    assert any("needs action supersede" in e for e in ap.validate_record(revoke))
    assert any("schema must be one of" in e for e in ap.validate_record(dict(ok, schema="forge.pin_approval.v3")))


# ── a valid material update passes ─────────────────────────────────────────────────

def test_valid_material_update_is_approved(repo):
    sid, report = _approved_then_updated(repo, material=True, **UPDATE)
    assert report.ok, report.findings
    assert report.approved_pins == 1
    assert _codes(report) == set()


def test_v2_non_material_wording_fix_is_approved(repo):
    sid = _a_pin(repo)
    _, report = _approved_then_updated(repo, material=False, top={"title": "A reworded title"})
    assert report.ok and _codes(report) == set()


# ── a material update without material approval is refused ───────────────────────

def test_update_fields_under_a_v1_supersede_are_refused(repo):
    sid, report = _approved_then_updated(repo, material=False, schema=ap.RECORD_SCHEMA, **UPDATE)
    assert "unflagged-material-update" in _codes(report)
    assert not report.ok and report.approved_pins == 0


def test_update_fields_under_a_non_material_v2_supersede_are_refused(repo):
    sid, report = _approved_then_updated(repo, material=False, **UPDATE)
    assert "unflagged-material-update" in _codes(report)
    assert report.approved_pins == 0


def test_update_fields_with_no_new_approval_are_refused(repo):
    sid = _a_pin(repo)
    _write(repo, _record(repo, sid))
    _edit_checked(repo, sid, **UPDATE)
    report = _verify(repo)
    assert "hash-mismatch" in _codes(report) and report.approved_pins == 0


def test_update_fields_on_a_first_approval_are_refused(repo):
    sid = _a_pin(repo)
    pin = _edit_checked(repo, sid, **UPDATE)
    _write(repo, _record(repo, sid, payload=pin))
    assert "unflagged-material-update" in _codes(_verify(repo))


def test_correction_after_an_update_keeps_the_update_fields(repo):
    """A correction keeps its own wording: it may neither erase nor rewrite the update."""
    sid, report = _approved_then_updated(repo, material=True, **UPDATE)
    assert report.ok
    _, batch = _pin(repo, sid)
    f = repo / "docs" / "signals" / batch
    data = json.loads(f.read_text())
    for p in data["signals"]:
        if p["signal_id"] == sid:
            for k in cs.UPDATE_FIELDS:
                p["metadata"]["checked"].pop(k)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    cs.stamp_files(_index(repo))
    pin = _pin(repo, sid)[0]
    _write(repo, _record(repo, sid, version=3, action="supersede", supersedes=V2_ID,
                         approval_id="20261024T112000Z-0000000c", approved_at="2026-10-24T13:20:00+02:00",
                         schema=ap.RECORD_SCHEMA_V2, material=False, payload=pin))
    assert "unflagged-material-update" in _codes(_verify(repo))


def test_material_flag_with_nothing_changed_is_refused(repo):
    sid, report = _approved_then_updated(repo, material=True, top={"title": "Only the title"})
    assert "material-update-invalid" in _codes(report)


def test_material_update_dated_after_its_approval_is_refused(repo):
    late = dict(UPDATE, last_material_update_at="2026-10-24T15:00:00+02:00")   # approved 13:00
    sid, report = _approved_then_updated(repo, material=True, **late)
    assert "material-update-invalid" in _codes(report)


def test_second_update_must_be_later_than_the_first(repo):
    sid, report = _approved_then_updated(repo, material=True, **UPDATE)
    assert report.ok
    pin = _edit_checked(repo, sid, last_material_update_at="2026-10-24T11:00:00+02:00",
                        update_note="An earlier-dated second update.")
    _write(repo, _record(repo, sid, version=3, action="supersede", supersedes=V2_ID,
                         approval_id="20261024T112000Z-0000000c", approved_at="2026-10-24T13:20:00+02:00",
                         schema=ap.RECORD_SCHEMA_V2, material=True, payload=pin))
    assert "material-update-invalid" in _codes(_verify(repo))


def test_material_update_after_retraction_is_refused(repo):
    sid, report = _approved_then_updated(repo, material=True, **UPDATE)
    assert report.ok
    _, batch = _pin(repo, sid)
    f = repo / "docs" / "signals" / batch
    data = json.loads(f.read_text())
    data.setdefault("retractions", []).append(
        {"signal_id": sid, "reason": "Test retraction.", "retracted_at": "2026-10-24T10:00:00Z"})
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    cs.stamp_files(_index(repo))
    assert "retracted-update" in _codes(_verify(repo))     # update approved 11:00Z, retracted 10:00Z


# ── an update that changes incident_start (or published_at) is refused ─────────────

def test_update_that_changes_incident_start_is_refused(repo):
    sid = _a_pin(repo)
    start = _pin(repo, sid)[0]["metadata"]["checked"]["incident_start"]
    new_start = "2026-10-01" if start != "2026-10-01" else "2026-10-02"
    _, report = _approved_then_updated(repo, material=True, incident_start=new_start,
                                       incident_precision="day", **UPDATE)
    assert "update-changed-clock" in _codes(report)
    assert any("incident_start may not change" in f.message for f in report.findings)
    assert report.approved_pins == 0


def test_update_that_changes_published_at_is_refused(repo):
    _, report = _approved_then_updated(repo, material=True, top={"published_at": "2026-10-24T08:00:00Z"},
                                       **UPDATE)
    assert "update-changed-clock" in _codes(report)
    assert any("published_at may not change" in f.message for f in report.findings)


# ── the fields in metadata.checked ─────────────────────────────────────────────────

def _checked_pin(**changes) -> dict:
    s = copy.deepcopy(cs.load_index().signals[0])
    s["metadata"]["checked"].update(changes)
    s["fingerprint"] = cs.fingerprint(s)
    return s


def test_complete_update_fields_validate():
    cs.validate_signal(_checked_pin(**UPDATE))


@pytest.mark.parametrize("changes, words", [
    ({"last_material_update_at": UPDATE["last_material_update_at"]}, "needs all of"),
    ({**UPDATE, "last_material_update_at": "2026-10-24"}, "UTC offset"),
    ({**UPDATE, "last_material_update_at": "2026-10-24T12:00:00"}, "UTC offset"),
    ({**UPDATE, "last_material_update_at": "2020-01-01T12:00:00+02:00"}, "before"),
    ({**UPDATE, "update_note": ""}, "update_note"),
    ({**UPDATE, "update_note": "two\nlines"}, "update_note"),
    ({**UPDATE, "update_note": "x" * 281}, "update_note"),
    ({**UPDATE, "update_sources": []}, "update_sources"),
    ({**UPDATE, "update_sources": [{"name": "A", "url": "ftp://x"}]}, "update_sources"),
    ({**UPDATE, "update_sources": [{"name": "A", "url": "https://x", "quote": "q"}]}, "update_sources"),
    ({"last_checked_at": "2026-10-24T12:00:00+02:00"}, "pin-clocks.json"),
])
def test_bad_update_fields_are_refused(changes, words):
    with pytest.raises(cs.CheckedSignalError, match=words):
        cs.validate_signal(_checked_pin(**changes))


# ── routine checks: only last_checked_at ──────────────────────────────────────────

def _snap(repo: Path) -> dict[str, str]:
    return pc._snapshot_worktree(repo)


def _clocks(repo: Path) -> Path:
    return repo / "docs" / "signals" / pc.CLOCKS_NAME


def _check(repo: Path, sid: str, when: str, **kw):
    return pc.record_check(sid, when, path=_clocks(repo), index_path=_index(repo), **kw)


def test_routine_check_writes_only_last_checked_at(repo):
    sid = _a_pin(repo)
    before = _snap(repo)
    entry = _check(repo, sid, "2026-10-24T09:00:00+02:00")
    after = _snap(repo)
    assert entry == {"last_checked_at": "2026-10-24T09:00:00+02:00"}
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {"docs/signals/pin-clocks.json"}
    assert pc.routine_write_problems(before, after) == []
    assert pc.problems(cs.load_index(_index(repo)).signals, _clocks(repo)) == []
    # the approvals still pass: a routine check never touches a fingerprint
    _write(repo, _record(repo, sid))
    assert _verify(repo).approved_pins == 1
    # and a later check moves the clock forward
    _check(repo, sid, "2026-10-24T10:00:00+02:00")
    assert pc.load(_clocks(repo))[sid]["last_checked_at"] == "2026-10-24T10:00:00+02:00"


def test_routine_check_refusals_in_the_writer(repo):
    sid = _a_pin(repo)
    _check(repo, sid, "2026-10-24T09:00:00+02:00")
    with pytest.raises(pc.PinClockError, match="only move forward"):
        _check(repo, sid, "2026-10-24T08:00:00+02:00")
    with pytest.raises(pc.PinClockError, match="not a hand-checked pin"):
        _check(repo, "00000000-0000-4000-8000-000000000000", "2026-10-24T09:00:00+02:00")
    with pytest.raises(pc.PinClockError, match="UTC offset"):
        _check(repo, sid, "2026-10-24")
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    with pytest.raises(pc.PinClockError, match="future"):
        _check(repo, sid, future)
    with pytest.raises(pc.PinClockError, match="before the pin was published"):
        _check(repo, sid, "2020-01-01T09:00:00+02:00")


def test_routine_check_that_changes_a_pin_file_is_refused(repo):
    sid = _a_pin(repo)
    before = _snap(repo)
    _check(repo, sid, "2026-10-24T09:00:00+02:00")
    _edit_checked(repo, sid, top={"title": "Changed by a routine"})
    found = pc.routine_write_problems(before, _snap(repo))
    assert found and any("may change only last_checked_at" in p for p in found)


def test_routine_check_that_sets_update_fields_is_refused(repo):
    sid = _a_pin(repo)
    before = _snap(repo)
    _edit_checked(repo, sid, **UPDATE)
    assert pc.routine_write_problems(before, _snap(repo))


@pytest.mark.parametrize("rel", ["docs/signals/provenance.json", "docs/signals/incident-dates.json",
                                 "docs/approvals/pins/README.md"])
def test_routine_check_that_changes_another_file_is_refused(repo, rel):
    before = _snap(repo)
    f = repo / rel
    f.write_text(f.read_text() + "\n")
    assert any(rel in p for p in pc.routine_write_problems(before, _snap(repo)))


def test_routine_check_that_adds_an_approval_record_is_refused(repo):
    before = _snap(repo)
    _write(repo, _record(repo, _a_pin(repo)))
    assert pc.routine_write_problems(before, _snap(repo))


def test_routine_write_rules_inside_the_clocks_file(repo):
    sid = _a_pin(repo)
    _check(repo, sid, "2026-10-24T09:00:00+02:00")
    before = _snap(repo)
    rel = "docs/signals/pin-clocks.json"
    data = json.loads(before[rel])

    def after(mutate):
        d = copy.deepcopy(data)
        mutate(d)
        return {**before, rel: json.dumps(d)}

    def extra(d):
        d["pins"][sid]["note"] = "x"

    def backwards(d):
        d["pins"][sid]["last_checked_at"] = "2026-10-24T08:00:00+02:00"

    def removed(d):
        d["pins"].pop(sid)

    def about(d):
        d["about"] = "rewritten"

    def forward(d):
        d["pins"][sid]["last_checked_at"] = "2026-10-24T11:00:00+02:00"

    assert pc.routine_write_problems(before, after(extra))
    assert pc.routine_write_problems(before, after(backwards))
    assert pc.routine_write_problems(before, after(removed))
    assert pc.routine_write_problems(before, after(about))
    assert pc.routine_write_problems(before, {**before, rel: None})
    assert pc.routine_write_problems(before, after(forward)) == []


def test_clocks_file_problems_are_reported(repo):
    sid = _a_pin(repo)
    pins = cs.load_index(_index(repo)).signals
    f = _clocks(repo)
    data = pc.empty_file()
    data["pins"] = {sid: {"last_checked_at": "2026-10-24T09:00:00+02:00", "material": True},
                    "00000000-0000-4000-8000-000000000000": {"last_checked_at": "2026-10-24T09:00:00+02:00"}}
    f.write_text(json.dumps(data))
    found = pc.problems(pins, f)
    assert len(found) == 2
    assert any("only last_checked_at" in p for p in found)
    assert any("not a hand-checked pin" in p for p in found)
    f.write_text("{not json")
    assert pc.problems(pins, f)


def test_check_routine_against_git(repo):
    import subprocess
    env_args = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *env_args, "commit", "-q", "-m", "base"], check=True)
    sid = _a_pin(repo)
    _check(repo, sid, "2026-10-24T09:00:00+02:00")
    assert pc.check_routine(repo, "HEAD") == []
    _edit_checked(repo, sid, top={"title": "Changed"})
    assert pc.check_routine(repo, "HEAD")


# ── open records: four new columns at the end ─────────────────────────────────────

def test_records_gain_the_new_columns_at_the_end():
    assert rx.RECORD_FIELDS[-4:] == ("last_checked_at", "last_material_update_at",
                                     "update_note", "update_sources")
    assert rx.RECORD_FIELDS.index("data_version") == len(rx.RECORD_FIELDS) - 5
    for name in rx.RECORD_FIELDS[-4:]:
        assert rx.FIELD_HELP[name]


def _conn_for(sids) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE signals (signal_id TEXT, metadata_json TEXT, published_at TEXT, "
                 "status TEXT, lat REAL, lng REAL)")
    for sid in sids:
        conn.execute("INSERT INTO signals VALUES (?,?,?,?,?,?)",
                     (sid, json.dumps({"checked": {}}), "x", "published", 1, 1))
    return conn


def test_real_records_have_blank_update_columns_and_are_otherwise_unchanged():
    """All 83 records, with the four new columns blank and every earlier column as published."""
    published = json.loads((ROOT / "dist" / "records.json").read_text())
    rows = rx.build_records(_conn_for(r["signal_id"] for r in published["records"]),
                            version=published["data_version"])
    assert len(rows) == len(published["records"]) == 83
    old = {r["signal_id"]: r for r in published["records"]}
    earlier = rx.RECORD_FIELDS[:-4]
    # Before the first publish after the merge, dist/ has only the earlier
    # columns; from that publish on, all of them (Batch 3 found this test
    # would fail on a rebuilt dist/).
    assert [f["name"] for f in published["fields"]] in (list(earlier), list(rx.RECORD_FIELDS))
    for r in rows:
        assert {k: r[k] for k in earlier} == {k: old[r["signal_id"]][k] for k in earlier}
        assert all(r[k] == "" for k in rx.RECORD_FIELDS[-4:])
        assert all(old[r["signal_id"]].get(k, "") == "" for k in rx.RECORD_FIELDS[-4:])


def test_records_carry_clock_and_update_values(repo):
    sid, report = _approved_then_updated(repo, material=True, **UPDATE)
    assert report.ok
    _check(repo, sid, "2026-10-24T14:00:00+02:00")
    rows = rx.build_records(_conn_for([sid]), index_path=_index(repo), approvals_dir=_ledger(repo),
                            signals_dir=repo / "docs" / "signals", version="test")
    assert len(rows) == 1
    r = rows[0]
    assert r["last_checked_at"] == "2026-10-24T14:00:00+02:00"
    assert r["last_material_update_at"] == UPDATE["last_material_update_at"]
    assert r["update_note"] == UPDATE["update_note"]
    assert r["update_sources"] == "https://example.org/follow-up"
    assert r["pin_version"] == 2
    header = rx.to_csv(rows).split("\r\n")[0].lstrip("\ufeff").split(",")
    assert header[-4:] == list(rx.RECORD_FIELDS[-4:])


def test_invalid_clock_entry_exports_blank(repo):
    sid = _a_pin(repo)
    _write(repo, _record(repo, sid))
    data = pc.empty_file()
    data["pins"][sid] = {"last_checked_at": "not a time"}
    _clocks(repo).write_text(json.dumps(data))
    rows = rx.build_records(_conn_for([sid]), index_path=_index(repo), approvals_dir=_ledger(repo),
                            signals_dir=repo / "docs" / "signals", version="test")
    assert rows[0]["last_checked_at"] == ""
