"""
bridge_hunt.py — Phase 68/69: Targeted Intelligence Bridge Hunt
===============================================================
Scrapes official law-enforcement and investigative-journalism archives
for artifacts linking Dawie Groenewald, Machana Ronald Shamukuni, and
case reference NG13 into the FORGE signal graph.

PHASE 68 TARGET ARCHIVES
  1. Hawks/DPCI Media Statements   https://www.saps.gov.za/dpci/ms_all.php
  2. NPA Media Centre              https://www.npa.gov.za/media-releases

PHASE 69 TARGET ARCHIVES (Regional Infiltration)
  3. NPA News Portal               https://www.npa.gov.za/news  (paginated)
  4. DFFE Media Releases           https://www.dffe.gov.za/mediarelease
  5. Oxpeckers Trackers Archive    https://oxpeckers.org/category/trackers/

FILTER CRITERIA — phase 68 core
  'groenewald', 'shamukuni', 'ng13', 'ng 13',
  'botswana hunting', 'tcheku', 'dk superior', 'darimon', 'poaching syndicate'

FILTER CRITERIA — phase 69 additions
  'rhino', 'hunting permit', 'poaching', 'wildlife trafficking',
  'dawie', 'cites violation', 'environmental crime'

WHAT IT DOES
  For each matching page/article:
    1. Stores the artifact (title, source, description, type='document').
    2. Creates a signal with gravity scoring via the interpreter.
    3. Links the signal to known Case Alpha actors (955, 963) via signal_actors.
    4. Links to Case Alpha (case_id=44) via case_signals.
    5. Logs a sentinel_alert of type 'bridge_hunt_match'.

USAGE
  python tools/bridge_hunt.py
  python tools/bridge_hunt.py --dry-run
  python tools/bridge_hunt.py --phase 69           # Phase 69 archives only
  python tools/bridge_hunt.py --all                # All archives (68 + 69)
  python tools/bridge_hunt.py --limit 50 --verbose
  python tools/bridge_hunt.py --extra-terms "convicted,sentenced"
"""

import sys
import re
import ssl
import uuid
import json
import time
import argparse
import textwrap
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from core.db.connection import get_connection  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
CASE_ALPHA_ID     = 44
CASE_ALPHA_ACTORS = [955, 963]   # Groenewald, Shamukuni

DEFAULT_FILTER_TERMS = [
    # Phase 68 core
    "groenewald", "shamukuni", "ng13", "ng 13",
    "botswana hunting", "tcheku", "dk superior",
    "darimon", "poaching syndicate",
    # Phase 69 additions — broader environmental/wildlife-crime sweep
    "rhino", "hunting permit", "poaching",
    "wildlife trafficking", "dawie",
    "cites violation", "environmental crime",
    "rhino horn", "illegal hunting",
]

# Primary terms: at least one must match for archives with require_primary=True.
# This prevents Drupal/SPA sites from flooding the DB with template false positives.
PRIMARY_FILTER_TERMS = [
    "groenewald", "shamukuni", "ng13", "ng 13",
    "botswana hunting", "tcheku", "dk superior",
    "darimon", "dawie",
    # Poaching syndicate is specific enough to count
    "poaching syndicate",
]

# ── Archives — Phase 68 ───────────────────────────────────────────────────────
P68_ARCHIVES = [
    {
        "name":    "Hawks/DPCI Media Statements",
        "url":     "https://www.saps.gov.za/dpci/ms_all.php",
        "source":  "DPCI",
        "stream":  "CRIME_INTEL",
        "phase":   68,
    },
    {
        "name":    "NPA Media Centre",
        "url":     "https://www.npa.gov.za/media-releases",
        "source":  "NPA",
        "stream":  "CRIME_INTEL",
        "phase":   68,
    },
]

# ── Archives — Phase 69 ───────────────────────────────────────────────────────
P69_ARCHIVES = [
    {
        "name":       "NPA News Portal",
        "url":        "https://www.npa.gov.za/news",
        "source":     "NPA",
        "stream":     "CRIME_INTEL",
        "phase":      69,
        # Try paginating through the news archive
        "paginate":         True,
        "page_pattern":     "https://www.npa.gov.za/news?page={n}",
        "max_pages":        15,
    },
    {
        "name":           "DFFE Media Releases",
        "url":            "https://www.dffe.gov.za/mediarelease",
        "source":         "DFFE",
        "stream":         "ENVIRON_INTEL",
        "phase":          69,
        "ssl_bypass":     True,           # DFFE has cert issues
        "require_primary": True,          # Drupal SPA: require actor-name match
        # Also try the alternate news room path
        "alt_urls": [
            "https://www.dffe.gov.za/newsroom/mediareleases",
        ],
    },
    {
        "name":        "Oxpeckers Trackers Archive",
        "url":         "https://oxpeckers.org/category/trackers/",
        "source":      "OXPECKERS",
        "stream":      "ENVIRON_INTEL",
        "phase":       69,
        # Incapsula WAF expected — will detect & log; try anyway
        "waf_expected": True,
        # Pagination: /category/trackers/page/N/
        "paginate":         True,
        "page_pattern":     "https://oxpeckers.org/category/trackers/page/{n}/",
        "max_pages":        8,
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-ZA,en;q=0.9",
    "Referer": "https://www.google.com/",
}

REQUEST_TIMEOUT    = 25
INTER_REQUEST_DELAY = 1.5   # seconds between requests — be polite

# Incapsula WAF fingerprint: challenge iframe is ≤ 1200 bytes with this marker
_WAF_MARKERS = [b"incapsula", b"/_Incapsula_Resource", b"visitorId"]


# ─────────────────────────────────────────────────────────────────────────────
# HTML EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class _LinkExtractor(HTMLParser):
    """Extract (href, link_text) pairs from an HTML page."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.links: list[tuple[str, str]] = []
        self._current_href: Optional[str] = None
        self._current_text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href and not href.startswith("#") and not href.startswith("javascript"):
                if href.startswith("http"):
                    self._current_href = href
                elif href.startswith("/"):
                    parsed = urllib.parse.urlparse(self.base_url)
                    self._current_href = f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    self._current_href = f"{self.base_url}/{href.lstrip('/')}"
                self._current_text_parts = []

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href:
            text = " ".join(self._current_text_parts).strip()
            if text:
                self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text_parts = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text_parts.append(data.strip())


def _strip_html(html: str) -> str:
    """Very light HTML → plain-text strip."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_waf_blocked(raw_bytes: bytes) -> bool:
    """Return True if the response looks like an Incapsula/WAF challenge."""
    if len(raw_bytes) > 4096:
        return False   # real page, not a tiny challenge page
    low = raw_bytes.lower()
    return any(marker in low for marker in _WAF_MARKERS)


def _make_ssl_ctx(bypass: bool = False):
    """Return an SSL context. If bypass=True, skip cert verification."""
    if bypass:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None  # urllib default


def _fetch(url: str, verbose: bool = False,
           ssl_bypass: bool = False,
           waf_expected: bool = False) -> Optional[str]:
    """
    Fetch a URL, return HTML string or None on failure.
    Handles SSL bypass for DFFE and WAF detection for Oxpeckers.
    """
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        ctx = _make_ssl_ctx(bypass=ssl_bypass)
        kw  = {"timeout": REQUEST_TIMEOUT}
        if ctx:
            kw["context"] = ctx

        with urllib.request.urlopen(req, **kw) as resp:
            charset = "utf-8"
            ct = resp.headers.get("Content-Type", "")
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].strip().split(";")[0]
            raw = resp.read()

            # WAF detection
            if _is_waf_blocked(raw):
                if waf_expected:
                    print(f"    [waf-block] Incapsula challenge detected — {url[:70]}")
                else:
                    print(f"    [waf-block] Unexpected WAF challenge — {url[:70]}")
                return None

            html = raw.decode(charset, errors="replace")
            if verbose:
                print(f"    [fetch] {url[:80]}  ({len(html):,} bytes)")
            return html

    except urllib.error.HTTPError as e:
        print(f"    [http-error] {url[:80]}  status={e.code}")
        return None
    except ssl.SSLError as e:
        if not ssl_bypass:
            # Retry with SSL bypass
            if verbose:
                print(f"    [ssl-retry] {url[:70]} — retrying without cert verify")
            return _fetch(url, verbose=verbose, ssl_bypass=True, waf_expected=waf_expected)
        print(f"    [ssl-error] {url[:80]}  {e}")
        return None
    except Exception as e:
        print(f"    [fetch-error] {url[:80]}  {e}")
        return None


def _matches_filter(text: str, terms: list[str]) -> list[str]:
    """Return list of matched filter terms (case-insensitive). Empty = no match."""
    lower = text.lower()
    return [t for t in terms if t in lower]


# ─────────────────────────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_artifact(conn, title: str, description: str, source: str) -> int:
    """Insert artifact if not exists by title, return artifact_id."""
    existing = conn.execute(
        "SELECT artifact_id FROM artifacts WHERE title = ? AND source = ? LIMIT 1",
        (title, source)
    ).fetchone()
    if existing:
        return existing["artifact_id"]
    cur = conn.execute(
        """INSERT INTO artifacts (title, description, type, source, source_type, processing_status)
           VALUES (?, ?, 'document', ?, 'web', 'pending')""",
        (title, description, source)
    )
    return cur.lastrowid


def _ensure_signal(conn, title: str, external_id: str, source: str,
                   stream: str, artifact_id: int, gravity: float,
                   url: str) -> tuple[str, bool]:
    """Insert signal if not exists by external_id, return (signal_id, is_new)."""
    existing = conn.execute(
        "SELECT signal_id FROM signals WHERE external_id = ? LIMIT 1",
        (external_id,)
    ).fetchone()
    if existing:
        return existing["signal_id"], False

    signal_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO signals
           (signal_id, external_id, title, source, source_type, stream,
            gravity_score, relevance_score, status, timestamp,
            conclave_meta, metadata_json)
           VALUES (?, ?, ?, ?, 'web', ?, ?, 1.0, 'raw', ?, ?, ?)""",
        (
            signal_id, external_id, title[:500], source, stream,
            round(gravity, 4),
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"stage": "bridge_hunt_p69", "source_url": url}),
            json.dumps({"source_url": url}),
        )
    )
    return signal_id, True


def _link_signal_actors(conn, signal_id: str, actor_ids: list[int]) -> int:
    """Link signal to actors via signal_actors; returns new links created."""
    new = 0
    for aid in actor_ids:
        cur = conn.execute(
            "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, 'bridge_hunt')",
            (signal_id, aid)
        )
        new += cur.rowcount
    return new


def _link_case_signal(conn, case_id: int, signal_id: str) -> bool:
    """Link signal to a case via case_signals; return True if newly linked."""
    exists_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_signals'"
    ).fetchone()
    if not exists_tbl:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO case_signals (case_id, signal_id, note, pinned_at) VALUES (?, ?, ?, ?)",
        (case_id, signal_id, "bridge_hunt_p69 auto-link",
         datetime.now(timezone.utc).isoformat())
    )
    return cur.rowcount > 0


def _create_sentinel_alert(conn, signal_id: str, title: str,
                            matched: list[str], source: str):
    """Create a sentinel alert for each bridge hunt match."""
    try:
        conn.execute(
            """INSERT INTO sentinel_alerts
               (alert_type, signal_id, description, severity, created_at)
               VALUES ('bridge_hunt_match', ?, ?, 'high', ?)""",
            (
                signal_id,
                f"Bridge Hunt P69: [{source}] '{title[:120]}' matched: {', '.join(matched)}",
                datetime.now(timezone.utc).isoformat(),
            )
        )
    except Exception as e:
        print(f"    [alert-warn] {e}")


def _try_ner_triple(conn, signal_id: str, title: str, body: str,
                    source: str, dry_run: bool) -> int:
    """
    Phase 69 NER/Triple Bridge: for high-confidence institutional hits,
    attempt to materialise PROSECUTED_BY / ARRESTED_BY edges between
    Groenewald [955] and NPA [129] / Hawks [39].

    Returns count of new entity_relationships created.
    """
    NPA_ACTOR_ID   = 129
    HAWKS_ACTOR_ID = 39
    GROENEWALD_ID  = 955

    text_lower = (title + " " + body).lower()
    new_edges = 0

    # NPA institutional hit
    npa_hit = any(t in text_lower for t in
                  ["npa", "national prosecuting authority",
                   "prosecutor", "prosecuted", "charged by state"])
    # Hawks institutional hit
    hawks_hit = any(t in text_lower for t in
                    ["hawks", "dpci", "directorate for priority crime",
                     "arrested by hawks", "investigat"])
    # Groenewald name in body (sanity check)
    groenewald_hit = "groenewald" in text_lower

    if not groenewald_hit:
        return 0

    def _add_er(subj, obj, rel):
        nonlocal new_edges
        if dry_run:
            print(f"    [ner-dry] would add ER: actor[{subj}] —{rel}→ actor[{obj}]")
            new_edges += 1
            return
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO entity_relationships
                   (subject_actor_id, relation_type, object_actor_id,
                    source_signal_id, confidence, created_at)
                   VALUES (?, ?, ?, ?, 0.65, ?)""",
                (subj, rel, obj, signal_id,
                 datetime.now(timezone.utc).isoformat())
            )
            if cur.rowcount:
                new_edges += 1
                print(f"    [ner] +ER: actor[{subj}] —{rel}→ actor[{obj}]")
        except Exception as e:
            print(f"    [ner-warn] {e}")

    if npa_hit:
        _add_er(GROENEWALD_ID, "PROSECUTED_BY", NPA_ACTOR_ID)
        _add_er(NPA_ACTOR_ID,  "PROSECUTES",    GROENEWALD_ID)
    if hawks_hit:
        _add_er(GROENEWALD_ID, "ARRESTED_BY",  HAWKS_ACTOR_ID)
        _add_er(HAWKS_ACTOR_ID, "ARRESTED",    GROENEWALD_ID)

    return new_edges


# ─────────────────────────────────────────────────────────────────────────────
# GRAVITY ESTIMATE (lightweight, no full conclave)
# ─────────────────────────────────────────────────────────────────────────────

def _estimate_gravity(title: str, matched_terms: list[str], source: str) -> float:
    """
    Quick gravity estimate for bridge-hunt artifacts.
    Full pipeline rescoring can be run via rescore_signals_from_db later.
    """
    base = 0.30
    if any(t in ("groenewald", "shamukuni", "dawie") for t in matched_terms):
        base += 0.25
    if any(t in ("ng13", "ng 13") for t in matched_terms):
        base += 0.20
    if any(t in ("tcheku", "dk superior", "botswana hunting") for t in matched_terms):
        base += 0.10
    if any(t in ("rhino horn", "wildlife trafficking", "cites violation") for t in matched_terms):
        base += 0.12
    if any(t in ("poaching syndicate", "poaching") for t in matched_terms):
        base += 0.08
    # High-credibility sources
    if source in ("NPA", "DPCI"):
        base += 0.10
    if source == "OXPECKERS":
        base += 0.08   # award-winning investigative journalism
    return min(base, 0.95)


# ─────────────────────────────────────────────────────────────────────────────
# PAGINATOR
# ─────────────────────────────────────────────────────────────────────────────

def _collect_pages(archive: dict, verbose: bool) -> list[str]:
    """
    Collect all index-page URLs to scrape for this archive.
    Handles pagination if 'paginate' is set, plus 'alt_urls'.
    """
    urls = [archive["url"]]

    # Alt URLs (DFFE has multiple possible paths)
    for alt in archive.get("alt_urls", []):
        urls.append(alt)

    # Pagination
    if archive.get("paginate") and archive.get("page_pattern"):
        for n in range(1, archive.get("max_pages", 10) + 1):
            urls.append(archive["page_pattern"].format(n=n))

    return urls


# ─────────────────────────────────────────────────────────────────────────────
# ARCHIVE SCRAPER
# ─────────────────────────────────────────────────────────────────────────────

def scrape_archive(
    archive: dict,
    filter_terms: list[str],
    limit: int,
    dry_run: bool,
    verbose: bool,
    conn,
) -> dict:
    """
    Scrape one archive (potentially multiple pages).  For each link whose
    text or body matches filter_terms, store artifact + signal.
    Returns summary dict.
    """
    name            = archive["name"]
    source          = archive["source"]
    stream          = archive["stream"]
    ssl_bypass      = archive.get("ssl_bypass", False)
    waf_expected    = archive.get("waf_expected", False)
    require_primary = archive.get("require_primary", False)

    print(f"\n  ARCHIVE : {name}  [Phase {archive.get('phase', '?')}]")
    print(f"  Source  : {source}")

    summary = {
        "archive":       name,
        "phase":         archive.get("phase", 0),
        "links_checked": 0,
        "matched":       0,
        "new_signals":   0,
        "new_links":     0,
        "new_er_edges":  0,
        "waf_blocked":   0,
        "errors":        0,
    }

    index_urls = _collect_pages(archive, verbose)
    checked    = 0
    seen_hrefs: set[str] = set()   # deduplicate across pages

    for page_url in index_urls:
        if checked >= limit:
            break

        if verbose:
            print(f"\n    [page] {page_url}")

        html = _fetch(page_url, verbose=verbose,
                      ssl_bypass=ssl_bypass, waf_expected=waf_expected)
        if not html:
            if waf_expected:
                summary["waf_blocked"] += 1
            else:
                summary["errors"] += 1
            # If first page of main URL already blocked, bail out of archive
            if page_url == archive["url"] and not html:
                print(f"    [skip] Cannot reach primary URL — skipping archive")
                break
            continue

        extractor = _LinkExtractor(page_url)
        extractor.feed(html)
        links = extractor.links

        if verbose:
            print(f"    Extracted {len(links)} links from {page_url[:60]}")

        page_had_new = False
        for href, link_text in links:
            if checked >= limit:
                break
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            # Quick filter on link text first (cheap)
            matched_in_text = _matches_filter(link_text, filter_terms)

            if not matched_in_text:
                time.sleep(INTER_REQUEST_DELAY)
                article_html = _fetch(href, verbose=verbose,
                                      ssl_bypass=ssl_bypass,
                                      waf_expected=waf_expected)
                if not article_html:
                    summary["errors"] += 1
                    checked += 1
                    continue
                article_text  = _strip_html(article_html)
                matched_in_body = _matches_filter(article_text, filter_terms)
                if not matched_in_body:
                    checked += 1
                    summary["links_checked"] += 1
                    continue
                matched     = matched_in_body
                description = article_text[:800]
                body_text   = article_text
            else:
                matched     = matched_in_text
                description = link_text
                body_text   = link_text

            # Primary-term guard: for SPA/template-heavy sites, require at least
            # one actor or case-specific term to fire — prevents flooding the DB
            # with generic environmental content that matches "rhino"/"poaching"
            # from a site-wide navigation template.
            if require_primary:
                primary_hits = _matches_filter(
                    (link_text + " " + body_text).lower(),
                    PRIMARY_FILTER_TERMS
                )
                if not primary_hits:
                    checked += 1
                    summary["links_checked"] += 1
                    if verbose:
                        print(f"    [primary-gate] skipped (no actor/case name): {href[:60]}")
                    continue

            page_had_new = True
            checked += 1
            summary["links_checked"] += 1
            summary["matched"]       += 1

            title   = link_text[:300] if link_text else href[:300]
            gravity = _estimate_gravity(title.lower(), matched, source)

            print(f"\n    [MATCH] \"{title[:70]}\"")
            print(f"            matched={matched}  gravity={gravity:.3f}")
            print(f"            url={href[:80]}")

            if dry_run:
                # NER triple preview even in dry-run
                _try_ner_triple(None, None, title, body_text,
                                source, dry_run=True)
                continue

            time.sleep(INTER_REQUEST_DELAY)

            artifact_id = _ensure_artifact(conn, title, description, source)
            external_id = f"bridge_hunt:{source}:{hash(href) & 0xFFFFFFFF}"
            signal_id, is_new = _ensure_signal(
                conn, title, external_id, source, stream,
                artifact_id, gravity, href
            )
            if is_new:
                summary["new_signals"] += 1

            new_links = _link_signal_actors(conn, signal_id, CASE_ALPHA_ACTORS)
            summary["new_links"] += new_links

            _link_case_signal(conn, CASE_ALPHA_ID, signal_id)
            _create_sentinel_alert(conn, signal_id, title, matched, source)

            # NER/Triple Bridge — materialise institutional edges
            er_count = _try_ner_triple(conn, signal_id, title, body_text,
                                       source, dry_run=False)
            summary["new_er_edges"] += er_count

            conn.commit()

        # If a paginated archive returned an empty page, stop paginating
        if archive.get("paginate") and not page_had_new and page_url != archive["url"]:
            if verbose:
                print(f"    [paginate] No new links on {page_url[:60]} — stopping")
            break

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="FORGE Bridge Hunt — Phase 68/69 targeted Case Alpha ingestion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python tools/bridge_hunt.py --dry-run              # Phase 69 archives only
              python tools/bridge_hunt.py --all --dry-run        # All archives
              python tools/bridge_hunt.py --phase 68             # Phase 68 only (DPCI/NPA)
              python tools/bridge_hunt.py --phase 69             # Phase 69 only (new seeds)
              python tools/bridge_hunt.py                        # Phase 69 (default)
              python tools/bridge_hunt.py --limit 20 --verbose
        """),
    )
    ap.add_argument("--dry-run",     action="store_true",
                    help="Scan and report matches without writing to DB")
    ap.add_argument("--verbose",     action="store_true",
                    help="Print every link checked")
    ap.add_argument("--limit",       type=int, default=300,
                    help="Max links to check per archive (default 300)")
    ap.add_argument("--phase",       type=int, default=69,
                    choices=[68, 69],
                    help="Run a specific phase's archives (default 69)")
    ap.add_argument("--all",         action="store_true",
                    help="Run all archives across all phases")
    ap.add_argument("--extra-terms", type=str, default="",
                    help="Additional comma-separated filter terms")
    args = ap.parse_args()

    filter_terms = list(DEFAULT_FILTER_TERMS)
    if args.extra_terms:
        filter_terms += [t.strip().lower() for t in args.extra_terms.split(",") if t.strip()]

    if args.all:
        archives = P68_ARCHIVES + P69_ARCHIVES
        label = "ALL PHASES (68 + 69)"
    elif args.phase == 68:
        archives = P68_ARCHIVES
        label = "Phase 68"
    else:
        archives = P69_ARCHIVES
        label = "Phase 69 — Regional Infiltration"

    print("=" * 72)
    print(f"  FORGE BRIDGE HUNT — {label}")
    print(f"  Mode      : {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"  Targets   : {len(archives)} archives")
    print(f"  Filter    : {len(filter_terms)} terms")
    print(f"  Limit/arc : {args.limit}")
    print("=" * 72)
    if args.verbose:
        print(f"  Terms: {filter_terms}")

    conn = None if args.dry_run else get_connection()

    all_summaries = []
    for archive in archives:
        summary = scrape_archive(
            archive      = archive,
            filter_terms = filter_terms,
            limit        = args.limit,
            dry_run      = args.dry_run,
            verbose      = args.verbose,
            conn         = conn,
        )
        all_summaries.append(summary)

    if conn:
        conn.close()

    print("\n" + "=" * 72)
    print("  BRIDGE HUNT SUMMARY")
    print("=" * 72)
    total_matched   = 0
    total_signals   = 0
    total_er_edges  = 0
    for s in all_summaries:
        print(f"  {s['archive']}  [Phase {s['phase']}]")
        print(f"    Links checked : {s['links_checked']}")
        print(f"    Matched       : {s['matched']}")
        print(f"    New signals   : {s['new_signals']}")
        print(f"    Actor links   : {s['new_links']}")
        print(f"    New ER edges  : {s['new_er_edges']}")
        if s.get("waf_blocked"):
            print(f"    WAF blocked   : {s['waf_blocked']}")
        if s["errors"]:
            print(f"    Errors        : {s['errors']}")
        total_matched  += s["matched"]
        total_signals  += s["new_signals"]
        total_er_edges += s["new_er_edges"]

    print("-" * 72)
    print(f"  TOTAL MATCHES    : {total_matched}")
    print(f"  TOTAL NEW SIGNALS: {total_signals}")
    print(f"  TOTAL ER EDGES   : {total_er_edges}")
    if args.dry_run:
        print("  [DRY-RUN] No writes committed.")
    print()


if __name__ == "__main__":
    main()
