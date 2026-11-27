"""Public case files: only deliberately published material reaches dist/."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import public_cases as pc  # noqa: E402


def _png(path: pathlib.Path, gps: bool = False):
    from PIL import Image
    im = Image.new("RGB", (40, 30), (10, 20, 30))
    if gps:
        exif = Image.Exif()
        exif[pc.GPS_IFD_TAG] = {1: "S"}
        im.save(path, "PNG", exif=exif)
    else:
        im.save(path, "PNG")


def _case(tmp, **over):
    folder = tmp / "demo-case"
    folder.mkdir()
    _png(folder / "shot.png")
    data = {
        "public": True, "slug": "demo-case", "title": "Demo", "summary": "A demo case.",
        "case_type": "general", "published_at": "2026-11-27",
        "publication": {"approved_by": "owner", "approved_on": "2026-11-27", "basis": "test"},
        "artifacts": [{"kind": "photo", "file": "shot.png", "title": "Shot", "alt": "A shot",
                       "caption": "Cap", "credit": "Screenshot"},
                      {"kind": "video", "url": "https://www.youtube.com/watch?v=x", "title": "V",
                       "caption": "c"}],
    }
    data.update(over)
    (folder / "case.json").write_text(json.dumps(data))
    return folder


def test_repo_case_files_are_valid():
    cases = pc.load_cases()
    assert any(c["slug"] == "forge-map-screenshots" for c in cases)
    for c in cases:
        for a in c["photos"]:
            assert a["alt"] and a["caption"] and a["width"] > 0
        assert not c["unlisted"], "files in a public case folder must be listed"


def test_valid_case_loads(tmp_path):
    _case(tmp_path)
    (c,) = pc.load_cases(tmp_path)
    assert c["photos"][0]["width"] == 40
    assert c["photos"][0]["src"] == "static/cases/demo-case/shot.png"


def test_not_public_is_skipped(tmp_path):
    _case(tmp_path, public=False)
    assert pc.load_cases(tmp_path) == []


@pytest.mark.parametrize("art", [
    {"kind": "photo", "file": "../secret.png", "title": "t", "alt": "a", "caption": "c", "credit": "x"},
    {"kind": "photo", "file": "shot.png", "title": "t", "caption": "c", "credit": "x"},
    {"kind": "video", "url": "http://www.youtube.com/watch?v=x", "title": "t", "caption": "c"},
    {"kind": "video", "url": "https://evil.example/v.mp4", "title": "t", "caption": "c"},
    {"kind": "pdf", "file": "shot.png", "title": "t", "caption": "c"},
    {"kind": "photo", "file": "shot.png", "title": "t", "alt": "a", "caption": "c", "credit": "x",
     "db_path": "/home/x"},
])
def test_bad_artifacts_refused(tmp_path, art):
    _case(tmp_path, artifacts=[art])
    with pytest.raises(pc.PublicCaseError):
        pc.load_cases(tmp_path)


def test_fake_pdf_refused(tmp_path):
    folder = _case(tmp_path)
    (folder / "doc.pdf").write_bytes(b"not a pdf")
    data = json.loads((folder / "case.json").read_text())
    data["artifacts"] = [{"kind": "pdf", "file": "doc.pdf", "title": "t", "caption": "c"}]
    (folder / "case.json").write_text(json.dumps(data))
    with pytest.raises(pc.PublicCaseError):
        pc.load_cases(tmp_path)


def test_gps_photo_refused(tmp_path):
    folder = _case(tmp_path)
    _png(folder / "shot.png", gps=True)
    with pytest.raises(pc.PublicCaseError):
        pc.load_cases(tmp_path)


def test_build_copies_only_listed_files(tmp_path):
    from tools import publish
    base = tmp_path / "src"
    base.mkdir()
    folder = _case(base)
    (folder / "private-note.png").write_bytes(b"x")
    dist = tmp_path / "dist"
    dist.mkdir()
    env = publish.Environment(loader=publish.FileSystemLoader(str(publish.TMPL_DIR)), autoescape=True)
    images = publish.build_public_cases(env, dist=dist, base=base)
    copied = sorted(p.name for p in (dist / "static" / "cases" / "demo-case").iterdir())
    assert copied == ["shot.png"]
    html = (dist / "cases" / "demo-case.html").read_text()
    assert 'alt="A shot"' in html and "noindex" not in html
    assert "https://www.youtube.com/watch?v=x" in html
    page = f"{publish.SITE_URL}/cases/demo-case"
    assert images[page] == [f"{publish.SITE_URL}/static/cases/demo-case/shot.png"]
    xml = publish.sitemap_xml([page], images)
    assert "<image:loc>" in xml and "sitemap-image/1.1" in xml
