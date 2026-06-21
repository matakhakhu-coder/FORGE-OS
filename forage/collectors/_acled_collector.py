#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE — ACLED Collector  (forage/collectors/acled_collector.py)
═══════════════════════════════════════════════════════════════

Polls the Armed Conflict Location & Event Data (ACLED) API for recent
conflict, protest, and violence events in South Africa and neighbouring
countries. Maps structured ACLED event data directly to FORGE signal
fields — providing the highest-quality gravity inputs of any collector:

  severity         ← normalised fatality count  (0.0–1.0)
  actor_importance ← ACLED inter_code / actor type weight
  sentiment        ← negative for violence events (−1.0–0.0)
  source_credibility ← ACLED is verified/sourced: 0.85 fixed
  stream           ← CRIME_INTEL for violence; INFRASTRUCTURE for
                     riots/protests affecting services

Actor names from ACLED are passed through to the signal metadata so
EntityResolver can pick them up during ingest.

ACLED retired API-key auth in favour of OAuth2 (myACLED accounts).
Reference: https://acleddata.com/api-documentation/getting-started
Free tier: 10,000 rows/month — sufficient for daily polling at
~200 events/day for South Africa + neighbours.

Environment variables
─────────────────────
  ACLED_EMAIL      myACLED account email (register free at
                    https://acleddata.com/user/re-activate)
  ACLED_PASSWORD   myACLED account password
  FORGE_DB         Path to FORGE database (default: auto-detect)

Auth flow
─────────
  POST https://acleddata.com/oauth/token  (form-encoded: username, password,
  grant_type=password, client_id=acled, scope=authenticated)
  → {"access_token", "refresh_token", "expires_in": 86400 (24h)}
  Tokens are cached to .acled_token_cache.json (gitignored, next to this
  file) and refreshed automatically (refresh_token valid 14 days).

Usage
─────
  python forage/collectors/acled_collector.py
  python forage/collectors/acled_collector.py --days 7 --dry-run
  python forage/collectors/acled_collector.py --country "South Africa" --days 3
"""

__manifest__ = {
    "id":          "acled_collector",
    "name":        "ACLED Conflict Collector",
    "description": "Polls the ACLED API for conflict, protest, and violence events in South Africa. Requires ACLED_EMAIL and ACLED_PASSWORD env vars (myACLED account).",
    "icon":        "⚔",
    "entry":       "forage/collectors/acled_collector.py",
    "args":        [],
    "job_key":     "acled_collector",
    "version":     "2.0.0",
}

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

log = logging.getLogger("forge.collectors.acled")

# ── Configuration ─────────────────────────────────────────────────────────────

OAUTH_TOKEN_URL = "https://acleddata.com/oauth/token"
BASE_URL        = "https://acleddata.com/api/acled/read"
OAUTH_CLIENT_ID = "acled"

# Token cache file — never committed (holds a live refresh token)
TOKEN_CACHE_PATH = Path(__file__).resolve().parent / ".acled_token_cache.json"

# Refresh proactively this many seconds before actual expiry
TOKEN_REFRESH_MARGIN = 300

# Countries to collect. ACLED uses full names.
# Extend this list as needed — each country adds ~10–40 events/day.
DEFAULT_COUNTRIES = [
    "South Africa",
    "Zimbabwe",
    "Mozambique",
    "Eswatini",
    "Lesotho",
]

# ACLED event types and their gravity mappings
# event_type → (stream, base_severity, base_sentiment)
EVENT_TYPE_MAP: dict[str, tuple[str, float, float]] = {
    "Battles":                      ("CRIME_INTEL",    0.85, -0.9),
    "Violence against civilians":   ("CRIME_INTEL",    0.90, -1.0),
    "Explosions/Remote violence":   ("CRIME_INTEL",    0.80, -0.9),
    "Riots":                        ("CRIME_INTEL",    0.65, -0.7),
    "Protests":                     ("GLOBAL",         0.35, -0.3),
    "Strategic developments":       ("PRIORITY",       0.45, -0.4),
}

# ACLED sub-event types that should trigger is_priority=1
PRIORITY_SUB_EVENTS = {
    "Armed clash",
    "Attack",
    "Suicide bomb",
    "Air/drone strike",
    "Chemical weapon",
    "Looting/property destruction",
    "Mob violence",
    "Grenade",
    "Shelling/artillery/missile attack",
}

# Normalisation cap for fatalities → severity
# Events with >= CAP_FATALITIES get severity = 1.0
FATALITY_CAP = 50

# Maximum events per API page
PAGE_LIMIT = 500

# How far back to look on first run (days)
DEFAULT_LOOKBACK_DAYS = 3

# Fixed credibility for ACLED (peer-reviewed, sourced dataset)
SOURCE_CREDIBILITY = 0.85

# ── DB helpers ────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2] / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── Stream + gravity field mapping ───────────────────────────────────────────

def _classify_stream(event_type: str) -> str:
    return EVENT_TYPE_MAP.get(event_type, ("GLOBAL", 0.3, -0.2))[0]


def _base_severity(event_type: str) -> float:
    return EVENT_TYPE_MAP.get(event_type, ("GLOBAL", 0.3, -0.2))[1]


def _base_sentiment(event_type: str) -> float:
    return EVENT_TYPE_MAP.get(event_type, ("GLOBAL", 0.3, -0.2))[2]


def _fatality_severity(fatalities: int, base: float) -> float:
    """
    Blend base event-type severity with a fatality-scaled component.
    severity = 0.5 * base + 0.5 * min(fatalities / CAP, 1.0)
    This ensures zero-fatality protests don't get inflated scores
    while high-fatality events correctly saturate toward 1.0.
    """
    fatality_component = min(fatalities / FATALITY_CAP, 1.0)
    return round(0.5 * base + 0.5 * fatality_component, 4)


def _actor_importance(actor1_type: str, actor2_type: str) -> float:
    """
    Map ACLED actor type strings to importance scores.
    ACLED actor types: State Forces, Rebel Groups, Political Militias,
    Identity Militias, Rioters, Protesters, Civilians, External/Other.
    """
    type_weights = {
        "state forces":         0.90,
        "rebel groups":         0.85,
        "political militias":   0.80,
        "identity militias":    0.75,
        "external/other forces":0.70,
        "rioters":              0.55,
        "protesters":           0.30,
        "civilians":            0.20,
    }
    a1 = actor1_type.lower() if actor1_type else ""
    a2 = actor2_type.lower() if actor2_type else ""
    w1 = max((v for k, v in type_weights.items() if k in a1), default=0.40)
    w2 = max((v for k, v in type_weights.items() if k in a2), default=0.40)
    return round(max(w1, w2), 4)


# ── OAuth2 authentication ────────────────────────────────────────────────────

class ACLEDAuth:
    """
    Handles ACLED's OAuth2 password-grant flow with on-disk token caching.

    Access tokens last 24h, refresh tokens 14 days. We cache both plus an
    absolute expiry timestamp so repeated runs (e.g. daily mega_ingest)
    don't need to re-authenticate with the password every time.
    """

    def __init__(self, email: str, password: str, cache_path: Path = TOKEN_CACHE_PATH):
        self.email      = email
        self.password   = password
        self.cache_path = cache_path

    def _post_form(self, data: dict[str, str]) -> dict[str, Any]:
        body = urlencode(data).encode("utf-8")
        req = Request(
            OAUTH_TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent":   "FORGE-OSINT/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ACLED OAuth HTTP {e.code}: {detail}") from e
        except URLError as e:
            raise RuntimeError(f"ACLED OAuth network error: {e.reason}") from e

    def _load_cache(self) -> Optional[dict]:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_cache(self, token: dict) -> None:
        expires_at = time.time() + float(token.get("expires_in", 86400))
        cache = {
            "access_token":  token["access_token"],
            "refresh_token": token.get("refresh_token"),
            "expires_at":    expires_at,
        }
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except OSError as exc:
            log.debug(f"ACLED token cache write failed (non-fatal): {exc}")

    def _login(self) -> dict:
        log.info("[acled_collector] Authenticating via OAuth password grant")
        token = self._post_form({
            "username":   self.email,
            "password":   self.password,
            "grant_type": "password",
            "client_id":  OAUTH_CLIENT_ID,
            "scope":      "authenticated",
        })
        self._save_cache(token)
        return token

    def _refresh(self, refresh_token: str) -> dict:
        log.info("[acled_collector] Refreshing OAuth access token")
        token = self._post_form({
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
            "client_id":     OAUTH_CLIENT_ID,
        })
        # Some OAuth servers omit refresh_token on refresh — carry the old one forward
        token.setdefault("refresh_token", refresh_token)
        self._save_cache(token)
        return token

    def get_access_token(self) -> str:
        cache = self._load_cache()
        now = time.time()

        if cache and cache.get("expires_at", 0) - TOKEN_REFRESH_MARGIN > now:
            return cache["access_token"]

        if cache and cache.get("refresh_token"):
            try:
                token = self._refresh(cache["refresh_token"])
                return token["access_token"]
            except RuntimeError as exc:
                log.warning(f"[acled_collector] Refresh failed, re-authenticating: {exc}")

        token = self._login()
        return token["access_token"]


# ── ACLED API client ──────────────────────────────────────────────────────────

class ACLEDClient:
    """
    Minimal synchronous ACLED REST client using OAuth2 Bearer auth.
    Wrapped in async-compatible run_in_executor calls by the collector.
    """

    def __init__(self, auth: ACLEDAuth):
        self.auth = auth

    def fetch_events(
        self,
        country: str,
        since_date: str,   # "YYYY-MM-DD"
        page: int = 1,
        limit: int = PAGE_LIMIT,
    ) -> dict[str, Any]:
        """
        Fetch one page of ACLED events for a country since a given date.

        ACLED required fields we request:
          event_id_cnty, event_date, event_type, sub_event_type,
          actor1, assoc_actor_1, actor2, assoc_actor_2,
          inter1, inter2,
          country, admin1, admin2, location,
          latitude, longitude,
          fatalities, notes, source, source_scale
        """
        params = {
            "_format":     "json",
            "country":     country,
            "event_date":  since_date,
            "event_date_where": ">=",
            "fields":      (
                "event_id_cnty|event_date|event_type|sub_event_type"
                "|actor1|assoc_actor_1|inter1"
                "|actor2|assoc_actor_2|inter2"
                "|country|admin1|admin2|location"
                "|latitude|longitude"
                "|fatalities|notes|source|source_scale"
            ),
            "limit":       str(limit),
            "page":        str(page),
        }
        url = f"{BASE_URL}?{urlencode(params)}"
        log.debug(f"ACLED fetch: {url}")

        req = Request(url, headers={
            "User-Agent":    "FORGE-OSINT/1.0",
            "Authorization": f"Bearer {self.auth.get_access_token()}",
            "Content-Type":  "application/json",
        })
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw)
        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ACLED HTTP {e.code}: {e.reason} | {detail[:500]}") from e
        except URLError as e:
            raise RuntimeError(f"ACLED network error: {e.reason}") from e

    def fetch_all(
        self,
        country: str,
        since_date: str,
    ) -> list[dict]:
        """
        Paginate through all results for a country + date range.
        ACLED returns {"status": 200, "count": N, "data": [...]}
        """
        all_events: list[dict] = []
        page = 1
        while True:
            response = self.fetch_events(country, since_date, page=page)
            if response.get("status") != 200:
                err = response.get("error", "unknown error")
                raise RuntimeError(f"ACLED API error: {err}")
            data = response.get("data", [])
            if not data:
                break
            all_events.extend(data)
            log.info(
                f"  [{country}] page {page}: {len(data)} events "
                f"(total so far: {len(all_events)})"
            )
            # If we got a full page, there may be more
            if len(data) < PAGE_LIMIT:
                break
            page += 1
            time.sleep(0.5)   # polite rate limiting
        return all_events


# ── Signal builder ────────────────────────────────────────────────────────────

def _build_signal(event: dict) -> dict:
    """
    Map one ACLED event dict to a FORGE signals row.

    Key gravity field mappings
    ──────────────────────────
    severity         = blend(event_type base, fatality normalised)
    actor_importance = max(inter1 weight, inter2 weight)
    sentiment        = negative for violence events
    source_credibility = 0.85 (ACLED is peer-reviewed)
    frequency        = left to ingest.py to compute from DB history
    """
    event_type   = event.get("event_type", "Strategic developments")
    sub_event    = event.get("sub_event_type", "")
    fatalities   = int(event.get("fatalities") or 0)
    actor1       = event.get("actor1", "")
    actor2       = event.get("actor2", "")
    inter1       = event.get("inter1", "")
    inter2       = event.get("inter2", "")
    location     = event.get("location", "")
    admin1       = event.get("admin1", "")
    country      = event.get("country", "")
    notes        = (event.get("notes") or "")[:1000]
    source       = event.get("source", "ACLED")
    source_scale = event.get("source_scale", "")

    # Unique stable ID from ACLED's own event identifier
    acled_id  = event.get("event_id_cnty", "")
    ext_id    = f"acled:{acled_id}"
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ext_id))

    # Coordinates
    try:
        lat = float(event.get("latitude") or 0) or None
        lng = float(event.get("longitude") or 0) or None
    except (TypeError, ValueError):
        lat, lng = None, None

    # Timestamp (ACLED provides "YYYY-MM-DD")
    raw_date = event.get("event_date", "")
    try:
        timestamp = (
            datetime.strptime(raw_date, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except ValueError:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Gravity inputs
    base_sev    = _base_severity(event_type)
    severity    = _fatality_severity(fatalities, base_sev)
    actor_imp   = _actor_importance(inter1, inter2)
    sentiment   = _base_sentiment(event_type)
    stream      = _classify_stream(event_type)
    is_priority = 1 if sub_event in PRIORITY_SUB_EVENTS or fatalities >= 5 else 0

    # Human-readable title
    location_str = ", ".join(filter(None, [location, admin1, country]))
    actor_str    = actor1
    if actor2:
        actor_str += f" vs {actor2}"
    title = f"{event_type}: {actor_str} — {location_str}"
    if fatalities:
        title += f" ({fatalities} fatalities)"
    title = title[:200]

    # Content: notes field from ACLED (eyewitness/journalist sourced)
    content = notes or f"{sub_event} event recorded by ACLED. Source: {source} ({source_scale})."

    # Full metadata for downstream processors and EntityResolver
    metadata = {
        "acled_id":      acled_id,
        "event_type":    event_type,
        "sub_event":     sub_event,
        "actor1":        actor1,
        "actor2":        actor2,
        "inter1":        inter1,
        "inter2":        inter2,
        "fatalities":    fatalities,
        "admin1":        admin1,
        "country":       country,
        "source":        source,
        "source_scale":  source_scale,
        # Pre-computed gravity inputs (available to ingest.py without re-parsing)
        "severity":           severity,
        "actor_importance":   actor_imp,
        "sentiment":          sentiment,
        "source_credibility": SOURCE_CREDIBILITY,
    }

    return {
        "signal_id":        signal_id,
        "source":           "acled",
        "external_id":      ext_id,
        "title":            title,
        "content":          content,
        "lat":              lat,
        "lng":              lng,
        "timestamp":        timestamp,
        "status":           "raw",
        "is_priority":      is_priority,
        "stream":           stream,
        "source_type":      "live",
        "relevance_score":  round(0.4 + severity * 0.6, 4),   # high baseline
        "metadata_json":    json.dumps(metadata, ensure_ascii=False),
        # Gravity inputs stored at top level for ingest.py score_signal()
        "severity":           severity,
        "actor_importance":   actor_imp,
        "sentiment":          sentiment,
        "source_credibility": SOURCE_CREDIBILITY,
        "frequency":          0.0,   # populated by ingest.py history check
    }


# ── DB write ──────────────────────────────────────────────────────────────────

def _insert_signals(conn: sqlite3.Connection, signals: list[dict]) -> tuple[int, int]:
    """
    Bulk insert signals. Skips duplicates via external_id UNIQUE constraint.
    Returns (inserted, skipped).
    """
    inserted = 0
    skipped  = 0

    for sig in signals:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                    (signal_id, source, external_id, title, content,
                     lat, lng, timestamp, status, is_priority,
                     stream, source_type, relevance_score, metadata_json)
                VALUES
                    (:signal_id, :source, :external_id, :title, :content,
                     :lat, :lng, :timestamp, :status, :is_priority,
                     :stream, :source_type, :relevance_score, :metadata_json)
            """, sig)
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            log.warning(f"Insert error for {sig.get('external_id')}: {e}")
            skipped += 1

    conn.commit()
    return inserted, skipped


# ── Pipeline run logger ───────────────────────────────────────────────────────

def _log_run(
    db_path: Path,
    status: str,
    records_in: int,
    records_out: int,
    duration_s: float,
    detail: dict,
) -> None:
    """Write a pipeline_runs entry. Non-fatal on any error."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("""
            INSERT INTO pipeline_runs
                (component, status, records_in, records_out, duration_s, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "acled_collector",
            status,
            records_in,
            records_out,
            round(duration_s, 2),
            json.dumps(detail, ensure_ascii=False),
        ))
        conn.commit()
        conn.close()
    except Exception as exc:
        log.debug(f"pipeline_runs log failed (non-fatal): {exc}")


# ── Main collector ────────────────────────────────────────────────────────────

class ACLEDCollector:

    def __init__(
        self,
        db_path: Optional[Path] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        countries: Optional[list[str]] = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ):
        self.db_path      = db_path or _resolve_db()
        self.email        = email    or os.environ.get("ACLED_EMAIL", "")
        self.password     = password or os.environ.get("ACLED_PASSWORD", "")
        self.countries    = countries or DEFAULT_COUNTRIES
        self.lookback_days = lookback_days

        if not self.email:
            raise ValueError(
                "ACLED account email required. Set ACLED_EMAIL environment variable. "
                "Register free at https://acleddata.com/user/re-activate"
            )
        if not self.password:
            raise ValueError(
                "ACLED account password required. Set ACLED_PASSWORD environment variable."
            )

    def _since_date(self) -> str:
        """Return ISO date string for lookback window."""
        d = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        return d.strftime("%Y-%m-%d")

    def run(self, dry_run: bool = False) -> dict:
        """
        Full collection pass: fetch → map → deduplicate → insert.
        Suitable for both direct CLI use and asyncio.run() via mega_ingest.
        """
        start     = time.monotonic()
        auth      = ACLEDAuth(self.email, self.password)
        client    = ACLEDClient(auth)
        since     = self._since_date()
        all_sigs  : list[dict] = []
        errors    : list[str]  = []

        log.info(
            f"[acled_collector] Starting collection: "
            f"{len(self.countries)} countries, since={since}, dry_run={dry_run}"
        )

        for country in self.countries:
            try:
                log.info(f"[acled_collector] Fetching: {country}")
                events = client.fetch_all(country, since_date=since)
                for evt in events:
                    all_sigs.append(_build_signal(evt))
                log.info(
                    f"[acled_collector] {country}: {len(events)} events fetched"
                )
            except Exception as exc:
                msg = f"{country}: {exc}"
                log.warning(f"[acled_collector] WARN {msg}")
                errors.append(msg)

        total_fetched = len(all_sigs)
        log.info(
            f"[acled_collector] Total signals built: {total_fetched} "
            f"across {len(self.countries)} countries"
        )

        inserted = 0
        skipped  = 0

        if dry_run:
            log.info(
                f"[acled_collector] DRY RUN — {total_fetched} signals "
                f"not written to DB"
            )
            for s in all_sigs[:5]:
                log.info(
                    f"  SAMPLE: [{s['stream']}] prio={s['is_priority']} "
                    f"sev={s['severity']:.3f} | {s['title'][:80]}"
                )
        else:
            conn = _open_db(self.db_path)
            try:
                inserted, skipped = _insert_signals(conn, all_sigs)
            finally:
                conn.close()
            log.info(
                f"[acled_collector] Written: {inserted} new, "
                f"{skipped} duplicates skipped"
            )

        duration = round(time.monotonic() - start, 2)
        result   = {
            "status":        "success" if not errors else "partial",
            "fetched":       total_fetched,
            "inserted":      inserted,
            "skipped":       skipped,
            "errors":        errors,
            "countries":     self.countries,
            "since":         since,
            "dry_run":       dry_run,
            "computed_at":   datetime.now(timezone.utc).isoformat(),
            "duration_s":    duration,
        }

        if not dry_run:
            _log_run(
                self.db_path,
                status="success" if not errors else "error",
                records_in=total_fetched,
                records_out=inserted,
                duration_s=duration,
                detail=result,
            )

        return result


# ── Async wrapper (for mega_ingest compatibility) ─────────────────────────────

async def collect(
    db_path: Optional[Path] = None,
    email: Optional[str] = None,
    password: Optional[str] = None,
    countries: Optional[list[str]] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """
    Async entry point called by mega_ingest.run_all_collectors().
    Runs the synchronous ACLEDCollector in a thread pool executor
    so it doesn't block the event loop.
    """
    loop = asyncio.get_event_loop()
    collector = ACLEDCollector(
        db_path=db_path,
        email=email,
        password=password,
        countries=countries,
        lookback_days=lookback_days,
    )
    return await loop.run_in_executor(None, collector.run)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="FORGE ACLED Collector — fetch conflict events from ACLED API"
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"Days to look back (default: {DEFAULT_LOOKBACK_DAYS})"
    )
    parser.add_argument(
        "--country", type=str, default=None,
        help="Single country override (default: all DEFAULT_COUNTRIES)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and map events but do not write to DB"
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Path to database.db (default: auto-detect)"
    )
    parser.add_argument(
        "--email", type=str, default=None,
        help="ACLED account email (default: ACLED_EMAIL env var)"
    )
    parser.add_argument(
        "--password", type=str, default=None,
        help="ACLED account password (default: ACLED_PASSWORD env var)"
    )
    args = parser.parse_args()

    countries = [args.country] if args.country else DEFAULT_COUNTRIES

    try:
        collector = ACLEDCollector(
            db_path=_resolve_db(args.db),
            email=args.email,
            password=args.password,
            countries=countries,
            lookback_days=args.days,
        )
        result = collector.run(dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0 if result["status"] in ("success", "partial") else 1)

    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        log.exception(f"Unhandled error: {exc}")
        sys.exit(1)
