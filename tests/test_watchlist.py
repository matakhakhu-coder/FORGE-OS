"""Watchlist: bookmarked names and links render as plain text, never as markup.

Runs the Node tests in tests/js/watchlist.test.js (a fake DOM that fails on
any innerHTML use) and checks the built copy of watchlist.js matches the source.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "publisher" / "static" / "js" / "watchlist.js"


def test_watchlist_js_renders_names_as_text():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "watchlist.test.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_watchlist_js_has_no_markup_sinks():
    src = SRC.read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in src, sink


def test_star_buttons_escape_names_in_templates():
    # The names reach the star buttons through Jinja with autoescape on.
    publish = (ROOT / "tools" / "publish.py").read_text(encoding="utf-8")
    assert "autoescape=True" in publish
    for t in ("map.html", "actor_profile.html"):
        html = (ROOT / "publisher" / "templates" / t).read_text(encoding="utf-8")
        assert 'data-wl-title="{{ ' in html and "|safe" not in html.split("data-wl-title")[1][:60]


def test_built_watchlist_js_matches_source():
    built = ROOT / "dist" / "static" / "js" / "watchlist.js"
    if not built.exists():
        pytest.skip("dist/ not built")
    assert built.read_bytes() == SRC.read_bytes(), "rebuild dist/ after changing watchlist.js"
