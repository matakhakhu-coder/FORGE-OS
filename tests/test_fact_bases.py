"""Regression checks for the approved private-audit publication preparation."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Gitignored (private/ is never committed), so it exists only on audit machines.
PRIVATE_AUDIT = ROOT / 'private/admiralty-audit/audit.json'
# Audited separately after the frozen 36-pin Admiralty audit; the private audit is not widened.
CAFDA = '7aa4eea1-c1f4-55af-b761-d55ff2df3a51'
# Sprint 5 pins, each checked claim by claim in its own evidence batch (Gqeberha B, Steenberg E, Strandfontein H).
GQEBERHA = 'b842a3b5-eda7-5b97-bfe1-0448fe0272a5'
STEENBERG = '048440e0-0be5-53d8-934a-1ba6233a0dfa'
STRANDFONTEIN = 'adf99019-941e-5dd0-9851-635da0a1e146'
# Sprint 5 draft batch C5: every quote re-matched word for word against the saved copies (22 Oct 2026).
C5 = {
    '49773d55-5704-5fcb-ac36-1f7eda5de54b',  # Tafelsig barber
    '06219161-76ad-5b50-91d0-e58235427edf',  # Athlone
    '0caa5a74-cc5e-5f04-a0cb-aa13466552d4',  # Valhalla Park
    '0c9f233c-6c38-5da4-a660-c6776f575667',  # St Montague Village
    'e0fa3712-df77-531e-a040-437dee019c3c',  # Firethorne (draft batch C5c, eight quotes re-matched)
}
SEPARATELY_AUDITED = {CAFDA, GQEBERHA, STEENBERG, STRANDFONTEIN} | C5


def load(path):
    return json.loads((ROOT / path).read_text())


def test_export_matches_private_audit_scope():
    if not PRIVATE_AUDIT.exists():
        pytest.skip('private Admiralty audit is gitignored; scope cross-check needs an audit machine')
    exported = load('docs/signals/fact-bases.json')['pins']
    audit_ids = {p['signal_id'] for p in json.loads(PRIVATE_AUDIT.read_text())}
    assert len(audit_ids) == 36
    assert not audit_ids & SEPARATELY_AUDITED
    assert set(exported) == audit_ids | SEPARATELY_AUDITED


def test_export_covers_frozen_scope_without_numeric_grades():
    exported = load('docs/signals/fact-bases.json')['pins']
    assert len(exported) == 45
    assert sum(len(p['facts']) for p in exported.values()) == 270   # 222 + six each for Gqeberha, Steenberg, Strandfontein, the four C5 pins and Firethorne
    assert SEPARATELY_AUDITED <= set(exported)
    text = json.dumps(exported)
    for key in ('credibility', 'source_grades', 'pin_notes', 'text_sha256'):
        assert key not in text


def test_both_conflict_shapes_and_caveats_are_preserved():
    pins = load('docs/signals/fact-bases.json')['pins']
    seweding = next(p for sid, p in pins.items() if sid.startswith('64b7adb5'))
    date = seweding['facts'][1]
    assert date['conflicts'] and 'Sunday' in date['detail']
    assert 'Saturday' in date['detail']
    wesbank = next(p for sid, p in pins.items() if sid.startswith('218c685c'))
    date = wesbank['facts'][1]
    assert len(date['conflicts']) == 2
    assert 'impossible' in date['detail']


def test_cafda_entry_rests_on_one_police_account():
    entry = load('docs/signals/fact-bases.json')['pins'][CAFDA]
    assert entry['status'] == 'audited'
    facts = entry['facts']
    assert len(facts) == 6
    assert facts[0]['claim'] == 'A man, 41, dead.'
    text = json.dumps(entry)
    assert 'Wesley Twigg' in text
    assert 'Captain' not in text and 'Sergeant' not in text
    assert all(len(f['sources']) == 1 for f in facts)
    assert all(f['sources'][0]['url'] == 'https://smilefm.co.za/retreat-clinic-reopened-after-fatal-shooting/' for f in facts)
    assert {f['basis'] for f in facts} == {'Police account only', 'Undetermined in the saved reports'}


def test_missing_pin_is_explicitly_unaudited():
    from tools.publish import _fact_basis_for
    assert _fact_basis_for('nonexistent-test-pin') == {'status': 'not yet audited', 'facts': []}
    sid = next(iter(load('docs/signals/fact-bases.json')['pins']))
    assert _fact_basis_for(sid)['status'] == 'audited'


def test_wording_and_provenance_corrections():
    nw = (ROOT / 'docs/signals/north-west-killings-2026-10-19.json').read_text()
    wc = (ROOT / 'docs/signals/western-cape-murders-2026-10-18-batch2.json').read_text()
    assert 'date not supplied in the saved SAPS statement' in nw
    assert 'police say the motive is believed to be gang-related (Daily Voice)' in wc
    pins = load('docs/signals/provenance.json')['pins']
    site = next(p for sid, p in pins.items() if sid.startswith('0c5e795f'))
    wes = next(p for sid, p in pins.items() if sid.startswith('218c685c'))
    assert site['other_voices'] == ['Phiwe, community member']
    assert any('School Facebook post' in s for s in wes['other_voices'])
