# Manual phone/desktop check of interactive charts Batches 6 and 7 in a built
# dist/index.html (not run by pytest): the map follows the slider, and the
# week buttons on the incident line. Serve dist/
# (python3 -m http.server 8952 -d dist), then:
#   python3 tests/browser/map_follow_check.py http://127.0.0.1:8952/
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
COL = {c: i for i, c in enumerate(table['columns'])}
STREAM_OF = {r[COL['id']]: r[COL['stream']] for r in table['rows']}
LABEL = {s['key']: s['label'] for s in table['streams']}
STEP_OF = {r[COL['id']]: r[COL['step']] for r in table['rows']}

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else f'  -> {detail}'))


def plural(n, w):
    return f'{n} {w}' + ('' if n == 1 else 's')


def swipe(pg, x0, x1, y, dy=0):
    cdp = pg.context.new_cdp_session(pg)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y}]})
    for i in range(1, 11):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                 'touchPoints': [{'x': x0 + (x1 - x0) * i / 10, 'y': y + dy * i / 10}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    pg.wait_for_timeout(900)


def note(pg):
    return pg.evaluate("""() => { const n = document.getElementById('gp-mapnote');
      const r = n.getBoundingClientRect(), b = document.getElementById('gp-mapnote-now').getBoundingClientRect();
      return {hidden: n.hidden, text: document.getElementById('gp-mapnote-time').textContent,
              now: !document.getElementById('gp-mapnote-now').hidden,
              box: [r.left, r.right, r.top, r.bottom], btn: [b.width, b.height], vw: innerWidth}; }""")


def shown(pg):
    return sorted(pg.evaluate('ZADMapTime.shown()'))


LINE = """() => {
  const pts = [...document.querySelectorAll('#gp-page-incident .gp-line__pt')];
  const vis = pts.filter(c => c.style.display !== 'none');
  return {vis: vis.map(c => +c.querySelector('title').textContent.split(': ')[1].split(' ')[0]),
          days: [...document.querySelectorAll('#gp-page-incident .gp-line__hit')].filter(r => r.style.display !== 'none').map(r => r.getAttribute('data-day')),
          xs: pts.map(c => +c.getAttribute('cx')),
          labs: [...document.querySelectorAll('#gp-page-incident .gp-line__xlab')].filter(t => t.style.display !== 'none').map(t => t.textContent),
          note: (n => n && !n.hidden ? n.textContent : '')(document.querySelector('#gp-page-incident .gp-weeks__note')),
          btns: [...document.querySelectorAll('#gp-page-incident .gp-weeks__btn')].map(b => [b.textContent, b.getAttribute('aria-pressed')]),
          addedBtns: document.querySelectorAll('#gp-page-added .gp-weeks__btn').length,
          overflow: document.documentElement.scrollWidth - window.innerWidth};
}"""

LIST = """(pid) => {
  const box = document.querySelector('#gp-page-' + pid + ' [data-gp-list]');
  if (!box || box.hidden) return null;
  const bs = box.querySelectorAll('.gp-list__head .gp-list__btn');
  return {title: (box.querySelector('.gp-list__title') || {}).textContent,
          ids: [...box.querySelectorAll('.gp-list__item')].map(li => li.getAttribute('data-id')),
          prevOff: bs[0] ? bs[0].disabled : null, nextOff: bs[1] ? bs[1].disabled : null};
}"""


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
        pg.wait_for_timeout(900)

        def tap(sel):
            loc = pg.locator(sel).first
            loc.scroll_into_view_if_needed()
            (loc.tap if touch else loc.click)()
            pg.wait_for_timeout(150)

        # ── Batch 6: at "now" the map is exactly as without the charts
        n0 = note(pg)
        check(f'{name}: at now all {len(records)} pins show, no note', len(shown(pg)) == len(records) and n0['hidden'],
              (len(shown(pg)), n0))

        # every publish step: the pins are the ones added by then
        bad = []
        for s in range(LAST, -1, -1):
            c = pg.evaluate(f'ZADRecordCharts.setStep({s}, {{settled: true}}).total')
            pg.wait_for_timeout(30)
            want = sorted(rc.ids_on_map(table, s))
            got = shown(pg)
            n = note(pg)
            st = table['steps'][s]
            text = f"Map as of {st['label']} (pins added by then) \u00b7 {st['total']} of {plural(len(records), 'record')}"
            if got != want or c != len(want) or (s < LAST and (n['hidden'] or n['text'] != text or not n['now'])) \
                    or (s == LAST and not n['hidden']):
                bad.append((s, len(got), len(want), n['text']))
        check(f'{name}: at all {LAST + 1} steps the map shows exactly the pins added by then, same total as the charts, note reads right',
              not bad, bad[:3])

        pg.evaluate('ZADRecordCharts.setStep(0, {settled: true})')
        pg.wait_for_timeout(150)
        got = shown(pg)
        check(f'{name}: acceptance: back to {table["steps"][0]["label"]} shows only the first 5 pins',
              table['steps'][0]['label'] == '18 Oct, 7:28 PM' and len(got) == 5 and got == sorted(rc.ids_on_map(table, 0)), got)
        n = note(pg)
        check(f'{name}: note fits the screen, Back to now at least 44px',
              n['box'][0] >= 0 and n['box'][1] <= n['vw'] and n['btn'][0] >= 44 and n['btn'][1] >= 44, n)
        pg.screenshot(path=f'{OUT}b6-{name}-step0.png')

        # once per publish step, never per animation frame: count map redraws
        pg.evaluate("""() => { window.__redraws = 0; const f = ZADMapTime.setTime;
          ZADMapTime.setTime = function (o) { if (!o || o.ids) window.__redraws++; return f.apply(this, arguments); };
          window.__steps = new Set(); document.addEventListener('gp-time', e => window.__steps.add(e.detail.step)); }""")
        pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()")
        pg.wait_for_timeout(700)
        pg.evaluate("document.getElementById('gp-tab-incident').click()")
        pg.wait_for_timeout(700)
        pg.locator('#gp-time-incident').focus()
        for _ in range(5):
            pg.keyboard.press('ArrowRight')
            pg.wait_for_timeout(40)     # mid-animation
        pg.wait_for_timeout(500)
        r = pg.evaluate('[window.__redraws, window.__steps.size, ZADRecordCharts.step()]')
        check(f'{name}: five slider moves mid-animation redraw the map five times, not per frame', r[0] == 5 and r[1] == 5 and r[2] == 5, r)
        check(f'{name}: after the moves the map shows the step-5 pins', shown(pg) == sorted(rc.ids_on_map(table, 5)))

        # streams on top: Crime & Security off one step before now
        s2 = LAST - 1
        pg.evaluate(f'ZADRecordCharts.setStep({s2}, {{settled: true}})')
        cs = next(k for k in ALL if LABEL[k].startswith('Crime'))
        pg.evaluate(f'ZADRecordCharts.setStream({json.dumps(cs)}, false)')
        pg.wait_for_timeout(200)
        want = sorted(i for i in rc.ids_on_map(table, s2) if STREAM_OF[i] != LABEL[cs])
        tot = pg.evaluate(f'ZADRecordCharts.counts({s2}).total')
        n = note(pg)
        check(f'{name}: stream switch applies on top of the slider ({len(want)} pins, note says 3 of 4 streams)',
              want and shown(pg) == want and tot == len(want) and '(3 of 4 streams)' in n['text'] and f'{len(want)} of' in n['text'],
              (len(shown(pg)), len(want), n['text']))
        pg.evaluate(f'ZADRecordCharts.setStream({json.dumps(cs)}, true)')
        pg.wait_for_timeout(200)

        # the open drawer's pin stays visible off "now"
        pg.evaluate('ZADRecordCharts.setStep(0, {settled: true})')
        late = next(r[COL['id']] for r in table['rows'] if r[COL['step']] == LAST)
        pg.evaluate(f'window.forgeOpenSignal({json.dumps(late)})')
        pg.wait_for_timeout(500)
        got = shown(pg)
        check(f'{name}: the open drawer\'s pin stays on the map at step 0 (5 + 1)', late in got and len(got) == 6, got)
        pg.keyboard.press('Escape')
        pg.evaluate("document.querySelector('.drawer-close, #drawer-close') && document.querySelector('.drawer-close, #drawer-close').click()")
        pg.wait_for_timeout(400)
        got = shown(pg)
        check(f'{name}: closing the drawer takes it off again (5 pins)', len(got) == 5, got)

        # Back to now (map note's button)
        if pg.evaluate("document.body.classList.contains('panel-open') || !!document.querySelector('.md-shell.panel-open')") and touch:
            pg.evaluate('window.closePanel && window.closePanel()')
            pg.wait_for_timeout(500)
        tap('#gp-mapnote-now')
        pg.wait_for_timeout(300)
        check(f'{name}: Back to now returns the slider to now and every pin', pg.evaluate('ZADRecordCharts.step()') == LAST
              and len(shown(pg)) == len(records) and note(pg)['hidden'])

        # ── Batch 7: week buttons
        pg.evaluate("window.openPanel && window.openPanel('graph')")
        pg.wait_for_timeout(600)
        pg.evaluate("document.getElementById('gp-tab-incident').click()")
        pg.wait_for_timeout(600)
        L = pg.evaluate(LINE)
        weeks = AX['incident_weeks']
        check(f'{name}: line has All + {len(weeks)} week buttons, All pressed; Added has none (its axis is {len(AX["added_days"])} days)',
              [x[0] for x in L['btns']] == ['All'] + [w['label'] for w in weeks] and L['btns'][0][1] == 'true'
              and L['addedBtns'] == (len(AX['added_weeks']) + 1 if AX['added_weeks'] else 0), (L['btns'], L['addedBtns']))
        xs_all = L['xs']
        labs_all = L['labs']
        for step in (LAST, 3):
            pg.evaluate(f'ZADRecordCharts.setStep({step}, {{settled: true}})')
            for k, w in enumerate(weeks):
                tap(f'#gp-page-incident .gp-weeks__btn[data-week="{k}"]')
                pg.wait_for_timeout(450)
                L = pg.evaluate(LINE)
                c = rc.counts_from_table(table, step)
                ws = rc.week_split(table, step, 'incident', w)
                dated = c['incident']['occurred'] + c['incident']['found']
                ok = (L['vis'] == c['incident']['days'][w['from']:w['to'] + 1] and L['days'] == ws['days']
                      and L['note'] == f"{w['label']}: {plural(ws['in_week'], 'record')} on the line \u00b7 {ws['other_days']} on other days"
                      and ws['in_week'] + ws['other_days'] == dated
                      and ws['in_week'] + ws['other_days'] + c['incident']['undated'] + c['incident']['later_step'] == c['total']
                      and len(L['labs']) == len(ws['days']) and L['overflow'] <= 0)
                check(f'{name}: step {step}, {w["label"]}: 7 days drawn, counts match Python, totals add up to {c["total"]}',
                      ok, (L['vis'], L['note'], L['labs'], ws))
        pg.evaluate(f'ZADRecordCharts.setStep({LAST}, {{settled: true}})')
        tap('#gp-page-incident .gp-weeks__btn[data-week="2"]')
        pg.wait_for_timeout(450)
        pg.screenshot(path=f'{OUT}b7-{name}-week3.png')
        # tap a day inside the week, with a real tap
        box = pg.locator('#gp-page-incident .gp-line__hit[data-day="2026-10-19"]').bounding_box()
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] * 0.4
        (pg.touchscreen.tap if touch else pg.mouse.click)(x, y)
        pg.wait_for_timeout(250)
        lst = pg.evaluate(LIST, 'incident')
        want = rc.ids_from_table(table, LAST, 'incident', '2026-10-19')
        check(f'{name}: inside the week, a tap on 19 Oct lists its {len(want)} records', lst and sorted(lst['ids']) == sorted(want), lst)
        if touch:
            check(f'{name}: inside a week each day column is at least 44px wide ({box["width"]:.0f}px)', box['width'] >= 44, box)
        # previous / next stay inside the week
        pg.evaluate("ZADRecordCharts.openList('incident', '2026-10-21')")
        pg.wait_for_timeout(150)
        lst = pg.evaluate(LIST, 'incident')
        check(f'{name}: on the week\'s last day, Next is off; on its first, Previous is off', lst['nextOff'] is True)
        pg.evaluate("ZADRecordCharts.openList('incident', '2026-10-15')")
        pg.wait_for_timeout(150)
        check(f'{name}: ... first day', pg.evaluate(LIST, 'incident')['prevOff'] is True)
        # arrow keys stay inside the week
        pg.locator('#gp-page-incident .gp-line').focus()
        pg.keyboard.press('End')
        pg.wait_for_timeout(100)
        e = pg.evaluate(LIST, 'incident')['title']
        pg.keyboard.press('Home')
        pg.wait_for_timeout(100)
        h = pg.evaluate(LIST, 'incident')['title']
        check(f'{name}: Home and End go to the week\'s first and last day', e.startswith('21 Oct') and h.startswith('15 Oct'), (h, e))
        # a list on a day outside the new week closes
        tap('#gp-page-incident .gp-weeks__btn[data-week="0"]')
        pg.wait_for_timeout(300)
        check(f'{name}: picking another week closes a list on a day outside it', pg.evaluate(LIST, 'incident') is None)
        # the slider still works inside a week (real drag on touch, keys on desktop)
        pg.locator('#gp-time-incident').focus()
        pg.keyboard.press('Home')
        pg.wait_for_timeout(450)
        L = pg.evaluate(LINE)
        c = rc.counts_from_table(table, 0)
        check(f'{name}: slider to the first publish inside 1 to 7 Oct matches Python', L['vis'] == c['incident']['days'][0:7], L['vis'])
        pg.keyboard.press('End')
        pg.wait_for_timeout(450)
        # swiping on the week buttons still turns the page
        if touch:
            pos = "Math.round(document.getElementById('gp-track').scrollLeft / document.getElementById('gp-track').clientWidth)"
            pg.locator('#gp-page-incident .gp-weeks__btns').scroll_into_view_if_needed()
            before = pg.evaluate(pos)
            bb = pg.locator('#gp-page-incident .gp-weeks__btns').bounding_box()
            swipe(pg, bb['x'] + bb['width'] * 0.85, bb['x'] + bb['width'] * 0.1, bb['y'] + bb['height'] / 2)
            after = pg.evaluate(pos)
            check(f'{name}: a swipe across the week buttons still turns the page', before != after, (before, after))
            pg.evaluate("document.getElementById('gp-tab-incident').click()")
            pg.wait_for_timeout(600)
        # back to All: the line is as before
        tap('#gp-page-incident .gp-weeks__btn[data-week="-1"]')
        pg.wait_for_timeout(450)
        L = pg.evaluate(LINE)
        check(f'{name}: All puts every day and the original labels back', L['xs'] == xs_all and L['labs'] == labs_all
              and len(L['vis']) == len(AX['incident_days']) and L['note'] == '', (L['labs'], labs_all))
        check(f'{name}: no browser errors', not errors, errors[:3])
        ctx.close()

        # Area check still applies on top: a saved 5 km check around a Cape Town pin
        rec = next(r for r in records if r['short_id'] == '1a328ebb')
        saved = json.dumps({'v': 1, 'lat': round(float(rec['latitude']), 4), 'lng': round(float(rec['longitude']), 4),
                            'label': 'Check point', 'radius': 5000, 'dates': 'all', 'on': True})
        ctx = b.new_context(**ctx_args)
        ctx.add_init_script(f"try {{ localStorage.setItem('zad.areaCheck.v1', {json.dumps(saved)}); }} catch (e) {{}}")
        pg = ctx.new_page()
        pg.goto(BASE + 'index.html', wait_until='load')
        pg.wait_for_timeout(1200)
        area = set(shown(pg))
        bad = []
        for st in (0, 4, 8, LAST - 1, LAST):
            pg.evaluate(f'ZADRecordCharts.setStep({st}, {{settled: true}})')
            pg.wait_for_timeout(60)
            want = sorted(area & set(rc.ids_on_map(table, st)))
            if shown(pg) != want:
                bad.append((st, len(shown(pg)), len(want)))
        check(f'{name}: with Area check on ({len(area)} of {len(records)} pins in it), each step shows only the pins in both',
              0 < len(area) < len(records) and not bad, (len(area), bad))
        ctx.close()
    # with scripts off: no week buttons, no map note, the charts as before
    ctx = b.new_context(**p.devices['iPhone 13'], java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(BASE + 'index.html', wait_until='load')
    html = pg.content()
    check('no-script: no week buttons, the map note stays hidden',
          pg.locator('.gp-weeks__btn').count() == 0 and pg.locator('#gp-mapnote[hidden]').count() == 1
          and not pg.locator('#gp-mapnote').is_visible())
    ctx.close()
    b.close()

print(f'{sum(results)} of {len(results)} checks passed')
sys.exit(0 if all(results) else 1)
