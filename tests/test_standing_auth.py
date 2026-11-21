"""Standing publication authorization: records under it approve only while it is
active and only pins that pass its machine checks; everything else fails closed."""
from __future__ import annotations

import copy
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import approvals as ap  # noqa: E402
from tools import standing_auth as sa  # noqa: E402

REAL = ROOT / "docs" / "approvals" / "standing" / "standing-autopub-test-2026-11-21.json"
FLORA = ROOT / "docs" / "signals" / "limpopo-polokwane-flora-park-2026-11-21.json"


def _auth(**kw):
    a = json.loads(REAL.read_text(encoding="utf-8"))
    a.update(kw)
    return a


def _pin():
    return copy.deepcopy(json.loads(FLORA.read_text(encoding="utf-8"))["signals"][0])


def _rec(pin, approved_at="2026-11-21T15:00:00Z", **kw):
    r = {"approval_reference": {"id": "standing-autopub-test-2026-11-21", "summary": "x"},
         "approved_at": approved_at, "action": "approve", "material": False, "owner": "Riley Bishop"}
    r.update(kw)
    return r


@pytest.fixture
def sdir(tmp_path):
    def write(auth):
        (tmp_path / f"{auth['authorization_id']}.json").write_text(json.dumps(auth), encoding="utf-8")
        return tmp_path
    return write


def test_real_file_is_valid_and_bounded():
    a = json.loads(REAL.read_text(encoding="utf-8"))
    assert sa.validate(a) == []
    assert a["expires_at"] == "2026-11-21T23:59:00+02:00"     # the owner's choice
    assert a["scope"]["material_updates"] is False and a["scope"]["actions"] == ["approve"]
    assert a["review"] == "machine"


def test_active_window(sdir):
    d = sdir(_auth())
    at = lambda s: datetime.fromisoformat(s)
    assert sa.active_now("standing-autopub-test-2026-11-21", d, at("2026-11-21T20:00:00+02:00"))[1] is None
    assert "expired" in sa.active_now("standing-autopub-test-2026-11-21", d, at("2026-11-21T23:59:00+02:00"))[1]
    assert "earlier than the grant" in sa.active_now("standing-autopub-test-2026-11-21", d, at("2026-11-21T16:00:00+02:00"))[1]


def test_no_end_date_is_not_active(sdir):
    d = sdir(_auth(expires_at=None))
    _, why = sa.active_now("standing-autopub-test-2026-11-21", d, datetime(2026, 11, 21, 15, tzinfo=timezone.utc))
    assert "no end date" in why


def test_revocation_stops_later_records_only(sdir):
    d = sdir(_auth(revoked_at="2026-11-21T18:00:00+02:00", revocation_reason="owner stopped it"))
    assert sa.record_problems(_rec(_pin(), "2026-11-21T15:30:00Z"), _pin(), d) == []
    assert any("revoked" in p for p in sa.record_problems(_rec(_pin(), "2026-11-21T16:30:00Z"), _pin(), d))


def test_missing_or_malformed_authorization_fails_closed(sdir, tmp_path):
    assert sa.record_problems(_rec(_pin()), _pin(), tmp_path)          # no file
    bad = _auth(); bad["scope"]["material_updates"] = True
    assert sa.record_problems(_rec(_pin()), _pin(), sdir(bad))
    assert sa.validate(_auth(review="owner"))
    assert sa.validate({**_auth(), "conditions": {"min_independent_source_domains": 1}})


def test_machine_conditions(sdir):
    d = sdir(_auth())
    pin = _pin()
    assert sa.record_problems(_rec(pin), pin, d) == []                 # IOL + SABC News: two sites
    one = _pin()
    one["metadata"]["checked"]["sources"] = one["metadata"]["checked"]["sources"][:1] * 2
    assert any("different sites" in p for p in sa.record_problems(_rec(one), one, d))
    sister = _pin()
    sister["metadata"]["checked"]["sources"][1]["url"] = "https://news.iol.co.za/other-copy/"
    assert any("different sites" in p for p in sa.record_problems(_rec(sister), sister, d))
    glob = _pin(); glob["stream"] = "GLOBAL"
    assert any("stream" in p for p in sa.record_problems(_rec(glob), glob, d))
    assert any("material" in p for p in sa.record_problems(_rec(pin, material=True), pin, d))
    assert any("outside" in p for p in sa.record_problems(_rec(pin, action="supersede"), pin, d))


def test_source_domain():
    assert sa.source_domain("https://www.sabcnews.com/x") == "sabcnews.com"
    assert sa.source_domain("https://iol.co.za/x") == sa.source_domain("https://www.iol.co.za/y") == "iol.co.za"
    assert sa.source_domain("https://reviewonline.co.za/a") != sa.source_domain("https://iol.co.za/a")


def test_ledger_accepts_and_refuses_standing_records(tmp_path, sdir):
    """End to end on a copy of the ledger: a standing record for a new pin verifies
    inside the window and refuses once the window is gone."""
    import shutil
    pins = tmp_path / "approvals" / "pins"
    shutil.copytree(ROOT / "docs" / "approvals" / "pins", pins)
    standing = tmp_path / "approvals" / "standing"
    standing.mkdir()
    shutil.copy(REAL, standing / REAL.name)
    flora = "c522699e-12c5-537b-8759-d35744aa28ce"
    shutil.rmtree(pins / flora)                                        # treat Flora Park as new
    when = datetime(2026, 11, 21, 15, 30, tzinfo=timezone.utc)
    path = ap.write_standing_record(flora, REAL.stem, approvals_dir=pins, now=when)
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["basis"] == "standing" and "did not review" in rec["approval_reference"]["summary"]
    rep = ap.verify(approvals_dir=pins, check_git=False, enforce=True)
    assert rep.ok, ap.report_lines(rep)
    with pytest.raises(ap.ApprovalError, match="already has approval records"):
        ap.write_standing_record(flora, REAL.stem, approvals_dir=pins, now=when)
    # Revoked before that approval: the same record now refuses the build.
    a = _auth(revoked_at="2026-11-21T17:00:00+02:00", revocation_reason="test")
    (standing / REAL.name).write_text(json.dumps(a), encoding="utf-8")
    rep = ap.verify(approvals_dir=pins, check_git=False, enforce=True)
    assert not rep.ok and any(f.code == "standing-refused" for f in rep.violations)
    # Expired: no new record can be written.
    shutil.rmtree(pins / flora)
    with pytest.raises(ap.ApprovalError, match="cannot approve now"):
        ap.write_standing_record(flora, REAL.stem, approvals_dir=pins,
                                 now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc))


def test_publish_gate_is_set_only_in_autopublish():
    """FORGE_ALLOW_PUBLISH=1 is set in exactly one place: autopublish's publish.py call."""
    hits = [p for p in (ROOT / "tools").rglob("*.py")
            if 'FORGE_ALLOW_PUBLISH="1"' in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == [ROOT / "tools" / "autopublish.py"]
