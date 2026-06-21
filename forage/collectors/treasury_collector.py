#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- National Treasury eTender Auditor
===========================================
Scrapes the South African National Treasury eTender portal and government
gazette tender pages for active and awarded procurement notices. Provides
financial crime investigators with contract-level intelligence: tender
reference numbers, department allocations, closing dates, and value
estimates.

Collection strategy
-------------------
  1. Primary: Fetch the eTenders advertised-tenders listing page and parse
     HTML for tender entries (reference, description, department, dates).
  2. Fallback: If the primary site returns minimal content (JS-rendered SPA),
     fetch from the Government gazette tenders listing at gov.za/documents/tenders.
  3. Each discovered tender becomes a signal pinned to National Treasury HQ
     in Pretoria (-25.7461, 28.1881).

Stream routing
--------------
  INFRASTRUCTURE — tenders matching energy/water/logistics/construction/
                   transport/roads/rail/building/electrification keywords
  GLOBAL         — all other procurement

Compliance
----------
  source = manifest["id"] = "treasury_tenders"
  relevance_score = 1.4 (procurement record, analyst value)
  gravity_score = 0.25 base (institutional procurement record)
  Zero new DB tables. All columns present in Stable 1.1 schema.

Dependencies: stdlib only (urllib.request, html.parser)
"""

# -- Manifest (AST-parsed by Autodiscovery Registry at boot) -----------------
__manifest__ = {
    "id":          "treasury_tenders",
    "name":        "National Treasury Tender Auditor",
    "description": "Scrapes National Treasury eTender portals and procurement notices to trace state infrastructure contract awards and tender irregularities.",
    "icon":        "\U0001f4b0",
    "entry":       "forage/collectors/treasury_collector.py",
    "args":        [],
    "job_key":     "treasury_tenders",
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

# -- Pipeline logger (path-safe, no hard coupling) ---------------------------
try:
    from forage.utils.pipeline_logger import log_run
except ImportError:
    def log_run(*_a, **_kw):
        pass

# -- Constants ----------------------------------------------------------------

# National Treasury HQ, Pretoria
TREASURY_LAT = -25.7461
TREASURY_LNG = 28.1881

# URLs to try (government sites restructure frequently; try multiple paths)
TENDER_URLS = [
    "http://www.etenders.gov.za/content/advertised-tenders",
    "https://www.etenders.gov.za/content/advertised-tenders",
    "https://www.gov.za/documents/tenders",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 25   # seconds
INTER_REQUEST_DELAY = 2  # seconds between page fetches
CONTENT_CAP = 3000     # max chars stored in signal.content

# Keywords that route a tender to INFRASTRUCTURE stream (case-insensitive)
INFRASTRUCTURE_KEYWORDS = re.compile(
    r"energy|electricity|electrif|power\s+(?:station|plant|grid|generat)|"
    r"water|sanitation|sewage|reservoir|pipeline|dam\b|"
    r"road|highway|freeway|bridge|overpass|interchange|"
    r"rail|railway|locomotive|prasa|transnet|"
    r"transport|logistics|freight|port\b|harbour|airport|"
    r"construction|building|refurbish|renovati|"
    r"infrastructure|housing|settlement|"
    r"telecom|fibre|broadband|network\s+roll",
    re.IGNORECASE,
)

# Patterns that indicate procurement categories
CATEGORY_PATTERNS = {
    "construction":   re.compile(r"construct|building|renovati|refurbish", re.I),
    "it_services":    re.compile(r"software|ICT|IT\s+service|digital|cyber|system", re.I),
    "consulting":     re.compile(r"consult|advisory|profession|feasibil", re.I),
    "supply":         re.compile(r"supply|deliver|provision|procure|equipment", re.I),
    "energy":         re.compile(r"energy|electric|solar|wind|power|generat", re.I),
    "transport":      re.compile(r"transport|vehicle|fleet|logistics|freight", re.I),
    "water":          re.compile(r"water|sanitation|sewage|plumbing", re.I),
    "security":       re.compile(r"security|guard|surveillance|protect|CCTV", re.I),
    "health":         re.compile(r"health|medical|hospital|clinic|pharma", re.I),
    "education":      re.compile(r"education|school|university|training|learner", re.I),
}


# =============================================================================
#  HTML PARSERS
#  Lightweight stdlib-only parsers that extract tender information from
#  government portal HTML. No BeautifulSoup dependency.
# =============================================================================

class _TenderTableParser(HTMLParser):
    """
    Parse HTML tables and div-based listings from eTender portal pages.
    Extracts structured tender data from table rows and list items.
    """

    def __init__(self):
        super().__init__()
        self.tenders: list[dict] = []
        # State tracking
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._in_link = False
        self._current_row: list[str] = []
        self._current_text: list[str] = []
        self._current_href: str | None = None
        self._cell_count = 0
        self._header_row = True
        # Also capture links with tender-like text
        self._links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        if tag_lower == "table":
            self._in_table = True
            self._header_row = True
        elif tag_lower == "tr":
            self._in_row = True
            self._current_row = []
            self._cell_count = 0
        elif tag_lower in ("td", "th"):
            self._in_cell = True
            self._current_text = []
        elif tag_lower == "a":
            href = None
            for name, value in attrs:
                if name.lower() == "href" and value:
                    href = value.strip()
            if href:
                self._in_link = True
                self._current_href = href

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower == "table":
            self._in_table = False
        elif tag_lower == "tr":
            if self._in_row and self._current_row:
                if self._header_row:
                    self._header_row = False
                else:
                    self._process_row(self._current_row)
            self._in_row = False
        elif tag_lower in ("td", "th"):
            if self._in_cell:
                cell_text = " ".join(self._current_text).strip()
                self._current_row.append(cell_text)
                self._cell_count += 1
            self._in_cell = False
        elif tag_lower == "a":
            if self._in_link and self._current_href:
                link_text = " ".join(self._current_text).strip()
                self._links.append((self._current_href, link_text))
            self._in_link = False
            self._current_href = None

    def handle_data(self, data: str):
        stripped = data.strip()
        if stripped:
            self._current_text.append(stripped)

    def error(self, message):
        pass  # Suppress HTMLParser errors on malformed HTML

    def _process_row(self, cells: list[str]):
        """
        Try to extract tender data from a table row.
        Government tender tables typically have columns like:
        [Reference, Description, Department, Closing Date, ...]
        """
        if len(cells) < 2:
            return

        # Look for a cell that looks like a reference number
        ref_number = None
        description = ""
        department = ""
        closing_date = ""
        value_text = ""

        for cell in cells:
            # Reference number patterns: alphanumeric with slashes/hyphens
            if not ref_number and re.match(
                r"^[A-Z]{1,10}[\s/\-]?\d{1,4}[\s/\-]?\d{0,4}",
                cell.strip(), re.I,
            ):
                ref_number = cell.strip()[:80]
            # Date patterns
            elif not closing_date and _extract_date(cell):
                closing_date = _extract_date(cell)
            # Value patterns (R1,000,000 or R 1 000 000)
            elif not value_text and re.search(
                r"R\s*[\d,.\s]+(?:million|billion)?", cell, re.I,
            ):
                value_text = cell.strip()[:60]
            # Department: look for "Department of" or known dept names
            elif not department and re.search(
                r"(?:department|dept|ministry|province|municipal|metro)",
                cell, re.I,
            ):
                department = cell.strip()[:120]
            # Longest remaining cell is likely the description
            elif len(cell) > len(description):
                description = cell.strip()

        if not description and not ref_number:
            return

        self.tenders.append({
            "reference":    ref_number or "",
            "description":  description[:500],
            "department":   department,
            "closing_date": closing_date or "",
            "value_text":   value_text,
            "source_url":   "",  # filled later
        })


class _LinkExtractor(HTMLParser):
    """Extract all <a> tags with their href and inner text."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
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
        pass


# =============================================================================
#  DATE EXTRACTION
# =============================================================================

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_RE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DATE_RE_DMY = re.compile(
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)
_DATE_RE_MDY = re.compile(
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)


def _extract_date(text: str) -> str | None:
    """
    Try to extract a date from text.
    Returns 'YYYY-MM-DD HH:MM:SS' or None.
    """
    if not text:
        return None

    m = _DATE_RE_ISO.search(text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    m = _DATE_RE_DMY.search(text)
    if m:
        try:
            d = int(m.group(1))
            mo = _MONTH_MAP.get(m.group(2).lower())
            y = int(m.group(3))
            if mo:
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    m = _DATE_RE_MDY.search(text)
    if m:
        try:
            mo = _MONTH_MAP.get(m.group(1).lower())
            d = int(m.group(2))
            y = int(m.group(3))
            if mo:
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    return None


# =============================================================================
#  FETCH HELPERS
# =============================================================================

def _fetch_page(url: str) -> str | None:
    """
    Fetch a URL using urllib.request with a realistic User-Agent.
    Returns decoded HTML string or None on failure.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
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
        print(f"  [treasury] HTTP {exc.code} fetching {url}")
        return None
    except URLError as exc:
        print(f"  [treasury] URL error fetching {url}: {exc.reason}")
        return None
    except Exception as exc:
        print(f"  [treasury] Fetch error {url}: {exc}")
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
    if base_url.endswith("/"):
        return base_url + href
    return base_url.rsplit("/", 1)[0] + "/" + href


def _is_page_substantive(html: str) -> bool:
    """
    Check if the fetched HTML has enough textual content to be useful.
    JS-rendered SPAs often return a near-empty shell.
    """
    if not html:
        return False
    # Strip tags and check remaining text length
    text = re.sub(r"<[^>]*>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    # If the page has fewer than 500 chars of actual text, it is likely
    # a JS-rendered shell that didn't hydrate
    return len(text) > 500


# =============================================================================
#  TENDER EXTRACTION
# =============================================================================

def _extract_tenders_from_table(html: str, page_url: str) -> list[dict]:
    """
    Parse an HTML page for table-based tender listings.
    Returns list of tender dicts extracted from <table> rows.
    """
    parser = _TenderTableParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    for tender in parser.tenders:
        tender["source_url"] = page_url

    return parser.tenders


def _extract_tenders_from_links(html: str, page_url: str) -> list[dict]:
    """
    Fallback: extract tender-like links from a page.
    Used when the portal doesn't present tenders in tables.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass

    tenders = []
    seen = set()

    # Patterns that suggest a link points to a tender document
    tender_link_re = re.compile(
        r"tender|bid|rfp|rfq|rfi|procurement|quot|supply\s+chain|"
        r"advertise|award|contract|SCM|B/",
        re.IGNORECASE,
    )

    for href, text in parser.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        clean_text = text.strip()
        if len(clean_text) < 10:
            continue

        combined = f"{href} {clean_text}"
        if not tender_link_re.search(combined):
            continue

        full_url = _resolve_url(href, page_url)
        if full_url in seen:
            continue
        seen.add(full_url)

        # Try to extract a reference number from the text
        ref_match = re.search(
            r"([A-Z]{1,10}[\s/\-]?\d{1,4}[\s/\-]?\d{0,4}[\s/\-]?\d{0,4})",
            clean_text,
        )
        ref_number = ref_match.group(1).strip()[:80] if ref_match else ""

        tenders.append({
            "reference":    ref_number,
            "description":  clean_text[:500],
            "department":   "",
            "closing_date": _extract_date(clean_text) or "",
            "value_text":   "",
            "source_url":   full_url,
        })

    return tenders


def _classify_category(text: str) -> str:
    """Classify a tender description into a procurement category."""
    for category, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(text):
            return category
    return "general"


def _classify_stream(text: str) -> str:
    """Route tender to INFRASTRUCTURE or GLOBAL based on keywords."""
    if INFRASTRUCTURE_KEYWORDS.search(text):
        return "INFRASTRUCTURE"
    return "GLOBAL"


def _parse_value_estimate(value_text: str) -> str | None:
    """
    Try to extract a numeric value from South African Rand notation.
    Returns a human-readable string or None.
    """
    if not value_text:
        return None

    # Match "R 1,234,567" or "R1 234 567" or "R1234567"
    m = re.search(r"R\s*([\d,.\s]+)", value_text, re.I)
    if not m:
        return None

    raw = m.group(1).replace(",", "").replace(" ", "").replace(".", "")
    try:
        val = int(raw)
        if val > 0:
            if val >= 1_000_000_000:
                return f"R{val / 1_000_000_000:.1f}B"
            elif val >= 1_000_000:
                return f"R{val / 1_000_000:.1f}M"
            elif val >= 1_000:
                return f"R{val / 1_000:.0f}K"
            return f"R{val:,}"
    except (ValueError, OverflowError):
        pass

    return value_text.strip()[:40]


# =============================================================================
#  SIGNAL BUILDER
# =============================================================================

def _build_signal(tender: dict) -> dict:
    """
    Convert an extracted tender dict into a FORGE signal dict.
    """
    ref = tender.get("reference", "").strip()
    description = sanitize_text(tender.get("description", ""))
    department = sanitize_text(tender.get("department", ""))
    closing_date = tender.get("closing_date", "")
    value_text = tender.get("value_text", "")
    source_url = tender.get("source_url", "")

    # Build stable external ID
    if ref:
        external_id = f"treasury:{ref}"
    else:
        # Hash of description + closing date as fallback
        hash_input = f"{description[:200]}|{closing_date}"
        external_id = "treasury:" + hashlib.sha1(
            hash_input.encode("utf-8")
        ).hexdigest()[:16]

    # Stream classification
    combined_text = f"{description} {department} {ref}"
    stream = _classify_stream(combined_text)

    # Category classification
    category = _classify_category(combined_text)

    # Value estimate
    value_estimate = _parse_value_estimate(value_text)

    # Build descriptive content
    parts = []
    if ref:
        parts.append(f"Tender Reference: {ref}")
    if department:
        parts.append(f"Department: {department}")
    if description:
        parts.append(f"Description: {description}")
    if closing_date:
        parts.append(f"Closing Date: {closing_date[:10]}")
    if value_estimate:
        parts.append(f"Estimated Value: {value_estimate}")
    parts.append(f"Category: {category}")
    parts.append(f"Source: {source_url}")

    content = sanitize_text("\n".join(parts))[:CONTENT_CAP]

    # Title
    title_parts = []
    if ref:
        title_parts.append(f"[{ref}]")
    if department:
        title_parts.append(department[:60])
    elif description:
        title_parts.append(description[:80])
    title = sanitize_text(" - ".join(title_parts) if title_parts else description[:120])[:200]

    # Timestamp: use closing date or current time
    timestamp = closing_date or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Metadata
    metadata = {
        "reference_number": ref,
        "department":       department,
        "closing_date":     closing_date,
        "category":         category,
        "value_estimate":   value_estimate,
        "source_url":       source_url,
        "collector":        SOURCE_ID,
    }

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          SOURCE_ID,
        "external_id":     external_id,
        "title":           title,
        "content":         content,
        "lat":             TREASURY_LAT,
        "lng":             TREASURY_LNG,
        "timestamp":       timestamp,
        "status":          "raw",
        "stream":          stream,
        "relevance_score": 1.4,
        "is_priority":     0,
        "source_type":     "live",
        "gravity_score":   0.25,
        "metadata_json":   json.dumps(metadata, ensure_ascii=False),
    }


# =============================================================================
#  DATABASE PERSISTENCE
# =============================================================================

INSERT_SQL = """
    INSERT OR IGNORE INTO signals (
        signal_id, source, external_id, title, content,
        lat, lng, timestamp, status, stream,
        relevance_score, is_priority, metadata_json,
        source_type, gravity_score
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _persist_signals(signals: list[dict]) -> tuple[int, int, int]:
    """
    Write signals to the database.
    Returns (inserted, skipped, errors).
    """
    if not signals:
        return 0, 0, 0

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    inserted = 0
    skipped = 0
    errors = 0
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        for sig in signals:
            try:
                cur = conn.execute(INSERT_SQL, (
                    sig["signal_id"],
                    sig["source"],
                    sig["external_id"],
                    sig["title"],
                    sig["content"],
                    sig["lat"],
                    sig["lng"],
                    sig["timestamp"],
                    sig["status"],
                    sig["stream"],
                    sig["relevance_score"],
                    sig["is_priority"],
                    sig["metadata_json"],
                    sig["source_type"],
                    sig["gravity_score"],
                ))
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                if errors <= 5:
                    print(f"  [treasury] Insert error for {sig.get('external_id', '?')}: {exc}")

        conn.commit()
    finally:
        conn.close()

    return inserted, skipped, errors


# =============================================================================
#  MAIN RUNNER
# =============================================================================

def run(dry_run: bool = False, max_age_days: int = 30) -> dict:
    """
    National Treasury tender collection cycle:
      1. Try each tender URL in order until substantive HTML is fetched
      2. Extract tender records from tables and/or links
      3. Filter by recency (--days flag)
      4. Deduplicate via external_id INSERT OR IGNORE
      5. Report results

    Args:
        dry_run:       If True, print signals without writing to DB.
        max_age_days:  Only insert tenders with dates within this window.

    Returns:
        Summary dict with counts.
    """
    start_ts = datetime.now(timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[treasury] National Treasury Tender Auditor starting")
    print(f"[treasury] DB: {DB_PATH}")
    print(f"[treasury] Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"[treasury] Max age: {max_age_days} days (cutoff: {cutoff_str[:10]})")

    # -- Phase 1: Fetch pages -------------------------------------------------
    all_tenders: list[dict] = []
    pages_tried = 0
    pages_fetched = 0

    for i, url in enumerate(TENDER_URLS):
        if i > 0:
            print(f"[treasury] Waiting {INTER_REQUEST_DELAY}s before next request...")
            time.sleep(INTER_REQUEST_DELAY)

        pages_tried += 1
        print(f"[treasury] Fetching {url}")
        html = _fetch_page(url)

        if html is None:
            print(f"[treasury] No response from {url}")
            continue

        if not _is_page_substantive(html):
            print(f"[treasury] Page returned minimal content (likely JS-rendered SPA)")
            continue

        pages_fetched += 1

        # Try table-based extraction first
        table_tenders = _extract_tenders_from_table(html, url)
        if table_tenders:
            print(f"[treasury] Found {len(table_tenders)} tenders from table parsing at {url}")
            all_tenders.extend(table_tenders)

        # Also try link-based extraction (catches items outside tables)
        link_tenders = _extract_tenders_from_links(html, url)
        if link_tenders:
            print(f"[treasury] Found {len(link_tenders)} tenders from link parsing at {url}")
            all_tenders.extend(link_tenders)

        if not table_tenders and not link_tenders:
            print(f"[treasury] No tender entries found on {url}")

    # Deduplicate by reference number or description hash
    seen_keys: set[str] = set()
    unique_tenders: list[dict] = []
    for tender in all_tenders:
        ref = tender.get("reference", "")
        desc = tender.get("description", "")
        key = ref if ref else hashlib.sha1(desc.encode("utf-8")).hexdigest()[:16]
        if key and key not in seen_keys:
            seen_keys.add(key)
            unique_tenders.append(tender)

    print(f"[treasury] Total unique tenders: {len(unique_tenders)} "
          f"(from {pages_fetched}/{pages_tried} pages)")

    if not unique_tenders:
        print("[treasury] No tenders found. Portal may be down or JS-rendered.")
        print("[treasury] This is normal for gov.za sites -- they restructure frequently.")
        elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
        log_run(
            DB_PATH, SOURCE_ID, "success",
            records_in=0, records_out=0, duration_s=elapsed,
            detail={"pages_tried": pages_tried, "pages_fetched": pages_fetched,
                    "tenders_found": 0},
        )
        return {"inserted": 0, "skipped": 0, "filtered": 0, "errors": 0}

    # -- Phase 2: Build signals and filter by age -----------------------------
    signals: list[dict] = []
    filtered_count = 0
    build_errors = 0

    for tender in unique_tenders:
        try:
            sig = _build_signal(tender)

            # Filter by recency: skip tenders older than max_age_days
            if sig["timestamp"] < cutoff_str:
                filtered_count += 1
                continue

            signals.append(sig)
        except Exception as exc:
            build_errors += 1
            if build_errors <= 5:
                print(f"  [treasury] Error building signal for "
                      f"{tender.get('reference', '?')}: {exc}")

    print(f"[treasury] Signals after age filter: {len(signals)} "
          f"({filtered_count} older than {max_age_days} days, "
          f"{build_errors} build errors)")

    # Stream distribution
    stream_counts: dict[str, int] = {}
    for sig in signals:
        stream_counts[sig["stream"]] = stream_counts.get(sig["stream"], 0) + 1
    for stream, count in sorted(stream_counts.items()):
        print(f"[treasury]   {stream}: {count}")

    # -- Phase 3: Dry run or insert -------------------------------------------
    if dry_run:
        print(f"\n[treasury] DRY RUN -- would insert {len(signals)} signals:")
        for sig in signals[:10]:
            print(f"  {sig['external_id']:40s} | {sig['stream']:15s} | "
                  f"{sig['title'][:60]}")
        if len(signals) > 10:
            print(f"  ... and {len(signals) - 10} more")
        return {
            "inserted": 0,
            "skipped": 0,
            "filtered": filtered_count,
            "errors": build_errors,
            "dry_run": True,
            "would_insert": len(signals),
        }

    # -- Phase 4: Persist to database -----------------------------------------
    inserted, skipped, db_errors = _persist_signals(signals)
    total_errors = build_errors + db_errors

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()

    # -- Summary --------------------------------------------------------------
    print(
        f"[treasury] Complete in {elapsed:.1f}s -- "
        f"+{inserted} new | "
        f"~{skipped} known | "
        f">{filtered_count} filtered | "
        f"x{total_errors} errors"
    )

    # -- Pipeline telemetry ---------------------------------------------------
    log_run(
        DB_PATH, SOURCE_ID, "success" if total_errors == 0 else "error",
        records_in=len(unique_tenders), records_out=inserted, duration_s=elapsed,
        detail={
            "pages_tried": pages_tried,
            "pages_fetched": pages_fetched,
            "tenders_found": len(unique_tenders),
            "filtered": filtered_count,
            "skipped": skipped,
            "stream_counts": stream_counts,
        },
    )

    return {
        "inserted": inserted,
        "skipped": skipped,
        "filtered": filtered_count,
        "errors": total_errors,
    }


# =============================================================================
#  CLI ENTRY POINT
# =============================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FORGE National Treasury Tender Auditor -- scrapes SA "
                    "eTender portals for procurement intelligence.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing to the database.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only insert tenders from the last N days (default: 30).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run(dry_run=args.dry_run, max_age_days=args.days)
    print(json.dumps(result, indent=2))


# -- MEGA RUNNER ADAPTER ------------------------------------------------------
import asyncio as _asyncio


async def async_main(**kwargs):
    """Adapter for tools/mega_ingest.py async dispatch."""
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in treasury_collector.py: {e}")
