"""The How It Works page (publisher/templates/about.html) describes the workflow
as it runs now: hand-checked pins, owner approval, the assistant's role and the
known limitations. These tests stop the old machine-scored bulletin copy from
coming back and check the built page matches its template."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "publisher" / "templates" / "about.html"
BUILT = ROOT / "dist" / "about.html"

# Claims from the old page that the code no longer supports.
RETIRED = [
    "machine-scored",
    "Gravity Scoring",
    "Evidence Confidence",
    "SAFLII",
    "GDELT",
    "verified against court records",
    "Corrections are published",
    "Pulsing markers",
    "satellite",
]

REQUIRED = [
    "Who does what",
    "The owner decides",
    "Maya can&rsquo;t publish a pin on its own",
    "From news report to pin",
    "Known limitations",
    "Not a crime rate",
    "Pins lag the news",
    "It depends on news reports",
    "map point inferred",
    "Method last updated",
    "https://github.com/matakhakhu-coder/FORGE-OS/tree/main/docs/approvals/pins",
]


def _text(path):
    return path.read_text(encoding="utf-8")


def test_template_drops_retired_claims():
    t = _text(TEMPLATE)
    for phrase in RETIRED:
        assert phrase.lower() not in t.lower(), phrase


def test_template_has_current_sections():
    t = _text(TEMPLATE)
    for phrase in REQUIRED:
        assert phrase in t, phrase


def test_no_invented_contact_address():
    # A corrections contact is added only once the owner supplies one.
    t = _text(TEMPLATE)
    assert "mailto:" not in t
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)


def test_built_page_is_current():
    b = _text(BUILT)
    for phrase in REQUIRED:
        assert phrase in b, phrase
    for phrase in RETIRED:
        assert phrase.lower() not in b.lower(), phrase


def test_internal_links_exist():
    b = _text(BUILT)
    for href in set(re.findall(r'href="([a-z0-9_-]+\.html)"', b)):
        assert (ROOT / "dist" / href).exists(), href


SA_PROVINCES = ["Western Cape", "Eastern Cape", "Northern Cape", "North West", "Free State",
                "Gauteng", "KwaZulu-Natal", "Mpumalanga", "Limpopo"]


def _coverage_sentence(t):
    m = re.search(r"The pins so far cover incidents from(.*?)\.\s*</p>", t, re.S)
    assert m, "coverage sentence missing"
    return " ".join(m.group(1).split())


def test_coverage_names_exactly_the_published_provinces():
    """The page names every province with pins, and none without."""
    index = json.loads((ROOT / "docs" / "signals" / "index.json").read_text(encoding="utf-8"))
    published = {b["province"] for b in index["batches"]}
    sentence = _coverage_sentence(_text(TEMPLATE))
    named = {p for p in SA_PROVINCES if p in sentence}
    assert named == published, (named, published)


def test_template_explains_estimated_points():
    # Template only: dist/about.html picks this up on the next tools/publish.py run.
    t = _text(TEMPLATE)
    assert "map point estimated" in t
    assert "reported, inferred or estimated" in t


def test_precision_table_lists_every_level():
    """Every map-precision level the data can use has a row on the page."""
    levels = json.loads((ROOT / "docs" / "signals" / "pin-precision.json").read_text(encoding="utf-8"))["levels"]
    t = _text(TEMPLATE)
    for lvl in levels:
        assert f"<tr><td>{lvl.capitalize()}</td>" in t, lvl
