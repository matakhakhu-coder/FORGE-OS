# Manual phone/desktop check of dist/records.html (not run by pytest).
# Serve dist/ (python3 -m http.server 8941 -d dist) then:
#   python3 tests/browser/records_phone_check.py http://127.0.0.1:8941/
# Needs the Python playwright package and its Chromium. Screenshots go to /tmp/zyd/shots/.
import json, sys, csv, io
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip('/') + '/'
OUT = '/tmp/zyd/shots/'
import os; os.makedirs(OUT, exist_ok=True)
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, ctx_args in [
        ('phone', dict(**p.devices['iPhone 13'])),
        ('android', dict(**p.devices['Pixel 5'])),
        ('desktop', dict(viewport={'width': 1366, 'height': 900})),
    ]:
        ctx = b.new_context(accept_downloads=True, **ctx_args)
        pg = ctx.new_page()
        errors = []
        pg.on('pageerror', lambda e: errors.append(str(e)))
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        pg.goto(BASE + 'records.html', wait_until='networkidle')
        r = {}
        r['overflow_px'] = pg.evaluate('document.documentElement.scrollWidth - window.innerWidth')
        r['count_text'] = pg.inner_text('#rec-count')
        r['visible_rows'] = pg.evaluate("document.querySelectorAll('#rec-rows tr:not([hidden])').length")
        r['caveat_visible'] = pg.is_visible('.rec-caveats')
        r['filters_open_initially'] = pg.evaluate("document.getElementById('rec-filter-box').open")
        r['first_row_y'] = pg.evaluate("document.querySelector('#rec-rows tr').getBoundingClientRect().top + scrollY")
        pg.screenshot(path=OUT + f'{name}-records-top.png')
        # filters
        if not pg.evaluate("document.getElementById('rec-filter-box').open"):
            pg.click('#rec-filter-box > summary')
        r['filters_open_after_tap'] = pg.evaluate("document.getElementById('rec-filter-box').open")
        pg.fill('input[name=q]', 'Athlone')
        r['athlone'] = pg.inner_text('#rec-count')
        r['summary_when_filtered'] = pg.inner_text('#rec-filter-box > summary')
        pg.screenshot(path=OUT + f'{name}-records-filtered.png')
        pg.fill('input[name=q]', '')
        pg.select_option('select[name=basis]', 'inferred')
        r['inferred'] = pg.inner_text('#rec-count')
        r['inferred_titles'] = pg.eval_on_selector_all('#rec-rows tr:not([hidden]) .rec-title', 'els => els.map(e => e.textContent)')
        with pg.expect_download() as dl:
            pg.click('#rec-download-filtered')
        d = dl.value
        text = open(d.path(), encoding='utf-8').read()
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
        r['filtered_csv'] = {'name': d.suggested_filename, 'rows': len(rows) - 1, 'header_cols': len(rows[0])}
        pg.click('button[type=reset]')
        pg.wait_for_timeout(100)
        r['after_reset'] = pg.inner_text('#rec-count')
        pg.fill('input[name=from]', '2026-10-19')
        pg.dispatch_event('input[name=from]', 'change')
        r['from_19_oct'] = pg.inner_text('#rec-count')
        pg.click('button[type=reset]')
        pg.wait_for_timeout(100)
        pg.select_option('select[name=province]', 'Eastern Cape')
        r['eastern_cape'] = pg.inner_text('#rec-count')
        pg.click('button[type=reset]')
        pg.wait_for_timeout(100)
        # full CSV link
        with pg.expect_download() as dl2:
            pg.click('a[href="records.csv"]')
        t2 = open(dl2.value.path(), encoding='utf-8').read()
        r['full_csv_rows'] = len(list(csv.reader(io.StringIO(t2.lstrip('\ufeff'))))) - 1
        # scroll to a card
        pg.evaluate("document.querySelector('#rec-rows tr').scrollIntoView()")
        pg.screenshot(path=OUT + f'{name}-records-cards.png')
        # tap targets
        r['small_tap_targets'] = pg.evaluate("""[...document.querySelectorAll('.rec-btn, .rec-filters input, .rec-filters select')].filter(e => e.offsetParent && e.getBoundingClientRect().height < 44).length""")
        # nav on the map page
        pg.goto(BASE + 'index.html', wait_until='domcontentloaded')
        pg.wait_for_timeout(1500)
        r['map_has_records_link'] = pg.locator('a[href="records.html"]').count()
        r['map_has_pro_link'] = pg.locator('a[href="subscribe.html"]').count()
        pg.screenshot(path=OUT + f'{name}-map-top.png')
        r['errors'] = errors
        res[name] = r
        ctx.close()
    b.close()
print(json.dumps(res, indent=1))
