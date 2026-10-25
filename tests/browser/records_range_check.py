"""Browser check for Records date ranges in the page address (time-aware pins, Batch 10).

records.html?from=YYYY-MM-DD&to=YYYY-MM-DD opens filtered to that range of
South African incident days. This drives the real Records page in Chromium on
a computer and a phone, with pretend dates (forge_clock), against the builds
from time_aware_dists.py (oct/ built 24 October, dec/ built 15 December, where
every pin is archived on the map but Records still lists all of them).

It checks: a link restores the range (inputs, rows, count words, open
filters); editing the dates writes the address with one Back step per edit,
including a date typed on the keyboard part by part; Back and Forward move
between ranges without reloading; Clear filters clears the address; bad,
swapped, partial and out-of-range dates get a plain note; the filtered CSV
holds exactly the range; the full CSV is untouched; archived months are in
range; screen-reader wiring, phone overflow and page errors.

  python tests/browser/records_range_check.py --base http://127.0.0.1:8765/ --dists /tmp/dists
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forge_clock  # noqa: E402

SA = timezone(timedelta(hours=2))
SIZES = {"computer": {"viewport": {"width": 1366, "height": 900}},
         "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}}
# (build, pretend day, full set of checks?)
RUNS = [("oct", "2026-10-24", True), ("oct", "2026-10-31", False), ("oct", "2026-11-23", False),
        ("dec", "2026-12-15", True)]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

SHOWN_JS = "() => [...document.querySelectorAll('#rec-rows tr[data-id]:not([hidden])')].map(t => t.dataset.id)"
STATE_JS = """() => ({
  search: location.search, len: history.length,
  from: document.querySelector('input[name=from]').value,
  to: document.querySelector('input[name=to]').value,
  count: document.getElementById('rec-count').textContent,
  note: document.getElementById('rec-range-note').hidden ? '' : document.getElementById('rec-range-note').textContent,
  empty: !document.getElementById('rec-empty').hidden,
  dl: document.getElementById('rec-download-filtered').disabled,
  open: document.getElementById('rec-filter-box').open,
  summary: document.querySelector('#rec-filter-box > summary').textContent.trim(),
  marker: window.__noReload === 1,
})"""


def sa_day(v: str) -> str:
    v = v.strip()
    if len(v) == 10:
        return v
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=SA)).astimezone(SA).date().isoformat()


def label(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{int(dd)} {MONTHS[int(m) - 1]} {y}"


def run(base: str, dists: Path, sizes=("computer", "phone"), runs=RUNS) -> dict:
    from playwright.sync_api import sync_playwright
    problems: list[str] = []
    results: dict[str, dict] = {}
    n = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for build, day, full in runs:
            recs = json.loads((dists / build / "records.json").read_text())["records"]
            fields = [f["name"] for f in json.loads((dists / build / "records.json").read_text())["fields"]]
            days = {r["signal_id"]: sa_day(r["incident_start"]) for r in recs}
            first, last = min(days.values()), max(days.values())

            def want(frm="", to="", stream=""):
                return {r["signal_id"] for r in recs
                        if (not frm or days[r["signal_id"]] >= frm) and (not to or days[r["signal_id"]] <= to)
                        and (not stream or r["stream"] == stream)}

            for size in sizes:
                key = f"{build}@{day}/{size}"
                res: dict[str, bool] = {}
                results[key] = res

                def check(name, cond, detail="", res=res, key=key):
                    nonlocal n
                    n += 1
                    res[name] = bool(cond)
                    if not cond:
                        problems.append(f"{key}: {name} {detail}".strip())

                ctx = browser.new_context(accept_downloads=True, **SIZES[size])
                page = ctx.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                forge_clock.freeze_today(page, day)
                url = base + build + "/records.html"

                def go(q=""):
                    page.goto(url + q, wait_until="load")
                    page.wait_for_function("() => document.documentElement.classList.contains('rec-js')")
                    page.evaluate("() => { window.__noReload = 1; }")
                    return page.evaluate(STATE_JS)

                def shown():
                    return set(page.evaluate(SHOWN_JS))

                def ensure_open():
                    if not page.evaluate("() => document.getElementById('rec-filter-box').open"):
                        page.click("#rec-filter-box > summary")

                # A. a link restores the range
                frm, to = "2026-10-15", "2026-10-20"
                s = go(f"?from={frm}&to={to}")
                w = want(frm, to)
                check("link: inputs hold the range", (s["from"], s["to"]) == (frm, to), str(s))
                check("link: rows are exactly the range", shown() == w, f"{len(shown())} vs {len(w)}")
                check("link: count names the range",
                      s["count"] == f"Showing {len(w)} of {len(recs)} records, incidents from {label(frm)} to {label(to)}",
                      s["count"])
                check("link: filters open with two on", s["open"] and "(2 on)" in s["summary"], str(s))
                check("link: no note, address kept", s["note"] == "" and s["search"] == f"?from={frm}&to={to}", str(s))
                check("link: records page lists every record", page.locator("#rec-rows tr[data-id]").count() == len(recs) == 72)

                # J. archived months are in range (every pin is from October)
                s = go("?from=2026-10-01&to=2026-10-31")
                check("archive: all of October in range", shown() == want("2026-10-01", "2026-10-31") and len(shown()) == 72,
                      str(len(shown())))
                maplinks = page.evaluate("() => [...document.querySelectorAll('#rec-rows tr[data-id]:not([hidden]) a.rec-map')].filter(a => a.getAttribute('href').includes('signal=')).length")
                check("archive: every row keeps its map link", maplinks == 72, str(maplinks))

                # partial ranges
                s = go("?from=2026-10-19")
                check("partial: from only", shown() == want("2026-10-19") and s["note"] == ""
                      and s["count"].endswith(f"incidents from {label('2026-10-19')} on"), s["count"])
                s = go("?to=2026-10-10")
                check("partial: to only", shown() == want("", "2026-10-10")
                      and s["count"].endswith(f"incidents up to {label('2026-10-10')}"), s["count"])

                # E. a bad date is ignored, said and tidied out of the address
                s = go("?from=2026-02-30&to=2026-10-20")
                check("bad: from ignored, to kept", s["from"] == "" and s["to"] == "2026-10-20" and shown() == want("", "2026-10-20"), str(s))
                check("bad: note says it was ignored", "2026-02-30" in s["note"] and "ignored" in s["note"], s["note"])
                check("bad: address shows what is used", s["search"] == "?to=2026-10-20", s["search"])
                s = go("?from=%3Cb%3Ehi%3C%2Fb%3E")
                check("bad: markup in a link stays text", page.locator("#rec-range-note b").count() == 0
                      and "<b>hi</b>" in s["note"] and len(shown()) == len(recs), s["note"])

                # F. swapped
                s = go("?from=2026-10-20&to=2026-10-05")
                check("swap: dates swapped and said", (s["from"], s["to"]) == ("2026-10-05", "2026-10-20")
                      and "swapped" in s["note"] and s["search"] == "?from=2026-10-05&to=2026-10-20"
                      and shown() == want("2026-10-05", "2026-10-20"), str(s))

                # G. outside the records
                s = go("?from=2027-01-01")
                check("outside: nothing, said plainly", shown() == set() and s["empty"] and s["dl"]
                      and s["note"] == f"No records fall in this range. Records run from {label(first)} to {label(last)}.", str(s))

                # M. with the stream link
                s = go("?stream=Infrastructure&from=2026-10-01")
                check("stream: both apply", shown() == want("2026-10-01", "", "Infrastructure")
                      and page.get_attribute('#rec-streams [data-stream="Infrastructure"]', "aria-pressed") == "true", str(len(shown())))

                # screen-reader wiring and the phone layout with a note showing
                s = go("?from=2026-02-30")
                wiring = page.evaluate("""() => ({
                  role: document.getElementById('rec-range-note').getAttribute('role'),
                  live: document.getElementById('rec-count').getAttribute('aria-live'),
                  desc: [...document.querySelectorAll('input[type=date]')].map(i => i.getAttribute('aria-describedby')),
                  hint: (document.getElementById('rec-dates-hint') || {}).textContent || '',
                  over: document.documentElement.scrollWidth - innerWidth,
                  small: [...document.querySelectorAll('.rec-btn, .rec-filters input, .rec-filters select')].filter(e => e.offsetParent && e.getBoundingClientRect().height < 44).length,
                })""")
                check("a11y: note is a status, count is live, dates described",
                      wiring["role"] == "status" and wiring["live"] == "polite" and wiring["desc"] == ["rec-dates-hint"] * 2
                      and "page address" in wiring["hint"], str(wiring))
                check("layout: no sideways scroll, tap targets 44px", wiring["over"] <= 0 and wiring["small"] == 0, str(wiring))

                if full:
                    # B. edits write the address; Back and Forward move between ranges
                    s0 = go("")
                    ensure_open()
                    h0 = s0["len"]
                    page.fill("input[name=from]", "2026-10-10")
                    s1 = page.evaluate(STATE_JS)
                    page.wait_for_timeout(1500)
                    page.fill("input[name=to]", "2026-10-18")
                    s2 = page.evaluate(STATE_JS)
                    check("edit: from goes in the address", s1["search"] == "?from=2026-10-10" and s1["len"] == h0 + 1, str(s1))
                    check("edit: to joins it as a new Back step", s2["search"] == "?from=2026-10-10&to=2026-10-18"
                          and s2["len"] == h0 + 2 and shown() == want("2026-10-10", "2026-10-18"), str(s2))
                    page.go_back()
                    page.wait_for_function("() => location.search === '?from=2026-10-10'")
                    page.wait_for_timeout(100)
                    b1 = page.evaluate(STATE_JS)
                    check("back: one step drops to", b1["to"] == "" and b1["from"] == "2026-10-10"
                          and shown() == want("2026-10-10") and b1["marker"], str(b1))
                    page.go_back()
                    page.wait_for_function("() => location.search === ''")
                    page.wait_for_timeout(100)
                    b2 = page.evaluate(STATE_JS)
                    check("back: two steps clears it", b2["from"] == b2["to"] == "" and len(shown()) == len(recs) and b2["marker"], str(b2))
                    page.go_forward()
                    page.wait_for_function("() => location.search === '?from=2026-10-10'")
                    page.go_forward()
                    page.wait_for_function("() => location.search === '?from=2026-10-10&to=2026-10-18'")
                    page.wait_for_timeout(100)
                    f2 = page.evaluate(STATE_JS)
                    check("forward: range comes back, no reload", (f2["from"], f2["to"]) == ("2026-10-10", "2026-10-18")
                          and shown() == want("2026-10-10", "2026-10-18") and f2["marker"] and f2["len"] == h0 + 2, str(f2))

                    # D. Clear filters clears the address, and Back brings the range back
                    page.click("button[type=reset]")
                    page.wait_for_function("() => !location.search.includes('from=')")
                    c = page.evaluate(STATE_JS)
                    check("clear: address cleared as one Back step", c["search"] == "" and c["len"] == h0 + 3
                          and len(shown()) == len(recs), str(c))
                    page.go_back()
                    page.wait_for_function("() => location.search.includes('to=2026-10-18')")
                    page.wait_for_timeout(100)
                    check("clear: Back restores the range", shown() == want("2026-10-10", "2026-10-18"))

                    # C. a date typed on the keyboard, part by part, is one Back step
                    s0 = go("")
                    ensure_open()
                    h0 = s0["len"]
                    if size == "phone":
                        # A phone's date box opens the system picker, which hands
                        # the page a whole date at once; Playwright's fill does that.
                        page.fill("input[name=from]", "2026-10-15")
                    else:
                        page.focus("input[name=from]")
                        page.keyboard.type("10152026", delay=40)
                    page.wait_for_timeout(200)
                    k = page.evaluate(STATE_JS)
                    check("keyboard or picker: date lands in the address once", k["from"] == "2026-10-15"
                          and k["search"] == "?from=2026-10-15" and k["len"] == h0 + 1 and k["note"] == "", str(k))

                    # H. typed the wrong way round: said, nothing matches, address keeps what was typed
                    page.fill("input[name=to]", "2026-10-05")
                    page.wait_for_timeout(100)
                    r = page.evaluate(STATE_JS)
                    check("typed backwards: note and no rows", "after" in r["note"] and shown() == set() and r["empty"]
                          and r["search"] == "?from=2026-10-15&to=2026-10-05", str(r))

                    # an edit clears the note a link left
                    s = go("?from=2026-02-30")
                    ensure_open()
                    page.fill("input[name=to]", "2026-10-20")
                    page.wait_for_timeout(100)
                    r = page.evaluate(STATE_JS)
                    check("edit: link's note goes once you edit a date", r["note"] == "" and r["search"] == "?to=2026-10-20", str(r))

                    # I. the filtered CSV holds exactly the range; the full CSV is untouched
                    go(f"?from={frm}&to={to}")
                    with page.expect_download() as dl:
                        page.click("#rec-download-filtered")
                    text = Path(dl.value.path()).read_text(encoding="utf-8")
                    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
                    head = next(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
                    check("csv: filtered download is exactly the range", {x["signal_id"] for x in rows} == want(frm, to)
                          and len(rows) == len(want(frm, to)) and head == fields, f"{len(rows)} rows")
                    check("csv: file name unchanged", dl.value.suggested_filename.endswith("-filtered.csv"), dl.value.suggested_filename)
                    full_csv = urllib.request.urlopen(base + build + "/records.csv").read().decode("utf-8-sig")
                    full_json = json.loads(urllib.request.urlopen(base + build + "/records.json").read())
                    check("csv/json: full downloads keep every record",
                          len(list(csv.DictReader(io.StringIO(full_csv)))) == len(recs) == full_json["count"] == 72)
                    canon = page.get_attribute('link[rel="canonical"]', "href")
                    check("seo: a range link's canonical is plain Records", canon == "https://forgemap.co.za/records", str(canon))

                check("no page errors", not errors, str(errors[:3]))
                ctx.close()
        browser.close()
    return {"results": results, "problems": problems, "checks": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--dists", required=True, type=Path)
    a = ap.parse_args()
    rep = run(a.base.rstrip("/") + "/", a.dists)
    for k, r in rep["results"].items():
        print(f"{k}: {sum(r.values())}/{len(r)}")
    for p in rep["problems"]:
        print("  FAIL", p)
    print(f"[records range] {rep['checks']} checks " + ("PASS" if not rep["problems"] else "FAIL"))
    return 0 if not rep["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
