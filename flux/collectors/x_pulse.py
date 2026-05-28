#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE FLUX — X-Pulse Collector  (flux/collectors/x_pulse.py)
═════════════════════════════════════════════════════════════
Dual-mode collector for X (Twitter) social intelligence signals.

Modes
─────
  nitter    (default) — Fetches public RSS feeds from rotating Nitter
                        instances. Zero authentication required.
                        Parses with xml.etree.ElementTree (stdlib).
                        Resilient: rotates through NITTER_INSTANCES on
                        connection failure or HTTP error.

  guest_api (fallback) — Calls the X v2 search API using a bearer token.
                         Requires X_BEARER_TOKEN env var.
                         Falls back to the undocumented guest token flow
                         if the official bearer returns 401.

Dependencies
────────────
  requests         — HTTP client (already in FORGE stack)
  xml.etree        — RSS parsing (stdlib)
  email.utils      — RFC 2822 date parsing (stdlib)

Environment variables
─────────────────────
  X_PULSE_MODE       "nitter" | "guest_api"  (default: "nitter")
  X_BEARER_TOKEN     Required for guest_api mode
  X_PULSE_TARGETS    Comma-separated targets: @handle,#hashtag,$CASHTAG
                     Default: see DEFAULT_TARGETS below
  FORGE_DB           Override database path

Signal emission
───────────────
  Every collected tweet is written to TWO tables:
    signals        — standard FORGE signal store (global pipeline visibility)
    socint_signals — FLUX-specific store with actor FK and richer metadata

  Deduplication: INSERT OR IGNORE on signals.external_id (tweet URL).
  The socint_signals row is only written after the signals row succeeds,
  using the returned signal_id as the FK.

Usage
─────
  python flux/collectors/x_pulse.py
  python flux/collectors/x_pulse.py --dry-run
  python flux/collectors/x_pulse.py --mode nitter
  python flux/collectors/x_pulse.py --mode guest_api
  python flux/collectors/x_pulse.py --targets "@SARS_ZA,#ZAR,$JSE"
"""

__manifest__ = {
    "id":          "x_pulse",
    "name":        "X Pulse Collector",
    "description": (
        "Dual-mode SOCINT collector for X (Twitter). Ingests tweets from "
        "tracked handles, hashtags, and cashtags via Nitter RSS (default) "
        "or X guest API. Feeds the FLUX stylometric pipeline."
    ),
    "icon":        "X",
    "entry":       "flux/collectors/x_pulse.py",
    "args":        ["--mode", "--targets", "--dry-run"],
    "job_key":     "x_pulse",
    "version":     "0.1.0",
}

import argparse
import hashlib
import json
import logging
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
from typing import Optional
from urllib.parse import quote_plus

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("[x_pulse] FATAL: 'requests' library not installed.", file=sys.stderr)
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = Path(os.environ.get("FORGE_DB", str(BASE_DIR / "database.db")))

try:
    from forage.utils.pipeline_logger import log_run as _log_run
except ImportError:
    def _log_run(*a, **kw): pass  # type: ignore[misc]

try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t): return t  # type: ignore[misc]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [x_pulse] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("forge.flux.x_pulse")

# ── Configuration ─────────────────────────────────────────────────────────────

X_PULSE_MODE    = os.environ.get("X_PULSE_MODE",   "nitter").strip().lower()
X_BEARER_TOKEN  = os.environ.get("X_BEARER_TOKEN", "").strip()
X_PULSE_TARGETS = os.environ.get("X_PULSE_TARGETS", "").strip()

# ── Butterfly / Discovery mode ────────────────────────────────────────────────
# FLUX_DISCOVERY_MODE=true  → append top latent seeds from previous pulse
# FLUX_MAX_DISCOVERY_DEPTH  → max expansion depth (0=seeds, 1=disc-1, 2=disc-2)
# FLUX_DISCOVERY_TOP_N      → how many latent seeds to append per pulse
FLUX_DISCOVERY_MODE      = os.environ.get("FLUX_DISCOVERY_MODE", "").strip().lower() == "true"
FLUX_MAX_DISCOVERY_DEPTH = int(os.environ.get("FLUX_MAX_DISCOVERY_DEPTH", "2"))
FLUX_DISCOVERY_TOP_N     = int(os.environ.get("FLUX_DISCOVERY_TOP_N", "3"))

# Rotating Nitter instance pool — tried in order, failed instances skipped.
# Public instances fluctuate; keep this list current.
NITTER_INSTANCES: list[str] = [
    "nitter.net",            # verified live 2026-05-02 (user feeds ✓, hashtag RSS limited)
    "nitter.poast.org",
    "nitter.cz",
    "nitter.fdn.fr",
    # Add more from https://github.com/zedeus/nitter/wiki/Instances
]

# Default SA-SOCINT targets when X_PULSE_TARGETS is not set
DEFAULT_TARGETS: list[str] = [
    "@SARS_ZA",
    "@TreasuryRSA",
    "#ZAR",
    "#loadshedding",
    "$JSE",
    "$ZAR",
]

# X API endpoints
X_GUEST_ACTIVATE  = "https://api.twitter.com/1.1/guest/activate.json"
X_V2_SEARCH       = "https://api.twitter.com/2/tweets/search/recent"
X_V1_SEARCH       = "https://api.twitter.com/1.1/search/tweets.json"

# HTTP settings
REQUEST_TIMEOUT   = (4, 12)  # (connect_s, read_s) — fast fail on dead instances
INTER_REQUEST_GAP = 1.5   # seconds between Nitter RSS fetches (rate-limit courtesy)
MAX_ITEMS_PER_FEED = 40   # cap per RSS feed to prevent runaway inserts

# ── Regex — pre-extract metadata at collection time ──────────────────────────

_CASHTAG_RE = re.compile(r'\$[A-Z]{1,6}\b')
_HASHTAG_RE = re.compile(r'#\w+')
_EMOJI_RE   = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000027BF"
    "]",
    flags=re.UNICODE,
)
_HANDLE_FROM_TITLE_RE = re.compile(r'^(@\w+)\s*:\s*', re.UNICODE)


def _normalize_tag(tag: str) -> str:
    """Strip @ # $ prefix, lowercase — canonical form for co-occurrence keys."""
    return tag.lstrip("@#$").lower().strip()


# ─────────────────────────────────────────────────────────────────────────────
# HTTP session factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_session(bearer: str = "") -> requests.Session:
    """
    Build a requests.Session with retry logic and a FORGE User-Agent.
    bearer: if provided, sets Authorization header for X API calls.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=0,           # never retry DNS / connection failures — fail fast, try next instance
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({
        "User-Agent": "FORGE-FLUX/0.1 (+https://forge.local; SOCINT collector)",
        "Accept":     "application/rss+xml, application/xml, text/xml, */*",
    })
    if bearer:
        session.headers["Authorization"] = f"Bearer {bearer}"
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Tweet model
# ─────────────────────────────────────────────────────────────────────────────

class Tweet:
    """
    Normalised tweet container, source-agnostic.
    Populated by both Nitter and guest API parsers.
    """
    __slots__ = (
        "tweet_url", "handle", "display_name", "content",
        "published_at", "cashtags", "hashtags", "emojis", "seed",
    )

    def __init__(
        self,
        tweet_url:    str,
        handle:       str,
        display_name: str,
        content:      str,
        published_at: str,
        seed:         str = "",
    ) -> None:
        self.tweet_url    = tweet_url
        self.handle       = handle.lower().lstrip("@")
        self.display_name = display_name
        self.content      = content
        self.published_at = published_at
        self.seed         = seed   # which target triggered this tweet's collection
        # Pre-extract stylometric indicators at collection time
        upper             = content.upper()
        self.cashtags     = sorted(set(_CASHTAG_RE.findall(upper)))
        self.hashtags     = sorted(set(t.lower() for t in _HASHTAG_RE.findall(content)))
        self.emojis       = _EMOJI_RE.findall(content)

    @property
    def external_id(self) -> str:
        """Stable deduplication key — the canonical tweet URL."""
        return self.tweet_url

    @property
    def title(self) -> str:
        return f"@{self.handle}: {self.content[:120]}"

    def metadata(self) -> dict:
        # co_occurring_tags = all tags in this tweet that are NOT the seed itself
        seed_norm = _normalize_tag(self.seed) if self.seed else ""
        all_tags  = list(
            set(self.hashtags)
            | {_normalize_tag(t) for t in self.cashtags}
        )
        co_tags = [t for t in all_tags if t and t != seed_norm]
        return {
            "x_handle":          f"@{self.handle}",
            "x_display_name":    self.display_name,
            "x_collection_mode": X_PULSE_MODE,
            "cashtags":          self.cashtags,
            "hashtags":          self.hashtags,
            "emojis":            self.emojis[:20],
            "seed_tag":          self.seed,
            "co_occurring_tags": co_tags,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Mode A: Nitter RSS
# ─────────────────────────────────────────────────────────────────────────────

def _nitter_feed_urls(instance: str, target: str) -> list[str]:
    """
    Return candidate Nitter RSS URLs for a given target, in priority order.

      @handle   → [/handle/rss]
      #hashtag  → [/hashtag/tag/rss, /search/rss?q=%23tag&f=tweets]
                   Two patterns because many instances only support one form.
      $CASHTAG  → [/search/rss?q=%24CASHTAG&f=tweets]
    """
    t = target.strip()
    if t.startswith("@"):
        return [f"https://{instance}/{t[1:]}/rss"]
    elif t.startswith("#"):
        tag = quote_plus(t[1:])
        return [
            f"https://{instance}/hashtag/{tag}/rss",
            f"https://{instance}/search/rss?q=%23{tag}&f=tweets",
        ]
    elif t.startswith("$"):
        return [f"https://{instance}/search/rss?q={quote_plus(t)}&f=tweets"]
    else:
        return [f"https://{instance}/{t}/rss"]


def _parse_nitter_rss(xml_bytes: bytes, instance: str, seed: str = "") -> list[Tweet]:
    """
    Parse a Nitter RSS feed byte string into a list of Tweet objects.

    Nitter RSS is standard RSS 2.0. The author is in <author> or the
    channel title. Tweet text is in <description>; <title> contains
    '@handle: truncated text'.
    """
    tweets: list[Tweet] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        log.warning("RSS parse error from %s: %s", instance, exc)
        return tweets

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}

    # Channel-level handle (fallback if item has no author)
    channel    = root.find("channel")
    chan_title = channel.findtext("title", "") if channel is not None else ""
    # Nitter channel title: "@handle / Nitter" or "hashtag - Nitter"
    chan_handle_m = re.match(r"@(\w+)", chan_title or "")
    chan_handle   = chan_handle_m.group(1) if chan_handle_m else "unknown"

    items = root.findall(".//item")[:MAX_ITEMS_PER_FEED]
    for item in items:
        link        = (item.findtext("link")    or "").strip()
        title_raw   = (item.findtext("title")   or "").strip()
        desc_raw    = (item.findtext("description") or title_raw).strip()
        pub_raw     = (item.findtext("pubDate") or "").strip()
        author_raw  = (item.findtext("author")  or
                       item.findtext("dc:creator", namespaces=ns) or "").strip()

        if not link or not desc_raw:
            continue

        # Clean HTML tags from description (Nitter wraps some content in <p>)
        content = re.sub(r"<[^>]{0,300}>", " ", desc_raw)
        content = re.sub(r"\s{2,}", " ", content).strip()
        content = _sanitize(content)
        if not content:
            continue

        # Resolve handle: author field > title prefix > channel
        if author_raw.startswith("@"):
            handle = author_raw.lstrip("@")
            display = handle
        else:
            m = _HANDLE_FROM_TITLE_RE.match(title_raw)
            handle  = m.group(1).lstrip("@") if m else chan_handle
            display = handle

        # Parse publish timestamp
        published_at = datetime.now(timezone.utc).isoformat()
        if pub_raw:
            try:
                published_at = parsedate_to_datetime(pub_raw).isoformat()
            except Exception:
                pass

        tweets.append(Tweet(
            tweet_url=link,
            handle=handle,
            display_name=display,
            content=content,
            published_at=published_at,
            seed=seed,
        ))

    return tweets


def collect_nitter(targets: list[str], session: requests.Session) -> list[Tweet]:
    """
    Collect tweets via Nitter RSS for all targets.

    For each target, iterates NITTER_INSTANCES × URL_PATTERNS until one
    succeeds. Dead instances fail immediately (connect=0 in Retry config)
    and are skipped without backoff delay.

    Hashtag targets try two URL patterns:
      1. /hashtag/{tag}/rss       — native hashtag endpoint
      2. /search/rss?q=%23{tag}   — search RSS fallback
    """
    all_tweets: list[Tweet] = []
    seen_urls:  set[str]    = set()

    for target in targets:
        fetched = False
        for instance in NITTER_INSTANCES:
            if fetched:
                break
            for url in _nitter_feed_urls(instance, target):
                try:
                    resp = session.get(url, timeout=REQUEST_TIMEOUT)
                    if resp.status_code != 200:
                        log.debug(
                            "Nitter [%s] HTTP %d — %s",
                            instance, resp.status_code, url,
                        )
                        continue

                    # Reject empty or non-XML bodies (instance returned
                    # an HTML error page or empty response for this endpoint)
                    body = resp.content.strip()
                    if not body or not body.startswith(b"<"):
                        log.debug(
                            "Nitter [%s] empty/non-XML body for %s — trying next pattern",
                            instance, url,
                        )
                        continue

                    tweets = _parse_nitter_rss(body, instance, seed=target)
                    new    = [t for t in tweets if t.tweet_url not in seen_urls]
                    seen_urls.update(t.tweet_url for t in new)
                    all_tweets.extend(new)
                    log.info(
                        "Nitter [%s] target=%-22s  fetched=%d  new=%d",
                        instance, target, len(tweets), len(new),
                    )
                    fetched = True
                    break
                except Exception as exc:
                    log.debug("Nitter [%s] error: %s — %s", instance, url, exc)

        if not fetched:
            log.warning("All Nitter instances failed for target: %s", target)
        time.sleep(INTER_REQUEST_GAP)

    return all_tweets


# ─────────────────────────────────────────────────────────────────────────────
# Mode B: X Guest / Bearer API
# ─────────────────────────────────────────────────────────────────────────────

def _activate_guest_token(session: requests.Session, bearer: str) -> Optional[str]:
    """
    Obtain a guest token via the undocumented activation endpoint.
    Used as fallback when the official bearer token is absent or invalid.
    """
    try:
        resp = session.post(
            X_GUEST_ACTIVATE,
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("guest_token")
    except Exception as exc:
        log.warning("Guest token activation failed: %s", exc)
    return None


def _parse_v2_tweets(data: dict) -> list[Tweet]:
    """Parse X API v2 search response into Tweet objects."""
    tweets: list[Tweet] = []
    for item in data.get("data", []):
        tweet_id  = item.get("id", "")
        text      = item.get("text", "").strip()
        author_id = item.get("author_id", "")
        created   = item.get("created_at", datetime.now(timezone.utc).isoformat())
        url       = f"https://twitter.com/i/web/status/{tweet_id}"
        if not text:
            continue
        tweets.append(Tweet(
            tweet_url=url,
            handle=f"uid_{author_id}",   # v2 free tier doesn't return username without expansion
            display_name=author_id,
            content=_sanitize(text),
            published_at=created,
        ))
    return tweets


def _parse_v1_tweets(data: dict) -> list[Tweet]:
    """Parse X API v1.1 search response into Tweet objects."""
    tweets: list[Tweet] = []
    for item in data.get("statuses", []):
        tweet_id  = str(item.get("id_str", ""))
        text      = (item.get("full_text") or item.get("text") or "").strip()
        user      = item.get("user", {})
        handle    = user.get("screen_name", "unknown")
        name      = user.get("name", handle)
        created   = item.get("created_at", datetime.now(timezone.utc).isoformat())
        url       = f"https://twitter.com/{handle}/status/{tweet_id}"
        if not text:
            continue
        # Parse v1.1 date: "Mon Jan 01 00:00:00 +0000 2026"
        try:
            created = parsedate_to_datetime(created).isoformat()
        except Exception:
            created = datetime.now(timezone.utc).isoformat()
        tweets.append(Tweet(
            tweet_url=url,
            handle=handle,
            display_name=name,
            content=_sanitize(text),
            published_at=created,
        ))
    return tweets


def collect_guest_api(targets: list[str], session: requests.Session) -> list[Tweet]:
    """
    Collect tweets using X bearer token (v2 or v1.1 fallback).
    Attempts official v2 API first; falls back to v1.1 with guest token.
    """
    bearer = X_BEARER_TOKEN
    if not bearer:
        log.warning("X_BEARER_TOKEN not set — guest_api mode unavailable")
        return []

    all_tweets: list[Tweet] = []
    seen_urls:  set[str]    = set()

    for target in targets:
        # Build query string
        if target.startswith("@"):
            query = f"from:{target[1:]}"
        elif target.startswith("#"):
            query = target
        elif target.startswith("$"):
            query = target
        else:
            query = target

        fetched = False

        # ── Attempt 1: v2 search/recent ──────────────────────────────────────
        try:
            resp = session.get(
                X_V2_SEARCH,
                params={
                    "query":        query,
                    "max_results":  "100",
                    "tweet.fields": "author_id,created_at,text",
                },
                headers={"Authorization": f"Bearer {bearer}"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                batch = _parse_v2_tweets(resp.json())
                for tw in batch:
                    tw.seed = target
                new = [t for t in batch if t.tweet_url not in seen_urls]
                seen_urls.update(t.tweet_url for t in new)
                all_tweets.extend(new)
                log.info("X v2 API  query=%s  new=%d", query, len(new))
                fetched = True
            elif resp.status_code == 401:
                log.warning("X v2 API 401 for %s — trying guest token fallback", query)
        except Exception as exc:
            log.warning("X v2 API error for %s: %s", query, exc)

        # ── Attempt 2: v1.1 with guest token ─────────────────────────────────
        if not fetched:
            guest_token = _activate_guest_token(session, bearer)
            if guest_token:
                try:
                    resp = session.get(
                        X_V1_SEARCH,
                        params={"q": query, "count": "100", "tweet_mode": "extended"},
                        headers={
                            "Authorization": f"Bearer {bearer}",
                            "x-guest-token": guest_token,
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        batch = _parse_v1_tweets(resp.json())
                        for tw in batch:
                            tw.seed = target
                        new = [t for t in batch if t.tweet_url not in seen_urls]
                        seen_urls.update(t.tweet_url for t in new)
                        all_tweets.extend(new)
                        log.info("X v1.1 API query=%s  new=%d", query, len(new))
                        fetched = True
                    else:
                        log.warning(
                            "X v1.1 API HTTP %d for %s", resp.status_code, query
                        )
                except Exception as exc:
                    log.warning("X v1.1 API error for %s: %s", query, exc)

        if not fetched:
            log.warning("guest_api: all attempts failed for target: %s", target)
        time.sleep(INTER_REQUEST_GAP)

    return all_tweets


# ─────────────────────────────────────────────────────────────────────────────
# Tag Co-Occurrence Matrix
# ─────────────────────────────────────────────────────────────────────────────

class TagCoOccurrenceMatrix:
    """
    Per-pulse accumulator for tag co-occurrence counts.

    For every tweet collected, records which tags appear alongside the seed
    that triggered the fetch. Persisted to flux_tag_cooccurrence at run end.

    A special co_tag value "__total__" is written per seed to record how many
    tweets were collected for that seed — used by discovery.py for rate calc.
    """

    def __init__(self, pulse_id: str, pulse_ts: str) -> None:
        self.pulse_id = pulse_id
        self.pulse_ts = pulse_ts
        # (seed_norm, co_tag_norm) → count
        self._counts: dict[tuple[str, str], int] = {}
        # seed_norm → total tweet count
        self._seed_totals: dict[str, int] = {}

    def record(self, tweet: Tweet) -> None:
        """Record all co-occurring tags for one tweet."""
        seed_norm = _normalize_tag(tweet.seed) if tweet.seed else ""
        if not seed_norm:
            return
        self._seed_totals[seed_norm] = self._seed_totals.get(seed_norm, 0) + 1

        all_tags = set(tweet.hashtags) | {_normalize_tag(t) for t in tweet.cashtags}
        for raw_tag in all_tags:
            co_norm = _normalize_tag(raw_tag)
            if co_norm and co_norm != seed_norm:
                key = (seed_norm, co_norm)
                self._counts[key] = self._counts.get(key, 0) + 1

    def rows(self) -> list[tuple]:
        """All (pulse_id, pulse_ts, seed_tag, co_tag, count) rows to persist."""
        out: list[tuple] = []
        # co-occurrence pairs
        for (seed, co), cnt in self._counts.items():
            out.append((self.pulse_id, self.pulse_ts, seed, co, cnt))
        # sentinel totals — "__total__" lets discovery.py compute rates
        for seed, total in self._seed_totals.items():
            out.append((self.pulse_id, self.pulse_ts, seed, "__total__", total))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Butterfly schema + persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_butterfly_schema(conn: sqlite3.Connection) -> None:
    """
    Idempotently create the two FLUX discovery tables.
    Called inside both _persist_cooccurrence and _load_latent_seeds so
    the tables always exist when needed, without a separate migration step.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flux_tag_cooccurrence (
            pulse_id    TEXT    NOT NULL,
            pulse_ts    TEXT    NOT NULL,
            seed_tag    TEXT    NOT NULL,
            co_tag      TEXT    NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pulse_id, seed_tag, co_tag)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ftc_seed ON flux_tag_cooccurrence(seed_tag)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ftc_co  ON flux_tag_cooccurrence(co_tag)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flux_latent_seeds (
            tag             TEXT    PRIMARY KEY,
            parent_seed     TEXT,
            discovery_depth INTEGER NOT NULL DEFAULT 1,
            jaccard_score   REAL    NOT NULL DEFAULT 0.0,
            velocity        REAL    NOT NULL DEFAULT 1.0,
            total_count     INTEGER NOT NULL DEFAULT 0,
            first_seen      TEXT    NOT NULL,
            last_seen       TEXT    NOT NULL,
            is_active       INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()


def _persist_cooccurrence(
    matrix: TagCoOccurrenceMatrix,
    dry_run: bool = False,
) -> int:
    """
    Write the co-occurrence matrix to flux_tag_cooccurrence.
    Returns number of rows written (0 if dry_run).
    """
    rows = matrix.rows()
    if not rows:
        return 0
    if dry_run:
        log.info("[DRY-RUN] co-occurrence rows that would be written: %d", len(rows))
        return len(rows)

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_butterfly_schema(conn)
        conn.executemany(
            """
            INSERT INTO flux_tag_cooccurrence
                (pulse_id, pulse_ts, seed_tag, co_tag, count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pulse_id, seed_tag, co_tag) DO UPDATE SET
                count = count + excluded.count
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def _load_latent_seeds(depth_limit: int = 1) -> list[tuple[str, int]]:
    """
    Return top FLUX_DISCOVERY_TOP_N active latent seeds within depth_limit.
    Used by run() in butterfly mode to expand the target list.
    Returns list of (tag, discovery_depth).
    """
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            _ensure_butterfly_schema(conn)
            rows = conn.execute(
                """
                SELECT tag, discovery_depth
                FROM   flux_latent_seeds
                WHERE  is_active = 1
                  AND  discovery_depth <= ?
                ORDER  BY jaccard_score * velocity DESC
                LIMIT  ?
                """,
                (depth_limit, FLUX_DISCOVERY_TOP_N),
            ).fetchall()
            return [(r[0], r[1]) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Could not load latent seeds: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Database persistence
# ─────────────────────────────────────────────────────────────────────────────

def _persist(tweets: list[Tweet], dry_run: bool = False) -> tuple[int, int]:
    """
    Write collected tweets to signals + socint_signals tables.

    Returns (signals_written, socint_written).
    INSERT OR IGNORE on external_id ensures idempotency.
    """
    if not tweets:
        return 0, 0

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        sig_written    = 0
        socint_written = 0
        now            = datetime.now(timezone.utc).isoformat() + "Z"

        for tw in tweets:
            signal_id = str(uuid.uuid4())

            if dry_run:
                log.info(
                    "[DRY-RUN] @%s  cashtags=%s  emojis=%d  content=%s",
                    tw.handle, tw.cashtags, len(tw.emojis),
                    tw.content[:80].encode("ascii", "replace").decode("ascii"),
                )
                sig_written += 1
                continue

            # ── Write to main signals table ───────────────────────────────────
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO signals (
                    signal_id, source, external_id, title, content,
                    timestamp, status, metadata_json, stream,
                    relevance_score, source_type, is_priority
                ) VALUES (?, 'x_pulse', ?, ?, ?, ?, 'raw', ?, 'GLOBAL', 1.0, 'live', 0)
                """,
                (
                    signal_id,
                    tw.external_id,
                    tw.title,
                    tw.content,
                    tw.published_at,
                    json.dumps(tw.metadata()),
                ),
            )

            if cur.rowcount == 0:
                # Already exists — skip socint_signals write too
                continue
            sig_written += 1

            # ── Write to socint_signals table ─────────────────────────────────
            conn.execute(
                """
                INSERT OR IGNORE INTO socint_signals
                    (source, actor_id, signal_id, content, metadata_json, timestamp)
                VALUES ('x_pulse', NULL, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    tw.content,
                    json.dumps(tw.metadata()),
                    tw.published_at,
                ),
            )
            socint_written += 1

        if not dry_run:
            conn.commit()

    finally:
        conn.close()

    return sig_written, socint_written


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(
    mode:            str            = X_PULSE_MODE,
    targets:         list[str] | None = None,
    dry_run:         bool           = False,
    discovery_mode:  bool           = FLUX_DISCOVERY_MODE,
) -> dict:
    """
    Main collector entry point. Called by the FORGE pipeline dispatcher
    or directly from the CLI.

    When discovery_mode=True (FLUX_DISCOVERY_MODE=true env var), the
    top latent seeds from flux_latent_seeds are appended to the target
    list up to FLUX_MAX_DISCOVERY_DEPTH hops from the original seeds.

    Returns a summary dict for telemetry logging.
    """
    _t0      = time.monotonic()
    pulse_id = str(uuid.uuid4())
    pulse_ts = datetime.now(timezone.utc).isoformat() + "Z"

    # ── Resolve base targets ──────────────────────────────────────────────────
    if not targets:
        raw     = X_PULSE_TARGETS
        targets = (
            [t.strip() for t in raw.split(",") if t.strip()]
            if raw else DEFAULT_TARGETS
        )
    base_targets = list(targets)

    # ── Butterfly expansion ───────────────────────────────────────────────────
    # Append up to FLUX_DISCOVERY_TOP_N latent seeds whose discovery_depth is
    # below the max, so the net never drifts past FLUX_MAX_DISCOVERY_DEPTH hops.
    if discovery_mode:
        latent = _load_latent_seeds(depth_limit=FLUX_MAX_DISCOVERY_DEPTH - 1)
        appended: list[str] = []
        for tag, _depth in latent:
            # Re-attach the prefix Nitter expects (#tag or $TAG)
            if tag.startswith("$"):
                formatted = tag.upper()
            else:
                formatted = f"#{tag}"
            if formatted not in targets:
                targets = list(targets) + [formatted]
                appended.append(formatted)
        if appended:
            log.info(
                "Butterfly: appended %d latent seeds → %s",
                len(appended), appended,
            )

    log.info("Mode       : %s", mode)
    log.info("Targets    : %s", targets)
    log.info("Dry run    : %s", dry_run)
    log.info("Discovery  : %s", discovery_mode)
    log.info("DB         : %s", DB_PATH)

    # ── Collect ───────────────────────────────────────────────────────────────
    session = _make_session()
    tweets: list[Tweet] = []

    if mode == "nitter":
        tweets = collect_nitter(targets, session)
    elif mode == "guest_api":
        tweets = collect_guest_api(targets, session)
    else:
        log.warning("Unknown mode '%s' — defaulting to nitter", mode)
        tweets = collect_nitter(targets, session)

    log.info("Tweets collected : %d", len(tweets))

    # ── Build co-occurrence matrix ────────────────────────────────────────────
    matrix = TagCoOccurrenceMatrix(pulse_id, pulse_ts)
    for tw in tweets:
        matrix.record(tw)

    # ── Persist ───────────────────────────────────────────────────────────────
    sig_written, socint_written = _persist(tweets, dry_run=dry_run)
    cooc_rows = _persist_cooccurrence(matrix, dry_run=dry_run)

    elapsed = time.monotonic() - _t0
    summary = {
        "status":          "done",
        "mode":            mode,
        "base_targets":    base_targets,
        "targets":         targets,
        "pulse_id":        pulse_id,
        "collected":       len(tweets),
        "signals_written": sig_written,
        "socint_written":  socint_written,
        "cooc_pairs":      cooc_rows,
        "discovery_mode":  discovery_mode,
        "dry_run":         dry_run,
        "duration_s":      round(elapsed, 2),
    }
    log.info("Complete: %s", summary)
    _log_run(DB_PATH, "x_pulse", "success",
             records_in=len(tweets), records_out=sig_written,
             duration_s=elapsed, detail=summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE FLUX — X-Pulse Collector")
    parser.add_argument("--mode",      choices=["nitter", "guest_api"], default=X_PULSE_MODE)
    parser.add_argument("--targets",   type=str, default=X_PULSE_TARGETS,
                        help="Comma-separated: @handle,#hashtag,$CASHTAG")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--discovery", action="store_true", default=FLUX_DISCOVERY_MODE,
                        help="Butterfly mode: append top latent seeds to target list")
    args = parser.parse_args()

    target_list = (
        [t.strip() for t in args.targets.split(",") if t.strip()]
        if args.targets else None
    )

    result = run(
        mode=args.mode,
        targets=target_list,
        dry_run=args.dry_run,
        discovery_mode=args.discovery,
    )
    sys.exit(0 if result["status"] == "done" else 1)
