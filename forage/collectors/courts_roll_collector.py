#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- Predictive Court Docket Engine
========================================
Parses South African High Court daily rolls to provide predictive forensic
lookahead -- indexing upcoming trial dates, defendant names, and case
references before they hit general news.

Data sources:
  - Gauteng Division:    judiciary.org.za court rolls / gauteng-local-division
  - Western Cape:        judiciary.org.za court rolls / western-cape-division
  - KwaZulu-Natal:       judiciary.org.za court rolls / kwazulu-natal-division
  - Online Court Roll:   judiciary.org.za court-online-court-roll

Collection strategy
-------------------
  1. Fetch the index page for each division from the Judiciary website.
  2. Extract links to court roll documents (PDF links, HTML entries).
  3. For PDF links: store URL + extract title/date from filename or link text.
  4. For HTML content: parse case entries directly (case number, parties,
     courtroom, judge).
  5. Cross-reference parties against FORGE actor registry for predictive
     escalation -- if a tracked actor appears in an upcoming roll, boost
     gravity and flag priority.

Gravity scoring:
  Base:  0.40 (court docket -- high forensic value)
  +0.15  if a FORGE actor name appears in the case parties
  Cap:   0.55

Stream: CRIME_INTEL (all court docket entries)

Usage:
  python forage/collectors/courts_roll_collector.py
  python forage/collectors/courts_roll_collector.py --dry-run
  python forage/collectors/courts_roll_collector.py --divisions gauteng,kzn
  python forage/collectors/courts_roll_collector.py --days 14 --dry-run

Dependencies: stdlib only (urllib.request, html.parser)
"""

# -- Manifest (AST-parsed by Autodiscovery Registry at boot) -----------------
__manifest__ = {
    "id":          "court_rolls_predictive",
    "name":        "Predictive Court Docket Engine",
    "description": "Parses regional and provincial High Court daily rolls to index upcoming litigation, defendant tracks, and corporate case schedules.",
    "icon":        "⚖",
    "entry":       "forage/collectors/courts_roll_collector.py",
    "args":        [],
    "job_key":     "court_rolls_predictive",
    "version":     "1.0.0",
}

# -- Windows CP1252 safety -- reconfigure stdout to UTF-8 before any print() -
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# -- Standard library ---------------------------------------------------------
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# -- Path setup ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SOURCE_ID = __manifest__["id"]

_FORGE_DB_ENV = os.environ.get("FORGE_DB")
DB_PATH = Path(_FORGE_DB_ENV) if _FORGE_DB_ENV else BASE_DIR / "database.db"

# -- Sanitizer (Stable 1.1 compliance) ---------------------------------------
try:
    from core.pipeline.ingest import sanitize_text
except ImportError:
    def sanitize_text(t):
        return re.sub(r"<[^>]{0,500}>", " ", t or "").strip()

# -- Pipeline logger ----------------------------------------------------------
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*_a, **_kw):
        pass


# =============================================================================
# Configuration
# =============================================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 30       # seconds per HTTP request
INTER_REQUEST_DELAY = 2.0  # seconds between page fetches (courteous)

# Gravity thresholds
GRAVITY_BASE = 0.40
GRAVITY_ACTOR_BOOST = 0.15
GRAVITY_CAP = 0.55

# -- Division registry --------------------------------------------------------
# Each division has: display name, index URL, lat/lng for the court seat.

DIVISIONS: dict[str, dict] = {
    "gauteng": {
        "name": "Gauteng Division",
        "url":  "https://www.judiciary.org.za/index.php/court-rolls/gauteng-local-division",
        "lat":  -26.2041,
        "lng":  28.0473,
    },
    "western-cape": {
        "name": "Western Cape Division",
        "url":  "https://www.judiciary.org.za/index.php/court-rolls/western-cape-division",
        "lat":  -33.9249,
        "lng":  18.4241,
    },
    "kzn": {
        "name": "KwaZulu-Natal Division",
        "url":  "https://www.judiciary.org.za/index.php/court-rolls/kwazulu-natal-division",
        "lat":  -29.8587,
        "lng":  31.0218,
    },
}

# Supplementary URL -- generic "online court roll" page
ONLINE_ROLL_URL = "https://www.judiciary.org.za/index.php/court-online-court-roll"


# =============================================================================
# HTML Parsing
# =============================================================================

class _LinkExtractor(HTMLParser):
    """Extract all <a> tags with their href and inner text."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, text)
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() == "a":
            href = None
            for name, value in attrs:
                if name.lower() == "href" and value:
                    href = value.strip()
            if href:
                self._in_a = True
                self._current_href = href
                self._current_text = []

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._in_a:
            text = " ".join(self._current_text).strip()
            if self._current_href:
                self.links.append((self._current_href, text))
            self._in_a = False
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str):
        if self._in_a:
            self._current_text.append(data.strip())

    def error(self, message):
        pass  # Suppress HTMLParser errors on malformed HTML


class _TableExtractor(HTMLParser):
    """
    Extract rows from HTML tables on court roll pages.

    Court roll pages may display case data in <table> elements with
    columns for case number, parties, courtroom, judge, etc.
    Returns a list of row-lists: [[cell1, cell2, ...], ...]
    """

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_l = tag.lower()
        if tag_l == "table":
            self._in_table = True
        elif tag_l == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag_l in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str):
        tag_l = tag.lower()
        if tag_l in ("td", "th") and self._in_cell:
            cell_text = " ".join(self._current_cell).strip()
            self._current_row.append(cell_text)
            self._in_cell = False
        elif tag_l == "tr" and self._in_row:
            if self._current_row:
                self.rows.append(self._current_row)
            self._in_row = False
        elif tag_l == "table":
            self._in_table = False

    def handle_data(self, data: str):
        if self._in_cell:
            self._current_cell.append(data.strip())

    def error(self, message):
        pass


# =============================================================================
# Date Extraction
# =============================================================================

_DATE_PATTERNS = [
    # YYYY-MM-DD or YYYY/MM/DD
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
    # DD Month YYYY or DD Mon YYYY
    re.compile(
        r"(\d{1,2})\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(\d{4})",
        re.IGNORECASE,
    ),
    # Month DD, YYYY
    re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ),
    # DD-MM-YYYY or DD/MM/YYYY (SA format)
    re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})"),
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_date(text: str) -> str | None:
    """
    Extract a date from text (URL, link text, or surrounding context).
    Returns 'YYYY-MM-DDTHH:MM:SSZ' or None.
    """
    if not text:
        return None

    # Pattern 1: YYYY-MM-DD or YYYY/MM/DD
    m = _DATE_PATTERNS[0].search(text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    # Pattern 2: DD Month YYYY
    m = _DATE_PATTERNS[1].search(text)
    if m:
        try:
            d = int(m.group(1))
            mo = _MONTH_MAP.get(m.group(2).lower())
            y = int(m.group(3))
            if mo:
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    # Pattern 3: Month DD, YYYY
    m = _DATE_PATTERNS[2].search(text)
    if m:
        try:
            mo = _MONTH_MAP.get(m.group(1).lower())
            d = int(m.group(2))
            y = int(m.group(3))
            if mo:
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    # Pattern 4: DD-MM-YYYY or DD/MM/YYYY (SA date format)
    m = _DATE_PATTERNS[3].search(text)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    return None


# =============================================================================
# Case Number Extraction
# =============================================================================

# SA case number patterns:
#   12345/2026, A123/2026, CC 12/2023, CCT 23/2025, etc.
_CASE_NUMBER_RE = re.compile(
    r"(?:(?:CC[T]?|CA|SCA|GP|WCC|KZN|KZD|EC|FS|LP|MP|NC|NW)"
    r"\s*)?"
    r"(\d{1,6})\s*/\s*(\d{4})",
)


def _extract_case_numbers(text: str) -> list[str]:
    """Extract SA court case numbers from text."""
    if not text:
        return []
    matches = _CASE_NUMBER_RE.findall(text)
    # Reconstruct "num/year" form
    return [f"{num}/{year}" for num, year in matches]


# =============================================================================
# Fetch Helpers
# =============================================================================

def _fetch_page(url: str) -> str | None:
    """
    Fetch a URL with a realistic User-Agent.
    Returns decoded HTML string or None on failure.
    """
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-ZA,en;q=0.9",
    })

    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip().split(";")[0]
            try:
                return raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return raw.decode("utf-8", errors="replace")
    except HTTPError as exc:
        print(f"  [court] HTTP {exc.code} fetching {url}")
        return None
    except URLError as exc:
        print(f"  [court] URL error fetching {url}: {exc.reason}")
        return None
    except Exception as exc:
        print(f"  [court] Fetch error {url}: {exc}")
        return None


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a potentially relative URL against a base URL."""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    # Relative path
    if base_url.endswith("/"):
        return base_url + href
    return base_url.rsplit("/", 1)[0] + "/" + href


# =============================================================================
# Court Roll Keywords
# =============================================================================

_ROLL_KEYWORDS = re.compile(
    r"court\s*roll|daily\s*roll|hearing|motion|trial|"
    r"case\s*(?:number|no\.?|#)|matter|"
    r"opposed|unopposed|application|"
    r"plaintiff|defendant|applicant|respondent|"
    r"judge|justice|magistrate|"
    r"criminal|civil|commercial|"
    r"docket|roll\s*for|set\s*down",
    re.IGNORECASE,
)


def _is_roll_relevant(href: str, text: str) -> bool:
    """Determine if a link is relevant to court roll content."""
    combined = f"{href} {text}"

    # Always include PDFs from the judiciary domain
    href_lower = href.lower()
    if href_lower.endswith(".pdf") and "judiciary" in href_lower:
        return True

    # Include links with roll-related keywords
    if _ROLL_KEYWORDS.search(combined):
        return True

    # Include links whose text or URL contains "roll"
    if "roll" in combined.lower():
        return True

    return False


# =============================================================================
# Item Extraction
# =============================================================================

def _extract_items_from_links(
    html_content: str,
    page_url: str,
    division_key: str,
) -> list[dict]:
    """
    Parse an HTML page and extract court-roll-relevant link items.
    Each item: {url, title, date, item_type, division, case_numbers}.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html_content)
    except Exception:
        pass

    items: list[dict] = []
    seen_urls: set[str] = set()

    for href, text in parser.links:
        # Skip empty, anchor-only, mailto, javascript links
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue

        full_url = _resolve_url(href, page_url)

        if full_url in seen_urls:
            continue

        clean_text = text.strip()
        if len(clean_text) < 3:
            continue

        # Skip obvious navigation links
        if clean_text.lower() in (
            "home", "about", "contact", "menu", "search", "login",
            "register", "back", "next", "previous", "more", "read more",
            "skip to content", "close", "sitemap",
        ):
            continue

        if not _is_roll_relevant(full_url, clean_text):
            continue

        seen_urls.add(full_url)

        item_type = "pdf_document" if full_url.lower().endswith(".pdf") else "roll_page"
        date_str = _extract_date(full_url) or _extract_date(clean_text)
        case_numbers = _extract_case_numbers(clean_text)

        items.append({
            "url":          full_url,
            "title":        clean_text,
            "date":         date_str,
            "item_type":    item_type,
            "division":     division_key,
            "case_numbers": case_numbers,
        })

    return items


def _extract_items_from_tables(
    html_content: str,
    division_key: str,
    page_url: str,
) -> list[dict]:
    """
    Parse HTML tables for inline court roll entries.
    Court roll tables may have columns like:
      Case Number | Parties | Courtroom | Judge | Date
    """
    parser = _TableExtractor()
    try:
        parser.feed(html_content)
    except Exception:
        return []

    if len(parser.rows) < 2:
        return []  # Need at least a header row + 1 data row

    # Try to identify column indices from the header row
    header = [cell.lower().strip() for cell in parser.rows[0]]

    col_map: dict[str, int] = {}
    for idx, cell in enumerate(header):
        if any(k in cell for k in ("case", "number", "no")):
            col_map.setdefault("case_number", idx)
        elif any(k in cell for k in ("part", "applicant", "respondent", "plaintiff", "defendant")):
            col_map.setdefault("parties", idx)
        elif any(k in cell for k in ("court", "room")):
            col_map.setdefault("courtroom", idx)
        elif any(k in cell for k in ("judge", "justice")):
            col_map.setdefault("judge", idx)
        elif any(k in cell for k in ("date", "time", "set down")):
            col_map.setdefault("date", idx)
        elif any(k in cell for k in ("matter", "type", "nature")):
            col_map.setdefault("matter_type", idx)

    # If we cannot identify at least case number or parties, skip
    if "case_number" not in col_map and "parties" not in col_map:
        return []

    items: list[dict] = []

    for row in parser.rows[1:]:  # Skip header
        if not row or all(not cell.strip() for cell in row):
            continue

        def _get(key: str) -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""

        case_number = _get("case_number")
        parties = _get("parties")
        courtroom = _get("courtroom")
        judge = _get("judge")
        date_text = _get("date")
        matter_type = _get("matter_type")

        # Need at least a case number or parties to be useful
        if not case_number and not parties:
            continue

        date_str = _extract_date(date_text) if date_text else None

        # Build a descriptive title
        title_parts = []
        if case_number:
            title_parts.append(case_number)
        if parties:
            title_parts.append(parties[:120])
        division_info = DIVISIONS.get(division_key, {})
        division_name = division_info.get("name", division_key)
        title_parts.append(f"({division_name})")
        title = " — ".join(title_parts)

        case_numbers = _extract_case_numbers(case_number) if case_number else []

        items.append({
            "url":          page_url,
            "title":        title,
            "date":         date_str,
            "item_type":    "table_entry",
            "division":     division_key,
            "case_numbers": case_numbers,
            "case_number":  case_number,
            "parties":      parties,
            "courtroom":    courtroom,
            "judge":        judge,
            "matter_type":  matter_type,
        })

    return items


# =============================================================================
# Actor Cross-Reference
# =============================================================================

def _load_actor_names(conn: sqlite3.Connection) -> set[str]:
    """Load all actor names from the database for cross-referencing."""
    try:
        rows = conn.execute(
            "SELECT name FROM actors WHERE length(name) > 3"
        ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _check_actor_match(text: str, actor_names: set[str]) -> str | None:
    """
    Check if any known actor name appears in the given text.
    Returns the first matching actor name, or None.
    """
    if not text or not actor_names:
        return None
    text_lower = text.lower()
    for actor in actor_names:
        if actor.lower() in text_lower:
            return actor
    return None


# =============================================================================
# Signal Builder
# =============================================================================

def _build_signal(item: dict, actor_names: set[str]) -> dict:
    """
    Convert an extracted court roll item into a FORGE signal dict.

    Gravity scoring:
      Base:  0.40 (court docket -- high forensic value)
      +0.15  if a FORGE actor name appears in the parties/title
      Cap:   0.55
    """
    division_key = item["division"]
    division_info = DIVISIONS.get(division_key, {})
    division_name = division_info.get("name", division_key)
    lat = division_info.get("lat", -26.2041)
    lng = division_info.get("lng", 28.0473)

    url = item["url"]
    title_raw = item.get("title", "")
    title = sanitize_text(title_raw)[:400]

    # Combine searchable text for actor matching
    searchable_text = " ".join(filter(None, [
        title_raw,
        item.get("parties", ""),
        item.get("case_number", ""),
    ]))

    # Actor cross-reference
    matched_actor = _check_actor_match(searchable_text, actor_names)

    # Gravity scoring
    gravity = GRAVITY_BASE
    is_priority = 0
    if matched_actor:
        gravity = min(gravity + GRAVITY_ACTOR_BOOST, GRAVITY_CAP)
        is_priority = 1

    # Build content field
    content_parts = [f"Court roll entry: {title}."]
    if item.get("parties"):
        content_parts.append(f"Parties: {item['parties']}.")
    if item.get("judge"):
        content_parts.append(f"Judge: {item['judge']}.")
    if item.get("courtroom"):
        content_parts.append(f"Courtroom: {item['courtroom']}.")
    if item.get("matter_type"):
        content_parts.append(f"Matter: {item['matter_type']}.")
    content_parts.append(f"Division: {division_name}.")
    if item["item_type"] == "pdf_document":
        content_parts.append(f"Source: PDF document at {url}.")
    if matched_actor:
        content_parts.append(f"[ACTOR MATCH: {matched_actor}]")
    content = sanitize_text(" ".join(content_parts))

    # Timestamp: use extracted date or current UTC time
    timestamp = item.get("date") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Hearing date for metadata (may differ from timestamp if extracted)
    hearing_date = item.get("date")

    # External ID: deterministic dedup key
    # Use court seat + case number + date for table entries,
    # or URL hash for link-based entries
    case_number = item.get("case_number", "")
    if case_number and hearing_date:
        dedup_seed = f"{division_key}:{case_number}:{hearing_date}"
    else:
        dedup_seed = f"{division_key}:{url}:{title_raw}"

    external_id = "court_roll:" + hashlib.sha256(
        dedup_seed.encode("utf-8")
    ).hexdigest()[:16]

    # Structured metadata
    metadata = {
        "case_number":  case_number or None,
        "court_seat":   division_name,
        "division":     division_key,
        "hearing_date": hearing_date,
        "judge":        item.get("judge") or None,
        "courtroom":    item.get("courtroom") or None,
        "parties":      _split_parties(item.get("parties", "")),
        "matter_type":  item.get("matter_type") or None,
        "source_url":   url,
        "item_type":    item["item_type"],
        "case_numbers": item.get("case_numbers", []),
        "matched_actor": matched_actor,
        "collector":    SOURCE_ID,
    }

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          SOURCE_ID,
        "external_id":     external_id,
        "title":           title,
        "content":         content,
        "lat":             lat,
        "lng":             lng,
        "timestamp":       timestamp,
        "status":          "raw",
        "stream":          "CRIME_INTEL",
        "relevance_score": 1.6,
        "is_priority":     is_priority,
        "source_type":     "live",
        "gravity_score":   gravity,
        "metadata_json":   json.dumps(metadata, ensure_ascii=False),
    }


def _split_parties(parties_str: str) -> list[str]:
    """
    Split a parties string into individual names.
    Common separators: 'v', 'vs', 'and', '&', ';'
    """
    if not parties_str:
        return []
    # Normalize separators
    text = re.sub(r"\b(?:vs?\.?|versus)\b", "|", parties_str, flags=re.IGNORECASE)
    text = re.sub(r"\band\b", "|", text, flags=re.IGNORECASE)
    text = text.replace("&", "|").replace(";", "|")
    parts = [p.strip() for p in text.split("|") if p.strip()]
    return parts


# =============================================================================
# Database Persistence
# =============================================================================

INSERT_SQL = """
    INSERT OR IGNORE INTO signals (
        signal_id, source, external_id, title, content,
        lat, lng, timestamp, status, metadata_json,
        stream, relevance_score, source_type, is_priority,
        gravity_score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _persist_signals(signals: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """
    Write signals to the database.
    Returns (inserted_count, error_count).
    """
    if dry_run:
        print(f"[court] DRY RUN -- {len(signals)} signals would be inserted:")
        for s in signals[:10]:
            actor_flag = " [ACTOR]" if s["is_priority"] else ""
            print(f"  {s['external_id'][:24]:24s} | g={s['gravity_score']:.2f}"
                  f"{actor_flag} | {s['title'][:70]}")
        if len(signals) > 10:
            print(f"  ... and {len(signals) - 10} more")
        return 0, 0

    if not signals:
        return 0, 0

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    inserted = 0
    errors = 0
    try:
        for sig in signals:
            try:
                conn.execute(INSERT_SQL, (
                    sig["signal_id"],
                    sig["source"],
                    sig["external_id"],
                    sig["title"],
                    sig["content"],
                    sig["lat"],
                    sig["lng"],
                    sig["timestamp"],
                    sig["status"],
                    sig["metadata_json"],
                    sig["stream"],
                    sig["relevance_score"],
                    sig["source_type"],
                    sig["is_priority"],
                    sig["gravity_score"],
                ))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  [court] Insert error for {sig.get('external_id', '?')}: {e}")

        conn.commit()
    finally:
        conn.close()

    if errors:
        print(f"[court] {errors} insert errors encountered")
    return inserted, errors


# =============================================================================
# Collection Engine
# =============================================================================

def _collect_division(
    division_key: str,
    division_info: dict,
) -> list[dict]:
    """
    Fetch and parse court roll data for a single division.
    Returns a list of extracted item dicts.
    """
    url = division_info["url"]
    name = division_info["name"]

    print(f"[court] Fetching {name}: {url}")
    html_content = _fetch_page(url)

    if html_content is None:
        print(f"[court] {name}: site unreachable or down -- skipping")
        return []

    # Extract items from both links and inline tables
    link_items = _extract_items_from_links(html_content, url, division_key)
    table_items = _extract_items_from_tables(html_content, division_key, url)

    all_items = link_items + table_items
    print(f"[court] {name}: {len(link_items)} link items + "
          f"{len(table_items)} table entries = {len(all_items)} total")

    return all_items


def _collect_online_roll() -> list[dict]:
    """
    Fetch the generic online court roll page as a supplementary source.
    Items are tagged with the closest matching division or 'online'.
    """
    print(f"[court] Fetching online court roll: {ONLINE_ROLL_URL}")
    html_content = _fetch_page(ONLINE_ROLL_URL)

    if html_content is None:
        print("[court] Online court roll: unreachable -- skipping")
        return []

    # Extract from both link references and inline tables
    # Default to gauteng coordinates for undetermined division
    link_items = _extract_items_from_links(html_content, ONLINE_ROLL_URL, "gauteng")
    table_items = _extract_items_from_tables(html_content, "gauteng", ONLINE_ROLL_URL)

    all_items = link_items + table_items
    print(f"[court] Online roll: {len(all_items)} items")
    return all_items


# =============================================================================
# Main Runner
# =============================================================================

def run_collector(
    divisions: list[str] | None = None,
    days: int = 30,
    dry_run: bool = False,
) -> dict:
    """
    Main collection function.

    Args:
        divisions:  List of division keys to target (default: all).
        days:       Only insert items with dates within this window.
        dry_run:    If True, print signals without writing to DB.

    Returns:
        Summary dict for pipeline telemetry.
    """
    start_time = time.time()
    print(f"[court] Predictive Court Docket Engine starting")
    print(f"[court] DB: {DB_PATH}")
    print(f"[court] Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"[court] Max age: {days} days")

    # Resolve target divisions
    target_divisions = divisions or list(DIVISIONS.keys())
    valid_divisions = [d for d in target_divisions if d in DIVISIONS]
    if not valid_divisions:
        print(f"[court] ERROR: No valid divisions in {target_divisions}")
        print(f"[court] Valid divisions: {list(DIVISIONS.keys())}")
        return {"status": "error", "reason": "no valid divisions"}

    print(f"[court] Targeting divisions: {valid_divisions}")

    # Age cutoff
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -- Phase 1: Fetch court roll pages from each division -------------------
    all_items: list[dict] = []

    for i, div_key in enumerate(valid_divisions):
        if i > 0:
            time.sleep(INTER_REQUEST_DELAY)

        try:
            items = _collect_division(div_key, DIVISIONS[div_key])
            all_items.extend(items)
        except Exception as exc:
            print(f"[court] Error collecting {div_key}: {exc}")

    # Also try the generic online roll page
    time.sleep(INTER_REQUEST_DELAY)
    try:
        online_items = _collect_online_roll()
        all_items.extend(online_items)
    except Exception as exc:
        print(f"[court] Error collecting online roll: {exc}")

    # Deduplicate by URL + title
    seen: set[str] = set()
    unique_items: list[dict] = []
    for item in all_items:
        dedup_key = f"{item['url']}|{item.get('title', '')}|{item.get('case_number', '')}"
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique_items.append(item)

    print(f"[court] Total unique items: {len(unique_items)} "
          f"(from {len(all_items)} raw)")

    if not unique_items:
        duration = round(time.time() - start_time, 2)
        print(f"[court] No court roll items found. "
              f"Site may be down or rolls not yet published.")
        log_run(SOURCE_ID, "success", 0, 0, duration)
        return {
            "status": "done",
            "fetched": 0,
            "inserted": 0,
            "duration_s": duration,
        }

    # -- Phase 2: Load actor names for cross-reference ------------------------
    actor_names: set[str] = set()
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        try:
            actor_names = _load_actor_names(conn)
        finally:
            conn.close()
        print(f"[court] Loaded {len(actor_names)} actor names for cross-reference")
    except Exception as e:
        print(f"[court] Actor name load failed (non-fatal): {e}")

    # -- Phase 3: Build signals -----------------------------------------------
    signals: list[dict] = []
    build_errors = 0
    filtered = 0
    actor_matches = 0

    for item in unique_items:
        try:
            sig = _build_signal(item, actor_names)

            # Filter by recency: skip items with dates older than cutoff
            if sig["timestamp"] < cutoff_str:
                filtered += 1
                continue

            signals.append(sig)
            if sig["is_priority"]:
                actor_matches += 1
        except Exception as exc:
            build_errors += 1
            if build_errors <= 5:
                print(f"  [court] Signal build error: {exc}")

    print(f"[court] Built {len(signals)} signals "
          f"({filtered} filtered by age, {build_errors} build errors)")
    if actor_matches:
        print(f"[court] Actor matches: {actor_matches} entries flagged priority")

    # Division distribution
    div_counts: dict[str, int] = {}
    for s in signals:
        meta = json.loads(s["metadata_json"])
        div = meta.get("division", "unknown")
        div_counts[div] = div_counts.get(div, 0) + 1
    for div, count in sorted(div_counts.items()):
        print(f"[court]   {div}: {count}")

    # -- Phase 4: Persist signals ---------------------------------------------
    inserted, errors = _persist_signals(signals, dry_run=dry_run)
    duration = round(time.time() - start_time, 2)

    if not dry_run:
        duplicates = len(signals) - inserted - errors
        print(f"[court] Inserted {inserted} new signals "
              f"({duplicates} duplicates, {errors} errors)")
    print(f"[court] Done in {duration}s")

    log_run(SOURCE_ID, "success", len(unique_items), inserted, duration)

    return {
        "status": "done",
        "fetched": len(unique_items),
        "built": len(signals),
        "inserted": inserted,
        "filtered": filtered,
        "actor_matches": actor_matches,
        "build_errors": build_errors,
        "insert_errors": errors,
        "duration_s": duration,
        "division_counts": div_counts,
    }


# -- Mega-runner adapter ------------------------------------------------------

async def async_main(**kwargs):
    """Entry point for tools/mega_ingest.py async collector dispatch."""
    try:
        run_collector()
    except Exception as e:
        print(f"[ERROR] async_main failed in courts_roll_collector.py: {e}")


# -- CLI ----------------------------------------------------------------------

def _parse_divisions(raw: str) -> list[str]:
    """Parse comma-separated division keys, normalizing aliases."""
    aliases = {
        "gp": "gauteng",
        "gauteng": "gauteng",
        "wc": "western-cape",
        "western-cape": "western-cape",
        "westerncape": "western-cape",
        "kzn": "kzn",
        "kwazulu-natal": "kzn",
        "kwazulunatal": "kzn",
    }
    result = []
    for part in raw.split(","):
        key = part.strip().lower()
        resolved = aliases.get(key)
        if resolved:
            result.append(resolved)
        else:
            print(f"[court] WARNING: Unknown division '{key}' -- skipping")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Predictive Court Docket Engine -- parses SA High "
                    "Court daily rolls for upcoming trials and hearings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse without writing to the database.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only insert items from the last N days (default: 30).",
    )
    parser.add_argument(
        "--divisions",
        type=str,
        default="",
        help="Comma-separated division keys to target "
             "(default: all). Options: gauteng, western-cape, kzn "
             "(aliases: gp, wc, kwazulu-natal).",
    )

    args = parser.parse_args()

    division_list = None
    if args.divisions:
        division_list = _parse_divisions(args.divisions)
        if not division_list:
            print("[court] ERROR: No valid divisions specified")
            sys.exit(1)

    result = run_collector(
        divisions=division_list,
        days=args.days,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
