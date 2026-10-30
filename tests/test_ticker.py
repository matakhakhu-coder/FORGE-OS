"""Bottom ticker: full headlines and a steady reading pace.

The scroll strip must be as wide as its content, or translateX(-50%) moves
only half a screen and the ticker crawls (2.7 px/s on a phone before this).
Headlines are shown whole; the old [:80] cut them mid-word and dropped the date.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "publisher" / "templates" / "map.html"
DIST = ROOT / "dist" / "index.html"


def test_scroll_strip_is_content_wide():
    t = TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.md-ticker__scroll \{(.*?)\}", t, re.S).group(1)
    assert "width: max-content" in block
    assert "translateX(-50%)" in t


def test_speed_is_a_steady_pace_not_capped():
    t = TEMPLATE.read_text(encoding="utf-8")
    assert "var TICKER_PX_PER_SEC = 60;" in t
    assert "Math.min(60, w / 120)" not in t
    assert "w / TICKER_PX_PER_SEC" in t


def test_ticker_headlines_are_not_cut_mid_word():
    t = TEMPLATE.read_text(encoding="utf-8")
    tick = t[t.index('id="ticker-scroll"'):]
    tick = tick[:tick.index("</div>")]
    assert "title[:80]" not in tick
    assert tick.count("s.title|truncate(140, False, '\u2026', 0)") == 4


def test_built_ticker_carries_every_checked_title_whole():
    html = DIST.read_text(encoding="utf-8")
    strip = html[html.index('id="ticker-scroll"'):]
    strip = strip[:strip.index("</div>")]
    import html as h
    shown = {h.unescape(x) for x in re.findall(r'class="md-ticker__item"[^>]*>(.*?)</a>', strip, re.S)}
    assert shown, "ticker is empty"
    for title in shown:
        assert not title.endswith("\u2026"), title  # all current titles fit under 140
    # The ticker carries the newest pins; since the Durban sweep (30 Oct) the 13 Oct pin has rolled off it.
    assert any("on his way to a tuck shop; security-company account, 28 Oct" in s for s in shown)
