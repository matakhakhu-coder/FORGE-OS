"""Location photos beside a pin (18 Nov 2026).

A pin can show a small photo of the *place* its map point is at, beside its
sources. The policy is docs/signals/LOCATION_PHOTOS.md; this module enforces it.

Two kinds, never mixed:

- ``location_context``: a public, licensed photo of the place, found from its
  own location data near the pin's map point. It says nothing about the
  incident, and the page says so on every photo.
- ``incident_verified``: a photo of the incident itself. Only with a
  recorded original source, permission or licence, and a recorded
  verification. None exist yet; the validator keeps the bar.

Where a photo's location comes from is kept, never upgraded:

- ``camera_gps``: the photo file's own camera GPS data (EXIF) holds a position.
- ``page_location``: the photo's page gives a position, but the file holds no
  camera GPS (placed on a map by the uploader, or carried over from Flickr
  or Panoramio).
- ``described``: no position at all; the place is only named in the photo's
  title or description. Shown without a distance.

Nothing reaches the page unless its record says ``status: approved`` with the
owner's approval and privacy check written in. Candidates found by ``nearby``
stay in docs/photos/ and are never published.

CLI:
  python -m tools.location_photos check            # validate the registry
  python -m tools.location_photos nearby SIGNAL_ID  # research only, writes
                                                    # docs/photos/candidates/
  python -m tools.location_photos fetch PHOTO_ID    # download the reduced copy
                                                    # into docs/photos/files/
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs" / "signals" / "location-photos.json"
FILES_DIR = ROOT / "docs" / "photos" / "files"
CANDIDATES_DIR = ROOT / "docs" / "photos" / "candidates"
# Where approved files go in the built site (dist/static/media/locations/).
SITE_SUBDIR = "media/locations"

KINDS = ("location_context", "incident_verified")
STATUSES = ("candidate", "approved", "withdrawn")
GEOTAG_BASES = ("camera_gps", "page_location", "described")

GEOTAG_LABELS = {
    "camera_gps": "Location from the photo file's camera GPS data",
    "page_location": "Location from the photo's page, not from camera data",
    "described": "No location data; the place is only named in the photo's description",
}

# The farthest a context photo may be from the pin's map point. A pin's own
# uncertainty is shown beside the distance; a wider pin does not widen this.
MAX_DISTANCE_M = 2000

# Licences a stranger may republish with credit. No NC (FORGE takes support
# payments) and no ND (FORGE resizes every file).
_LICENCE_RE = re.compile(
    r"^(CC0( 1\.0)?|Public domain|CC BY( \d\.\d)?|CC BY-SA( \d\.\d)?)$", re.I)
_LICENCE_URL_HOSTS = ("creativecommons.org", "commons.wikimedia.org", "en.wikipedia.org")

# Platforms whose pages carry the licence, the creator and the location data.
_SOURCE_HOSTS = ("commons.wikimedia.org",)
# A search result or an image cache is not a source.
_BLOCKED_HOSTS = ("google.", "gstatic.", "bing.", "pinterest.", "yandex.",
                  "duckduckgo.", "tineye.", "lens.", "images.search.")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Context captions name a place, never an event.
_INCIDENT_WORDS = re.compile(
    r"\b(scene|crime|shoot\w*|shot|stab\w*|murder\w*|kill\w*|attack\w*|victim\w*|"
    r"body|bodies|blood\w*|assault\w*|robber\w*|hijack\w*|gang\w*|mob|riot\w*|"
    r"police tape|cordon\w*|where it happened|incident)\b", re.I)
# A place note that puts the pin at a home, a school or a grave gets no
# location photo at all: a photo would point at a family or at children.
BLOCKED_PLACE = re.compile(
    r"\b(home|homes|house|houses|residence|residential|yard|shack|dwelling|"
    r"property|premises|school|schools|creche|cr\u00e8che|learners?|grave|cemetery|"
    r"shelter|clinic|hospital)\b", re.I)
# What a photo found by location must not be about.
BLOCKED_SUBJECT = re.compile(
    r"(ai[- ]generated|midjourney|stable diffusion|dall-?e|illustration|"
    r"specimen|victims? of violence|gang tag|graffiti|childhood development|"
    r"\bschool\b|creche|view of earth|\bISS\d)", re.I)

# Not a picture of a place: found near real pins in the 26 Nov research run
# (banknotes, a bird subspecies, plates of food, a pallet jack). Refused as
# off-topic in research so the owner is only shown photos of places.
OFF_TOPIC = re.compile(
    r"(banknote|\bcoins?\b|postage stamp|subspecies|\bssp\.|\bspecies\b|\bbirds?\b|"
    r"insect|butterfl|\bflowers?\b|\bfood\b|cuisine|cousine|\bdish\b|recipe|portrait|"
    r"selfie|\blogo\b|pallet|equipment|product photo|diagram|\bmap of\b|screenshot)", re.I)

_PRIVACY_FALSE = ("people_identifiable", "vehicle_plates_readable", "private_home",
                  "children", "sensitive_site", "house_numbers_readable")


class PhotoError(ValueError):
    pass


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _https(url) -> bool:
    return isinstance(url, str) and url.startswith("https://") and bool(_host(url))


def _host_in(url: str, hosts) -> bool:
    h = _host(url)
    return any(h == x or h.endswith("." + x) for x in hosts)


def place_text(pin: dict) -> str:
    """The words that say where a pin is: its place note and the place part
    of its title (before the colon), not what happened there."""
    title = str(pin.get("title") or "").split(":", 1)[0]
    return f"{pin.get('location_note') or ''} {title}"


def load_registry(path: Path | None = None) -> dict:
    path = path or REGISTRY
    if not path.exists():
        return {"version": 1, "photos": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("photos"), list):
        raise PhotoError(f"{path.name}: needs a 'photos' list")
    return data


def validate_photo(p: dict, pins: dict | None = None, files_dir: Path | None = None) -> list[str]:
    """Return the problems with one record; empty when it may be published
    in its stated status. ``pins`` maps signal_id to {lat, lng, location_note}."""
    errs: list[str] = []
    pid = p.get("id", "?")
    say = lambda m: errs.append(f"{pid}: {m}")
    files_dir = files_dir or FILES_DIR

    if not isinstance(p.get("id"), str) or not _ID_RE.match(p["id"]):
        say("id must be lowercase letters, digits and dashes")
    kind, status = p.get("kind"), p.get("status")
    if kind not in KINDS:
        say(f"kind must be one of {KINDS}")
    if status not in STATUSES:
        say(f"status must be one of {STATUSES}")
    sid = p.get("signal_id")
    if not isinstance(sid, str) or not sid:
        say("signal_id missing")
    if p.get("ai_generated") is not False:
        say("ai_generated must be recorded as false; generated imagery is never shown")

    src = p.get("source") or {}
    page = src.get("page_url")
    if not _https(page):
        say("source.page_url must be an https page")
    elif _host_in(page, _BLOCKED_HOSTS) or any(b in _host(page) for b in _BLOCKED_HOSTS):
        say("source.page_url is a search result or image cache, not a source")
    elif not _host_in(page, _SOURCE_HOSTS) and kind == "location_context":
        say("context photos come from a page that states licence, creator and location (Wikimedia Commons)")
    if not src.get("platform"):
        say("source.platform missing")
    if not (isinstance(src.get("retrieved_at"), str) and _DATE_RE.match(src["retrieved_at"])):
        say("source.retrieved_at must be YYYY-MM-DD")
    if src.get("original_sha1") is not None and not _SHA1_RE.match(str(src.get("original_sha1"))):
        say("source.original_sha1 must be a 40-character hex SHA-1")

    if not str(p.get("creator") or "").strip():
        say("creator missing (credit is required)")
    lic = str(p.get("licence") or "").strip()
    perm = p.get("permission")
    if lic:
        if not _LICENCE_RE.match(lic):
            say(f"licence {lic!r} is not one FORGE can republish (no NC, no ND)")
        if not lic.lower().startswith(("public domain",)) and not (
                _https(p.get("licence_url")) and _host_in(p["licence_url"], _LICENCE_URL_HOSTS)):
            say("licence_url must link the licence text")
    elif not (isinstance(perm, dict) and perm.get("granted_by") and perm.get("record")):
        say("needs a licence, or a written permission record")

    for f in ("caption", "alt"):
        if not str(p.get(f) or "").strip():
            say(f"{f} missing")
    if kind == "location_context":
        for f in ("caption", "alt"):
            if _INCIDENT_WORDS.search(str(p.get(f) or "")):
                say(f"{f} names an event; a context photo names only the place")
        subj = " ".join(str(src.get(k) or "") for k in ("file_title", "description", "categories"))
        if BLOCKED_SUBJECT.search(subj):
            say("the photo's own title, description or categories put it off-limits")
        if "verification" in p:
            say("a context photo carries no incident verification")

    geo = p.get("geotag") or {}
    basis = geo.get("basis")
    if basis not in GEOTAG_BASES:
        say(f"geotag.basis must be one of {GEOTAG_BASES}")
    has_pos = isinstance(geo.get("lat"), (int, float)) and isinstance(geo.get("lng"), (int, float))
    if basis in ("camera_gps", "page_location") and not has_pos:
        say("geotag needs lat and lng copied from the photo's own data")
    if basis == "described" and has_pos:
        say("a described-only photo has no position; do not add one")
    if basis == "described" and not str(geo.get("place_named") or "").strip():
        say("geotag.place_named must quote the place the description names")

    pin = (pins or {}).get(sid) if sid else None
    if pins is not None and pin is None and status == "approved":
        say("its pin is not a published pin")
    if pin:
        note = place_text(pin)
        if kind == "location_context" and BLOCKED_PLACE.search(note):
            say("the pin's place is a home, school, grave or care site; no location photo")
        if has_pos and pin.get("lat") is not None:
            d = haversine_m(pin["lat"], pin["lng"], geo["lat"], geo["lng"])
            if kind == "location_context" and d > MAX_DISTANCE_M:
                say(f"{d:.0f} m from the pin's map point; the limit is {MAX_DISTANCE_M} m")

    priv = p.get("privacy") or {}
    appr = p.get("approval") or {}
    if status == "approved":
        for k in _PRIVACY_FALSE:
            if priv.get(k) is not False:
                say(f"privacy.{k} must be checked and false before approval")
        if not (priv.get("checked_by") and isinstance(priv.get("checked_at"), str)
                and _DATE_RE.match(priv["checked_at"])):
            say("privacy check needs checked_by and checked_at (YYYY-MM-DD)")
        if appr.get("by") != "owner" or not (isinstance(appr.get("at"), str) and _DATE_RE.match(appr["at"])):
            say("approval needs by: owner and at: YYYY-MM-DD")
        fl = p.get("file") or {}
        name = fl.get("name")
        if not (isinstance(name, str) and re.match(r"^[a-z0-9][a-z0-9-]*\.(jpg|jpeg|webp|png)$", name)):
            say("file.name must be a plain .jpg/.webp/.png name")
        else:
            fp = files_dir / name
            if not fp.exists():
                say(f"file {name} is not in docs/photos/files/")
            elif fl.get("sha1") != hashlib.sha1(fp.read_bytes()).hexdigest():
                say("file.sha1 does not match the hosted file")
        for k in ("width", "height"):
            if not (isinstance(fl.get(k), int) and fl[k] > 0):
                say(f"file.{k} needed (stops the layout jumping while it loads)")
        if not str(fl.get("edits") or "").strip():
            say("file.edits must say what FORGE changed (e.g. 'resized'); licences require it")

    if kind == "incident_verified":
        v = p.get("verification") or {}
        for k in ("original_source_url", "method", "verified_by", "verified_at", "links_to_incident"):
            if not str(v.get(k) or "").strip():
                say(f"verification.{k} missing; an incident photo needs recorded provenance")
        if v.get("original_source_url") and not _https(v.get("original_source_url")):
            say("verification.original_source_url must be https")
        if priv.get("shows_victim_or_body") is not False:
            say("privacy.shows_victim_or_body must be checked and false")
    return errs


def validate(registry: dict, pins: dict | None = None, files_dir: Path | None = None) -> list[str]:
    errs, seen = [], set()
    for p in registry.get("photos", []):
        if not isinstance(p, dict):
            errs.append("a photo record is not an object")
            continue
        if p.get("id") in seen:
            errs.append(f"{p.get('id')}: duplicate id")
        seen.add(p.get("id"))
        errs.extend(validate_photo(p, pins, files_dir))
    return errs


def page_entry(p: dict, pin: dict) -> dict:
    """What the drawer gets for one approved photo. Distance is worked out
    here from the two positions, never typed in."""
    geo = p["geotag"]
    dist = None
    if geo["basis"] in ("camera_gps", "page_location"):
        dist = int(round(haversine_m(pin["lat"], pin["lng"], geo["lat"], geo["lng"]) / 10.0) * 10)
    fl = p["file"]
    src = p["source"]
    return {
        "kind": p["kind"],
        "src": f"static/{SITE_SUBDIR}/{fl['name']}",
        "width": fl["width"],
        "height": fl["height"],
        "alt": p["alt"].strip(),
        "caption": p["caption"].strip(),
        "creator": str(p["creator"]).strip(),
        "licence": str(p.get("licence") or "Used with permission").strip(),
        "licence_url": p.get("licence_url") or None,
        "edits": str(fl.get("edits") or "").strip(),
        "source_url": src["page_url"],
        "platform": src["platform"],
        "taken": (str(p.get("taken")).strip() or None) if p.get("taken") else None,
        "geotag_basis": geo["basis"],
        "geotag_label": GEOTAG_LABELS[geo["basis"]],
        "place_named": geo.get("place_named") or None,
        "distance_m": dist,
    }


def published_for(signal_id: str, pin: dict, registry: dict | None = None,
                  files_dir: Path | None = None) -> list[dict]:
    """Approved photos for one pin that pass every rule, context first."""
    reg = registry if registry is not None else load_registry()
    out = []
    for p in reg.get("photos", []):
        if not isinstance(p, dict) or p.get("signal_id") != signal_id or p.get("status") != "approved":
            continue
        if validate_photo(p, {signal_id: pin}, files_dir):
            continue
        out.append(page_entry(p, pin))
    out.sort(key=lambda e: (e["kind"] != "location_context", e["distance_m"] if e["distance_m"] is not None else 10 ** 9))
    return out[:2]


def approved_files(registry: dict | None = None) -> list[str]:
    reg = registry if registry is not None else load_registry()
    return sorted({p["file"]["name"] for p in reg.get("photos", [])
                   if isinstance(p, dict) and p.get("status") == "approved"
                   and isinstance(p.get("file"), dict) and p["file"].get("name")})


# ── Research: what Commons has near a pin (never published by itself) ─────────

UA = "FORGE-Map-location-photos/1.0 (https://forgemap.co.za)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _commons(params: dict) -> dict:
    params = dict(params, format="json", formatversion="2")
    req = urllib.request.Request(COMMONS_API + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _strip(s) -> str:
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def commons_nearby(lat: float, lng: float, radius_m: int = MAX_DISTANCE_M, limit: int = 50) -> list[dict]:
    """Commons files whose page gives a position within radius_m. Each row
    keeps what the page and the file say, plus why it would be refused."""
    hits = _commons({"action": "query", "list": "geosearch", "gscoord": f"{lat}|{lng}",
                     "gsradius": int(radius_m), "gsnamespace": 6, "gslimit": limit}
                    ).get("query", {}).get("geosearch", [])
    rows = []
    for i in range(0, len(hits), 20):
        titles = "|".join(h["title"] for h in hits[i:i + 20])
        info = _commons({"action": "query", "titles": titles, "prop": "imageinfo|coordinates",
                         "coprimary": "all", "iiprop": "url|size|sha1|mime|extmetadata|metadata",
                         "iiurlwidth": 640})
        for pg in info.get("query", {}).get("pages", []):
            ii = (pg.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata", {})
            g = lambda k: _strip(em.get(k, {}).get("value"))
            coords = pg.get("coordinates") or []
            cam = [c for c in coords if c.get("primary")] or coords[:1]
            if not cam:
                continue
            exif = {m.get("name") for m in (ii.get("metadata") or []) if isinstance(m, dict)}
            basis = "camera_gps" if {"GPSLatitude", "GPSLongitude"} & exif else "page_location"
            row = {
                "file_title": pg["title"], "page_url": ii.get("descriptionurl"),
                "thumb_url": ii.get("thumburl"), "mime": ii.get("mime"),
                "width": ii.get("width"), "height": ii.get("height"),
                "original_sha1": ii.get("sha1"),
                "geotag": {"basis": basis, "lat": cam[0]["lat"], "lng": cam[0]["lon"]},
                "distance_m": round(haversine_m(lat, lng, cam[0]["lat"], cam[0]["lon"])),
                "creator": g("Artist"), "licence": g("LicenseShortName"),
                "licence_url": g("LicenseUrl") or None, "taken": g("DateTimeOriginal") or None,
                "description": g("ImageDescription")[:400], "categories": g("Categories")[:600],
            }
            refuse = []
            if not _LICENCE_RE.match(row["licence"] or ""):
                refuse.append("licence")
            subj_text = " ".join([row["file_title"], row["description"], row["categories"]])
            if BLOCKED_SUBJECT.search(subj_text):
                refuse.append("subject")
            if OFF_TOPIC.search(subj_text):
                refuse.append("off-topic")
            if row["mime"] not in ("image/jpeg", "image/png", "image/webp"):
                refuse.append("not a photo file")
            row["auto_refusals"] = refuse
            rows.append(row)
    rows.sort(key=lambda r: r["distance_m"])
    return rows


def write_candidates(sid: str, pin: dict, search=None) -> tuple[Path, dict]:
    """Research one pin: write docs/photos/candidates/<id8>.json. Never
    approves or publishes anything."""
    search = search or commons_nearby
    blocked = bool(BLOCKED_PLACE.search(place_text(pin)))
    rows = [] if blocked else search(pin["lat"], pin["lng"])
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    outp = CANDIDATES_DIR / f"{sid[:8]}.json"
    data = {
        "signal_id": sid, "pin": pin, "searched_at": _dt.date.today().isoformat(),
        "radius_m": MAX_DISTANCE_M, "place_blocked": blocked,
        "note": "Research only. Nothing here is published until a record in "
                "docs/signals/location-photos.json is approved by the owner.",
        "candidates": rows}
    outp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return outp, data


def summary_line(sid: str, data: dict) -> str:
    """One line for a run report: what the owner can approve for this pin."""
    if data.get("place_blocked"):
        return f"{sid[:8]}: no photo (the place is a home, school, grave or care site)"
    usable = [r for r in data.get("candidates", []) if not r.get("auto_refusals")]
    if not usable:
        return f"{sid[:8]}: no usable licensed photo within {MAX_DISTANCE_M} m; published without one"
    r = usable[0]
    return (f"{sid[:8]}: {len(usable)} candidate(s) for the owner to look at; nearest "
            f"{r['file_title']} ({r['distance_m']} m, {r['licence']}, {r['creator']}) {r['page_url']}")


def research_new_pins(pins: dict, search=None) -> tuple[list[Path], list[str]]:
    """For an unattended run: research every new pin. Never raises and never
    blocks a pin; a failed lookup is reported and the pin goes out without a photo."""
    written, lines = [], []
    for sid, pin in pins.items():
        try:
            p, data = write_candidates(sid, pin, search)
            written.append(p)
            lines.append(summary_line(sid, data))
        except Exception as exc:   # noqa: BLE001 - research must never stop a publish
            lines.append(f"{sid[:8]}: photo research failed ({type(exc).__name__}); published without one")
    return written, lines


def _pins_from_db(db_path: str) -> dict:
    import sqlite3
    con = sqlite3.connect(db_path)
    out = {}
    for sid, title, meta, lat, lng in con.execute(
            "SELECT signal_id, title, metadata_json, lat, lng FROM signals WHERE published_at IS NOT NULL"):
        try:
            chk = (json.loads(meta or "{}") or {}).get("checked") or {}
        except ValueError:
            chk = {}
        out[sid] = {"title": title, "lat": lat, "lng": lng, "location_note": chk.get("location")}
    return out


def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tools.location_photos")
    ap.add_argument("cmd", choices=("check", "nearby", "fetch"))
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--db", default=None, help="FORGE database (default: tools.publish DB)")
    a = ap.parse_args(argv)
    db = a.db
    if db is None:
        import os
        db = os.environ.get("FORGE_DB") or str(ROOT / "database.db")
    if a.cmd == "check":
        pins = _pins_from_db(db) if Path(db).exists() else None
        errs = validate(load_registry(), pins)
        for e in errs:
            print("  " + e)
        reg = load_registry()
        n_ok = sum(1 for p in reg["photos"] if p.get("status") == "approved")
        print(f"[location-photos] {len(reg['photos'])} records, {n_ok} approved, {len(errs)} problems")
        return 1 if errs else 0
    if a.cmd == "nearby":
        pins = _pins_from_db(db)
        pin = pins.get(a.arg) or next((v for k, v in pins.items() if k.startswith(a.arg or "-")), None)
        if not pin:
            print("no published pin with that id")
            return 2
        sid = a.arg if a.arg in pins else next(k for k in pins if k.startswith(a.arg))
        outp, data = write_candidates(sid, pin)
        print(f"[location-photos] {len(data['candidates'])} candidates within {MAX_DISTANCE_M} m -> "
              f"{outp.relative_to(ROOT)}")
        print("[location-photos] " + summary_line(sid, data))
        return 0
    if a.cmd == "fetch":
        reg = load_registry()
        p = next((x for x in reg["photos"] if x.get("id") == a.arg), None)
        if not p:
            print("no photo record with that id")
            return 2
        url = (p.get("source") or {}).get("thumb_url")
        if not (_https(url) and _host(url).endswith("wikimedia.org")):
            print("source.thumb_url must be an https upload.wikimedia.org / thumb URL")
            return 2
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        name = (p.get("file") or {}).get("name") or f"{p['id']}.jpg"
        (FILES_DIR / name).write_bytes(data)
        print(f"[location-photos] {name}: {len(data)} bytes, sha1 {hashlib.sha1(data).hexdigest()}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
