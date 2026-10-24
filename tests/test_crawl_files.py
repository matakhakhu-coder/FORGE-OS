"""Home page title, canonical links, robots.txt and sitemap.xml (24 Oct 2026).

Every indexable page names its own address on forgemap.co.za; the noindex
Madlanga report has no canonical link and stays out of the sitemap, but
robots.txt must not block it, or crawlers would never read its tag."""
import pathlib
import re
import sys

from jinja2 import Environment, FileSystemLoader

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import publish  # noqa: E402

DIST = REPO / "dist"
HOME_TITLE = "FORGE Map, Open Source Intelligence"
MADLANGA = "articles/madlanga-hawks-limpopo-municipal-fraud.html"
CANON_RE = re.compile(r'<link rel="canonical" href="([^"]+)">')


def _head(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8").split("</head>")[0]


def test_canonical_url_mapping():
    c = publish.canonical_url
    assert publish.SITE_URL == "https://forgemap.co.za"
    assert c("index.html") == "https://forgemap.co.za/"
    assert c("about.html") == "https://forgemap.co.za/about"
    assert c("articles/x.html") == "https://forgemap.co.za/articles/x"
    assert c("cases/y.html") == "https://forgemap.co.za/cases/y"


def test_home_title_in_template_and_dist():
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    src = (publish.TMPL_DIR / "map.html").read_text(encoding="utf-8")
    assert "{% block full_title %}" + HOME_TITLE + "{% endblock %}" in src
    # Other pages keep their own title through base.html
    about = env.get_template("about.html").render(
        generated_at="now", canonical_url=publish.canonical_url("about.html"),
        **publish._get_revenue_context())
    assert "<title>How It Works — " in about
    assert '<link rel="canonical" href="https://forgemap.co.za/about">' in about
    assert f"<title>{HOME_TITLE}</title>" in _head(DIST / "index.html")


def test_template_omits_canonical_when_not_given():
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    html = env.get_template("contact.html").render(
        generated_at="now", **publish._get_revenue_context())
    assert 'rel="canonical"' not in html


def test_every_dist_page_has_its_own_canonical_except_noindex():
    pages = sorted(DIST.rglob("*.html"))
    assert pages
    seen = set()
    for p in pages:
        rel = p.relative_to(DIST).as_posix()
        found = CANON_RE.findall(_head(p))
        if rel == MADLANGA:
            assert found == [], rel
            continue
        assert found == [publish.canonical_url(rel)], rel
        assert found[0] not in seen
        seen.add(found[0])
        assert "vercel.app" not in found[0]


def test_sitemap_lists_indexable_pages_only():
    text = (DIST / "sitemap.xml").read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    assert locs == publish.sitemap_urls(DIST)
    assert locs[0] == "https://forgemap.co.za/"
    assert all(u.startswith("https://forgemap.co.za/") for u in locs)
    assert not any("madlanga" in u for u in locs)
    assert not any(u.endswith(".html") for u in locs)
    for page in ("about", "records", "contact", "privacy"):
        assert f"https://forgemap.co.za/{page}" in locs


def test_robots_points_to_sitemap_and_blocks_nothing():
    text = (DIST / "robots.txt").read_text(encoding="utf-8")
    assert text == publish.robots_txt()
    assert "Sitemap: https://forgemap.co.za/sitemap.xml" in text
    assert not re.search(r"^disallow:", text, re.I | re.M)
