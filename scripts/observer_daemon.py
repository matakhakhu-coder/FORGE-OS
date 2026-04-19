#!/usr/bin/env python3
"""
FORGE Observer Daemon — Phase 65: The Observer's Vigil
=======================================================
Autonomous entity-promotion engine. Continuously scans signal_entities for
NER-extracted text that meets structural significance thresholds, then promotes
qualifying entities into the actors table and establishes signal_actor links.

Architectural Design Principles
--------------------------------
1. QUALITY GATE FIRST — every candidate passes a tri-gate filter before any
   DB write occurs. Prevents NER noise (HTML artefacts, acronyms, encoding
   garbage) from polluting the actor graph.

2. CANONICAL DEDUPLICATION — before creating a new actor row, an existence
   check against normalised actor names prevents "South Africa" / "South Africa'"
   / "South Africa " from spawning separate actor rows.

3. CASCADE-SAFE BATCHING — signal_actor links are committed in batches of ≤200.
   This prevents a single 17,000-row transaction from locking the WAL file while
   the main ingest pipeline is writing, without sacrificing FK=ON atomicity
   at the batch level.

4. PERMANENT PROMOTION LOG — observer_promotion_log records every decision.
   'promoted' and 'rejected' are terminal; 'cooldown' is time-bounded.
   This reduces each scan cycle to O(new entities only).

5. SENTINEL ALERT ROUTING — immediately after promotion, co-occurrence is
   checked against HIGH_GRAVITY_INSTITUTIONS. Any match routes a priority
   alert to sentinel_alerts with alert_type='observer_cooccurrence'.

Thresholds (corrected from Sprint Plan)
-----------------------------------------
  Frequency : entity appears in signals with >= CLUSTER_DIVERSITY_MIN distinct
              cluster_id values  [Sprint Plan: "frequency > 3 clusters"]
  Gravity   : MAX(gravity_score) >= GRAVITY_FLOOR  OR
              MAX(relevance_score) > RELEVANCE_FLOOR
  Note      : gravity_score is clamped to 1.0 by the gravity engine.
              "gravity > 1.2" in the Sprint Plan is unreachable; this daemon
              uses GRAVITY_FLOOR=0.65 (FLAG MONITOR adjacent) instead.

Usage
------
  python scripts/observer_daemon.py            # run continuously (10-min loop)
  python scripts/observer_daemon.py --once     # single cycle then exit
  python scripts/observer_daemon.py --dry-run  # scan + report, no DB writes
"""

import sys, re, time, logging, argparse, sqlite3
sys.path.insert(0, r'C:\Users\matam\Projects\FORGE')

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from core.db.connection import get_connection

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = Path(r'C:\Users\matam\Projects\FORGE\logs')
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-5s %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / 'observer_daemon.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('forge.observer')


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

CYCLE_INTERVAL_SEC   = 600        # 10-minute patrol cadence
CLUSTER_DIVERSITY_MIN = 3         # distinct cluster_id values (cross-cluster presence)
GRAVITY_FLOOR        = 0.65       # max gravity_score across linked signals
RELEVANCE_FLOOR      = 1.5        # max relevance_score across linked signals
MAX_CANDIDATES_PER_CYCLE = 50     # cap to prevent runaway cycles
LINK_BATCH_SIZE      = 200        # max signal_actor inserts per commit (WAL-safe)
COOLDOWN_HOURS       = 24         # hours before re-evaluating a 'cooldown' entity

# High-gravity enforcement institutions — any co-occurrence triggers a sentinel alert
HIGH_GRAVITY_INSTITUTIONS = {
    39:  'Directorate for Priority Crime Investigation (Hawks/DPCI)',
    40:  'South African Police Service',
    24:  'National Prosecuting Authority',
    60:  'NPA',
    51:  'National Treasury',
    177: 'NATIONAL PROSECUTING AUTHORITY - npa.gov.za',
}

# Actor type mapping by NER label
LABEL_TO_TYPE = {
    'PERSON': 'person',
    'ORG':    'institution',
    'GPE':    'location',
}


# ══════════════════════════════════════════════════════════════════════════════
# Quality Gate — Tri-filter entity text validation
# ══════════════════════════════════════════════════════════════════════════════

# NER noise: generic terms, locations, common words, encoding artefacts
_NER_NOISE = {
    # Generic categories (ACTOR_PATTERNS artefacts)
    'government', 'company', 'location', 'npa', 'cia', 'fbi',
    # SA geographic noise
    'south africa', 'southern africa', 'zimbabwe', 'namibia', 'botswana',
    'china', 'uk', 'us', 'usa', 'eu', 'qatar', 'doha', 'india',
    'mpumalanga', 'gauteng', 'pretoria', 'cape town', 'johannesburg',
    'limpopo', 'eswatini', 'mozambique', 'lesotho', 'middelburg',
    # Common RSS/NER noise
    'parliament', 'president', 'minister', 'department', 'municipality',
    'government news agency', 'news agency', 'south african government',
    'report', 'bill', 'act', 'commission', 'committee',
    # Source / publication names that show up as ORG
    'groundup', 'iol', 'timeslive', 'news24', 'dailymaverick',
    'mybroadband', 'oxpeckers', 'mongabay', 'bloomberg', 'reuters',
    # Abbreviation noise
    'sa', 'ge', 'fmd', 'cbm', 'lng', 'alpr', 'cctv',
    # Artefacts
    'untitled', 'broken', 'household', 'coal', 'energy', 'park',
}

# Patterns that indicate HTML/encoding artefacts
_NOISE_RE = re.compile(
    r'&[a-zA-Z]+;'      # HTML entities: &nbsp; &amp; etc.
    r'|\\u[0-9a-fA-F]{4}'  # Unicode escapes
    r'|\xa0'            # non-breaking space byte
    r'|\u00a0'          # non-breaking space char
    r'|\ufffd'          # replacement character
    r'|&nbsp'           # partial HTML entity
    r'|prefe'           # known artefact
    r'|\u2019'          # curly apostrophe (encoding artefact in some extractions)
)


# Phase 68 — Acronym Whitelist
# Legitimate short-form actors that must NOT be blocked by the 3-char gate.
# The gate was designed to eliminate MW/FRP noise; these are real institutional
# actors with canonical long-form entries — but if a new signal introduces the
# acronym form, the Observer must be able to route it to the canonical actor.
KNOWN_ACRONYMS: set[str] = {
    'NPA',   # National Prosecuting Authority
    'SIU',   # Special Investigating Unit
    'FBI',   # Federal Bureau of Investigation
    'CIA',   # Central Intelligence Agency
    'DPCI',  # Directorate for Priority Crime Investigation (Hawks)
    'SAPS',  # South African Police Service
    'EFF',   # Economic Freedom Fighters
    'ANC',   # African National Congress (now merged, but guard future aliases)
    'IEC',   # Electoral Commission of SA
    'SARB',  # South African Reserve Bank (4 chars, but include for safety)
    'IPID',  # Independent Police Investigative Directorate
    'NHI',   # National Health Insurance
    'GBV',   # Gender-Based Violence
}


def _quality_gate(text: str, label: str) -> tuple[bool, str]:
    """
    Returns (passes: bool, rejection_reason: str).

    Gate 1 — Length: must be 4–120 chars after stripping.
              Exception: KNOWN_ACRONYMS bypass the length floor.
    Gate 2 — HTML/encoding artefact: regex match = reject.
    Gate 3 — Noise set: normalised lowercase must not be in _NER_NOISE.
    Gate 4 — Acronym filter: all-caps with len <= 3 = reject (MW, FRP, SA…)
              Exception: KNOWN_ACRONYMS bypass this gate.
    Gate 5 — Digit-heavy filter: >60% digits = reject (coordinates, codes).
    Gate 6 — Webpage title / SEO artefact.
    Gate 7 — Media outlet noise.
    """
    stripped = text.strip()

    # Acronym whitelist pre-check: bypass length + acronym gates for known actors
    letters_only_pre = re.sub(r'\W', '', stripped)
    is_known_acronym = letters_only_pre.upper() in KNOWN_ACRONYMS

    # Gate 1 — length (whitelisted acronyms skip the floor)
    if not is_known_acronym and len(stripped) < 4:
        return False, f'too_short ({len(stripped)} chars)'
    if len(stripped) > 120:
        return False, f'too_long ({len(stripped)} chars)'

    # Gate 2 — artefact
    if _NOISE_RE.search(stripped):
        return False, 'html_artefact'
    # Also check for literal &nbsp; that might survive as text
    if '&nbsp' in stripped or '\xa0' in stripped:
        return False, 'html_artefact'

    # Gate 3 — noise set
    normalised = stripped.lower().strip("'\".,;: ")
    if normalised in _NER_NOISE:
        return False, 'noise_set'
    # Also check without trailing 's or possessives
    base = re.sub(r"'s?$", '', normalised).strip()
    if base in _NER_NOISE:
        return False, 'noise_set_possessive'

    # Gate 4 — acronym filter (all caps, ≤ 3 chars); whitelisted acronyms skip
    letters_only = re.sub(r'\W', '', stripped)
    if not is_known_acronym and letters_only.isupper() and len(letters_only) <= 3:
        return False, f'short_acronym ({stripped})'

    # Gate 5 — digit-heavy
    digits = sum(1 for c in stripped if c.isdigit())
    if len(stripped) > 0 and digits / len(stripped) > 0.6:
        return False, 'digit_heavy'

    # Gate 6 — web-page title / SEO artefact
    # Pattern: "Keyword - Domain - site.gov.za" or "Title - Source - url.co.za"
    if re.search(r'\s-\s.*\.(gov|co|org|com|net)\.(za|uk|us)$', stripped, re.I):
        return False, 'webpage_title_artefact'
    if re.search(r'\.(gov|co|org|com|net)\.(za|uk)$', stripped, re.I):
        return False, 'webpage_title_artefact'

    # Gate 7 — news publication noise (media outlets, not intelligence subjects)
    _MEDIA_NOISE = {
        'moneyweb', 'news24', 'timeslive', 'dailymaverick', 'daily maverick',
        'the mail & guardian', 'mail & guardian', 'cape times', 'cape time',
        'businesstech', 'daily investor', 'green building africa',
        'bizcommunity', 'fin24', 'sowetan', 'the citizen', 'sunday times',
        'mybroadband', 'groundup', 'iol', 'eyewitness news', 'ewn',
        'south african government news agency', 'sanews',
    }
    if normalised in _MEDIA_NOISE or base in _MEDIA_NOISE:
        return False, 'media_outlet_noise'

    return True, ''


# ══════════════════════════════════════════════════════════════════════════════
# Schema bootstrap
# ══════════════════════════════════════════════════════════════════════════════

_SCHEMA_PROMOTION_LOG = """
CREATE TABLE IF NOT EXISTS observer_promotion_log (
    log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_text       TEXT    NOT NULL,
    normalised_text   TEXT    NOT NULL,
    label             TEXT    NOT NULL,
    status            TEXT    NOT NULL CHECK(status IN ('promoted','rejected','cooldown')),
    actor_id          INTEGER REFERENCES actors(actor_id) ON DELETE SET NULL,
    signal_count      INTEGER DEFAULT 0,
    cluster_diversity INTEGER DEFAULT 0,
    max_gravity       REAL    DEFAULT 0,
    max_relevance     REAL    DEFAULT 0,
    rejection_reason  TEXT,
    evaluated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    cooldown_until    TEXT,
    cycle_id          TEXT,
    UNIQUE(normalised_text, label)
)
"""

_SCHEMA_PROMOTION_IDX = """
CREATE INDEX IF NOT EXISTS idx_opl_status
    ON observer_promotion_log(status, cooldown_until)
"""

_SCHEMA_ALERT_COL = """
-- sentinel_alerts already exists; we add actor_id column if absent
"""


def _bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Create observer_promotion_log and add observer columns to sentinel_alerts."""
    conn.execute(_SCHEMA_PROMOTION_LOG)
    conn.execute(_SCHEMA_PROMOTION_IDX)

    # Add actor_id column to sentinel_alerts if missing (schema migration)
    existing = {r['name'] for r in conn.execute('PRAGMA table_info(sentinel_alerts)')}
    if 'actor_id' not in existing:
        conn.execute("ALTER TABLE sentinel_alerts ADD COLUMN actor_id INTEGER REFERENCES actors(actor_id) ON DELETE SET NULL")
    if 'related_actor_id' not in existing:
        conn.execute("ALTER TABLE sentinel_alerts ADD COLUMN related_actor_id INTEGER REFERENCES actors(actor_id) ON DELETE SET NULL")

    conn.commit()
    log.info('[bootstrap] Schema ready (observer_promotion_log + sentinel_alerts cols)')


# ══════════════════════════════════════════════════════════════════════════════
# Candidate Scanner
# ══════════════════════════════════════════════════════════════════════════════

_SCAN_SQL = """
SELECT
    se.text                                            AS entity_text,
    se.label                                           AS label,
    COUNT(DISTINCT se.signal_id)                       AS signal_count,
    COUNT(DISTINCT s.cluster_id)                       AS cluster_diversity,
    ROUND(MAX(COALESCE(s.gravity_score,   0)), 4)      AS max_gravity,
    ROUND(MAX(COALESCE(s.relevance_score, 0)), 4)      AS max_relevance
FROM signal_entities se
JOIN signals s ON s.signal_id = se.signal_id
WHERE se.label IN ('PERSON', 'ORG', 'GPE')
  -- Exclude permanently decided entities
  AND NOT EXISTS (
      SELECT 1 FROM observer_promotion_log opl
      WHERE opl.normalised_text = lower(trim(se.text))
        AND opl.label           = se.label
        AND opl.status IN ('promoted', 'rejected')
  )
  -- Exclude entities currently in cooldown
  AND NOT EXISTS (
      SELECT 1 FROM observer_promotion_log opl
      WHERE opl.normalised_text = lower(trim(se.text))
        AND opl.label           = se.label
        AND opl.status          = 'cooldown'
        AND opl.cooldown_until  > datetime('now')
  )
GROUP BY lower(trim(se.text)), se.label
HAVING (
    cluster_diversity >= :cluster_min
    OR max_gravity    >= :gravity_floor
    OR max_relevance  > :relevance_floor
)
ORDER BY cluster_diversity DESC, max_gravity DESC
LIMIT :limit
"""


def scan_candidates(conn: sqlite3.Connection, cycle_id: str) -> list[dict]:
    """Return list of entity candidate dicts that pass structural thresholds."""
    rows = conn.execute(_SCAN_SQL, {
        'cluster_min':    CLUSTER_DIVERSITY_MIN,
        'gravity_floor':  GRAVITY_FLOOR,
        'relevance_floor': RELEVANCE_FLOOR,
        'limit':          MAX_CANDIDATES_PER_CYCLE,
    }).fetchall()

    candidates = []
    filtered   = 0
    for row in rows:
        text  = (row['entity_text'] or '').strip()
        label = row['label']
        passes, reason = _quality_gate(text, label)
        if not passes:
            filtered += 1
            _log_rejection(conn, text, label, reason, row, cycle_id)
            continue
        candidates.append({
            'text':             text,
            'normalised':       text.lower().strip("'\".,;: "),
            'label':            label,
            'signal_count':     row['signal_count'],
            'cluster_diversity': row['cluster_diversity'],
            'max_gravity':      row['max_gravity'],
            'max_relevance':    row['max_relevance'],
        })

    log.info(f'[scan] {len(rows)} candidates above threshold | {filtered} rejected by quality gate | {len(candidates)} passing')
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# Deduplication Check
# ══════════════════════════════════════════════════════════════════════════════

def _find_existing_actor(conn: sqlite3.Connection, normalised: str) -> Optional[int]:
    """
    Check if an actor with a semantically equivalent name already exists.

    Match order (most to least specific):
      1. Exact case-insensitive match on normalised text or its possessive-stripped base.
      2. Known-acronym expansion: if the candidate is a KNOWN_ACRONYM, resolve it
         to the canonical full-name actor (e.g. 'NPA' -> 'National Prosecuting Authority').
      3. Fuzzy containment: if the candidate text is a strict substring of an existing
         actor name, or vice-versa, and the overlap ratio exceeds 0.8, return the
         higher-signal (higher degree) existing actor.

    Returns actor_id or None.
    """
    base = re.sub(r"'s?$", '', normalised).strip()

    # ── 1. Exact match ────────────────────────────────────────────────────────
    row = conn.execute(
        "SELECT actor_id FROM actors WHERE lower(trim(name)) = ? OR lower(trim(name)) = ?",
        (normalised, base)
    ).fetchone()
    if row:
        return row['actor_id']

    # ── 2. Known-acronym expansion ────────────────────────────────────────────
    # Map acronym -> canonical actor name fragment; find by substring match.
    _ACRONYM_EXPANSIONS: dict[str, str] = {
        'npa':  'national prosecuting authority',
        'siu':  'special investigating unit',
        'dpci': 'directorate for priority crime',
        'saps': 'south african police',
        'eff':  'economic freedom fighters',
        'anc':  'african national congress',
        'ipid': 'independent police investigative',
        'sarb': 'south african reserve bank',
    }
    expansion = _ACRONYM_EXPANSIONS.get(base.lower())
    if expansion:
        row = conn.execute(
            "SELECT actor_id FROM actors WHERE lower(name) LIKE ? ORDER BY actor_id LIMIT 1",
            (f'%{expansion}%',)
        ).fetchone()
        if row:
            return row['actor_id']

    # ── 3. Fuzzy substring containment ───────────────────────────────────────
    # Candidate must be >= 5 chars to avoid false positives.
    if len(base) >= 5:
        # Check if candidate is a substring of any existing actor name
        row = conn.execute(
            "SELECT actor_id, name FROM actors WHERE lower(name) LIKE ? ORDER BY actor_id LIMIT 1",
            (f'%{base}%',)
        ).fetchone()
        if row:
            existing_lower = row['name'].lower()
            # Overlap ratio: shorter / longer
            ratio = len(base) / max(len(existing_lower), 1)
            if ratio >= 0.75:   # candidate covers >=75% of existing name
                return row['actor_id']

        # Check if any existing actor name is a substring of the candidate
        # (e.g. "Hawk" is a substring of "Directorate for Priority Crime Investigation")
        # Only fire if existing name is at least 4 chars and is a meaningful fragment
        row2 = conn.execute(
            """SELECT actor_id, name FROM actors
               WHERE length(name) >= 4
                 AND ? LIKE '%' || lower(name) || '%'
               ORDER BY length(name) DESC LIMIT 1""",
            (base,)
        ).fetchone()
        if row2:
            existing_lower = row2['name'].lower()
            ratio = len(existing_lower) / max(len(base), 1)
            if ratio >= 0.75:
                return row2['actor_id']

    return None


# ══════════════════════════════════════════════════════════════════════════════
# Promotion Engine
# ══════════════════════════════════════════════════════════════════════════════

def promote_entity(conn: sqlite3.Connection, candidate: dict, cycle_id: str,
                   dry_run: bool = False) -> Optional[int]:
    """
    Promote a candidate entity to actors table and link to its signals.

    Returns the actor_id if promotion succeeded (or actor already existed),
    None if promotion was skipped.

    Uses FK=ON (inherited from get_connection). All writes are batched
    in LINK_BATCH_SIZE commits to prevent WAL lock.
    """
    text      = candidate['text']
    normalised = candidate['normalised']
    label     = candidate['label']
    actor_type = LABEL_TO_TYPE.get(label, 'person')

    if dry_run:
        log.info(f'  [DRY-RUN] Would promote: "{text}" ({label}) -> {actor_type}')
        return None

    # 1. Deduplication — find or create canonical actor
    existing_id = _find_existing_actor(conn, normalised)

    if existing_id:
        actor_id = existing_id
        log.info(f'  [dedup] "{text}" matches existing actor [{actor_id}] — linking only')
    else:
        # Create actor row
        conn.execute("BEGIN")
        cur = conn.execute(
            "INSERT OR IGNORE INTO actors (name, type) VALUES (?, ?)",
            (text, actor_type)
        )
        if cur.rowcount == 0:
            # INSERT OR IGNORE fired — row was created by a race; fetch it
            conn.execute("ROLLBACK")
            existing_id = _find_existing_actor(conn, normalised)
            if existing_id:
                actor_id = existing_id
            else:
                log.warning(f'  [promote] Could not resolve actor_id for "{text}" after INSERT OR IGNORE')
                return None
        else:
            actor_id = cur.lastrowid
            conn.execute("COMMIT")
            log.info(f'  [promote] Created actor [{actor_id}] "{text}" ({actor_type})')

    # 2. Fetch signal_ids for this entity (ordered by gravity DESC)
    sig_rows = conn.execute("""
        SELECT DISTINCT se.signal_id
        FROM signal_entities se
        JOIN signals s ON s.signal_id = se.signal_id
        WHERE lower(trim(se.text)) = ? AND se.label = ?
        ORDER BY s.gravity_score DESC
    """, (normalised, label)).fetchall()
    signal_ids = [r['signal_id'] for r in sig_rows]

    # 3. Batch-insert signal_actor links (LINK_BATCH_SIZE per commit)
    new_links = 0
    for batch_start in range(0, len(signal_ids), LINK_BATCH_SIZE):
        batch = signal_ids[batch_start: batch_start + LINK_BATCH_SIZE]
        conn.execute("BEGIN")
        for sid in batch:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO signal_actors (signal_id, actor_id, role) VALUES (?, ?, 'observer')",
                    (sid, actor_id)
                )
                new_links += conn.execute("SELECT changes()").fetchone()[0]
            except sqlite3.IntegrityError as e:
                # FK violation — signal_id no longer exists; skip silently
                log.debug(f'  [link] FK reject sid={sid} actor={actor_id}: {e}')
        conn.execute("COMMIT")

    log.info(f'  [link] [{actor_id}] "{text}" -> {new_links} new signal_actor links ({len(signal_ids)} total signals)')

    # 4. Write promotion log
    _log_promotion(conn, candidate, actor_id, cycle_id)

    return actor_id


# ══════════════════════════════════════════════════════════════════════════════
# Sentinel Alert — Co-occurrence with High-Gravity Institutions
# ══════════════════════════════════════════════════════════════════════════════

def check_cooccurrence_alert(conn: sqlite3.Connection, actor_id: int,
                              actor_name: str, dry_run: bool = False) -> int:
    """
    After promotion, check if the new actor co-occurs in any signal with a
    known HIGH_GRAVITY_INSTITUTION. If yes, write a sentinel_alert.

    Returns number of alerts written.
    """
    alerts_written = 0
    hgi_ids = list(HIGH_GRAVITY_INSTITUTIONS.keys())
    ph = ','.join('?' * len(hgi_ids))

    # Find co-occurring signals
    hits = conn.execute(f"""
        SELECT s.signal_id, s.title, s.gravity_score,
               sa2.actor_id AS hgi_actor_id
        FROM signals s
        JOIN signal_actors sa1 ON sa1.signal_id = s.signal_id AND sa1.actor_id = ?
        JOIN signal_actors sa2 ON sa2.signal_id = s.signal_id AND sa2.actor_id IN ({ph})
        ORDER BY s.gravity_score DESC
        LIMIT 5
    """, [actor_id] + hgi_ids).fetchall()

    for hit in hits:
        hgi_name = HIGH_GRAVITY_INSTITUTIONS.get(hit['hgi_actor_id'], 'Unknown Institution')
        summary  = (
            f"Observer auto-materialised actor '{actor_name}' (actor_id={actor_id}) "
            f"co-occurs with HIGH-GRAVITY institution '{hgi_name}' "
            f"(actor_id={hit['hgi_actor_id']}) in signal: \"{(hit['title'] or '')[:80]}\". "
            f"Signal gravity={hit['gravity_score']:.3f}. "
            f"Recommend analyst review for investigative linkage."
        )
        confidence = min(0.5 + (hit['gravity_score'] or 0) * 0.5, 1.0)

        if dry_run:
            log.info(f'  [DRY-RUN] Would write sentinel_alert: co-occurrence with {hgi_name}')
            alerts_written += 1
            continue

        conn.execute("BEGIN")
        conn.execute("""
            INSERT INTO sentinel_alerts
              (alert_type, confidence_score, signal_count, summary, status, actor_id, related_actor_id)
            VALUES ('observer_cooccurrence', ?, 1, ?, 'new', ?, ?)
        """, (confidence, summary, actor_id, hit['hgi_actor_id']))
        conn.execute("COMMIT")
        alerts_written += 1
        log.warning(f'  [ALERT] Sentinel alert: "{actor_name}" co-occurs with {hgi_name} '
                    f'(gravity={hit["gravity_score"]:.3f})')

    return alerts_written


# ══════════════════════════════════════════════════════════════════════════════
# Promotion / Rejection Log Writers
# ══════════════════════════════════════════════════════════════════════════════

def _log_promotion(conn: sqlite3.Connection, candidate: dict,
                   actor_id: int, cycle_id: str) -> None:
    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)).isoformat()
    conn.execute("""
        INSERT INTO observer_promotion_log
          (entity_text, normalised_text, label, status, actor_id,
           signal_count, cluster_diversity, max_gravity, max_relevance,
           evaluated_at, cycle_id)
        VALUES (?,?,?,'promoted',?,?,?,?,?,datetime('now'),?)
        ON CONFLICT(normalised_text, label) DO UPDATE SET
          status='promoted', actor_id=excluded.actor_id,
          signal_count=excluded.signal_count,
          cluster_diversity=excluded.cluster_diversity,
          max_gravity=excluded.max_gravity,
          evaluated_at=datetime('now'),
          cycle_id=excluded.cycle_id
    """, (
        candidate['text'], candidate['normalised'], candidate['label'],
        actor_id, candidate['signal_count'], candidate['cluster_diversity'],
        candidate['max_gravity'], candidate['max_relevance'], cycle_id
    ))
    conn.commit()


def _log_rejection(conn: sqlite3.Connection, text: str, label: str,
                   reason: str, row, cycle_id: str) -> None:
    normalised = text.lower().strip("'\".,;: ")
    cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)).isoformat()
    conn.execute("""
        INSERT INTO observer_promotion_log
          (entity_text, normalised_text, label, status,
           signal_count, cluster_diversity, max_gravity, max_relevance,
           rejection_reason, evaluated_at, cooldown_until, cycle_id)
        VALUES (?,?,?,'rejected',?,?,?,?,?,datetime('now'),?,?)
        ON CONFLICT(normalised_text, label) DO UPDATE SET
          status='rejected',
          rejection_reason=excluded.rejection_reason,
          evaluated_at=datetime('now'),
          cycle_id=excluded.cycle_id
    """, (
        text, normalised, label,
        row['signal_count'], row['cluster_diversity'],
        row['max_gravity'], row['max_relevance'],
        reason, cooldown_until, cycle_id
    ))
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Single Observer Cycle
# ══════════════════════════════════════════════════════════════════════════════

def run_cycle(dry_run: bool = False) -> dict:
    """
    Execute one full Observer cycle. Returns a stats dict.
    """
    cycle_id  = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    t_start   = time.monotonic()

    promoted  = 0
    rejected  = 0
    alerts    = 0
    deduped   = 0

    log.info(f'[cycle:{cycle_id}] Observer cycle starting')

    conn = get_connection()

    try:
        _bootstrap_schema(conn)

        # ── SCAN ──────────────────────────────────────────────────────────────
        candidates = scan_candidates(conn, cycle_id)

        if not candidates:
            log.info(f'[cycle:{cycle_id}] No new candidates above threshold this cycle')
            return {'cycle_id': cycle_id, 'promoted': 0, 'rejected': 0,
                    'alerts': 0, 'elapsed_s': round(time.monotonic() - t_start, 2)}

        log.info(f'[cycle:{cycle_id}] Processing {len(candidates)} candidates...')

        # ── PROMOTE ───────────────────────────────────────────────────────────
        for c in candidates:
            actor_id = promote_entity(conn, c, cycle_id, dry_run=dry_run)

            if actor_id is None:
                rejected += 1
                continue

            # Track whether this was a dedup (existing actor reused)
            existing_before = _find_existing_actor(conn, c['normalised'])
            if existing_before and existing_before == actor_id:
                deduped += 1

            promoted += 1

            # ── CO-OCCURRENCE ALERT ───────────────────────────────────────────
            n_alerts = check_cooccurrence_alert(conn, actor_id, c['text'], dry_run=dry_run)
            alerts  += n_alerts

    finally:
        conn.close()

    elapsed = round(time.monotonic() - t_start, 2)

    log.info(
        f'[cycle:{cycle_id}] COMPLETE | '
        f'promoted={promoted} deduped={deduped} rejected={rejected} '
        f'alerts={alerts} elapsed={elapsed}s'
    )

    return {
        'cycle_id': cycle_id,
        'promoted':  promoted,
        'deduped':   deduped,
        'rejected':  rejected,
        'alerts':    alerts,
        'elapsed_s': elapsed,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Daemon Main Loop
# ══════════════════════════════════════════════════════════════════════════════

def run_daemon(dry_run: bool = False) -> None:
    """Continuous 10-minute patrol loop. Ctrl-C to exit cleanly."""
    log.info('=' * 60)
    log.info('  FORGE Observer Daemon — Phase 65: The Observer\'s Vigil')
    log.info(f'  Interval : {CYCLE_INTERVAL_SEC}s ({CYCLE_INTERVAL_SEC//60} min)')
    log.info(f'  Thresholds: cluster_diversity>={CLUSTER_DIVERSITY_MIN} OR '
             f'gravity>={GRAVITY_FLOOR} OR relevance>{RELEVANCE_FLOOR}')
    log.info(f'  Batch size: {LINK_BATCH_SIZE} links/commit')
    log.info(f'  Dry run   : {dry_run}')
    log.info('=' * 60)

    cycle_count = 0
    total_stats = {'promoted': 0, 'deduped': 0, 'rejected': 0, 'alerts': 0}

    try:
        while True:
            cycle_count += 1
            log.info(f'\n[daemon] --- Cycle #{cycle_count} ---')
            stats = run_cycle(dry_run=dry_run)

            for k in total_stats:
                total_stats[k] += stats.get(k, 0)

            log.info(
                f'[daemon] Cumulative: promoted={total_stats["promoted"]} '
                f'deduped={total_stats["deduped"]} '
                f'rejected={total_stats["rejected"]} '
                f'alerts={total_stats["alerts"]}'
            )
            log.info(f'[daemon] Sleeping {CYCLE_INTERVAL_SEC}s until next cycle...')
            time.sleep(CYCLE_INTERVAL_SEC)

    except KeyboardInterrupt:
        log.info('\n[daemon] Shutdown signal received. Observer standing down.')
        log.info(f'[daemon] Final totals: {total_stats}')


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='FORGE Observer Daemon — Phase 65')
    parser.add_argument('--once',    action='store_true', help='Run a single cycle then exit')
    parser.add_argument('--dry-run', action='store_true', help='Scan and report only — no DB writes')
    args = parser.parse_args()

    if args.once or args.dry_run:
        stats = run_cycle(dry_run=args.dry_run)
        print()
        print('=== Observer Cycle Report ===')
        for k, v in stats.items():
            print(f'  {k:<18}: {v}')
    else:
        run_daemon(dry_run=False)
