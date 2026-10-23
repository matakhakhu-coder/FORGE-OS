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
A record with no readable date is counted under "date not given" and never
dropped.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

SAST = timezone(timedelta(hours=2))

# The renderers map.html has. A page whose kind is not here is refused.
RENDERERS = ("line", "columns", "bars")

# The fixed chart pages, in swipe order (after the Network page).
CHART_PAGES: tuple[dict, ...] = (
    {
        "id": "incident",
        "tab": "Incident dates",
        "title": "Records by incident date",
        "kind": "line",
        "caption": ("The date it happened, or the date a body was found. Not the report date. "
                    "Days with no records show as zero."),
    },
    {
        "id": "added",
        "tab": "Added",
        "title": "Records added to the map by day",
        "kind": "columns",
        "caption": ("When each pin went on the map, in South African time. This shows how the map "
                    "grew, not when things happened."),
    },
    {
        "id": "stream",
        "tab": "Streams",
        "title": "Records by stream",
        "kind": "bars",
        "caption": "The map stream each pin is filed under.",
    },
    {
        "id": "province",
        "tab": "Provinces",
        "title": "Records by province",
        "kind": "bars",
        "caption": ("Where FORGE has checked so far, not where most incidents happen. "
                    "Some provinces have had more dedicated checking passes than others."),
    },
)

UNDATED = "date not given"

# Line chart drawing box (SVG viewBox units).
LINE_W, LINE_H, LINE_PAD_X, LINE_PAD_TOP, LINE_PAD_BOTTOM = 320, 190, 24, 14, 26


def _check_registry() -> None:
    ids = [p["id"] for p in CHART_PAGES]
    if len(ids) != len(set(ids)) or "network" in ids:
        raise ValueError("chart page ids must be unique and not 'network'")
    for p in CHART_PAGES:
        if p["kind"] not in RENDERERS:
            raise ValueError(f"chart page {p['id']!r}: no renderer for kind {p['kind']!r}")


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


def _incident_series(records: list[dict]) -> dict:
    per_day: dict[date, Counter] = {}
    undated = 0
    for r in records:
        d = sa_date(r.get("incident_start"))
        if d is None:
            undated += 1
            continue
        kind = "found" if r.get("incident_event") == "found" else "occurred"
        per_day.setdefault(d, Counter())[kind] += 1
    days = []
    for d in _fill_days(per_day):
        c = per_day.get(d, Counter())
        days.append({"date": d.isoformat(), "label": _day_label(d),
                     "occurred": c["occurred"], "found": c["found"],
                     "count": c["occurred"] + c["found"]})
    occurred = sum(x["occurred"] for x in days)
    found = sum(x["found"] for x in days)
    out = {"days": days, "undated": undated, "occurred": occurred, "found": found,
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
    c = Counter((r.get(field) or "").strip() or "Not given" for r in records)
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
    pages = []
    for p in CHART_PAGES:
        pages.append({**p, "data": data[p["id"]]})
    return {"total": len(records), "pages": pages}


def chart_total(page: dict) -> int:
    """How many records one chart page counts, undated ones included."""
    d = page["data"]
    if "rows" in d:
        return sum(x["count"] for x in d["rows"])
    return sum(x["count"] for x in d["days"]) + d["undated"]
