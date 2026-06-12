#!/usr/bin/env python3
from __future__ import annotations

"""
forage/collectors/bi196_collector.py
=====================================
Phase P5 — DHA BI-196 Surname-Change Gazette Collector

Crawls the municipal/government OSINT layer for references to the South African
Department of Home Affairs BI-196 form ("Authority to Assume Another Surname").

Intelligence value:
  SA law requires applicants to publish a surname-change notice in the
  Government Gazette BEFORE the DHA registers the change. Those notices are
  public record and name the original surname, new surname, and often the SA
  ID number. This collector ingests those notices and cross-references them
  against active FORGE actors — an actor who has changed surnames is a
  potential identity-obfuscation signal.

Four crawl layers (executed in order):
  1. Gazette RSS    → Google News RSS site:gpwonline.co.za
  2. DHA portal RSS → Google News RSS site:dha.gov.za
  3. Gazette PDFs   → opengazettes.org.za open archive (1958–2021, no auth).
                      Legal Gazette A/C PDFs downloaded, text extracted via
                      pdfplumber, notice blocks scanned for BI-196 content.
                      Requires: pip install pdfplumber requests beautifulsoup4
  4. Actor sweep    → Per active-case actor: Google News gazette query.
                      Detects published surname-change notices for tracked actors.

Gazette PDF architecture:
  gpwonline.co.za moved to a paid eGazette subscriber delivery system in 2026 —
  gazette PDFs are no longer freely web-accessible. laws.africa (gazettes.africa)
  is the canonical open-data provider for SA Government Gazette content and
  exposes a structured API with PDF download links.

  API endpoint used:
    GET https://api.laws.africa/v3/gazette/
        ?country=za&nature=legal+notice&ordering=-date&page_size=20
    Authorization: Token <LAWS_AFRICA_TOKEN>

  Each gazette publication returned includes a 'pdf_url' field. The collector
  downloads each PDF, extracts text via pdfplumber, splits into notice blocks,
  and runs _parse_notice() on any block that matches the BI-196 pre-filter.

Extracted fields (metadata_json):
  form_ref       "BI-196"
  name_before    Original surname (regex-extracted from notice text)
  name_after     New surname
  id_number      SA ID number if present in notice (published by law)
  gazette_notice true if notice confirmed from laws.africa / gpwonline
  from_pdf       true if extracted from a gazette PDF
  gazette_issue  Gazette title / identifier for provenance
  actor_match    Actor name if FORGE actor cross-reference matched
  source_url     Canonical URL

Signal stream:  CRIME_INTEL
Signal source:  bi196_collector
Priority flag:  is_priority=1 if actor cross-reference matched

Deduplication: external_id = "bi196:{sha1(url)[:16]}"
               PDF notices:  "bi196:pdf:{sha1(notice_block)[:16]}"
Rate limit:     2 s between requests
Max actors:     20 per run
Max PDFs:       15 per run

Environment variables:
  LAWS_AFRICA_TOKEN   API token from edit.laws.africa — enables layer 3 PDF crawl.
                      Without this, layer 3 is skipped with a warning.

Usage:
    python forage/collectors/bi196_collector.py
    python forage/collectors/bi196_collector.py --dry-run
    python forage/collectors/bi196_collector.py --actor "Thabo Nkosi"
    python forage/collectors/bi196_collector.py --scan-only
    python forage/collectors/bi196_collector.py --gazette-pdfs-only
"""

__manifest__ = {
    "id":          "bi196_collector",
    "name":        "DHA BI-196 Surname-Change Collector",
    "description": (
        "Crawls Government Gazette (RSS + laws.africa PDF API) and DHA portal for BI-196 "
        "'Authority to Assume Another Surname' notices. Cross-references "
        "active FORGE actors to detect potential identity-obfuscation events. "
        "Set LAWS_AFRICA_TOKEN env var to enable full gazette PDF layer."
    ),
    "icon":        "📋",
    "entry":       "forage/collectors/bi196_collector.py",
    "args":        ["--dry-run", "--actor", "--scan-only", "--gazette-pdfs-only"],
    "job_key":     "bi196_collector",
    "version":     "1.4.0",
}

import argparse
import gc
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

_log = logging.getLogger("forge.bi196_collector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_PATH      = Path(__file__).resolve().parent.parent.parent / "database.db"
REQ_DELAY    = 2.0    # seconds between HTTP requests
MAX_ACTORS   = 20     # actors per actor-sweep run
MAX_PDFS     = 15     # gazette PDFs per run
MAX_PDF_MB   = 15     # skip PDFs larger than this
MAX_PDF_PAGES = 30    # pdfplumber page cap per gazette

UA           = "FORGE-OSINT/1.1 (public-records research; non-commercial)"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-ZA&gl=ZA&ceid=ZA:en"

# ── Gazette notice regex patterns ─────────────────────────────────────────────

# SA Government Gazette notices (Births and Deaths Registration Act s.26) use
# the specific wording: "to assume the surname X in lieu of the surname Y"
# All four forms are matched below.

# Form 1 — SA statutory wording (most common in gazette):
#   "to assume the surname SITHOLE in lieu of the surname DLAMINI"
_ASSUME_INLIEU_RE = re.compile(
    r"assume\s+the\s+surname\s+([A-Z][A-Z\s\-\']{1,40?})\s+in\s+lieu\s+of\s+"
    r"(?:the\s+surname\s+)?([A-Z][A-Z\s\-\']{1,40})",
    re.IGNORECASE,
)

# Form 2 — "assume the surname X instead of Y"
_ASSUME_INSTEAD_RE = re.compile(
    r"assume\s+the\s+surname\s+([A-Z][A-Z\s\-\']{1,40?})\s+instead\s+of\s+"
    r"(?:the\s+surname\s+)?([A-Z][A-Z\s\-\']{1,40})",
    re.IGNORECASE,
)

# Form 3 — plain English: "change my/the surname from X to Y"
_CHANGE_FROM_RE = re.compile(
    r"change\s+(?:my\s+|the\s+)?surname\s+from\s+"
    r"([A-Z][A-Z\s\-\']{1,40})\s+to\s+([A-Z][A-Z\s\-\']{1,40})",
    re.IGNORECASE,
)

# Form 4 — Afrikaans: "naam verander van X na Y" / "van naam X na naam Y"
_AFRIKAANS_RE = re.compile(
    r"(?:naam\s+verander|van\s+naam)\s+(?:van\s+)?([A-Z][A-Z\s\-\']{1,40})\s+na\s+"
    r"(?:naam\s+)?([A-Z][A-Z\s\-\']{1,40})",
    re.IGNORECASE,
)

# Convenience tuple — checked in priority order
_SURNAME_PATTERNS = (
    _ASSUME_INLIEU_RE,
    _ASSUME_INSTEAD_RE,
    _CHANGE_FROM_RE,
    _AFRIKAANS_RE,
)

# Keep for backward-compat references elsewhere in this file
_SURNAME_CHANGE_RE = _CHANGE_FROM_RE

# South African ID number: 13 digits, first 6 = YYMMDD
_SA_ID_RE = re.compile(r"\b(\d{13})\b")

# Gazette notice number: e.g. "Notice No. 1234 of 2024"
_GAZETTE_NOTICE_RE = re.compile(
    r"(?:Notice\s+No\.?\s*|No\.?\s*)(\d{3,6})\s+(?:of\s+(\d{4}))?",
    re.IGNORECASE,
)

# ── Open Gazettes archive ─────────────────────────────────────────────────────
# opengazettes.org.za (run by OpenUp SA) is a free, open archive of SA Government
# Gazette PDFs — 42,000+ issues from 1958 to 2021. No authentication required.
#
# National gazette listing: https://opengazettes.org.za/gazettes/ZA/{year}
# Archive PDFs:             https://archive.opengazettes.org.za/archive/ZA/{year}/...
#
# Surname-change personal notices appear in the Legal Gazette A series, alongside
# estate/insolvency notices. The Births and Deaths Registration Act (s.26) requires
# publication in the Gazette before DHA registers a surname change.
#
# Gazette types targeted (by URL slug):
#   legal-notices-A  — personal + estate notices (highest BI-196 yield)
#   legal-notices-C  — misc personal notices (party registrations, etc.)
#   (plain)          — main Government Gazette, may contain general personal notices
#
# Archive coverage: 2000–2021. For 2022+ see LAWS_AFRICA_TOKEN option below.
#
# laws.africa free token (LAWS_AFRICA_TOKEN): gives access only to Cape Town
# municipal by-laws on the free tier — national gazette requires a paid subscription.
# Token is preserved here for future paid-tier use.

_OPEN_GAZETTES_BASE    = "https://opengazettes.org.za/gazettes/ZA"
_OPEN_GAZETTES_ARCHIVE = "https://archive.opengazettes.org.za"
# Gazette URL slug suffixes to prioritise
#
# Gazette A/B/C breakdown (confirmed via PDF inspection):
#   legal-notices-A  — BUSINESS NOTICES: deeds registry (lost deeds), company
#                      registrations, POCA forfeiture orders. NOT surname changes.
#   legal-notices-B  — SALES IN EXECUTION: property auctions. NOT surname changes.
#   legal-notices-C  — MISC personal/electoral notices. Rarely has surname changes.
#   (no slug suffix)  — Plain government gazette issues. These contain General
#                       Notices including surname-change personal notices under
#                       the Births and Deaths Registration Act s.26. This is the
#                       correct type.
#
# We do NOT include legal-notices-A/B — they're the wrong gazette type and
# generate false keyword hits from deeds-registry "in lieu of" language.
_GAZETTE_PRIORITY_SLUGS: tuple[str, ...] = ()    # empty = accept all, then filter below

# Gazette URL exclusion patterns — skip known wrong types
_GAZETTE_EXCLUDE_SLUGS = (
    "legal-notices-a",
    "legal-notices-b",
    "tender-bulletin",
    "road-carrier",
    "liquor-license",
    "regulation-gazette",
)

# Tight keyword pre-filter — only the phrase-level terms that appear in actual
# Births and Deaths Registration Act s.26 personal notices. Deliberately excludes:
#   "in lieu of"     — deeds registry false positive
#   "section 26"     — appears in deeds registry and POCA as well
#   "intend to"      — appears in POCA forfeiture notices
_BI196_KEYWORDS = (
    "bi-196",
    "bi 196",
    "authority to assume",
    "change of surname",
    "assume the surname",
    "in lieu of the surname",          # surname-specific (not deed-specific)
    "births and deaths registration",  # full phrase, avoids partial matches
    "van naam",                        # Afrikaans: change of surname
    "naam verander",                   # Afrikaans: name changed
)

# ── Google News RSS queries ────────────────────────────────────────────────────

_GAZETTE_QUERIES = [
    '"BI-196" site:gpwonline.co.za',
    '"authority to assume another surname" site:gpwonline.co.za',
    '"surname change" gazette "South Africa" BI-196',
    '"change of surname" gazette notice "South Africa"',
    '"intend to change" surname "Government Gazette" "South Africa"',
]

_DHA_QUERIES = [
    '"BI-196" site:dha.gov.za',
    '"authority to assume another surname" site:dha.gov.za',
    'BI-196 "home affairs" "surname" "South Africa"',
]


# ── Optional dependency helpers ────────────────────────────────────────────────

def _get_requests_session():
    """Build a requests.Session with SSL bypass and FORGE UA. Returns None if requests unavailable."""
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        sess = requests.Session()
        sess.verify = False
        sess.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-ZA,en-GB;q=0.9,en;q=0.7",
        })
        return sess
    except ImportError:
        return None


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes via pdfplumber (with gc on large files)."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    texts: list[str] = []
    buf = io.BytesIO(pdf_bytes)
    pdf_obj = None
    try:
        pdf_obj = pdfplumber.open(buf)
        for page in pdf_obj.pages[:MAX_PDF_PAGES]:
            try:
                t = page.extract_text()
                if t:
                    texts.append(t)
            finally:
                page.close()
    except Exception as exc:
        _log.debug("pdfplumber extraction failed: %s", exc)
    finally:
        if pdf_obj is not None:
            pdf_obj.close()
        buf.close()
    if len(pdf_bytes) > 5_000_000:
        gc.collect()
    return "\n".join(texts)


# ── Notice parsing ─────────────────────────────────────────────────────────────

def _parse_notice(text: str) -> dict:
    """
    Extract structured fields from a gazette notice text blob.

    Handles all four SA gazette wording forms:
      - "assume the surname X in lieu of the surname Y"  (statutory s.26 form)
      - "assume the surname X instead of Y"
      - "change my surname from X to Y"
      - Afrikaans: "naam verander van X na Y"
    """
    result: dict = {}

    # Try each pattern in priority order — first match wins
    for pat in _SURNAME_PATTERNS:
        m = pat.search(text)
        if m:
            # group(1) = new surname, group(2) = old surname for assume-forms
            # group(1) = old surname, group(2) = new surname for change-from form
            if pat in (_ASSUME_INLIEU_RE, _ASSUME_INSTEAD_RE, _AFRIKAANS_RE):
                result["name_after"]  = m.group(1).strip().title()
                result["name_before"] = m.group(2).strip().title()
            else:
                result["name_before"] = m.group(1).strip().title()
                result["name_after"]  = m.group(2).strip().title()
            break

    ids = _SA_ID_RE.findall(text)
    if ids:
        result["id_number"] = ids[0]

    gm = _GAZETTE_NOTICE_RE.search(text)
    if gm:
        ref = f"Notice {gm.group(1)}"
        if gm.group(2):
            ref += f" of {gm.group(2)}"
        result["gazette_ref"] = ref

    return result


def _is_bi196_block(text: str) -> bool:
    """Pre-filter: does this text block look like a BI-196 surname-change notice?"""
    tl = text.lower()
    return (
        "bi-196" in tl
        or "bi 196" in tl
        or "authority to assume" in tl
        or "change of surname" in tl
        or "assume the surname" in tl          # statutory s.26 wording
        or "in lieu of the surname" in tl      # statutory s.26 wording
        or "section 26" in tl                  # Births and Deaths Registration Act
        or ("surname" in tl and "intend" in tl)
        or any(pat.search(text) for pat in _SURNAME_PATTERNS)
    )


def _split_gazette_into_blocks(text: str) -> list[str]:
    """
    Split a gazette PDF text into individual notice blocks.

    Government Gazette legal notices are separated by bold notice headers:
      NOTICE 1234 OF 2024
      No. 1234, 2024
    Fall back to double-newline splitting for dense gazette layouts.
    """
    # Try notice-number boundary splits first
    blocks = re.split(
        r'\n\s*(?:NOTICE\s+\d+\s+OF\s+\d{4}|No\.?\s+\d{3,6}[,\s]+\d{4})\s*\n',
        text,
        flags=re.IGNORECASE,
    )
    if len(blocks) >= 3:
        return [b.strip() for b in blocks if len(b.strip()) > 80]

    # Fallback: paragraph splits
    blocks = [b.strip() for b in re.split(r'\n{2,}', text) if len(b.strip()) > 80]
    return blocks


# ── Signal writer ──────────────────────────────────────────────────────────────

def _ext_id(url: str) -> str:
    return "bi196:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def _ext_id_block(block: str) -> str:
    return "bi196:pdf:" + hashlib.sha1(block.encode()).hexdigest()[:16]


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


def _write_signal(
    conn: sqlite3.Connection,
    item: dict,
    extra_meta: dict,
    is_priority: bool,
    dry_run: bool,
) -> bool:
    ext_id = item.get("_ext_id") or _ext_id(item["link"])
    if conn.execute("SELECT 1 FROM signals WHERE external_id=?", (ext_id,)).fetchone():
        return False

    sig_id  = str(uuid.uuid4())
    now     = datetime.now(timezone.utc).isoformat()
    content = _strip_tags(item["desc"])[:800]
    title   = _strip_tags(item["title"])[:300]

    notice_fields = _parse_notice(f"{title} {content}")
    metadata = {
        "form_ref":       "BI-196",
        "source_url":     item["link"],
        "gazette_notice": "gpwonline" in item["link"].lower(),
        **notice_fields,
        **extra_meta,
    }

    if not dry_run:
        conn.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, source, external_id, title, content,
                stream, status, source_type, is_priority, timestamp, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig_id, "bi196_collector", ext_id,
                title, content,
                "CRIME_INTEL", "raw", "live",
                1 if is_priority else 0,
                now,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        conn.commit()
        name_note = ""
        if notice_fields.get("name_before") and notice_fields.get("name_after"):
            name_note = f" | {notice_fields['name_before']} → {notice_fields['name_after']}"
        _log.info("  [BI-196] Stored: %s%s", title[:70], name_note)
    else:
        _log.info("  [DRY] Would store: %s", title[:70])

    return True


# ── Layer 1 + 2: RSS sweeps ────────────────────────────────────────────────────

def _fetch_rss(query: str) -> list[dict]:
    encoded = urllib.parse.quote_plus(query)
    url = GOOGLE_NEWS_RSS.format(query=encoded)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            link  = item.findtext("link")  or ""
            desc  = item.findtext("description") or ""
            pub   = item.findtext("pubDate") or ""
            items.append({"title": title, "link": link, "desc": desc, "pub": pub})
        return items
    except Exception as exc:
        _log.debug("RSS fetch failed for %r: %s", query, exc)
        return []


def _scan_gazette_and_dha(conn: sqlite3.Connection, dry_run: bool) -> int:
    written = 0
    for query in _GAZETTE_QUERIES + _DHA_QUERIES:
        _log.info("RSS scan: %s", query)
        items = _fetch_rss(query)
        for item in items[:8]:
            if _write_signal(conn, item, {}, False, dry_run):
                written += 1
        time.sleep(REQ_DELAY)
    return written


# ── Layer 3: Gazette PDF crawl via opengazettes.org.za ────────────────────────

def _fetch_gazette_pdf_urls(year: int, sess) -> list[tuple[str, str]]:
    """
    Fetch the gazette listing page for a given year and return
    (title, pdf_url) pairs for plain government gazette issues.

    Excludes: legal-notices-A/B (deeds registry/POCA/business — wrong type),
              tender-bulletin, road-carrier, liquor-license, regulation-gazette.
    These exclusions are based on confirmed PDF inspection — they contain
    zero surname-change personal notice content.
    """
    from bs4 import BeautifulSoup as _BS
    listing_url = f"{_OPEN_GAZETTES_BASE}/{year}"
    try:
        resp = sess.get(listing_url, timeout=(5, 15))
        resp.raise_for_status()
    except Exception as exc:
        _log.debug("Gazette listing fetch failed %s: %s", listing_url, exc)
        return []

    soup = _BS(resp.text, "html.parser")
    all_pairs = [
        (a.text.strip(), a["href"])
        for a in soup.find_all("a", href=True)
        if "archive.opengazettes.org.za" in a.get("href", "")
        and ".pdf" in a["href"].lower()
    ]

    # Exclude gazette types confirmed to not contain surname-change notices
    filtered = [
        (t, h) for t, h in all_pairs
        if not any(excl in h.lower() for excl in _GAZETTE_EXCLUDE_SLUGS)
    ]

    # If priority slugs are defined, narrow further; otherwise use filtered set
    if _GAZETTE_PRIORITY_SLUGS:
        priority = [
            (t, h) for t, h in filtered
            if any(slug in h.lower() for slug in _GAZETTE_PRIORITY_SLUGS)
        ]
        return priority if priority else filtered

    return filtered


def _gazette_pdf_pass(conn: sqlite3.Connection, dry_run: bool, max_pdfs: int = MAX_PDFS) -> int:
    """
    Crawl opengazettes.org.za (free open archive, 1958–2021), download
    Legal Notice A/C gazette PDFs, extract text via pdfplumber, split into
    notice blocks, run _parse_notice() on any block that matches the BI-196
    keyword pre-filter, and write a signal per confirmed surname-change notice.

    Requires pip packages: pdfplumber requests beautifulsoup4
    No authentication required. Gracefully skips if packages are absent.
    Scans the two most recent available years to balance coverage vs. speed.
    """
    sess = _get_requests_session()
    if sess is None:
        _log.warning("gazette_pdf_pass skipped — 'requests' not installed")
        return 0

    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        _log.warning("gazette_pdf_pass skipped — 'pdfplumber' not installed")
        return 0

    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        _log.warning("gazette_pdf_pass skipped — 'beautifulsoup4' not installed")
        return 0

    # Scan the 2 most recent years in the archive (2021, 2020)
    target_years = [2021, 2020]
    written      = 0
    pdfs_done    = 0
    seen: set[str] = set()

    for year in target_years:
        if pdfs_done >= max_pdfs:
            break

        _log.info("Open Gazettes: fetching Legal Notice index for %d", year)
        pdf_pairs = _fetch_gazette_pdf_urls(year, sess)
        _log.info("  %d Legal Notice PDFs found for %d", len(pdf_pairs), year)

        # Reverse so newest issues come first (most recent gazette number last on page)
        for title, pdf_url in reversed(pdf_pairs):
            if pdfs_done >= max_pdfs:
                break
            if pdf_url in seen:
                continue
            seen.add(pdf_url)

            _log.info("  Downloading: %s", pdf_url[-65:])
            try:
                pr = sess.get(pdf_url, timeout=(5, 60), stream=True)
                pr.raise_for_status()
                pdf_bytes = b""
                for chunk in pr.iter_content(65536):
                    pdf_bytes += chunk
                    if len(pdf_bytes) > MAX_PDF_MB * 1024 * 1024:
                        _log.debug("  Skip — exceeded %dMB", MAX_PDF_MB)
                        pdf_bytes = b""
                        break
            except Exception as exc:
                _log.debug("  Download failed: %s", exc)
                time.sleep(REQ_DELAY)
                continue

            if not pdf_bytes:
                time.sleep(REQ_DELAY)
                continue

            pdfs_done += 1
            text = _extract_pdf_text(pdf_bytes)
            if len(text.strip()) < 200:
                # Likely a scanned/image-based gazette PDF — pdfplumber cannot
                # read these without OCR. Most pre-2015 SA Government Gazette
                # issues are scanned. OCR support would require pytesseract and
                # adds ~30–60 s per page; not practical at MAX_PDFS=15 per run.
                # The laws.africa paid API provides OCR'd full-text search and
                # is the efficient path for these documents.
                _log.debug(
                    "  Sparse text (%d chars) — likely scanned PDF, skipping. "
                    "Set LAWS_AFRICA_TOKEN with a paid subscription for OCR'd content.",
                    len(text.strip()),
                )
                time.sleep(REQ_DELAY)
                continue

            # Broad pre-filter: skip PDFs with no BI-196 related content at all
            tl = text.lower()
            if not any(kw in tl for kw in _BI196_KEYWORDS):
                _log.debug("  No BI-196 keywords — skipping")
                time.sleep(REQ_DELAY)
                continue

            _log.info("  BI-196 keywords found — scanning %d chars", len(text))

            # Gazette PDFs often have multi-column layouts whose extracted text
            # interleaves lines from different columns. Block-splitting fragments
            # notice sentences. Instead, collapse whitespace and scan the full
            # text for every surname-change pattern match directly.
            flat = re.sub(r"\s+", " ", text)   # collapse all whitespace → single space

            seen_pairs: set[tuple[str, str]] = set()
            for pat in _SURNAME_PATTERNS:
                for m in pat.finditer(flat):
                    if pat in (_ASSUME_INLIEU_RE, _ASSUME_INSTEAD_RE, _AFRIKAANS_RE):
                        name_after  = m.group(1).strip().title()
                        name_before = m.group(2).strip().title()
                    else:
                        name_before = m.group(1).strip().title()
                        name_after  = m.group(2).strip().title()

                    # Sanity: names should be 2-30 chars, no stray digits
                    if not (2 <= len(name_before) <= 30 and 2 <= len(name_after) <= 30):
                        continue
                    if re.search(r"\d", name_before + name_after):
                        continue
                    pair = (name_before, name_after)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    # Extract surrounding context (200 chars) for signal content
                    s = max(0, m.start() - 150)
                    context = flat[s: m.end() + 150].strip()

                    # Try to pull SA ID from nearby text
                    id_match = _SA_ID_RE.search(flat[s: m.end() + 200])
                    id_number = id_match.group(1) if id_match else None

                    # Gazette notice number
                    gn = _GAZETTE_NOTICE_RE.search(flat[max(0, m.start()-300): m.start()+50])
                    gazette_ref = None
                    if gn:
                        gazette_ref = f"Notice {gn.group(1)}"
                        if gn.group(2):
                            gazette_ref += f" of {gn.group(2)}"

                    title_str = f"BI-196 Gazette Notice — {name_before} → {name_after}"
                    if gazette_ref:
                        title_str += f" [{gazette_ref}]"

                    # Use (pdf_url + name pair) as dedup key so same person
                    # doesn't create duplicate signals across re-runs
                    dedup_key = f"{pdf_url}|{name_before}|{name_after}"
                    item = {
                        "_ext_id": "bi196:pdf:" + hashlib.sha1(dedup_key.encode()).hexdigest()[:16],
                        "title":   title_str,
                        "link":    pdf_url,
                        "desc":    context[:800],
                        "pub":     datetime.now(timezone.utc).isoformat(),
                    }
                    extra = {
                        "gazette_notice":    True,
                        "from_pdf":          True,
                        "gazette_issue":     title[:100],
                        "open_gazettes_url": pdf_url,
                        "gazette_year":      year,
                        "name_before":       name_before,
                        "name_after":        name_after,
                    }
                    if id_number:
                        extra["id_number"] = id_number
                    if gazette_ref:
                        extra["gazette_ref"] = gazette_ref

                    if _write_signal(conn, item, extra, False, dry_run):
                        written += 1
                        _log.info(
                            "  [BI-196] %s → %s%s",
                            name_before, name_after,
                            f" ID:{id_number}" if id_number else "",
                        )

            time.sleep(REQ_DELAY)

    _log.info(
        "Gazette PDF pass complete: %d PDFs processed, %d signals written",
        pdfs_done, written,
    )
    return written


# ── Layer 4: Actor sweep ───────────────────────────────────────────────────────

def _sweep_actors(conn: sqlite3.Connection, dry_run: bool) -> int:
    actors = conn.execute(
        """SELECT DISTINCT a.name
           FROM actors a
           JOIN case_actors ca ON a.actor_id = ca.actor_id
           JOIN cases c ON ca.case_id = c.case_id
           WHERE c.status != 'closed'
             AND a.confidence_score >= 0.30
             AND a.type IN ('person','unknown')
           ORDER BY a.confidence_score DESC
           LIMIT ?""",
        (MAX_ACTORS,),
    ).fetchall()

    written = 0
    for row in actors:
        name = row[0]
        _log.info("Actor sweep: %s", name)
        query = f'"{name}" "surname" gazette "South Africa"'
        items = _fetch_rss(query)
        for item in items[:4]:
            text = f"{item['title']} {_strip_tags(item['desc'])}"
            if not _SURNAME_CHANGE_RE.search(text) and "bi-196" not in text.lower() and "surname" not in text.lower():
                continue
            if _write_signal(conn, item, {"actor_match": name}, True, dry_run):
                written += 1
        time.sleep(REQ_DELAY)

    return written


def _single_actor(conn: sqlite3.Connection, name: str, dry_run: bool) -> int:
    written = 0
    queries = [
        f'"{name}" "surname" gazette "South Africa"',
        f'"{name}" BI-196 site:gpwonline.co.za',
        f'"{name}" "authority to assume another surname"',
    ]
    for query in queries:
        _log.info("Actor query: %s", query)
        items = _fetch_rss(query)
        for item in items[:6]:
            if _write_signal(conn, item, {"actor_match": name}, True, dry_run):
                written += 1
        time.sleep(REQ_DELAY)
    return written


# ── Entry point ────────────────────────────────────────────────────────────────

def run(
    actor_name:        str | None = None,
    scan_only:         bool = False,
    gazette_pdfs_only: bool = False,
    dry_run:           bool = False,
) -> dict:
    stats = {
        "rss_scan":    0,
        "gazette_pdfs": 0,
        "actor_sweep": 0,
        "total_written": 0,
    }
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        if actor_name:
            stats["actor_sweep"] = _single_actor(conn, actor_name, dry_run)

        elif gazette_pdfs_only:
            stats["gazette_pdfs"] = _gazette_pdf_pass(conn, dry_run)

        elif scan_only:
            stats["rss_scan"] = _scan_gazette_and_dha(conn, dry_run)

        else:
            # Full run: all four layers
            stats["rss_scan"]    = _scan_gazette_and_dha(conn, dry_run)
            stats["gazette_pdfs"] = _gazette_pdf_pass(conn, dry_run)
            stats["actor_sweep"] = _sweep_actors(conn, dry_run)

        stats["total_written"] = sum(
            v for k, v in stats.items() if k != "total_written"
        )
        _log.info("BI-196 collection complete: %s", stats)
    finally:
        conn.close()

    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FORGE DHA BI-196 Surname-Change Collector")
    parser.add_argument("--dry-run",          action="store_true",
                        help="Parse and log without writing to DB")
    parser.add_argument("--scan-only",        action="store_true",
                        help="RSS sweep only (layers 1+2), skip PDF crawl and actor sweep")
    parser.add_argument("--gazette-pdfs-only", action="store_true",
                        help="PDF crawl only (layer 3), skip RSS and actor sweep")
    parser.add_argument("--actor",            type=str, default=None, metavar="NAME",
                        help="Targeted sweep for a specific individual")
    args = parser.parse_args()
    result = run(
        actor_name=args.actor,
        scan_only=args.scan_only,
        gazette_pdfs_only=args.gazette_pdfs_only,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
