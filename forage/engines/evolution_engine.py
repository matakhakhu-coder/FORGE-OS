"""
FORGE — Evolution Engine  (Phase 33)
======================================
Scans the signal archive for emerging entities and phrases that appear
frequently in high-scoring correlated pairs but are not yet covered by
any existing collector source.

Algorithm
─────────
1. Pull the top N correlated pairs by score from correlated_incidents
   (excluding FIRMS pixel-pairs).
2. Extract title + content text from both signals in every pair.
3. Tokenise into 2-gram and 3-gram phrases (N-grams).
4. Score each N-gram candidate by:
     candidate_score = frequency × mean_correlation_score_of_containing_pairs
5. Filter out:
   - English + Afrikaans stop words
   - Single-character tokens
   - Numeric-only tokens
   - Terms already present in existing collector source labels/queries
   - Generic signal noise terms (fire, magnitude, earthquake, firms, etc.)
6. Write top 25 candidates to discovery_targets table (status='pending').
   Existing pending/approved entries are not overwritten — only new terms
   that aren't already in the table are inserted.
7. Log heartbeat to pipeline_runs via pipeline_logger.

Usage
─────
    python forage/engines/evolution_engine.py
    python forage/engines/evolution_engine.py --top 50 --pairs 1000
    python forage/engines/evolution_engine.py --dry-run

Author: FORGE Phase 33
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
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

# ── Stop words ─────────────────────────────────────────────────────────────
# English + Afrikaans common terms + generic signal noise
STOP_WORDS = {
    # English articles / prepositions / conjunctions
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "by","from","as","is","was","are","were","be","been","being","have",
    "has","had","do","does","did","will","would","could","should","may",
    "might","shall","can","not","no","nor","so","yet","both","either",
    "neither","each","few","more","most","other","some","such","than",
    "too","very","just","about","above","after","before","between","during",
    "into","through","under","over","again","then","once","here","there",
    "when","where","why","how","all","any","both","each","few","more",
    "that","this","these","those","it","its","he","she","they","we","you",
    "his","her","their","our","your","who","which","what","said","says",
    # Afrikaans common
    "van","die","en","in","op","aan","met","vir","dat","wat","het","is",
    "was","nie","om","na","uit","oor","deur","kan","sal","ook","maar",
    # Generic signal noise
    "fire","active","firms","magnitude","earthquake","depth","alert",
    "signal","report","release","media","press","news","new","latest",
    "update","south","africa","african","national","provincial","local",
    "government","minister","department","official","statement","says",
    "said","told","according","per","cent","percent","million","billion",
    "rand","year","years","month","months","day","days","week","weeks",
    "last","first","second","third","also","however","while","despite",
    # Geographic noise at country level
    "johannesburg","pretoria","cape","town","durban","gauteng","western",
    "eastern","northern","kwazulu","natal","limpopo","mpumalanga","free",
    "state","north","west","region","area","city","province","district",
    # Time noise
    "monday","tuesday","wednesday","thursday","friday","saturday","sunday",
    "january","february","march","april","may","june","july","august",
    "september","october","november","december",
}

# ── Terms already covered by existing collectors ───────────────────────────
# These are extracted from civic_intel_collector.py source labels and queries
# to avoid suggesting sources we already ingest.
EXISTING_COVERAGE = {
    "amabhungane","amabungane","oxpeckers","dailymaverick","daily maverick",
    "groundup","ground up","news24","timeslive","times live","eskom",
    "municipality","municipal","saps","police service","hawks","dpci",
    "directorate priority crime","npa","national prosecuting","corruption",
    "vbs","tender","load shedding","loadshedding","water outage","sewage",
    "road collapse","infrastructure","arrest","prosecution","crime court",
    "investigat","zondo","gupta","magashule","poca","precca","mfma",
}


def _resolve_db(override: Optional[str] = None) -> Path:
    import os
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return DB_PATH


def _open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Database not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create discovery_targets if it doesn't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_targets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_name     TEXT    NOT NULL UNIQUE,
            suggested_query TEXT    NOT NULL,
            evidence_count  INTEGER NOT NULL DEFAULT 0,
            evidence_json   TEXT,
            candidate_score REAL    NOT NULL DEFAULT 0.0,
            status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','approved','ignored')),
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            actioned_at     TEXT
        )
    """)
    conn.commit()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{_ts()}] [evolution_engine] {msg}", flush=True)


def _clean_text(text: str) -> str:
    """Strip HTML, URLs, punctuation; lowercase."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def _ngrams(tokens: list[str], n: int) -> list[str]:
    return [" ".join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]


def _is_covered(phrase: str) -> bool:
    """Return True if phrase overlaps significantly with existing coverage."""
    pl = phrase.lower()
    for term in EXISTING_COVERAGE:
        if term in pl or pl in term:
            return True
    return False


def _is_valid_token(tok: str) -> bool:
    return (
        len(tok) > 2
        and tok not in STOP_WORDS
        and not tok.isdigit()
        and not re.match(r"^\d+[\.,]\d+$", tok)  # decimals
    )


def _is_valid_phrase(phrase: str) -> bool:
    """A phrase is valid if all its tokens pass and it's not existing coverage."""
    tokens = phrase.split()
    if not all(_is_valid_token(t) for t in tokens):
        return False
    if _is_covered(phrase):
        return False
    return True


def _build_google_query(phrase: str) -> str:
    """Generate a Google News RSS URL for a candidate phrase."""
    import urllib.parse
    q = f'"{phrase}" south africa'
    params = urllib.parse.urlencode({
        "q":    q,
        "hl":   "en-ZA",
        "gl":   "ZA",
        "ceid": "ZA:en",
    })
    return f"https://news.google.com/rss/search?{params}"


class EvolutionEngine:

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or _resolve_db()

    def run(self, top_n: int = 25, pair_limit: int = 500,
            dry_run: bool = False) -> dict:
        log(f"Database : {self._db_path}")
        log(f"Top N    : {top_n} | Pair limit: {pair_limit} | Dry run: {dry_run}")

        conn = _open_db(self._db_path)
        _ensure_schema(conn)

        # ── 1. Pull top correlated pairs (exclude FIRMS) ──────────────────
        log("Loading top correlated pairs…")
        try:
            pairs = conn.execute("""
                SELECT ci.correlation_score,
                       sa.signal_id AS sid_a, sa.title AS title_a,
                       sa.content   AS content_a, sa.source AS src_a,
                       sb.signal_id AS sid_b, sb.title AS title_b,
                       sb.content   AS content_b, sb.source AS src_b
                FROM   correlated_incidents ci
                JOIN   signals sa ON sa.signal_id = ci.signal_a
                JOIN   signals sb ON sb.signal_id = ci.signal_b
                WHERE  sa.source != 'firms'
                  AND  sb.source != 'firms'
                  AND  ci.correlation_score >= 0.7
                ORDER  BY ci.correlation_score DESC
                LIMIT  ?
            """, (pair_limit,)).fetchall()
        except Exception as exc:
            log(f"ERROR loading pairs: {exc}")
            conn.close()
            return {"status": "error", "error": str(exc)}

        log(f"Pairs loaded: {len(pairs)}")
        if not pairs:
            log("No pairs found — run correlation_engine first")
            conn.close()
            return {"status": "no_pairs", "candidates": 0, "written": 0}

        # ── 2. Extract N-grams from pair text ─────────────────────────────
        # Track: phrase → {total_score, count, evidence_signal_ids}
        phrase_scores: dict[str, float]     = Counter()
        phrase_counts: dict[str, int]       = Counter()
        phrase_evidence: dict[str, set]     = defaultdict(set)

        for row in pairs:
            score = float(row["correlation_score"])
            for side in ("a", "b"):
                title   = row[f"title_{side}"]   or ""
                content = row[f"content_{side}"] or ""
                sid     = row[f"sid_{side}"]

                text   = _clean_text(f"{title} {content}")
                tokens = [t for t in text.split() if _is_valid_token(t)]

                for n in (2, 3):
                    for phrase in _ngrams(tokens, n):
                        if _is_valid_phrase(phrase):
                            phrase_scores[phrase]  += score
                            phrase_counts[phrase]  += 1
                            phrase_evidence[phrase].add(sid)

        log(f"Unique candidate phrases: {len(phrase_scores)}")

        # ── 3. Score and rank ─────────────────────────────────────────────
        # candidate_score = frequency × mean_correlation_score
        scored = []
        for phrase, total_score in phrase_scores.items():
            count = phrase_counts[phrase]
            if count < 2:   # must appear in at least 2 pairs
                continue
            mean_score = total_score / count
            candidate_score = round(count * mean_score, 4)
            evidence_ids    = list(phrase_evidence[phrase])[:10]
            scored.append((candidate_score, count, phrase, evidence_ids))

        scored.sort(reverse=True)
        top_candidates = scored[:top_n]

        log(f"Top {len(top_candidates)} candidates selected")
        for cs, cnt, phrase, _ in top_candidates[:10]:
            log(f"  [{cs:.3f} score | {cnt} pairs] {phrase}")

        if dry_run:
            conn.close()
            result = {
                "status":     "dry_run",
                "candidates": len(top_candidates),
                "written":    0,
                "top":        [{"phrase": p, "score": s, "count": c}
                               for s, c, p, _ in top_candidates],
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }
            log(f"Dry run complete: {result}")
            return result

        # ── 4. Write to discovery_targets ─────────────────────────────────
        written = 0
        skipped = 0
        for candidate_score, count, phrase, evidence_ids in top_candidates:
            # Don't overwrite existing entries
            existing = conn.execute(
                "SELECT id, status FROM discovery_targets WHERE entity_name=?",
                (phrase,)
            ).fetchone()
            if existing:
                skipped += 1
                continue

            suggested_query = _build_google_query(phrase)
            evidence_json   = json.dumps({"signal_ids": evidence_ids, "pair_count": count})

            try:
                conn.execute("""
                    INSERT INTO discovery_targets
                        (entity_name, suggested_query, evidence_count,
                         evidence_json, candidate_score, status)
                    VALUES (?,?,?,?,?,'pending')
                """, (phrase, suggested_query, count, evidence_json, candidate_score))
                written += 1
            except sqlite3.IntegrityError:
                skipped += 1

        conn.commit()
        conn.close()

        summary = {
            "status":          "done",
            "pairs_scanned":   len(pairs),
            "unique_phrases":  len(phrase_scores),
            "candidates":      len(top_candidates),
            "written":         written,
            "skipped_existing": skipped,
            "computed_at":     datetime.now(timezone.utc).isoformat(),
        }
        log(f"Complete: {summary}")

        log_run(
            self._db_path,
            "evolution_engine",
            "success",
            records_in=len(pairs),
            records_out=written,
            detail=summary,
        )
        return summary


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Evolution Engine — candidate entity discovery"
    )
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--top",     type=int,  default=25)
    parser.add_argument("--pairs",   type=int,  default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = EvolutionEngine(
        db_path=_resolve_db(str(args.db) if args.db else None)
    )
    result = engine.run(
        top_n=args.top,
        pair_limit=args.pairs,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") in ("done", "dry_run", "no_pairs") else 1)