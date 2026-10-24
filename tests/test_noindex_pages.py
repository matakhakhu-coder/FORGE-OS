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


def test_only_madlanga_is_noindex():
    assert publish.NOINDEX_ARTICLES == {"madlanga-hawks-limpopo-municipal-fraud"}
    # still served by direct link, not deleted
    assert "madlanga-hawks-limpopo-municipal-fraud" in publish.DIRECT_LINK_ONLY_ARTICLES


def test_template_renders_noindex_only_for_listed_article():
    head = _render("madlanga-hawks-limpopo-municipal-fraud").split("</head>")[0]
    assert NOINDEX_RE.search(head)
    other = _render("limpopo-procurement-arrests-v2").split("</head>")[0]
    assert not NOINDEX_RE.search(other)


def test_committed_dist_matches():
    pages = sorted(DIST.rglob("*.html"))
    assert pages
    for p in pages:
        head = p.read_text(encoding="utf-8").split("</head>")[0]
        rel = p.relative_to(DIST).as_posix()
        if rel == "articles/madlanga-hawks-limpopo-municipal-fraud.html":
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
