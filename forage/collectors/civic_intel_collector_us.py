from __future__ import annotations
"""
FORGE — Civic Intelligence Collector (US Region)  (Phase 74)
=================================================================
Regional sibling to forage/collectors/civic_intel_collector.py, scoped to
United States investigative/civic sources. Same fetch strategies, dedup
logic, and signal shape — different SOURCES registry and default geocode
(Washington, DC).

Fetch strategies per source type:
  RSS_DIRECT   — feedparser via requests raw fetch
  GOOGLE_NEWS  — Google News RSS proxy: news.google.com/rss/search?q=site:X

Stream classification:
  CRIME_INTEL     — investigative journalism, DOJ, corruption, procurement fraud
  INFRASTRUCTURE  — federal/state infrastructure failure, utilities

All civic intel signals start at relevance_score=1.5.

Dependencies:
  pip install feedparser requests beautifulsoup4 --break-system-packages

Author: FORGE Phase 74
"""

__manifest__ = {
    "id":          "civic_intel_collector_us",
    "name":        "Civic Intelligence Collector (US)",
    "description": "Hybrid OSINT collector for United States investigative sources. Pulls from ProPublica, ICIJ, The Intercept, Reveal/CIR, and OCCRP via RSS and Google News.",
    "icon":        "🏛",
    "entry":       "forage/collectors/civic_intel_collector_us.py",
    "args":        [],
    "job_key":     "civic_intel_collector_us",
    "version":     "1.0.0",
}

import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = BASE_DIR / "database.db"

# ── Phase 32: path-safe pipeline logger ───────────────────────────────────
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu
    _lp = Path(__file__).resolve().parent.parent.parent / "forage" / "utils" / "pipeline_logger.py"
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_lp))
        _mod  = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass
log_run = _log_run_safe

# ── Refinery (Stable 1.1) ─────────────────────────────────────────────────
try:
    from core.pipeline.ingest import sanitize_text as _sanitize
except ImportError:
    def _sanitize(t): return t  # noqa: E731

# ── Optional dependencies ──────────────────────────────────────────────────
try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    print("[civic_intel_us] WARN: feedparser not installed — pip install feedparser --break-system-packages")

try:
    import requests as _requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[civic_intel_us] WARN: requests not installed — pip install requests --break-system-packages")

# ── Shared requests session ────────────────────────────────────────────────
_SESSION = None

def _get_session():
    global _SESSION
    if _SESSION is None and HAS_REQUESTS:
        _SESSION = _requests.Session()
        _SESSION.verify  = False
        _SESSION.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; FORGE-OSINT/1.0; "
                "+https://github.com/matakhakhu-coder/FORGE)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
    return _SESSION

# ── Source registry ────────────────────────────────────────────────────────
# Default geocode: Washington, DC (38.9072, -77.0369) — regional anchor for
# US federal-government-focused investigative content.

SOURCES = [
    {
        "source_key":          "propublica",
        "label":               "ProPublica",
        "fetch_mode":          "rss_direct",
        "url":                 "https://www.propublica.org/feeds/propublica/main",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
    {
        "source_key":          "icij",
        "label":               "ICIJ (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:icij.org&hl=en-US&gl=US&ceid=US:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.5,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
    {
        "source_key":          "theintercept",
        "label":               "The Intercept",
        "fetch_mode":          "rss_direct",
        "url":                 "https://theintercept.com/feed/?rss",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.4,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
    {
        "source_key":          "revealnews",
        "label":               "Reveal / Center for Investigative Reporting",
        "fetch_mode":          "rss_direct",
        "url":                 "https://revealnews.org/feed/",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.4,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
    {
        "source_key":          "occrp_us",
        "label":               "OCCRP — United States (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=site:occrp.org+%22United+States%22&hl=en-US&gl=US&ceid=US:en",
        "stream":              "CRIME_INTEL",
        "base_relevance":      1.4,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
    {
        "source_key":          "us_infrastructure",
        "label":               "US Infrastructure Failure (Google News)",
        "fetch_mode":          "google_news",
        "url":                 "https://news.google.com/rss/search?q=%22infrastructure+failure%22+OR+%22water+main%22+OR+%22bridge+collapse%22+OR+%22grid+failure%22+United+States&hl=en-US&gl=US&ceid=US:en",
        "stream":              "INFRASTRUCTURE",
        "base_relevance":      1.3,
        "default_lat":         38.9072,
        "default_lng":         -77.0369,
        "is_priority_default": 0,
    },
]

# ── Keyword classification rules ───────────────────────────────────────────
KEYWORD_RULES = [
    (r"\b(corrupt(ion|ed)?|brib(ery|e)|kickback|procurement\s+fraud)\b",  "CRIME_INTEL", True,  0.0),
    (r"\b(DOJ|FBI|grand\s+jury|indict(ed|ment)|federal\s+prosecutor)\b",  "CRIME_INTEL", True,  0.0),
    (r"\b(money\s+laundering|racketeering|RICO|offshore)\b",              "CRIME_INTEL", True,  0.0),
    (r"\b(arrest(ed)?|charge[sd]?|convict|plea\s+deal)\b",                "CRIME_INTEL", False, 0.1),
    (r"\b(power\s+grid|blackout|grid\s+failure|outage)\b",                "INFRASTRUCTURE", False, 0.1),
    (r"\b(water\s+main|pipe[\s.]burst|sewage|lead\s+pipe)\b",             "INFRASTRUCTURE", False, 0.1),
    (r"\b(bridge\s+collaps|road\s+clos|infrastructure\s+fail)\b",         "INFRASTRUCTURE", False, 0.0),
]

_KEYWORD_PATTERNS = [
    (re.compile(pat, re.IGNORECASE), stream, prio, boost)
    for pat, stream, prio, boost in KEYWORD_RULES
]


def classify_signal(title: str, content: str, base_stream: str,
                    base_relevance: float, base_priority: int) -> tuple:
    text      = f"{title} {content}"
    stream    = base_stream
    relevance = base_relevance
    priority  = base_priority
    for pattern, stream_override, prio_boost, rel_boost in _KEYWORD_PATTERNS:
        if pattern.search(text):
            stream    = stream_override
            relevance = min(relevance + rel_boost, 1.5)
            if prio_boost:
                priority = 1
    return stream, round(relevance, 3), priority


# ── Fetch helpers ──────────────────────────────────────────────────────────

def _raw_get(url: str, timeout: int = 20) -> Optional[bytes]:
    sess = _get_session()
    if not sess:
        return None
    try:
        resp = sess.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  [warn] HTTP fetch failed: {url} — {exc}")
        return None


def fetch_rss_direct(source: dict) -> list:
    if not HAS_FEEDPARSER:
        return []
    raw = _raw_get(source["url"])
    if raw is None:
        return []
    try:
        feed = feedparser.parse(raw)
        if feed.entries:
            return feed.entries
        if feed.bozo:
            print(f"  [warn] feed bozo: {feed.bozo_exception}")
        return []
    except Exception as exc:
        print(f"  [error] feedparser: {exc}")
        return []


def fetch_google_news(source: dict) -> list:
    if not HAS_FEEDPARSER:
        return []
    raw = _raw_get(source["url"])
    if raw is None:
        return []
    try:
        feed = feedparser.parse(raw)
        return feed.entries or []
    except Exception as exc:
        print(f"  [error] google_news feedparser: {exc}")
        return []


def fetch_source(source: dict) -> list:
    mode = source.get("fetch_mode", "rss_direct")
    if mode == "rss_direct":
        return fetch_rss_direct(source)
    elif mode == "google_news":
        return fetch_google_news(source)
    else:
        print(f"  [warn] unknown fetch_mode: {mode}")
        return []


# ── Signal builder ─────────────────────────────────────────────────────────

def entry_to_signal(entry: dict, source: dict) -> Optional[dict]:
    title = (entry.get("title") or "").strip()
    if not title:
        return None

    content = (entry.get("summary") or "").strip()
    if not content and entry.get("content"):
        try:
            content = entry["content"][0].get("value", "").strip()
        except (IndexError, AttributeError):
            pass
    content = re.sub(r"<[^>]+>", " ", content).strip()
    content = re.sub(r"\s+", " ", content)[:2000]

    published = None
    if entry.get("published_parsed"):
        try:
            published = datetime(
                *entry["published_parsed"][:6], tzinfo=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if not published:
        published = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    link     = entry.get("link") or entry.get("id") or title
    ext_id   = "{}:{}".format(
        source["source_key"],
        hashlib.sha1(link.encode("utf-8", errors="replace")).hexdigest()[:16]
    )

    stream, relevance, priority = classify_signal(
        title, content,
        source["stream"],
        source["base_relevance"],
        source["is_priority_default"],
    )

    return {
        "signal_id":       str(uuid.uuid4()),
        "source":          source["source_key"],
        "external_id":     ext_id,
        "title":           _sanitize(title)[:400],
        "content":         _sanitize(content),
        "lat":             source.get("default_lat"),
        "lng":             source.get("default_lng"),
        "timestamp":       published,
        "status":          "raw",
        "stream":          stream,
        "relevance_score": relevance,
        "is_priority":     priority,
    }


# ── Title-based deduplication ──────────────────────────────────────────────

_STOPWORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","was","are","were","be","been","has","have","had",
    "will","would","could","should","this","that","it","its","as","up",
    "out","about","united","states","new","also","after","says",
    "said","news","media","releases","newsroom","featured","inside","untitled",
}


def _normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    tokens = [t for t in title.split() if t not in _STOPWORDS and len(t) > 1]
    return " ".join(tokens)


def _title_similarity(norm_a: str, norm_b: str) -> float:
    if not norm_a or not norm_b:
        return 0.0
    set_a = set(norm_a.split())
    set_b = set(norm_b.split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union        = len(set_a | set_b)
    return intersection / union if union else 0.0


def _ensure_dedup_index(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_title_time "
            "ON signals(title, timestamp)"
        )
        conn.commit()
    except Exception:
        pass


def _load_title_cache(conn: sqlite3.Connection,
                      window_hours: int = 24) -> list:
    try:
        rows = conn.execute(
            "SELECT signal_id, title FROM signals "
            "WHERE timestamp >= datetime('now', ?) "
            "  AND title IS NOT NULL",
            (f"-{window_hours} hours",)
        ).fetchall()
        return [(r["signal_id"], _normalize_title(r["title"])) for r in rows]
    except Exception:
        return []


def _check_dedup(sig: dict, title_cache: list) -> tuple:
    if not sig.get("title"):
        return ("allow", None, 0.0)

    norm_new = _normalize_title(sig["title"])
    if len(norm_new.split()) <= 2:
        return ("allow", None, 0.0)

    best_sim = 0.0
    best_sid = None
    for (sid, norm_existing) in title_cache:
        sim = _title_similarity(norm_new, norm_existing)
        if sim > best_sim:
            best_sim = sim
            best_sid = sid
            if sim >= 0.90:
                break

    if best_sim >= 0.90:
        return ("block", best_sid, best_sim)
    if best_sim >= 0.70:
        return ("near_dup", best_sid, best_sim)
    return ("allow", None, best_sim)


def _increment_duplicate_count(conn: sqlite3.Connection,
                                signal_id: str) -> None:
    try:
        row = conn.execute(
            "SELECT metadata_json FROM signals WHERE signal_id=?",
            (signal_id,)
        ).fetchone()
        if not row:
            return
        meta = {}
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
            except Exception:
                pass
        meta["duplicate_count"] = meta.get("duplicate_count", 0) + 1
        conn.execute(
            "UPDATE signals SET metadata_json=? WHERE signal_id=?",
            (json.dumps(meta), signal_id)
        )
    except Exception:
        pass


# ── DB helpers ─────────────────────────────────────────────────────────────

def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python app.py --init-db"
        )
    conn = sqlite3.connect(str(path), timeout=60, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── Main run loop ──────────────────────────────────────────────────────────

def run(db_path: Path = DB_PATH) -> dict:
    if not HAS_FEEDPARSER:
        print("[civic_intel_us] feedparser required — pip install feedparser --break-system-packages")
    if not HAS_REQUESTS:
        print("[civic_intel_us] requests required — pip install requests --break-system-packages")

    conn = _open_db(db_path)
    try:
        _ensure_dedup_index(conn)
        title_cache = _load_title_cache(conn, window_hours=24)
        print(f"[civic_intel_us] Dedup cache: {len(title_cache)} titles from last 24h")

        total_new      = 0
        total_skipped  = 0
        total_errors   = 0
        total_blocked  = 0
        total_near_dup = 0
        per_source     = {}

        for source in SOURCES:
            key   = source["source_key"]
            label = source["label"]
            mode  = source.get("fetch_mode", "rss_direct")
            url_display = source.get("url") or "?"
            print(f"[{label}] [{mode}] {url_display}")

            entries = fetch_source(source)
            new_s = skipped_s = blocked_s = near_dup_s = 0

            for entry in entries:
                sig = entry_to_signal(entry, source)
                if sig is None:
                    continue

                if conn.execute(
                    "SELECT 1 FROM signals WHERE external_id=?",
                    (sig["external_id"],)
                ).fetchone():
                    skipped_s += 1
                    continue

                action, matched_sid, sim = _check_dedup(sig, title_cache)

                if action == "block":
                    _increment_duplicate_count(conn, matched_sid)
                    conn.commit()
                    blocked_s += 1
                    print(
                        f"  [dedup:block]    {sig['title'][:60]!r} "
                        f"sim={sim:.2f} → blocked, counter↑ on {matched_sid[:8]}"
                    )
                    continue

                meta = {}
                if action == "near_dup":
                    meta["near_duplicate"] = True
                    meta["near_dup_sim"]   = round(sim, 3)
                    meta["near_dup_ref"]   = matched_sid
                    near_dup_s += 1
                    print(
                        f"  [dedup:near_dup] {sig['title'][:60]!r} "
                        f"sim={sim:.2f} → inserting with tag"
                    )

                meta_json = json.dumps(meta) if meta else None

                try:
                    conn.execute("""
                        INSERT INTO signals
                            (signal_id, source, external_id, title, content,
                             lat, lng, timestamp, status,
                             stream, relevance_score, is_priority,
                             metadata_json, source_type)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'live')
                    """, (
                        sig["signal_id"], sig["source"], sig["external_id"],
                        sig["title"],     sig["content"],
                        sig["lat"],       sig["lng"],
                        sig["timestamp"], sig["status"],
                        sig["stream"],    sig["relevance_score"], sig["is_priority"],
                        meta_json,
                    ))
                    new_s += 1
                    title_cache.append(
                        (sig["signal_id"], _normalize_title(sig["title"]))
                    )
                except sqlite3.IntegrityError:
                    skipped_s += 1
                except Exception as exc:
                    print(f"  [error] insert {sig['external_id']}: {exc}")
                    total_errors += 1

            conn.commit()
            per_source[key] = {
                "new":      new_s,
                "skipped":  skipped_s,
                "blocked":  blocked_s,
                "near_dup": near_dup_s,
                "mode":     mode,
            }
            total_new      += new_s
            total_skipped  += skipped_s
            total_blocked  += blocked_s
            total_near_dup += near_dup_s

            if new_s > 0 or blocked_s > 0:
                parts = [f"✓ {new_s} new"]
                if blocked_s  > 0: parts.append(f"⊘ {blocked_s} blocked")
                if near_dup_s > 0: parts.append(f"≈ {near_dup_s} near-dup")
                parts.append(f"({skipped_s} known)")
                print(f"  {' · '.join(parts)}")
            else:
                print(f"  · {skipped_s} known, nothing new")

        sources_ok = sum(
            1 for v in per_source.values()
            if (v["new"] + v["skipped"]) > 0
        )
        if total_errors > 0 and sources_ok == 0:
            run_status = "error"
        elif sources_ok < len(SOURCES):
            run_status = "partial_success"
        else:
            run_status = "success"

        summary = {
            "collector":       "civic_intel_us",
            "sources":         len(SOURCES),
            "sources_ok":      sources_ok,
            "sources_failed":  len(SOURCES) - sources_ok,
            "total_new":       total_new,
            "total_skipped":   total_skipped,
            "total_blocked":   total_blocked,
            "total_near_dup":  total_near_dup,
            "total_errors":    total_errors,
            "per_source":      per_source,
            "run_status":      run_status,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

        dedup_line = ""
        if total_blocked > 0 or total_near_dup > 0:
            dedup_line = f" · ⊘ {total_blocked} blocked · ≈ {total_near_dup} near-dup"

        print(f"\n[civic_intel_us] Done — {total_new} new{dedup_line} · "
              f"{sources_ok}/{len(SOURCES)} sources active · status: {run_status}")

        log_run(
            db_path,
            "civic_intel_collector_us",
            "success" if run_status in ("success", "partial_success") else "error",
            records_in=total_new + total_skipped + total_blocked,
            records_out=total_new,
            detail=summary,
        )
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="FORGE Civic Intelligence Collector (US)")
    _parser.add_argument("--db", type=Path, default=None, help="Path to database.db")
    _parser.add_argument("--dry-run", action="store_true", help="Fetch and display without DB writes")
    _args = _parser.parse_args()
    db = _args.db.resolve() if _args.db else DB_PATH
    if _args.dry_run:
        print("[civic_intel_us] DRY RUN — would connect to:", db)
        print("[civic_intel_us] Dry run complete (no writes)")
    else:
        print(json.dumps(run(db_path=db), indent=2))

# --- MEGA RUNNER ADAPTER ---
import asyncio as _asyncio

async def async_main(**kwargs):
    try:
        result = run()
        if _asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[ERROR] async_main failed in civic_intel_collector_us.py: {e}")
