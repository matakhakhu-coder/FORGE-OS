# -*- coding: utf-8 -*-
from __future__ import annotations
"""
FORGE -- PDF Infiltrator  (forage/collectors/pdf_infiltrator.py)
================================================================
Phase 43 → 48: Government document intelligence pipeline.

Phase 48 changes (Evidence Closure Pipeline):
  P1-01  Persistent no-intel cache: PDFs that yield no extractable text or
         structured intel are recorded in artifacts (processing_status=
         'no_text'/'no_intel') so they are never re-downloaded on future runs.
  P1-08  _insert_artifact() fully rewritten to use the correct artifacts schema
         (source=URL, file_path, source_type='pdf_portal', raw_text_cache).
         Returns artifact_id; updates signals.source_artifact_id for provenance.
  P2-01  Amount regex hardened: bare 'b' suffix removed (Trillion hallucination
         fix); only 'billion', 'bn', 'million', 'm' accepted. Values normalised
         to rand millions; hallucination guard rejects >R999 billion.
  P2-02  Award pattern expanded: "selected as contractor", "selected as service
         provider", "preferred bidder", "appointed contractor".
  P2-03  Departments field enabled in extraction payload tags_json so every
         artifact record carries its originating government branch.
         _DEPT_PATTERN expanded to cover Provincial, Office of the Premier/DG.
  P2-04  OCR bridge integrated: pdfplumber text-layer is attempted first;
         if <50 chars extracted (scanned/image-based PDF), pytesseract runs
         against the first 3 pages via pdf2image. OCR text flows into
         raw_text_cache so triple_extractor can build evidence relationships.
         --reprocess-vault CLI mode re-extracts all saved PDFs in media/documents/
         to back-fill empty raw_text_cache entries and create missing signals.

Modes:
    default   -- Follow Google News RSS dork signals → article pages → PDFs
    --portals -- Crawl known SA government portals directly (HTML + sitemap)

Pipeline (per PDF):
    URL source
      --> artifact cache check (skip if previously tried)
      --> HTTP (Chrome UA, tuple timeout, ReadTimeout shield)
      --> BeautifulSoup / sitemap XML → PDF links
      --> stream-download → MEDIA_DIR physical save
      --> pdfplumber (explicit close + gc.collect)
      --> regex: Tender No, R amount, awardee, dept
      --> signals INSERT + artifacts INSERT (file_path + raw_text_cache)

Physical storage: FORGE/media/documents/[Portal]_[SigID8]_[Title].pdf
Provenance chain: signals.source_artifact_id → artifacts.artifact_id

Dependencies:
    pip install pdfplumber beautifulsoup4 requests

Author: FORGE Phase 47
"""

__manifest__ = {
    "id":          "pdf_infiltrator",
    "name":        "PDF Infiltrator",
    "description": "Government document intelligence pipeline. Crawls NPA, SIU, National Treasury, and AGSA portals for new PDFs. Runs OCR on scanned documents and feeds raw text into the artifact queue.",
    "icon":        "📄",
    "entry":       "forage/collectors/pdf_infiltrator.py",
    "args":        [],
    "job_key":     "pdf_infiltrator",
    "version":     "1.0.0",
}

import gc
import hashlib
import io
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

BASE_DIR  = Path(__file__).resolve().parent.parent.parent
# P3-01: honour FORGE_DB env var; fall back to repo-root default
import os as _os
DB_PATH   = Path(_os.environ["FORGE_DB"]).resolve() if _os.environ.get("FORGE_DB") else BASE_DIR / "database.db"
MEDIA_DIR = BASE_DIR / "media" / "documents"   # organised under media/documents/


# ---------------------------------------------------------------------------
# Extraction patterns — SA government procurement
# ---------------------------------------------------------------------------

_TENDER_PATTERNS = [
    re.compile(r"(?:Tender\s*(?:No\.?|Number|#)\s*:?\s*)"
               r"([A-Z0-9]{2,10}[/-]\d{2,4}[/-][A-Z0-9]{1,10})", re.I),
    re.compile(r"\b(RFP[-\s]?\d{3,})\b", re.I),
    re.compile(r"\b(SCM[/-]\d{3,}[/-]\d{4})\b", re.I),
    re.compile(r"(?:Bid\s*(?:No\.?|Number)\s*:?\s*)(\d{4}[/-][A-Z0-9]{3,})", re.I),
    re.compile(r"(?:Quotation\s*(?:Ref\.?|Number)\s*:?\s*)([A-Z0-9]{3,}[/-]\d{2,})", re.I),
    re.compile(r"\b([A-Z]{2,6}/\d{4}/\d{2,6})\b"),   # generic DEPT/YYYY/NNN
]

# P2-01 (Phase 48 / Path B): hardened amount pattern.
# Handles: R4.5m (no space), R4.5bn, R4,5 million (comma decimal),
# R4 500 million (space thousands sep).  Suffix space is now optional (\s*).
# Suffix order: longest first so 'billion' beats 'bn', 'million' beats 'm'.
# 'b' suffix REMOVED — non-standard in SA procurement writing and a source of
# "trillion-range" hallucinations when matched against unrelated text.
# Hallucination guard applied in _parse_intelligence: values > R999 billion
# (999_000 million) are rejected.
_AMOUNT_PATTERN = re.compile(
    r"R\s*"
    r"(\d{1,3}(?:[\s,]\d{3})*(?:[.,]\d{1,2})?)"   # integer + optional decimal
    r"\s*"                                            # optional space before suffix
    r"(billion|million|bn|m)"                        # suffix — no bare 'b'
    r"\b",
    re.I,
)

# P2-02 (Path B): expanded trigger phrases — covers bureaucratic SA procurement
# language including bidder/contractor/provider appointment variants.
_AWARD_PATTERN = re.compile(
    r"(?:awarded?\s+to"
    r"|appointed\s+(?:as\s+)?(?:service\s+provider|contractor)"
    r"|appointed\s+contractor"          # bare form without 'as'
    r"|selected\s+(?:as\s+)?(?:bidder|contractor|service\s+provider)"
    r"|winning\s+bidder"
    r"|preferred\s+(?:service\s+)?provider"
    r"|preferred\s+bidder"
    r"|contractor"
    r"|service\s+provider"
    r"|supplier)"
    r"\s*:?\s*([A-Z][A-Za-z\s&()\-]{3,60}?)(?:\.|,|\n|$)",
    re.I,
)

# P2-03 (Path B): expanded department detection — Provincial prefix, Office of
# the Premier/President/DG/Director-General, and common SA department names.
# Outer group is capturing (group 1) to match _parse_intelligence's m.group(1).
_DEPT_PATTERN = re.compile(
    r"("
    r"(?:Provincial\s+)?Department\s+of\s+[A-Z][A-Za-z\s&]{3,60}"
    r"|Ministry\s+of\s+[A-Z][A-Za-z\s]{3,50}"
    r"|Office\s+of\s+the\s+(?:Premier|President|Director.General|DG|"
    r"Auditor.General|Public\s+Protector|Accountant.General)"
    r"|(?:DPWI|DPSA|COGTA|DRDLR|DHS|DALRRD|DCOG|NPA|SIU|NT|AGSA|SAPS)"
    r")",
    re.I,
)

_GOV_DOMAINS = re.compile(r"\.gov\.za", re.I)
_PDF_HREF    = re.compile(r"\.pdf(\?[^\"']*)?$", re.I)

# P2-09: per-portal URL exclusion patterns.
# NPA legislation page surfaces statutory acts from justice.gov.za (7 of 11 PDFs
# irrelevant to investigative mandate). Exclude those URL prefixes.
_PORTAL_URL_EXCLUDES: dict[str, list[str]] = {
    "NPA_legis": ["justice.gov.za/legislation/acts/"],
}

_REQUEST_DELAY = 2.0   # seconds between downloads
_MAX_PDF_MB    = 15    # skip PDFs larger than this
_MAX_PAGES     = 20    # read at most N pages per PDF
_MAX_PER_RUN   = 50    # cap PDFs processed per run


# ---------------------------------------------------------------------------
# Portal targets — (label, url, gov_only, mode)
# mode: 'page' = BeautifulSoup HTML scan | 'sitemap' = XML sitemap pivot
# URLs verified live April 2026.
# ---------------------------------------------------------------------------

_PORTAL_TARGETS: List[tuple] = [
    # (label, url, gov_only, mode, require_intel)
    # require_intel=False: write signal for any PDF with extractable text ≥200 chars,
    #   even without tender refs / amounts. Use for investigation/narrative portals
    #   so their prose reaches raw_text_cache → triple_extractor.
    # require_intel=True (default): only write signals with structured procurement data.

    # SIU — investigation narratives; no tender refs expected
    ("SIU",           "https://www.siu.org.za/",                                          False, "sitemap2hop", False),
    # NPA — annual reports and legislation; investigation content, not procurement
    ("NPA_annual",    "https://www.npa.gov.za/annual-reports",                            True,  "page",        False),
    ("NPA_legis",     "https://www.npa.gov.za/legislation",                               True,  "page",        False),
    # NT — procurement-structured PDFs; require tender refs
    # NT_restricted removed (P3-06): NT_tender page scrape already finds
    # RestrictedSuppliersReport.pdf as a link — direct target was redundant.
    ("NT_tender",     "https://www.treasury.gov.za/tenderinfo/default.aspx",              True,  "page",        True),
    ("NT_defaulters", "https://www.treasury.gov.za/publications/other/Register%20for%20Tender%20Defaulters.pdf", True, "direct", True),
    # AGSA — audit reports; require structured findings
    ("AGSA_pfma",     "https://www.agsa.co.za/Reporting/PFMAReports.aspx",                False, "page",        True),
    ("AGSA_hub",      "https://www.agsareports.co.za/",                                   False, "page",        True),
    # SAPS removed — connection timeout (firewall)
]


# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

def _migrate_media_root(
    db_path: Path,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Startup hygiene: move any PDFs sitting in media/ root into media/documents/
    and update their file_path in the artifacts table so links don't break.
    Runs silently — does not abort the pipeline on failure.

    P1-02: accepts an optional open connection to avoid opening a new
    SQLite connection per migrated file when called from within a run.
    """
    import shutil
    media_root = BASE_DIR / "media"
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    _own_conn = conn is None  # True → we must open and close our own connection

    moved = 0
    for pdf_file in media_root.glob("*.pdf"):
        dest = MEDIA_DIR / pdf_file.name
        try:
            shutil.move(str(pdf_file), str(dest))
            moved += 1
            # Update artifacts table — reuse caller's connection when available
            try:
                _conn = conn if conn is not None else sqlite3.connect(str(db_path), timeout=10)
                _conn.execute(
                    "UPDATE artifacts SET file_path=? WHERE file_path=?",
                    (str(dest), str(pdf_file))
                )
                if _own_conn:
                    _conn.commit()
                    _conn.close()
            except Exception:
                pass  # DB update non-fatal
        except Exception as exc:
            log(f"WARN migrate {pdf_file.name}: {exc}")

    if moved:
        log(f"Migrated {moved} PDF(s) from media/ root → media/documents/")


def _check_deps() -> tuple[bool, bool]:
    """Returns (has_pdfplumber, has_requests)."""
    try:
        import pdfplumber  # noqa: F401
        has_pdf = True
    except ImportError:
        has_pdf = False
    try:
        import requests    # noqa: F401
        has_req = True
    except ImportError:
        has_req = False
    return has_pdf, has_req


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"),
              flush=True)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    _safe_print(f"[{_ts()}] [pdf_infiltrator] {msg}")


# ---------------------------------------------------------------------------
# HTTP session — Chrome UA, tuple timeouts, SSL bypass
# ---------------------------------------------------------------------------

# P3-02: UA rotation pool — never self-identify; cycle through realistic
# desktop browser strings to reduce WAF/CDN fingerprint risk.
_UA_POOL = [
    # Chrome 124 Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36"),
    # Chrome 123 macOS
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/123.0.0.0 Safari/537.36"),
    # Firefox 125 Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
    # Firefox 124 Linux
    ("Mozilla/5.0 (X11; Linux x86_64; rv:124.0) "
     "Gecko/20100101 Firefox/124.0"),
    # Edge 124 Windows
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    # Safari 17 macOS
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
     "Version/17.4.1 Safari/605.1.15"),
]

_SESSION = None


def _get_session():
    global _SESSION
    if _SESSION is None:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _SESSION = requests.Session()
        _SESSION.verify = False
        _SESSION.headers.update({
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-ZA,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
    # P3-02: rotate UA on every session retrieval so consecutive portal
    # requests don't share the same fingerprint.
    import random
    _SESSION.headers["User-Agent"] = random.choice(_UA_POOL)
    return _SESSION


# ---------------------------------------------------------------------------
# Task 1: Physical PDF storage
# ---------------------------------------------------------------------------

def _sanitize_filename(s: str) -> str:
    """Strip characters illegal in Windows filenames."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f%\s]+', '_', s)
    return s[:80].strip('_.')


def _save_pdf_local(pdf_bytes: bytes, label: str,
                    sig_id: str, title: str) -> Optional[Path]:
    """
    Save PDF to MEDIA_DIR/[label]_[sig_id8]_[title].pdf.
    Returns the Path on success, None on failure.
    """
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        safe_label = _sanitize_filename(label)
        safe_title = _sanitize_filename(title)
        sig_short  = (sig_id or "unknown")[:8]
        filename   = f"{safe_label}_{sig_short}_{safe_title}.pdf"
        dest       = MEDIA_DIR / filename
        dest.write_bytes(pdf_bytes)
        log(f"  Saved: media/{filename}")
        return dest
    except Exception as exc:
        log(f"  WARN local save failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# HTTP helpers — resilient fetch, redirect resolution, page fetch, PDF download
# ---------------------------------------------------------------------------

def _resilient_get(url: str, *, timeout=(5, 30), stream: bool = False,
                   max_retries: int = 3, backoff: float = 2.0):
    """
    P3-04: Retry wrapper around session.get() for transient failures.

    Retries on: ConnectTimeout, ReadTimeout, ConnectionError, 429, 503.
    Does NOT retry on 4xx client errors (except 429) or permanent failures.
    Exponential backoff: 2s → 4s → 8s between attempts.

    Returns: requests.Response on success.
    Raises: the last exception if all retries are exhausted.
    """
    import time as _time
    import requests as _req
    sess = _get_session()
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(max_retries):
        try:
            r = sess.get(url, timeout=timeout, stream=stream)
            if r.status_code in (429, 503) and attempt < max_retries - 1:
                wait = backoff ** (attempt + 1)
                log(f"  [retry] HTTP {r.status_code} on {url[:60]} — retrying in {wait:.0f}s")
                _time.sleep(wait)
                continue
            return r
        except (_req.exceptions.ConnectTimeout,
                _req.exceptions.ReadTimeout,
                _req.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = backoff ** (attempt + 1)
                log(f"  [retry] {type(exc).__name__} on {url[:60]} — retrying in {wait:.0f}s")
                _time.sleep(wait)
            else:
                raise
    raise last_exc

def _resolve_google_news_url(rss_url: str) -> Optional[str]:
    """Follow Google News RSS redirect to get the real article URL."""
    import requests
    log(f"Resolving: {rss_url[:60]}...")
    try:
        sess = _get_session()
        r = sess.get(rss_url, timeout=(5, 10), allow_redirects=True)
        final = r.url
        if "news.google.com" in final:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.content, "html.parser")
            canon = soup.find("link", rel="canonical")
            if canon and canon.get("href"):
                return canon["href"]
            og = soup.find("meta", property="og:url")
            if og and og.get("content"):
                return og["content"]
            return None
        return final
    except requests.exceptions.ReadTimeout:
        log(f"WARN: Redirect Timeout on {rss_url[:60]}. Skipping.")
        return None
    except requests.exceptions.ChunkedEncodingError:
        log(f"WARN: Redirect Timeout on {rss_url[:60]}. Skipping.")
        return None
    except Exception as exc:
        log(f"WARN redirect resolution failed: {exc}")
        return None


def _find_pdf_links(page_url: str, timeout: int = 12) -> List[str]:
    """Scrape a page for .gov.za PDF hrefs."""
    try:
        from bs4 import BeautifulSoup
        r = _resilient_get(page_url, timeout=(5, timeout))  # P3-04: retry on transient errors
        soup = BeautifulSoup(r.content, "html.parser")
        pdf_links: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if not _PDF_HREF.search(href):
                continue
            pdf_links.append(urljoin(page_url, href))
        pdf_links.sort(key=lambda u: (0 if _GOV_DOMAINS.search(u) else 1))
        return pdf_links[:5]
    except Exception as exc:
        log(f"  WARN page fetch failed for {page_url[:60]}: {exc}")
        return []


def _download_pdf(pdf_url: str, timeout: int = 30) -> Optional[bytes]:
    """Stream-download a PDF with size cap. Returns bytes or None.

    P1-03: Streams to a tempfile on disk instead of holding the full
    download buffer AND the pdfplumber buffer simultaneously in RAM.
    Peak RAM drops from ~30-40 MB per PDF to ~pdfplumber working set only.

    P3-04: uses _resilient_get() for automatic retry on transient failures.
    """
    import tempfile
    try:
        r = _resilient_get(pdf_url, timeout=(5, timeout), stream=True)
        r.raise_for_status()
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > _MAX_PDF_MB * 1024 * 1024:
            log(f"  SKIP oversized PDF ({int(content_length)//1024//1024}MB)")
            return None
        # P1-03: write to temp file — avoids holding download + pdfplumber
        # buffers in RAM simultaneously (~30-40 MB peak → pdfplumber only)
        downloaded = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            for chunk in r.iter_content(chunk_size=65536):
                downloaded += len(chunk)
                if downloaded > _MAX_PDF_MB * 1024 * 1024:
                    log(f"  SKIP PDF exceeded {_MAX_PDF_MB}MB limit mid-download")
                    try:
                        import os as _os; _os.unlink(tmp_path)
                    except Exception:
                        pass
                    return None
                tmp.write(chunk)
        # Read back from disk — download buffer is now released
        try:
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                import os as _os; _os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as exc:
        log(f"  WARN PDF download failed (all retries exhausted): {exc}")
        return None


# ---------------------------------------------------------------------------
# Task 2: Sitemap pivot — bypasses JS-rendered portals (SIU, NPA)
# ---------------------------------------------------------------------------

def _fetch_sitemap_xml(url: str) -> Optional[ET.Element]:
    """Fetch and parse a sitemap XML URL. Returns root element or None.
    P3-04: uses _resilient_get() for automatic retry on transient failures.
    """
    try:
        r = _resilient_get(url, timeout=(5, 15))
        if r.status_code != 200:
            return None
        if b"<" not in r.content[:100]:
            return None
        return ET.fromstring(r.content)
    except Exception:
        return None


def _sitemap_page_urls(root: ET.Element) -> List[str]:
    """Extract all <loc> page URLs from a regular sitemap (not index)."""
    NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = root.findall(".//sm:url/sm:loc", NS) or root.findall(".//url/loc")
    return [(loc.text or "").strip() for loc in locs if loc.text]


def _crawl_sitemap(base_url: str, max_pdfs: int = 20,
                   _depth: int = 0) -> List[str]:
    """
    Discover sitemap.xml from base_url and extract direct PDF <loc> entries.
    Sub-sitemap URLs are fetched DIRECTLY (not with /sitemap.xml appended).
    Depth-2 recursion handles sitemap index files.
    Returns list of PDF URLs only — page URLs are ignored here (handled in 2-hop mode).
    """
    if _depth > 2:
        return []

    NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    # For root discovery: try known sitemap filenames under base_url.
    # For recursive sub-map fetches: the full URL is passed directly.
    if base_url.endswith(".xml"):
        # Direct sub-sitemap URL — fetch as-is
        candidates = [base_url]
    else:
        candidates = [
            base_url.rstrip("/") + "/sitemap.xml",
            base_url.rstrip("/") + "/sitemap_index.xml",
            base_url.rstrip("/") + "/wp-sitemap.xml",
        ]

    for sitemap_url in candidates:
        root = _fetch_sitemap_xml(sitemap_url)
        if root is None:
            continue

        # Sitemap index — sub-maps listed as <sitemap><loc>
        sub_locs = root.findall(".//sm:sitemap/sm:loc", NS)
        if not sub_locs:
            sub_locs = root.findall(".//sitemap/loc")

        if sub_locs:
            log(f"  Sitemap index: {len(sub_locs)} sub-maps at {sitemap_url}")
            pdf_urls: List[str] = []
            for sm_loc in sub_locs[:10]:
                sub_url = (sm_loc.text or "").strip()
                if not sub_url:
                    continue
                # Recurse with direct URL — NOT base discovery
                pdf_urls.extend(
                    _crawl_sitemap(sub_url, max_pdfs - len(pdf_urls),
                                   _depth=_depth + 1)
                )
                if len(pdf_urls) >= max_pdfs:
                    break
            return pdf_urls[:max_pdfs]

        # Regular sitemap — look for direct .pdf <loc> entries
        all_locs = root.findall(".//sm:url/sm:loc", NS) or root.findall(".//url/loc")
        pdf_urls = []
        for loc in all_locs:
            url = (loc.text or "").strip()
            if url and _PDF_HREF.search(url):
                pdf_urls.append(url)
            if len(pdf_urls) >= max_pdfs:
                break

        if pdf_urls:
            log(f"  Sitemap: {len(pdf_urls)} direct PDF URL(s) at {sitemap_url}")
        return pdf_urls

    return []


def _crawl_sitemap_2hop(base_url: str, max_pages: int = 30,
                        max_pdfs: int = 10) -> List[str]:
    """
    2-hop variant for JS portals (SIU): discover page URLs via sitemap,
    then scrape each page for PDF <a href> links.
    Serial version — kept for non-async callers.
    See _crawl_sitemap_2hop_async for the concurrent version used in mega_ingest.
    """
    NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    candidates = [
        base_url.rstrip("/") + "/sitemap.xml",
        base_url.rstrip("/") + "/wp-sitemap.xml",
    ]

    page_urls: List[str] = []
    for sitemap_url in candidates:
        root = _fetch_sitemap_xml(sitemap_url)
        if root is None:
            continue

        # Collect sub-map URLs for content-type sitemaps
        sub_locs = root.findall(".//sm:sitemap/sm:loc", NS) or root.findall(".//sitemap/loc")
        for sm_loc in sub_locs[:12]:
            sub_url = (sm_loc.text or "").strip()
            if not sub_url:
                continue
            sub_root = _fetch_sitemap_xml(sub_url)
            if sub_root is None:
                continue
            for loc in (sub_root.findall(".//sm:url/sm:loc", NS)
                        or sub_root.findall(".//url/loc")):
                url = (loc.text or "").strip()
                if url:
                    page_urls.append(url)
                if len(page_urls) >= max_pages:
                    break
            if len(page_urls) >= max_pages:
                break
        if page_urls:
            break

    if not page_urls:
        return []

    log(f"  2-hop: scraped {len(page_urls)} page URL(s) from sitemap — scanning for PDFs")
    pdf_urls: List[str] = []
    for page_url in page_urls:
        if len(pdf_urls) >= max_pdfs:
            break
        links = _find_pdf_links(page_url, timeout=10)
        pdf_urls.extend(links)
        time.sleep(2.0)   # Thermal Guard: 2s between page scrapes (CPU temperature)

    return pdf_urls[:max_pdfs]


async def _crawl_sitemap_2hop_async(base_url: str, max_pages: int = 30,
                                    max_pdfs: int = 10) -> List[str]:
    """
    C-2: Async 2-hop sitemap crawl for JS portals (SIU).

    Phase 1: Discover page URLs from sitemap (serial — XML fetches are fast).
    Phase 2: Scrape each page for PDF links CONCURRENTLY via asyncio + ThreadPool.

    This replaces the ~80-second serial loop with a concurrent fan-out.
    Each page scrape is I/O-bound (HTTP GET), so thread-pool parallelism
    captures the full speedup without adding aiohttp as a dependency.
    A semaphore limits concurrent connections to 5 (polite crawling).
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    # Phase 1: sitemap discovery — serial (fast, 2-3 requests)
    page_urls = _crawl_sitemap_2hop.__wrapped__(base_url, max_pages, max_pdfs * 3) \
        if hasattr(_crawl_sitemap_2hop, '__wrapped__') else None

    # Fall back to a direct page_urls extraction inline
    NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if page_urls is None:
        page_urls = []
        for sitemap_url in [
            base_url.rstrip("/") + "/sitemap.xml",
            base_url.rstrip("/") + "/wp-sitemap.xml",
        ]:
            root = _fetch_sitemap_xml(sitemap_url)
            if root is None:
                continue
            sub_locs = (root.findall(".//sm:sitemap/sm:loc", NS)
                        or root.findall(".//sitemap/loc"))
            for sm_loc in sub_locs[:12]:
                sub_url = (sm_loc.text or "").strip()
                if not sub_url:
                    continue
                sub_root = _fetch_sitemap_xml(sub_url)
                if sub_root is None:
                    continue
                for loc in (sub_root.findall(".//sm:url/sm:loc", NS)
                            or sub_root.findall(".//url/loc")):
                    url = (loc.text or "").strip()
                    if url:
                        page_urls.append(url)
                    if len(page_urls) >= max_pages:
                        break
                if len(page_urls) >= max_pages:
                    break
            if page_urls:
                break

    if not page_urls:
        return []

    log(f"  2-hop async: {len(page_urls)} pages to scan concurrently for PDFs")

    # Phase 2: concurrent page scraping — fan-out up to 5 simultaneous
    sem = asyncio.Semaphore(5)
    loop = asyncio.get_event_loop()

    def _scrape_page(page_url: str) -> List[str]:
        return _find_pdf_links(page_url, timeout=10)

    async def _scrape_with_sem(page_url: str) -> List[str]:
        async with sem:
            with ThreadPoolExecutor(max_workers=1) as pool:
                links = await loop.run_in_executor(pool, _scrape_page, page_url)
            return links

    tasks = [_scrape_with_sem(u) for u in page_urls[:max_pages]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    pdf_urls: List[str] = []
    for r in results:
        if isinstance(r, list):
            pdf_urls.extend(r)
        if len(pdf_urls) >= max_pdfs:
            break

    # Deduplicate preserving order
    seen: set = set()
    deduped = []
    for u in pdf_urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    log(f"  2-hop async: found {len(deduped)} unique PDF(s)")
    return deduped[:max_pdfs]


def _crawl_portal(label: str, portal_url: str,
                  gov_only: bool, mode: str,
                  max_pdfs: int) -> List[str]:
    """
    Dispatch to the correct crawl strategy based on mode:
      'direct'     -- portal_url IS the PDF URL; return it directly
      'sitemap'    -- XML sitemap pivot for direct PDF <loc> entries
      'sitemap2hop'-- sitemap page URL discovery → per-page PDF scrape (serial)
      'page'       -- BeautifulSoup HTML scan for <a href=".pdf">

    For async 2-hop (mega_ingest), use _crawl_portal_async() instead.
    """
    import requests
    log(f"Portal scan [{mode}]: [{label}] {portal_url[:70]}")

    if mode == "direct":
        log(f"  [{label}] direct PDF target")
        return [portal_url]

    # P2-09: apply per-portal URL exclusions before returning any results
    _excludes = _PORTAL_URL_EXCLUDES.get(label, [])

    def _apply_excludes(urls: list) -> list:
        if not _excludes:
            return urls
        filtered = [u for u in urls if not any(ex in u for ex in _excludes)]
        if len(filtered) < len(urls):
            log(f"  [{label}] excluded {len(urls)-len(filtered)} URL(s) via portal exclusion list")
        return filtered

    if mode == "sitemap":
        found = _crawl_sitemap(portal_url, max_pdfs)
        if gov_only:
            found = [u for u in found if _GOV_DOMAINS.search(u)]
        found = _apply_excludes(found)
        log(f"  [{label}] sitemap yielded {len(found)} PDF(s)")
        return found[:max_pdfs]

    if mode == "sitemap2hop":
        found = _crawl_sitemap_2hop(portal_url, max_pages=40, max_pdfs=max_pdfs)
        if gov_only:
            found = [u for u in found if _GOV_DOMAINS.search(u)]
        found = _apply_excludes(found)
        log(f"  [{label}] sitemap 2-hop yielded {len(found)} PDF(s)")
        return found[:max_pdfs]

    # mode == "page" — BeautifulSoup HTML scan
    try:
        from bs4 import BeautifulSoup
        r = _resilient_get(portal_url, timeout=(10, 20))  # P3-04: retry on transient errors
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        found = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if not _PDF_HREF.search(href):
                continue
            abs_url = urljoin(portal_url, href)
            if gov_only and not _GOV_DOMAINS.search(abs_url):
                continue
            found.append(abs_url)
            if len(found) >= max_pdfs:
                break
        found = _apply_excludes(found)
        log(f"  [{label}] found {len(found)} PDF link(s)")
        return found
    except requests.exceptions.ReadTimeout:
        log(f"  [{label}] WARN: Read Timeout. Skipping.")
        return []
    except requests.exceptions.ChunkedEncodingError:
        log(f"  [{label}] WARN: Chunked Encoding Error. Skipping.")
        return []
    except Exception as exc:
        log(f"  [{label}] WARN: {exc}")
        return []


async def _crawl_portal_async(label: str, portal_url: str,
                               gov_only: bool, mode: str,
                               max_pdfs: int) -> List[str]:
    """
    C-2: Async portal crawl — uses _crawl_sitemap_2hop_async for sitemap2hop
    portals (SIU), falls back to sync for all other modes.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    if mode == "sitemap2hop":
        log(f"Portal scan [async 2-hop]: [{label}] {portal_url[:70]}")
        found = await _crawl_sitemap_2hop_async(portal_url, max_pages=40, max_pdfs=max_pdfs)
        if gov_only:
            found = [u for u in found if _GOV_DOMAINS.search(u)]
        return found[:max_pdfs]

    # All other modes: run sync version in executor (non-blocking)
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        found = await loop.run_in_executor(
            pool, _crawl_portal, label, portal_url, gov_only, mode, max_pdfs
        )
    return found


# ---------------------------------------------------------------------------
# Task 3: Text extraction — explicit close + gc.collect
# ---------------------------------------------------------------------------

def _ocr_pdf_pages(pdf_bytes: bytes, max_pages: int = 3) -> str:
    """
    P2-04 OCR bridge: convert first max_pages of a PDF to images and run
    pytesseract OCR on each.  Called only when pdfplumber yields < 50 chars
    (scanned / image-based PDFs with no embedded text layer).

    Dependencies:
        pip install pytesseract pdf2image
        + Tesseract-OCR installed (https://github.com/UB-Mannheim/tesseract/wiki)
        + poppler installed (winget install poppler)

    Returns extracted text or '' on any failure.
    """
    try:
        import pytesseract
    except ImportError:
        log("  OCR: pytesseract not installed (pip install pytesseract)")
        return ""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        log("  OCR: pdf2image not installed (pip install pdf2image)")
        return ""

    import shutil

    # Tesseract: prefer PATH, fall back to standard Windows install location.
    if not shutil.which("tesseract"):
        _tess_exe = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if _tess_exe.exists():
            pytesseract.pytesseract.tesseract_cmd = str(_tess_exe)

    # Poppler: prefer PATH, fall back to known winget install location.
    _poppler_path = None
    if not shutil.which("pdftoppm"):
        _winget_poppler = Path(
            r"C:\Users") / Path.home().name / (
            r"AppData\Local\Microsoft\WinGet\Packages"
            r"\oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
            r"\poppler-25.07.0\Library\bin"
        )
        if _winget_poppler.exists():
            _poppler_path = str(_winget_poppler)

    try:
        images = convert_from_bytes(
            pdf_bytes,
            first_page=1,
            last_page=max_pages,
            dpi=200,
            poppler_path=_poppler_path,
        )
    except Exception as exc:
        log(f"  OCR: pdf2image conversion failed: {exc}")
        return ""

    texts: List[str] = []
    for i, img in enumerate(images, start=1):
        try:
            page_text = pytesseract.image_to_string(img, lang="eng")
            if page_text.strip():
                texts.append(page_text)
            log(f"  OCR: page {i} -> {len(page_text)} chars")
        except Exception as exc:
            log(f"  OCR: page {i} failed: {exc}")

    gc.collect()
    return "\n".join(texts)


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract text from PDF bytes.

    Primary path: pdfplumber (text-layer extraction, explicit close + gc).
    P2-04 OCR fallback: if pdfplumber yields < 50 chars, the PDF likely has
    no embedded text layer (scanned court order, image-based tender).
    pytesseract is run against the first 3 pages via pdf2image.

    Processes pages serially — no concurrent reads.
    """
    import pdfplumber
    texts: List[str] = []
    buf = io.BytesIO(pdf_bytes)
    pdf = None
    try:
        pdf = pdfplumber.open(buf)
        for page in pdf.pages[:_MAX_PAGES]:      # serial, 1 page at a time
            try:
                t = page.extract_text()
                if t:
                    texts.append(t)
            finally:
                page.close()                     # explicit page release
    except Exception as exc:
        log(f"  WARN pdfplumber extraction failed: {exc}")
    finally:
        if pdf is not None:
            pdf.close()                          # explicit PDF release
        buf.close()
    # P1-04: only invoke gc on large PDFs — saves ~150–300 ms per call on small docs
    if len(pdf_bytes) > 5_000_000:
        gc.collect()

    text = "\n".join(texts)

    # P2-04: OCR bridge — scanned PDFs have no text layer
    if len(text.strip()) < 50:
        log(f"  Text layer sparse ({len(text.strip())} chars) — attempting OCR on first 3 pages")
        ocr_text = _ocr_pdf_pages(pdf_bytes, max_pages=3)
        if len(ocr_text.strip()) > len(text.strip()):
            log(f"  OCR bridge yielded {len(ocr_text)} chars")
            return ocr_text

    return text


# ---------------------------------------------------------------------------
# Intelligence parsing
# ---------------------------------------------------------------------------

def _parse_intelligence(text: str) -> Dict:
    """Extract tender numbers, amounts, awardees, departments from raw text."""
    intel: Dict = {
        "tender_numbers": [],
        "amounts":        [],
        "awardees":       [],
        "departments":    [],
    }

    for pat in _TENDER_PATTERNS:
        for m in pat.finditer(text):
            ref = m.group(1).strip()
            if ref and ref not in intel["tender_numbers"]:
                intel["tender_numbers"].append(ref)

    for m in _AMOUNT_PATTERN.finditer(text):
        raw_num = m.group(1)
        # Detect comma-as-decimal: single comma followed by 1–2 digits at end
        # e.g. "4,5" → 4.5 million.  Otherwise treat commas as thousands sep.
        if re.match(r"^\d+,\d{1,2}$", raw_num.strip()):
            raw_num = raw_num.replace(",", ".")
        else:
            raw_num = raw_num.replace(",", "").replace(" ", "")
        try:
            val  = float(raw_num)
            unit = (m.group(2) or "").lower().strip()
            # P2-07: preserve the original unit suffix BEFORE normalisation so
            # downstream code can distinguish "R4.5m" from "R4.5bn".
            unit_label = "billion" if unit in ("billion", "bn") else "million"
            if unit in ("billion", "bn"):
                val *= 1000          # normalise to rand millions
            # P2-01 hallucination guard: reject implausible values.
            # >999,000 million == >R999 billion; almost certainly a parse error.
            if 0.01 <= val <= 999_000:
                entry = {
                    "value_millions": round(val, 2),
                    "unit":           unit_label,    # P2-07: original scale
                    "raw_suffix":     unit,          # P2-07: exact matched suffix
                }
                intel["amounts"].append(entry)
        except ValueError:
            pass

    for m in _AWARD_PATTERN.finditer(text):
        name = m.group(1).strip()
        if len(name) > 3 and name not in intel["awardees"]:
            intel["awardees"].append(name[:100])

    for m in _DEPT_PATTERN.finditer(text):
        dept = m.group(1).strip()
        if dept not in intel["departments"]:
            intel["departments"].append(dept[:100])

    # Deduplicate scalar lists (tender_numbers, awardees, departments)
    for key in ("tender_numbers", "awardees", "departments"):
        intel[key] = list(dict.fromkeys(intel[key]))[:10]
    # P2-07: deduplicate amounts by value_millions (dicts aren't hashable)
    seen_vals: set = set()
    deduped_amounts = []
    for entry in intel["amounts"]:
        v = entry["value_millions"]
        if v not in seen_vals:
            seen_vals.add(v)
            deduped_amounts.append(entry)
    intel["amounts"] = deduped_amounts[:10]

    # P2-08: confidence scoring — factors: signal type diversity + hit counts
    # Score range: 0.0 – 1.0
    # • Each populated field type contributes up to 0.25
    # • Diminishing returns on individual hit counts (log-scale)
    import math as _math
    _type_weights = {
        "tender_numbers": 0.35,   # highest: direct procurement identifier
        "amounts":        0.30,   # high: financial intelligence
        "awardees":       0.25,   # medium: contractor identity
        "departments":    0.10,   # lower: common noise
    }
    confidence = 0.0
    for field, weight in _type_weights.items():
        count = len(intel.get(field, []))
        if count > 0:
            # log-scale: 1 hit = full weight, 5+ hits = marginal gain
            confidence += weight * min(1.0, 0.6 + 0.1 * _math.log1p(count))
    confidence = round(min(confidence, 1.0), 3)
    intel["confidence"] = confidence

    return intel


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _signal_exists(conn: sqlite3.Connection, ext_id: str) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM signals WHERE external_id=?", (ext_id,)).fetchone()
    )


# ---------------------------------------------------------------------------
# P1-01: Persistent no-intel cache helpers
# ---------------------------------------------------------------------------

def _artifact_url_status(conn: sqlite3.Connection, pdf_url: str) -> Optional[str]:
    """
    Return the processing_status for this PDF URL if it exists in artifacts,
    or None if it has never been attempted.
    URL is stored in description (source column has a controlled-vocabulary CHECK).
    """
    row = conn.execute(
        "SELECT processing_status FROM artifacts WHERE description=? LIMIT 1",
        (pdf_url,)
    ).fetchone()
    return row[0] if row else None


def _artifact_url_exists(conn: sqlite3.Connection, pdf_url: str,
                         require_intel: bool = True) -> bool:
    """
    Return True if this PDF URL should be skipped based on artifact cache.

    require_intel=True:  skip if any prior attempt exists (no_text, no_intel, done)
    require_intel=False: only skip if prior attempt was no_text (truly un-extractable
                         scan PDF).  no_intel records are re-eligible because they
                         may have narrative prose we now want to capture.
    URL is stored in description (source column has a controlled-vocabulary CHECK).
    """
    status = _artifact_url_status(conn, pdf_url)
    if status is None:
        return False
    if require_intel:
        return True  # any prior attempt blocks re-download
    # For narrative portals: only block truly un-extractable PDFs
    return status == "no_text"


def _record_artifact_skip(conn: sqlite3.Connection, pdf_url: str,
                           status: str,
                           file_path: Optional[Path] = None) -> None:
    """
    Record a tried-but-skipped PDF in artifacts so future runs skip the
    download entirely.  status: 'no_text' | 'no_intel' (mapped to 'skipped')
    """
    try:
        if _artifact_url_status(conn, pdf_url) is not None:
            return  # already recorded (any status)
        title    = Path(urlparse(pdf_url).path).stem[:80] or "unknown"
        path_str = str(file_path) if file_path else None
        db_status = "skipped"  # CHECK constraint: pending|processing|done|failed|skipped
        conn.execute(
            """INSERT INTO artifacts
                   (title, description, type, source, file_path,
                    processing_status, source_type, created_at)
               VALUES (?, ?, 'document', 'government', ?,
                       ?, 'pdf_portal', datetime('now'))""",
            (title, pdf_url, path_str, db_status),
        )
        conn.commit()
    except Exception as exc:
        log(f"  WARN skip-record failed: {exc}")


def _insert_signal(conn: sqlite3.Connection, pdf_url: str,
                   intel: Dict, actor_name: str,
                   source_signal_id: str) -> Optional[str]:
    """Insert a PDF-derived signal. Returns new signal_id or None on duplicate."""
    ext_id = "pdf:" + hashlib.sha1(pdf_url.encode()).hexdigest()[:20]
    if _signal_exists(conn, ext_id):
        return None

    tenders    = intel.get("tender_numbers", [])
    title_core = tenders[0] if tenders else Path(urlparse(pdf_url).path).stem[:60]
    title      = f"[PDF] {actor_name}: {title_core}"[:400]

    content_parts = []
    if tenders:
        content_parts.append("Tender refs: " + ", ".join(tenders[:5]))
    if intel.get("amounts"):
        # P2-07: format with original unit label (million/billion) not a bare float
        content_parts.append("Amounts: " + ", ".join(
            f"R{a['value_millions']}M ({a['unit']})" for a in intel["amounts"][:5]
        ))
    if intel.get("awardees"):
        content_parts.append("Awardees: " + "; ".join(intel["awardees"][:3]))
    if intel.get("departments"):
        content_parts.append("Departments: " + "; ".join(intel["departments"][:3]))
    content = " | ".join(content_parts) or "PDF extracted — no structured fields found"

    sig_id = str(uuid.uuid4())
    meta   = json.dumps({
        "pdf_url":          pdf_url,
        "source_signal_id": source_signal_id,
        "dork_actor":       actor_name,
        "intel":            intel,
    }, ensure_ascii=False)

    conn.execute("""
        INSERT INTO signals
            (signal_id, source, external_id, title, content,
             lat, lng, timestamp, status, stream,
             relevance_score, is_priority, metadata_json, source_type)
        VALUES (?,?,?,?,?,
                -25.7479, 28.2293, ?, 'raw', 'CRIME_INTEL',
                1.8, 1, ?, 'live')
    """, (
        sig_id, "pdf_infiltrator", ext_id, title, content,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        meta,
    ))
    return sig_id


def _insert_artifact(conn: sqlite3.Connection, sig_id: str,
                     pdf_url: str, intel: Dict,
                     file_path: Optional[Path] = None,
                     raw_text: str = "") -> Optional[int]:
    """
    P1-08 (Phase 48): insert artifact with the real artifacts schema.

    Columns used:
      source          — PDF URL (used for dedup)
      file_path       — local path in media/documents/
      raw_text_cache  — first 8000 chars of PDF text (for triple_extractor)
      processing_status — 'done'
      source_type     — 'pdf_portal'
      tags            — JSON of tender_numbers + amounts

    Returns artifact_id on success (so signals.source_artifact_id can be set),
    or None on failure.
    """
    try:
        # Dedup: URL stored in description (source has a controlled-vocab CHECK)
        existing = conn.execute(
            "SELECT artifact_id FROM artifacts WHERE description=? LIMIT 1", (pdf_url,)
        ).fetchone()
        if existing:
            return int(existing[0])

        tenders  = intel.get("tender_numbers", [])
        title    = (tenders[0] if tenders else
                    Path(urlparse(pdf_url).path).stem[:80]) or "unknown"

        # description stores the canonical PDF URL for dedup and provenance
        description = pdf_url

        path_str   = str(file_path) if file_path else None
        text_cache = raw_text[:8000] if raw_text else None
        # P2-03: include departments + awardees so every artifact record
        # carries full extraction payload for provenance attribution.
        # P2-08: include extraction confidence score so surface tier can
        # filter low-confidence intel before display.
        tags_json  = json.dumps({
            "tender_numbers": tenders[:5],
            "amounts":        intel.get("amounts", [])[:5],
            "awardees":       intel.get("awardees", [])[:5],
            "departments":    intel.get("departments", [])[:5],
            "confidence":     intel.get("confidence", 0.0),
        }, ensure_ascii=False)

        cur = conn.execute(
            """INSERT INTO artifacts
                   (title, description, type, source, file_path,
                    processing_status, source_type, raw_text_cache,
                    tags, created_at)
               VALUES (?, ?, 'document', 'government', ?,
                       'done', 'pdf_portal', ?,
                       ?, datetime('now'))""",
            (title[:200], description, path_str,
             text_cache, tags_json),
        )
        artifact_id = cur.lastrowid

        # Wire provenance: signal → artifact
        if sig_id and artifact_id:
            conn.execute(
                "UPDATE signals SET source_artifact_id=? WHERE signal_id=?",
                (artifact_id, sig_id),
            )

        return artifact_id

    except Exception as exc:
        log(f"  WARN artifact insert failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Core processing block — shared between dork and portal modes
# ---------------------------------------------------------------------------

_STATUS_WRITTEN  = "written"   # new signal written to DB
_STATUS_SKIP     = "skip"      # dedup / no intel / no text — not an error
_STATUS_ERROR    = "error"     # download failure / connection error


def _process_pdf(conn: sqlite3.Connection,
                 pdf_url: str,
                 label: str,
                 source_signal_id: str,
                 require_intel: bool = True) -> str:
    """
    Download → save locally → extract → parse → write to DB.
    Returns _STATUS_WRITTEN, _STATUS_SKIP, or _STATUS_ERROR.

    require_intel=False: write signal for any PDF with ≥200 chars of text,
      even without tender refs/amounts. Used for investigation portals
      (SIU, NPA) so their prose reaches raw_text_cache → triple_extractor.

    Phase 48 additions:
      - P1-01: checks artifact cache before downloading (persistent no-intel skip)
      - P1-08: passes raw PDF text to _insert_artifact for raw_text_cache storage
    """
    # 1. Signal dedup — already written to DB on a previous run
    ext_id_check = "pdf:" + hashlib.sha1(pdf_url.encode()).hexdigest()[:20]
    if _signal_exists(conn, ext_id_check):
        log(f"  SKIP (known): {pdf_url[:70]}")
        return _STATUS_SKIP

    # 2. P1-01: artifact cache — previously tried, no intel / no text
    # For narrative portals (require_intel=False): only block truly un-extractable
    # PDFs (no_text). no_intel records are re-eligible — they may now produce signals.
    if _artifact_url_exists(conn, pdf_url, require_intel=require_intel):
        log(f"  SKIP (cached): {pdf_url[:70]}")
        return _STATUS_SKIP

    log(f"  Downloading [{label}]: {pdf_url[:70]}")
    pdf_bytes = _download_pdf(pdf_url)
    time.sleep(_REQUEST_DELAY)
    if not pdf_bytes:
        return _STATUS_ERROR

    # Save to disk before extraction
    title_hint = Path(urlparse(pdf_url).path).stem[:60]
    local_path = _save_pdf_local(pdf_bytes, label, source_signal_id, title_hint)

    # Extract text (explicit close + gc)
    text = _extract_text_from_pdf(pdf_bytes)
    if not text.strip():
        log(f"  SKIP (no extractable text)")
        _record_artifact_skip(conn, pdf_url, "no_text", local_path)   # P1-01
        return _STATUS_SKIP

    try:
        intel = _parse_intelligence(text)
    except Exception as exc:
        log(f"  ERROR parsing intelligence: {exc}")
        return _STATUS_ERROR

    has_intel = any(intel[k] for k in ("tender_numbers", "amounts", "awardees"))
    log(
        f"  Parsed [{label}]: tenders={len(intel['tender_numbers'])} "
        f"amounts={len(intel['amounts'])} awardees={len(intel['awardees'])}"
    )
    if not has_intel:
        if require_intel:
            log(f"  SKIP (no structured intel found)")
            _record_artifact_skip(conn, pdf_url, "no_intel", local_path)  # P1-01
            return _STATUS_SKIP
        elif len(text) < 200:
            log(f"  SKIP (text too short for narrative signal: {len(text)} chars)")
            _record_artifact_skip(conn, pdf_url, "no_intel", local_path)
            return _STATUS_SKIP
        else:
            log(f"  Narrative signal (no structured intel, {len(text)} chars prose)")

    new_sig_id = _insert_signal(conn, pdf_url, intel, label, source_signal_id)
    if new_sig_id:
        # P1-08: pass raw text → stored in raw_text_cache for triple_extractor
        _insert_artifact(conn, new_sig_id, pdf_url, intel, local_path, text)
        log(f"  Written signal: {new_sig_id[:8]}...")
        return _STATUS_WRITTEN

    return _STATUS_SKIP


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PDFInfiltrator:
    """Class-based wrapper compatible with mega_ingest.py _run_engine() pattern."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def run(self, max_pdfs: int = _MAX_PER_RUN) -> dict:
        return _run_infiltration(self.db_path, max_pdfs=max_pdfs)


# ---------------------------------------------------------------------------
# Run mode 1: dork signal RSS → article → PDF
# ---------------------------------------------------------------------------

def _run_infiltration(db_path: Path = DB_PATH,
                      max_pdfs: int = _MAX_PER_RUN) -> dict:

    _migrate_media_root(db_path)   # hygiene: move stray PDFs → media/documents/
    has_pdf, has_req = _check_deps()
    if not has_req:
        log("ERROR: requests not installed — pip install requests")
        return {"status": "error", "error": "requests_missing"}
    if not has_pdf:
        log("ERROR: pdfplumber not installed — pip install pdfplumber")
        return {"status": "error", "error": "pdfplumber_missing"}
    if not db_path.exists():
        log(f"ERROR: DB not found at {db_path}")
        return {"status": "error", "error": "db_missing"}

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    dork_sigs = conn.execute("""
        SELECT s.signal_id, s.metadata_json, s.title
        FROM   signals s
        WHERE  s.source = 'dork'
          AND  s.metadata_json IS NOT NULL
          AND  s.metadata_json LIKE '%source_url%'
          AND  NOT EXISTS (
              SELECT 1 FROM signals p
              WHERE p.source = 'pdf_infiltrator'
                AND p.metadata_json LIKE '%' || s.signal_id || '%'
          )
        ORDER BY s.relevance_score DESC
        LIMIT ?
    """, (max_pdfs * 3,)).fetchall()

    log(f"Dork signals to scan: {len(dork_sigs)} (PDF cap: {max_pdfs})")

    pdfs_processed = 0
    signals_written = 0
    pages_scanned   = 0
    errors          = 0

    for sig in dork_sigs:
        if pdfs_processed >= max_pdfs:
            break
        try:
            meta = json.loads(sig["metadata_json"] or "{}")
        except Exception:
            continue

        rss_url    = meta.get("source_url", "")
        actor_name = meta.get("dork_actor", "unknown")
        if not rss_url:
            continue

        article_url = _resolve_google_news_url(rss_url)
        if not article_url:
            continue

        pdf_links = _find_pdf_links(article_url)
        pages_scanned += 1
        time.sleep(_REQUEST_DELAY)
        if not pdf_links:
            continue

        log(f"  Found {len(pdf_links)} PDF(s) via {actor_name!r}")

        for pdf_url in pdf_links:
            if pdfs_processed >= max_pdfs:
                break
            status = _process_pdf(conn, pdf_url, actor_name, sig["signal_id"])
            if status == _STATUS_WRITTEN:
                signals_written += 1
                pdfs_processed  += 1
            elif status == _STATUS_ERROR:
                errors += 1

        conn.commit()

    conn.close()

    summary = {
        "status":            "done",
        "collector":         "pdf_infiltrator",
        "dork_sigs_scanned": len(dork_sigs),
        "pages_scanned":     pages_scanned,
        "pdfs_processed":    pdfs_processed,
        "signals_written":   signals_written,
        "errors":            errors,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }
    log(f"Done: {signals_written} signals written from {pdfs_processed} PDFs")
    return summary


# ---------------------------------------------------------------------------
# Run mode 2: direct portal crawl (--portals)
# ---------------------------------------------------------------------------

def _run_portal_infiltration(db_path: Path = DB_PATH,
                              max_pdfs: int = _MAX_PER_RUN) -> dict:
    """Crawl _PORTAL_TARGETS directly — no dork signals required."""
    _migrate_media_root(db_path)   # hygiene: move stray PDFs → media/documents/

    has_pdf, has_req = _check_deps()
    if not has_req:
        log("ERROR: requests not installed")
        return {"status": "error", "error": "requests_missing"}
    if not has_pdf:
        log("ERROR: pdfplumber not installed")
        return {"status": "error", "error": "pdfplumber_missing"}

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    pdfs_processed  = 0
    signals_written = 0
    errors          = 0
    _seen_this_run: set = set()   # cross-portal dedup for URLs not written to DB

    for label, portal_url, gov_only, mode, require_intel in _PORTAL_TARGETS:
        if pdfs_processed >= max_pdfs:
            break

        pdf_links = _crawl_portal(
            label, portal_url, gov_only, mode,
            max_pdfs - pdfs_processed
        )
        # Deduplicate within this portal's crawl result (sitemap can surface
        # the same PDF from multiple page references)
        pdf_links = list(dict.fromkeys(pdf_links))
        time.sleep(_REQUEST_DELAY)

        for pdf_url in pdf_links:
            if pdfs_processed >= max_pdfs:
                break
            if pdf_url in _seen_this_run:
                log(f"  SKIP (seen this run): {pdf_url[:70]}")
                continue
            _seen_this_run.add(pdf_url)
            status = _process_pdf(conn, pdf_url, label, "portal",
                                   require_intel=require_intel)
            if status == _STATUS_WRITTEN:
                signals_written += 1
                pdfs_processed  += 1
                conn.commit()   # commit each signal immediately — crash-safe
            elif status == _STATUS_ERROR:
                errors += 1

    conn.close()

    summary = {
        "status":          "done",
        "mode":            "portal",
        "portals_scanned": len(_PORTAL_TARGETS),
        "pdfs_processed":  pdfs_processed,
        "signals_written": signals_written,
        "errors":          errors,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    log(f"Portal mode done: {signals_written} signals from {pdfs_processed} PDFs")
    return summary


# ---------------------------------------------------------------------------
# Run mode 3: vault reprocessor (P2-04) — back-fill raw_text_cache
# ---------------------------------------------------------------------------

def _reprocess_vault(db_path: Path = DB_PATH) -> dict:
    """
    P2-04: Re-extract text (with OCR fallback) from every saved PDF in the
    vault (media/documents/) whose artifact record has an empty raw_text_cache.

    For each qualifying artifact:
      1. Read the local file from file_path.
      2. Run _extract_text_from_pdf() — pdfplumber primary, OCR fallback.
      3. Update artifacts.raw_text_cache with the result.
      4. If the artifact had no linked signal, create one so triple_extractor
         can operate on it (uses existing _insert_signal logic).
      5. If status was 'no_text' and OCR yielded text, reset to 'done'.

    The 200-char intake valve from artifact_processor is NOT applied here;
    the vault contains pre-vetted documents and we want all recoverable text.
    """
    import pdfplumber  # noqa — verify dependency present

    if not db_path.exists():
        log(f"ERROR: DB not found at {db_path}")
        return {"status": "error", "error": "db_missing"}

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    # Artifacts with a local file but no useful cache
    candidates = conn.execute("""
        SELECT a.artifact_id, a.file_path, a.description,
               a.processing_status, a.title,
               (SELECT s.signal_id FROM signals s
                WHERE s.source_artifact_id = a.artifact_id LIMIT 1) AS linked_sig
        FROM   artifacts a
        WHERE  a.file_path IS NOT NULL
          AND  (a.raw_text_cache IS NULL OR length(trim(a.raw_text_cache)) < 50)
    """).fetchall()

    log(f"Vault reprocessor: {len(candidates)} artifact(s) with empty/thin cache")

    updated      = 0
    ocr_used     = 0
    signals_made = 0
    skipped      = 0

    for art in candidates:
        fp = Path(art["file_path"])
        if not fp.exists():
            log(f"  WARN: file not found: {fp.name}")
            skipped += 1
            continue

        log(f"  [{art['artifact_id']}] {fp.name[:60]}")

        try:
            pdf_bytes = fp.read_bytes()
        except Exception as exc:
            log(f"    WARN read failed: {exc}")
            skipped += 1
            continue

        # OCR-aware extraction
        # sqlite3.Row doesn't support .get(), use try/except for optional cols
        try:
            _cached = art["raw_text_cache"]
        except IndexError:
            _cached = None
        old_len = len((_cached or "").strip())
        text    = _extract_text_from_pdf(pdf_bytes)
        new_len = len(text.strip())

        if new_len < 50:
            log(f"    Still no text after OCR ({new_len} chars) — skipping")
            skipped += 1
            continue

        # Was OCR the differentiator?
        try:
            _status = art["processing_status"]
        except IndexError:
            _status = None
        used_ocr = (old_len < 50 and _status in ("no_text", None) and new_len >= 50)
        if used_ocr:
            ocr_used += 1

        # Update raw_text_cache + reset status if previously 'no_text'
        new_status = "done" if _status == "no_text" else _status
        conn.execute("""
            UPDATE artifacts
               SET raw_text_cache    = ?,
                   processing_status = ?
             WHERE artifact_id = ?
        """, (text[:8000], new_status, art["artifact_id"]))
        updated += 1

        # Create a signal if this artifact has none, so triple_extractor
        # can process it under the pdf_infiltrator source family.
        try:
            _linked = art["linked_sig"]
        except IndexError:
            _linked = None
        if not _linked:
            pdf_url = art["description"] or fp.name
            intel   = _parse_intelligence(text)
            new_sig = _insert_signal(conn, pdf_url, intel,
                                     art["title"] or fp.stem,
                                     "vault_reprocess")
            if new_sig:
                conn.execute(
                    "UPDATE signals SET source_artifact_id=? WHERE signal_id=?",
                    (art["artifact_id"], new_sig),
                )
                signals_made += 1
                log(f"    Signal created: {new_sig[:8]}...")

        conn.commit()
        log(f"    Cache updated: {new_len} chars"
            + (" [OCR]" if used_ocr else ""))

    conn.close()

    summary = {
        "status":       "done",
        "candidates":   len(candidates),
        "updated":      updated,
        "ocr_used":     ocr_used,
        "signals_made": signals_made,
        "skipped":      skipped,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }
    log(f"Vault reprocess done: {summary}")
    return summary


# ---------------------------------------------------------------------------
# Async shim + CLI
# ---------------------------------------------------------------------------

async def collect(db_path=None, max_pdfs: int = _MAX_PER_RUN) -> dict:
    """
    C-1: Primary async collect() interface for mega_ingest.py.

    Runs _PORTAL_TARGETS concurrently (one coroutine per portal).
    sitemap2hop portals (SIU) use the async 2-hop fan-out (C-2).
    All other portals run in a ThreadPoolExecutor (non-blocking).

    After URL discovery, PDF processing remains serial per-portal to
    respect rate limits and keep thermal load predictable.

    Returns a result dict compatible with mega_ingest collector logging.
    """
    import asyncio

    _db = Path(db_path) if db_path else DB_PATH
    _migrate_media_root(_db)

    has_pdf, has_req = _check_deps()
    if not has_req or not has_pdf:
        missing = "requests" if not has_req else "pdfplumber"
        return {"status": "error", "error": f"{missing}_missing", "collector": "pdf_infiltrator"}

    conn = sqlite3.connect(str(_db), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    pdfs_processed  = 0
    signals_written = 0
    errors          = 0
    _seen_this_run: set = set()

    # Fan-out portal URL discovery concurrently
    per_portal_cap = max(3, max_pdfs // len(_PORTAL_TARGETS))
    discovery_tasks = [
        _crawl_portal_async(label, portal_url, gov_only, mode, per_portal_cap)
        for label, portal_url, gov_only, mode, _req_intel in _PORTAL_TARGETS
    ]
    portal_results = await asyncio.gather(*discovery_tasks, return_exceptions=True)

    # Process discovered PDFs — serial per-portal for rate-limit safety
    for (label, portal_url, gov_only, mode, require_intel), pdf_links in zip(
        _PORTAL_TARGETS, portal_results
    ):
        if isinstance(pdf_links, Exception):
            log(f"  [{label}] discovery error: {pdf_links}")
            errors += 1
            continue

        pdf_links = list(dict.fromkeys(pdf_links))  # dedup
        for pdf_url in pdf_links:
            if pdfs_processed >= max_pdfs:
                break
            if pdf_url in _seen_this_run:
                continue
            _seen_this_run.add(pdf_url)
            status = _process_pdf(conn, pdf_url, label, "portal",
                                  require_intel=require_intel)
            if status == _STATUS_WRITTEN:
                signals_written += 1
                pdfs_processed  += 1
                conn.commit()
            elif status == _STATUS_ERROR:
                errors += 1

    conn.close()

    return {
        "status":          "done",
        "collector":       "pdf_infiltrator",
        "portals_scanned": len(_PORTAL_TARGETS),
        "pdfs_processed":  pdfs_processed,
        "inserted":        signals_written,
        "errors":          errors,
    }


async def async_main(**kwargs):
    """Legacy interface — delegates to collect()."""
    return await collect()


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(
        description="FORGE PDF Infiltrator — government tender document extraction"
    )
    parser.add_argument("--db",       type=Path, default=None)
    parser.add_argument("--max-pdfs", type=int,  default=_MAX_PER_RUN)
    parser.add_argument("--portals",  action="store_true",
                        help="Crawl SA government portals directly (bypasses dork RSS)")
    parser.add_argument("--reprocess-vault", action="store_true",
                        help="P2-04: re-extract text (with OCR) from saved PDFs "
                             "in media/documents/ to back-fill empty raw_text_cache")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print collection plan without downloading or writing to database")
    args = parser.parse_args()

    db = args.db.resolve() if args.db else DB_PATH

    if args.dry_run:
        print(f"[pdf_infiltrator] DRY RUN — DB: {db}")
        print(f"[pdf_infiltrator] Max PDFs per run: {args.max_pdfs}")
        print(f"[pdf_infiltrator] Mode: {'reprocess-vault' if args.reprocess_vault else 'portals' if args.portals else 'dork-rss'}")
        print("[pdf_infiltrator] Dry run complete (no downloads, no writes)")
        sys.exit(0)

    if getattr(args, "reprocess_vault", False):
        result = _reprocess_vault(db_path=db)
    elif args.portals:
        result = _run_portal_infiltration(db_path=db, max_pdfs=args.max_pdfs)
    else:
        result = _run_infiltration(db_path=db, max_pdfs=args.max_pdfs)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("status") == "done" else 1)
