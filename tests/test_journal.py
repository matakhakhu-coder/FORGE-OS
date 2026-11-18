"""Journal page (18 Nov 2026): field notes tied to a place on the map.

Frontend foundation only. docs/journal/entries.json holds published entries
(none yet); tools/journal.py refuses bad, sample or draft-looking entries; the
page shows an honest empty state with noindex until the first entry; every
page links to it. There is no upload, sign-in or publish control on the site."""
import datetime as dt
import json
import pathlib
import re
import sys

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import journal, publish  # noqa: E402

DIST = REPO / "dist"
TODAY = dt.date(2026, 11, 18)
NOINDEX_RE = re.compile(r'<meta\s+name="robots"\s+content="[^"]*noindex', re.I)


def _entry(**kw):
    e = {
        "id": "field-note-one", "title": "A field note", "published": "2026-11-18",
        "author": "A Writer", "place": "Main Road, Town", "summary": "Short summary.",
        "body": ["First paragraph.", "Second paragraph."],
    }
    e.update(kw)
    return e


def _render(entries):
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    return env.get_template("journal.html").render(
        generated_at="now", entries=entries,
        canonical_url=publish.canonical_url("journal.html") if entries else None,
        **publish._get_revenue_context())


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ── Registry ──

def test_committed_registry_is_empty_and_has_no_samples():
    data = json.loads(journal.ENTRIES_FILE.read_text(encoding="utf-8"))
    assert data["entries"] == []
    assert journal.load_entries() == []


def test_missing_file_means_no_entries(tmp_path):
    assert journal.load_entries(tmp_path / "nope.json") == []


def test_bad_file_is_refused(tmp_path):
    p = tmp_path / "entries.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(journal.JournalError):
        journal.load_entries(p)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(journal.JournalError):
        journal.load_entries(p)


def test_valid_entry_passes():
    assert journal.validate([_entry(pin="0c5e795f")], {"0c5e795f"}, TODAY) == []


@pytest.mark.parametrize("bad, needle", [
    (dict(example=True), "never publish"),
    (dict(sample=True), "never publish"),
    (dict(title=""), "'title' is required"),
    (dict(author="  "), "'author' is required"),
    (dict(id="Bad ID"), "id must be"),
    (dict(published="18 Nov 2026"), "must be a date"),
    (dict(published="2026-11-19"), "in the future"),
    (dict(body=[]), "'body' must be"),
    (dict(body="one string"), "'body' must be"),
    (dict(summary="x" * 281), "over 280"),
    (dict(pin="ZZ"), "8-character"),
    (dict(pin="deadbeef"), "not on the live map"),
    (dict(sources=[{"label": "S", "url": "http://x"}]), "https://"),
    (dict(status="draft"), "unknown field"),
])
def test_rules_refuse(bad, needle):
    problems = journal.validate([_entry(**bad)], {"0c5e795f"}, TODAY)
    assert any(needle in p for p in problems), problems


def test_duplicate_ids_refused():
    problems = journal.validate([_entry(), _entry()], None, TODAY)
    assert any("used twice" in p for p in problems)


def test_published_entries_raise_instead_of_dropping():
    with pytest.raises(journal.JournalError):
        journal.published_entries([_entry(), _entry(example=True, id="x")], None, TODAY)


def test_published_entries_newest_first_with_map_link():
    out = journal.published_entries([
        _entry(id="older", published="2026-11-01"),
        _entry(id="newer", pin="0c5e795f"),
    ], {"0c5e795f"}, TODAY)
    assert [e["id"] for e in out] == ["newer", "older"]
    assert out[0]["pin_url"] == "index.html?signal=0c5e795f"
    assert out[1]["pin_url"] is None
    assert out[0]["published_fmt"] == "18 Nov 2026"


# ── Template ──

def test_empty_state_is_honest_and_noindex():
    html = _render([])
    head = html.split("</head>")[0]
    assert NOINDEX_RE.search(head)
    assert 'rel="canonical"' not in head
    text = _text(html)
    assert "No journal entries yet" in text
    assert "Nothing is posted automatically" in text
    assert 'href="index.html"' in html and "Back to the map" in text


def test_page_has_no_publish_controls():
    html = _render([])
    content = html.split('<main class="site-main">')[1].split("</main>")[0]
    for tag in ("<form", "<input", "<textarea", "<button", 'type="file"'):
        assert tag not in content, tag
    assert "geolocation" not in html


def test_entries_render_escaped_with_canonical_and_pin_link():
    entries = journal.published_entries(
        [_entry(title="Fish & <chips>", pin="0c5e795f",
                sources=[{"label": "A source", "url": "https://example.org/a"}])],
        {"0c5e795f"}, TODAY)
    html = _render(entries)
    head = html.split("</head>")[0]
    assert not NOINDEX_RE.search(head)
    assert '<link rel="canonical" href="https://forgemap.co.za/journal">' in head
    assert "Fish &amp; &lt;chips&gt;" in html and "<chips>" not in html
    assert 'href="index.html?signal=0c5e795f"' in html
    assert 'id="field-note-one"' in html
    assert "Written by A Writer" in _text(html)
    assert "No journal entries yet" not in _text(html)


def test_journal_is_active_in_nav():
    html = _render([])
    nav = html.split('<nav class="site-nav">')[1].split("</nav>")[0]
    assert 'href="journal.html" class="site-nav__link site-nav__link--active"' in nav


# ── Publisher and built site ──

def test_live_pin_ids_match_the_map_links():
    sigs = [{"signal_id": "0c5e795fabcdef", "lat": -26.1, "lng": 28.0},
            {"signal_id": "11112222ffff", "lat": None, "lng": None}]
    assert publish.live_pin_ids(sigs) == {"0c5e795f"}


def test_dist_journal_is_built_empty_and_noindex():
    page = DIST / "journal.html"
    assert page.exists()
    html = page.read_text(encoding="utf-8")
    assert NOINDEX_RE.search(html.split("</head>")[0])
    assert "No journal entries yet" in _text(html)
    assert "https://forgemap.co.za/journal" not in (DIST / "sitemap.xml").read_text(encoding="utf-8")


def test_every_top_level_page_links_journal_in_nav_and_footer():
    pages = sorted(DIST.glob("*.html"))
    assert {p.name for p in pages} >= {"index.html", "about.html", "records.html",
                                       "journal.html", "contact.html", "privacy.html"}
    for p in pages:
        html = p.read_text(encoding="utf-8")
        nav = html.split('<nav class="site-nav">')[1].split("</nav>")[0]
        footer = html.split('<footer class="site-footer">')[1]
        assert 'href="journal.html"' in nav, p
        assert 'href="journal.html"' in footer, p


@pytest.mark.parametrize("name", ["article.html", "case_detail.html", "actor_profile.html"])
def test_subfolder_templates_link_journal_one_level_up(name):
    t = (publish.TMPL_DIR / name).read_text(encoding="utf-8")
    assert "{% block nav_journal %}../journal.html{% endblock %}" in t
    assert "{% block footer_journal %}../journal.html{% endblock %}" in t
