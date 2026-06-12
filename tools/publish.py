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

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = pathlib.Path(__file__).parent.parent
DB_PATH     = ROOT / "database.db"
PUBLISHER   = ROOT / "publisher"
TMPL_DIR    = PUBLISHER / "templates"
STATIC_SRC  = PUBLISHER / "static"
DIST        = ROOT / "dist"
DIST_STATIC = DIST / "static"
DIST_ART    = DIST / "articles"
DIST_CASES  = DIST / "cases"

# Vercel deploy hook — triggered after every git push to force immediate deployment
VERCEL_DEPLOY_HOOK = "https://api.vercel.com/v1/integrations/deploy/prj_ibD3AwjGwVKVA5tA43Evg93d5Xzf/QTHKub0Cbl"

# SA cases to publish — cases 1-6 are global/seed/auto-generated noise
PUBLISHED_CASE_IDS = (7, 8, 9, 10, 11, 12)  # 11 = Regional Pathogen Surveillance (Project Aegis); 12 = Operation Matlala

# Maps signal_id → article slug for "Read analysis →" on timeline signal cards
SIGNAL_ARTICLE_MAP: dict[str, str] = {
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


# ── Build dist/ ───────────────────────────────────────────────────────────────

def _build_dist(
    env: Environment,
    conn: sqlite3.Connection,
    signals: list[dict],
    articles: list[dict],
    now_str: str,
) -> None:
    DIST.mkdir(exist_ok=True)
    DIST_ART.mkdir(exist_ok=True)
    DIST_CASES.mkdir(exist_ok=True)

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
        ),
        encoding="utf-8",
    )
    print(f"[publish] index.html  - {span.get('total', 0)} timeline items")

    # map.html
    geo_signals = [s for s in signals if s["lat"] and s["lng"]]
    (DIST / "map.html").write_text(
        env.get_template("map.html").render(
            markers=geo_signals,
            stream_labels=STREAM_LABELS,
            generated_at=now_str,
        ),
        encoding="utf-8",
    )
    print(f"[publish] map.html    - {len(geo_signals)} geo-tagged signals")

    # article pages
    tmpl = env.get_template("article.html")
    for art in articles:
        body_html = md_parser.render(art["body_markdown"])
        out = DIST_ART / f"{art['slug']}.html"
        out.write_text(
            tmpl.render(article=art, body_html=body_html, generated_at=now_str),
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

    # cases.html + individual case pages
    cases = _fetch_cases(conn)
    total_signals = sum(c["signal_count"] for c in cases)
    total_actors  = sum(c["actor_count"]  for c in cases)

    (DIST / "cases.html").write_text(
        env.get_template("cases.html").render(
            cases=cases,
            total_signals=total_signals,
            total_actors=total_actors,
            generated_at=now_str,
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
            ),
            encoding="utf-8",
        )
    print(f"[publish] cases/        - {len(cases)} detail pages")

    # graph.html
    graph_data = _build_graph_data(conn)
    (DIST / "graph.html").write_text(
        env.get_template("graph.html").render(
            graph=graph_data,
            stream_labels=STREAM_LABELS,
            generated_at=now_str,
        ),
        encoding="utf-8",
    )
    print(f"[publish] graph.html    - {len(graph_data['nodes'])} nodes / {len(graph_data['edges'])} edges")


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

def main() -> None:
    parser = argparse.ArgumentParser(description="ZA-DIVERGENT static site publisher")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="After building, commit dist/ to the publication branch and push to GitHub",
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
        print(f"[publish] {len(signals)} published signals / {len(articles)} published articles")
        _build_dist(env, conn, signals, articles, now_str)
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
