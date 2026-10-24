"""Test builds for the time-aware browser checks (Batch 5).

Builds into OUT, from docs/signals through an empty database and
tools/publish.py main(), the same route as tests/test_checked_signals.py:

  oct/    built on 24 October 2026: all 72 pins on the page's live list
  dec/    built on 15 December 2026: the page carries November and December
          (empty), and every pin sits only in the October month file
  synth/  the December build plus made-up pins from 1 November to 15
          December, two a day, and four pins on the edges of the live window
          (South African midnight against UTC). The page carries November
          and December; October stays in its month file

Nothing here touches dist/ or the database.

    python tests/browser/time_aware_dists.py OUT
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import pytest  # noqa: E402  (pytest.MonkeyPatch outside a test)

from tools import map_archive as ma  # noqa: E402
from tools import map_data  # noqa: E402

SYNTH_TODAY = date(2026, 12, 15)
SYNTH_PREFIX = "5a"          # made-up ids start with this, so they are easy to tell apart
# The edges: on 15 December the live window is 16 November to 15 December.
EDGE_PINS = [
    ("2026-11-15T21:59:00Z", False),   # 23:59 on 15 November in South Africa: archived
    ("2026-11-15T22:00:00Z", True),    # 00:00 on 16 November: live
    ("2026-11-16T00:30:00+02:00", True),
    ("2026-12-15T21:30:00Z", True),    # 23:30 on 15 December: today
]


def build(tmp: Path, today: date) -> Path:
    from tests.test_checked_signals import _fresh_db, _publish_to
    from tools.checked_signals import run_import
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ma, "build_today", lambda: today)
        conn, db_file = _fresh_db(tmp, mp)
        conn.close()
        run_import(db_file, write=True, out=lambda m: None)
        return _publish_to(tmp, mp, db_file)


def _made_up_id(n: int) -> str:
    u = uuid.UUID(int=uuid.uuid5(uuid.NAMESPACE_URL, f"forge-ta5-{n}").int)
    return SYNTH_PREFIX + str(u)[2:]


def synth_pins(template: list[dict]) -> list[dict]:
    """Made-up pins: two a day from 1 November to 15 December 2026 at noon
    South African time, then the edge pins. Positions and details are
    copied from real pins."""
    out, n = [], 0
    stamps = []
    d = date(2026, 11, 1)
    while d <= SYNTH_TODAY:
        stamps += [f"{d.isoformat()}T12:00:00+02:00", f"{d.isoformat()}T08:15:00+02:00"]
        d += timedelta(days=1)
    stamps += [s for s, _ in EDGE_PINS]
    for stamp in stamps:
        src = template[n % len(template)]
        p = json.loads(json.dumps(src))
        p["signal_id"] = _made_up_id(n)
        p["title"] = f"Made-up pin {n} ({stamp})"
        p["incident_start"] = stamp
        p["incident_at"] = stamp
        p["published_at"] = stamp
        out.append(p)
        n += 1
    return out


def make_synth(dec: Path, out: Path) -> dict:
    """Copy the December build and add made-up pins, rebuilding the page's
    live list, the month files and the details folder as publish would."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(dec, out)
    real = [{**map_data.read_detail(dec, e["signal_id"]), **e} for e in ma.all_entries(dec)]
    made = synth_pins(real)
    every = real + made
    live, index = ma.write_archive(out, every, today=SYNTH_TODAY)
    map_data.write_pin_details(out, every)
    html_path = out / "index.html"
    html = html_path.read_text(encoding="utf-8")
    body = json.dumps(map_data.map_list(live), ensure_ascii=False)
    html, k = map_data.RAW_RE.subn(lambda m: "var RAW = " + body + ";\n", html, count=1)
    assert k == 1, "could not find var RAW in the December build"
    html_path.write_text(html, encoding="utf-8")
    return {"real": len(real), "made_up": len(made), "page": len(live),
            "months": [(r["month"], r["count"]) for r in index["months"]]}


def build_all(out: Path) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_work"
    shutil.rmtree(work, ignore_errors=True)
    res = {}
    for name, day in (("oct", date(2026, 10, 24)), ("dec", SYNTH_TODAY)):
        tmp = work / name
        tmp.mkdir(parents=True)
        dist = build(tmp, day)
        shutil.rmtree(out / name, ignore_errors=True)
        shutil.copytree(dist, out / name)
        res[name] = len(map_data.read_map_list(out / name / "index.html"))
    res["synth"] = make_synth(out / "dec", out / "synth")
    shutil.rmtree(work, ignore_errors=True)
    return res


if __name__ == "__main__":
    print(json.dumps(build_all(Path(sys.argv[1])), indent=1))
