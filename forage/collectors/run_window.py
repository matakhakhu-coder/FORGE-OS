"""
Run window: which publication dates a collector run keeps (Batch 6 step 2, G12).

Until now a run could only count back N days from the moment it started
(--days). A Polokwane run on 23 October with a 22-day window started its
window at about 8 PM on 1 October, so a story Google News dated 07:00 UTC
on 1 October fell just outside it (the 88 firearms story, G12).

A fixed start date is the alternative: "from 1 October" keeps everything
published from the start of 1 October, South African time, up to the end of
the run, whatever hour the run starts. Days still work as before, and with
neither set nothing is filtered.

    window = RunWindow.build(start="1 October")        # or "2026-10-01"
    window.contains("2026-10-01T07:00:00Z")            # True
    window.query_days()                                 # N for Google News when:Nd

The start can also come from FORGE_WINDOW_START. Only the date of the step
the story reports is windowed here: an older conduct date (a court step on a
2025 case) does not push a story out of the window; court_step.py flags it
instead, so it is held on its own merits rather than silently dropped.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# South Africa has one time zone and no daylight saving.
SAST = timezone(timedelta(hours=2))
ENV_START = "FORGE_WINDOW_START"

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_RE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_RE_DM = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\.?(?:,?\s+(\d{4}))?$")
_RE_MD = re.compile(r"^([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$")


def _today_sast(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(SAST).date()


def parse_start(value: str, now: Optional[datetime] = None) -> date:
    """Read a start date: '2026-10-01', '1 October', 'from 1 Oct 2026', 'October 1'.

    With no year, it is the most recent such date that is not in the future
    (on 23 October 2026, '1 October' is 1 October 2026; '1 December' is
    1 December 2025). Raises ValueError for anything else, including a start
    after today.
    """
    if value is None:
        raise ValueError("no start date given")
    s = re.sub(r"\s+", " ", str(value).strip().lower())
    s = re.sub(r"^(from|since|starting)\s+", "", s)
    today = _today_sast(now)
    year = month = day = None
    m = _RE_ISO.match(s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _RE_DM.match(s)
        if m and m.group(2).rstrip(".") in _MONTHS:
            day, month = int(m.group(1)), _MONTHS[m.group(2).rstrip(".")]
            year = int(m.group(3)) if m.group(3) else None
        else:
            m = _RE_MD.match(s)
            if m and m.group(1).rstrip(".") in _MONTHS:
                month, day = _MONTHS[m.group(1).rstrip(".")], int(m.group(2))
                year = int(m.group(3)) if m.group(3) else None
    if month is None or day is None:
        raise ValueError(f"can't read a start date from {value!r}; use YYYY-MM-DD or '1 October'")
    if year is None:
        year = today.year
        try:
            if date(year, month, day) > today:
                year -= 1
        except ValueError:
            pass
    try:
        d = date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not a real date") from exc
    if d > today:
        raise ValueError(f"start date {d.isoformat()} is after today ({today.isoformat()})")
    return d


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RunWindow:
    """start (a SAST calendar day) wins over days; neither means no window."""

    start: Optional[date] = None
    days: Optional[int] = None
    now: Optional[datetime] = None

    @classmethod
    def build(cls, days: Optional[int] = None, start=None, now: Optional[datetime] = None,
              use_env: bool = True) -> "RunWindow":
        """start may be a date or text; when None, FORGE_WINDOW_START is read."""
        if start is None and use_env:
            start = os.environ.get(ENV_START) or None
        if isinstance(start, datetime):
            start = start.astimezone(SAST).date()
        elif start is not None and not isinstance(start, date):
            start = parse_start(start, now=now)
        return cls(start=start, days=days or None, now=now)

    def _now(self) -> datetime:
        return self.now or datetime.now(timezone.utc)

    @property
    def mode(self) -> str:
        if self.start:
            return "fixed_start"
        return "count_back" if self.days else "none"

    def cutoff(self) -> Optional[datetime]:
        """Earliest publication instant kept, in UTC; None when nothing is cut."""
        if self.start:
            return datetime(self.start.year, self.start.month, self.start.day,
                            tzinfo=SAST).astimezone(timezone.utc)
        if self.days:
            return self._now() - timedelta(days=self.days)
        return None

    def contains(self, ts) -> bool:
        """Same contract as infra_signals.in_window: unreadable dates are out
        when a window is set, and up to a day of clock skew ahead is allowed."""
        cut = self.cutoff()
        if cut is None:
            return True
        d = _parse_ts(ts)
        if d is None:
            return False
        return cut <= d <= self._now() + timedelta(days=1)

    def query_days(self, default: int = 7) -> int:
        """N for a Google News 'when:Nd' search that reaches back to the start.
        One day of margin so the first day of the window is never cut off by
        Google's own rounding; the cutoff then trims the extra."""
        if self.start:
            return max(1, (_today_sast(self._now()) - self.start).days + 2)
        return self.days or default

    def describe(self) -> str:
        if self.start:
            return f"from {self.start.isoformat()} (SAST) to the end of the run"
        if self.days:
            return f"last {self.days} days before the run started"
        return "no window"

    def to_dict(self) -> dict:
        cut = self.cutoff()
        return {"mode": self.mode, "start": self.start.isoformat() if self.start else None,
                "days": self.days, "cutoff_utc": cut.strftime("%Y-%m-%dT%H:%M:%SZ") if cut else None}
