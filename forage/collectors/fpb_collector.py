#!/usr/bin/env python3
from __future__ import annotations
"""
FORGE -- FPB Enforcement Tracker  (Case #16 / CSAM Regulatory)
================================================================
Monitors the South African Film and Publications Board (FPB) for
enforcement committee rulings, classification decisions, hearing
schedules, and regulatory actions.

Directly relevant to Case #16 (CSAM / Regulatory enforcement).

Collection strategy
───────────────────
  1. Fetch FPB enforcement and media-centre pages (multiple URL paths
     tried in order; .gov.za sites restructure frequently).
  2. Parse HTML for links to enforcement-related content: PDF links
     (hearing schedules, rulings), and article/post entries.
  3. Polite crawling: 5-second delay between page requests, realistic
     User-Agent, respects failures gracefully.
  4. Each discovered item becomes a CRIME_INTEL signal pinned to FPB HQ
     in Pretoria (-25.7479, 28.2293).

Compliance
──────────
  source = manifest["id"] = "fpb_enforcement"
  stream = CRIME_INTEL
  relevance_score = 1.5 (regulatory enforcement, high analyst value)
  Zero new DB tables. All columns present in Stable 1.1 schema.

Dependencies:  stdlib only (urllib.request, html.parser)
"""

# ── Manifest (AST-parsed by Autodiscovery Registry at boot) ──────────────────
__manifest__ = {
    "id":          "fpb_enforcement",
    "name":        "FPB Enforcement Tracker",
    "description": "Monitors the Film and Publications Board enforcement committee rulings and classification decisions.",
    "icon":        "⚖",
    "entry":       "forage/collectors/fpb_collector.py",
    "args":        [],
    "job_key":     "fpb_enforcement",
    "version":     "1.0.0",
}

# ── Windows CP1252 safety -- reconfigure stdout to UTF-8 before any print() ──
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Standard library ─────────────────────────────────────────────────────────
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

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = (
    Path(os.environ["FORGE_DB"]).resolve()
    if os.environ.get("FORGE_DB")
    else BASE_DIR / "database.db"
)

# Canonical source key -- MUST match manifest["id"] for auto-pin membrane query
SOURCE_ID = "fpb_enforcement"

# ── Refinery (Stable 1.1) ────────────────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t): return t  # noqa: E731

# ── Pipeline logger (path-safe, no hard coupling) ────────────────────────────
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu
    _lp = BASE_DIR / "forage" / "utils" / "pipeline_logger.py"
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_lp))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass

log_run = _log_run_safe

# ── Constants ─────────────────────────────────────────────────────────────────

# FPB HQ, Pretoria
FPB_LAT = -25.7479
FPB_LNG = 28.2293

# URLs to try (FPB site restructures frequently; try multiple paths)
FPB_URLS = [
    "https://www.fpb.org.za/enforcement/",
    "https://www.fpb.org.za/category/enforcement/",
    "https://www.fpb.org.za/media-centre/",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20  # seconds
INTER_REQUEST_DELAY = 5  # seconds between page fetches

# Keywords that signal enforcement-relevant content (case-insensitive)
ENFORCEMENT_KEYWORDS = re.compile(
    r"enforce|ruling|classif|hearing|tribunal|compliance|"
    r"penalty|sanction|prohibit|ban|restrict|appeal|"
    r"csam|child\s+(?:sexual|exploitation|pornograph)|"
    r"online\s+(?:harm|safety|content\s+regulat)|"
    r"internet\s+service\s+provider|ISP\s+blocking|"
    r"take[\s-]?down|committee|gazette|notice|decision",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
#  HTML LINK PARSER
#  Lightweight stdlib-only parser that extracts <a href="..."> links and
#  their text content from an HTML page. No BeautifulSoup dependency.
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
#  DATE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

# Match dates in URLs or text: YYYY-MM-DD, YYYY/MM/DD, DD-Mon-YYYY, etc.
_DATE_PATTERNS = [
    # YYYY-MM-DD or YYYY/MM/DD
    (re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"), "%Y-%m-%d"),
    # DD Month YYYY or DD Mon YYYY
    (re.compile(
        r"(\d{1,2})\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(\d{4})",
        re.IGNORECASE,
    ), None),
    # Month DD, YYYY
    (re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December|"
        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(\d{1,2}),?\s+(\d{4})",
        re.IGNORECASE,
    ), None),
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
    Try to extract a date from text (URL, link text, or surrounding context).
    Returns 'YYYY-MM-DD HH:MM:SS' or None.
    """
    if not text:
        return None

    # Pattern 1: YYYY-MM-DD or YYYY/MM/DD
    m = _DATE_PATTERNS[0][0].search(text)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # Pattern 2: DD Month YYYY
    m = _DATE_PATTERNS[1][0].search(text)
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

    # Pattern 3: Month DD, YYYY
    m = _DATE_PATTERNS[2][0].search(text)
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


# ─────────────────────────────────────────────────────────────────────────────
#  FETCH HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_page(url: str) -> str | None:
    """
    Fetch a URL using urllib.request with a realistic User-Agent.
    Returns decoded HTML string or None on failure.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            # Try to detect encoding from Content-Type header
            ct = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip().split(";")[0]
            try:
                return raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return raw.decode("utf-8", errors="replace")
    except HTTPError as exc:
        print(f"  [fpb] HTTP {exc.code} fetching {url}")
        return None
    except URLError as exc:
        print(f"  [fpb] URL error fetching {url}: {exc.reason}")
        return None
    except Exception as exc:
        print(f"  [fpb] Fetch error {url}: {exc}")
        return None


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve a potentially relative URL against a base URL."""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        # Extract scheme + host from base
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    # Relative path
    if base_url.endswith("/"):
        return base_url + href
    return base_url.rsplit("/", 1)[0] + "/" + href


# ─────────────────────────────────────────────────────────────────────────────
#  ITEM EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_enforcement_relevant(href: str, text: str) -> bool:
    """
    Determine if a link is relevant to FPB enforcement actions.
    Matches PDF documents and enforcement-keyword-bearing pages.
    """
    combined = f"{href} {text}"

    # Always include PDFs from the FPB domain (likely rulings/schedules)
    if href.lower().endswith(".pdf") and "fpb" in href.lower():
        return True

    # Check for enforcement keywords in link text or URL
    if ENFORCEMENT_KEYWORDS.search(combined):
        return True

    return False


def _extract_items_from_page(html: str, page_url: str) -> list[dict]:
    """
    Parse an HTML page and extract enforcement-relevant items.
    Returns a list of dicts: {url, title, date, item_type}.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass

    items = []
    seen_urls = set()

    for href, text in parser.links:
        # Skip empty, anchor-only, mailto, and javascript links
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue

        full_url = _resolve_url(href, page_url)

        # Skip already-seen URLs
        if full_url in seen_urls:
            continue

        # Skip navigation/boilerplate links (very short text, common nav words)
        clean_text = text.strip()
        if len(clean_text) < 5:
            continue
        if clean_text.lower() in (
            "home", "about", "contact", "menu", "search", "login",
            "register", "back", "next", "previous", "more", "read more",
            "skip to content", "close",
        ):
            continue

        # Check enforcement relevance
        if not _is_enforcement_relevant(full_url, clean_text):
            continue

        seen_urls.add(full_url)

        # Determine item type
        item_type = "article"
        if full_url.lower().endswith(".pdf"):
            item_type = "pdf_document"

        # Try to extract a date from URL or link text
        date_str = _extract_date(full_url) or _extract_date(clean_text)

        items.append({
            "url":       full_url,
            "title":     clean_text,
            "date":      date_str,
            "item_type": item_type,
        })

    return items


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_signal(item: dict) -> dict:
    """
    Convert an extracted FPB item into a FORGE signal dict.
    """
    url = item["url"]
    title = _sanitize(item["title"])[:400]

    # Build descriptive content
    item_type_label = (
        "PDF document (likely ruling, hearing schedule, or gazette notice)"
        if item["item_type"] == "pdf_document"
        else "enforcement-related page or article"
    )
    content = _sanitize(
        f"FPB enforcement action: {title}. "
        f"Source type: {item_type_label}. "
        f"Source URL: {url}"
    )

    # Timestamp: use extracted date or current time
    timestamp = item.get("date") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Stable external ID: "fpb:" + sha1(url)[:16]
    external_id = "fpb:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          SOURCE_ID,
        "external_id":     external_id,
        "title":           title,
        "content":         content,
        "lat":             FPB_LAT,
        "lng":             FPB_LNG,
        "timestamp":       timestamp,
        "status":          "raw",
        "stream":          "CRIME_INTEL",
        "relevance_score": 1.5,
        "is_priority":     0,
        "source_type":     "live",
        "metadata_json":   json.dumps({
            "source_url":  url,
            "item_type":   item["item_type"],
            "collector":   SOURCE_ID,
            "case_hint":   "case_16_csam_regulatory",
        }),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, max_age_days: int = 30) -> dict:
    """
    FPB enforcement collection cycle:
      1. Try each FPB URL in order until pages are fetched
      2. Extract enforcement-relevant items from fetched HTML
      3. Filter by recency (--days flag)
      4. Deduplicate via external_id INSERT OR IGNORE
      5. Report results

    Args:
        dry_run:       If True, print signals without writing to DB.
        max_age_days:  Only insert items with dates within this window (default 30).

    Returns:
        Summary dict with counts.
    """
    start_ts = datetime.now(timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[fpb] FPB Enforcement Tracker starting")
    print(f"[fpb] DB: {DB_PATH}")
    print(f"[fpb] Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"[fpb] Max age: {max_age_days} days (cutoff: {cutoff_str[:10]})")

    # ── Phase 1: Fetch pages ──────────────────────────────────────────────────
    all_items: list[dict] = []

    for i, url in enumerate(FPB_URLS):
        if i > 0:
            print(f"[fpb] Waiting {INTER_REQUEST_DELAY}s before next request...")
            time.sleep(INTER_REQUEST_DELAY)

        print(f"[fpb] Fetching {url}")
        html = _fetch_page(url)
        if html is None:
            print(f"[fpb] No response from {url}")
            continue

        items = _extract_items_from_page(html, url)
        print(f"[fpb] Found {len(items)} enforcement-relevant items from {url}")
        all_items.extend(items)

    # Deduplicate by URL across pages
    seen_urls = set()
    unique_items: list[dict] = []
    for item in all_items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_items.append(item)

    print(f"[fpb] Total unique items: {len(unique_items)}")

    if not unique_items:
        print("[fpb] No enforcement items found. FPB site may have restructured.")
        elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
        log_run(
            collector=SOURCE_ID,
            new_signals=0,
            errors=0,
            runtime_seconds=elapsed,
            meta={"pages_tried": len(FPB_URLS), "items_found": 0},
        )
        return {"inserted": 0, "skipped": 0, "filtered": 0, "errors": 0}

    # ── Phase 2: Build signals and filter by age ─────────────────────────────
    signals: list[dict] = []
    filtered_count = 0

    for item in unique_items:
        try:
            sig = _build_signal(item)

            # Filter by recency: skip items older than max_age_days
            if sig["timestamp"] < cutoff_str:
                filtered_count += 1
                continue

            signals.append(sig)
        except Exception as exc:
            print(f"  [fpb] Error building signal for {item.get('url', '?')}: {exc}")

    print(f"[fpb] Signals after age filter: {len(signals)} "
          f"({filtered_count} older than {max_age_days} days)")

    # ── Phase 3: Dry run or insert ────────────────────────────────────────────
    if dry_run:
        print(f"\n[fpb] DRY RUN — would insert {len(signals)} signals:")
        for sig in signals:
            print(f"  {sig['external_id']}  {sig['title'][:80]}")
            print(f"    timestamp={sig['timestamp']}  stream={sig['stream']}")
        return {
            "inserted": 0,
            "skipped": 0,
            "filtered": filtered_count,
            "errors": 0,
            "dry_run": True,
            "would_insert": len(signals),
        }

    # ── DB connection ─────────────────────────────────────────────────────────
    db_path = str(DB_PATH)
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        inserted = 0
        skipped = 0
        errors = 0

        for sig in signals:
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO signals
                        (signal_id, source, external_id, title, content,
                         lat, lng, timestamp, status, stream,
                         relevance_score, is_priority, metadata_json,
                         source_type)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
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
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:
                print(f"  [fpb] Insert error for {sig['external_id']}: {exc}")
                errors += 1

        conn.commit()
    finally:
        conn.close()

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(
        f"[fpb] Complete in {elapsed:.1f}s — "
        f"+{inserted} new | "
        f"~{skipped} known | "
        f">{filtered_count} filtered | "
        f"x{errors} errors"
    )

    # ── Pipeline telemetry ────────────────────────────────────────────────────
    log_run(
        collector=SOURCE_ID,
        new_signals=inserted,
        errors=errors,
        runtime_seconds=elapsed,
        meta={
            "pages_tried": len(FPB_URLS),
            "items_found": len(unique_items),
            "filtered": filtered_count,
            "skipped": skipped,
        },
    )

    return {
        "inserted": inserted,
        "skipped": skipped,
        "filtered": filtered_count,
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FORGE FPB Enforcement Tracker — monitors SA Film and "
                    "Publications Board enforcement actions.",
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
        help="Only insert items from the last N days (default: 30).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run(dry_run=args.dry_run, max_age_days=args.days)
    print(json.dumps(result, indent=2))


# ── MEGA RUNNER ADAPTER ──────────────────────────────────────────────────────
import asyncio as _asyncio


async def async_main(**kwargs):
    """Adapter for tools/mega_ingest.py async dispatch."""
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in fpb_collector.py: {e}")
