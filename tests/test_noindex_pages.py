"""Noindex pages (24 Oct 2026): the Madlanga Pattern case study stays open by
link but asks search engines to leave it out of results. Every other page
stays indexable, and robots.txt (if any) must not block the noindex page,
or crawlers would never see the tag."""
import pathlib
import re
import sys

from jinja2 import Environment, FileSystemLoader

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import publish  # noqa: E402

DIST = REPO / "dist"
FOLLOW_UP = "limpopo-procurement-arrests-v2"
CASE17 = "limpopo-municipal-procurement-arrests-april-2026-rerun-v2"
NOINDEX_PAGES = {
    "articles/madlanga-hawks-limpopo-municipal-fraud.html",
    f"articles/{FOLLOW_UP}.html",
    f"cases/{CASE17}.html",
}
NOINDEX_RE = re.compile(r'<meta\s+name="robots"\s+content="[^"]*noindex', re.I)


def _render(slug: str) -> str:
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    art = {
        "title": "T", "slug": slug, "summary": "", "stream": "GLOBAL",
        "stream_label": "Global", "author": "FORGE Map Staff",
        "published_at": "2026-04-01", "published_fmt": "1 Apr 2026", "tags": [],
        "case_study": publish.CASE_STUDY_NOTES.get(slug),
        "robots_noindex": slug in publish.NOINDEX_ARTICLES,
    }
    return env.get_template("article.html").render(
        article=art, body_html="<p>x</p>", generated_at="now")


def test_noindex_lists():
    # Madlanga (24 Oct), plus the follow-up report and its case page (24 Oct,
    # owner's decision). All three stay served by direct link, not deleted.
    assert publish.NOINDEX_ARTICLES == {"madlanga-hawks-limpopo-municipal-fraud", FOLLOW_UP}
    assert publish.NOINDEX_CASES == {CASE17}
    assert "madlanga-hawks-limpopo-municipal-fraud" in publish.DIRECT_LINK_ONLY_ARTICLES
    assert CASE17 in publish.DIRECT_LINK_ONLY_CASES


def test_template_renders_noindex_only_for_listed_article():
    head = _render("madlanga-hawks-limpopo-municipal-fraud").split("</head>")[0]
    assert NOINDEX_RE.search(head)
    follow_up = _render(FOLLOW_UP).split("</head>")[0]
    assert NOINDEX_RE.search(follow_up)
    other = _render("some-other-article").split("</head>")[0]
    assert not NOINDEX_RE.search(other)


def test_case_template_noindex_flag():
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    src = (publish.TMPL_DIR / "case_detail.html").read_text(encoding="utf-8")
    assert '{% if robots_noindex %}<meta name="robots" content="noindex">{% endif %}' in src


def test_committed_dist_matches():
    pages = sorted(DIST.rglob("*.html"))
    assert pages
    for p in pages:
        head = p.read_text(encoding="utf-8").split("</head>")[0]
        rel = p.relative_to(DIST).as_posix()
        if rel in NOINDEX_PAGES:
            assert NOINDEX_RE.search(head), rel
        else:
            assert not NOINDEX_RE.search(head), rel


def test_robots_txt_does_not_block_noindex_page():
    robots = DIST / "robots.txt"
    if not robots.exists():
        return
    text = robots.read_text(encoding="utf-8").lower()
    assert "madlanga" not in text
    assert not re.search(r"^disallow:\s*/\s*$", text, re.M)
