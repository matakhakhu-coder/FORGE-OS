#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE NDBC Buoy Collector
━━━━━━━━━━━━━━━━━━━━━━━━
Ingests marine meteorological observations from NOAA's National Data Buoy Center
(ndbc.noaa.gov) into the FORGE signals table.

Dual-mode per station
─────────────────────
• First run  → realtime2 text file  (up to 45 days of hourly observations)
• Subsequent → RSS feed             (last ~5 observations, low overhead)

Detection of "first run": checks whether any signal with source='ndbc' and
external_id prefix matching the station already exists in the database.

Configuration
─────────────
  NDBC_STATIONS  comma-separated station IDs, e.g. "41049,13008,46042"
                 Browse active stations: https://www.ndbc.noaa.gov/obs.shtml
                 Pan to your area of interest and note the 5-char station codes.
  FORGE_DB       path to database.db  (default: project root)

Priority triggers  (is_priority = 1)
─────────────────────────────────────
  WVHT >= 4.0 m     significant wave height  (storm / gale sea state)
  WSPD >= 15.0 m/s  wind speed               (~Beaufort 7, near-gale)

Stream classification: INFRASTRUCTURE
  Marine buoys monitor ocean conditions relevant to shipping lanes, ports, and
  coastal infrastructure. Decay rate 0.006 (slowest — conditions persist).
"""
__manifest__ = {
    "id":          "ndbc_collector",
    "name":        "NDBC Buoy Observations",
    "description": "NOAA buoy: wave height, wind, SST, pressure. realtime2 backfill + RSS live.",
    "icon":        "🌊",
    "entry":       "forage/collectors/ndbc_collector.py",
    "args":        [],
    "job_key":     "ndbc_collector",
    "version":     "1.0.0",
}

import asyncio as _asyncio
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── Try to import pipeline logger (graceful no-op if unavailable) ─────────────
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*a, **kw): pass  # noqa: E704

# ── Config ────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parents[2]
_DB_PATH  = (
    Path(os.environ["FORGE_DB"]).resolve()
    if os.environ.get("FORGE_DB")
    else _BASE_DIR / "database.db"
)

# Station IDs set via NDBC_STATIONS env var.
# Leave empty — must be configured by the operator.
# How to find station IDs:
#   1. Go to https://www.ndbc.noaa.gov/obs.shtml
#   2. Pan the map to your region (e.g. South African coast, Southern Ocean)
#   3. Click a buoy marker — the station ID appears in the URL and title
#   4. Add it to your .env:  NDBC_STATIONS="41049,13008,46042"
_DEFAULT_STATIONS: list[str] = []

_PRIORITY_WAVE_M  = 4.0    # WVHT metres — storm/gale sea state
_PRIORITY_WIND_MS = 15.0   # WSPD m/s    — Beaufort 7 near-gale

_STATION_TABLE_URL = "https://www.ndbc.noaa.gov/data/stations/station_table.txt"
_REALTIME_URL      = "https://www.ndbc.noaa.gov/data/realtime2/{sid}.txt"
_RSS_URL           = "https://www.ndbc.noaa.gov/rss/buoy.rss?station={sid}"
_UA = (
    "Mozilla/5.0 (compatible; FORGE-OSINT-NDBC/1.0; "
    "https://github.com/forge-osint)"
)

# ── Logging helpers ───────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"[ndbc_collector] {msg}", flush=True)

def _warn(msg: str) -> None:
    print(f"[ndbc_collector] WARNING: {msg}", flush=True)

def _err(msg: str) -> None:
    print(f"[ndbc_collector] ERROR: {msg}", file=sys.stderr, flush=True)

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 30) -> str:
    """Fetch URL, return decoded text. Raises RuntimeError on failure."""
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"fetch failed [{url}]: {exc}") from exc

# ── Database ──────────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"FORGE database not found at {path}.\n"
            "Run:  python app.py --init-db\n"
            "Or:   set FORGE_DB=/path/to/database.db"
        )
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _station_has_data(conn: sqlite3.Connection, station_id: str) -> bool:
    """True if any signals already exist for this station."""
    row = conn.execute(
        "SELECT 1 FROM signals WHERE source = 'ndbc' "
        "AND external_id LIKE ? LIMIT 1",
        (f"ndbc:{station_id.lower()}:%",),
    ).fetchone()
    return row is not None


def _insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """INSERT OR IGNORE — returns (inserted, skipped)."""
    inserted = skipped = 0
    for sig in signals:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO signals
                (signal_id, source, external_id, title, content,
                 lat, lng, timestamp, status, metadata_json, is_priority)
            VALUES
                (:signal_id, :source, :external_id, :title, :content,
                 :lat, :lng, :timestamp, :status, :metadata_json, :is_priority)
            """,
            sig,
        )
        if cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return inserted, skipped

# ── Station metadata (lat / lng / name) ───────────────────────────────────────

def _load_station_meta(station_ids: list[str]) -> dict[str, dict]:
    """
    Fetch NDBC station_table.txt and return {STATION_ID: {lat, lng, name}}.
    File format (after comment lines):
        STN     LAT       LON      ELEV  |WNAME
        41001   34.704   -72.734     0   |HATTERAS - 150 NM North...
    """
    try:
        raw = _get(_STATION_TABLE_URL, timeout=20)
    except RuntimeError as exc:
        _warn(f"could not fetch station table — lat/lng will be NULL: {exc}")
        return {}

    target = {s.upper() for s in station_ids}
    meta: dict[str, dict] = {}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        sid = parts[0].upper()
        if sid not in target:
            continue
        try:
            lat = float(parts[1])
            lng = float(parts[2])
        except ValueError:
            lat = lng = None
        # Name: everything after the 4th column, strip leading "|"
        name_parts = parts[4:] if len(parts) > 4 else [sid]
        name = " ".join(name_parts).lstrip("|").strip() or sid
        meta[sid] = {"lat": lat, "lng": lng, "name": name}

    missing = target - set(meta.keys())
    if missing:
        _warn(f"station metadata not found for: {missing} — lat/lng will be NULL")
    return meta

# ── Realtime2 text file parser ────────────────────────────────────────────────

def _fval(row: dict, key: str) -> float | None:
    """Extract a float from a realtime2 row, returning None for 'MM' / sentinel values."""
    v = row.get(key, "MM")
    if v in ("MM", "99", "999", "9999", "99.0", "999.0", "9999.0"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _realtime_to_signals(
    raw: str,
    station_id: str,
    station_meta: dict,
) -> list[dict]:
    """
    Parse NDBC realtime2 standard meteorological text file.

    File layout:
      Line 1  (#YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP ...)
      Line 2  (#yr mo dy hr mn degT m/s  m/s  m   sec  sec degT hPa degC degC degC ...)
      Line 3+ data rows
    """
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 3:
        return []

    # Strip '#' from header line and parse column names
    header = lines[0].lstrip("#").split()

    smeta  = station_meta.get(station_id.upper(), {})
    lat    = smeta.get("lat")
    lng    = smeta.get("lng")
    sname  = smeta.get("name", station_id.upper())
    signals: list[dict] = []

    for line in lines[2:]:          # skip header and units rows
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < len(header):
            continue

        row = dict(zip(header, parts))

        # Timestamp
        try:
            yr  = int(row.get("YY", "0"))
            mon = int(row.get("MM", "0"))
            day = int(row.get("DD", "0"))
            hr  = int(row.get("hh", "0"))
            mn  = int(row.get("mm", "0"))
            ts  = datetime(yr, mon, day, hr, mn, tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue

        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        ext_id = f"ndbc:{station_id.lower()}:{ts.strftime('%Y%m%dT%H%MZ')}"

        # Observations
        wvht = _fval(row, "WVHT")
        wspd = _fval(row, "WSPD")
        wdir = _fval(row, "WDIR")
        gst  = _fval(row, "GST")
        dpd  = _fval(row, "DPD")
        pres = _fval(row, "PRES")
        atmp = _fval(row, "ATMP")
        wtmp = _fval(row, "WTMP")

        is_priority = int(
            (wvht is not None and wvht >= _PRIORITY_WAVE_M) or
            (wspd is not None and wspd >= _PRIORITY_WIND_MS)
        )

        # Title: most useful observations in one line
        title_parts: list[str] = []
        if wvht  is not None: title_parts.append(f"Waves {wvht:.1f} m")
        if wspd  is not None:
            wdir_s = f" {wdir:.0f}°" if wdir is not None else ""
            title_parts.append(f"Wind {wspd:.1f} m/s{wdir_s}")
        if wtmp  is not None: title_parts.append(f"SST {wtmp:.1f}°C")
        if pres  is not None: title_parts.append(f"Pres {pres:.0f} hPa")
        summary = " • ".join(title_parts) if title_parts else "No data"
        title   = f"NDBC {station_id.upper()} ({sname}) — {summary}"

        # Content: prose observation block
        obs: list[str] = [
            f"Station {station_id.upper()} ({sname}), observed {ts_str} UTC."
        ]
        for label, val, unit in [
            ("Significant wave height", wvht, "m"),
            ("Dominant wave period",    dpd,  "s"),
            ("Wind speed",              wspd, "m/s"),
            ("Wind direction",          wdir, "°"),
            ("Peak gust",               gst,  "m/s"),
            ("Sea-level pressure",      pres, "hPa"),
            ("Air temperature",         atmp, "°C"),
            ("Sea surface temperature", wtmp, "°C"),
        ]:
            if val is not None:
                obs.append(f"{label}: {val:.1f} {unit}.")
        content = " ".join(obs)

        signals.append({
            "signal_id":     str(uuid.uuid4()),
            "source":        "ndbc",
            "external_id":   ext_id,
            "title":         title,
            "content":       content,
            "lat":           lat,
            "lng":           lng,
            "timestamp":     ts_str,
            "status":        "raw",
            "is_priority":   is_priority,
            "metadata_json": json.dumps({
                "station_id":   station_id.upper(),
                "station_name": sname,
                "observed_utc": ts_str,
                "wvht_m":  wvht, "wspd_ms": wspd, "wdir_deg": wdir,
                "gst_ms":  gst,  "dpd_s":   dpd,
                "pres_hpa":pres, "atmp_c":  atmp, "wtmp_c": wtmp,
                "is_storm": bool(is_priority),
            }, default=str),
        })

    return signals

# ── RSS feed parser ───────────────────────────────────────────────────────────

_WVHT_RE = re.compile(r"wave\s+height[:\s]+([\d.]+)\s*m", re.I)
_WSPD_RE = re.compile(r"wind\s+speed[:\s]+([\d.]+)\s*m/s", re.I)


def _rss_to_signals(
    raw: str,
    station_id: str,
    station_meta: dict,
) -> list[dict]:
    """Parse NDBC RSS 2.0 feed — typically the last 5 observations."""
    smeta  = station_meta.get(station_id.upper(), {})
    lat    = smeta.get("lat")
    lng    = smeta.get("lng")
    sname  = smeta.get("name", station_id.upper())
    signals: list[dict] = []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        _warn(f"RSS parse error for {station_id}: {exc}")
        return signals

    for item in root.iter("item"):
        title_el   = item.find("title")
        desc_el    = item.find("description")
        pubdate_el = item.find("pubDate")
        link_el    = item.find("link")

        if title_el is None:
            continue

        title_text = (title_el.text  or "").strip()
        desc_text  = (desc_el.text   or "").strip() if desc_el  is not None else ""
        pub_raw    = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
        link_text  = (link_el.text   or "").strip() if link_el  is not None else ""

        # Timestamp
        ts_str = ""
        try:
            ts_dt  = parsedate_to_datetime(pub_raw).astimezone(timezone.utc)
            ts_str = ts_dt.strftime("%Y-%m-%d %H:%M:%S")
            ext_id = f"ndbc:{station_id.lower()}:{ts_dt.strftime('%Y%m%dT%H%MZ')}"
        except Exception:
            # Fallback: hash-based unique ID
            ext_id = f"ndbc:{station_id.lower()}:rss:{abs(hash(title_text + pub_raw))}"
            ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Priority: scan description text for storm thresholds
        is_priority = 0
        m_wvht = _WVHT_RE.search(desc_text)
        m_wspd = _WSPD_RE.search(desc_text)
        if m_wvht and float(m_wvht.group(1)) >= _PRIORITY_WAVE_M:
            is_priority = 1
        if m_wspd and float(m_wspd.group(1)) >= _PRIORITY_WIND_MS:
            is_priority = 1

        signals.append({
            "signal_id":     str(uuid.uuid4()),
            "source":        "ndbc",
            "external_id":   ext_id,
            "title":         f"NDBC {station_id.upper()} ({sname}) — {title_text}",
            "content":       desc_text,
            "lat":           lat,
            "lng":           lng,
            "timestamp":     ts_str,
            "status":        "raw",
            "is_priority":   is_priority,
            "metadata_json": json.dumps({
                "station_id":   station_id.upper(),
                "station_name": sname,
                "pub_date":     pub_raw,
                "link":         link_text,
                "mode":         "rss",
            }, default=str),
        })

    return signals

# ── Collection logic ──────────────────────────────────────────────────────────

def collect(
    db_path:     Path            = _DB_PATH,
    station_ids: list[str] | None = None,
    force_backfill: bool          = False,
) -> dict:
    """
    For each station:
      - First run (or force_backfill=True): fetch realtime2 (up to 45 days)
      - Subsequent runs: fetch RSS (last ~5 observations)
    """
    if station_ids is None:
        env_val = os.environ.get("NDBC_STATIONS", "").strip()
        station_ids = [s.strip() for s in env_val.split(",") if s.strip()]
    if not station_ids:
        station_ids = list(_DEFAULT_STATIONS)

    if not station_ids:
        _warn(
            "No stations configured. Set NDBC_STATIONS env var.\n"
            "  Example: export NDBC_STATIONS='41049,13008'\n"
            "  Browse: https://www.ndbc.noaa.gov/obs.shtml"
        )
        return {"status": "skipped", "reason": "no_stations_configured"}

    _log(f"Stations: {station_ids}")

    # Station metadata — lat / lng / name
    _log("Fetching station metadata from NDBC station table...")
    station_meta = _load_station_meta(station_ids)

    conn = _open_db(db_path)
    total_inserted = total_skipped = 0
    station_results: dict[str, dict] = {}

    try:
        for sid in station_ids:
            sid = sid.strip().upper()
            _log(f"--- Station {sid} ---")

            first_run = force_backfill or not _station_has_data(conn, sid)
            mode = "backfill" if first_run else "rss"
            signals: list[dict] = []

            if first_run:
                _log(f"{sid}: first run — fetching realtime2 (up to 45 days)...")
                try:
                    raw = _get(_REALTIME_URL.format(sid=sid.lower()))
                    signals = _realtime_to_signals(raw, sid, station_meta)
                    _log(f"{sid}: realtime2 parsed {len(signals)} observations")
                except RuntimeError as exc:
                    _err(f"{sid}: realtime2 fetch failed: {exc}")
            else:
                _log(f"{sid}: fetching RSS...")
                try:
                    raw = _get(_RSS_URL.format(sid=sid.lower()))
                    signals = _rss_to_signals(raw, sid, station_meta)
                    _log(f"{sid}: RSS parsed {len(signals)} observations")
                except RuntimeError as exc:
                    _err(f"{sid}: RSS fetch failed: {exc}")

            if signals:
                ins, skp = _insert_signals(conn, signals)
                _log(f"{sid}: +{ins} new, {skp} duplicate(s) skipped")
            else:
                ins = skp = 0
                _log(f"{sid}: no observations to insert")

            total_inserted += ins
            total_skipped  += skp
            station_results[sid] = {
                "mode": mode, "parsed": len(signals),
                "inserted": ins, "skipped": skp,
            }
    finally:
        conn.close()

    _log(f"Complete — {total_inserted} new signals, {total_skipped} duplicates skipped")
    return {
        "status":   "success",
        "inserted": total_inserted,
        "skipped":  total_skipped,
        "stations": station_results,
    }

# ── Async adapter for mega_ingest ─────────────────────────────────────────────

async def async_main(**kwargs) -> None:
    _t0 = time.monotonic()
    try:
        result = collect()
        if _asyncio.iscoroutine(result):
            await result
        log_run(
            _DB_PATH, "ndbc_collector", "success",
            records_in=result.get("inserted", 0) + result.get("skipped", 0),
            records_out=result.get("inserted", 0),
            duration_s=time.monotonic() - _t0,
        )
    except Exception as exc:
        _err(f"async_main: {exc}")
        log_run(_DB_PATH, "ndbc_collector", "error", detail={"error": str(exc)})

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FORGE NDBC Buoy Collector")
    ap.add_argument(
        "--stations", "-s", metavar="IDs",
        help="Comma-separated NDBC station IDs. Overrides NDBC_STATIONS env var.",
    )
    ap.add_argument(
        "--backfill", action="store_true",
        help="Force realtime2 backfill even if station already has signals.",
    )
    ap.add_argument(
        "--list-meta", action="store_true",
        help="Print station metadata (lat/lng/name) for configured stations and exit.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and display station data without writing to database.",
    )
    args = ap.parse_args()

    ids: list[str] | None = None
    if args.stations:
        ids = [s.strip() for s in args.stations.split(",") if s.strip()]

    if args.list_meta:
        check_ids = ids or [s.strip() for s in os.environ.get("NDBC_STATIONS","").split(",") if s.strip()]
        if not check_ids:
            print("No stations specified. Use --stations or NDBC_STATIONS env var.")
            sys.exit(1)
        meta = _load_station_meta(check_ids)
        for sid, info in meta.items():
            print(f"{sid:10s}  lat={info['lat']:8.3f}  lng={info['lng']:9.3f}  {info['name']}")
        for sid in check_ids:
            if sid.upper() not in meta:
                print(f"{sid.upper():10s}  (not found in station table)")
        sys.exit(0)

    if args.dry_run:
        check_ids = ids or [s.strip() for s in os.environ.get("NDBC_STATIONS", "").split(",") if s.strip()] or list(_DEFAULT_STATIONS)
        print(f"[ndbc] DRY RUN — would collect from stations: {check_ids}")
        meta = _load_station_meta(check_ids)
        print(f"[ndbc] Station metadata resolved: {len(meta)} stations")
        for sid, info in meta.items():
            print(f"  {sid:10s}  lat={info['lat']:8.3f}  lng={info['lng']:9.3f}  {info['name']}")
        print("[ndbc] Dry run complete (no writes)")
        sys.exit(0)

    result = collect(station_ids=ids, force_backfill=args.backfill)
    sys.exit(0 if result.get("status") != "error" else 1)
