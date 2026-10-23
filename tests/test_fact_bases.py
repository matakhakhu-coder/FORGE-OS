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
# Limpopo batch (limpopo-killings-2026-10-22.json): each fact matched to a quote in the
# batch's own evidence pass, outside the frozen 36-pin Admiralty audit.
LIMPOPO = {
    'f217ddf4-4573-5fe1-995d-64f8da89a86d',  # A Mamaolo tavern owner
    '0caff1cd-7cbe-51b2-aab4-ea3528b56f88',  # B near Peter Mokaba Stadium
    '1f7dbda3-e019-52ae-8832-8cae6ab01b47',  # C Rostock (estimated map point)
    'c889d991-c9d9-541d-9976-934dc46c385c',  # D Malope Section, Masemola
    'fb9450c3-dd3b-5402-8c4f-5e26f433d317',  # E Sekurung (estimated map point)
    'c99779f1-095f-5d2d-88c3-19f74e8c39d6',  # F Nkuri-Tomu
    'eaf77245-e677-5ccb-955d-764253f566d2',  # G Madonsi Boltman B
    'cd3f9959-d1de-58d2-8a7d-cf0cb3bad8e7',  # H Martinique village
}
# Mpumalanga batch (mpumalanga-killings-2026-10-23.json): each fact matched word for word to the
# saved source text on 23 Oct 2026, outside the frozen 36-pin Admiralty audit.
MPUMALANGA = {
    '3fd7a79d-11b5-5499-a2c9-71ea8925984e',  # A Siyabuswa
    '9188e545-8113-50c9-ac7e-98ac9a03f457',  # B Lillydale Trust
    '0710d74d-c67c-5899-95db-54e2f4fab60d',  # C Bhekiswayo Trust, Masoyi (police are a party)
    '3679ce0d-0799-50c5-9fdf-76e85d662b52',  # D Thusi Village (estimated map point)
    'a98cb800-3be8-586b-9997-84c9041cc35b',  # E Farm near Amersfoort (estimated map point)
}
LIMPOPO_INFRA = {
    '638ae62e-cbf5-53c3-95aa-4d36af9c2419',
    '736aae9c-e6de-5bb2-acb1-4fb32bb23c99',
    'b2ec397b-458f-55c5-9882-65064085e1be',
    '34ab4d0c-f76b-5775-9f43-faf729c21de4',
}  # Seshego substation, Tzaneen burst main, Polokwane PS3 leaks, Musina boreholes (approved 23 Oct 2026)
SEPARATELY_AUDITED = {CAFDA, GQEBERHA, STEENBERG, STRANDFONTEIN} | C5 | LIMPOPO | MPUMALANGA | LIMPOPO_INFRA


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
    assert len(exported) == 62                                      # 36 audited + 26 separately audited
    assert sum(len(p['facts']) for p in exported.values()) == 367   # 222 + six each for Gqeberha, Steenberg, Strandfontein, the four C5 pins and Firethorne, + 43 Limpopo + 30 Mpumalanga + 24 Limpopo infrastructure
    assert sum(len(exported[s]['facts']) for s in LIMPOPO_INFRA) == 24  # six each
    assert all(exported[s]['status'] == 'audited' and exported[s]['police_party'] is False for s in LIMPOPO_INFRA)
    assert sum(len(exported[s]['facts']) for s in MPUMALANGA) == 30  # six each
    assert all(exported[s]['status'] == 'audited' for s in MPUMALANGA)
    assert exported['0710d74d-c67c-5899-95db-54e2f4fab60d']['police_party'] is True   # Masoyi
    assert sum(len(exported[s]['facts']) for s in LIMPOPO) == 43     # A, B, E six each; C, D, F, G, H five each
    assert all(exported[s]['status'] == 'audited' for s in LIMPOPO)
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
