"""Contact and Privacy pages (24 Oct 2026): rendered on every build, linked
from every page footer, and free of owner notes, placeholders and the phone
number. Privacy must describe Social now that it runs on forgemap.co.za."""
import pathlib
import re
import sys

from jinja2 import Environment, FileSystemLoader

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import publish  # noqa: E402

DIST = REPO / "dist"
EMAIL = "rileynotbishop@gmail.com"
OPERATOR = "K2026598376 (South Africa) (Pty) Ltd"
PRIVATE_MARKERS = ("[CONFIRM", "CONFIRM EMAIL", "Owner checklist", "not for publication",
                   "713 572", "071 357", "0713572252", "WhatsApp", "[DATE")


def _render(name: str) -> str:
    env = Environment(loader=FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    return env.get_template(name).render(generated_at="now", **publish._get_revenue_context())


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_legal_pages_are_built():
    assert publish.LEGAL_PAGES == ("contact.html", "privacy.html")


def test_pages_name_operator_and_email_without_private_notes():
    for name in publish.LEGAL_PAGES:
        for html in (_render(name), (DIST / name).read_text(encoding="utf-8")):
            text = _text(html)
            assert OPERATOR in text, name
            assert "2026/598376/07" in text, name
            assert "Matamela Ramovha" in text and "2026-063320" in text, name
            assert EMAIL in text, name
            for marker in PRIVATE_MARKERS:
                assert marker not in html, (name, marker)


def test_privacy_describes_social_and_retention():
    text = _text((DIST / "privacy.html").read_text(encoding="utf-8"))
    assert "Member sign-in and chat (Social)" in text
    assert "Firebase Authentication" in text
    assert "no set period" in text
    assert "90 days after the request is resolved" in text
    assert "there is no sign-up" not in text
    assert "nothing you type on the site is sent to us" not in text


def test_contact_has_no_response_time_promise():
    text = _text((DIST / "contact.html").read_text(encoding="utf-8")).lower()
    assert "working days" not in text and "reply within" not in text


def test_every_page_footer_links_contact_and_privacy():
    pages = sorted(DIST.rglob("*.html"))
    assert len(pages) >= 8
    for p in pages:
        footer = p.read_text(encoding="utf-8").split('<footer class="site-footer">')[1]
        prefix = "../" if p.parent != DIST else ""
        assert f'href="{prefix}contact.html"' in footer, p
        assert f'href="{prefix}privacy.html"' in footer, p


def test_area_check_notice_and_about_link_to_pages():
    index = (DIST / "index.html").read_text(encoding="utf-8")
    assert 'Geoapify privacy policy</a> &middot; <a href="privacy.html">FORGE Map privacy</a>' in index
    about = (DIST / "about.html").read_text(encoding="utf-8")
    assert 'id="trust-and-corrections"' in about
    assert 'href="contact.html#report-a-mistake"' in about
    assert 'id="report-a-mistake"' in (DIST / "contact.html").read_text(encoding="utf-8")
