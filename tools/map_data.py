"""The map's data, split in two (time-aware pins, Batch 3).

Before Batch 3 the home page carried every pin's full record in one
`var RAW = [...]` list: about 6.6 KB a pin, most of it the drawer's story,
sources, provenance and fact audit. Page size was the first thing to break
in the Batch 1 load test.

Now the build writes two things:

- the map list, still `var RAW = [...]` in index.html, but holding only what
  the map draws and lists without opening a pin (MAP_FIELDS below): the id,
  position, stream, title, the clocks, date precision and location
  uncertainty, plus the few labels the preview, Wire and Area check show;
- one detail file per pin, dist/pins/<first 8 of id>.json, holding the
  pin's full record. The drawer fetches it when the pin is opened.

The map list stays in the page because the map, ticker, Wire, Area check and
the Graph panel all read it at load. Batch 4 splits it into the live window
and the month files.

`load_markers(dist)` puts the two back together, so the guard and the tests
see each pin's full record exactly as before. `problems(dist)` is the after-
build check: one readable detail file per map pin, for that pin, and no
leftover file for a pin no longer on the map.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil

PIN_DIR = "pins"

# What the page needs before a pin is opened. Everything else waits in the
# pin's detail file. Who reads each field:
#   map + preview: signal_id, lat, lng, stream, stream_label, title,
#                  incident, published_fmt, gravity_score, published_at (pulse)
#   Wire list:     checked, source, region, gravity_score
#   Area check:    precision, uncertainty_m, incident_at, incident_end,
#                  incident_event, published_at
#   clocks:        incident_start, incident_at, incident_end, published_at
#   age ring and Updated mark (Batch 7): incident_start, last_material_update_at
MAP_FIELDS: tuple[str, ...] = (
    "signal_id",
    "title",
    "stream",
    "stream_label",
    "lat",
    "lng",
    "gravity_score",
    "checked",
    "source",
    "region",
    "incident",
    "incident_start",
    "incident_at",
    "incident_end",
    "incident_event",
    "precision",
    "uncertainty_m",
    "published_at",
    "published_fmt",
    "last_material_update_at",
)

RAW_RE = re.compile(r"var RAW = (\[.*?\]);\s*$", re.M)


def short_id(signal_id) -> str:
    return str(signal_id or "")[:8].lower()


def detail_name(signal_id) -> str:
    return f"{short_id(signal_id)}.json"


def map_entry(marker: dict) -> dict:
    """The map list's copy of one pin: MAP_FIELDS only, in that order."""
    return {k: marker.get(k) for k in MAP_FIELDS}


def map_list(markers: list[dict]) -> list[dict]:
    return [map_entry(m) for m in markers]


class ShortIdClash(ValueError):
    """Two pins share the first 8 characters of their id: one detail file
    (and one ?signal= link) would serve both."""


def write_pin_details(dist: pathlib.Path, markers: list[dict]) -> int:
    """Write dist/pins/<short id>.json for every pin, after clearing the old
    folder so a pin taken off the map leaves no file behind. Returns the
    number of files written."""
    seen: dict[str, str] = {}
    for m in markers:
        sid = str(m.get("signal_id") or "")
        short = short_id(sid)
        if len(short) < 8:
            raise ShortIdClash(f"pin has no usable id: {m.get('title')!r}")
        if short in seen and seen[short] != sid:
            raise ShortIdClash(f"two pins share the short id {short}: {seen[short]} and {sid}")
        seen[short] = sid
    out = pathlib.Path(dist) / PIN_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for m in markers:
        (out / detail_name(m["signal_id"])).write_text(
            json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(markers)


def read_map_list(index_html: pathlib.Path) -> list[dict]:
    """The map list embedded in index.html (var RAW = [...])."""
    html = pathlib.Path(index_html).read_text(encoding="utf-8")
    m = RAW_RE.search(html)
    if not m:
        raise ValueError("could not find the map markers (var RAW) in index.html")
    return json.loads(m.group(1))


def read_detail(dist: pathlib.Path, signal_id) -> dict:
    return json.loads((pathlib.Path(dist) / PIN_DIR / detail_name(signal_id)).read_text(encoding="utf-8"))


def load_markers(dist: pathlib.Path) -> list[dict]:
    """Every map pin's full record: the map list joined with each pin's
    detail file. A build from before Batch 3 (no pins/ folder, full records
    in the page) is read as it is."""
    dist = pathlib.Path(dist)
    entries = read_map_list(dist / "index.html")
    if not (dist / PIN_DIR).is_dir():
        return entries
    full = []
    for e in entries:
        rec = read_detail(dist, e.get("signal_id"))
        full.append({**rec, **e})
    return full


def _archived_entries(dist: pathlib.Path) -> list[dict]:
    """Map entries of the pins in dist/archive/ month files (Batch 4), so a
    pin that has left the live list still has its detail file. Empty for a
    build without an archive; an unreadable archive is map_archive's to
    report."""
    if not (dist / "archive" / "index.json").is_file():
        return []
    try:
        from tools import map_archive
        months = map_archive.month_entries(dist)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [e for k in sorted(months) for e in months[k]]


def problems(dist: pathlib.Path) -> list[str]:
    """After a build: one readable detail file per pin (on the live map list
    or in a month file), carrying that pin and agreeing with the map list,
    and no file for any other pin."""
    dist = pathlib.Path(dist)
    try:
        live = read_map_list(dist / "index.html")
    except FileNotFoundError:
        return [f"{dist / 'index.html'} was not built"]
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"map markers are not readable ({exc})"]
    live_ids = {e.get("signal_id") for e in live}
    entries = live + [e for e in _archived_entries(dist) if e.get("signal_id") not in live_ids]
    folder = dist / PIN_DIR
    if not folder.is_dir():
        return [f"no {PIN_DIR}/ folder of pin details next to index.html"]
    out: list[str] = []
    extra_keys = sorted({k for e in entries for k in e} - set(MAP_FIELDS))
    if extra_keys:
        out.append("the map list carries detail fields: " + ", ".join(extra_keys))
    want: dict[str, str] = {}
    for e in entries:
        sid = str(e.get("signal_id") or "")
        short = short_id(sid)
        if short in want and want[short] != sid:
            out.append(f"two map pins share the short id {short}")
            continue
        want[short] = sid
        path = folder / detail_name(sid)
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            out.append(f"no detail file for {short} ({e.get('title')})")
            continue
        except json.JSONDecodeError as exc:
            out.append(f"detail file for {short} is not readable JSON ({exc})")
            continue
        if not isinstance(rec, dict) or rec.get("signal_id") != sid:
            out.append(f"detail file {short}.json is for another pin")
            continue
        for k in MAP_FIELDS:
            if rec.get(k) != e.get(k):
                out.append(f"{short}: {k} differs between the map list and its detail file")
    for p in sorted(folder.glob("*.json")):
        if p.stem not in want:
            out.append(f"leftover detail file for a pin not on the map or in the archive: {p.name}")
    return out
