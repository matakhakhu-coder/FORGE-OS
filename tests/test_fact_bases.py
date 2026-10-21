"""Regression checks for the approved private-audit publication preparation."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Gitignored (private/ is never committed), so it exists only on audit machines.
PRIVATE_AUDIT = ROOT / 'private/admiralty-audit/audit.json'
# Audited separately after the frozen 36-pin Admiralty audit; the private audit is not widened.
CAFDA = '7aa4eea1-c1f4-55af-b761-d55ff2df3a51'
SEPARATELY_AUDITED = {CAFDA}


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
    assert len(exported) == 37
    assert sum(len(p['facts']) for p in exported.values()) == 222
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
