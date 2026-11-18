"""Location photos beside a pin (18 Nov 2026). docs/signals/LOCATION_PHOTOS.md"""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from tools import location_photos as lp
from tools import map_data

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "publisher" / "templates" / "map.html").read_text(encoding="utf-8")

PIN = {"title": "Polokwane: something reported", "lat": -23.9204, "lng": 29.4661,
       "location_note": "Near the stadium, Polokwane"}
SID = "0caff1cd-7cbe-51b2-aab4-ea3528b56f88"


@pytest.fixture
def files(tmp_path):
    data = b"\xff\xd8\xff fake jpeg bytes"
    (tmp_path / "place-photo.jpg").write_bytes(data)
    return tmp_path, hashlib.sha1(data).hexdigest()


def approved(sha1):
    return {
        "id": "place-photo", "signal_id": SID, "kind": "location_context", "status": "approved",
        "ai_generated": False, "caption": "Peter Mokaba Stadium, Polokwane",
        "alt": "Photo of Peter Mokaba Stadium in Polokwane", "taken": "17 June 2010",
        "creator": "Celso FLORES", "licence": "CC BY 2.0",
        "licence_url": "https://creativecommons.org/licenses/by/2.0",
        "source": {"platform": "Wikimedia Commons",
                   "page_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
                   "file_title": "File:Example.jpg", "description": "Peter Mokaba Stadium",
                   "categories": "Stadiums", "original_sha1": "b018f3784721c7af196cc6b3746472dcc28cb984",
                   "retrieved_at": "2026-11-18"},
        "geotag": {"basis": "page_location", "lat": -23.925012, "lng": 29.46855},
        "file": {"name": "place-photo.jpg", "sha1": sha1, "width": 640, "height": 427,
                 "edits": "Resized"},
        "privacy": {k: False for k in lp._PRIVACY_FALSE} | {"checked_by": "owner", "checked_at": "2026-11-18"},
        "approval": {"by": "owner", "at": "2026-11-18"},
    }


def errs_for(p, files_dir):
    return lp.validate_photo(p, {SID: PIN}, files_dir)


def test_a_complete_approved_record_passes(files):
    d, sha = files
    assert errs_for(approved(sha), d) == []


def test_page_entry_works_out_distance_and_keeps_the_basis(files):
    d, sha = files
    got = lp.published_for(SID, PIN, {"photos": [approved(sha)]}, d)
    assert len(got) == 1
    e = got[0]
    assert e["src"] == "static/media/locations/place-photo.jpg"
    assert e["geotag_basis"] == "page_location"
    assert e["geotag_label"] == lp.GEOTAG_LABELS["page_location"]
    want = lp.haversine_m(PIN["lat"], PIN["lng"], -23.925012, 29.46855)
    assert abs(e["distance_m"] - want) <= 5
    assert e["distance_m"] % 10 == 0


def test_candidates_and_withdrawn_never_reach_the_page(files):
    d, sha = files
    for status in ("candidate", "withdrawn"):
        p = approved(sha) | {"status": status}
        assert lp.published_for(SID, PIN, {"photos": [p]}, d) == []


@pytest.mark.parametrize("field", ["people_identifiable", "vehicle_plates_readable",
                                   "private_home", "children", "sensitive_site",
                                   "house_numbers_readable"])
def test_every_privacy_item_must_be_checked(files, field):
    d, sha = files
    p = approved(sha)
    p["privacy"][field] = None
    assert any(f"privacy.{field}" in e for e in errs_for(p, d))
    p["privacy"][field] = True
    assert lp.published_for(SID, PIN, {"photos": [p]}, d) == []


def test_needs_owner_approval(files):
    d, sha = files
    p = approved(sha) | {"approval": {"by": "agent", "at": "2026-11-18"}}
    assert any("approval" in e for e in errs_for(p, d))


@pytest.mark.parametrize("lic", ["CC BY-NC 2.0", "CC BY-ND 4.0", "All rights reserved", "Fair use"])
def test_refuses_licences_forge_cannot_republish(files, lic):
    d, sha = files
    p = approved(sha) | {"licence": lic}
    assert any("licence" in e for e in errs_for(p, d))


def test_needs_credit_and_a_licence_link(files):
    d, sha = files
    assert any("creator" in e for e in errs_for(approved(sha) | {"creator": " "}, d))
    assert any("licence_url" in e for e in errs_for(approved(sha) | {"licence_url": None}, d))


@pytest.mark.parametrize("url", ["https://www.google.com/imgres?x=1",
                                 "https://encrypted-tbn0.gstatic.com/images?q=1",
                                 "https://www.bing.com/images/search?q=x",
                                 "https://za.pinterest.com/pin/1/",
                                 "http://commons.wikimedia.org/wiki/File:X.jpg"])
def test_search_results_and_image_caches_are_not_sources(files, url):
    d, sha = files
    p = approved(sha)
    p["source"]["page_url"] = url
    assert any("source.page_url" in e or "context photos come from" in e for e in errs_for(p, d))


def test_generated_imagery_is_refused(files):
    d, sha = files
    p = approved(sha) | {"ai_generated": True}
    assert any("ai_generated" in e for e in errs_for(p, d))
    p = approved(sha)
    del p["ai_generated"]
    assert any("ai_generated" in e for e in errs_for(p, d))
    p = approved(sha)
    p["source"]["categories"] = "AI-generated images"
    assert any("off-limits" in e for e in errs_for(p, d))


@pytest.mark.parametrize("cap", ["Crime scene in Polokwane", "Where the shooting happened",
                                 "Street where the victim was found", "Gang territory"])
def test_context_captions_name_the_place_not_an_event(files, cap):
    d, sha = files
    assert any("names an event" in e for e in errs_for(approved(sha) | {"caption": cap}, d))
    assert any("names an event" in e for e in errs_for(approved(sha) | {"alt": cap}, d))


@pytest.mark.parametrize("subj", ["Ergates specimen", "Gang tag graffiti", "Mimosa Primary School",
                                  "Victims of violence in South Africa", "ISS047 view of Earth"])
def test_off_limits_subjects(files, subj):
    d, sha = files
    p = approved(sha)
    p["source"]["description"] = subj
    assert any("off-limits" in e for e in errs_for(p, d))


@pytest.mark.parametrize("note", ["At a house in Tafelsig", "Outside the family home",
                                  "In a yard on Main Road", "Near the primary school",
                                  "At the cemetery", "At a shack in the settlement"])
def test_homes_schools_and_graves_get_no_photo(files, note):
    d, sha = files
    pin = dict(PIN, location_note=note)
    assert any("no location photo" in e for e in lp.validate_photo(approved(sha), {SID: pin}, d))
    assert lp.published_for(SID, pin, {"photos": [approved(sha)]}, d) == []


def test_event_words_in_a_title_do_not_block_the_place():
    assert not lp.BLOCKED_PLACE.search(lp.place_text(
        {"title": "Polokwane CBD: man shot outside home affairs office", "location_note": "CBD"}).split(" ", 1)[0])
    assert lp.BLOCKED_PLACE.search(lp.place_text({"title": "House in Delft: x", "location_note": ""}))


def test_too_far_from_the_pin(files):
    d, sha = files
    p = approved(sha)
    p["geotag"] = {"basis": "camera_gps", "lat": -23.90, "lng": 29.52}
    assert any("limit is 2000 m" in e for e in errs_for(p, d))


def test_geotag_basis_is_kept_honest(files):
    d, sha = files
    p = approved(sha)
    p["geotag"] = {"basis": "camera_gps"}
    assert any("lat and lng" in e for e in errs_for(p, d))
    p["geotag"] = {"basis": "described", "lat": -23.9, "lng": 29.4, "place_named": "Polokwane"}
    assert any("no position" in e for e in errs_for(p, d))
    p["geotag"] = {"basis": "described", "place_named": "Peter Mokaba Stadium"}
    assert errs_for(p, d) == []
    e = lp.published_for(SID, PIN, {"photos": [p]}, d)[0]
    assert e["distance_m"] is None and e["place_named"] == "Peter Mokaba Stadium"
    p["geotag"] = {"basis": "guessed", "lat": -23.9, "lng": 29.4}
    assert any("geotag.basis" in e for e in errs_for(p, d))


def test_file_must_match_its_recorded_hash(files):
    d, sha = files
    p = approved(sha)
    p["file"]["sha1"] = "0" * 40
    assert any("sha1 does not match" in e for e in errs_for(p, d))
    p = approved(sha)
    p["file"]["name"] = "../../etc/passwd"
    assert any("file.name" in e for e in errs_for(p, d))
    p = approved(sha)
    p["file"]["width"] = None
    assert any("file.width" in e for e in errs_for(p, d))


def test_incident_photos_need_recorded_verification(files):
    d, sha = files
    p = approved(sha) | {"kind": "incident_verified", "caption": "Scene photo", "alt": "Scene photo"}
    got = errs_for(p, d)
    for k in ("original_source_url", "method", "verified_by", "verified_at", "links_to_incident"):
        assert any(f"verification.{k}" in e for e in got)
    assert any("shows_victim_or_body" in e for e in got)
    p["verification"] = {"original_source_url": "https://example.org/a", "method": "m",
                         "verified_by": "owner", "verified_at": "2026-11-18",
                         "links_to_incident": "named in the police statement"}
    p["privacy"]["shows_victim_or_body"] = False
    assert errs_for(p, d) == []


def test_context_photo_cannot_carry_incident_verification(files):
    d, sha = files
    p = approved(sha) | {"verification": {"method": "x"}}
    assert any("carries no incident verification" in e for e in errs_for(p, d))


def test_at_most_two_per_pin_context_first(files):
    d, sha = files
    a = approved(sha)
    ps = []
    for i in range(3):
        q = copy.deepcopy(a)
        q["id"] = f"p-{i}"
        ps.append(q)
    assert len(lp.published_for(SID, PIN, {"photos": ps}, d)) == 2


# ── The registry as committed ──

def test_committed_registry_is_valid_and_only_the_owner_approved_photo_is_approved():
    # 18 Nov 2026: the owner looked at the Polokwane photo and approved it
    # ("Yes approved" to: free of faces and number plates?). Nothing else.
    reg = lp.load_registry()
    assert lp.validate(reg) == []
    assert lp.approved_files(reg) == ["polokwane-peter-mokaba-stadium-2010.jpg"]
    for p in reg["photos"]:
        assert p["kind"] == "location_context"
        assert p["ai_generated"] is False


def test_committed_polokwane_photo_carries_the_owner_check_and_matches_its_file():
    reg = lp.load_registry()
    p = next(x for x in reg["photos"] if x["id"] == "polokwane-peter-mokaba-stadium-2010")
    assert p["status"] == "approved"
    assert p["approval"] == {"by": "owner", "at": "2026-11-18"}
    assert p["privacy"]["checked_by"] == "owner"
    assert p["privacy"]["checked_at"] == "2026-11-18"
    assert "PREVIEW" not in json.dumps(p)
    fp = lp.FILES_DIR / p["file"]["name"]
    assert hashlib.sha1(fp.read_bytes()).hexdigest() == p["file"]["sha1"]
    assert p["geotag"]["basis"] == "page_location"
    shown = lp.published_for(SID, PIN, reg)
    assert len(shown) == 1
    assert shown[0]["geotag_basis"] == "page_location"
    assert shown[0]["geotag_label"] == "Location from the photo's page, not from camera data"


def test_repo_gitignore_lets_the_deploy_stage_built_place_photos():
    # The deploy stages new dist/ files by the repo's .gitignore; its
    # 'media/' rule would otherwise drop dist/static/media/locations/*.
    lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "!dist/static/media/" in lines
    assert lines.index("!dist/static/media/") > lines.index("media/")


def test_candidate_files_are_outside_the_public_static_folder():
    assert not (ROOT / "publisher" / "static" / "media" / "locations").exists()
    assert "docs" in lp.FILES_DIR.parts


# ── The page ──

def test_photos_are_drawer_detail_not_map_list():
    assert "location_photos" not in map_data.MAP_FIELDS


def test_drawer_marks_context_and_loads_lazily():
    assert 'id="sd-lp-wrap" hidden' in TEMPLATE
    assert "It is not a photo of this incident" in TEMPLATE
    assert "Place photo \\u00b7 not the incident" in TEMPLATE
    assert "img.loading = 'lazy'" in TEMPLATE
    assert "img.addEventListener('error'" in TEMPLATE
    assert "'sd-lp-wrap'" in TEMPLATE.split("var DETAIL_SECTIONS", 1)[1].split(";", 1)[0]


def test_drawer_only_loads_forge_hosted_files():
    m = re.search(r"if \(!p \|\| !/(.+?)/\.test\(p\.src", TEMPLATE)
    assert m and m.group(1).startswith("^static\\/media\\/locations\\/")
    assert "upload.wikimedia.org" not in TEMPLATE


def test_publish_detail_carries_only_approved_photos():
    from tools import publish
    reg = lp.load_registry()
    pin_checked = {"location_note": PIN["location_note"]}
    # The committed registry holds one owner-approved photo (18 Nov 2026).
    shown = publish._location_photos_for(SID, PIN["title"], PIN["lat"], PIN["lng"], pin_checked)
    assert [e["src"] for e in shown] == ["static/media/locations/polokwane-peter-mokaba-stadium-2010.jpg"]
    assert publish._location_photos_for(SID, PIN["title"], None, None, pin_checked) == []
    # No other pin gets it.
    assert publish._location_photos_for("00000000-0000-0000-0000-000000000000", PIN["title"],
                                        PIN["lat"], PIN["lng"], pin_checked) == []
    # A record put back to candidate is not shown.
    cand = copy.deepcopy(reg)
    for p in cand["photos"]:
        p["status"] = "candidate"
    assert lp.published_for(SID, PIN, cand) == []
