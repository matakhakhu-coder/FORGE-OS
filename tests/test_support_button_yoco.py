"""Support button (30 Oct 2026): "Support Independent OSINT" opens the owner's
Yoco Payment Page, not Ko-fi (the Ko-fi page could not take payments).

The button is rendered in the footer of every page (base.html), as a centred
copy on articles (article.html), and map-first.js moves the footer copy into
the map menu. Three committed pages are not re-rendered by the build (the
direct-link-only articles and case page), so they carry the link by hand;
this checks every committed page, not just the templates."""
import importlib
import pathlib
import re
import sys

from jinja2 import Environment, FileSystemLoader

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import publish  # noqa: E402

YOCO_URL = "https://pay.yoco.com/matameramovhacoza"
LABEL = "Support Independent OSINT"
DIST = REPO / "dist"
KOFI_RE = re.compile(r"ko-?fi", re.I)
BTN_RE = re.compile(r'<a\s+href="([^"]*)"[^>]*class="membership-btn"', re.S)
HAND_KEPT = (
    "articles/madlanga-hawks-limpopo-municipal-fraud.html",
    "articles/limpopo-procurement-arrests-v2.html",
    "cases/limpopo-municipal-procurement-arrests-april-2026-rerun-v2.html",
)


def _config(monkeypatch):
    monkeypatch.delenv("FORGE_MEMBERSHIP_URL", raising=False)
    monkeypatch.delenv("FORGE_REVENUE_LIVE", raising=False)
    import revenue.config as cfg
    return importlib.reload(cfg)


def _env():
    return Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)


def test_config_defaults_to_yoco(monkeypatch):
    cfg = _config(monkeypatch)
    assert cfg.MEMBERSHIP_URL == YOCO_URL
    assert cfg.MEMBERSHIP_LABEL == LABEL
    ctx = cfg.get_template_context()
    assert ctx["membership_url"] == YOCO_URL
    assert ctx["membership_sim"] is False


def test_article_template_both_buttons_go_to_yoco(monkeypatch):
    ctx = _config(monkeypatch).get_template_context()
    art = {
        "title": "T", "slug": "t", "summary": "", "stream": "GLOBAL",
        "stream_label": "Global", "author": "FORGE Map Staff",
        "published_at": "2026-04-01", "published_fmt": "1 Apr 2026", "tags": [],
        "case_study": None, "robots_noindex": False,
    }
    html = _env().get_template("article.html").render(
        article=art, body_html="<p>x</p>", generated_at="now", **ctx)
    hrefs = BTN_RE.findall(html)
    assert len(hrefs) == 2, hrefs  # centred article copy + footer copy
    assert set(hrefs) == {YOCO_URL}
    assert LABEL in html
    assert not KOFI_RE.search(html)


def test_footer_template_goes_to_yoco(monkeypatch):
    ctx = _config(monkeypatch).get_template_context()
    html = _env().get_template("privacy.html").render(generated_at="now", **ctx)
    hrefs = BTN_RE.findall(html)
    assert hrefs == [YOCO_URL]
    assert not KOFI_RE.search(html)


def test_privacy_page_describes_yoco():
    src = (publish.TMPL_DIR / "privacy.html").read_text(encoding="utf-8")
    assert not KOFI_RE.search(src)
    assert "Yoco" in src
    assert "never sees your\n        card details" in src or "never sees your card details" in src


def test_no_kofi_in_templates_or_static():
    for path in list(publish.TMPL_DIR.rglob("*")) + list(publish.STATIC_SRC.rglob("*")):
        if path.is_file() and path.suffix in {".html", ".js", ".css", ".json"}:
            assert not KOFI_RE.search(path.read_text(encoding="utf-8", errors="ignore")), path


def test_every_committed_page_button_goes_to_yoco():
    pages = sorted(DIST.rglob("*.html"))
    assert pages
    with_button = 0
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="ignore")
        rel = page.relative_to(DIST).as_posix()
        assert not KOFI_RE.search(html), f"Ko-fi still mentioned in {rel}"
        hrefs = BTN_RE.findall(html)
        if hrefs:
            with_button += 1
            assert set(hrefs) == {YOCO_URL}, (rel, hrefs)
    for rel in ("index.html", "about.html", "records.html", "contact.html", "privacy.html") + HAND_KEPT:
        assert BTN_RE.findall((DIST / rel).read_text(encoding="utf-8")), f"no support button in {rel}"
    for rel in HAND_KEPT[:2]:
        assert len(BTN_RE.findall((DIST / rel).read_text(encoding="utf-8"))) == 2, rel
    assert with_button >= 8


def test_no_kofi_in_committed_data_files():
    for page in DIST.rglob("*"):
        if page.is_file() and page.suffix in {".json", ".xml", ".txt", ".csv", ".js"}:
            assert not KOFI_RE.search(page.read_text(encoding="utf-8", errors="ignore")), page
