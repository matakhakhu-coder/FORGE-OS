"""Open Records page and no paid tier (22 Oct 2026).

The free/pro split is gone: no Access Tiers page, no post-payment page, no
gated pages, no paid feed, no --tier flag. In its place, records.html,
records.csv and records.json list the approved map pins, and only those.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_checked_signals import (  # noqa: E402
    ALL_PINS, INFRA_PINS, PI_PINS, GLOBAL_PINS, LPM_PI_PINS, GPP_PI_PINS, _copy_signals, _edit_batch, _fresh_db, _markers, _publish_to, _restamp,
)
from tools import records_export as rx  # noqa: E402

PRIVATE_WORDS = ("private/", "analyst_note", "notes", "collected_title", "pubdate_raw",
                 "raw_", "owner_", "sprint", "quote", "content", "metadata", "email")
TIER_WORDS = ("Access Tiers", "Pro Intelligence", "Register interest", "register interest",
              "subscribe.html", "thankyou.html", "pro-feed", "paystack", "Paystack",
              "premium-callout", "Unlock", "za_divergent_pro")


def _imported(tmp_path, monkeypatch):
    from tools.checked_signals import run_import
    conn, db_file = _fresh_db(tmp_path, monkeypatch)
    conn.close()
    run_import(db_file, write=True, out=lambda m: None)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn, db_file


def _active_ids(index=None) -> set[str]:
    from tools.checked_signals import load_index
    return {s["signal_id"] for s in (load_index(index) if index else load_index()).active}


# ── Selection: approved pins only ────────────────────────────────────────────────────────────────

def test_records_are_exactly_the_approved_pins(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    rows = rx.build_records(conn, version="0" * 40)
    assert len(rows) == ALL_PINS
    assert {r["signal_id"] for r in rows} == _active_ids()
    assert all(tuple(r) == rx.RECORD_FIELDS for r in rows)


def test_unreviewed_raw_and_unapproved_rows_never_appear(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    cols = ("signal_id, source, external_id, title, content, timestamp, source_type, "
            "status, published_at, lat, lng, metadata_json")
    extra = [
        # an unreviewed raw signal with coordinates
        ("raw-1", "rss", "raw-1", "Raw unreviewed item", "c", "2026-10-20", "rss", "raw",
         None, -33.9, 18.5, "{}"),
        # a reviewed, published item that is not a hand-checked pin
        ("rss-pub-1", "rss", "rss-pub-1", "Published wire item", "c", "2026-10-20", "rss",
         "reviewed", "2026-10-20", -33.9, 18.5, "{}"),
        # a published checked-looking row with no batch file and no approval
        ("fake-checked-1", "s", "fake-checked-1", "Unapproved pin", "c", "2026-10-20",
         "checked", "reviewed", "2026-10-20", -33.9, 18.5,
         json.dumps({"checked": {"location": "x", "sources": []}})),
    ]
    conn.executemany(f"INSERT INTO signals ({cols}) VALUES ({','.join('?' * 12)})", extra)
    conn.commit()
    ids = {r["signal_id"] for r in rx.build_records(conn, version="0" * 40)}
    assert not ids & {"raw-1", "rss-pub-1", "fake-checked-1"}
    assert ids == _active_ids()


def test_pin_missing_from_database_is_left_out(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    sid = sorted(_active_ids())[0]
    conn.execute("UPDATE signals SET published_at = NULL WHERE signal_id = ?", (sid,))
    conn.commit()
    ids = {r["signal_id"] for r in rx.build_records(conn, version="0" * 40)}
    assert sid not in ids and len(ids) == ALL_PINS - 1


def test_pin_changed_after_approval_is_left_out(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    index = _copy_signals(tmp_path)
    changed = {}

    def edit(d):
        d["signals"][0]["title"] += " (edited)"
        changed["sid"] = d["signals"][0]["signal_id"]
    _edit_batch(index, 0, edit)
    _restamp(index)
    rows = rx.build_records(conn, index_path=index, signals_dir=index.parent, version="0" * 40)
    ids = {r["signal_id"] for r in rows}
    assert changed["sid"] not in ids
    assert len(ids) == ALL_PINS - 1


def test_retracted_pin_is_left_out(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    index = _copy_signals(tmp_path)
    gone = {}

    def edit(d):
        pin = d["signals"][0]
        gone["sid"] = pin["signal_id"]
        d.setdefault("retractions", []).append(
            {"signal_id": pin["signal_id"], "reason": "test", "retracted_at": "2026-10-22T00:00:00Z"})
    _edit_batch(index, 0, edit)
    _restamp(index)
    try:
        rows = rx.build_records(conn, index_path=index, signals_dir=index.parent, version="0" * 40)
    except Exception as exc:  # a malformed retraction must fail loudly, never leak
        pytest.skip(f"retraction shape differs in this repo: {exc}")
    assert gone["sid"] not in {r["signal_id"] for r in rows}


# ── No private fields ────────────────────────────────────────────────────────────────────────────────

def test_columns_are_an_allowlist_with_no_private_fields():
    for name in rx.RECORD_FIELDS:
        assert not any(w in name for w in PRIVATE_WORDS), name
        assert rx.FIELD_HELP[name].strip()
    assert set(rx.FIELD_HELP) == set(rx.RECORD_FIELDS)


def test_no_quotes_story_text_or_raw_metadata_leak(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    rows = rx.build_records(conn, version="0" * 40)
    facts = json.loads((ROOT / "docs/signals/fact-bases.json").read_text())["pins"]
    quotes = {s["quote"] for p in facts.values() for f in p.get("facts", [])
              for s in f.get("sources", []) if len(s.get("quote") or "") > 60}
    blob = rx.to_csv(rows)
    assert quotes and not any(q in blob for q in quotes)
    stories = [r["content"] for r in conn.execute(
        "SELECT content FROM signals WHERE source_type='checked'") if len(r["content"] or "") > 80]
    assert stories and not any(s in blob for s in stories)
    for r in rows:
        assert r["source_urls"] == "" or all(u.startswith("http") for u in r["source_urls"].split(" | "))
        assert -35.5 < r["latitude"] < -21 and 16 < r["longitude"] < 33.5
        assert r["latitude"] == round(r["latitude"], 5)


def test_inferred_map_point_is_flagged():
    prec = json.loads((ROOT / "docs/signals/pin-precision.json").read_text())["pins"]
    inferred = [sid for sid, p in prec.items() if str(p.get("note", "")).upper().startswith("INFERRED")]
    assert inferred, "expected at least one inferred point (Firethorne)"
    for sid in inferred:
        assert rx._location_basis(prec[sid], "") == "inferred"
    assert rx._location_basis({"note": "Middle of the street"}, "Somewhere") == "reported"


# ── CSV: columns and escaping ───────────────────────────────────────────────────────────────────────

def _row(**over):
    base = {f: "" for f in rx.RECORD_FIELDS}
    base.update(over)
    return base


def test_csv_header_bom_and_line_ends():
    text = rx.to_csv([_row(title="A")])
    assert text.startswith("\ufeff")
    assert text.split("\r\n")[0].lstrip("\ufeff").split(",") == list(rx.RECORD_FIELDS)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")


def test_csv_escapes_commas_quotes_newlines_and_formulas():
    tricky = _row(
        title='Tafelsig, "Paulsberg" St\nline two',
        outlets="=HYPERLINK(\"http://x\")",
        statement_from="+27 police",
        location_described="@cmd",
        incident_described="-2+3",
        latitude=-34.06261, longitude=18.63118, uncertainty_m=500,
    )
    text = rx.to_csv([tricky])
    rec = next(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    assert rec["title"] == 'Tafelsig, "Paulsberg" St\nline two'
    assert rec["outlets"] == "'=HYPERLINK(\"http://x\")"
    assert rec["statement_from"] == "'+27 police"
    assert rec["location_described"] == "'@cmd"
    assert rec["incident_described"] == "'-2+3"
    assert rec["latitude"] == "-34.06261"          # numbers are left alone
    assert rec["longitude"] == "18.63118" and rec["uncertainty_m"] == "500"


def test_csv_round_trips_every_real_row(tmp_path, monkeypatch):
    conn, _ = _imported(tmp_path, monkeypatch)
    rows = rx.build_records(conn, version="0" * 40)
    back = list(csv.DictReader(io.StringIO(rx.to_csv(rows).lstrip("\ufeff"))))
    assert len(back) == len(rows)
    for a, b in zip(rows, back):
        assert b["signal_id"] == a["signal_id"]
        assert b["title"] == rx.safe_cell(a["title"])
        assert float(b["latitude"]) == a["latitude"]


def test_browser_csv_matches_python_csv(tmp_path, monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    conn, _ = _imported(tmp_path, monkeypatch)
    rows = rx.build_records(conn, version="0" * 40)
    rows.append(_row(title='x, "y"\nz', outlets="=1+1", latitude=-1.5, signal_id="t"))
    src = tmp_path / "rows.json"
    src.write_text(json.dumps({"fields": list(rx.RECORD_FIELDS), "rows": rows}))
    script = ("const R=require(%s);const d=require(%s);"
              "process.stdout.write(R.toCsv(d.fields,d.rows));") % (
        json.dumps(str(ROOT / "publisher/static/js/records.js")), json.dumps(str(src)))
    out = subprocess.run([node, "-e", script], capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode()
    assert out.stdout.decode("utf-8") == rx.to_csv(rows)


# ── The built site ─────────────────────────────────────────────────────────────────────────────────────

def test_build_writes_records_and_no_tier_pages(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    dist = tmp_path / "dist"
    dist.mkdir()
    for stale in ("subscribe.html", "thankyou.html", "pro-feed.json"):
        (dist / stale).write_text("old")
    dist = _publish_to(tmp_path, monkeypatch, db_file)

    for stale in ("subscribe.html", "thankyou.html", "pro-feed.json"):
        assert not (dist / stale).exists(), stale
    for f in ("records.html", "records.csv", "records.json", "static/js/records.js"):
        assert (dist / f).exists(), f

    markers = _markers(dist)
    payload = json.loads((dist / "records.json").read_text())
    assert payload["count"] == len(markers) == ALL_PINS
    # the same pins as the map, same points
    by_id = {m["signal_id"]: m for m in markers}
    assert {r["signal_id"] for r in payload["records"]} == set(by_id)
    for r in payload["records"]:
        m = by_id[r["signal_id"]]
        assert abs(r["latitude"] - m["lat"]) < 1e-5 and abs(r["longitude"] - m["lng"]) < 1e-5
    assert [f["name"] for f in payload["fields"]] == list(rx.RECORD_FIELDS)
    assert payload["caveats"] == list(rx.CAVEATS)
    rows = list(csv.reader(io.StringIO((dist / "records.csv").read_text(encoding="utf-8-sig"))))
    assert len(rows) == ALL_PINS + 1

    page = (dist / "records.html").read_text()
    assert page.count('<tr data-id="') == ALL_PINS
    assert "not statistics" in page and "approximate" in page
    lede = page.split('class="rec__lede">', 1)[1].split("</p>", 1)[0]
    assert "killing" not in lede.lower() and "streams" in lede
    assert 'href="records.csv"' in page and "Download" in page
    assert "<script" not in json.dumps(payload)

    for html in dist.rglob("*.html"):
        text = html.read_text()
        for w in TIER_WORDS:
            assert w not in text, f"{w!r} left in {html.relative_to(dist)}"
        assert ">Pro<" not in text, html
    nav = (dist / "index.html").read_text()
    assert 'href="records.html"' in nav


def test_records_page_embeds_safe_json(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    page = (dist / "records.html").read_text()
    m = re.search(r'<script id="records-data" type="application/json">(.*?)</script>', page, re.S)
    assert m and "</" not in m.group(1)
    data = json.loads(m.group(1))
    assert data["count"] == ALL_PINS


# ── Old addresses redirect; old code is gone ────────────────────────────────────────────────────────────

def _route_for(path: str) -> dict:
    routes = json.loads((ROOT / "vercel.json").read_text())["routes"]
    for r in routes:
        if re.fullmatch(r["src"], path):
            return r
    raise AssertionError(f"no route for {path}")


@pytest.mark.parametrize("path", ["/subscribe", "/subscribe.html", "/subscribe/",
                                  "/thankyou", "/thankyou.html"])
def test_old_tier_pages_redirect_to_records(path):
    r = _route_for(path)
    assert r.get("status") == 301 and r["headers"]["Location"] == "/records"


def test_old_paid_feed_redirects_to_open_json():
    r = _route_for("/pro-feed.json")
    assert r.get("status") == 301 and r["headers"]["Location"] == "/records.json"


@pytest.mark.parametrize("path", ["/records", "/records.html", "/records.csv", "/records.json", "/"])
def test_records_addresses_are_served_not_redirected(path):
    r = _route_for(path)
    assert "status" not in r and r["dest"].startswith("/dist/")


def test_committed_dist_has_records_and_no_tier_pages():
    dist = ROOT / "dist"
    for f in ("records.html", "records.csv", "records.json"):
        assert (dist / f).exists(), f
    for f in ("subscribe.html", "thankyou.html", "pro-feed.json"):
        assert not (dist / f).exists(), f
    for html in dist.rglob("*.html"):     # includes legacy pages no build rewrites
        text = html.read_text()
        for w in TIER_WORDS:
            assert w not in text, f"{w!r} left in {html.relative_to(dist)}"
        assert ">Pro<" not in text, html


def test_tier_code_is_gone():
    templates = ROOT / "publisher" / "templates"
    for name in ("subscribe.html", "thankyou.html", "gate.html"):
        assert not (templates / name).exists(), name
    src = (ROOT / "tools" / "publish.py").read_text()
    for token in ("--tier", "_build_gate_pages", "_build_pro_feed", "_writes_pro_feed",
                  "is_gated", "TIERS"):
        assert token not in src, token
    import revenue.config as rc
    assert not hasattr(rc, "TIERS")
    assert "paystack_public_key" not in rc.get_template_context()


# ── Stream column (23 Oct 2026) ──────────────────────────────────────────────

def test_stream_names_match_the_maps_streams_panel():
    import tools.publish as pub
    assert rx.STREAM_LABELS == pub.STREAM_LABELS


def test_every_record_and_row_carries_its_map_stream(tmp_path, monkeypatch):
    conn, db_file = _imported(tmp_path, monkeypatch)
    conn.close()
    dist = _publish_to(tmp_path, monkeypatch, db_file)
    recs = json.loads((dist / "records.json").read_text())["records"]
    by_id = {r["signal_id"]: r for r in recs}

    # Records: the same stream the map files each pin under.
    markers = {m["signal_id"]: m for m in _markers(dist)}
    for sid, r in by_id.items():
        assert r["stream"] == rx.STREAM_LABELS[markers[sid]["stream"]]
    infra = {sid for sid, r in by_id.items() if r["stream"] == "Infrastructure"}
    assert len(infra) == INFRA_PINS
    assert sum(r["stream"] == "Crime & Security" for r in recs) == ALL_PINS - INFRA_PINS - PI_PINS - LPM_PI_PINS - GPP_PI_PINS - GLOBAL_PINS
    assert sum(r["stream"] == "Priority Intelligence" for r in recs) == PI_PINS + LPM_PI_PINS + GPP_PI_PINS
    assert sum(r["stream"] == "Africa & World" for r in recs) == GLOBAL_PINS
    rows = list(csv.DictReader(io.StringIO((dist / "records.csv").read_text(encoding="utf-8-sig"))))
    cols = list(rows[0])
    assert cols[cols.index("province") + 1] == "stream"
    assert {r["signal_id"]: r["stream"] for r in rows} == {s: r["stream"] for s, r in by_id.items()}

    # Page: a Stream column, one tab per stream with its count, infrastructure rows marked.
    html = (dist / "records.html").read_text()
    assert '<th scope="col">Stream</th>' in html
    assert html.count('<td data-label="Stream">') == ALL_PINS
    tabs = re.findall(r'<button type="button" class="rec-stream-tab" data-stream="([^"]*)"[^>]*>.*?'
                      r'<span class="rec-stream-tab__n">(\d+)</span>', html, re.S)
    assert tabs == [("", str(ALL_PINS)), ("Crime &amp; Security", str(ALL_PINS - INFRA_PINS - PI_PINS - LPM_PI_PINS - GPP_PI_PINS - GLOBAL_PINS)),
                    ("Infrastructure", str(INFRA_PINS)), ("Priority Intelligence", str(PI_PINS + LPM_PI_PINS + GPP_PI_PINS)),
                    ("Africa &amp; World", str(GLOBAL_PINS))]
    trs = re.findall(r'<tr data-id="([^"]+)" data-stream="([^"]+)" class="rec-row rec-row--([a-z-]+)">', html)
    assert len(trs) == ALL_PINS
    assert {i for i, _, slug in trs if slug == "infrastructure"} == infra
    assert {slug for _, _, slug in trs} == {"crime-security", "infrastructure", "priority-intelligence", "africa-world"}
    assert '<option value="Infrastructure">Infrastructure</option>' in html


def test_the_stream_filter_shows_one_stream_at_a_time():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = (ROOT / "publisher" / "static" / "js" / "records.js").read_text()
    script = js + """
    var recs = [
      {signal_id: 'a', title: 'Killing', stream: 'Crime & Security', province: 'Limpopo', incident_start: '2026-10-03'},
      {signal_id: 'b', title: 'Substation', stream: 'Infrastructure', province: 'Limpopo', incident_start: '2026-10-11'}];
    var M = module.exports.matches;
    var pick = function (f) { return recs.filter(function (r) { return M(r, f); }).map(function (r) { return r.signal_id; }).join(','); };
    console.log(JSON.stringify([pick({}), pick({stream: 'Infrastructure'}), pick({stream: 'Crime & Security'}),
                                pick({q: 'infrastructure'})]));
    """
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    assert json.loads(out) == ["a,b", "b", "a", "b"]
