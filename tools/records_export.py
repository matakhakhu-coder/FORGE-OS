"""Open Records: the approved map pins as a table, a CSV and a JSON file.

The Records page (dist/records.html), dist/records.csv and dist/records.json
list every pin that is on the public map, and nothing else. A pin is listed
only when all of these hold:

  * it is an active pin in a batch that docs/signals/index.json lists
    (retracted pins are dropped);
  * its approval chain in docs/approvals/pins/ is intact, its latest record
    is an approve or supersede (not a revoke), and that record's payload hash and batch
    file match the pin as it is now;
  * the database row for it is a published, reviewed, hand-checked signal
    (so a pin that never reached the map is never listed); and
  * it has a valid precision entry, a source-provenance entry and a valid
    incident-date entry (docs/signals/incident-dates.json).

Every value comes from an explicit allowlist (RECORD_FIELDS). Nothing is
read from private/, from the database's raw or unreviewed rows, from
analyst notes or from the sprint files, and no verbatim source quote is
exported. The data is a small hand-checked sample of news-reported incidents
across the map's streams, not crime statistics; the caveats travel with the files (CAVEATS).
"""
from __future__ import annotations

import csv
import io
import json
import pathlib
import re
import sqlite3
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SIGNALS_DIR = ROOT / "docs" / "signals"

# The CSV columns, in order. Nothing outside this list is ever written.
RECORD_FIELDS: tuple[str, ...] = (
    "signal_id",
    "short_id",
    "title",
    "province",
    "stream",
    "incident_start",
    "incident_precision",
    "incident_event",
    "incident_date_basis",
    "incident_described",
    "reported_at",
    "location_described",
    "latitude",
    "longitude",
    "map_precision",
    "uncertainty_m",
    "location_basis",
    "reporting_independence",
    "statement_from",
    "outlets",
    "source_urls",
    "source_count",
    "archived_source_count",
    "fact_conflict_count",
    "fact_audit",
    "approval_id",
    "pin_version",
    "approved_at",
    "published_at",
    "map_link",
    "data_version",
    # Time-aware pins, Batch 2: the update clocks, appended so every earlier
    # column keeps its position.
    "last_checked_at",
    "last_material_update_at",
    "update_note",
    "update_sources",
)

# Plain-language help for each column, shown on the page and in records.json.
FIELD_HELP: dict[str, str] = {
    "signal_id": "FORGE's ID for the pin.",
    "short_id": "First 8 characters of the ID, as used in map links.",
    "title": "The pin's title on the map.",
    "province": "Province of the batch the pin was published in.",
    "stream": "The map stream the pin is filed under, as named in the map's Streams panel: Crime & Security, Infrastructure, Priority Intelligence or Africa & World.",
    "incident_start": "When the incident happened or began, as best the reports allow (ISO 8601).",
    "incident_precision": "How exact incident_start is: time, approx_time, part_of_day or day.",
    "incident_event": "occurred = when it happened; found = when a body was found.",
    "incident_date_basis": ("What incident_start stands for. incident = when it happened or began (or was found); "
                            "report = the sources never say when it happened, so it is the report or notice date; "
                            "later_step = an arrest, charge or court date in an older case, whose earlier date "
                            "is in incident_described."),
    "incident_described": "The incident time in words, with who gave it.",
    "reported_at": "Date of the earliest report found (not proven to be the first).",
    "location_described": "Where the reports place it, and what the map point marks.",
    "latitude": "Map point latitude, rounded to 5 decimals. Not an exact scene.",
    "longitude": "Map point longitude, rounded to 5 decimals. Not an exact scene.",
    "map_precision": "What the map point stands for: spot, street, area, suburb, village, town, farm or precinct (an estimate for the police area only).",
    "uncertainty_m": "How far, at most, the real place can plausibly be from the point, in metres.",
    "location_basis": "reported = the reports name this place; inferred = FORGE's reading of a place the reports do not pin down; estimated = the place could not be mapped, so the point stands for the police area that handled the case.",
    "reporting_independence": "Whether the reports are independent of each other (most are not).",
    "statement_from": "Who the reporting rests on, usually police.",
    "outlets": "News outlets that reported it, separated by ' | '.",
    "source_urls": "Source article links, separated by ' | '.",
    "source_count": "Number of source articles linked.",
    "archived_source_count": "How many of those links have a saved archive copy.",
    "fact_conflict_count": "Facts on which the reports disagree.",
    "fact_audit": "Whether the pin's key facts were checked claim by claim against the sources.",
    "approval_id": "The owner approval record this row matches.",
    "pin_version": "Version of the pin's approved data.",
    "approved_at": "When that approval was given.",
    "published_at": "When the pin went on the map.",
    "map_link": "The pin on the map (relative to the site).",
    "data_version": "Git commit of the pin and approval files this export was built from.",
    "last_checked_at": ("When a routine check last re-read the pin's sources. A routine check changes nothing "
                        "else and needs no approval. Blank = not re-checked since it was approved."),
    "last_material_update_at": ("When an owner-approved material update (a real new development) was made. "
                                "Blank = no material update. Wording fixes and corrections never set it, "
                                "and an update never changes incident_start or published_at."),
    "update_note": "One line on what the material update changed. Blank = no material update.",
    "update_sources": "Source links for the material update, separated by ' | '. Blank = no material update.",
}

CAVEATS: tuple[str, ...] = (
    "These are hand-checked news reports of incidents across the map's streams, not statistics. "
    "They are not complete and not representative of everything that happens in any area.",
    "Most rows rest on a single report or on police statements repeated by several outlets; "
    "see reporting_independence.",
    "Map points are approximate. uncertainty_m says how far off a point can be, "
    "and location_basis says when a point is inferred or estimated rather than reported.",
    "Reports can be wrong or change after publication. Check the source links "
    "before relying on any row.",
)

# Stream names as the map's Streams panel shows them. Must match
# STREAM_LABELS in tools/publish.py (a test holds them together).
STREAM_LABELS = {
    "CRIME_INTEL": "Crime & Security",
    "INFRASTRUCTURE": "Infrastructure",
    "PRIORITY": "Priority Intelligence",
    "GLOBAL": "Africa & World",
}

INDEPENDENCE_LABELS = {
    "single_report": "One report only",
    "same_outlet": "One outlet only",
    "same_statement": "All from police statements",
    "shared_wording": "One account, repeated",
    "partly_independent": "Partly independent",
    "independent": "Independent reports",
}
PRECISION_LEVELS = ("spot", "street", "area", "suburb", "village", "town", "farm", "precinct")
LIST_SEP = " | "

# Cells starting with these can be read as formulas by spreadsheet apps.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


class RecordsError(RuntimeError):
    pass


def _read_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordsError(f"{path.name}: could not be read ({exc})") from None
    return data if isinstance(data, dict) else {}


def _clean(value) -> str:
    """One line of plain text."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _http(url) -> str | None:
    u = _clean(url)
    return u if re.match(r"^https?://", u, re.I) else None


def data_version(repo_root: pathlib.Path = ROOT) -> str:
    """The last commit that changed the pin or approval files, or 'unknown'."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%H", "--",
             "docs/signals", "docs/approvals"],
            capture_output=True, text=True, timeout=20)
        sha = out.stdout.strip()
        return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def approved_tips(index_path: pathlib.Path, approvals_dir: pathlib.Path) -> dict[str, dict]:
    """signal_id -> {pin, batch, province, tip} for every active pin whose
    current approval is an intact approve or supersede (never a revoke) that matches the pin
    exactly. Pins that fail any of that are left out."""
    from tools import approvals as ap
    from tools.checked_signals import load_index

    cs = load_index(index_path)
    findings: list = []
    records = ap.load_records(approvals_dir, findings)
    gone = set(cs.retracted)
    out: dict[str, dict] = {}
    for batch in cs.batches:
        for pin in batch.signals:
            sid = pin["signal_id"]
            if sid in gone or sid not in records:
                continue
            tip = ap.chain_tip(sid, records[sid], findings)
            if not tip or tip.get("action") not in ("approve", "supersede"):
                continue
            if tip.get("payload_sha256") != ap.payload_sha256(pin):
                continue
            if tip.get("batch_file") != batch.path.name:
                continue
            out[sid] = {"pin": pin, "batch": batch.path.name,
                        "province": batch.province, "tip": tip}
    return out


def _published_ids(conn: sqlite3.Connection) -> set[str]:
    """Signal IDs the public map can show: reviewed, published, hand-checked."""
    rows = conn.execute(
        "SELECT signal_id, metadata_json FROM signals "
        "WHERE published_at IS NOT NULL AND status IN ('reviewed','published') "
        "AND lat IS NOT NULL AND lng IS NOT NULL").fetchall()
    ids = set()
    for r in rows:
        try:
            meta = json.loads(r[1] or "{}")
        except ValueError:
            continue
        if isinstance(meta, dict) and isinstance(meta.get("checked"), dict):
            ids.add(r[0])
    return ids


def _location_basis(precision_entry: dict, title: str) -> str:
    note = _clean(precision_entry.get("note"))
    if note.upper().startswith("INFERRED") or "map point inferred" in title.lower():
        return "inferred"
    if note.upper().startswith("ESTIMATE") or "map point estimated" in title.lower():
        return "estimated"
    return "reported"


def build_records(conn: sqlite3.Connection,
                  index_path: pathlib.Path | None = None,
                  approvals_dir: pathlib.Path | None = None,
                  signals_dir: pathlib.Path | None = None,
                  version: str | None = None) -> list[dict]:
    """The open records, newest incident first. Each row has exactly RECORD_FIELDS."""
    from tools import approvals as ap

    signals_dir = pathlib.Path(signals_dir or SIGNALS_DIR)
    index_path = pathlib.Path(index_path or signals_dir / "index.json")
    approvals_dir = pathlib.Path(approvals_dir or ap.APPROVALS_DIR)
    version = version or data_version()

    precision = _read_json(signals_dir / "pin-precision.json").get("pins") or {}
    provenance = _read_json(signals_dir / "provenance.json").get("pins") or {}
    fact_bases = _read_json(signals_dir / "fact-bases.json").get("pins") or {}
    from tools import incident_dates as idates
    date_entries = _read_json(signals_dir / "incident-dates.json").get("pins") or {}
    from tools import pin_clocks
    clocks = pin_clocks.load(signals_dir / pin_clocks.CLOCKS_NAME)

    tips = approved_tips(index_path, approvals_dir)
    on_map = _published_ids(conn)

    rows: list[dict] = []
    for sid, item in tips.items():
        if sid not in on_map:
            continue
        pin, tip = item["pin"], item["tip"]
        checked = (pin.get("metadata") or {}).get("checked") or {}
        prec = precision.get(sid)
        prov = provenance.get(sid)
        if not isinstance(prec, dict) or prec.get("precision") not in PRECISION_LEVELS:
            continue
        try:
            unc = int(prec.get("uncertainty_m"))
        except (TypeError, ValueError):
            continue
        if not (0 < unc <= 50000):
            continue
        if not isinstance(prov, dict) or prov.get("independence") not in INDEPENDENCE_LABELS:
            continue
        if pin.get("stream") not in STREAM_LABELS:
            continue
        date_basis = idates.basis_for(date_entries.get(sid), checked.get("incident_start"))
        if date_basis is None:
            continue
        try:
            lat, lng = round(float(pin["lat"]), 5), round(float(pin["lng"]), 5)
        except (KeyError, TypeError, ValueError):
            continue

        urls: list[str] = []
        for s in checked.get("sources") or []:
            u = _http(s.get("url")) if isinstance(s, dict) else None
            if u and u not in urls:
                urls.append(u)

        fb = fact_bases.get(sid)
        audited = isinstance(fb, dict) and fb.get("status") == "audited"
        archived, conflicts = set(), 0
        if audited:
            for fact in fb.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                if [c for c in (fact.get("conflicts") or []) if c]:
                    conflicts += 1
                for src in fact.get("sources") or []:
                    if isinstance(src, dict) and _http(src.get("archive")) and _http(src.get("url")):
                        archived.add(_http(src.get("url")))
        archived &= set(urls)

        title = _clean(pin.get("title"))
        outlets = [_clean(o) for o in (prov.get("outlets") or []) if _clean(o)]
        row = {
            "signal_id": sid,
            "short_id": sid[:8],
            "title": title,
            "province": _clean(item["province"]),
            "stream": STREAM_LABELS[pin["stream"]],
            "incident_start": _clean(checked.get("incident_start")),
            "incident_precision": _clean(checked.get("incident_precision")),
            "incident_event": _clean(checked.get("incident_event")),
            "incident_date_basis": date_basis,
            "incident_described": _clean(checked.get("incident")),
            "reported_at": _clean(checked.get("reported_at")),
            "location_described": _clean(checked.get("location")),
            "latitude": lat,
            "longitude": lng,
            "map_precision": prec["precision"],
            "uncertainty_m": unc,
            "location_basis": _location_basis(prec, title),
            "reporting_independence": INDEPENDENCE_LABELS[prov["independence"]],
            "statement_from": _clean(prov.get("statement_from")),
            "outlets": LIST_SEP.join(outlets),
            "source_urls": LIST_SEP.join(urls),
            "source_count": len(urls),
            "archived_source_count": len(archived) if audited else "",
            "fact_conflict_count": conflicts if audited else "",
            "fact_audit": "audited" if audited else "not yet audited",
            "approval_id": _clean(tip.get("approval_id")),
            "pin_version": int(tip.get("pin_version") or 0),
            "approved_at": _clean(tip.get("approved_at")),
            "published_at": _clean(pin.get("published_at")),
            "map_link": f"index.html?signal={sid[:8]}",
            "data_version": version,
            "last_checked_at": pin_clocks.last_checked_at(clocks.get(sid), pin),
            "last_material_update_at": _clean(checked.get("last_material_update_at")),
            "update_note": _clean(checked.get("update_note")),
            "update_sources": LIST_SEP.join(
                u for u in (_http(s.get("url")) if isinstance(s, dict) else None
                            for s in (checked.get("update_sources") or [])) if u),
        }
        assert tuple(row) == RECORD_FIELDS
        rows.append(row)

    rows.sort(key=lambda r: (r["incident_start"], r["published_at"], r["signal_id"]), reverse=True)
    return rows


def safe_cell(value) -> str:
    """A CSV cell a spreadsheet will not run as a formula. Plain numbers
    (including negative coordinates) stay as they are."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START) and not _NUMBER_RE.match(text):
        return "'" + text
    return text


def to_csv(rows: list[dict]) -> str:
    """RFC 4180 CSV (CRLF line ends, quoted where needed), with a UTF-8 BOM
    so spreadsheet apps read accented names correctly."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(RECORD_FIELDS)
    for r in rows:
        w.writerow([safe_cell(r.get(f)) for f in RECORD_FIELDS])
    return "\ufeff" + buf.getvalue()


def to_json(rows: list[dict], version: str) -> dict:
    return {
        "schema": "za-divergent.records",
        "version": 1,
        "data_version": version,
        "count": len(rows),
        "caveats": list(CAVEATS),
        "fields": [{"name": f, "help": FIELD_HELP[f]} for f in RECORD_FIELDS],
        "records": rows,
    }


def write_records(dist: pathlib.Path, rows: list[dict], version: str) -> dict:
    """Write dist/records.csv and dist/records.json; return the JSON payload."""
    payload = to_json(rows, version)
    (dist / "records.csv").write_text(to_csv(rows), encoding="utf-8", newline="")
    (dist / "records.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
