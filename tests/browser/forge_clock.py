"""Set "today" in a browser test (time-aware pins, Batch 1).

The live map will work out each pin's age in the browser from today's date, so
browser checks need to pretend it is another day: 31 October (the first day a
1 October pin leaves the live map) or 15 December. This wraps Playwright's
clock so every check sets the day the same way, in South African time.

    from forge_clock import freeze_today
    ctx = browser.new_context()
    freeze_today(ctx, "2026-10-31")          # noon SAST unless at= is given
    page = ctx.new_page(); page.goto(url)    # Date.now() is now 31 Oct, 12:00 SAST
    freeze_today(page, "2026-12-15", at="08:30")   # move the day mid-test

Only Date.now() and new Date() are fixed; timers and animation frames keep
running, so Leaflet and the page behave normally. Call it on the context (or
page) before page.goto() so the page's first read of the date is the fake one.
Needs Playwright 1.45 or later (the clock API).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

SA_TZ = timezone(timedelta(hours=2))      # South Africa: UTC+2, no daylight saving
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")


def sa_instant(day: str, at: str = "12:00") -> datetime:
    """The moment `day` `at` o'clock in South Africa, as an aware datetime."""
    if not isinstance(day, str) or not _DAY.match(day):
        raise ValueError(f"day must be YYYY-MM-DD, got {day!r}")
    if not isinstance(at, str) or not _AT.match(at):
        raise ValueError(f"at must be HH:MM or HH:MM:SS, got {at!r}")
    parts = [int(x) for x in at.split(":")] + [0]
    d = datetime.fromisoformat(day)           # raises on 2026-02-30
    return d.replace(hour=parts[0], minute=parts[1], second=parts[2], tzinfo=SA_TZ)


def epoch_ms(day: str, at: str = "12:00") -> int:
    """What Date.now() returns in the page once freeze_today(day, at) is set."""
    return int(sa_instant(day, at).timestamp() * 1000)


def freeze_today(target, day: str, at: str = "12:00") -> int:
    """Fix the browser's date to `day` `at` (SAST) on a Playwright context or page.

    Returns the epoch milliseconds the page will see.
    """
    clock = getattr(target, "clock", None)
    if clock is None:
        raise RuntimeError("this Playwright has no clock API (needs 1.45 or later)")
    when = sa_instant(day, at)
    clock.set_fixed_time(when)
    return int(when.timestamp() * 1000)
