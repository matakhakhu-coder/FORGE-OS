# Manual phone/desktop check of the Graph panel pages in dist/index.html
# (not run by pytest). Serve a built dist/ (python3 -m http.server 8952 -d dist), then:
#   python3 tests/browser/graph_pages_check.py http://127.0.0.1:8952/
# Needs the Python playwright package and its Chromium. Screenshots go to /tmp/zyd/shots/.
import json, os, sys
from playwright.sync_api import sync_playwright

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
