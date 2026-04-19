# -*- coding: utf-8 -*-
"""
FORGE -- Relationship Triple Extractor  (forage/processors/triple_extractor.py)
================================================================================
Extracts (Subject) --[Verb]--> (Object) triples from high-relevance signals
and writes directed, typed edges into entity_relationships.

Phase 44 changes (Evidence Closure Pipeline):
  - SA EntityRuler loaded before 'ner': SIU, NPA, Hawks, DPCI, Treasury etc.
    are now correctly tagged ORG rather than MISC or skipped entirely.
  - pdf_infiltrator signals: text sourced from artifacts.raw_text_cache
    (real PDF prose) rather than the structured intel summary in content.
  - entity_relationships.source_artifact_id populated on every INSERT so
    every edge can be traced back to its source PDF artifact.
  - CLI: --pdf-only flag runs extraction on pdf_infiltrator signals only.

Signal selection:
    Priority 1: relevance_score > 1.0  (investigative sources + PDF signals)
    Priority 2: gravity_score   > 0.2  (conclave-scored signals)
    Exclusions: firms, usgs, GDACS, earthquake (sensor noise)

spaCy model: en_core_web_sm (required)
    python -m spacy download en_core_web_sm

Idempotent: UNIQUE(subject, object, relation_type) prevents duplicates.

Author: FORGE Phase 43 → 44
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH  = BASE_DIR / "database.db"

# ---------------------------------------------------------------------------
# Canonical verb -> relation_type mapping
# Keys are spaCy verb LEMMAS (lowercase).
# Grouped by investigative significance for SA anti-corruption context.
# ---------------------------------------------------------------------------
VERB_TO_RELATION: Dict[str, str] = {
    # ── INVESTIGATES ────────────────────────────────────────────────────────
    "investigate": "INVESTIGATES", "arrest":     "INVESTIGATES",
    "charge":      "INVESTIGATES", "prosecute":  "INVESTIGATES",
    "raid":        "INVESTIGATES", "detain":     "INVESTIGATES",
    "probe":       "INVESTIGATES", "indict":     "INVESTIGATES",
    "convict":     "INVESTIGATES", "sentence":   "INVESTIGATES",
    "search":      "INVESTIGATES", "seize":      "INVESTIGATES",
    "summon":      "INVESTIGATES", "subpoena":   "INVESTIGATES",
    "question":    "INVESTIGATES", "apprehend":  "INVESTIGATES",
    "implicate":   "INVESTIGATES", "implicated": "INVESTIGATES",
    "expose":      "INVESTIGATES", "uncover":    "INVESTIGATES",

    # ── CONTRACTED ──────────────────────────────────────────────────────────
    "award":   "CONTRACTED", "tender":  "CONTRACTED",
    "receive": "CONTRACTED", "pay":     "CONTRACTED",
    "appoint": "CONTRACTED", "procure": "CONTRACTED",
    "bid":     "CONTRACTED", "win":     "CONTRACTED",
    "grant":   "CONTRACTED", "fund":    "CONTRACTED",
    "contract":"CONTRACTED", "hire":    "CONTRACTED",
    "outsource":"CONTRACTED","supply":  "CONTRACTED",

    # ── ACCUSED_OF ──────────────────────────────────────────────────────────
    "accuse":    "ACCUSED_OF", "allege":  "ACCUSED_OF",
    "claim":     "ACCUSED_OF", "suspect": "ACCUSED_OF",
    "link":      "ACCUSED_OF", "allege":  "ACCUSED_OF",
    "blame":     "ACCUSED_OF", "implicate":"ACCUSED_OF",

    # ── MEMBER_OF ───────────────────────────────────────────────────────────
    "join":    "MEMBER_OF", "lead":    "MEMBER_OF",
    "head":    "MEMBER_OF", "belong":  "MEMBER_OF",
    "chair":   "MEMBER_OF", "direct":  "MEMBER_OF",
    "represent":"MEMBER_OF","command": "MEMBER_OF",
}

# Minimum token length for an actor substring match (avoids "NPA" matching "snap")
_MIN_MATCH_LEN = 4

# Generic nouns that spaCy tags as ORG/GPE but carry no forensic identity as
# triple SUBJECTS. They are valid objects (e.g. "SIU investigated the State")
# but if they appear as the subject the triple has no investigative value.
_GENERIC_SUBJECTS = frozenset({
    # ── Generic institutional / geographic placeholders ───────────────────────
    "south africa", "government", "state", "provincial", "municipality",
    "suspect", "person", "individual", "company", "entity", "organisation",
    "organization", "authority", "department", "ministry", "office",
    "court", "report", "media", "source", "official", "spokesperson",
    "representative", "committee", "commission", "board", "panel",
    "unit", "team", "group", "service", "agency", "body", "centre",
    # ── Satellite / sensor noise (NASA FIRMS thermal anomaly feed) ─────────────
    # spaCy tags 'MW' (Megawatt power unit) as ORG and 'FRP' (Fire Radiative
    # Power, a FIRMS column header) as ORG/PERSON. 'Alaska' and 'Myanmar' are
    # FIRMS region labels that leak into the actor index as GPE. 'actor2' is a
    # raw GDELT/ACLED column header. All four clogged signal_entities with
    # 223,517 noise rows before this guardrail was added (2026-04-14).
    "mw", "frp", "alaska", "myanmar", "actor2",
    # Related FIRMS / sensor headers that may appear in the same feeds
    "firms", "viirs", "modis", "acq", "satellite", "confidence",
})

# SA professional title prefixes to strip before actor resolution.
# "Advocate Simelane" → "Simelane"; "DPP Jiba" → "Jiba"
_TITLE_PREFIXES = (
    "advocate ", "adv ", "adv. ",
    "director of public prosecutions ", "dpp ",
    "national director of public prosecutions ", "ndpp ",
    "senior public prosecutor ", "public prosecutor ",
    "prosecutor ", "magistrate ", "judge ", "justice ",
    "senior advocate ", "sc ", "kc ",
    "inspector general ", "special investigator ",
    "commissioner ", "general ", "brigadier ", "colonel ",
    "minister ", "deputy minister ", "director general ",
    "chief executive officer ", "ceo ", "cfo ", "coo ",
    "mr ", "ms ", "mrs ", "dr ", "prof ", "rev ",
)

# HTML + artifact cleaning
_HTML_RE      = re.compile(r"<[^>]+>")
_NBSP_RE      = re.compile(r"&nbsp;|\\xa0|\xa0")
_URL_SUFFIX   = re.compile(r"\s+\S+\.(gov\.za|co\.za|org\.za|com)\S*", re.I)
_MULTI_SPACE  = re.compile(r"\s{2,}")

# Normalize name for matching (mirrors entity_resolver.normalize_name)
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    t = text.strip().lower()
    t = _NORM_RE.sub(" ", t)
    return " ".join(p for p in t.split() if p)


def _clean_text(raw: str) -> str:
    t = _HTML_RE.sub(" ", raw or "")
    t = _NBSP_RE.sub(" ", t)
    t = _URL_SUFFIX.sub(" ", t)
    t = _MULTI_SPACE.sub(" ", t)
    return t.strip()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    try:
        print(f"[{_ts()}] [triple_extractor] {msg}", flush=True)
    except UnicodeEncodeError:
        safe = msg.encode("utf-8", errors="replace").decode("ascii", errors="replace")
        print(f"[{_ts()}] [triple_extractor] {safe}", flush=True)


# ---------------------------------------------------------------------------
# Actor index — built once per run from the actors table
# ---------------------------------------------------------------------------

def _build_actor_index(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Returns {normalized_name: actor_id} for all actors.
    Also adds common abbreviation forms:
        'south african police service' -> also index 'saps'
        'national prosecuting authority' -> also 'npa'

    Applies a quality filter to exclude NER-artifact actors whose names
    contain publication markers, emoji, HTML, or leading punctuation.
    """
    # Publication byline markers — actors whose names contain these are artifacts
    _NOISE_MARKERS = (
        " - timeslive", " - groundup", " - daily maverick",
        " - news24", " - dailymaverick", " - iol", " - saps",
        "saps.gov.za", ".gov.za", "date published",
        "groundup.",   # 'GroundUp. City Power' compound artifacts
    )
    # Standalone publication names (exact normalized match)
    _PUBLICATION_NAMES = frozenset({
        "groundup", "timeslive", "news24", "daily maverick", "dailymaverick",
        "iol", "amabhungane", "oxpeckers", "mybroadband", "pressreader",
        "businessday", "business day", "dispatch", "herald",
    })
    _NOISE_START = ("-", "–", "—", "&", "<", ">", "·")
    # Typographic noise characters that appear in mangled NER extractions
    _TYPO_CHARS = frozenset({
        "\u2018", "\u2019",  # smart single quotes
        "\u201c", "\u201d",  # smart double quotes
        "\ufffd",            # Unicode replacement character
        "\u2022",            # bullet
    })

    def _is_quality_actor(name: str) -> bool:
        """Return True if the actor name is usable for triple extraction."""
        if not name or len(name.strip()) < 3:
            return False
        stripped = name.strip()
        low      = stripped.lower()
        # Leading punctuation artifacts
        if stripped[0] in _NOISE_START:
            return False
        # Typographic mangling (curly quotes, replacement char, etc.)
        if any(c in _TYPO_CHARS for c in stripped):
            return False
        # Emoji / supplementary Unicode planes
        if any(ord(c) > 0x2FFF for c in stripped):
            return False
        # Publication byline substring markers
        if any(m in low for m in _NOISE_MARKERS):
            return False
        # Exact-match publication names (normalized)
        norm = _normalize(stripped)
        if norm in _PUBLICATION_NAMES:
            return False
        # Pure numeric
        if stripped.isdigit():
            return False
        return True

    index: Dict[str, int] = {}

    for row in conn.execute("SELECT actor_id, name FROM actors WHERE name IS NOT NULL"):
        actor_id = int(row[0])
        name     = row[1]
        if not _is_quality_actor(name):
            continue
        norm = _normalize(name)
        if norm:
            index[norm] = actor_id

    # Well-known SA abbreviations — supplement missing short forms
    _KNOWN_ABBREVS = {
        "dpci":  "directorate for priority crime investigation",
        "saps":  "south african police service",
        "npa":   "national prosecuting authority",
        "siu":   "special investigating unit",
        "ssa":   "state security agency",
        "anc":   "african national congress",
        "eff":   "economic freedom fighters",
        "da":    "democratic alliance",
        "sarb":  "south african reserve bank",
        "sars":  "south african revenue service",
        "agsa":  "auditor general south africa",
    }
    for abbr, full in _KNOWN_ABBREVS.items():
        if full in index and abbr not in index:
            index[abbr] = index[full]

    return index


def _strip_title_prefix(text: str) -> str:
    """
    Strip common SA professional title prefixes so 'Advocate Simelane'
    resolves to 'Simelane' and matches the existing actor record.
    """
    low = text.lower()
    for prefix in _TITLE_PREFIXES:
        if low.startswith(prefix):
            stripped = text[len(prefix):].strip()
            if stripped:   # don't reduce to empty string
                return stripped
    return text


def _match_actor(
    span_text: str,
    actor_index: Dict[str, int],
) -> Optional[int]:
    """
    Try to resolve a spaCy entity/span to a known actor_id.

    Strategy (in order):
    1. Try with title prefix stripped first (Advocate X → X)
    2. Exact normalized match of original span
    3. Known actor name is a substring of the span (or vice versa),
       minimum _MIN_MATCH_LEN chars, to avoid 'NPA' inside 'snap'
    """
    # Try prefix-stripped form first
    stripped = _strip_title_prefix(span_text)
    candidates = [stripped, span_text] if stripped != span_text else [span_text]

    for candidate in candidates:
        norm = _normalize(candidate)
        if not norm:
            continue

        # 1. Exact
        if norm in actor_index:
            return actor_index[norm]

        # 2. Substring (both directions)
        for actor_norm, actor_id in actor_index.items():
            if len(actor_norm) < _MIN_MATCH_LEN:
                continue
            if actor_norm in norm or norm in actor_norm:
                return actor_id

    return None


# ---------------------------------------------------------------------------
# Triple extraction logic
# ---------------------------------------------------------------------------

def _extract_triples_from_doc(
    doc,                           # spaCy Doc
    actor_index: Dict[str, int],
    signal_id:   str,
    confidence:  float,
) -> List[Tuple[int, int, str, float, str]]:
    """
    Extract (subject_actor_id, object_actor_id, relation_type,
             confidence, signal_id) from a spaCy Doc.

    Handles:
    - Active:  nsubj  -> ROOT verb -> dobj/pobj
    - Passive: nsubjpass -> ROOT verb <- agent (by-phrase)

    Returns deduplicated list of tuples.
    """
    results: List[Tuple[int, int, str, float, str]] = []
    seen: set = set()

    for sent in doc.sents:
        # Find the root verb of this sentence
        roots = [t for t in sent if t.dep_ == "ROOT" and t.pos_ == "VERB"]
        if not roots:
            continue
        root = roots[0]

        rel_type = VERB_TO_RELATION.get(root.lemma_.lower())
        if rel_type is None:
            continue

        # --- Active voice: nsubj --> ROOT --> dobj/prep ---
        subjects = [t for t in root.lefts  if t.dep_ in ("nsubj",)]
        objects  = [t for t in root.rights if t.dep_ in ("dobj", "pobj", "attr")]

        # --- Passive voice: nsubjpass + agent (by X) ---
        nsubjpass = [t for t in root.lefts  if t.dep_ == "nsubjpass"]
        agents    = [
            child
            for t in root.rights if t.dep_ == "agent"
            for child in t.subtree if child.dep_ == "pobj"
        ]

        # Build candidate (subject_span, object_span) pairs
        pairs: List[Tuple, Tuple] = []

        # Active pairs: each subject × each object
        for s in subjects:
            s_span = doc[s.left_edge.i : s.right_edge.i + 1]
            for o in objects:
                o_span = doc[o.left_edge.i : o.right_edge.i + 1]
                pairs.append((s_span, o_span))

        # Passive pairs: agent is the real subject, nsubjpass is the object
        for o in nsubjpass:
            o_span = doc[o.left_edge.i : o.right_edge.i + 1]
            for s in agents:
                s_span = doc[s.left_edge.i : s.right_edge.i + 1]
                pairs.append((s_span, o_span))

        # Also try matching NER entities in the sentence against the verb
        # This catches "DPCI and Hawks arrested the Gupta associate"
        # where subjects/objects may not be perfectly parsed
        sent_ents = [e for e in sent.ents if e.label_ in ("PERSON", "ORG", "GPE")]
        if len(sent_ents) >= 2 and (subjects or agents):
            # Treat first ent that matches an actor as subject,
            # remaining matched ents as objects
            matched_ents = [
                (e, _match_actor(e.text, actor_index)) for e in sent_ents
                if _normalize(e.text) not in _GENERIC_SUBJECTS
            ]
            matched_ents = [(e, aid) for e, aid in matched_ents if aid is not None]
            if len(matched_ents) >= 2:
                s_aid = matched_ents[0][1]
                for _, o_aid in matched_ents[1:]:
                    if s_aid != o_aid:
                        key = (s_aid, o_aid, rel_type)
                        if key not in seen:
                            seen.add(key)
                            results.append((s_aid, o_aid, rel_type,
                                            round(confidence, 4), signal_id))

        # Resolve each (subject_span, object_span) pair to actor_ids
        for s_span, o_span in pairs:
            # Reject generic nouns as subjects — they carry no forensic identity
            if _normalize(s_span.text) in _GENERIC_SUBJECTS:
                continue
            s_aid = _match_actor(s_span.text, actor_index)
            o_aid = _match_actor(o_span.text, actor_index)
            if s_aid is None or o_aid is None or s_aid == o_aid:
                continue
            key = (s_aid, o_aid, rel_type)
            if key not in seen:
                seen.add(key)
                results.append((s_aid, o_aid, rel_type,
                                round(confidence, 4), signal_id))

    return results


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------

class TripleExtractor:
    """
    Class-based wrapper compatible with mega_ingest.py _run_engine() pattern.

    Usage:
        TripleExtractor(db_path=DB_PATH).run()
        TripleExtractor(db_path=DB_PATH).run(limit=500, dry_run=True)
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def run(
        self,
        limit:    int  = 2000,
        dry_run:  bool = False,
        since:    Optional[str] = None,
        pdf_only: bool = False,
    ) -> dict:
        return _run_extraction(
            db_path=self.db_path,
            limit=limit,
            dry_run=dry_run,
            since=since,
            pdf_only=pdf_only,
        )


def _run_extraction(
    db_path:  Path = DB_PATH,
    limit:    int  = 2000,
    dry_run:  bool = False,
    since:    Optional[str] = None,
    pdf_only: bool = False,
) -> dict:
    """
    Full extraction pipeline:
      1. Load spaCy model
      2. Load actor index
      3. Select candidate signals
      4. Extract triples per signal
      5. Write to entity_relationships (INSERT OR IGNORE)
      6. Optionally refresh graph_engine metrics
    """

    # -- 1. Load spaCy -------------------------------------------------------
    try:
        import spacy
    except ImportError:
        log("ERROR: spaCy not installed -- pip install spacy")
        return {"status": "error", "error": "spacy_missing"}

    model_name = "en_core_web_sm"
    try:
        nlp = spacy.load(model_name, disable=["senter"])
        log(f"spaCy model '{model_name}' loaded | pipes: {nlp.pipe_names}")
    except OSError:
        log(f"ERROR: model '{model_name}' not found -- run: python -m spacy download {model_name}")
        return {"status": "error", "error": f"model_{model_name}_missing"}

    # Phase 44: inject SA EntityRuler before statistical NER so government
    # abbreviations (SIU, NPA, Hawks, DPCI, Treasury…) are correctly tagged ORG.
    # Ensure FORGE root is on sys.path so the import works whether this module
    # is invoked as a script directly or imported via mega_ingest.
    _forge_root = str(BASE_DIR)
    if _forge_root not in sys.path:
        sys.path.insert(0, _forge_root)
    try:
        from forage.processors.sa_entity_ruler import build_sa_ruler
        nlp = build_sa_ruler(nlp)
        log("SA EntityRuler loaded (SA government entities prioritised)")
    except Exception as exc:
        log(f"WARN: SA EntityRuler failed to load (non-fatal): {exc}")

    # -- 2. Open DB + build actor index ---------------------------------------
    if not db_path.exists():
        log(f"ERROR: database not found at {db_path}")
        return {"status": "error", "error": "db_missing"}

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")

    actor_index = _build_actor_index(conn)
    log(f"Actor index: {len(actor_index)} entries")

    # -- 3. Select candidate signals ------------------------------------------
    # Primary:   relevance_score > 1.0 (investigative + pdf_infiltrator=1.8)
    # Secondary: gravity_score   > 0.2 (conclave-scored)
    # Phase 44:  LEFT JOIN artifacts to get raw_text_cache for PDF signals.
    since_clause = ""
    since_params: list = []
    if since:
        since_clause = "AND s.timestamp >= ?"
        since_params = [since]

    pdf_only_clause = ""
    if pdf_only:
        pdf_only_clause = "AND s.source = 'pdf_infiltrator'"

    try:
        signals = conn.execute(f"""
            SELECT s.signal_id, s.title, s.content,
                   s.relevance_score, s.gravity_score, s.source,
                   s.source_artifact_id,
                   a.artifact_id, a.raw_text_cache
            FROM   signals s
            LEFT JOIN artifacts a ON s.source_artifact_id = a.artifact_id
            WHERE  (s.relevance_score > 1.0 OR s.gravity_score > 0.2)
              AND  s.source NOT IN ('firms','usgs','GDACS','earthquake')
              AND  (
                       (s.content IS NOT NULL AND length(trim(s.content)) > 50)
                    OR (a.raw_text_cache IS NOT NULL AND length(a.raw_text_cache) > 50)
                   )
              {since_clause}
              {pdf_only_clause}
            ORDER  BY s.relevance_score DESC, s.gravity_score DESC
            LIMIT  ?
        """, since_params + [limit]).fetchall()
    except Exception as exc:
        conn.close()
        log(f"ERROR: signal query failed: {exc}")
        return {"status": "error", "error": str(exc)}

    log(f"Candidate signals: {len(signals)} (limit={limit})")

    # -- 4. Extract triples ---------------------------------------------------
    total_triples              = 0
    total_written              = 0
    total_skipped              = 0
    total_provenance_backfilled = 0
    signals_hit                = 0
    relation_counts: Dict[str, int] = {}

    for sig in signals:
        # Phase 44: for any artifact-linked signal, prefer raw_text_cache
        # (real extracted prose) over the structured summary stored in content.
        # Originally restricted to pdf_infiltrator; extended to all artifact-
        # backed signals so that seed NPA PDFs and A-tier government docs also
        # use their full extracted text (not just the description snippet).
        artifact_id = sig["artifact_id"]   # may be None for non-artifact signals
        if sig["raw_text_cache"]:
            raw_text = _clean_text(sig["raw_text_cache"])
            log(f"  [PDF] using raw_text_cache ({len(raw_text)} chars) for {sig['signal_id'][:8]}")
        else:
            raw_text = _clean_text(
                f"{sig['title'] or ''} . {sig['content'] or ''}"
            )

        if not raw_text or len(raw_text) < 20:
            continue

        # Cap text length for spaCy (avoid OOM on huge content)
        raw_text = raw_text[:4000]

        confidence = min(1.0, max(
            float(sig["relevance_score"] or 0) / 2.0,
            float(sig["gravity_score"]   or 0) / 0.7,
        ))
        confidence = max(0.1, round(confidence, 4))

        try:
            doc = nlp(raw_text)
        except Exception as exc:
            log(f"  WARN spaCy parse error on {sig['signal_id'][:8]}: {exc}")
            continue

        triples = _extract_triples_from_doc(
            doc, actor_index, sig["signal_id"], confidence
        )
        total_triples += len(triples)

        if triples:
            signals_hit += 1

        if dry_run:
            for t in triples:
                log(f"  [DRY] {t[2]}: actor#{t[0]} -> actor#{t[1]}  conf={t[3]}")
            continue

        # -- 5. Write triples ------------------------------------------------
        for subj_id, obj_id, rel_type, conf, sig_id in triples:
            relation_counts[rel_type] = relation_counts.get(rel_type, 0) + 1
            try:
                # Phase 44: source_artifact_id provides full provenance chain
                # relationship -> artifact -> physical PDF file
                cur = conn.execute("""
                    INSERT OR IGNORE INTO entity_relationships
                        (subject_actor_id, object_actor_id, relation_type,
                         confidence, extraction_method,
                         source_artifact_id)
                    VALUES (?, ?, ?, ?, 'spacy', ?)
                """, (subj_id, obj_id, rel_type, conf, artifact_id))

                if cur.rowcount > 0:
                    # New record inserted successfully
                    total_written += 1
                elif artifact_id is not None:
                    # Record already existed (INSERT OR IGNORE) but may have
                    # been written before Phase 44 with NULL source_artifact_id.
                    # Backfill the provenance link without disturbing other fields.
                    upd = conn.execute("""
                        UPDATE entity_relationships
                        SET    source_artifact_id = ?
                        WHERE  subject_actor_id   = ?
                          AND  object_actor_id    = ?
                          AND  relation_type      = ?
                          AND  source_artifact_id IS NULL
                    """, (artifact_id, subj_id, obj_id, rel_type))
                    if upd.rowcount > 0:
                        total_provenance_backfilled += 1
                    else:
                        total_skipped += 1
                else:
                    total_skipped += 1

            except sqlite3.IntegrityError:
                total_skipped += 1
            except Exception as exc:
                log(f"  WARN insert error: {exc}")
                total_skipped += 1

    if not dry_run:
        conn.commit()

    total_er = conn.execute(
        "SELECT COUNT(*) FROM entity_relationships"
    ).fetchone()[0]
    conn.close()

    summary = {
        "status":                    "dry_run" if dry_run else "done",
        "signals_scanned":           len(signals),
        "signals_with_hits":         signals_hit,
        "triples_extracted":         total_triples,
        "written":                   total_written,
        "provenance_backfilled":     total_provenance_backfilled,
        "skipped_existing":          total_skipped,
        "entity_relationships_total": total_er,
        "by_relation":               relation_counts,
        "computed_at":               datetime.now(timezone.utc).isoformat(),
    }
    log(f"Complete: {summary}")

    # -- 6. Auto-refresh graph metrics if new edges were written -------------
    if not dry_run and total_written > 0:
        log("Refreshing graph metrics (Factor 2 updated)...")
        try:
            import importlib
            ge_mod = importlib.import_module("forage.engines.graph_engine")
            ge_mod.GraphEngine(db_path=db_path).run()
            log("Graph metrics refreshed")
        except Exception as exc:
            log(f"WARN: graph refresh failed (non-fatal): {exc}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FORGE Triple Extractor -- NLP relationship extraction"
    )
    parser.add_argument("--db",      type=Path, default=None,
                        help="Path to database.db")
    parser.add_argument("--limit",   type=int,  default=2000,
                        help="Max signals to process (default: 2000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract and log triples without writing")
    parser.add_argument("--since",    type=str,  default=None,
                        help="Only process signals after this ISO datetime")
    parser.add_argument("--pdf-only", action="store_true",
                        help="Only process pdf_infiltrator signals (Phase 44)")
    args = parser.parse_args()

    db = args.db.resolve() if args.db else DB_PATH
    result = _run_extraction(
        db_path=db,
        limit=args.limit,
        dry_run=args.dry_run,
        since=args.since,
        pdf_only=args.pdf_only,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("status") in ("done", "dry_run") else 1)


# -- Mega runner adapter ------------------------------------------------------
def run_all():
    print(f"[{__name__}] Executing run_all...")
    _run_extraction()
