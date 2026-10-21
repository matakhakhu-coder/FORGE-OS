"""The staging tool records its full query list and run settings in every output file."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import stage_gnews_window as s  # noqa: E402


def _item(q, title, when):
    return {'title': title, 'link': f'https://example.test/{title}', 'source_name': 'Outlet',
            'pubdate_parsed': when, 'query': q, 'description': ''}


def _offline(monkeypatch, feeds):
    """feeds maps query -> list of items, or None for a failed fetch."""
    g = s.g
    monkeypatch.setattr(g, 'REQUEST_DELAY', 0)
    monkeypatch.setattr(g, '_build_rss_url', lambda q: f'rss://{q}')
    monkeypatch.setattr(g, '_fetch_rss', lambda url: None if feeds[url[6:]] is None else url.encode())
    monkeypatch.setattr(g, '_parse_rss_items', lambda xml, q, days: list(feeds[q]))
    monkeypatch.setattr(g, 'build_signal', lambda it, crime: {'external_id': 'gnews:' + it['title'],
                                                            'title': it['title']})


def test_every_query_and_setting_is_recorded_even_with_no_results(monkeypatch, tmp_path):
    feeds = {
        'Delft shooting': [_item('Delft shooting', 'a', '2026-10-20T10:00:00'),
                           _item('Delft shooting', 'old', '2026-09-01T10:00:00')],
        'Khayelitsha murder': [_item('Khayelitsha murder', 'a', '2026-10-20T10:00:00')],
        'Nyanga murder': [],
        'Mfuleni shooting': None,
    }
    _offline(monkeypatch, feeds)
    out = tmp_path / 'staged.json'
    keywords = 'Delft shooting| Khayelitsha murder |Nyanga murder|Mfuleni shooting'
    assert s.main(['--keywords', keywords, '--from', '2026-10-19', '--to', '2026-10-21',
                   '--out', str(out)]) == 0

    data = json.loads(out.read_text())
    run = data['run']
    assert run['keywords_arg'] == keywords
    assert (run['from'], run['to']) == ('2026-10-19', '2026-10-21')
    assert run['query_count'] == 4
    assert [q['query'] for q in run['queries']] == list(feeds)
    for key in ('window_timezone', 'lookback_days', 'request_delay_s', 'started_utc', 'finished_utc'):
        assert key in run
    by_q = {q['query']: q for q in run['queries']}
    assert by_q['Delft shooting'] == {'query': 'Delft shooting', 'rss_url': 'rss://Delft shooting',
                                      'fetch_ok': True, 'fetched': 2, 'in_window': 1, 'new_in_window': 1}
    assert by_q['Khayelitsha murder']['in_window'] == 1
    assert by_q['Khayelitsha murder']['new_in_window'] == 0      # a duplicate leaves a trace here
    assert by_q['Nyanga murder'] == {'query': 'Nyanga murder', 'rss_url': 'rss://Nyanga murder',
                                     'fetch_ok': True, 'fetched': 0, 'in_window': 0, 'new_in_window': 0}
    assert by_q['Mfuleni shooting']['fetch_ok'] is False

    assert run['unique_items'] == 1
    assert data['items'] == [{'external_id': 'gnews:a', 'title': 'a', 'outlet': 'Outlet',
                              'published_sast': '2026-10-20 12:00', 'link': 'https://example.test/a',
                              'queries': ['Delft shooting', 'Khayelitsha murder']}]


def test_a_run_that_finds_nothing_still_writes_its_queries(monkeypatch, tmp_path):
    _offline(monkeypatch, {'Gugulethu murder': [], 'Bishop Lavis shooting': []})
    out = tmp_path / 'empty.json'
    s.main(['--keywords', 'Gugulethu murder|Bishop Lavis shooting', '--from', '2026-10-19',
            '--to', '2026-10-19', '--out', str(out)])
    data = json.loads(out.read_text())
    assert data['items'] == []
    assert [q['query'] for q in data['run']['queries']] == ['Gugulethu murder', 'Bishop Lavis shooting']
