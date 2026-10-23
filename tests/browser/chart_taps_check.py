# Manual phone/desktop check of interactive charts Batches 4 and 5 in a built
# dist/index.html (not run by pytest): tap to see the records, and the stream
# switches shared with the map. Serve dist/ (python3 -m http.server 8952 -d dist), then:
#   python3 tests/browser/chart_taps_check.py http://127.0.0.1:8952/
# Needs the Python playwright package and its Chromium. Screenshots go to
# /tmp/zyd/shots/. Prints a PASS/FAIL line per check; exits 1 if any fails.
# Every expected number comes from tools/record_charts (Python owns counting).
import json, os, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import record_charts as rc  # noqa: E402

BASE = sys.argv[1].rstrip('/') + '/'
OUT = '/tmp/zyd/shots/'
os.makedirs(OUT, exist_ok=True)

records = json.loads(urllib.request.urlopen(BASE + 'records.json').read())['records']
table = rc.timeline_table(records, rc.chart_counts(records))
AX = table['axes']
LAST = len(table['steps']) - 1
ALL = {s['key'] for s in table['streams']}
LABEL = {s['key']: s['label'] for s in table['streams']}

results = []


def swipe(pg, x0, x1, y, dy=0):
    """A finger drag as raw touch events through CDP (as graph_pages_check)."""
    cdp = pg.context.new_cdp_session(pg)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y}]})
    for i in range(1, 11):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                 'touchPoints': [{'x': x0 + (x1 - x0) * i / 10, 'y': y + dy * i / 10}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    pg.wait_for_timeout(900)


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  -> {detail}'))


def day_label(iso):
    y, m, d = iso.split('-')
    return f"{int(d)} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m) - 1]}"


def plural(n, w):
    return f'{n} {w}' + ('' if n == 1 else 's')


LIST = """(pid) => {
  const box = document.querySelector('#gp-page-' + pid + ' [data-gp-list]');
  if (!box || box.hidden) return null;
  return {title: (box.querySelector('.gp-list__title') || {}).textContent,
          ids: [...box.querySelectorAll('.gp-list__item')].map(li => li.getAttribute('data-id')),
          titles: [...box.querySelectorAll('.gp-list__t')].map(t => t.textContent),
          links: [...box.querySelectorAll('.gp-list__show')].map(a => a.getAttribute('href'))};
}"""

CHARTS = """() => {
  const n = s => [...document.querySelectorAll(s)].map(e => +e.textContent);
  const lab = document.querySelector('#gp-page-stream .gp-time__label');
  return {
    line: [...document.querySelectorAll('#gp-page-incident .gp-line__pt title')].map(t => +t.textContent.split(': ')[1].split(' ')[0]),
    summary: (document.querySelector('#gp-page-incident .gp-chart__summary') || {}).textContent,
    added: n('#gp-page-added .gp-col__n'),
    stream: n('#gp-page-stream .gp-bar__n'),
    province: n('#gp-page-province .gp-bar__n'),
    label: lab && lab.textContent,
    notes: [...document.querySelectorAll('[data-gp-streams-note]')].map(e => e.hidden ? '' : e.textContent),
    chips: [...document.querySelectorAll('#gp-page-incident [data-gp-stream]')].map(b => [b.getAttribute('data-gp-stream'), b.getAttribute('aria-pressed')]),
    switches: [...document.querySelectorAll('#gp-page-stream [role=switch]')].map(b => b.getAttribute('aria-checked')),
    mapToggles: Object.fromEntries([...document.querySelectorAll('.stream-toggle')].map(t => [t.dataset.streamToggle, t.classList.contains('on')])),
    shared: window.ZADStreams && ZADStreams.visible(),
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  };
}"""


def expect_charts(step, on):
    c = rc.counts_from_table(table, step, on)
    return {'incident': c['incident']['days'], 'added': c['added']['days'], 'stream': c['stream'],
            'province': c['province'], 'undated': c['incident']['undated'],
            'later_step': c['incident']['later_step'], 'added_undated': c['added']['undated'], 'total': c['total']}


def charts_match(sh, step, on):
    c = expect_charts(step, on)
    return (sh['line'] == c['incident'] and sh['added'] == c['added'] and sh['stream'] == c['stream']
            and sh['province'] == c['province']), c


def total_of(sh, c):
    return {'line': sum(sh['line']) + c['undated'] + c['later_step'],
            'added': sum(sh['added']) + c['added_undated'], 'stream': sum(sh['stream']),
            'province': sum(sh['province'])}


with sync_playwright() as p:
    b = p.chromium.launch()
    for name, ctx_args in [
        ('phone', dict(**p.devices['iPhone 13'])),
        ('android', dict(**p.devices['Pixel 5'])),
        ('desktop', dict(viewport={'width': 1366, 'height': 900})),
    ]:
        touch = name != 'desktop'
        ctx = b.new_context(**ctx_args)
        pg = ctx.new_page()
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        pg.goto(BASE + 'index.html', wait_until='load')
        pg.wait_for_timeout(800)
        pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()")
        pg.wait_for_timeout(700)
        titles = pg.evaluate("""() => { const T = window.ZADRecordCharts.table, i = T.columns.indexOf('id');
          return Object.fromEntries(T.rows.map(r => [r[i], (window.forgeSignalInfo(r[i]) || {}).title || null])); }""")

        def tap(sel):
            loc = pg.locator(sel).first
            loc.scroll_into_view_if_needed()
            if touch:
                loc.tap()
            else:
                loc.click()
            pg.wait_for_timeout(120)

        def tap_day(day):
            # a real tap at the day's column, a little above the line's base
            pg.click('#gp-tab-incident'); pg.wait_for_timeout(500)
            r = pg.locator(f'#gp-page-incident .gp-line__hit[data-day="{day}"]').bounding_box()
            x, y = r['x'] + r['width'] / 2, r['y'] + r['height'] * 0.4
            if touch:
                pg.touchscreen.tap(x, y)
            else:
                pg.mouse.click(x, y)
            pg.wait_for_timeout(150)

        # ── Batch 4: tap to see the records ──
        tap_day('2026-10-19')
        ls = pg.evaluate(LIST, 'incident')
        want = rc.ids_from_table(table, LAST, 'incident', '2026-10-19')
        check(f'{name}: tapping 19 Oct at now lists its {len(want)} records',
              ls and len(want) == 4 and sorted(ls['ids']) == sorted(want) and ls['title'] == '19 Oct: 4 records', ls)
        check(f'{name}: list titles come from the map\'s pin data, with Show on map links',
              ls and all(t and t == titles[i] for t, i in zip(ls['titles'], ls['ids']))
              and ls['links'] == [f'?signal={i}' for i in ls['ids']], ls and ls['titles'])
        if name == 'phone':
            pg.screenshot(path=OUT + 'b45-phone-list-19oct.png')
        # the list follows the slider
        mid = next((s for s in range(LAST + 1) if 0 < len(rc.ids_from_table(table, s, 'incident', '2026-10-19')) < 4), 3)
        pg.evaluate(f'ZADRecordCharts.setStep({mid})'); pg.wait_for_timeout(450)
        ls3 = pg.evaluate(LIST, 'incident')
        want3 = rc.ids_from_table(table, mid, 'incident', '2026-10-19')
        check(f'{name}: the open list follows the slider (stop {mid + 1}: {len(want3)} of 4)',
              ls3 and sorted(ls3['ids']) == sorted(want3) and ls3['title'] == f'19 Oct: {plural(len(want3), "record")}', (ls3, want3))
        pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(450)
        # previous / next day buttons
        tap('#gp-page-incident .gp-list__btn[aria-label="Next day"]')
        ls = pg.evaluate(LIST, 'incident')
        check(f'{name}: next day button moves the list to 20 Oct',
              ls and ls['title'].startswith('20 Oct:') and sorted(ls['ids']) == sorted(rc.ids_from_table(table, LAST, 'incident', '2026-10-20')), ls)

        # every tap target at now: the list matches the count on the chart and Python
        bad = []
        for i, d in enumerate(AX['incident_days']):
            pg.evaluate(f"ZADRecordCharts.openList('incident', '{d}')")
            ls = pg.evaluate(LIST, 'incident')
            if sorted(ls['ids']) != sorted(rc.ids_from_table(table, LAST, 'incident', d)):
                bad.append(d)
        shown = pg.evaluate(CHARTS)
        for key in ('undated', 'later_step'):
            n = expect_charts(LAST, None)[key]
            if n:
                tap(f'#gp-page-incident .gp-chart__summary button:nth-of-type({1 if key == "undated" else 2})')
                ls = pg.evaluate(LIST, 'incident')
                if not ls or sorted(ls['ids']) != sorted(rc.ids_from_table(table, LAST, 'incident', key)) or len(ls['ids']) != n:
                    bad.append((key, ls))
        pg.click('#gp-tab-added'); pg.wait_for_timeout(500)
        for i, d in enumerate(AX['added_days']):
            tap(f'#gp-page-added .gp-col:nth-child({i + 1}) .gp-col__btn')
            ls = pg.evaluate(LIST, 'added')
            if not ls or sorted(ls['ids']) != sorted(rc.ids_from_table(table, LAST, 'added', d)) or len(ls['ids']) != shown['added'][i]:
                bad.append(('added', d))
        pg.click('#gp-tab-stream'); pg.wait_for_timeout(500)
        for i, lab in enumerate(AX['stream']):
            tap(f'#gp-page-stream .gp-bar:nth-child({i + 1}) .gp-bar__count')
            ls = pg.evaluate(LIST, 'stream')
            if not ls or sorted(ls['ids']) != sorted(rc.ids_from_table(table, LAST, 'stream', lab)) or len(ls['ids']) != shown['stream'][i]:
                bad.append(('stream', lab))
        pg.click('#gp-tab-province'); pg.wait_for_timeout(500)
        for i, lab in enumerate(AX['province']):
            tap(f'#gp-page-province .gp-bar:nth-child({i + 1}) .gp-bar__btn')
            ls = pg.evaluate(LIST, 'province')
            if not ls or sorted(ls['ids']) != sorted(rc.ids_from_table(table, LAST, 'province', lab)) or len(ls['ids']) != shown['province'][i]:
                bad.append(('province', lab))
        check(f'{name}: every bar, column, line day and the two counts list exactly the records counted there', not bad, bad)
        if name == 'phone':
            pg.screenshot(path=OUT + 'b45-phone-province-list.png')

        # tap targets
        sizes = pg.evaluate("""() => [...document.querySelectorAll('#panel-graph .gp-page--chart .gp-hit, #panel-graph .gp-list__show')]
          .filter(e => e.getClientRects().length).map(e => { const r = e.getBoundingClientRect();
            return [e.className + '|' + e.textContent.slice(0, 20), Math.round(r.width), Math.round(r.height)]; })""")
        small = [s for s in sizes if s[2] < 44 or (s[1] < 44 and 'gp-list__show' not in s[0])]
        check(f'{name}: every chart button and link is at least 44px ({len(sizes)} measured)', not small, small[:5])
        pg.click('#gp-tab-incident'); pg.wait_for_timeout(500)
        hit = pg.evaluate("""() => [...document.querySelectorAll('#gp-page-incident .gp-line__hit')].map(r => { const b = r.getBoundingClientRect(); return [b.width, b.height]; })""")
        check(f'{name}: line day columns are the full chart height (>= 44px tall)',
              hit and min(h for _, h in hit) >= 44, hit[:2])
        print(f'INFO {name}: line day columns are {min(w for w, _ in hit):.1f}px wide (21 days share the chart width)')

        # Show on map opens the map's event drawer for that record
        pg.evaluate("ZADRecordCharts.openList('incident', '2026-10-19')"); pg.wait_for_timeout(150)
        first = pg.evaluate(LIST, 'incident')
        tap('#gp-page-incident .gp-list__show')
        pg.wait_for_timeout(700)
        dr = pg.evaluate("() => ({open: !document.getElementById('signal-drawer').hidden, title: document.getElementById('sd-title').textContent, url: location.search})")
        check(f'{name}: Show on map opens the event drawer for that record',
              dr['open'] and dr['title'] == first['titles'][0] and first['ids'][0] in dr['url'], dr)
        if name == 'phone':
            pg.screenshot(path=OUT + 'b45-phone-drawer.png')
        pg.keyboard.press('Escape'); pg.wait_for_timeout(500)
        pg.evaluate("document.querySelector('[data-panel=\"graph\"]').getAttribute('aria-pressed') !== 'true' && !document.getElementById('panel-graph').classList.contains('open') && document.querySelector('[data-panel=\"graph\"]').click()")
        pg.wait_for_timeout(700)
        if not pg.evaluate("() => document.getElementById('gp-track').getBoundingClientRect().width > 0"):
            pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()"); pg.wait_for_timeout(700)

        # keyboard
        pg.click('#gp-tab-province'); pg.wait_for_timeout(500)
        pg.evaluate("ZADRecordCharts.closeList('province')")
        pg.locator('#gp-page-province .gp-bar__btn').first.focus()
        pg.keyboard.press('Enter'); pg.wait_for_timeout(150)
        k1 = pg.evaluate(LIST, 'province')
        pg.keyboard.press('Tab'); pg.keyboard.press('Shift+Tab')
        pg.locator('#gp-page-province .gp-list__close').focus()
        pg.keyboard.press('Escape'); pg.wait_for_timeout(150)
        k2 = pg.evaluate(LIST, 'province')
        foc = pg.evaluate("() => document.activeElement.classList.contains('gp-bar__btn')")
        check(f'{name}: keyboard: Enter on a bar opens its list, Escape closes it and focus returns to the bar',
              k1 and k1['title'].startswith(AX['province'][0]) and k2 is None and foc, (k1, k2, foc))
        pg.click('#gp-tab-incident'); pg.wait_for_timeout(500)
        pg.evaluate("ZADRecordCharts.closeList('incident')")
        pg.locator('#gp-page-incident .gp-line').focus()
        pg.keyboard.press('End'); pg.wait_for_timeout(100)
        e1 = pg.evaluate(LIST, 'incident')
        pg.keyboard.press('ArrowLeft'); pg.keyboard.press('ArrowLeft'); pg.wait_for_timeout(100)
        e2 = pg.evaluate(LIST, 'incident')
        page_now = pg.evaluate("() => Math.round(document.getElementById('gp-track').scrollLeft / document.getElementById('gp-track').clientWidth)")
        check(f'{name}: keyboard on the line: End, then arrows step through the days; the page stays',
              e1 and e1['title'].startswith(day_label(AX['incident_days'][-1]) + ':')
              and e2 and e2['title'].startswith(day_label(AX['incident_days'][-3]) + ':') and page_now == 1, (e1, e2, page_now))
        pg.keyboard.press('Escape'); pg.wait_for_timeout(100)

        # swiping still flips pages from the charts, and a tap never does
        if touch:
            pg.click('#gp-tab-province'); pg.wait_for_timeout(500)
            pg.evaluate("ZADRecordCharts.closeList('province'); document.getElementById('gp-page-province').scrollTop = 0")
            pg.wait_for_timeout(200)
            bb = pg.locator('#gp-page-province .gp-bar__btn').first.bounding_box()
            tr = pg.locator('#gp-track').bounding_box()
            swipe(pg, tr['x'] + tr['width'] * 0.15, tr['x'] + tr['width'] * 0.85, bb['y'] + bb['height'] / 2)
            pg1 = pg.evaluate("() => Math.round(document.getElementById('gp-track').scrollLeft / document.getElementById('gp-track').clientWidth)")
            open_after = pg.evaluate(LIST, 'province')
            pg.click('#gp-tab-incident'); pg.wait_for_timeout(500)
            pg.evaluate("ZADRecordCharts.closeList('incident')")
            lb = pg.locator('#gp-page-incident .gp-line').bounding_box()
            swipe(pg, tr['x'] + tr['width'] * 0.85, tr['x'] + tr['width'] * 0.15, lb['y'] + lb['height'] / 2)
            pg2 = pg.evaluate("() => Math.round(document.getElementById('gp-track').scrollLeft / document.getElementById('gp-track').clientWidth)")
            check(f'{name}: swiping on a bar or on the line still flips the page and opens no list',
                  pg1 == 3 and pg2 == 2 and open_after is None and pg.evaluate(LIST, 'incident') is None, (pg1, pg2, open_after))

        # ── Batch 5: stream switches shared with the map ──
        pg.evaluate("['incident','added','stream','province'].forEach(p => ZADRecordCharts.closeList(p))")
        pg.click('#gp-tab-incident'); pg.wait_for_timeout(500)
        for k in ('INFRASTRUCTURE', 'PRIORITY', 'GLOBAL'):
            tap(f'#gp-page-incident [data-gp-stream="{k}"]')
        pg.wait_for_timeout(400)
        sh = pg.evaluate(CHARTS)
        ok, c = charts_match(sh, LAST, {'CRIME_INTEL'})
        tot = total_of(sh, c)
        crime_now = c['total']
        check(f'{name}: only Crime & Security on: every chart counts its {crime_now} records at now',
              ok and crime_now == 62 and set(tot.values()) == {62}, (tot, crime_now))
        check(f'{name}: the map follows the chart switches (Streams panel and pins state)',
              sh['mapToggles'] == {k: k == 'CRIME_INTEL' for k in ALL} and sh['shared'] == {k: k == 'CRIME_INTEL' for k in ALL}, (sh['mapToggles'], sh['shared']))
        check(f'{name}: caption says "Showing 1 of 4 streams" on all four pages, the slider label too',
              all(n.startswith('Showing 1 of 4 streams (Crime & Security)') for n in sh['notes']) and len(sh['notes']) == 4
              and sh['label'].endswith(f'{crime_now} of {len(records)} records (1 of 4 streams)'), (sh['notes'], sh['label']))
        if name == 'phone':
            pg.screenshot(path=OUT + 'b45-phone-crime-only.png')
        tap_day('2026-10-19')
        ls = pg.evaluate(LIST, 'incident')
        check(f'{name}: lists only hold records in the streams that are on',
              ls and sorted(ls['ids']) == sorted(rc.ids_from_table(table, LAST, 'incident', '2026-10-19', {'CRIME_INTEL'})), ls)
        # the slider with a stream filter: every step matches Python
        badsteps = []
        for s in range(LAST + 1):
            pg.evaluate(f'ZADRecordCharts.setStep({s})')
            okk, _ = charts_match(pg.evaluate(CHARTS), s, {'CRIME_INTEL'})
            if not okk:
                badsteps.append(s)
        pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(450)
        check(f'{name}: with streams off, every slider stop still matches Python', not badsteps, badsteps)
        # the map's own Streams panel switch moves the charts too
        pg.evaluate("document.querySelector('.stream-toggle[data-stream-toggle=\"INFRASTRUCTURE\"]').click()")
        pg.wait_for_timeout(400)
        sh = pg.evaluate(CHARTS)
        ok, c = charts_match(sh, LAST, {'CRIME_INTEL', 'INFRASTRUCTURE'})
        check(f'{name}: switching a stream on in the map\'s Streams panel updates the charts and their switches',
              ok and dict(sh['chips'])['INFRASTRUCTURE'] == 'true' and sh['notes'][0].startswith('Showing 2 of 4 streams'), (sh['chips'], sh['notes'][0]))
        # Streams page: tapping a bar switches its stream
        pg.click('#gp-tab-stream'); pg.wait_for_timeout(500)
        gi = AX['stream'].index(LABEL['GLOBAL'])
        tap(f'#gp-page-stream .gp-bar:nth-child({gi + 1}) [role=switch]')
        pg.wait_for_timeout(400)
        sh = pg.evaluate(CHARTS)
        ok, c = charts_match(sh, LAST, {'CRIME_INTEL', 'INFRASTRUCTURE', 'GLOBAL'})
        check(f'{name}: tapping the Africa & World bar on Streams turns that stream on for the charts and the map',
              ok and sh['switches'][gi] == 'true' and sh['mapToggles']['GLOBAL'] and dict(sh['chips'])['GLOBAL'] == 'true', (sh['switches'], sh['mapToggles']))
        tap(f'#gp-page-stream .gp-bar:nth-child({gi + 1}) [role=switch]')
        pg.wait_for_timeout(400)
        sh = pg.evaluate(CHARTS)
        check(f'{name}: tapping it again turns it off (bar at 0, marked off)',
              sh['switches'][gi] == 'false' and sh['stream'][gi] == 0 and not sh['mapToggles']['GLOBAL'], (sh['switches'], sh['stream']))
        if name == 'phone':
            pg.screenshot(path=OUT + 'b45-phone-streams-page.png')
        # all back on
        for k in ('PRIORITY', 'GLOBAL'):
            pg.evaluate(f"document.querySelector('.stream-toggle[data-stream-toggle=\"{k}\"]').click()")
        pg.wait_for_timeout(400)
        sh = pg.evaluate(CHARTS)
        ok, c = charts_match(sh, LAST, ALL)
        check(f'{name}: all streams back on: 72 of 72, no note',
              ok and sh['label'].endswith(f'(now) \u00b7 {len(records)} of {len(records)} records') and not any(sh['notes']), (sh['label'], sh['notes']))
        # turning a stream off in the map's Streams panel takes it off the charts
        pg.evaluate("document.querySelector('.stream-toggle[data-stream-toggle=CRIME_INTEL]').click()")
        pg.wait_for_timeout(400)
        sh2 = pg.evaluate(CHARTS)
        ok2, c2 = charts_match(sh2, LAST, ALL - {'CRIME_INTEL'})
        check(f'{name}: turning Crime & Security off in the map\'s Streams panel leaves the charts its other {c2["total"]} records',
              ok2 and c2['total'] == len(records) - 62 and dict(sh2['chips'])['CRIME_INTEL'] == 'false', (c2['total'], sh2['chips']))
        pg.evaluate("document.querySelector('.stream-toggle[data-stream-toggle=CRIME_INTEL]').click()")
        pg.wait_for_timeout(400)
        check(f'{name}: nothing runs off the side', sh['overflow'] <= 0, sh['overflow'])
        check(f'{name}: no browser errors', not errors, errors[:3])
        ctx.close()

    # no script: none of the new controls, today's charts printed as before
    ctx = b.new_context(java_script_enabled=False, viewport={'width': 390, 'height': 844})
    pg = ctx.new_page()
    pg.goto(BASE + 'index.html', wait_until='load')
    ns = pg.evaluate("""() => ({chips: [...document.querySelectorAll('[data-gp-streams]')].every(e => e.hidden),
      lists: [...document.querySelectorAll('[data-gp-list]')].every(e => e.hidden && !e.children.length),
      buttons: document.querySelectorAll('#panel-graph .gp-page--chart button').length,
      bars: document.querySelectorAll('#gp-page-province .gp-bar__n').length})""")
    check('no script: no switches, no lists, no chart buttons; the charts are printed', ns['chips'] and ns['lists'] and ns['buttons'] == 3 * len(table['streams']) and ns['bars'] == len(AX['province']), ns)
    ctx.close()
    b.close()

print(f'\n{sum(results)} of {len(results)} checks passed')
sys.exit(0 if all(results) else 1)
