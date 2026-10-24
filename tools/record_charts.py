"""Fixed charts for the Graph panel, counted from the open records.

The Graph panel on the public map has swipeable pages: the actor network
first, then the fixed charts listed in CHART_PAGES. Every chart counts the
same approved records that tools/records_export.build_records() returns for
records.html and records.json, one per signal_id, so the charts and the
Records page always show the same total. publish.py builds both in one pass,
so the charts change in the same publish that adds or removes pins.

This is a small registry, not a chart builder: each page names one of the
renderers map.html already has (RENDERERS). Nothing here reads the
database, and the output holds only labels, dates and counts: no titles,
quotes, places or sources.

Dates used:
  * incident date: the date part of incident_start, in South African time.
    For a body that was found, that is the date it was found
    (incident_event = "found"). It is never the report date.
  * added to the map: published_at (UTC in the pin files), converted to
    South African time before the date is taken.
Which records go on the incident line comes from incident_date_basis
(docs/signals/incident-dates.json, via tools/records_export.py):
  * incident: on the line, on the day of incident_start;
  * report: incident_start is the report or notice date because the source
    never says when it happened, so it counts as "date not stated";
  * later_step: incident_start is an arrest, charge or court date in an older
    case, so it is counted apart ("later step in an older case") and never
    put on the day of that later step;
  * missing or anything else: "date not stated" (never plotted unclassified).
A record with no readable date is counted under "date not stated" too. No
record is ever dropped: every chart adds up to the Records page total.

Timeline (the slider under the chart pages): timeline_table() writes a
compact table the page script (publisher/static/js/record-charts.js) only
filters and draws. One step per publish (one distinct published_at); one row
per record with its short ID, the step that added it, and the same incident
day/kind, added day, stream and province the charts above use, worked out by
the same helpers. The axes (days and bar order) are today's, so they stay
fixed while scrubbing. No titles, places, quotes or sources.

Stream switches (interactive charts Batch 5): the charts share the map's
stream filter, so the table also names the map's four streams in the
Streams panel order (key and label), and each chart page says where its
switches sit (STREAM_FILTER): a row of switches above the chart, or, on
Streams, the bars themselves.

Heat map (Batch 8): records by town and incident date, one row per town and
one cell per day on the incident line's axis, plus a "date not stated" and a
"later step" cell per town, by the same incident_date_basis rules as the
line. Records have no town field: publish.py adds one for the charts only,
from tools/record_towns.py (FORGE's coverage-area naming rule plus the
hand-kept fixes in docs/signals/record-towns.json), and stops if a record has
no town. The open data stays as it is.

Tap to see the records (Batch 4) takes the
titles from the map's own pin data, matched by short ID, never from here.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

SAST = timezone(timedelta(hours=2))

# The renderers map.html has. A page whose kind is not here is refused.
RENDERERS = ("line", "columns", "bars", "heatmap")

# Where a chart page's stream switches sit: "chips" is the row of switches
# above the chart, "bars" means tapping a bar turns its stream on or off.
STREAM_FILTERS = ("chips", "bars")

# The fixed chart pages, in swipe order (after the Network page).
CHART_PAGES: tuple[dict, ...] = (
    {
        "id": "incident",
        "tab": "Incident dates",
        "title": "Records by incident date",
        "kind": "line",
        "stream_filter": "chips",
        "caption": ("The date it happened, or the date a body was found. Never the report date: "
                    "where a report gives only that, the record is counted as date not stated. "
                    "An arrest, charge or court step in an older case is counted apart, not on the "
                    "day of that step. Days with no records show as zero."),
    },
    {
        "id": "added",
        "tab": "Added",
        "title": "Records added to the map by day",
        "kind": "columns",
        "stream_filter": "chips",
        "caption": ("When each pin went on the map, in South African time. This shows how the map "
                    "grew, not when things happened."),
    },
    {
        "id": "stream",
        "tab": "Streams",
        "title": "Records by stream",
        "kind": "bars",
        "stream_filter": "bars",
        "caption": "The map stream each pin is filed under.",
    },
    {
        "id": "province",
        "tab": "Provinces",
        "title": "Records by province",
        "kind": "bars",
        "stream_filter": "chips",
        "caption": ("Where FORGE has checked so far, not where most incidents happen. "
                    "Some provinces have had more dedicated checking passes than others."),
    },
    {
        # Interactive charts Batch 8. Towns come from tools/record_towns.py.
        "id": "town",
        "tab": "Towns",
        "title": "Records by town and incident date",
        "kind": "heatmap",
        "stream_filter": "chips",
        "caption": ("Where FORGE has checked so far, not where most incidents happen. A blank cell "
                    "means FORGE published nothing for that town and day, not that nothing happened. "
                    "Towns follow FORGE's coverage-area naming rule, with a few hand-kept fixes."),
    },
)

UNDATED = "date not stated"

# Line chart drawing box (SVG viewBox units).
LINE_W, LINE_H, LINE_PAD_X, LINE_PAD_TOP, LINE_PAD_BOTTOM = 320, 190, 24, 14, 26


def _check_registry() -> None:
    ids = [p["id"] for p in CHART_PAGES]
    if len(ids) != len(set(ids)) or "network" in ids:
        raise ValueError("chart page ids must be unique and not 'network'")
    for p in CHART_PAGES:
        if p["kind"] not in RENDERERS:
            raise ValueError(f"chart page {p['id']!r}: no renderer for kind {p['kind']!r}")
        if p.get("stream_filter") not in STREAM_FILTERS:
            raise ValueError(f"chart page {p['id']!r}: stream_filter must be one of {STREAM_FILTERS}")
        if p["stream_filter"] == "bars" and p["id"] != "stream":
            raise ValueError(f"chart page {p['id']!r}: only the Streams bars can be stream switches")


_check_registry()


def sa_date(value) -> date | None:
    """The South African calendar date of an ISO date or date-time, or None.

    A plain date is taken as it is. A date-time with an offset is converted
    to UTC+02:00 first; one without an offset is taken as local time."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(SAST)
    return dt.date()


def _day_label(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _fill_days(counts: dict[date, dict]) -> list[date]:
    """Every day from the first to the last, so quiet days show as zero."""
    if not counts:
        return []
    first, last = min(counts), max(counts)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def incident_class(r: dict) -> tuple[date | None, str]:
    """(day on the incident line or None, kind): kind is occurred, found,
    undated ("date not stated") or later_step. The one rule for the line,
    shared by the charts and the timeline table."""
    basis = r.get("incident_date_basis")
    if basis == "later_step":
        return None, "later_step"
    d = sa_date(r.get("incident_start")) if basis == "incident" else None
    if d is None:
        return None, "undated"
    return d, ("found" if r.get("incident_event") == "found" else "occurred")


def category_label(r: dict, field: str) -> str:
    return (r.get(field) or "").strip() or "Not given"


def _town_series(records: list[dict], days: list[str]) -> dict:
    """The heat map (Batch 8): one row per town, one cell per incident day on
    the incident line's axis, plus the date-not-stated and later-step counts,
    which follow the same incident_date_basis rules as the line. Towns are
    ordered by records, then by name."""
    idx = {d: i for i, d in enumerate(days)}
    towns = Counter(category_label(r, "town") for r in records)
    cells = {t: [0] * len(days) for t in towns}
    und = Counter()
    later = Counter()
    for r in records:
        t = category_label(r, "town")
        d, kind = incident_class(r)
        if kind == "later_step":
            later[t] += 1
        elif d is None:
            und[t] += 1
        else:
            cells[t][idx[d.isoformat()]] += 1
    rows = [{"label": t, "count": n, "days": cells[t], "undated": und[t], "later_step": later[t]}
            for t, n in sorted(towns.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
    peak = max([v for x in rows for v in x["days"] + [x["undated"], x["later_step"]]] or [0])
    return {"rows": rows, "days": [{"date": d, "label": _day_label(date.fromisoformat(d))} for d in days],
            "max": peak, "undated": sum(und.values()), "later_step": sum(later.values())}


def _incident_series(records: list[dict]) -> dict:
    per_day: dict[date, Counter] = {}
    undated = later_step = 0
    for r in records:
        d, kind = incident_class(r)
        if kind == "later_step":
            later_step += 1
            continue
        if d is None:
            undated += 1
            continue
        per_day.setdefault(d, Counter())[kind] += 1
    days = []
    for d in _fill_days(per_day):
        c = per_day.get(d, Counter())
        days.append({"date": d.isoformat(), "label": _day_label(d),
                     "occurred": c["occurred"], "found": c["found"],
                     "count": c["occurred"] + c["found"]})
    occurred = sum(x["occurred"] for x in days)
    found = sum(x["found"] for x in days)
    out = {"days": days, "undated": undated, "later_step": later_step,
           "occurred": occurred, "found": found,
           "max": max([x["count"] for x in days] or [0])}
    out["line"] = _line_geometry(days, out["max"])
    return out


def _line_geometry(days: list[dict], top: int) -> dict:
    """Points for the SVG line, worked out here so the template only prints."""
    if not days:
        return {"points": [], "polyline": "", "ticks": [], "grid": [], "w": LINE_W, "h": LINE_H,
                "x0": LINE_PAD_X, "x1": LINE_W - LINE_PAD_X}
    n = len(days)
    top = max(top, 1)
    inner_w = LINE_W - 2 * LINE_PAD_X
    inner_h = LINE_H - LINE_PAD_TOP - LINE_PAD_BOTTOM
    pts = []
    for i, d in enumerate(days):
        x = LINE_PAD_X + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        y = LINE_PAD_TOP + inner_h * (1 - d["count"] / top)
        pts.append({"x": round(x, 1), "y": round(y, 1), "count": d["count"],
                    "label": d["label"], "date": d["date"]})
    step = max(1, -(-n // 5))                        # at most about 5 date labels (readable on a phone)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        if 2 * (n - 1 - idx[-1]) < step and len(idx) > 1:
            idx.pop()                                # keep the last day, drop a crowded neighbour
        idx.append(n - 1)
    ticks = [pts[i] for i in idx]
    grid = [{"value": v, "y": round(LINE_PAD_TOP + inner_h * (1 - v / top), 1)}
            for v in sorted({0, top, (top + 1) // 2 if top > 1 else 0})]
    return {"points": pts, "polyline": " ".join(f"{p['x']},{p['y']}" for p in pts),
            "ticks": ticks, "grid": grid, "w": LINE_W, "h": LINE_H,
            "x0": LINE_PAD_X, "x1": LINE_W - LINE_PAD_X}


def _added_series(records: list[dict]) -> dict:
    per_day: Counter = Counter()
    undated = 0
    for r in records:
        d = sa_date(r.get("published_at"))
        if d is None:
            undated += 1
        else:
            per_day[d] += 1
    days = [{"date": d.isoformat(), "label": _day_label(d), "count": per_day.get(d, 0)}
            for d in _fill_days(per_day)]
    return {"days": days, "undated": undated, "max": max([x["count"] for x in days] or [0])}


def _category_series(records: list[dict], field: str) -> dict:
    c = Counter(category_label(r, field) for r in records)
    rows = [{"label": k, "count": v} for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"rows": rows, "max": max([x["count"] for x in rows] or [0])}


def chart_counts(records: list[dict]) -> dict:
    """Counts for every chart page, from the open records (one per signal_id).

    Raises ValueError if a signal_id appears twice, so a chart can never
    count one pin more than once."""
    ids = [r.get("signal_id") for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("chart_counts: a signal_id appears more than once")
    data = {
        "incident": _incident_series(records),
        "added": _added_series(records),
        "stream": _category_series(records, "stream"),
        "province": _category_series(records, "province"),
    }
    data["town"] = _town_series(records, [d["date"] for d in data["incident"]["days"]])
    pages = []
    for p in CHART_PAGES:
        pages.append({**p, "data": data[p["id"]]})
    return {"total": len(records), "pages": pages}


def chart_total(page: dict) -> int:
    """How many records one chart page counts, undated ones included."""
    d = page["data"]
    if "rows" in d:
        return sum(x["count"] for x in d["rows"])
    return sum(x["count"] for x in d["days"]) + d["undated"] + d.get("later_step", 0)


# ── Timeline table for the slider (Batch 1 of the interactive charts) ─────────

TABLE_COLUMNS = ("id", "step", "inc", "kind", "added", "stream", "province", "town")


def _published_instant(value) -> datetime | None:
    text = str(value or "").strip()
    if len(text) <= 10:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=SAST)).astimezone(SAST)


def _step_label(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{dt.day} {dt.strftime('%b')}, {h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _week_label(a: date, b: date) -> str:
    if a == b:
        return _day_label(a)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day} to {b.day} {b.strftime('%b')}"
    return f"{_day_label(a)} to {_day_label(b)}"


def chart_weeks(days: list[str]) -> list[dict]:
    """The week buttons for a day axis (interactive charts Batch 7): runs of
    seven days counted from the axis's first day, so 1 to 21 October gives
    "1 to 7 Oct", "8 to 14 Oct" and "15 to 21 Oct"; the last week may be
    short. An axis of seven days or fewer has nothing to zoom into, so it gets
    no buttons (today the Added axis is 18 to 23 October). from/to are
    indexes into the axis, both inclusive."""
    if len(days) <= 7:
        return []
    out = []
    for i in range(0, len(days), 7):
        j = min(i + 6, len(days) - 1)
        out.append({"label": _week_label(date.fromisoformat(days[i]), date.fromisoformat(days[j])),
                    "from": i, "to": j})
    return out


def week_split(table: dict, step: int, chart: str, week: dict, streams_on=None) -> dict:
    """What a week button shows at one slider step: the records on the
    week's days and on the axis's other days, which together make the
    chart's dated total (the summary's undated and later-step counts sit
    outside every week). chart is "incident" or "added". Mirrors the page
    script's week note; the tests use it to prove the totals add up."""
    c = counts_from_table(table, step, streams_on)
    days = table["axes"][f"{chart}_days"]
    per = c[chart]["days"]
    inside = sum(per[week["from"]:week["to"] + 1])
    every = sum(per)
    return {"days": days[week["from"]:week["to"] + 1], "in_week": inside, "other_days": every - inside}


def ids_on_map(table: dict, step: int) -> list[str]:
    """The short IDs the map keeps when the slider sits on one publish step
    (interactive charts Batch 6): every record added by that publish. Streams
    and Area check apply on top in the map itself."""
    col = {c: i for i, c in enumerate(table["columns"])}
    return [r[col["id"]] for r in table["rows"] if r[col["step"]] <= step]


def timeline_table(records: list[dict], charts: dict | None = None) -> dict:
    """The publish steps and one compact row per record, for the page script.

    steps: one per distinct published_at, oldest first, in South African
    time, each with the cumulative record total on the map by then. A record
    with no readable published_at cannot be placed in time, so it joins the
    last step ("on the map now"); no record is ever dropped.
    axes: today's days and bar order (from chart_counts), fixed while
    scrubbing."""
    if charts is None:
        charts = chart_counts(records)
    pages = {p["id"]: p["data"] for p in charts["pages"]}
    instants = sorted({i for i in (_published_instant(r.get("published_at")) for r in records) if i})
    unplaced = any(_published_instant(r.get("published_at")) is None for r in records)
    steps = [{"at": i.isoformat(timespec="minutes"), "label": _step_label(i), "day": i.date().isoformat()}
             for i in instants]
    if unplaced or (records and not steps):
        steps.append({"at": None, "label": "date added not recorded", "day": None})
    index = {i: k for k, i in enumerate(instants)}

    rows = []
    for r in sorted(records, key=lambda r: (str(r.get("published_at") or "~"), r.get("signal_id") or "")):
        inst = _published_instant(r.get("published_at"))
        d, kind = incident_class(r)
        added = sa_date(r.get("published_at"))
        rows.append([
            (r.get("short_id") or str(r.get("signal_id") or "")[:8]),
            index[inst] if inst is not None else len(steps) - 1,
            d.isoformat() if d else None,
            kind,
            added.isoformat() if added else None,
            category_label(r, "stream"),
            category_label(r, "province"),
            category_label(r, "town"),
        ])
    per_step = Counter(row[1] for row in rows)
    running = 0
    for k, st in enumerate(steps):
        running += per_step.get(k, 0)
        st["total"] = running
    # Day marks under the slider: where each day starts. The month is named
    # on the first mark and when it changes; the rest are the day number, so
    # the marks fit side by side on a phone.
    marks, last_day, last_month = [], object(), None
    for k, st in enumerate(steps):
        if st["day"] and st["day"] != last_day:
            d = date.fromisoformat(st["day"])
            marks.append({"step": k, "label": _day_label(d) if (d.year, d.month) != last_month else str(d.day)})
            last_month = (d.year, d.month)
        last_day = st["day"]
    inc = pages.get("incident", {})
    from tools.records_export import STREAM_LABELS   # the map's four streams, in panel order
    return {
        "total": len(records),
        "columns": list(TABLE_COLUMNS),
        "streams": [{"key": k, "label": v} for k, v in STREAM_LABELS.items()],
        "steps": steps,
        "marks": marks,
        "rows": rows,
        "axes": {
            "incident_days": [x["date"] for x in inc.get("days", [])],
            "incident_max": inc.get("max", 0),
            "incident_weeks": chart_weeks([x["date"] for x in inc.get("days", [])]),
            "added_days": [x["date"] for x in pages.get("added", {}).get("days", [])],
            "added_max": pages.get("added", {}).get("max", 0),
            "added_weeks": chart_weeks([x["date"] for x in pages.get("added", {}).get("days", [])]),
            "stream": [x["label"] for x in pages.get("stream", {}).get("rows", [])],
            "stream_max": pages.get("stream", {}).get("max", 0),
            "province": [x["label"] for x in pages.get("province", {}).get("rows", [])],
            "province_max": pages.get("province", {}).get("max", 0),
            "town": [x["label"] for x in pages.get("town", {}).get("rows", [])],
            "town_max": pages.get("town", {}).get("max", 0),
        },
        "line": {k: inc.get("line", {}).get(k) for k in ("w", "h", "x0", "x1")} | {
            "top": LINE_PAD_TOP, "bottom": LINE_H - LINE_PAD_BOTTOM},
    }


def counts_from_table(table: dict, step: int, streams_on: set[str] | None = None) -> dict:
    """Recount the charts from the table at one step (what the page script
    does). Used by the tests to prove the table reproduces chart_counts.
    streams_on: the map stream keys switched on (None: all of them); records
    in a stream switched off are left out of every chart, as on the page."""
    col = {c: i for i, c in enumerate(table["columns"])}
    ax = table["axes"]
    live = [r for r in table["rows"] if r[col["step"]] <= step]
    if streams_on is not None:
        on_labels = {s["label"] for s in table.get("streams", []) if s["key"] in streams_on}
        live = [r for r in live if r[col["stream"]] in on_labels]
    inc = {d: {"occurred": 0, "found": 0} for d in ax["incident_days"]}
    undated = later = 0
    for r in live:
        k = r[col["kind"]]
        if k == "later_step":
            later += 1
        elif k == "undated":
            undated += 1
        else:
            inc[r[col["inc"]]][k] += 1
    added = {d: 0 for d in ax["added_days"]}
    added_undated = 0
    for r in live:
        if r[col["added"]] is None:
            added_undated += 1
        else:
            added[r[col["added"]]] += 1
    out = {
        "total": len(live),
        "incident": {"days": [inc[d]["occurred"] + inc[d]["found"] for d in ax["incident_days"]],
                     "occurred": sum(v["occurred"] for v in inc.values()),
                     "found": sum(v["found"] for v in inc.values()),
                     "undated": undated, "later_step": later},
        "added": {"days": [added[d] for d in ax["added_days"]], "undated": added_undated},
    }
    for f in ("stream", "province"):
        c = Counter(r[col[f]] for r in live)
        out[f] = [c.get(lab, 0) for lab in ax[f]]
    if "town" in ax:
        ti = {t: i for i, t in enumerate(ax["town"])}
        di = {d: i for i, d in enumerate(ax["incident_days"])}
        cells = [[0] * len(ax["incident_days"]) for _ in ax["town"]]
        und = [0] * len(ax["town"])
        later = [0] * len(ax["town"])
        for r in live:
            i, k = ti[r[col["town"]]], r[col["kind"]]
            if k == "later_step":
                later[i] += 1
            elif k == "undated":
                und[i] += 1
            else:
                cells[i][di[r[col["inc"]]]] += 1
        out["town"] = {"cells": cells, "undated": und, "later_step": later,
                       "rows": [sum(cells[i]) + und[i] + later[i] for i in range(len(ax["town"]))]}
    return out


def ids_from_table(table: dict, step: int, chart: str, key=None,
                   streams_on: set[str] | None = None) -> list[str]:
    """The short IDs behind one bar, column, line day or count at one step
    (what the page's "tap to see the records" list shows). chart is a page
    id; key is the day (ISO), the bar label, or for the incident line
    "undated" / "later_step", and for Added None means "date added not
    recorded". Used by the tests to prove each list matches the count tapped."""
    col = {c: i for i, c in enumerate(table["columns"])}
    on_labels = None
    if streams_on is not None:
        on_labels = {s["label"] for s in table.get("streams", []) if s["key"] in streams_on}
    out = []
    for r in table["rows"]:
        if r[col["step"]] > step or (on_labels is not None and r[col["stream"]] not in on_labels):
            continue
        if chart == "incident":
            hit = (r[col["kind"]] == key) if key in ("undated", "later_step") else \
                  (r[col["kind"]] in ("occurred", "found") and r[col["inc"]] == key)
        elif chart == "added":
            hit = r[col["added"]] == key
        elif chart == "town":
            # key "Town|2026-10-16", "Town|undated", "Town|later_step" or "Town|*"
            town, _, day = key.partition("|")
            k = r[col["kind"]]
            hit = r[col["town"]] == town and (
                day == "*" or (k == day if day in ("undated", "later_step")
                               else (k in ("occurred", "found") and r[col["inc"]] == day)))
        else:
            hit = r[col[chart]] == key
        if hit:
            out.append(r[col["id"]])
    return out
