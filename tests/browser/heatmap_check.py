# Manual phone/desktop check of interactive charts Batch 8 in a built
# dist/index.html (not run by pytest): the town by incident date heat map,
# tapping a cell (list + map filter), the slider, stream switches, weeks,
# keyboard, swiping and the no-script fallback. Serve dist/
# (python3 -m http.server 8953 -d dist), then:
#   python3 tests/browser/heatmap_check.py http://127.0.0.1:8953/
# Needs the Python playwright package and its Chromium. Screenshots go to
# /tmp/zyd/shots/. Prints a PASS/FAIL line per check; exits 1 if any fails.
# Every expected number comes from tools/record_charts and tools/record_towns
# (Python owns counting and the towns).
import json, os, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import record_charts as rc  # noqa: E402
from tools.record_towns import towns_for_charts  # noqa: E402

BASE = sys.argv[1].rstrip('/') + '/'
OUT = '/tmp/zyd/shots/'
os.makedirs(OUT, exist_ok=True)

records = json.loads(urllib.request.urlopen(BASE + 'records.json').read())['records']
towned, problems, _ = towns_for_charts(records)
table = rc.timeline_table(towned, rc.chart_counts(towned))
AX = table['axes']
LAST = len(table['steps']) - 1
ALL = {s['key'] for s in table['streams']}
COL = {c: i for i, c in enumerate(table['columns'])}
STREAM_KEY = {s['label']: s['key'] for s in table['streams']}
TOWNS = AX['town']
DAYS = AX['incident_days']
results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  -> {detail}'))


def plural(n, w):
    return f'{n} {w}' + ('' if n == 1 else 's')


def day_label(iso):
    return f'{int(iso[8:10])} Oct'


def ids(step, key, on=None):
    return sorted(rc.ids_from_table(table, step, 'town', key, on))


def cell_ids(key):
    # every record of the cell, before slider and streams (what the map is sent)
    return ids(LAST, key)


def swipe(pg, x0, x1, y, dy=0):
    cdp = pg.context.new_cdp_session(pg)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y}]})
    for i in range(1, 11):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                 'touchPoints': [{'x': x0 + (x1 - x0) * i / 10, 'y': y + dy * i / 10}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    pg.wait_for_timeout(900)


GRID = """() => {
  const page = document.getElementById('gp-page-town');
  const trs = [...page.querySelectorAll('.gp-heat tbody tr')];
  return {
    towns: trs.map(tr => tr.querySelector('.gp-heat__name').textContent),
    totals: trs.map(tr => +tr.querySelector('.gp-heat__total').textContent),
    nums: trs.map(tr => [...tr.querySelectorAll('td')].map(td => { const t = td.querySelector('.gp-heat__n').textContent; return t ? +t : 0; })),
    shade: trs.map(tr => [...tr.querySelectorAll('td')].map(td => +td.querySelector('.gp-heat__fill').style.opacity)),
    btns: page.querySelectorAll('.gp-heat__btn').length, townBtns: page.querySelectorAll('.gp-heat__townbtn').length,
    tabStops: [...page.querySelectorAll('.gp-heat button')].filter(b => b.tabIndex === 0).length,
    summary: page.querySelector('.gp-chart__summary').textContent,
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    pos: document.getElementById('gp-pos') ? document.getElementById('gp-pos').textContent : ''
  };
}"""

LIST = """() => {
  const box = document.querySelector('#gp-page-town [data-gp-list]');
  if (!box || box.hidden) return null;
  const bs = box.querySelectorAll('.gp-list__head .gp-list__btn');
  return {title: (box.querySelector('.gp-list__title') || {}).textContent,
          ids: [...box.querySelectorAll('.gp-list__item')].map(li => li.getAttribute('data-id')),
          titles: [...box.querySelectorAll('.gp-list__item .gp-list__t')].map(e => e.textContent),
          links: [...box.querySelectorAll('.gp-list__show')].map(a => a.getAttribute('href')),
          prevOff: bs[0] ? bs[0].disabled : null, nextOff: bs[1] ? bs[1].disabled : null};
}"""

NOTE = """() => { const n = document.getElementById('gp-mapnote'), c = document.getElementById('gp-mapnote-clear');
  const b = c.getBoundingClientRect();
  return {hidden: n.hidden, cell: document.getElementById('gp-mapnote-cell').textContent,
          cellHidden: document.getElementById('gp-mapnote-cell').hidden, clear: !c.hidden, btn: [b.width, b.height],
          time: document.getElementById('gp-mapnote-time').hidden ? '' : document.getElementById('gp-mapnote-time').textContent}; }"""


def want_grid(step, on=None):
    c = rc.counts_from_table(table, step, on)['town']
    nums = [c['cells'][i] + [c['undated'][i], c['later_step'][i]] for i in range(len(TOWNS))]
    return c, nums


with sync_playwright() as p:
    b = p.chromium.launch()
    check('python: every record has a town', not problems and all(r['town'] for r in towned), problems)
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
            (loc.tap if touch else loc.click)()
            pg.wait_for_timeout(200)

        def tap_cell(town, col):
            i, j = TOWNS.index(town), (DAYS.index(col) if col in DAYS else len(DAYS) + (col == 'later_step'))
            loc = pg.locator('#gp-page-town .gp-heat tbody tr').nth(i).locator('.gp-heat__btn').nth(j)
            loc.scroll_into_view_if_needed()
            (loc.tap if touch else loc.click)()
            pg.wait_for_timeout(250)

        pg.click('#gp-tab-town'); pg.wait_for_timeout(600)
        g = pg.evaluate(GRID)
        c, nums = want_grid(LAST)
        check(f'{name}: heat map is the last page, {len(TOWNS)} towns in Python\'s order and totals',
              g['towns'] == TOWNS and g['totals'] == c['rows'] and sum(g['totals']) == len(records), (g['towns'][:4], g['totals'][:4]))
        check(f'{name}: every cell shows Python\'s count at now (blank for zero)', g['nums'] == nums,
              [(TOWNS[i], r) for i, r in enumerate(g['nums']) if r != nums[i]][:2])
        mx = max(AX['town_max'], 1)
        okshade = all(abs(g['shade'][i][j] - ((0.18 + 0.82 * v / mx) if v else 0)) < 0.01
                      for i, row in enumerate(nums) for j, v in enumerate(row))
        check(f'{name}: shades follow the counts on today\'s fixed scale', okshade)
        check(f'{name}: one button per cell and town, one tab stop on the grid',
              g['btns'] == len(TOWNS) * (len(DAYS) + 2) and g['townBtns'] == len(TOWNS) and g['tabStops'] == 1,
              (g['btns'], g['townBtns'], g['tabStops']))
        dated = sum(sum(r) for r in c['cells'])
        check(f'{name}: summary says records on a day, date not stated, later step',
              g['summary'].startswith(f'{dated} on a day (1 Oct to 21 Oct)') and f"{sum(c['undated'])} date not stated" in g['summary']
              and f"{sum(c['later_step'])} later step in an older case" in g['summary'] and g['summary'].endswith(f'{len(TOWNS)} towns'), g['summary'])
        check(f'{name}: nothing runs off the side of the screen', g['overflow'] <= 0, g['overflow'])
        size = pg.evaluate("""() => { const bs = [...document.querySelectorAll('#gp-page-town .gp-heat__btn')].map(b => b.getBoundingClientRect());
          const tb = [...document.querySelectorAll('#gp-page-town .gp-heat__townbtn')].map(b => b.getBoundingClientRect());
          const box = document.querySelector('#gp-page-town .gp-heat-scroll');
          return {w: Math.min(...bs.map(r => r.width)), h: Math.min(...bs.map(r => r.height)), tw: Math.min(...tb.map(r => r.width)),
                  th: Math.min(...tb.map(r => r.height)), scroll: box.scrollWidth - box.clientWidth}; }""")
        print(f"INFO {name}: heat map cells are {size['w']:.1f} x {size['h']:.1f}px (all days); town names {size['tw']:.0f} x {size['th']:.0f}px; table box side-scroll {size['scroll']}px")
        if name == 'phone':
            pg.screenshot(path=OUT + 'b8-phone-heatmap.png', full_page=False)

        # ── tap a cell: list + map filter
        town0 = TOWNS[0]
        day0 = max(DAYS, key=lambda d: (len(ids(LAST, f'{town0}|{d}')), d))
        key0 = f'{town0}|{day0}'
        tap_cell(town0, day0)
        ls = pg.evaluate(LIST)
        w0 = ids(LAST, key0)
        check(f'{name}: tapping {town0}, {day_label(day0)} lists its {len(w0)} records',
              ls and sorted(ls['ids']) == w0 and ls['title'] == f'{town0}, {day_label(day0)}: {plural(len(w0), "record")}', (ls, w0))
        check(f'{name}: list titles come from the map\'s pin data, with Show on map links',
              ls and all(t and t == titles[i] for t, i in zip(ls['titles'], ls['ids'])) and ls['links'] == [f'?signal={i}' for i in ls['ids']],
              ls and ls['titles'])
        shown = sorted(pg.evaluate('ZADMapTime.shown()'))
        n = pg.evaluate(NOTE)
        check(f'{name}: the map shows only that town and day, with a note and a 44px Clear button',
              shown == w0 and not n['hidden'] and not n['cellHidden'] and n['clear']
              and n['cell'] == f'Map: {town0}, {day_label(day0)} \u00b7 {plural(len(w0), "record")}' and n['btn'][0] >= 44 and n['btn'][1] >= 44,
              (shown, w0, n))
        pk = pg.evaluate("() => [...document.querySelectorAll('#gp-page-town .gp-heat__btn.is-picked')].map(b => b.getAttribute('data-key'))")
        check(f'{name}: the tapped cell is marked', pk == [key0], pk)
        if name == 'phone':
            pg.screenshot(path=OUT + 'b8-phone-cell.png')

        # the list and the map follow the slider (Batch 6 map filter on top)
        mid = next((s for s in range(LAST + 1) if 0 < len(ids(s, key0)) < len(w0)), None)
        if mid is None:
            mid = next((s for s in range(LAST + 1) if len(ids(s, key0)) < len(w0)), 0)
        pg.evaluate(f'ZADRecordCharts.setStep({mid}, {{settled: true}})'); pg.wait_for_timeout(450)
        ls = pg.evaluate(LIST)
        wm = ids(mid, key0)
        shown = sorted(pg.evaluate('ZADMapTime.shown()'))
        onmap = sorted(set(rc.ids_on_map(table, mid)) & set(w0))
        n = pg.evaluate(NOTE)
        g = pg.evaluate(GRID)
        _, nm = want_grid(mid)
        check(f'{name}: at stop {mid + 1} the list, the cells and the map follow the slider ({len(wm)} of {len(w0)})',
              ls and sorted(ls['ids']) == wm and shown == onmap == wm and g['nums'] == nm and n['time'] and
              n['cell'] == f'Map: {town0}, {day_label(day0)} \u00b7 {plural(len(wm), "record")}', (ls, wm, shown, n))
        # every step: each town total and cell matches Python
        bad = []
        for s in range(LAST + 1):
            pg.evaluate(f'ZADRecordCharts.setStep({s}, {{settled: true}})')
            g = pg.evaluate(GRID)
            cs, ns = want_grid(s)
            if g['nums'] != ns or g['totals'] != cs['rows']:
                bad.append(s)
        check(f'{name}: at all {LAST + 1} slider stops every cell and town total matches Python', not bad, bad)
        pg.evaluate(f'ZADRecordCharts.setStep({LAST}, {{settled: true}})'); pg.wait_for_timeout(300)

        # previous / next day stay in the same town
        tap('#gp-page-town .gp-list__btn[aria-label="Next day"]')
        ls = pg.evaluate(LIST)
        nd = DAYS[DAYS.index(day0) + 1] if DAYS.index(day0) + 1 < len(DAYS) else None
        if nd:
            check(f'{name}: next day moves the list and the map to {town0}, {day_label(nd)}',
                  ls and ls['title'].startswith(f'{town0}, {day_label(nd)}:') and sorted(ls['ids']) == ids(LAST, f'{town0}|{nd}')
                  and sorted(pg.evaluate('ZADMapTime.shown()')) == ids(LAST, f'{town0}|{nd}'), ls)

        # every cell and town name at now: the list and the map match Python
        bad = []
        for t in TOWNS:
            for col in DAYS + ['undated', 'later_step', '*']:
                key = f'{t}|{col}'
                pg.evaluate("k => ZADRecordCharts.openList('town', k)", key)
                ls = pg.evaluate(LIST)
                want = ids(LAST, key)
                if not ls or sorted(ls['ids']) != want or sorted(pg.evaluate('ZADMapTime.shown()')) != want:
                    bad.append(key)
        check(f'{name}: all {len(TOWNS) * (len(DAYS) + 3)} cells and town names list exactly their records and filter the map to them',
              not bad, bad[:3])

        # stream switches on top
        on = {'CRIME_INTEL'}
        for k in ALL - on:
            pg.evaluate(f"ZADRecordCharts.setStream('{k}', false)")
        pg.wait_for_timeout(300)
        tkey = f'{TOWNS[0]}|*'
        pg.evaluate("k => ZADRecordCharts.openList('town', k)", tkey)
        g = pg.evaluate(GRID)
        cs, ns = want_grid(LAST, on)
        ls = pg.evaluate(LIST)
        shown = sorted(pg.evaluate('ZADMapTime.shown()'))
        n = pg.evaluate(NOTE)
        check(f'{name}: with only Crime & Security on, cells, totals, list and map count {sum(cs["rows"])} records',
              g['nums'] == ns and g['totals'] == cs['rows'] and sorted(ls['ids']) == ids(LAST, tkey, on) and shown == ids(LAST, tkey, on)
              and '(1 of 4 streams)' in n['cell'], (sum(cs['rows']), n))
        for k in ALL - on:
            pg.evaluate(f"ZADRecordCharts.setStream('{k}', true)")
        pg.wait_for_timeout(300)

        # Clear on the map note closes the list and brings all pins back
        pg.evaluate("() => document.getElementById('gp-mapnote-clear').click()"); pg.wait_for_timeout(300)
        n = pg.evaluate(NOTE)
        check(f'{name}: Clear on the map closes the list and shows all {len(records)} pins again',
              pg.evaluate(LIST) is None and n['hidden'] and len(pg.evaluate('ZADMapTime.shown()')) == len(records), n)
        # closing the list clears the map too
        tap_cell(town0, day0)
        tap('#gp-page-town .gp-list__btn[aria-label="Close the list"]')
        check(f'{name}: closing the list clears the map filter',
              pg.evaluate(LIST) is None and pg.evaluate(NOTE)['hidden'] and len(pg.evaluate('ZADMapTime.shown()')) == len(records))

        # Show on map opens the drawer for a record of the cell, and the pin stays
        tap_cell(town0, day0)
        first = pg.evaluate(LIST)
        tap('#gp-page-town .gp-list__show')
        pg.wait_for_timeout(700)
        dr = pg.evaluate("() => ({open: !document.getElementById('signal-drawer').hidden, title: document.getElementById('sd-title').textContent, url: location.search})")
        check(f'{name}: Show on map opens the event drawer for that record, cell filter still on',
              dr['open'] and dr['title'] == first['titles'][0] and first['ids'][0] in dr['url']
              and first['ids'][0] in pg.evaluate('ZADMapTime.shown()'), dr)
        if name == 'phone':
            pg.screenshot(path=OUT + 'b8-phone-drawer.png')
        pg.keyboard.press('Escape'); pg.wait_for_timeout(500)
        if not pg.evaluate("() => document.getElementById('gp-track').getBoundingClientRect().width > 0"):
            pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()"); pg.wait_for_timeout(700)
        pg.evaluate("ZADRecordCharts.clearCell()"); pg.wait_for_timeout(200)
        pg.click('#gp-tab-town'); pg.wait_for_timeout(500)

        # weeks: only that week's days, cells widen, list closes if its day leaves
        wk = AX['incident_weeks'][2]
        tap_cell(town0, DAYS[wk['from'] - 1] if wk['from'] > 0 else DAYS[0])
        pg.evaluate("ZADRecordCharts.pickWeek('town', 2)"); pg.wait_for_timeout(400)
        vis = pg.evaluate("""() => { const h = [...document.querySelectorAll('#gp-page-town .gp-heat thead th')].slice(1);
          const bs = [...document.querySelectorAll('#gp-page-town .gp-heat__btn')].filter(b => b.offsetParent).map(b => b.getBoundingClientRect().width);
          const n = document.querySelector('#gp-page-town .gp-weeks__note');
          return {days: h.filter(x => !x.hidden).map(x => x.getAttribute('data-day')), w: Math.min(...bs), note: n && !n.hidden ? n.textContent : ''}; }""")
        want_days = DAYS[wk['from']:wk['to'] + 1] + ['undated', 'later_step']
        check(f'{name}: {wk["label"]} shows only its {len(want_days) - 2} days plus ? and L; the list for a day outside it closes',
              vis['days'] == want_days and pg.evaluate(LIST) is None and vis['note'], vis)
        print(f"INFO {name}: inside a week the heat map cells are {vis['w']:.1f}px wide")
        pg.evaluate("ZADRecordCharts.pickWeek('town', -1)"); pg.wait_for_timeout(300)

        # keyboard: Enter opens, arrows move, Escape closes and returns focus
        if name == 'desktop':
            pg.evaluate("document.querySelector('#gp-page-town .gp-heat__townbtn').focus()")
            pg.keyboard.press('ArrowRight'); pg.keyboard.press('ArrowRight'); pg.keyboard.press('ArrowDown')
            fk = pg.evaluate("document.activeElement.getAttribute('data-key')")
            pg.keyboard.press('Enter'); pg.wait_for_timeout(200)
            ls = pg.evaluate(LIST)
            ok1 = fk == f'{TOWNS[1]}|{DAYS[1]}' and ls and ls['title'].startswith(f'{TOWNS[1]}, {day_label(DAYS[1])}:')
            pg.evaluate("document.querySelector('#gp-page-town .gp-heat__btn[data-key=\"' + %r + '\"]').focus()" % fk)
            pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
            check(f'{name}: keyboard: arrows move across the grid, Enter opens the list, Escape closes it',
                  ok1 and pg.evaluate(LIST) is None, (fk, ls))

        # swiping on the heat map still turns the page; the last page swipes back
        if touch:
            pg.evaluate("ZADRecordCharts.setStep(%d, {settled: true})" % LAST)
            pg.click('#gp-tab-province'); pg.wait_for_timeout(600)
            box = pg.locator('#gp-page-province .gp-chart__title').bounding_box()
            swipe(pg, box['x'] + box['width'] * 0.8, box['x'] + 10, box['y'] + 10)
            sel = pg.evaluate("document.querySelector('#gp-tab-town').getAttribute('aria-selected')")
            pg.locator('#gp-page-town .gp-heat tbody tr').nth(2).scroll_into_view_if_needed(); pg.wait_for_timeout(200)
            r = pg.locator('#gp-page-town .gp-heat tbody tr').nth(2).bounding_box()
            swipe(pg, r['x'] + r['width'] * 0.15, r['x'] + r['width'] * 0.85, r['y'] + r['height'] / 2)
            back = pg.evaluate("document.querySelector('#gp-tab-province').getAttribute('aria-selected')")
            check(f'{name}: swiping turns to the heat map, and swiping on its cells turns back',
                  sel == 'true' and back == 'true' and pg.evaluate(LIST) is None, (sel, back))

        check(f'{name}: no browser errors', not errors, errors[:3])
        ctx.close()

    # no-script fallback: the table with its numbers, no buttons
    ctx = b.new_context(java_script_enabled=False, **p.devices['iPhone 13'])
    pg = ctx.new_page()
    pg.goto(BASE + 'index.html', wait_until='load')
    ns = pg.evaluate("""() => ({rows: document.querySelectorAll('#gp-page-town .gp-heat tbody tr').length,
      btns: document.querySelectorAll('#gp-page-town .gp-heat button, #gp-page-town .gp-weeks').length,
      sum: [...document.querySelectorAll('#gp-page-town .gp-heat__n')].reduce((a, e) => a + (+e.textContent || 0), 0)})""")
    check('no script: the heat map is a plain table with every count and no buttons',
          ns['rows'] == len(TOWNS) and ns['btns'] == 0 and ns['sum'] == len(records), ns)
    ctx.close()
    b.close()

print(f'{sum(results)}/{len(results)} passed')
sys.exit(0 if all(results) else 1)
