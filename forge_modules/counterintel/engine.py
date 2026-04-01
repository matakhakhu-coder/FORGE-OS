"""
FORGE — CounterIntel Engine  (forge_modules/counterintel/engine.py)
====================================================================
Detects adversarial influence patterns in the signal corpus.

THREE INDEPENDENT DETECTORS
────────────────────────────
1. NARRATIVE CLUSTERING
   TF-IDF cosine similarity across signal titles + content.
   Signals with similarity >= threshold (default 0.82) are grouped
   into narrative clusters. A cluster of 3+ signals from different
   sources is flagged as a coordinated narrative.

2. BOT-PATTERN DETECTION
   SimHash-based near-duplicate detection. Signals whose SimHash
   differs by <= 3 bits (Hamming distance) are near-duplicates.
   A signal appearing 5+ times as near-duplicate of others is
   flagged as bot-like repetition.

3. CAMPAIGN FINGERPRINTING
   Source coordination scoring. If 3+ different sources publish
   signals with >75% title token overlap within a 6-hour window,
   the cluster is flagged as a possible information campaign.

WHAT THIS DOES NOT DUPLICATE
─────────────────────────────
- signal_interpreter: severity, event_type, source_credibility — untouched
- ner_processor: signal_entities, confidence_score — untouched
- anomaly_engine: volume Z-scores, sentinel_alerts — untouched

STORAGE
────────
All flags written to signal_flags table (never metadata_json).
signal_flags rows are idempotent — re-running replaces existing flags.

SOURCE FILTER
─────────────
Only CRIME_INTEL and PRIORITY stream signals are analysed.
FIRMS, USGS, GDACS, RSS are excluded — they are legitimately
repetitive (fire pixels, seismic feeds) and would flood false positives.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("forge.modules.counterintel")

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

# ── Tuneable constants ────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD   = 0.82   # cosine similarity for narrative clustering
SIMHASH_BIT_THRESHOLD  = 3      # Hamming distance for near-duplicate
BOT_MIN_DUPLICATES     = 5      # min near-duplicate count to flag bot pattern
CAMPAIGN_MIN_SOURCES   = 3      # min distinct sources for campaign flag
CAMPAIGN_WINDOW_HOURS  = 6      # time window for campaign detection
CAMPAIGN_TOKEN_OVERLAP = 0.75   # min token overlap ratio for campaign
MIN_TEXT_LENGTH        = 20     # ignore signals shorter than this
ANALYSED_STREAMS       = {"CRIME_INTEL", "PRIORITY"}
EXCLUDED_SOURCES       = {"firms", "usgs", "gdacs", "rss", "earthquake"}
MAX_SIGNALS            = 2000   # cap to keep scans fast

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS signal_flags (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id       TEXT    NOT NULL REFERENCES signals(signal_id)
                                ON DELETE CASCADE,
        flag_type       TEXT    NOT NULL,
        flag_label      TEXT    NOT NULL,
        confidence      REAL    NOT NULL DEFAULT 0.5
                        CHECK(confidence >= 0.0 AND confidence <= 1.0),
        cluster_id      TEXT,
        detail_json     TEXT,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (signal_id, flag_type)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_signal_flags_signal
        ON signal_flags (signal_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_signal_flags_type
        ON signal_flags (flag_type, confidence DESC)
    """,
]


# ── Text utilities ────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Lowercase, strip punctuation, normalise whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenise(text: str) -> list[str]:
    STOPWORDS = {
        "the","a","an","and","or","but","in","on","at","to","for",
        "of","with","by","from","is","was","are","were","be","been",
        "has","have","had","will","would","could","should","may","might",
        "this","that","these","those","it","its","as","up","out","about",
        "south","africa","african","said","says","new","also","after",
    }
    return [t for t in _clean(text).split() if t not in STOPWORDS and len(t) > 2]


def _build_text(signal: dict) -> str:
    return f"{signal.get('title','') or ''} {signal.get('content','') or ''}".strip()


# ── TF-IDF cosine similarity ──────────────────────────────────────────────────

def _tfidf_vectors(corpus: list[list[str]]) -> list[dict[str, float]]:
    """Compute TF-IDF vectors for a corpus of tokenised documents."""
    N = len(corpus)
    if N == 0:
        return []

    # Document frequency
    df: dict[str, int] = defaultdict(int)
    for tokens in corpus:
        for t in set(tokens):
            df[t] += 1

    vectors = []
    for tokens in corpus:
        tf: dict[str, float] = defaultdict(float)
        for t in tokens:
            tf[t] += 1.0
        # Normalise TF and apply IDF
        vec: dict[str, float] = {}
        for t, freq in tf.items():
            idf = math.log((N + 1) / (df[t] + 1)) + 1.0
            vec[t] = (freq / len(tokens)) * idf
        vectors.append(vec)
    return vectors


def _cosine(v1: dict, v2: dict) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in common)
    mag1 = math.sqrt(sum(x * x for x in v1.values()))
    mag2 = math.sqrt(sum(x * x for x in v2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


# ── SimHash ───────────────────────────────────────────────────────────────────

def _simhash(tokens: list[str], bits: int = 64) -> int:
    """Compute a 64-bit SimHash fingerprint for a token list."""
    v = [0] * bits
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def _hamming(a: int, b: int) -> int:
    """Hamming distance between two integers (bit-level)."""
    return bin(a ^ b).count("1")


# ── Detector 1: Narrative clustering ─────────────────────────────────────────

def detect_narratives(signals: list[dict]) -> list[dict]:
    """
    Group signals by TF-IDF cosine similarity.
    Returns list of flag dicts for signals in multi-source clusters.
    """
    if len(signals) < 3:
        return []

    corpus     = [_tokenise(_build_text(s)) for s in signals]
    vectors    = _tfidf_vectors(corpus)
    n          = len(signals)

    # Build similarity clusters using greedy single-linkage
    assigned: dict[int, str] = {}   # index → cluster_id
    clusters: dict[str, list[int]] = defaultdict(list)
    cluster_counter = 0

    for i in range(n):
        if i in assigned:
            continue
        cluster_id = f"NAR_{cluster_counter:04d}"
        cluster_counter += 1
        assigned[i] = cluster_id
        clusters[cluster_id].append(i)

        for j in range(i + 1, n):
            if j in assigned:
                continue
            if not vectors[i] or not vectors[j]:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim >= SIMILARITY_THRESHOLD:
                assigned[j] = cluster_id
                clusters[cluster_id].append(j)

    flags = []
    for cluster_id, indices in clusters.items():
        if len(indices) < 3:
            continue
        # Check source diversity — needs 2+ distinct sources to be suspicious
        sources = {signals[i].get("source", "") for i in indices}
        if len(sources) < 2:
            continue

        member_count = len(indices)
        confidence   = min(0.5 + (member_count - 3) * 0.08, 0.95)

        for idx in indices:
            sig = signals[idx]
            flags.append({
                "signal_id":  sig["signal_id"],
                "flag_type":  "narrative_cluster",
                "flag_label": f"Coordinated narrative — {member_count} signals, "
                              f"{len(sources)} sources",
                "confidence": round(confidence, 3),
                "cluster_id": cluster_id,
                "detail": {
                    "cluster_size":    member_count,
                    "source_count":    len(sources),
                    "sources":         sorted(sources),
                    "similarity_threshold": SIMILARITY_THRESHOLD,
                },
            })

    log.info(
        f"[counterintel] Narrative detector: "
        f"{len([c for c in clusters.values() if len(c) >= 3])} clusters flagged"
    )
    return flags


# ── Detector 2: Bot-pattern detection ────────────────────────────────────────

def detect_bot_patterns(signals: list[dict]) -> list[dict]:
    """
    SimHash near-duplicate detection.
    Signals with Hamming distance <= SIMHASH_BIT_THRESHOLD are near-duplicates.
    Signals appearing as near-duplicate of BOT_MIN_DUPLICATES+ others are flagged.
    """
    fingerprints = []
    for sig in signals:
        tokens = _tokenise(_build_text(sig))
        if len(tokens) < 4:
            fingerprints.append(None)
            continue
        fingerprints.append(_simhash(tokens))

    # Count near-duplicate relationships per signal
    dup_count:  dict[int, int]       = defaultdict(int)
    dup_groups: dict[int, list[int]] = defaultdict(list)

    for i in range(len(signals)):
        if fingerprints[i] is None:
            continue
        for j in range(i + 1, len(signals)):
            if fingerprints[j] is None:
                continue
            dist = _hamming(fingerprints[i], fingerprints[j])
            if dist <= SIMHASH_BIT_THRESHOLD:
                dup_count[i]  += 1
                dup_count[j]  += 1
                dup_groups[i].append(j)
                dup_groups[j].append(i)

    flags = []
    for idx, count in dup_count.items():
        if count < BOT_MIN_DUPLICATES:
            continue
        sig        = signals[idx]
        confidence = min(0.55 + (count - BOT_MIN_DUPLICATES) * 0.05, 0.95)
        flags.append({
            "signal_id":  sig["signal_id"],
            "flag_type":  "bot_pattern",
            "flag_label": f"Bot-like repetition — {count} near-identical signals",
            "confidence": round(confidence, 3),
            "cluster_id": f"BOT_{idx:06d}",
            "detail": {
                "near_duplicate_count": count,
                "hamming_threshold":    SIMHASH_BIT_THRESHOLD,
                "source":               sig.get("source", ""),
            },
        })

    log.info(f"[counterintel] Bot detector: {len(flags)} signals flagged")
    return flags


# ── Detector 3: Campaign fingerprinting ──────────────────────────────────────

def detect_campaigns(signals: list[dict]) -> list[dict]:
    """
    Source coordination detection.
    If CAMPAIGN_MIN_SOURCES+ distinct sources publish signals with
    CAMPAIGN_TOKEN_OVERLAP+ title token overlap within CAMPAIGN_WINDOW_HOURS,
    the cluster is flagged as a possible information campaign.
    """
    # Sort by timestamp for windowed comparison
    timed = []
    for sig in signals:
        ts = sig.get("timestamp") or ""
        timed.append((ts, sig))
    timed.sort(key=lambda x: x[0])
    ordered = [s for _, s in timed]

    n          = len(ordered)
    flagged    = set()
    camp_flags = []
    campaign_counter = 0

    for i in range(n):
        if i in flagged:
            continue
        sig_i  = ordered[i]
        tok_i  = set(_tokenise(sig_i.get("title", "") or ""))
        if len(tok_i) < 4:
            continue
        ts_i   = sig_i.get("timestamp") or ""
        src_i  = sig_i.get("source", "")

        campaign_members = [i]
        campaign_sources = {src_i}

        for j in range(i + 1, n):
            sig_j = ordered[j]
            ts_j  = sig_j.get("timestamp") or ""

            # Time window check (string ISO comparison works for sorted data)
            if ts_i and ts_j:
                try:
                    from datetime import datetime as _dt
                    t1 = _dt.fromisoformat(ts_i.replace("Z", "+00:00")
                                           .replace(" ", "T"))
                    t2 = _dt.fromisoformat(ts_j.replace("Z", "+00:00")
                                           .replace(" ", "T"))
                    diff_hours = abs((t2 - t1).total_seconds()) / 3600
                    if diff_hours > CAMPAIGN_WINDOW_HOURS:
                        break
                except Exception:
                    pass

            tok_j = set(_tokenise(sig_j.get("title", "") or ""))
            if len(tok_j) < 4:
                continue

            # Token overlap (Jaccard-style: overlap / min set size)
            overlap = len(tok_i & tok_j) / min(len(tok_i), len(tok_j))
            if overlap >= CAMPAIGN_TOKEN_OVERLAP:
                campaign_members.append(j)
                campaign_sources.add(sig_j.get("source", ""))

        if len(campaign_sources) < CAMPAIGN_MIN_SOURCES:
            continue

        campaign_id = f"CAMP_{campaign_counter:04d}"
        campaign_counter += 1
        confidence  = min(0.60 + (len(campaign_sources) - CAMPAIGN_MIN_SOURCES) * 0.08, 0.95)

        for idx in campaign_members:
            flagged.add(idx)
            sig = ordered[idx]
            camp_flags.append({
                "signal_id":  sig["signal_id"],
                "flag_type":  "information_campaign",
                "flag_label": f"Possible campaign — {len(campaign_sources)} "
                              f"coordinated sources, {len(campaign_members)} signals",
                "confidence": round(confidence, 3),
                "cluster_id": campaign_id,
                "detail": {
                    "source_count":    len(campaign_sources),
                    "sources":         sorted(campaign_sources),
                    "signal_count":    len(campaign_members),
                    "window_hours":    CAMPAIGN_WINDOW_HOURS,
                    "token_overlap":   CAMPAIGN_TOKEN_OVERLAP,
                },
            })

    log.info(f"[counterintel] Campaign detector: {len(camp_flags)} signals flagged")
    return camp_flags


# ── Write flags ───────────────────────────────────────────────────────────────

def _write_flags(conn: sqlite3.Connection, flags: list[dict],
                 dry_run: bool = False) -> int:
    """Write flag list to signal_flags table. Returns count written."""
    if dry_run or not flags:
        return len(flags)
    written = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for f in flags:
        conn.execute("""
            INSERT INTO signal_flags
                (signal_id, flag_type, flag_label, confidence,
                 cluster_id, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id, flag_type) DO UPDATE SET
                flag_label  = excluded.flag_label,
                confidence  = excluded.confidence,
                cluster_id  = excluded.cluster_id,
                detail_json = excluded.detail_json,
                created_at  = excluded.created_at
        """, (
            f["signal_id"],
            f["flag_type"],
            f["flag_label"],
            f["confidence"],
            f.get("cluster_id"),
            json.dumps(f.get("detail", {})),
            now,
        ))
        written += 1
    conn.commit()
    return written


# ── Main engine function ──────────────────────────────────────────────────────

def run(signal: dict = None, dry_run: bool = False,
        db_path: Path = None) -> dict:
    """
    Public engine entry point.

    signal argument accepted for Conclave compatibility but not used —
    counterintel analyses the full corpus, not individual signals.
    """
    _db   = db_path or DB_PATH
    start = time.monotonic()

    conn = _open_db(_db)
    try:
        _ensure_schema(conn)

        # ── Load signals for analysis ─────────────────────────────────────
        # Only CRIME_INTEL + PRIORITY streams, excluding known repetitive sources
        excluded_ph = ",".join("?" * len(EXCLUDED_SOURCES))
        rows = conn.execute(f"""
            SELECT signal_id, source, stream, title, content, timestamp
            FROM   signals
            WHERE  stream IN ('CRIME_INTEL', 'PRIORITY')
              AND  (source IS NULL OR LOWER(source) NOT IN ({excluded_ph}))
              AND  status IN ('raw', 'promoted')
            ORDER  BY timestamp DESC
            LIMIT  ?
        """, (*EXCLUDED_SOURCES, MAX_SIGNALS)).fetchall()

        signals = [dict(r) for r in rows]

        # Filter by minimum text length
        signals = [
            s for s in signals
            if len(_build_text(s)) >= MIN_TEXT_LENGTH
        ]

        log.info(
            f"[counterintel] Analysing {len(signals)} signals "
            f"(streams: CRIME_INTEL, PRIORITY)"
        )

        if len(signals) < 3:
            conn.close()
            return {
                "status":    "success",
                "message":   "Insufficient signals for analysis (need 3+)",
                "signals_analysed": len(signals),
                "flags_written": 0,
                "duration_s": round(time.monotonic() - start, 2),
            }

        # ── Run three detectors ───────────────────────────────────────────
        narrative_flags = detect_narratives(signals)
        bot_flags       = detect_bot_patterns(signals)
        campaign_flags  = detect_campaigns(signals)

        all_flags = narrative_flags + bot_flags + campaign_flags

        # ── Write results ─────────────────────────────────────────────────
        written = _write_flags(conn, all_flags, dry_run=dry_run)

        duration = round(time.monotonic() - start, 2)

        result = {
            "status":              "success",
            "signals_analysed":    len(signals),
            "narrative_clusters":  len({f["cluster_id"] for f in narrative_flags}),
            "bot_flags":           len(bot_flags),
            "campaign_flags":      len({f["cluster_id"] for f in campaign_flags}),
            "total_flags":         len(all_flags),
            "flags_written":       written,
            "dry_run":             dry_run,
            "duration_s":          duration,
        }

        log.info(f"[counterintel] Complete: {result}")
        return result

    except Exception as exc:
        log.error(f"[counterintel] Engine error: {exc}")
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return {
            "status":    "error",
            "error":     str(exc),
            "duration_s": round(time.monotonic() - start, 2),
        }
    finally:
        conn.close()


# ── Query helpers ─────────────────────────────────────────────────────────────

def query_flags(db_path: Path = None, flag_type: str = None,
                min_confidence: float = 0.0,
                limit: int = 200) -> list[dict]:
    """Return signal flags, optionally filtered by type and confidence."""
    _db  = db_path or DB_PATH
    conn = _open_db(_db)
    try:
        _ensure_schema(conn)
        where = "WHERE sf.confidence >= ?"
        params: list = [min_confidence]
        if flag_type:
            where += " AND sf.flag_type = ?"
            params.append(flag_type)
        rows = conn.execute(f"""
            SELECT
                sf.id, sf.signal_id, sf.flag_type, sf.flag_label,
                sf.confidence, sf.cluster_id, sf.detail_json, sf.created_at,
                s.title, s.source, s.stream, s.timestamp
            FROM   signal_flags sf
            JOIN   signals s ON s.signal_id = sf.signal_id
            {where}
            ORDER  BY sf.confidence DESC, sf.created_at DESC
            LIMIT  ?
        """, (*params, limit)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        detail = {}
        if r["detail_json"]:
            try:
                detail = json.loads(r["detail_json"])
            except Exception:
                pass
        result.append({
            "id":          r["id"],
            "signal_id":   r["signal_id"],
            "flag_type":   r["flag_type"],
            "flag_label":  r["flag_label"],
            "confidence":  r["confidence"],
            "cluster_id":  r["cluster_id"],
            "detail":      detail,
            "created_at":  r["created_at"],
            "signal": {
                "title":     r["title"],
                "source":    r["source"],
                "stream":    r["stream"],
                "timestamp": r["timestamp"],
            },
        })
    return result


def query_summary(db_path: Path = None) -> dict:
    """Return aggregate summary of all flags."""
    _db  = db_path or DB_PATH
    conn = _open_db(_db)
    try:
        _ensure_schema(conn)
        rows = conn.execute("""
            SELECT
                flag_type,
                COUNT(*)            AS total,
                AVG(confidence)     AS avg_confidence,
                MAX(confidence)     AS max_confidence,
                COUNT(DISTINCT cluster_id) AS clusters,
                MAX(created_at)     AS last_run
            FROM signal_flags
            GROUP BY flag_type
            ORDER BY total DESC
        """).fetchall()
        total_flagged = conn.execute(
            "SELECT COUNT(DISTINCT signal_id) FROM signal_flags"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "total_flagged_signals": total_flagged,
        "by_type": [dict(r) for r in rows],
    }


# ── Internals ─────────────────────────────────────────────────────────────────

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_SQL:
        conn.execute(stmt)
    conn.commit()