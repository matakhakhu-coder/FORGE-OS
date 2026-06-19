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
import pathlib
import re
import shutil
import sqlite3
import subprocess
import urllib.request
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
DB_PATH     = ROOT / "database.db"
MEDIA_DIR   = ROOT / "media"
PUBLISHER   = ROOT / "publisher"
TMPL_DIR    = PUBLISHER / "templates"
STATIC_SRC  = PUBLISHER / "static"
DIST        = ROOT / "dist"
DIST_STATIC = DIST / "static"
DIST_ART    = DIST / "articles"
DIST_CASES  = DIST / "cases"
DIST_ENTITIES = DIST / "entities"

# Vercel deploy hook — triggered after every git push to force immediate deployment
VERCEL_DEPLOY_HOOK = "https://api.vercel.com/v1/integrations/deploy/prj_ibD3AwjGwVKVA5tA43Evg93d5Xzf/QTHKub0Cbl"

# SA cases to publish — cases 1-6 are global/seed/auto-generated noise
PUBLISHED_CASE_IDS = (7, 8, 9, 10, 11, 12, 13, 15)  # 11 = Regional Pathogen Surveillance (Project Aegis); 12 = Operation Matlala; 13 = Beitbridge Explosives (Maroto); 15 = MEGA Account Compromise (Cyber)

# Entity infobox: relationship-derived rows (Position/Affiliation from
# entity_relationships, co-occurrence from graph_edges). Off by default —
# NER coverage is currently 1.4% of signals (83/5835) and extracted entity
# text doesn't exact-match actors.name, so this data is too sparse/unreliable
# to publish. Flip on once NER coverage + name normalization are fixed.
ENABLE_INFOBOX_RELATIONSHIPS = False

# Maps signal_id → article slug for "Read analysis →" on timeline signal cards
SIGNAL_ARTICLE_MAP: dict[str, str] = {
    # Beitbridge / Edgar Maroto (Case #13)
    "f94b0c85-9fc5-49c2-8dfe-72090074f5bd": "beitbridge-explosives-smuggling-maroto",
    # Graft roundup: Joshco CEO bail + SIU Home Affairs (13 Jun 2026)
    "423f421e-9182-469b-b684-3d5e61a68d38": "graft-roundup-joshco-home-affairs-june-2026",
    "0450004e-732e-4e96-9044-bd5210ae9a33": "graft-roundup-joshco-home-affairs-june-2026",
    # ── SA Crime & Security ────────────────────────────────────────────────────
    # Limpopo / Mkhwanazi
    "c4d50e84-59ff-4a74-b608-1709feb2402b": "madlanga-hawks-limpopo-municipal-fraud",
    # Emfuleni / Martha Rantsofu
    "208633b9-e522-4c6c-a7a7-36f3bf342106": "emfuleni-martha-rantsofu-murder-investigation",
    # Fadiel Adams / Magaqa
    "5af98c1e-a6b3-49e1-ba20-cac68e86bcce": "operation-magaqa-anatomy-of-a-cover-up",
    # KZN HAWKS R200m drugs
    "b57d464f-cf86-4f6b-aca1-cf8d56671457": "kzn-hawks-three-signals-one-pattern",
    # KZN HAWKS cocaine theft
    "14f285b6-efc2-49a9-9ede-c80baa8d98a7": "kzn-hawks-three-signals-one-pattern",
    # Police minister / crime syndicates
    "0a714878-477c-4238-8164-667dfeb267b7": "saps-police-minister-crime-syndicate-allegation",
    # SAPS murder investigation sabotage
    "403e89f7-43ba-4193-97a4-fc2726cb61af": "saps-murder-investigation-sabotage-pattern",
    # Eskom diesel
    "82b8d529-892b-4d6e-9d42-82a6583f7620": "eskom-r21bn-diesel-fraud-mavuso",
    # ── Project Aegis: Regional Pathogen Surveillance (case 11) ───────────────
    "34a1be1a-1e5e-41c3-a61a-c3b9023324fa": "project-aegis-sadc-health-security",
    "811016d3-9abd-457e-8f50-2bd3a0aeed65": "project-aegis-sadc-health-security",
    "83fb987d-2ede-4f28-bfb8-2d254d4bcfd2": "project-aegis-sadc-health-security",
    "8db51668-0502-42de-8bff-dbdb4464169a": "project-aegis-sadc-health-security",
    "f511c9c6-03c5-4793-8e90-71dafcea75fa": "project-aegis-sadc-health-security",
    "d8e5a1db-87bf-4ec8-bed0-ba0afe56732c": "project-aegis-sadc-health-security",
    "85500d07-5284-48fe-93e9-c83015fd75ad": "project-aegis-sadc-health-security",
    "9dee7b1c-6611-4128-bc98-8a03e579afc8": "project-aegis-sadc-health-security",
    "aad3e8e2-87cb-4b63-a576-d3611fa8d0b6": "project-aegis-sadc-health-security",
    "2a3a8fee-72a9-4751-8d2a-f9aaa3d25f58": "project-aegis-sadc-health-security",
    # ── Operation Matlala (case 12) ───────────────────────────────────────────
    "abfd0ffb-e1d3-4e99-be3d-420676ebb923": "operation-matlala-saps-tender-capture",
    # ── MEGA Account Compromise (case 15) ────────────────────────────────────
    "5544a9d6-bfed-4266-807f-1b2466e4b626": "mega-account-compromise-3xktech-credential-stuffing",
    "6ab2784d-7e18-4200-899b-91a10cc6c4d6": "mega-account-compromise-3xktech-credential-stuffing",
    "94611d55-f713-4bb1-8e44-b7cf00ea262c": "mega-account-compromise-3xktech-credential-stuffing",
    "7b47d7c4-7a97-4867-b3d4-b083077fd17d": "mega-account-compromise-3xktech-credential-stuffing",
    "cd6b4568-0d3d-4282-8c5d-0a75f143c1bc": "mega-account-compromise-3xktech-credential-stuffing",
    "7e04f983-b2aa-4c15-a248-32d7470f09d8": "mega-account-compromise-3xktech-credential-stuffing",
    "1d646ff0-8edb-44a7-a7e0-8bc1e793f8d6": "mega-account-compromise-3xktech-credential-stuffing",
    "c551e334-f0bf-4379-afbb-53ae14ee0b81": "mega-account-compromise-3xktech-credential-stuffing",
    "bf73dc5d-7a33-4001-8d40-d9f60a0c78cd": "mega-account-compromise-3xktech-credential-stuffing",
}


# ── Stream labels (public-facing) ─────────────────────────────────────────────
STREAM_LABELS = {
    "CRIME_INTEL":    "Crime & Security",
    "INFRASTRUCTURE": "Infrastructure",
    "PRIORITY":       "Priority Intelligence",
    "GLOBAL":         "Africa & World",
}

md_parser = MarkdownIt()


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


# ── DB queries ────────────────────────────────────────────────────────────────

def _fetch_signals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT signal_id, title, content, stream, source, lat, lng,
               gravity_score, published_at, publish_slug
        FROM   signals
        WHERE  published_at IS NOT NULL
        ORDER  BY published_at DESC
        LIMIT  200
    """).fetchall()

    items = []
    for r in rows:
        slug = r["publish_slug"] or (_slugify(r["title"]) + "-" + r["signal_id"][:8])
        items.append({
            "kind":          "signal",
            "title":         r["title"],
            "summary":       _truncate(r["content"]),
            "stream":        r["stream"] or "GLOBAL",
            "stream_label":  STREAM_LABELS.get(r["stream"] or "GLOBAL", r["stream"]),
            "source":        (r["source"] or "").upper(),
            "lat":           r["lat"],
            "lng":           r["lng"],
            "gravity_score": r["gravity_score"],
            "gravity_pct":   int((r["gravity_score"] or 0.0) * 100),
            "published_at":  r["published_at"],
            "published_fmt": _fmt_dt(r["published_at"]),
            "slug":          slug,
            "article_slug":  SIGNAL_ARTICLE_MAP.get(r["signal_id"]),
        })
    return items


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
    if all_items:
        dates = [i["published_at"] for i in all_items if i.get("published_at")]
        if dates:
            span = {
                "start": _fmt_dt(min(dates)),
                "end":   _fmt_dt(max(dates)),
                "total": len(all_items),
            }
    return grouped, span


def _build_graph_data(conn: sqlite3.Connection) -> dict:
    """Build Cytoscape-ready nodes/edges from actors linked to published cases.

    Inclusion criteria (OR):
      1. Actor is linked to any published case via case_actors
      2. Actor is a person with confidence >= 0.35 (NER-extracted principals)
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
                 OR (a.type = 'person' AND a.confidence_score >= 0.35)
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
               stream, author, published_at, tags
        FROM   articles
        WHERE  status = 'published'
        ORDER  BY published_at DESC
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
        })
    return items


# ── Cases ────────────────────────────────────────────────────────────────────

def _fetch_cases(conn: sqlite3.Connection) -> list[dict]:
    ph = ",".join("?" * len(PUBLISHED_CASE_IDS))
    rows = conn.execute(f"""
        SELECT c.case_id, c.name, c.description, c.hypothesis,
               c.status, c.created_at,
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
        })
    return cases


def _fetch_case_signals(conn: sqlite3.Connection, case_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT s.title, s.content, s.source, s.gravity_score,
               s.timestamp, cs.note
        FROM   case_signals cs
        JOIN   signals s ON cs.signal_id = s.signal_id
        WHERE  cs.case_id = ?
        ORDER  BY s.gravity_score DESC
    """, (case_id,)).fetchall()

    return [{
        "title":         r["title"],
        "content":       _strip_html(r["content"] or ""),
        "source":        (r["source"] or "").upper(),
        "gravity_score": round(r["gravity_score"] or 0.0, 3),
        "published_fmt": _fmt_dt(r["timestamp"]),
        "note":          r["note"] or "",
    } for r in rows]


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
                 OR (a.type = 'person' AND a.confidence_score >= 0.35)
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

def _build_dist(
    env: Environment,
    conn: sqlite3.Connection,
    signals: list[dict],
    articles: list[dict],
    now_str: str,
) -> None:
    # Revenue context — merged into every template render
    rev = _get_revenue_context()

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

    # index.html — rendered from timeline template (feed removed, timeline is home)
    grouped, span = _build_timeline_data(signals, articles)
    (DIST / "index.html").write_text(
        env.get_template("timeline.html").render(
            grouped=grouped,
            span=span,
            stream_labels=STREAM_LABELS,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    print(f"[publish] index.html  - {span.get('total', 0)} timeline items")

    # Fetch cases early — used by both map dashboard and cases.html
    cases = _fetch_cases(conn)

    # map.html
    geo_signals = [s for s in signals if s["lat"] and s["lng"]]

    # Build cross-geography intel links: connect geo-tagged signals within
    # the same case that are in different locations (>50km apart).
    intel_links = []
    ph = ",".join("?" * len(PUBLISHED_CASE_IDS))
    case_geo_rows = conn.execute(f"""
        SELECT c.case_id, c.name, s.lat, s.lng, s.stream
        FROM   case_signals cs
        JOIN   signals s ON s.signal_id = cs.signal_id
        JOIN   cases c   ON c.case_id = cs.case_id
        WHERE  cs.case_id IN ({ph})
          AND  s.lat IS NOT NULL AND s.lng IS NOT NULL
          AND  s.published_at IS NOT NULL
        ORDER  BY cs.case_id, s.timestamp
    """, PUBLISHED_CASE_IDS).fetchall()

    from itertools import combinations
    case_points: dict[int, list] = {}
    for r in case_geo_rows:
        case_points.setdefault(r["case_id"], []).append(r)

    for cid, points in case_points.items():
        # Deduplicate by rough location (round to 0.5 degree)
        seen_locs: set[tuple] = set()
        unique_pts = []
        for p in points:
            loc_key = (round(p["lat"], 0), round(p["lng"], 0))
            if loc_key not in seen_locs:
                seen_locs.add(loc_key)
                unique_pts.append(p)
        if len(unique_pts) >= 2:
            for a, b in combinations(unique_pts, 2):
                intel_links.append({
                    "from_lat": a["lat"], "from_lng": a["lng"],
                    "to_lat": b["lat"], "to_lng": b["lng"],
                    "case_name": a["name"],
                    "stream": a["stream"] or "GLOBAL",
                })

    # Compute stream counts for map dashboard filter panel
    stream_counts = {}
    for s in geo_signals:
        st = s.get("stream", "GLOBAL")
        stream_counts[st] = stream_counts.get(st, 0) + 1

    (DIST / "map.html").write_text(
        env.get_template("map.html").render(
            markers=geo_signals,
            intel_links=intel_links,
            all_signals=signals,
            cases=cases,
            stream_labels=STREAM_LABELS,
            stream_counts=stream_counts,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    print(f"[publish] map.html    - {len(geo_signals)} geo-tagged signals, {len(intel_links)} intel links")

    # article pages
    tmpl = env.get_template("article.html")
    for art in articles:
        body_html = md_parser.render(art["body_markdown"])
        out = DIST_ART / f"{art['slug']}.html"
        out.write_text(
            tmpl.render(article=art, body_html=body_html, generated_at=now_str, **rev),
            encoding="utf-8",
        )
    print(f"[publish] articles/   - {len(articles)} pages")

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

    # cases.html + individual case pages (cases already fetched above for map dashboard)
    total_signals = sum(c["signal_count"] for c in cases)
    total_actors  = sum(c["actor_count"]  for c in cases)

    (DIST / "cases.html").write_text(
        env.get_template("cases.html").render(
            cases=cases,
            total_signals=total_signals,
            total_actors=total_actors,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    print(f"[publish] cases.html    - {len(cases)} cases")

    case_tmpl = env.get_template("case_detail.html")
    for case in cases:
        case_signals = _fetch_case_signals(conn, case["case_id"])
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
    print(f"[publish] cases/        - {len(cases)} detail pages")

    # entities.html + individual entity profile pages
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
    (DIST / "entities.html").write_text(
        env.get_template("entities.html").render(
            actors=directory_actors,
            entity_types=entity_types,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    actor_tmpl = env.get_template("actor_profile.html")
    for actor in directory_actors:
        actor["initials"] = _actor_initials(actor["name"])
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

    # graph.html
    graph_data = _build_graph_data(conn)
    (DIST / "graph.html").write_text(
        env.get_template("graph.html").render(
            graph=graph_data,
            stream_labels=STREAM_LABELS,
            generated_at=now_str,
            **rev,
        ),
        encoding="utf-8",
    )
    print(f"[publish] graph.html    - {len(graph_data['nodes'])} nodes / {len(graph_data['edges'])} edges")

    # watchlist.html — static shell, content from localStorage at runtime
    (DIST / "watchlist.html").write_text(
        env.get_template("watchlist.html").render(generated_at=now_str, **rev),
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
        search_docs.append({
            "id": s["slug"],
            "title": s["title"],
            "kind": "signal",
            "url": "index.html",
        })
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
            # Pro API feed
            if tier == "pro" or tier == "current":
                pro_feed = _build_pro_feed(signals, conn)
                (DIST / "pro-feed.json").write_text(
                    json.dumps(pro_feed, indent=2), encoding="utf-8"
                )
                print(f"[publish] pro-feed.json — {len(pro_feed)} signals")

        # ── Digest ───────────────────────────────────────────────────────
        if args.digest:
            cases = _fetch_cases(conn)
            _build_digest(env, signals, articles, cases, now_str, send=args.send)

    finally:
        conn.close()

    print(f"\n[publish] dist/ ready -> {DIST}")

    if args.deploy:
        print()
        _git_deploy(now_str)
        print("\n[deploy] Done. Vercel will auto-deploy from main -- check forge-os-alpha.vercel.app")
    else:
        print("[publish] Run with --deploy to commit dist/ and push to GitHub.")


if __name__ == "__main__":
    main()
