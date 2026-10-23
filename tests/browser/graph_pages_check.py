# Manual phone/desktop check of the Graph panel pages in dist/index.html
# (not run by pytest). Serve a built dist/ (python3 -m http.server 8952 -d dist), then:
#   python3 tests/browser/graph_pages_check.py http://127.0.0.1:8952/
# Needs the Python playwright package and its Chromium. Screenshots go to /tmp/zyd/shots/.
# The timeline slider checks (interactive charts Batches 2 and 3) print a
# PASS/FAIL line each and the script exits 1 if any fails.
import json, os, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import record_charts as rc  # noqa: E402

BASE = sys.argv[1].rstrip('/') + '/'
OUT = '/tmp/zyd/shots/'
os.makedirs(OUT, exist_ok=True)

STATE = """() => {
  const t = document.getElementById('gp-track');
  const sel = [...document.querySelectorAll('.gp-tab')].findIndex(b => b.getAttribute('aria-selected') === 'true');
  const tools = [...document.querySelectorAll('#panel-graph .gp-tool')].filter(e => !e.hidden).map(e => e.getAttribute('data-gp-tool'));
  const inert = [...document.querySelectorAll('.gp-page')].map(p => p.hasAttribute('inert'));
  return {page: Math.round(t.scrollLeft / t.clientWidth), selected: sel, pos: document.getElementById('gp-pos').textContent,
          tools: [...new Set(tools)], inert, focused: document.activeElement && document.activeElement.id,
          overflow_px: document.documentElement.scrollWidth - window.innerWidth};
}"""


def swipe(pg, x0, x1, y, dy=0):
    """A finger drag as raw touch events through CDP, so scroll-snap does
    what it does for a real finger."""
    cdp = pg.context.new_cdp_session(pg)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y}]})
    for i in range(1, 11):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                 'touchPoints': [{'x': x0 + (x1 - x0) * i / 10, 'y': y + dy * i / 10}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    pg.wait_for_timeout(900)


res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, ctx_args in [
        ('phone', dict(**p.devices['iPhone 13'])),
        ('android', dict(**p.devices['Pixel 5'])),
        ('desktop', dict(viewport={'width': 1366, 'height': 900})),
    ]:
        ctx = b.new_context(**ctx_args)
        pg = ctx.new_page()
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        pg.goto(BASE + 'index.html', wait_until='load')
        pg.wait_for_timeout(800)
        pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()")
        pg.wait_for_timeout(700)
        r = {'open': pg.evaluate(STATE)}
        pg.screenshot(path=OUT + f'{name}-graph-1-network.png')
        box = pg.locator('#gp-track').bounding_box()
        y = box['y'] + box['height'] * 0.5
        if name != 'desktop':
            swipe(pg, box['x'] + box['width'] * 0.85, box['x'] + box['width'] * 0.15, y)
            r['after_swipe_left'] = pg.evaluate(STATE)
            pg.screenshot(path=OUT + f'{name}-graph-2-incident.png')
            swipe(pg, box['x'] + box['width'] * 0.85, box['x'] + box['width'] * 0.15, y)
            r['after_second_swipe'] = pg.evaluate(STATE)
            pg.screenshot(path=OUT + f'{name}-graph-3-added.png')
            swipe(pg, box['x'] + box['width'] * 0.15, box['x'] + box['width'] * 0.85, y)
            r['after_swipe_back'] = pg.evaluate(STATE)
            # vertical scroll inside a chart page does not change page
            pg.click('#gp-tab-province')
            pg.wait_for_timeout(700)
            swipe(pg, box['x'] + box['width'] / 2, box['x'] + box['width'] / 2 + 15, y + 120, dy=-220)
            r['after_vertical_scroll'] = pg.evaluate(STATE)
            pg.screenshot(path=OUT + f'{name}-graph-5-province.png')
            pg.click('#gp-tab-network')
            pg.wait_for_timeout(700)
        # buttons and keys
        pg.click('.gp-empty__next')
        pg.wait_for_timeout(700)
        r['after_see_charts'] = pg.evaluate(STATE)
        pg.keyboard.press('ArrowRight')
        pg.wait_for_timeout(700)
        r['after_arrow_right_key'] = pg.evaluate(STATE)
        pg.keyboard.press('End')
        pg.wait_for_timeout(700)
        r['after_end_key'] = pg.evaluate(STATE)
        pg.screenshot(path=OUT + f'{name}-graph-province-key.png')
        pg.click('#gp-prev')
        pg.wait_for_timeout(700)
        r['after_prev_button'] = pg.evaluate(STATE)
        pg.screenshot(path=OUT + f'{name}-graph-4-stream.png')
        pg.focus('#gp-tab-stream')
        pg.keyboard.press('Home')
        pg.wait_for_timeout(700)
        r['after_home_key'] = pg.evaluate(STATE)
        r['chart_numbers'] = pg.evaluate("""() => ({
          stream: [...document.querySelectorAll('#gp-page-stream .gp-bar__n')].reduce((a, e) => a + +e.textContent, 0),
          province: [...document.querySelectorAll('#gp-page-province .gp-bar__n')].reduce((a, e) => a + +e.textContent, 0),
          added: [...document.querySelectorAll('#gp-page-added .gp-col__n')].reduce((a, e) => a + +e.textContent, 0),
          points: document.querySelectorAll('#gp-page-incident .gp-line__pt').length,
          bar_heights_px: [...document.querySelectorAll('#gp-page-added .gp-col__bar')].map(e => Math.round(e.getBoundingClientRect().height)),
          line_svg_px: Math.round(document.querySelector('#gp-page-incident svg').getBoundingClientRect().width)})""")
        r['errors'] = errors
        res[name] = r
        ctx.close()
    b.close()
print(json.dumps(res, indent=1))

# ── Timeline slider (interactive charts Batches 2 and 3) ─────────────────────
RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))


def touch_drag(pg, x0, x1, y, steps=12, watch=None):
    cdp = pg.context.new_cdp_session(pg)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y}]})
    seen = []
    for i in range(1, steps + 1):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x0 + (x1 - x0) * i / steps, 'y': y}]})
        pg.wait_for_timeout(16)
        if watch:
            seen.append(pg.evaluate(watch))
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    pg.wait_for_timeout(900)
    return seen


# What Python says each step should show: chart_counts over the records on
# the map by then, laid on today's fixed axes.
records = json.loads(urllib.request.urlopen(BASE + 'records.json').read())['records']
now_charts = rc.chart_counts(records)
table = rc.timeline_table(records, now_charts)
AX = table['axes']


def expected(step):
    at = table['steps'][step]['at']
    cut = [r for r in records if (lambda i: i is None and step == len(table['steps']) - 1 or
                                  i is not None and i.isoformat(timespec='minutes') <= at)(rc._published_instant(r['published_at']))]
    ch = {q['id']: q['data'] for q in rc.chart_counts(cut)['pages']}
    inc = {d['date']: d['count'] for d in ch['incident']['days']}
    add = {d['date']: d['count'] for d in ch['added']['days']}
    return {'total': len(cut),
            'incident': [inc.get(d, 0) for d in AX['incident_days']],
            'occurred': ch['incident']['occurred'], 'found': ch['incident']['found'],
            'undated': ch['incident']['undated'], 'later_step': ch['incident']['later_step'],
            'added': [add.get(d, 0) for d in AX['added_days']],
            'stream': [{x['label']: x['count'] for x in ch['stream']['rows']}.get(l, 0) for l in AX['stream']],
            'province': [{x['label']: x['count'] for x in ch['province']['rows']}.get(l, 0) for l in AX['province']]}


LAST = len(table['steps']) - 1
SHOWN = """() => {
  const nums = sel => [...document.querySelectorAll(sel)].map(e => +e.textContent);
  const pct = (sel, prop) => [...document.querySelectorAll(sel)].map(e => parseFloat(e.style[prop]));
  return {
    step: ZADRecordCharts.step(), values: [...document.querySelectorAll('.gp-time__range')].map(e => +e.value),
    labels: [...document.querySelectorAll('.gp-time__label')].map(e => e.textContent),
    added: nums('#gp-page-added .gp-col__n'), stream: nums('#gp-page-stream .gp-bar__n'),
    province: nums('#gp-page-province .gp-bar__n'),
    added_h: pct('#gp-page-added .gp-col__bar', 'height'), stream_w: pct('#gp-page-stream .gp-bar__fill', 'width'),
    province_w: pct('#gp-page-province .gp-bar__fill', 'width'),
    summary: document.querySelector('#gp-page-incident .gp-chart__summary').textContent,
    animating: ZADRecordCharts.animating(), has_line: !!ZADRecordCharts.line,
    page: Math.round(document.getElementById('gp-track').scrollLeft / document.getElementById('gp-track').clientWidth),
    overflow_px: document.documentElement.scrollWidth - innerWidth };
}"""


def close(a, b, tol=0.06):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def pcts(vals, top):
    return [v / top * 100 if top else 0 for v in vals]


def open_graph(pg, tab='stream'):
    pg.goto(BASE + 'index.html', wait_until='load')
    pg.wait_for_timeout(800)
    pg.evaluate("document.querySelector('[data-panel=\"graph\"]').click()")
    pg.wait_for_timeout(600)
    pg.click('#gp-tab-' + tab)
    pg.wait_for_timeout(700)


with sync_playwright() as p:
    b = p.chromium.launch()
    for name, ctx_args in [('phone', dict(**p.devices['iPhone 13'])), ('android', dict(**p.devices['Pixel 5'])),
                           ('desktop', dict(viewport={'width': 1366, 'height': 900}))]:
        ctx = b.new_context(**ctx_args)
        pg = ctx.new_page()
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        open_graph(pg)
        s0 = pg.evaluate(SHOWN)
        start_w = s0['stream_w']
        check(f'{name}: opens at now', s0['step'] == LAST and set(s0['values']) == {LAST}
              and all(l.endswith(f'(now) \u00b7 {len(records)} of {len(records)} records') for l in s0['labels']), s0['labels'][0])
        check(f'{name}: one visible slider per chart page', pg.evaluate(
            "[...document.querySelectorAll('[data-gp-time]')].filter(e => !e.hidden).length") == 4)
        check(f'{name}: slider control is at least 44px tall', pg.evaluate(
            "Math.min(...[...document.querySelectorAll('.gp-time__range')].map(e => e.offsetHeight))") >= 44)

        # every slider position: whole numbers equal Python's step counts, and
        # once the motion settles the bars sit at those counts on fixed axes
        bad = []
        for st in range(LAST, -1, -1):
            pg.evaluate(f'ZADRecordCharts.setStep({st})')
            sh = pg.evaluate(SHOWN)
            ex = expected(st)
            if not (sh['added'] == ex['added'] and sh['stream'] == ex['stream'] and sh['province'] == ex['province']
                    and set(sh['values']) == {st} and f"{ex['total']} of {len(records)} records" in sh['labels'][0]
                    and sum(ex['stream']) == sum(ex['province']) == sum(ex['added']) == ex['total'] == table['steps'][st]['total']
                    and (not sh['has_line'] or f"{ex['occurred']} occurred" in sh['summary'])):
                bad.append(('numbers', st))
            pg.wait_for_timeout(450)
            sh = pg.evaluate(SHOWN)
            if not (close(sh['added_h'], pcts(ex['added'], AX['added_max'])) and close(sh['stream_w'], pcts(ex['stream'], AX['stream_max']))
                    and close(sh['province_w'], pcts(ex['province'], AX['province_max'])) and not sh['animating']):
                bad.append(('bars', st))
        check(f'{name}: every step matches Python (numbers, bars, shared position, label)', not bad, bad)
        if name == 'phone':
            pg.screenshot(path=OUT + f'{name}-slider-stream-first.png')

        # motion: in between part way, settles by ~0.4 s, picks up mid-motion
        pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(500)
        pg.evaluate('ZADRecordCharts.setStep(0)')
        pg.wait_for_timeout(90)
        mid = pg.evaluate(SHOWN)['stream_w'][0]
        a, z = pcts(expected(LAST)['stream'], AX['stream_max'])[0], pcts(expected(0)['stream'], AX['stream_max'])[0]
        check(f'{name}: bars move (in between part way)', min(a, z) + 1 < mid < max(a, z) - 1, (a, mid, z))
        traj = pg.evaluate(f"""() => new Promise(res => {{
          const f = () => parseFloat(document.querySelector('#gp-page-stream .gp-bar__fill').style.width);
          const before = f(); ZADRecordCharts.setStep({LAST}); const out = [before];
          const t0 = performance.now();
          (function tick() {{ out.push(f()); if (performance.now() - t0 < 600) requestAnimationFrame(tick); else res(out); }})();
        }})""")
        jumps = [abs(traj[i + 1] - traj[i]) for i in range(len(traj) - 1)]
        check(f'{name}: retarget picks up mid-motion (no jump), no overshoot',
              jumps[0] < 0.35 * abs(a - z) and max(traj) <= a + 0.01 and abs(traj[-1] - a) < 0.06,
              (traj[:4], max(traj), traj[-1]))

        # dragging the slider by finger never flips the page
        if name != 'desktop':
            pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(500)
            box = pg.locator('#gp-time-stream').bounding_box()
            y = box['y'] + box['height'] / 2
            seen = touch_drag(pg, box['x'] + box['width'] - 11, box['x'] + 11, y,
                              watch="Math.round(document.getElementById('gp-track').scrollLeft)")
            sh = pg.evaluate(SHOWN)
            check(f'{name}: finger drag on the slider moves it, never the page',
                  sh['step'] == 0 and sh['page'] == 3 and len(set(seen)) == 1, (sh['step'], sh['page'], set(seen)))
            seen = touch_drag(pg, box['x'] + 11, box['x'] + box['width'] - 11, y)
            sh = pg.evaluate(SHOWN)
            pg.wait_for_timeout(400)
            sh2 = pg.evaluate(SHOWN)
            check(f'{name}: dragging from the first publish to now grows the bars to exactly today',
                  sh['step'] == LAST and sh['page'] == 3 and close(sh2['stream_w'], start_w, 0.06)
                  and sh2['stream'] == [x['count'] for x in now_charts['pages'][2]['data']['rows']], (sh['step'], sh2['stream_w'], start_w))
            # swiping elsewhere on the chart page (title, the slider's label) still flips it
            t = pg.locator('#gp-page-stream .gp-chart__title').bounding_box()
            touch_drag(pg, box['x'] + box['width'] * 0.85, box['x'] + box['width'] * 0.1, t['y'] + t['height'] / 2)
            check(f'{name}: swipe on the chart title still flips the page', pg.evaluate(SHOWN)['page'] == 4)
            lb = pg.locator('#gp-time-province').bounding_box()
            lab = pg.locator('#gp-page-province .gp-time__label').bounding_box()
            touch_drag(pg, lb['x'] + lb['width'] * 0.1, lb['x'] + lb['width'] * 0.85, lab['y'] + lab['height'] / 2)
            check(f'{name}: swipe on the slider label still flips the page', pg.evaluate(SHOWN)['page'] == 3)
        else:
            pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(500)
            box = pg.locator('#gp-time-stream').bounding_box()
            y = box['y'] + box['height'] / 2
            pg.mouse.move(box['x'] + box['width'] - 11, y); pg.mouse.down()
            pg.mouse.move(box['x'] + 11, y, steps=12); pg.mouse.up(); pg.wait_for_timeout(600)
            sh = pg.evaluate(SHOWN)
            check(f'{name}: mouse drag moves the slider, not the page', sh['step'] == 0 and sh['page'] == 3, (sh['step'], sh['page']))
        # keyboard: arrows move one publish
        pg.click('#gp-tab-stream'); pg.wait_for_timeout(700)
        pg.evaluate(f'ZADRecordCharts.setStep({LAST})'); pg.wait_for_timeout(450)
        pg.focus('#gp-time-stream'); pg.keyboard.press('ArrowLeft'); pg.wait_for_timeout(450)
        sh = pg.evaluate(SHOWN)
        check(f'{name}: arrow key moves one publish, page stays', sh['step'] == LAST - 1 and sh['page'] == 3, (sh['step'], sh['page']))
        # the same position shows on the other pages after a swipe or tab
        pg.click('#gp-tab-province'); pg.wait_for_timeout(700)
        check(f'{name}: position is shared across pages', pg.evaluate("+document.getElementById('gp-time-province').value") == LAST - 1)
        if name == 'phone':
            pg.screenshot(path=OUT + f'{name}-slider-province.png')
        sh = pg.evaluate(SHOWN)
        check(f'{name}: nothing runs off the side', sh['overflow_px'] <= 0, sh['overflow_px'])
        check(f'{name}: no browser errors', not errors, errors)
        ctx.close()

    # reduced motion: values jump, nothing animates
    ctx = b.new_context(**p.devices['iPhone 13'], reduced_motion='reduce')
    pg = ctx.new_page()
    open_graph(pg)
    pg.evaluate('ZADRecordCharts.setStep(0)')
    sh = pg.evaluate(SHOWN)
    ex = expected(0)
    check('reduced motion: bars jump straight to the step, no animation',
          not sh['animating'] and close(sh['stream_w'], pcts(ex['stream'], AX['stream_max']))
          and close(sh['added_h'], pcts(ex['added'], AX['added_max'])), sh['stream_w'])
    ctx.close()

    # no script: today's charts, no slider
    ctx = b.new_context(**p.devices['iPhone 13'], java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(BASE + 'index.html', wait_until='load')
    pg.wait_for_timeout(500)
    shown_sliders = sum(pg.locator('[data-gp-time]').nth(i).is_visible() for i in range(pg.locator('[data-gp-time]').count()))
    printed = [int(pg.locator('#gp-page-stream .gp-bar__n').nth(i).text_content())
               for i in range(pg.locator('#gp-page-stream .gp-bar__n').count())]
    check('no script: no slider, today\'s charts printed', shown_sliders == 0
          and printed == [x['count'] for x in now_charts['pages'][2]['data']['rows']], (shown_sliders, printed))
    ctx.close()
    b.close()

failed = [n for n, ok in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} slider checks passed")
sys.exit(1 if failed else 0)
