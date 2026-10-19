#!/usr/bin/env python3
from __future__ import annotations
"""
ZA-DIVERGENT Publisher
Queries FORGE's DB for published signals and articles, renders static HTML to dist/,
and optionally pushes to the publication branch on GitHub for Vercel auto-deploy.

Usage:
    python tools/publish.py              # generate dist/ only
    python tools/publish.py --deploy     # generate dist/ + push to origin/publication
"""

import argparse
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt

# Revenue module — optional, graceful fallback if not present
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
try:
    from revenue.config import get_template_context as _get_revenue_context
except ImportError:
    def _get_revenue_context() -> dict:
        return {
            "revenue_live": False, "membership_url": None, "membership_label": "",
            "membership_sim": False, "sponsor_slots": [], "payment_checkout_url": None,
        }

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = pathlib.Path(__file__).parent.parent
# FORGE_DB points the publisher at another database (an isolated test copy),
# the same variable the engines, app.py and checked_signals.py read.
DB_PATH     = (pathlib.Path(os.environ["FORGE_DB"]).expanduser().resolve()
               if os.environ.get("FORGE_DB", "").strip() else ROOT / "database.db")
MEDIA_DIR   = ROOT / "media"
PUBLISHER   = ROOT / "publisher"
TMPL_DIR    = PUBLISHER / "templates"
STATIC_SRC  = PUBLISHER / "static"
DIST        = ROOT / "dist"
DIST_STATIC = DIST / "static"
DIST_ART    = DIST / "articles"
DIST_CASES  = DIST / "cases"
DIST_ENTITIES = DIST / "entities"

VERCEL_DEPLOY_HOOK = os.environ.get("VERCEL_DEPLOY_HOOK_URL", "")

# SA cases to publish — cases 1-6 are global/seed/auto-generated noise
# Cleared 18 Oct 2026: the old cases (7-17) were retired from the public site.
# Their numbers belonged to the old database, which has been wiped, so they
# would point at the wrong cases in a fresh one. Add a case's ID here when it
# is ready to be published.
# Previously: (7, 8, 9, 10, 11, 12, 13, 15, 16, 17)
PUBLISHED_CASE_IDS: tuple[int, ...] = ()

# Entity infobox: relationship-derived rows (Position/Affiliation from
# entity_relationships, co-occurrence from graph_edges). Off by default —
# NER coverage is currently 1.4% of signals (83/5835) and extracted entity
# text doesn't exact-match actors.name, so this data is too sparse/unreliable
# to publish. Flip on once NER coverage + name normalization are fixed.
ENABLE_INFOBOX_RELATIONSHIPS = False

# Maps signal_id → article slug for "Read analysis →" on timeline signal cards
SIGNAL_ARTICLE_MAP: dict[str, str] = {
    # Cleared 18 Oct 2026 with the old content. The only page still served is
    # a direct-link-only case study (see DIRECT_LINK_ONLY_ARTICLES below); its
    # signal stays mapped so it is recognised and kept off the map and ticker.
    # Limpopo / Mkhwanazi
    "c4d50e84-59ff-4a74-b608-1709feb2402b": "madlanga-hawks-limpopo-municipal-fraud",
}


# ── Stream labels (public-facing) ─────────────────────────────────────────────
# Articles kept live as case studies: slug -> banner HTML shown above the headline
CASE_STUDY_NOTES: dict[str, str] = {
    "madlanga-hawks-limpopo-municipal-fraud": "<strong>Case study:</strong> this report contains errors; see <a href='limpopo-procurement-arrests-v2.html' style='color:inherit;text-decoration:underline'>the follow-up report</a>.",
}


# Direct-link-only pages (18 Oct 2026). These are the only old pages kept,
# because the OSINT blog (osint-platform-sage.vercel.app) links to them:
#   - madlanga-hawks-limpopo-municipal-fraud  <- post "A polished report is not a checked report"
#   - limpopo-procurement-arrests-v2          <- post "Checking the check"
#   - cases/limpopo-municipal-procurement-arrests-april-2026-rerun-v2
#                                             <- linked from the v2 report and described in "Checking the check"
# Their HTML files stay in dist/ (this publisher never deletes files from
# dist/), and an article is still re-rendered if it is in the database. They
# are kept off every listing: map markers, ticker, Wire (Top and Archive),
# Streams counts, Cases, Graph, Entities, feed.json and search-index.json.
# Do not delete these files without updating the blog first.
DIRECT_LINK_ONLY_ARTICLES: frozenset[str] = frozenset({
    "madlanga-hawks-limpopo-municipal-fraud",
    "limpopo-procurement-arrests-v2",
})
DIRECT_LINK_ONLY_CASES: frozenset[str] = frozenset({
    "limpopo-municipal-procurement-arrests-april-2026-rerun-v2",
})

# Uncased people (18 Oct 2026). Processing (tools/mega_ingest.py ->
# core/pipeline/ingest.py) auto-creates person actors from name extraction,
# e.g. a president mentioned in passing, at confidence 0.5. Graph and Entities
# used to publish every such person, with an entity page, even when no
# published case links them. That puts people on the site with no checked
# source. Keep this False: only actors on a case in PUBLISHED_CASE_IDS appear.
PUBLISH_UNCASED_PERSONS: bool = False


def _uncased_person_sql() -> str:
    """Extra OR-clause that admits uncased people, or nothing (the default)."""
    if PUBLISH_UNCASED_PERSONS:
        return "OR (a.type = 'person' AND a.confidence_score >= 0.35)"
    return ""


STREAM_LABELS = {
    "CRIME_INTEL":    "Crime & Security",
    "INFRASTRUCTURE": "Infrastructure",
    "PRIORITY":       "Priority Intelligence",
    "GLOBAL":         "Africa & World",
}

md_parser = MarkdownIt()

# ── Source label mapping (collector ID → human-readable) ─────────────────────
SOURCE_LABELS: dict[str, str] = {
    "civic_intel_collector":    "News Wire",
    "civic_intel":              "News Wire",
    "rss_collector":            "RSS Feed",
    "rss":                      "RSS Feed",
    "forge_incident":           "Incident Report",
    "saflii":                   "Court Records",
    "dork_collector":           "Web Intelligence",
    "dork":                     "Web Intelligence",
    "gdelt_collector":          "GDELT",
    "gdelt":                    "GDELT",
    "acled_collector":          "ACLED",
    "acled":                    "ACLED",
    "firms_collector":          "Satellite Fire",
    "earthquake_collector":     "Seismic",
    "usgs_collector":           "USGS",
    "disease_outbreak_collector": "Health Alert",
    "ndbc_collector":           "Marine Buoy",
    "cipc_collector":           "Company Registry",
    "pdf_infiltrator":          "Document Scan",
    "bi196_collector":          "SA Gazette",
    "x_pulse":                  "X / Social",
}


def _source_label(raw: str) -> str:
    """Map raw collector source ID to a human-readable label."""
    key = (raw or "").lower().strip()
    return SOURCE_LABELS.get(key, raw.upper() if raw else "UNKNOWN")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _actor_initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return parts[0][0].upper() if parts else "?"


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)[:80]
    return text.rstrip("-")


def _fmt_dt(val: str | None) -> str:
    if not val:
        return "-"
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y - %H:%M")
    except (ValueError, AttributeError):
        return str(val)[:16]


def _strip_html(text: str | None) -> str:
    """Strip any residual HTML tags from stored content (e.g. ReliefWeb RSS markup)."""
    if not text:
        return ""
    # Remove tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace left by removed tags
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean


def _truncate(text: str | None, length: int = 220) -> str:
    if not text:
        return ""
    text = _strip_html(text).strip()
    return text[:length] + "..." if len(text) > length else text


# The event drawer shows a hand-checked signal's own written story in full.
# Capped so a runaway body can't bloat the map page; cut on a word boundary.
STORY_MAX_CHARS = 2000


def _story(text: str | None, limit: int = STORY_MAX_CHARS) -> str:
    """Full story text for the event drawer: HTML stripped, capped at ~limit chars."""
    text = _strip_html(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def _source_domain(url: str) -> str:
    """Website shown beside a source's name, e.g. 'iol.co.za' (leading www. dropped)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# Media the drawer may show: only files FORGE hosts itself (no hotlinking of
# news photos), and only with a credit and a licence/permission line.
_MEDIA_SRC_RE = re.compile(r"^static/media/[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _clean_media(items) -> list[dict]:
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        credit = str(item.get("credit") or "").strip()
        licence = str(item.get("licence") or item.get("license") or "").strip()
        if not (src and credit and licence) or ".." in src or not _MEDIA_SRC_RE.match(src):
            continue
        out.append({
            "src": src,
            "credit": credit,
            "licence": licence,
            "caption": str(item.get("caption") or "").strip(),
        })
    return out


# ── DB queries ────────────────────────────────────────────────────────────────

def _incident_at(chk: dict) -> str | None:
    from tools.checked_signals import incident_sort_key
    return incident_sort_key(chk) or None


def _checked_fields(metadata_json: str | None) -> dict:
    """Hand-check details for a signal, read from metadata_json["checked"].

    A signal checked by hand carries, in its metadata_json:
      "checked": {"incident": "Fri 16 Oct 2026, about 06:00",
                  "reported": "17-18 Oct 2026",
                  "location": "Fisher Avenue, Epping 1 (exact spot not given)",
                  "sources": [{"name": "Cape Times (IOL)", "url": "https://..."}]}
    The map shows the incident date apart from the report date, where the pin
    really is, and links to the sources. Signals without it render as before.
    Only http(s) source links are kept; each gets its website ("domain").

    Optional, for the event drawer (signals without them keep working):
      sources[].date   - the outlet's own publication date, shown on its row
      "basis"          - what the reporting rests on, e.g. "all based on police
                         statements"; shown beside the report count
      "disagreements"  - list of short notes where the reports differ
      "search_aliases" - other place names the reports use; added to the
                         search index only, so the pin is found by either
      "media"          - [{"src": "static/media/...", "credit": "...",
                          "licence": "...", "caption": "..."}]; FORGE-hosted
                         files only, dropped without a credit and licence
    """
    empty = {"checked": False, "incident": None, "reported": None,
             "location_note": None, "source_links": [], "basis": None,
             "disagreements": [], "media": [], "incident_start": None,
             "link_records": [], "search_aliases": []}
    if not metadata_json:
        return empty
    try:
        meta = json.loads(metadata_json)
    except (TypeError, ValueError):
        return empty
    chk = meta.get("checked") if isinstance(meta, dict) else None
    if not isinstance(chk, dict):
        return empty
    links = []
    for src in chk.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            continue
        link = {"name": str(src.get("name") or "Source").strip(), "url": url,
                "domain": _source_domain(url)}
        if src.get("date"):
            link["date"] = str(src["date"]).strip()
        links.append(link)
    disagreements = chk.get("disagreements")
    if not isinstance(disagreements, list):
        disagreements = []
    aliases = chk.get("search_aliases")
    if not isinstance(aliases, list):
        aliases = []
    return {
        "checked":       True,
        "incident":      (str(chk["incident"]).strip() or None) if chk.get("incident") else None,
        "reported":      (str(chk["reported"]).strip() or None) if chk.get("reported") else None,
        "location_note": (str(chk["location"]).strip() or None) if chk.get("location") else None,
        "source_links":  links,
        "basis":         (str(chk["basis"]).strip() or None) if chk.get("basis") else None,
        "disagreements": [str(d).strip() for d in disagreements if str(d).strip()],
        "media":         _clean_media(chk.get("media")),
        # Sorting key for pattern cases (the incident, not the report).
        "incident_start": (str(chk["incident_start"]).strip() or None) if chk.get("incident_start") else None,
        # Machine-readable incident moment in UTC (Sprint 4); sorts by incident.
        "incident_at":   _incident_at(chk),
        # Raw link records; _resolve_links() turns them into drawer rows and
        # map lines, then drops this key so it never reaches the page.
        "link_records":  _clean_link_records(chk.get("links")),
        # Other names the reports use for the place (Sprint 5): searchable,
        # never shown as the pin's area.
        "search_aliases": [str(a).strip() for a in aliases if str(a).strip()],
    }


def _signal_search_target(s: dict) -> dict:
    """Where a signal's search result goes.

    A signal with a pin opens the map with its event drawer showing
    (index.html?signal=<first 8 of signal_id>, the same link the ticker uses).
    A signal without a pin goes to its analysis page, or to the map when it
    has none. Before this, every signal pointed at bare index.html, so a
    click just reloaded the map.
    """
    sid = str(s.get("signal_id") or "")[:8]
    if sid and s.get("lat") and s.get("lng"):
        return {"url": f"index.html?signal={sid}", "signal": sid}
    if s.get("article_slug"):
        return {"url": f"articles/{s['article_slug']}.html"}
    return {"url": "index.html"}


# ── Link records (Sprint 3) ──────────────────────────────────────────────────
# A line on the map says two incidents are connected, so only an approved
# incident link with a quote and a source draws one. Shared sources (the same
# police statement, spokesperson, reporter) are a paper trail, and similar
# patterns are a person's observation: both show as labelled text in the
# drawer and never as a line. Being in the same case draws nothing.
# tools/checked_signals.py validates the records before they reach the DB;
# this re-checks the shape so a hand-edited database can't draw a line.

LINK_KIND_LABELS = {
    "incident":        "Linked incident",
    "shared_source":   "Shared source",
    "similar_pattern": "Similar pattern",
}


def _clean_link_records(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for ln in raw:
        if not isinstance(ln, dict):
            continue
        kind = ln.get("type")
        to = str(ln.get("to") or "").strip().lower()
        label = str(ln.get("label") or "").strip()[:80]
        basis = ln.get("basis") if isinstance(ln.get("basis"), dict) else {}
        quote = str(basis.get("quote") or "").strip()
        url = str(basis.get("url") or "").strip()
        if not url.lower().startswith(("https://", "http://")):
            url = ""
        if kind not in LINK_KIND_LABELS or not to or not label:
            continue
        if not str(ln.get("approved_by") or "").strip():
            continue                      # unapproved: shown nowhere
        if kind == "incident" and not (quote and url):
            continue
        if not (quote or url):
            continue
        out.append({"to": to, "type": kind, "label": label, "quote": quote, "url": url})
    return out


def _resolve_links(signals: list[dict]) -> list[dict]:
    """Attach each published pin's related pins (both directions) for the
    drawer and return the map lines: one per approved incident link between
    two published pins that have coordinates."""
    by_id = {s["signal_id"].lower(): s for s in signals}
    for s in signals:
        s.setdefault("related", [])
    lines, seen = [], set()
    for s in signals:
        for ln in s.pop("link_records", None) or []:
            other = by_id.get(ln["to"])
            if other is None or other is s:
                continue                  # target retracted or unpublished
            key = (min(s["signal_id"], other["signal_id"]), max(s["signal_id"], other["signal_id"]),
                   ln["type"], ln["label"].lower())
            if key in seen:
                continue
            seen.add(key)
            for a, b in ((s, other), (other, s)):
                a["related"].append({
                    "type":       ln["type"],
                    "kind_label": LINK_KIND_LABELS[ln["type"]],
                    "label":      ln["label"],
                    "quote":      ln["quote"],
                    "url":        ln["url"],
                    "signal":     b["signal_id"][:8],
                    "title":      b["title"],
                })
            if (ln["type"] == "incident" and s["lat"] and s["lng"]
                    and other["lat"] and other["lng"]):
                lines.append({
                    "from_lat": s["lat"], "from_lng": s["lng"],
                    "to_lat": other["lat"], "to_lng": other["lng"],
                    "label": ln["label"],
                    "type": "incident",
                    "stream": s["stream"] or "GLOBAL",
                })
    order = {"incident": 0, "shared_source": 1, "similar_pattern": 2}
    for s in signals:
        s["related"].sort(key=lambda r: (order[r["type"]], r["title"]))
    return lines


def _fetch_signals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT signal_id, title, content, stream, source, lat, lng,
               gravity_score, published_at, publish_slug, metadata_json
        FROM   signals
        WHERE  published_at IS NOT NULL
        ORDER  BY published_at DESC, signal_id ASC
        LIMIT  200
    """).fetchall()
    # signal_id breaks ties: pins published in one batch share a publish time,
    # and without it their order (ticker, Wire) shuffled between rebuilds.

    items = []
    for r in rows:
        slug = r["publish_slug"] or (_slugify(r["title"]) + "-" + r["signal_id"][:8])
        checked = _checked_fields(r["metadata_json"])
        items.append({
            "kind":          "signal",
            "signal_id":     r["signal_id"],
            "title":         r["title"],
            "summary":       _truncate(r["content"]),
            "stream":        r["stream"] or "GLOBAL",
            "stream_label":  STREAM_LABELS.get(r["stream"] or "GLOBAL", r["stream"]),
            "source":        _source_label(r["source"]),
            "lat":           r["lat"],
            "lng":           r["lng"],
            "gravity_score": r["gravity_score"],
            "gravity_pct":   int((r["gravity_score"] or 0.0) * 100),
            "published_at":  r["published_at"],
            "published_fmt": _fmt_dt(r["published_at"]),
            "slug":          slug,
            "article_slug":  SIGNAL_ARTICLE_MAP.get(r["signal_id"]),
            # Full written story for the event drawer, hand-checked signals only
            # (machine-collected bodies keep the short summary).
            "story":         _story(r["content"]) if checked["checked"] else None,
            **checked,
            # Where the reporting comes from (Sprint 4), checked pins only.
            "provenance":    _provenance_for(r["signal_id"]) if checked["checked"] else None,
            # Province of the pin's batch file ("Western Cape", "North West"),
            # checked pins only. Shown on the drawer and the Wire so the
            # Western Cape pattern-test pins are never confused with others.
            "region":        _region_for(r["signal_id"]) if checked["checked"] else None,
        })
    # Same publish time (a whole batch goes live at once): put the incidents
    # in the order they happened, newest first, not in signal-ID order.
    items.sort(key=lambda x: x["signal_id"])
    items.sort(key=lambda x: x.get("incident_at") or "", reverse=True)
    items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return items


# ── Source provenance (Sprint 4) ─────────────────────────────────────────────
# docs/signals/provenance.json says, for each checked pin, who gave the
# statement, the spokesperson, the outlets and whether the reports are
# independent. It sits beside the pin files so their fingerprints stay as they
# are. Private coding (reporters, motive, victims) lives in private/ and the
# database, and is never read here.
PROVENANCE_FILE = ROOT / "docs" / "signals" / "provenance.json"
INDEPENDENCE_LABELS = {
    "single_report":      "One report only",
    "same_outlet":        "One outlet only",
    "same_statement":     "All from police statements",
    "shared_wording":     "One account, repeated",
    "partly_independent": "Partly independent",
    "independent":        "Independent reports",
}
_PROVENANCE_CACHE: dict | None = None


def _load_provenance() -> dict:
    global _PROVENANCE_CACHE
    if _PROVENANCE_CACHE is None:
        try:
            _PROVENANCE_CACHE = json.loads(PROVENANCE_FILE.read_text(encoding="utf-8")).get("pins") or {}
        except (OSError, ValueError):
            _PROVENANCE_CACHE = {}
    return _PROVENANCE_CACHE


_REGION_CACHE: dict | None = None


def _region_for(signal_id: str) -> str | None:
    """Province of a checked pin, from docs/signals/index.json."""
    global _REGION_CACHE
    if _REGION_CACHE is None:
        try:
            from tools.checked_signals import load_index, provinces
            _REGION_CACHE = provinces(load_index(CHECKED_INDEX))
        except Exception:
            _REGION_CACHE = {}
    return _REGION_CACHE.get(signal_id)


def _provenance_for(signal_id: str) -> dict | None:
    p = _load_provenance().get(signal_id)
    if not isinstance(p, dict) or p.get("independence") not in INDEPENDENCE_LABELS:
        return None
    clean = lambda xs: [str(x).strip() for x in (xs or []) if str(x).strip()]
    return {
        "statement_from":     str(p.get("statement_from") or "").strip() or None,
        "spokesperson":       clean(p.get("spokesperson")),
        "other_voices":       clean(p.get("other_voices")),
        "outlets":            clean(p.get("outlets")),
        "independence":       p["independence"],
        "independence_label": INDEPENDENCE_LABELS[p["independence"]],
        "note":               str(p.get("note") or "").strip() or None,
    }


def _build_timeline_data(
    signals: list[dict], articles: list[dict]
) -> tuple[dict, dict]:
    """Group combined items by year -> month (desc). Returns (grouped, span)."""
    all_items = sorted(
        signals + articles,
        key=lambda x: x["published_at"] or "",
        reverse=True,
    )

    from collections import OrderedDict

    grouped: dict[str, dict[str, list]] = OrderedDict()
    for item in all_items:
        pub = item.get("published_at")
        if not pub:
            continue
        # ── Article gate ──────────────────────────────────────────────────────
        # Every signal on the public timeline MUST have an analyst article.
        # Signals without an article_slug are held back (still visible in case
        # detail pages) until an article is written and wired into
        # SIGNAL_ARTICLE_MAP above.
        if item.get("kind") == "signal" and not item.get("article_slug"):
            print(f"[publish] GATE: no article for signal — add to SIGNAL_ARTICLE_MAP: {item.get('slug', item.get('title', ''))[:60]}")
            continue
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        year  = str(dt.year)
        month = dt.strftime("%B %Y")
        if year not in grouped:
            grouped[year] = OrderedDict()
        if month not in grouped[year]:
            grouped[year][month] = []
        grouped[year][month].append(item)

    span: dict = {}
    shown = [i for months in grouped.values() for its in months.values() for i in its]
    if shown:
        dates = [i["published_at"] for i in shown]
        span = {
            "start": _fmt_dt(min(dates)),
            "end":   _fmt_dt(max(dates)),
            # Count only the entries the Archive actually lists (signals held
            # back by the article gate are not shown, so not counted).
            "total": len(shown),
        }
    return grouped, span


def _build_graph_data(conn: sqlite3.Connection) -> dict:
    """Build Cytoscape-ready nodes/edges from actors linked to published cases.

    Inclusion criteria (OR):
      1. Actor is linked to any published case via case_actors
      2. Only if PUBLISH_UNCASED_PERSONS: a person with confidence >= 0.35
         (NER-extracted principals). Off by default; see the flag.
    Exclusion:
      - Generic noise names (location, company, etc.)
      - type = 'location'
    """
    ph = ",".join("?" * len(PUBLISHED_CASE_IDS))

    rows = conn.execute(f"""
        SELECT DISTINCT a.actor_id, a.name, a.type, a.confidence_score
        FROM   actors a
        WHERE  (
                 a.actor_id IN (
                     SELECT actor_id FROM case_actors
                     WHERE  case_id IN ({ph})
                 )
                 {_uncased_person_sql()}
               )
          AND  a.name NOT IN ('location','government','company','sa',
                               'south africa','gauteng','pretoria',
                               'johannesburg','cape town','kzn',
                               'kwazulu-natal')
          AND  a.type NOT IN ('location')
        ORDER  BY a.confidence_score DESC
        LIMIT  80
    """, PUBLISHED_CASE_IDS).fetchall()

    node_ids = set()
    nodes = []
    for r in rows:
        node_ids.add(r["actor_id"])
        nodes.append({
            "id":         r["actor_id"],
            "name":       r["name"],
            "type":       r["type"] or "unknown",
            "confidence": round(r["confidence_score"] or 0.0, 3),
        })

    if not node_ids:
        return {"nodes": [], "edges": []}

    placeholders = ",".join("?" * len(node_ids))
    edge_rows = conn.execute(f"""
        SELECT subject_actor_id, object_actor_id, relation_type,
               confidence, extraction_method, description
        FROM   entity_relationships
        WHERE  subject_actor_id IN ({placeholders})
          AND  object_actor_id  IN ({placeholders})
          AND  relation_type   != 'stylometric_match'
          AND  (extraction_method = 'manual' OR confidence >= 0.3)
        LIMIT  200
    """, list(node_ids) * 2).fetchall()

    edges = []
    seen = set()
    for r in edge_rows:
        key = (r["subject_actor_id"], r["object_actor_id"], r["relation_type"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "source":      r["subject_actor_id"],
            "target":      r["object_actor_id"],
            "relation":    r["relation_type"] or "ASSOCIATED_WITH",
            "confidence":  round(r["confidence"] or 0.0, 3),
            "method":      r["extraction_method"] or "auto",
            "description": r["description"] or "",
        })

    return {"nodes": nodes, "edges": edges}


def _fetch_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT article_id, title, slug, summary, body_markdown,
               stream, author, published_at, tags,
               COALESCE(tier, 'free') AS tier
        FROM   articles
        WHERE  status = 'published'
        ORDER  BY published_at DESC, article_id ASC
        LIMIT  200
    """).fetchall()

    items = []
    for r in rows:
        tags = []
        if r["tags"]:
            try:
                tags = json.loads(r["tags"])
            except (json.JSONDecodeError, TypeError):
                tags = [t.strip() for t in r["tags"].split(",") if t.strip()]

        items.append({
            "kind":          "article",
            "article_id":    r["article_id"],
            "title":         r["title"],
            "slug":          r["slug"],
            "summary":       r["summary"] or "",
            "body_markdown": r["body_markdown"] or "",
            "stream":        r["stream"] or "GLOBAL",
            "stream_label":  STREAM_LABELS.get(r["stream"] or "GLOBAL", r["stream"]),
            "author":        r["author"] or "ZA-DIVERGENT Staff",
            "published_at":  r["published_at"],
            "published_fmt": _fmt_dt(r["published_at"]),
            "tags":          tags,
            "tier":          r["tier"],
        })
    return items


# ── Cases ────────────────────────────────────────────────────────────────────

def _is_pattern_case(context_anchors: str | None) -> bool:
    """A pattern case groups pins a person thinks look alike. It claims no
    link between them: it draws no lines, shows no evidence-confidence score
    and lists its signals by incident date. Set in cases.context_anchors as
    {"pattern_test": true} (the Sprint 2 experiment case's
    {"links_incidents": false} counts too)."""
    try:
        anchors = json.loads(context_anchors or "null")
    except (TypeError, ValueError):
        return False
    if not isinstance(anchors, dict):
        return False
    return anchors.get("pattern_test") is True or anchors.get("links_incidents") is False


def _fetch_cases(conn: sqlite3.Connection) -> list[dict]:
    ph = ",".join("?" * len(PUBLISHED_CASE_IDS))
    rows = conn.execute(f"""
        SELECT c.case_id, c.name, c.description, c.hypothesis,
               c.status, c.created_at, c.context_anchors,
               COUNT(DISTINCT cs.signal_id) as signal_count,
               COUNT(DISTINCT ca.actor_id)  as actor_count,
               AVG(s.gravity_score)          as coe,
               MAX(s.gravity_score)          as max_gravity,
               MAX(s.timestamp)              as last_signal_at
        FROM   cases c
        LEFT JOIN case_signals cs ON cs.case_id = c.case_id
        LEFT JOIN case_actors  ca ON ca.case_id = c.case_id
        LEFT JOIN signals      s  ON cs.signal_id = s.signal_id
        WHERE  c.case_id IN ({ph})
        GROUP  BY c.case_id
        ORDER  BY coe DESC
    """, PUBLISHED_CASE_IDS).fetchall()

    cases = []
    for r in rows:
        updated = r["last_signal_at"] or r["created_at"]
        cases.append({
            "case_id":     r["case_id"],
            "name":        r["name"],
            "description": r["description"] or "",
            "hypothesis":  r["hypothesis"] or "",
            "status":      r["status"] or "active",
            "coe":         round(r["coe"] or 0.0, 3),
            "max_gravity": round(r["max_gravity"] or 0.0, 3),
            "signal_count": r["signal_count"] or 0,
            "actor_count":  r["actor_count"] or 0,
            "created_fmt":  _fmt_dt(r["created_at"]),
            "updated_fmt":  _fmt_dt(updated),
            "slug":        _slugify(r["name"])[:60],
            "pattern":     _is_pattern_case(r["context_anchors"]),
        })
    return cases


def _fetch_case_signals(conn: sqlite3.Connection, case_id: int,
                        pattern: bool = False) -> list[dict]:
    rows = conn.execute("""
        SELECT s.signal_id, s.title, s.content, s.source, s.gravity_score,
               s.timestamp, s.metadata_json, cs.note
        FROM   case_signals cs
        JOIN   signals s ON cs.signal_id = s.signal_id
        WHERE  cs.case_id = ?
        ORDER  BY s.gravity_score DESC, s.signal_id ASC
    """, (case_id,)).fetchall()

    out = []
    for r in rows:
        chk = _checked_fields(r["metadata_json"])
        out.append({
            "title":         r["title"],
            "content":       _strip_html(r["content"] or ""),
            "source":        _source_label(r["source"]),
            "gravity_score": round(r["gravity_score"] or 0.0, 3),
            "published_fmt": _fmt_dt(r["timestamp"]),
            "incident":      chk["incident"],
            "note":          r["note"] or "",
            # Pattern cases sort by when it happened; the timestamp column is
            # mostly the report time, which is what misled the Sprint 2 engines.
            # Full value, not just the date, so two incidents on the same day
            # keep their time order (06:00 before 06:30).
            "_sort":         str(chk["incident_start"] or r["timestamp"] or ""),
            "_id":           r["signal_id"],
        })
    if pattern:
        out.sort(key=lambda x: (x["_sort"], x["_id"]))
    for x in out:
        x.pop("_sort")
        x.pop("_id")
    return out


def _fetch_case_actors(conn: sqlite3.Connection, case_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT a.actor_id, a.name, a.type, a.confidence_score
        FROM   case_actors ca
        JOIN   actors a ON ca.actor_id = a.actor_id
        WHERE  ca.case_id = ?
        ORDER  BY a.confidence_score DESC
    """, (case_id,)).fetchall()

    return [{
        "actor_id":   r["actor_id"],
        "name":       r["name"],
        "type":       r["type"] or "unknown",
        "confidence": round(r["confidence_score"] or 0.0, 3),
    } for r in rows]


# ── Entity Directory ─────────────────────────────────────────────────────────
# Surfaces every actor that appears on the public graph (graph.html) as a
# static profile card — same eligibility criteria as _build_graph_data, kept
# in sync deliberately. Neutral framing: this is a directory of entities
# referenced in published investigations, not an accusation list.

def _fetch_directory_actors(conn: sqlite3.Connection) -> list[dict]:
    ph = ",".join("?" * len(PUBLISHED_CASE_IDS))

    rows = conn.execute(f"""
        SELECT DISTINCT a.actor_id, a.name, a.type, a.description,
               a.confidence_score, a.image_url
        FROM   actors a
        WHERE  (
                 a.actor_id IN (
                     SELECT actor_id FROM case_actors
                     WHERE  case_id IN ({ph})
                 )
                 {_uncased_person_sql()}
               )
          AND  a.name NOT IN ('location','government','company','sa',
                               'south africa','gauteng','pretoria',
                               'johannesburg','cape town','kzn',
                               'kwazulu-natal')
          AND  a.type NOT IN ('location')
        ORDER  BY a.confidence_score DESC
        LIMIT  80
    """, PUBLISHED_CASE_IDS).fetchall()

    items = []
    for r in rows:
        items.append({
            "actor_id":    r["actor_id"],
            "name":        r["name"],
            "type":        r["type"] or "unknown",
            "description": r["description"],
            "confidence":  round(r["confidence_score"] or 0.0, 3),
            "image_url":   r["image_url"],
            "slug":        _slugify(r["name"]) + "-" + str(r["actor_id"]),
            "initials":    _actor_initials(r["name"]),
        })
    return items


def _fetch_actor_events(conn: sqlite3.Connection, actor_id: int) -> list[dict]:
    """Known events this actor is linked to, most recent first."""
    rows = conn.execute("""
        SELECT e.event_id, e.title, e.summary, e.date, e.location,
               e.category, ae.role
        FROM   actor_events ae
        JOIN   events e ON e.event_id = ae.event_id
        WHERE  ae.actor_id = ?
        ORDER  BY e.date DESC
        LIMIT  20
    """, (actor_id,)).fetchall()

    return [{
        "event_id": r["event_id"],
        "title":    r["title"],
        "summary":  r["summary"],
        "date":     r["date"],
        "location": r["location"],
        "category": r["category"],
        "role":     r["role"],
    } for r in rows]


def _fetch_actor_cases(conn: sqlite3.Connection, actor_id: int) -> list[dict]:
    """Published cases (PUBLISHED_CASE_IDS) this actor is linked to."""
    placeholders = ",".join("?" * len(PUBLISHED_CASE_IDS))
    rows = conn.execute(f"""
        SELECT c.case_id, c.name
        FROM   case_actors ca
        JOIN   cases c ON c.case_id = ca.case_id
        WHERE  ca.actor_id = ? AND c.case_id IN ({placeholders})
    """, (actor_id, *PUBLISHED_CASE_IDS)).fetchall()

    return [{
        "case_id": r["case_id"],
        "name":    r["name"],
        "slug":    _slugify(r["name"])[:60],
    } for r in rows]


def _fetch_actor_activity(conn: sqlite3.Connection, actor_id: int) -> Optional[dict]:
    """
    Canon activity-stats infobox row, derived purely from signal_actors +
    signals — no NER/relationship dependency. Returns None if the actor has
    no linked signals (nothing to report).
    """
    row = conn.execute("""
        SELECT COUNT(*) AS signal_count,
               MIN(s.timestamp) AS first_seen,
               MAX(s.timestamp) AS last_seen,
               MAX(s.gravity_score) AS max_gravity
        FROM   signal_actors sa
        JOIN   signals s ON s.signal_id = sa.signal_id
        WHERE  sa.actor_id = ?
    """, (actor_id,)).fetchone()

    if not row or not row["signal_count"]:
        return None

    stream_row = conn.execute("""
        SELECT s.stream, COUNT(*) AS c
        FROM   signal_actors sa
        JOIN   signals s ON s.signal_id = sa.signal_id
        WHERE  sa.actor_id = ?
        GROUP  BY s.stream
        ORDER  BY c DESC
        LIMIT  1
    """, (actor_id,)).fetchone()

    return {
        "signal_count":   row["signal_count"],
        "first_seen":     (row["first_seen"] or "")[:10],
        "last_seen":      (row["last_seen"] or "")[:10],
        "max_gravity":    round(row["max_gravity"] or 0.0, 3),
        "dominant_stream": stream_row["stream"] if stream_row else None,
    }


def _fetch_actor_sources(conn: sqlite3.Connection, actor_id: int) -> list[dict]:
    """
    Citation list for an actor's profile — linked signals that carry a real
    article URL in metadata_json (populated going forward by
    civic_intel_collector.py's entry_to_signal). Only signals with a stored
    URL appear; older signals collected before this fix have no URL and are
    silently omitted — same "no data = no block" pattern as activity stats.
    """
    rows = conn.execute("""
        SELECT s.title, s.source, s.metadata_json, s.timestamp
        FROM   signal_actors sa
        JOIN   signals s ON s.signal_id = sa.signal_id
        WHERE  sa.actor_id = ? AND s.metadata_json IS NOT NULL
        ORDER  BY s.timestamp DESC
    """, (actor_id,)).fetchall()

    sources = []
    for r in rows:
        try:
            meta = json.loads(r["metadata_json"])
        except (TypeError, ValueError):
            continue
        url = meta.get("url")
        if not url:
            continue
        sources.append({
            "title":     r["title"],
            "source":    r["source"],
            "url":       url,
            "timestamp": (r["timestamp"] or "")[:10],
        })

    return sources[:10]


def _fetch_actor_relationships(conn: sqlite3.Connection, actor_id: int) -> list[dict]:
    """
    Infobox rows derived from entity_relationships (e.g. LEADS,
    AFFILIATED_WITH, member_of) — "Position"/"Affiliation" facts.

    Gated by ENABLE_INFOBOX_RELATIONSHIPS: currently off because NER
    coverage is too sparse (1.4% of signals) and extracted entity text
    doesn't exact-match actors.name, so entity_relationships for most
    actors is empty or unreliable. Flip the flag on once that's fixed —
    no other wiring changes needed.
    """
    if not ENABLE_INFOBOX_RELATIONSHIPS:
        return []

    rows = conn.execute("""
        SELECT er.relation_type, er.description, er.confidence,
               a.actor_id AS other_id, a.name AS other_name, a.type AS other_type,
               CASE WHEN er.subject_actor_id = ? THEN 'subject' ELSE 'object' END AS side
        FROM   entity_relationships er
        JOIN   actors a ON a.actor_id = CASE
                   WHEN er.subject_actor_id = ? THEN er.object_actor_id
                   ELSE er.subject_actor_id
               END
        WHERE  er.subject_actor_id = ? OR er.object_actor_id = ?
        ORDER  BY er.confidence DESC
        LIMIT  10
    """, (actor_id, actor_id, actor_id, actor_id)).fetchall()

    return [{
        "relation_type": r["relation_type"],
        "description":   r["description"],
        "confidence":    round(r["confidence"] or 0.0, 3),
        "other_id":      r["other_id"],
        "other_name":    r["other_name"],
        "other_type":    r["other_type"],
        "side":          r["side"],
    } for r in rows]


def _check_case_triangulation(
    conn: sqlite3.Connection,
    cases: list[dict],
    signals: list[dict],
    graph_data: dict,
) -> None:
    """Warn if a published case is missing timeline/article, map, or graph
    presence. Does not fail the build — surfaces gaps for the analyst to
    close (write article, geotag signal, link actor) on the next pass."""
    graph_node_ids = {n["id"] for n in graph_data["nodes"]}

    for case in cases:
        case_id = case["case_id"]
        name = case["name"]

        sig_rows = conn.execute(
            "SELECT cs.signal_id AS signal_id, s.lat AS lat, s.lng AS lng FROM case_signals cs "
            "JOIN signals s ON s.signal_id = cs.signal_id WHERE cs.case_id = ?",
            (case_id,),
        ).fetchall()
        sig_ids = [r["signal_id"] for r in sig_rows]

        has_article = any(
            SIGNAL_ARTICLE_MAP.get(sid) for sid in sig_ids
        )
        has_geo = any(r["lat"] and r["lng"] for r in sig_rows)

        actor_rows = conn.execute(
            "SELECT actor_id FROM case_actors WHERE case_id = ?", (case_id,)
        ).fetchall()
        actor_ids = [r["actor_id"] for r in actor_rows]
        has_graph_node = any(aid in graph_node_ids for aid in actor_ids)

        if not has_article:
            print(f"[publish] GATE: case #{case_id} '{name}' — no signal with an article in SIGNAL_ARTICLE_MAP (timeline gap)")
        if not has_geo:
            print(f"[publish] GATE: case #{case_id} '{name}' — no geo-tagged signal (map gap)")
        if not has_graph_node:
            print(f"[publish] GATE: case #{case_id} '{name}' — no linked actor appears on graph.html (graph gap)")


# ── Build dist/ ───────────────────────────────────────────────────────────────

def _direct_link_only_signal_ids(conn: sqlite3.Connection) -> set[str]:
    """Signal IDs pinned to a direct-link-only case, so they stay off the map,
    ticker and Wire even if that case is in the database."""
    if not DIRECT_LINK_ONLY_CASES:
        return set()
    try:
        rows = conn.execute("SELECT case_id, name FROM cases").fetchall()
        ids = [r["case_id"] for r in rows
               if _slugify(r["name"] or "")[:60] in DIRECT_LINK_ONLY_CASES]
        if not ids:
            return set()
        ph = ",".join("?" * len(ids))
        sig_rows = conn.execute(
            f"SELECT signal_id FROM case_signals WHERE case_id IN ({ph})", ids
        ).fetchall()
        return {r["signal_id"] for r in sig_rows}
    except sqlite3.Error:
        return set()


def _build_dist(
    env: Environment,
    conn: sqlite3.Connection,
    signals: list[dict],
    articles: list[dict],
    now_str: str,
) -> None:
    # Revenue context — merged into every template render
    rev = _get_revenue_context()

    # Direct-link-only pages: their article pages are still rendered (below),
    # but they and their signals are listed nowhere on the site.
    pages_articles = articles
    hidden_signal_ids = _direct_link_only_signal_ids(conn)
    signals = [
        s for s in signals
        if s.get("article_slug") not in DIRECT_LINK_ONLY_ARTICLES
        and s.get("signal_id") not in hidden_signal_ids
    ]
    articles = [a for a in articles if a["slug"] not in DIRECT_LINK_ONLY_ARTICLES]

    # Link records -> drawer rows + map lines, before any render sees the pins.
    intel_links = _resolve_links(signals)

    DIST.mkdir(exist_ok=True)
    DIST_ART.mkdir(exist_ok=True)
    DIST_CASES.mkdir(exist_ok=True)
    DIST_ENTITIES.mkdir(exist_ok=True)

    if DIST_STATIC.exists():
        shutil.rmtree(DIST_STATIC)
    if STATIC_SRC.exists():
        shutil.copytree(STATIC_SRC, DIST_STATIC)

    items = sorted(
        signals + articles, key=lambda x: x["published_at"] or "", reverse=True
    )

    # index.html — the map shell (World-Monitor-style: one home surface, every
    # other screen absorbed as an overlay panel). Data for all panels is
    # gathered here, then rendered in a single pass from map.html.
    grouped, span = _build_timeline_data(signals, articles)  # Wire panel: Archive mode

    # Cases — used by both the Cases panel and per-case detail pages
    all_cases = _fetch_cases(conn)
    cases = [c for c in all_cases if c["slug"] not in DIRECT_LINK_ONLY_CASES]

    # Entities — used by both the Entities panel and per-actor profile pages
    directory_actors = _fetch_directory_actors(conn)
    entity_types = sorted({a["type"] for a in directory_actors})

    photos_dir = DIST_STATIC / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    for actor in directory_actors:
        src = actor.pop("image_url", None)
        actor["photo"] = None
        if src:
            src_path = MEDIA_DIR / src
            if src_path.exists():
                dest_name = pathlib.Path(src).name
                shutil.copy2(src_path, photos_dir / dest_name)
                actor["photo"] = dest_name

    # Graph — with case→actor mapping for progressive expand/collapse
    graph_data = _build_graph_data(conn)
    case_actor_map = {}
    for cid in PUBLISHED_CASE_IDS:
        rows = conn.execute(
            "SELECT actor_id FROM case_actors WHERE case_id = ?", (cid,)
        ).fetchall()
        case_actor_map[str(cid)] = [r["actor_id"] for r in rows]

    # Map — geo-tagged signals + cross-geography intel links
    geo_signals = [s for s in signals if s["lat"] and s["lng"]]

    # Map lines (Sprint 3): one per approved incident link record between two
    # published pins, and nothing else. The old code joined every pair of
    # ~1-degree squares holding a pin from the same case (its comment said
    # "0.5 degree" and ">50km apart"; neither was true), which drew lines
    # between incidents nobody had linked. Case membership now draws nothing.
    # intel_links was built by _resolve_links() at the top of this function.

    # Compute stream counts for map dashboard filter panel
    stream_counts = {}
    for s in geo_signals:
        st = s.get("stream", "GLOBAL")
        stream_counts[st] = stream_counts.get(st, 0) + 1

    # Single render — index.html is the map shell; every other screen (Wire
    # archive, Streams, Cases, Graph, Entities, Watchlist) is an overlay panel
    # on this one page, populated from the context below.
    (DIST / "index.html").write_text(
        env.get_template("map.html").render(
            markers=geo_signals,
            intel_links=intel_links,
            all_signals=signals,
            cases=cases,
            grouped=grouped,
            span=span,
            graph=graph_data,
            case_actor_map=case_actor_map,
            actors=directory_actors,
            entity_types=entity_types,
            stream_labels=STREAM_LABELS,
            stream_counts=stream_counts,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    print(f"[publish] index.html  - {len(geo_signals)} geo signals, {len(intel_links)} intel links, "
          f"{span.get('total', 0)} wire items, {len(graph_data['nodes'])} graph nodes, {len(directory_actors)} entities")

    # article pages — tier-aware rendering
    tmpl = env.get_template("article.html")
    gate_tmpl = env.get_template("gate.html")
    gated_count = 0
    for art in pages_articles:
        article_tier = art.get("tier", "free")
        art["case_study"] = CASE_STUDY_NOTES.get(art["slug"])
        out = DIST_ART / f"{art['slug']}.html"

        if article_tier == "premium":
            gated_count += 1
            out.write_text(
                gate_tmpl.render(
                    gate_title=art["title"],
                    gate_description=art.get("summary") or "This intelligence brief is available to Pro subscribers.",
                    back_url="../index.html",
                    back_label="Bulletin",
                    generated_at=now_str,
                    **rev,
                ),
                encoding="utf-8",
            )
        elif article_tier == "preview":
            gated_count += 1
            full_html = md_parser.render(art["body_markdown"])
            preview_chars = 300
            preview_text = art["body_markdown"][:preview_chars]
            if len(art["body_markdown"]) > preview_chars:
                last_space = preview_text.rfind(" ")
                if last_space > 200:
                    preview_text = preview_text[:last_space]
                preview_text += "..."
            preview_html = md_parser.render(preview_text)
            out.write_text(
                tmpl.render(
                    article=art, body_html=preview_html,
                    is_gated=True, generated_at=now_str, **rev,
                ),
                encoding="utf-8",
            )
        else:
            body_html = md_parser.render(art["body_markdown"])
            out.write_text(
                tmpl.render(
                    article=art, body_html=body_html,
                    is_gated=False, generated_at=now_str, **rev,
                ),
                encoding="utf-8",
            )
    print(f"[publish] articles/   - {len(pages_articles)} pages ({gated_count} gated)")

    # feed.json
    feed_items = []
    for item in items[:50]:
        entry: dict = {
            "kind":         item["kind"],
            "title":        item["title"],
            "stream":       item["stream"],
            "stream_label": item["stream_label"],
            "published_at": item["published_at"],
        }
        if item["kind"] == "signal":
            entry["summary"] = item["summary"]
            entry["source"]  = item["source"]
            if item.get("region"):
                entry["region"] = item["region"]
            if item["lat"] and item["lng"]:
                entry["geo"] = {"lat": item["lat"], "lng": item["lng"]}
        else:
            entry["summary"] = item["summary"]
            entry["author"]  = item["author"]
            entry["url"]     = f"articles/{item['slug']}.html"
        feed_items.append(entry)

    (DIST / "feed.json").write_text(
        json.dumps(
            {
                "title":        "ZA-DIVERGENT Intelligence Feed",
                "description":  "South African open-source intelligence bulletin",
                "generated_at": now_str,
                "items":        feed_items,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("[publish] feed.json")

    # individual case detail pages (cases already fetched above for index.html)
    case_tmpl = env.get_template("case_detail.html")
    for case in all_cases:
        case_signals = _fetch_case_signals(conn, case["case_id"], case["pattern"])
        case_actors  = _fetch_case_actors(conn, case["case_id"])
        out = DIST_CASES / f"{case['slug']}.html"
        out.write_text(
            case_tmpl.render(
                case=case,
                signals=case_signals,
                actors=case_actors,
                generated_at=now_str,
                **rev,
            ),
            encoding="utf-8",
        )
    print(f"[publish] cases/        - {len(all_cases)} detail pages")

    # individual entity profile pages (directory_actors already fetched above for index.html)
    actor_tmpl = env.get_template("actor_profile.html")
    for actor in directory_actors:
        actor["initials"] = _actor_initials(actor["name"])
        raw_profile = conn.execute(
            "SELECT socint_profile FROM actors WHERE actor_id = ?", (actor["actor_id"],)
        ).fetchone()
        actor["properties"] = json.loads(raw_profile["socint_profile"] or "{}") if raw_profile and raw_profile["socint_profile"] else {}
        actor["cases"] = _fetch_actor_cases(conn, actor["actor_id"])
        actor["events"] = _fetch_actor_events(conn, actor["actor_id"])
        actor["activity"] = _fetch_actor_activity(conn, actor["actor_id"])
        actor["sources"] = _fetch_actor_sources(conn, actor["actor_id"])
        actor["relationships"] = _fetch_actor_relationships(conn, actor["actor_id"])
        out = DIST_ENTITIES / f"{actor['slug']}.html"
        out.write_text(
            actor_tmpl.render(actor=actor, generated_at=now_str, stream_labels=STREAM_LABELS, **rev),
            encoding="utf-8",
        )
    print(f"[publish] entities/    - {len(directory_actors)} entity profile pages")

    # about.html — methodology and "how it works" page
    (DIST / "about.html").write_text(
        env.get_template("about.html").render(generated_at=now_str, **rev),
        encoding="utf-8",
    )

    # subscribe.html — pricing/feature comparison page with Paystack checkout
    (DIST / "subscribe.html").write_text(
        env.get_template("subscribe.html").render(generated_at=now_str, **rev),
        encoding="utf-8",
    )

    # thankyou.html — post-payment landing page
    (DIST / "thankyou.html").write_text(
        env.get_template("thankyou.html").render(generated_at=now_str, **rev),
        encoding="utf-8",
    )

    # search-index.json — flat document list for client-side MiniSearch
    search_docs = []
    for s in signals:
        doc = {
            "id": s["slug"],
            "title": s["title"],
            "kind": "signal",
            **_signal_search_target(s),
        }
        aliases = list(s.get("search_aliases") or []) + ([s["region"]] if s.get("region") else [])
        if aliases:
            doc["aliases"] = " ".join(aliases)
        search_docs.append(doc)
    for a in articles:
        search_docs.append({
            "id": a["slug"],
            "title": a["title"],
            "kind": "article",
            "url": f"articles/{a['slug']}.html",
        })
    for act in directory_actors:
        search_docs.append({
            "id": str(act["actor_id"]),
            "title": act["name"],
            "kind": "actor",
            "url": f"entities/{act['slug']}.html",
        })
    (DIST / "search-index.json").write_text(
        json.dumps(search_docs), encoding="utf-8"
    )
    print(f"[publish] search-index.json - {len(search_docs)} documents")

    # entity-index.json — actor/entity names + slugs for client-side auto-linking
    entity_index = []
    try:
        for ent in entities:
            entity_index.append({
                "name": ent.get("name", ""),
                "slug": ent.get("slug", ""),
                "url": f"entities/{ent.get('slug', '')}.html",
                "type": ent.get("type", ""),
            })
    except Exception:
        pass
    (DIST / "entity-index.json").write_text(
        json.dumps(entity_index), encoding="utf-8"
    )
    print(f"[publish] entity-index.json - {len(entity_index)} entities")

    _check_case_triangulation(conn, cases, signals, graph_data)


# ── Git deploy ────────────────────────────────────────────────────────────────

def _run(args: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, **kwargs)


def _git_deploy(now_str: str) -> None:
    """
    Stages dist/ on the current branch (main), commits, and pushes.
    Vercel watches main and auto-deploys when it sees dist/ change.
    """
    cwd = str(ROOT)
    import os as _os
    if _os.environ.get("FORGE_ALLOW_PUBLISH") != "1":
        print("[deploy] blocked: set FORGE_ALLOW_PUBLISH=1 to allow git push")
        return

    _run(["git", "add", "dist/", "vercel.json"], cwd=cwd)

    has_changes = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=cwd, capture_output=True,
    ).returncode != 0

    if not has_changes:
        print("[deploy] No changes since last publish -- skipping push")
        return

    ts = now_str.replace(" UTC", "")
    _run(["git", "commit", "-m", f"publish: {ts}"], cwd=cwd)
    _run(["git", "push", "origin", "HEAD"], cwd=cwd)
    print("[deploy] Pushed to origin/main")

    if not VERCEL_DEPLOY_HOOK:
        print("[deploy] VERCEL_DEPLOY_HOOK_URL not set — skipping deploy hook. "
              "Set it in .env to trigger automatic Vercel deployments.")
    else:
        try:
            req = urllib.request.Request(VERCEL_DEPLOY_HOOK, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[deploy] Vercel hook triggered -- status {resp.status}")
        except Exception as exc:
            print(f"[deploy] Vercel hook call failed: {exc} (deploy still queued via GitHub)")


# ── Entry point ───────────────────────────────────────────────────────────────

def _build_gate_pages(
    env: Environment,
    conn: sqlite3.Connection,
    signals: list[dict],
    articles: list[dict],
    now_str: str,
) -> None:
    """Replace case detail and entity profile pages with gate pages in free tier."""
    rev = _get_revenue_context()
    gate_tmpl = env.get_template("gate.html")

    # Gate case detail pages (cases/ directory already built with limited data)
    cases = _fetch_cases(conn)
    for case in cases:
        gate_html = gate_tmpl.render(
            gate_title=f"Case: {case['name'][:50]}",
            gate_description="Full case evidence chains, linked actors, and analyst briefs are available to Pro subscribers.",
            back_url="../cases.html",
            back_label="Cases",
            generated_at=now_str,
            **rev,
        )
        out = DIST_CASES / f"{case['slug']}.html"
        out.write_text(gate_html, encoding="utf-8")

    # Gate entity profile pages (entities/ directory already built)
    directory_actors = _fetch_directory_actors(conn)
    for actor in directory_actors:
        gate_html = gate_tmpl.render(
            gate_title=f"Entity: {actor['name']}",
            gate_description="Complete entity dossiers with source signals, known events, and relationship networks are available to Pro subscribers.",
            back_url="../entities.html",
            back_label="Entities",
            generated_at=now_str,
            **rev,
        )
        out = DIST_ENTITIES / f"{actor['slug']}.html"
        out.write_text(gate_html, encoding="utf-8")

    print(f"[publish] Gate pages: {len(cases)} cases + {len(directory_actors)} entities")


def _build_pro_feed(signals: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """Build enriched signal data for pro-feed.json with case/actor linkages."""
    pro_items = []
    for s in signals:
        gs = s.get("gravity_score") or 0
        sig_label = ("Critical" if gs >= 0.75 else ("High" if gs >= 0.55
                     else ("Significant" if gs >= 0.35 else ("Notable" if gs >= 0.20 else "Routine"))))
        pro_items.append({
            "title":            s["title"],
            "stream":           s["stream"],
            "stream_label":     s["stream_label"],
            "source":           s["source"],
            "gravity_score":    s.get("gravity_score"),
            "significance":     sig_label,
            "lat":              s.get("lat"),
            "lng":              s.get("lng"),
            "published_at":     s.get("published_at"),
            "published_fmt":    s.get("published_fmt"),
            "slug":             s.get("slug"),
            "article_slug":     s.get("article_slug"),
        })
    return pro_items


def _build_digest(
    env: Environment,
    signals: list[dict],
    articles: list[dict],
    cases: list[dict],
    now_str: str,
    send: bool = False,
) -> None:
    """Generate intelligence digest HTML and optionally send via configured provider."""
    # For now, all published signals/articles are "new" — in production,
    # filter by last_digest_at timestamp from a state file.
    state_file = ROOT / "revenue" / ".last_digest"
    last_digest = None
    if state_file.exists():
        last_digest = state_file.read_text().strip()

    if last_digest:
        new_signals = [s for s in signals if (s.get("published_at") or "") > last_digest]
        new_articles = [a for a in articles if (a.get("published_at") or "") > last_digest]
    else:
        new_signals = signals[:20]
        new_articles = articles[:5]

    date_range = now_str
    if new_signals:
        dates = [s.get("published_fmt", "") for s in new_signals if s.get("published_fmt")]
        if len(dates) >= 2:
            date_range = f"{dates[-1]} — {dates[0]}"

    html = env.get_template("digest.html").render(
        new_signals=new_signals,
        new_articles=new_articles,
        active_cases=len(cases),
        date_range=date_range,
        site_url="https://forge-os-alpha.vercel.app",
    )

    DIST.mkdir(exist_ok=True)
    (DIST / "digest.html").write_text(html, encoding="utf-8")
    print(f"[digest] Generated digest.html — {len(new_signals)} signals, {len(new_articles)} articles")

    if send:
        try:
            from revenue.digest_provider import get_provider
            provider = get_provider()
            subject = f"ZA-DIVERGENT Intelligence Digest — {now_str}"
            provider.send(html, subject)
        except ImportError:
            print("[digest] revenue.digest_provider not available — skipping send")

    # Update last-digest timestamp
    state_file.parent.mkdir(exist_ok=True)
    state_file.write_text(now_str)


def _writes_pro_feed(tier: str) -> bool:
    """Whether this build writes dist/pro-feed.json.

    pro-feed.json is the paid API feed (revenue/config.py: api_feed is False
    for the free tier; docs/MONETIZATION_PLAN.md Module 5). The default
    'current' build is deployed publicly, so it must not include it.
    """
    return tier == "pro"


# ── Rebuild guard for hand-checked pins (Sprint 1) ─────────────────────────────
# database.db is not in git and Vercel serves the committed dist/, so a build
# from a database that lacks a checked batch would quietly drop those pins.
# Before building: every pin in docs/signals/index.json must be in the
# database exactly as its file says. After building: the map must carry every
# active checked pin. Either failure stops the publish before any deploy.

CHECKED_INDEX = ROOT / "docs" / "signals" / "index.json"


class CheckedGuardError(SystemExit):
    pass


def _checked_guard_before(conn):
    from tools.checked_signals import CheckedSignalError, load_index, verify
    try:
        cs = load_index(CHECKED_INDEX)
    except CheckedSignalError as exc:
        print(f"[publish] REFUSED: checked-signal files are not loadable: {exc}")
        raise CheckedGuardError(2)
    problems = verify(conn, cs)
    if problems:
        print(f"[publish] REFUSED: {len(problems)} hand-checked pin problem(s); nothing was built.")
        for p in problems[:40]:
            print(f"[publish]   {p}")
        print("[publish] Fix: python tools/checked_signals.py import --write  (then publish again)")
        raise CheckedGuardError(2)
    missing = [s["signal_id"][:8] for s in cs.active if _provenance_for(s["signal_id"]) is None]
    if missing:
        print(f"[publish] REFUSED: {len(missing)} checked pin(s) have no source provenance "
              f"in docs/signals/provenance.json: {', '.join(missing)}")
        raise CheckedGuardError(2)
    print(f"[publish] checked pins OK: {len(cs.active)} active, {len(cs.retracted)} retracted, "
          f"all with source provenance")
    return cs


def _checked_guard_after(cs):
    from tools.checked_signals import count_markers_problems
    problems = count_markers_problems(DIST / "index.html", cs)
    if problems:
        print(f"[publish] REFUSED after build: the map is missing hand-checked pins; not deploying.")
        for p in problems[:40]:
            print(f"[publish]   {p}")
        print("[publish] dist/ is now incomplete. Restore it with: git checkout -- dist/")
        raise CheckedGuardError(3)
    print(f"[publish] map carries all {len(cs.active)} checked pins")


def main() -> None:
    parser = argparse.ArgumentParser(description="ZA-DIVERGENT static site publisher")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="After building, commit dist/ to the publication branch and push to GitHub",
    )
    parser.add_argument(
        "--tier",
        choices=["free", "pro", "current"],
        default="current",
        help="Build tier: 'free' (gated), 'pro' (full), 'current' (default single build as today)",
    )
    parser.add_argument(
        "--digest",
        action="store_true",
        help="Generate intelligence digest email (dist/digest.html)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the digest via configured provider (requires --digest)",
    )
    args = parser.parse_args()

    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    env = Environment(loader=FileSystemLoader(str(TMPL_DIR)), autoescape=True)

    print(f"[publish] Connecting to {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        checked = _checked_guard_before(conn)
        signals  = _fetch_signals(conn)
        articles = _fetch_articles(conn)

        # ── Tier-aware build ─────────────────────────────────────────────
        tier = args.tier
        try:
            from revenue.config import TIERS
        except ImportError:
            TIERS = None

        if tier == "free" and TIERS:
            tier_cfg = TIERS["free"]
            limited_signals = signals[:tier_cfg["signals_limit"]] if tier_cfg["signals_limit"] else signals
            limited_articles = articles[:tier_cfg["articles_limit"]] if tier_cfg["articles_limit"] else articles
            print(f"[publish] FREE tier: {len(limited_signals)} signals / {len(limited_articles)} articles")
            _build_dist(env, conn, limited_signals, limited_articles, now_str)
            # Generate gate pages for gated content
            _build_gate_pages(env, conn, signals, articles, now_str)
        else:
            print(f"[publish] {len(signals)} published signals / {len(articles)} published articles")
            _build_dist(env, conn, signals, articles, now_str)
            # Pro API feed — only for an explicit pro build (see _writes_pro_feed)
            pro_feed_path = DIST / "pro-feed.json"
            if _writes_pro_feed(tier):
                pro_feed = _build_pro_feed(signals, conn)
                pro_feed_path.write_text(
                    json.dumps(pro_feed, indent=2), encoding="utf-8"
                )
                print(f"[publish] pro-feed.json — {len(pro_feed)} signals")
            elif pro_feed_path.exists():
                # Don't leave a stale paid feed behind in a public build.
                pro_feed_path.unlink()
                print("[publish] removed stale pro-feed.json (not a pro build)")

        # ── Digest ───────────────────────────────────────────────────────
        if args.digest:
            cases = _fetch_cases(conn)
            _build_digest(env, signals, articles, cases, now_str, send=args.send)

    finally:
        conn.close()

    _checked_guard_after(checked)
    print(f"\n[publish] dist/ ready -> {DIST}")

    if args.deploy:
        print()
        _git_deploy(now_str)
        print("\n[deploy] Done. Vercel will auto-deploy from main -- check forge-os-alpha.vercel.app")
    else:
        print("[publish] Run with --deploy to commit dist/ and push to GitHub.")


if __name__ == "__main__":
    main()
