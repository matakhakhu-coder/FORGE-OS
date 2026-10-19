"""Regression checks for the approved private-audit publication preparation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


def test_export_covers_frozen_scope_without_numeric_grades():
    exported = load('docs/signals/fact-bases.json')['pins']
    audit = load('private/admiralty-audit/audit.json')
    assert set(exported) == {p['signal_id'] for p in audit}
    assert len(exported) == 36
    assert sum(len(p['facts']) for p in exported.values()) == 216
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
