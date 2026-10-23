"""
Health and disaster alerts: which stream they go to (23 Oct 2026).

Owner decision, 23 Oct 2026: health and disaster alerts are not Priority
Intelligence. PI is organised, political and state-level crime that the
Hawks, Crime Intelligence, NICOC or the SSA would own (pi_signals.py); a
WHO outbreak notice or a GDACS flood alert is neither. They go to the
GLOBAL stream, which the map's Streams panel shows as "Africa & World"
(STREAM_LABELS in tools/publish.py and tools/records_export.py).

The collectors covered here:
  disease_outbreak_collector  HealthMap, PAHO, ReliefWeb Health and
                              ReliefWeb Epidemic, and the velocity-surge
                              signal it writes itself
  rss_collector               the GDACS disaster feed

The open problem, flagged and not guessed
-----------------------------------------
In code GLOBAL is the catch-all stream: anything no rule claims lands there,
whatever the country (Treasury tenders, PMG committees, Civic Intelligence
searches all start as GLOBAL). Its public label, "Africa & World", reads as
foreign news. So an alert about South Africa itself (a GDACS flood in
KwaZulu-Natal, an outbreak notice for South Africa) would sit under a label
that says it is foreign. No stream in code is meant for domestic hazards.

This module does not pick a home for those. Every alert still goes to
GLOBAL, and one that reads as South African carries
    alert_scope       "za_domestic" (named in the text or metadata) or
                      "za_possible" (only its coordinates fall in the box
                      around South Africa, which also holds Lesotho,
                      Eswatini and border areas of four neighbours)
    stream_review     a note that the owner has not decided where domestic
                      alerts belong
so they can be listed and moved later without collecting again. Alerts with
no South African marker carry alert_scope "international".

Nothing here sets is_priority. That column is the Signal Monitor bell, not
the PI stream, and each collector keeps its own bell rule.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    from forage.collectors import pi_signals as _pi
except ImportError:  # pragma: no cover - script use
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from forage.collectors import pi_signals as _pi

ALERT_STREAM = "GLOBAL"

# Sources whose signals are health or disaster alerts. tools/backfill_streams.py
# leaves these on ALERT_STREAM instead of running the news keyword rules.
ALERT_SOURCES = frozenset({
    "disease_outbreak_collector",
    "GDACS",
    "rss_collector",
})

DOMESTIC_REVIEW_NOTE = (
    "South African alert in the Africa & World stream: the owner has not "
    "decided where domestic health and disaster alerts belong (23 Oct 2026)."
)

# Rough box around South Africa (lat, lng). Also holds Lesotho, Eswatini and
# border areas of Namibia, Botswana, Zimbabwe and Mozambique, so a point in it
# is only "possible", never "domestic".
_ZA_BOX = (-35.0, -22.0, 16.3, 33.0)

# Neighbours inside the box: named in the text, they keep an alert foreign.
_NEIGHBOURS = re.compile(r"\b(Lesotho|Eswatini|Swaziland|Namibia|Botswana|Zimbabwe|Mozambique)\b")

# Words for South Africa that collide with nothing in alert text. The shared
# PI marker list also holds party and agency acronyms (ANC, EFF, SIU) that a
# health or disaster alert does not use, so it is read only for places.
_ZA_TEXT = re.compile(
    r"\b(South Africa\w*|Republic of South Africa|RSA|Gauteng|Limpopo|Mpumalanga|KwaZulu-?Natal|KZN|"
    r"Eastern Cape|Western Cape|Northern Cape|North West Province|Free State|Johannesburg|Pretoria|"
    r"Tshwane|Durban|eThekwini|Cape Town|Gqeberha|Port Elizabeth|Polokwane|Mbombela|Bloemfontein|"
    r"Kimberley|Mahikeng|Rustenburg|East London|Ekurhuleni|Hammanskraal)\b")


def _in_box(lat, lng) -> bool:
    try:
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    s, n, w, e = _ZA_BOX
    return s <= la <= n and w <= ln <= e


def alert_scope(title: str, content: str = "", meta: Optional[dict] = None,
                lat=None, lng=None) -> str:
    """'za_domestic', 'za_possible' or 'international'."""
    meta = meta or {}
    country = str(meta.get("country") or "").strip().lower()
    iso3 = str(meta.get("iso3") or meta.get("region_code") or "").strip().upper()
    if country in ("south africa", "republic of south africa", "za") or iso3 in ("ZAF", "ZA", "SOUTH_AFRICA"):
        return "za_domestic"
    text = f"{title or ''}. {content or ''} {meta.get('region') or ''} {meta.get('location') or ''}"
    if _ZA_TEXT.search(text) or _pi._SA_PLACES.search(text):
        return "za_domestic"
    if _NEIGHBOURS.search(text):
        return "international"
    if _in_box(lat if lat is not None else meta.get("lat"), lng if lng is not None else meta.get("lng")):
        return "za_possible"
    return "international"


def route_alert(title: str, content: str = "", meta: Optional[dict] = None,
                lat=None, lng=None) -> tuple[str, dict]:
    """Stream for a health or disaster alert, and the fields to merge into
    its metadata. The stream is always ALERT_STREAM; never PRIORITY."""
    scope = alert_scope(title, content, meta, lat, lng)
    extra = {"alert_scope": scope, "alert_stream_rule": "health/disaster alert -> GLOBAL (23 Oct 2026)"}
    if scope != "international":
        extra["stream_review"] = DOMESTIC_REVIEW_NOTE
    return ALERT_STREAM, extra
