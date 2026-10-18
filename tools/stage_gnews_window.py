"""Stage Google News results for review without touching any database.

Uses google_news_collector's own URL builder, fetcher, parser and signal
builder, then keeps only items whose publication date (SAST) falls inside
[--from, --to]. Writes a private JSON file; never calls persist_signals.

This is the first step of the hand-checked pin workflow: stage, read each
source by hand, write the checked pins into a docs/signals batch, then load
them with tools/checked_signals.py. (Used for the Western Cape batches of
18 Oct 2026; committed in Sprint 1.)

    python tools/stage_gnews_window.py \
        --keywords "Khayelitsha murder|Delft shooting" \
        --from 2026-10-01 --to 2026-10-17 --out /tmp/wc-staged.json
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forage.collectors import google_news_collector as g  # noqa: E402

SAST = timezone(timedelta(hours=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keywords', required=True, help="queries separated by '|'")
    ap.add_argument('--from', dest='frm', required=True, help='YYYY-MM-DD, SAST')
    ap.add_argument('--to', dest='to', required=True, help='YYYY-MM-DD, SAST, inclusive')
    ap.add_argument('--out', required=True, help='JSON file to write (keep it out of the repo)')
    a = ap.parse_args(argv)

    start = datetime.strptime(a.frm, '%Y-%m-%d').replace(tzinfo=SAST)
    end = datetime.strptime(a.to, '%Y-%m-%d').replace(tzinfo=SAST) + timedelta(days=1)
    days = (datetime.now(timezone.utc) - start).days + 2
    queries = [k.strip() for k in a.keywords.split('|') if k.strip()]
    seen, out = {}, []
    for i, q in enumerate(queries):
        xml = g._fetch_rss(g._build_rss_url(q))
        items = g._parse_rss_items(xml, q, days) if xml else []
        kept = 0
        for it in items:
            try:
                dt = datetime.strptime(it['pubdate_parsed'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if not (start <= dt < end):
                continue
            sig = g.build_signal(it, g._is_crime_context(q))
            key = sig['external_id']
            if key in seen:
                seen[key]['queries'].append(q)
                continue
            rec = {'external_id': key, 'title': sig['title'], 'outlet': it.get('source_name'),
                   'published_sast': dt.astimezone(SAST).strftime('%Y-%m-%d %H:%M'),
                   'link': it['link'], 'queries': [q]}
            seen[key] = rec
            out.append(rec)
            kept += 1
        print(f'[{i+1}/{len(queries)}] {q}: {len(items)} fetched, {kept} new in window', flush=True)
        if i < len(queries) - 1:
            time.sleep(g.REQUEST_DELAY)
    out.sort(key=lambda r: r['published_sast'])
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f'{len(out)} unique items -> {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
