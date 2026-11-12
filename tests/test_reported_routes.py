"""Reported routes (tools/routes.py, docs/routes/POLICY.md, 12 Nov 2026)."""
import copy
import json
from pathlib import Path

import pytest

from tools import routes

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "docs" / "routes" / "or-tambo-cocaine-2026-10-25.json"
AUGUST = ROOT / "docs" / "routes" / "or-tambo-cocaine-2026-08-25.json"


def pilot():
    return json.loads(PILOT.read_text(encoding="utf-8"))


def test_repo_cases_follow_the_rule():
    cases = routes.check()
    assert {c["case_id"] for c in cases} >= {"or-tambo-cocaine-2026-10-25", "or-tambo-cocaine-2026-08-25"}


def test_pilot_and_august_share_one_line_in_date_order():
    # August case approved by the owner on 12 Nov 2026 (approval_note in its file).
    lines = routes.page_routes()
    assert len(lines) == 1
    ids = [c["case_id"] for l in lines for c in l["cases"]]
    assert ids == ["or-tambo-cocaine-2026-08-25", "or-tambo-cocaine-2026-10-25"]
    aug = json.loads(AUGUST.read_text(encoding="utf-8"))
    assert aug["status"] == "approved" and aug["approved_by"] == "Matamela Ramovha"


def test_august_far_end_is_the_sars_release_quote():
    line = routes.page_routes()[0]
    case = next(c for c in line["cases"] if c["case_id"] == "or-tambo-cocaine-2026-08-25")
    assert case["kind"] == "seizure"
    assert case["claim_by"] == "customs"
    assert case["far_sources"][0]["quote"] == "on a flight arriving from Sao Paulo, Brazil"
    assert case["far_sources"][0]["url"].startswith("https://www.sars.gov.za/")
    assert len(case["disagreements"]) == 3


def test_pilot_far_end_is_the_police_statement_quote():
    line = routes.page_routes()[0]
    assert line["far"]["label"] == "São Paulo, Brazil"
    assert line["far"]["place_kind"] == "city"
    case = next(c for c in line["cases"] if c["case_id"] == "or-tambo-cocaine-2026-10-25")
    assert case["claim_by"] == "police"
    assert case["far_sources"][0]["quote"] == "The drugs had arrived on a flight from São Paulo, Brazil."
    assert case["far_sources"][0]["url"].startswith("https://www.saps.gov.za/")
    assert case["disagreements"], "the Witness 'origin undisclosed' report must stay visible"
    assert line["caption"] == routes.CAPTION


def test_a_country_alone_is_never_a_route_end():
    c = pilot()
    f = c["far_ends"][0]
    f["place"], f["label"] = "Brazil", "Brazil, Brazil"
    f["basis"]["quote"] = "The drugs came from Brazil."
    assert any("country or region" in p for p in routes.validate_case(c))
    c2 = pilot()
    c2["far_ends"][0]["place_kind"] = "country"
    assert any("never a country" in p for p in routes.validate_case(c2))


def test_the_quote_must_name_the_place_abroad():
    c = pilot()
    c["far_ends"][0]["basis"]["quote"] = "The drugs had arrived on an international flight."
    assert any("must name" in p for p in routes.validate_case(c))


def test_each_end_needs_a_source_link():
    c = pilot()
    c["far_ends"][0]["basis"]["url"] = ""
    assert any("https link" in p for p in routes.validate_case(c))
    c = pilot()
    c["sa_end"]["basis"] = []
    assert any("sa_end needs at least one source" in p for p in routes.validate_case(c))


def test_ends_on_the_right_side_of_the_border():
    c = pilot()
    c["far_ends"][0]["lat"], c["far_ends"][0]["lng"] = -26.2, 28.0
    assert any("cannot sit inside South Africa" in p for p in routes.validate_case(c))
    c = pilot()
    c["sa_end"]["lat"], c["sa_end"]["lng"] = -23.55, -46.63
    assert any("inside South Africa" in p for p in routes.validate_case(c))


@pytest.mark.parametrize("words", ["a drug hotspot", "a known source country", "a smuggling hub", "a threat"])
def test_bias_wording_is_refused(words):
    c = pilot()
    c["summary"] = c["summary"] + " São Paulo is " + words + "."
    assert any("forbids" in p for p in routes.validate_case(c))


def test_unapproved_cases_never_show():
    c = pilot()
    c["status"] = "held"
    assert routes.public_routes([c]) == []
    c["status"] = "approved"
    c["approved_by"] = ""
    assert any("approved_by" in p for p in routes.validate_case(c))


def test_cases_sharing_both_ends_share_one_line():
    a = pilot()
    b = copy.deepcopy(a)
    b["case_id"], b["incident_date"] = "or-tambo-cocaine-test", "2026-08-25"
    lines = routes.public_routes([a, b])
    assert len(lines) == 1
    assert [c["date"] for c in lines[0]["cases"]] == ["2026-08-25", "2026-10-25"]


def test_a_bad_case_stops_the_publish(tmp_path):
    bad = pilot()
    bad["far_ends"] = []
    (tmp_path / f"{bad['case_id']}.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(routes.RouteError):
        routes.check(tmp_path)


def test_seizures_stay_out_of_the_live_map_and_records():
    """Seizure cases are not signals: nothing in docs/signals carries them."""
    idx = (ROOT / "docs" / "signals" / "index.json").read_text(encoding="utf-8")
    assert "or-tambo-cocaine" not in idx
    assert "216 blocks" not in idx


def test_map_template_keeps_routes_apart_from_threads():
    html = (ROOT / "publisher" / "templates" / "map.html").read_text(encoding="utf-8")
    assert "var REPORTED_ROUTES" in html and "var STORY_THREADS" in html
    assert "forge-route-view" in html and "forge-thread-view" in html
    assert "#f2c14e" not in html.split("Reported routes view (tools/routes.py")[1].split("window.ForgeRoutes")[0]
    assert "this is not a measure of where crime comes from" in html
