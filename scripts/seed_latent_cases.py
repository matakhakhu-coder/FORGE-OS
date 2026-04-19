#!/usr/bin/env python3
"""
FORGE — Latent Case Seed: 5 Emergent Investigation Vectors
════════════════════════════════════════════════════════════

Sequential full-roster seed of all 5 cases approved by the Latent Case Scan.
Commits after each case to avoid SQLite write-locks.

Usage:
    python scripts/seed_latent_cases.py
    python scripts/seed_latent_cases.py --db path/to/database.db
    python scripts/seed_latent_cases.py --dry-run        # inspect without writing

Cases seeded (in priority order):
    1. OPERATION DARK BADGE   — Police command contamination / Masemola
    2. OPERATION PROMETHEUS   — Health sector systematic looting / Health DG
    3. OPERATION BLUE LIGHT   — Matlala R360m SAPS tender capture
    4. OPERATION GOLDFINGER   — SIU enforcement surge (diamonds, NLC, Mphaphuli)
    5. OPERATION IRON FIST    — Steinhoff terminal prosecution arc
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("forge.seed_latent")

# ── Paths ─────────────────────────────────────────────────────────────────────

def _resolve_db(override: Optional[str] = None) -> Path:
    if override:
        return Path(override).resolve()
    env = os.environ.get("FORGE_DB")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1] / "database.db"


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ── O(1) Entity Resolver (inline — no import dependency) ──────────────────────

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

def _normalize(text: str) -> str:
    name = text.strip().lower()
    return " ".join(p for p in _NORMALIZE_RE.sub(" ", name).split() if p)


class _Resolver:
    """
    Thin inline resolver wrapping the actors table.
    Builds two O(1) lookup dicts on init, updates them on INSERT.
    """
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._exact: dict[str, int] = {}
        self._norm:  dict[str, int] = {}
        self._refresh()

    def _refresh(self):
        self._exact.clear(); self._norm.clear()
        for row in self.conn.execute(
            "SELECT actor_id, name FROM actors WHERE name IS NOT NULL"
        ):
            actor_id = int(row["actor_id"])
            name     = row["name"] or ""
            if not name.strip():
                continue
            self._exact[name.strip().lower()] = actor_id
            n = _normalize(name)
            if n:
                self._norm[n] = actor_id

    def find(self, name: str) -> Optional[int]:
        if not name or not name.strip():
            return None
        ex = name.strip().lower()
        if ex in self._exact:
            return self._exact[ex]
        n = _normalize(name)
        if n and n in self._norm:
            return self._norm[n]
        return None

    def resolve_or_create(self, name: str, actor_type: str = "institution") -> int:
        aid = self.find(name)
        if aid is not None:
            return aid
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO actors (name, type, created_at, automated) "
            "VALUES (?, ?, datetime('now'), 1)",
            (name.strip(), actor_type),
        )
        self.conn.commit()
        aid = cur.lastrowid
        ex  = name.strip().lower()
        n   = _normalize(name)
        if ex: self._exact[ex] = aid
        if n:  self._norm[n]   = aid
        log.info(f"  [resolver] Created new actor: [{aid}] {name} ({actor_type})")
        return aid


# ── Core helpers ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _case_exists(conn: sqlite3.Connection, title: str) -> Optional[int]:
    row = conn.execute(
        "SELECT case_id FROM cases WHERE name = ?", (title,)
    ).fetchone()
    return row["case_id"] if row else None


def _create_case(
    conn: sqlite3.Connection,
    title: str,
    description: str,
    hypothesis: str,
    case_type: str = "general",
) -> int:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cases
            (name, description, status, hypothesis, case_type,
             source_type, auto_generated, created_at)
        VALUES (?, ?, 'active', ?, ?, 'seed', 0, ?)
    """, (title, description, hypothesis, case_type, _now()))
    conn.commit()
    return cur.lastrowid


def _pin_actors(
    conn: sqlite3.Connection,
    case_id: int,
    actor_roles: list[tuple[int, str]],   # (actor_id, note)
) -> int:
    inserted = 0
    for order, (actor_id, note) in enumerate(actor_roles, start=1):
        try:
            conn.execute("""
                INSERT OR IGNORE INTO case_actors
                    (case_id, actor_id, note, pinned_at, sequence_order)
                VALUES (?, ?, ?, ?, ?)
            """, (case_id, actor_id, note, _now(), order))
            inserted += 1
        except sqlite3.Error as exc:
            log.warning(f"  actor pin failed actor_id={actor_id}: {exc}")
    conn.commit()
    return inserted


def _pin_signals(
    conn: sqlite3.Connection,
    case_id: int,
    cluster_ids: list[str],
    max_signals: int = 30,
    note_prefix: str = "",
) -> int:
    """
    Pin the top-priority, highest-gravity signals from the given clusters.
    Caps at max_signals to keep the case workbench manageable.
    """
    if not cluster_ids:
        return 0
    placeholders = ",".join("?" * len(cluster_ids))
    rows = conn.execute(f"""
        SELECT signal_id, title, source, gravity_score, is_priority, relevance_score
        FROM signals
        WHERE cluster_id IN ({placeholders})
        ORDER BY is_priority DESC,
                 COALESCE(gravity_score, 0) DESC,
                 relevance_score DESC
        LIMIT ?
    """, (*cluster_ids, max_signals)).fetchall()

    inserted = 0
    for r in rows:
        note = (note_prefix + f"[{r['source']}] {(r['title'] or '')[:80]}").strip()
        try:
            conn.execute("""
                INSERT OR IGNORE INTO case_signals
                    (case_id, signal_id, note, pinned_at)
                VALUES (?, ?, ?, ?)
            """, (case_id, r["signal_id"], note, _now()))
            inserted += 1
        except sqlite3.Error as exc:
            log.warning(f"  signal pin failed {r['signal_id']}: {exc}")
    conn.commit()
    log.info(f"  Pinned {inserted} signals from {len(cluster_ids)} cluster(s)")
    return inserted


def _pin_artifacts(
    conn: sqlite3.Connection,
    case_id: int,
    artifact_ids: list[int],
    notes: dict[int, str] = None,
) -> int:
    notes = notes or {}
    inserted = 0
    for order, art_id in enumerate(artifact_ids, start=1):
        note = notes.get(art_id, "PDF evidence — forensic anchor")
        try:
            conn.execute("""
                INSERT OR IGNORE INTO case_artifacts
                    (case_id, artifact_id, note, pinned_at, sequence_order)
                VALUES (?, ?, ?, ?, ?)
            """, (case_id, art_id, note, _now(), order))
            inserted += 1
        except sqlite3.Error as exc:
            log.warning(f"  artifact pin failed art_id={art_id}: {exc}")
    conn.commit()
    log.info(f"  Pinned {inserted} artifacts")
    return inserted


def _pin_signals_by_keyword(
    conn: sqlite3.Connection,
    case_id: int,
    keywords: list[str],
    max_signals: int = 20,
    note_prefix: str = "",
) -> int:
    """Pin signals matching any keyword in title (LIKE), ranked by gravity."""
    inserted = 0
    seen: set[str] = set()
    for kw in keywords:
        rows = conn.execute("""
            SELECT signal_id, title, source, gravity_score, is_priority
            FROM signals
            WHERE title LIKE ?
            ORDER BY is_priority DESC, COALESCE(gravity_score, 0) DESC
            LIMIT ?
        """, (f"%{kw}%", max_signals // len(keywords) + 5)).fetchall()
        for r in rows:
            if r["signal_id"] in seen:
                continue
            seen.add(r["signal_id"])
            note = (note_prefix + f"[{r['source']}] keyword:{kw} — {(r['title'] or '')[:60]}").strip()
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO case_signals
                        (case_id, signal_id, note, pinned_at)
                    VALUES (?, ?, ?, ?)
                """, (case_id, r["signal_id"], note, _now()))
                inserted += 1
            except sqlite3.Error as exc:
                log.warning(f"  signal pin (kw) failed {r['signal_id']}: {exc}")
    conn.commit()
    log.info(f"  Pinned {inserted} keyword signals")
    return inserted


# ── Case definitions ──────────────────────────────────────────────────────────

def seed_dark_badge(conn: sqlite3.Connection, resolver: _Resolver, dry_run: bool) -> dict:
    """CASE 1 — OPERATION DARK BADGE: Police Command Contamination"""
    TITLE = "OPERATION DARK BADGE — Police Command Contamination"
    log.info(f"\n{'═'*60}")
    log.info(f"[DARK BADGE] Starting seed…")

    existing = _case_exists(conn, TITLE)
    if existing:
        log.info(f"[DARK BADGE] Already exists as case_id={existing}, skipping.")
        return {"case_id": existing, "status": "skipped"}

    # ── Actors ─────────────────────────────────────────────────────
    masemola_id  = resolver.resolve_or_create("Fannie Masemola",             "person")
    kganyago_id  = resolver.find("Kaizer Kganyago") or resolver.resolve_or_create("Kaizer Kganyago", "person")
    gruzd_id     = resolver.find("Steven Gruzd")    or resolver.resolve_or_create("Steven Gruzd",    "person")
    saps_id      = resolver.find("South African Police Service") or resolver.find("SAPS")
    npa_id       = resolver.find("National Prosecuting Authority")
    tshwane_id   = resolver.resolve_or_create("Tshwane Metro Police",        "institution")
    sibiya_id    = resolver.find("Sibiya")
    ramaphosa_id = resolver.find("Cyril Ramaphosa") or resolver.resolve_or_create("Cyril Ramaphosa", "person")

    actor_roles = [r for r in [
        (masemola_id,  "PRIMARY — SAPS Commissioner under suspension pressure"),
        (kganyago_id,  "CRITICAL — NPA-investigated, then hired by NPA (conflict vector)"),
        (gruzd_id,     "VICTIM — Researcher murder; highest gravity signal in cluster"),
        (tshwane_id,   "INSTITUTION — R2bn security contract scandal"),
        (saps_id,      "INSTITUTION — Command-level accountability actor"),
        (sibiya_id,    "SECONDARY — Commissioner denying impala gifts (Matlala crossover)"),
        (ramaphosa_id, "POLITICAL — Resolution pressure point re: Masemola suspension"),
        (npa_id,       "AUTHORITY — Investigating Kganyago; Kganyago now joined NPA"),
    ] if r[0] is not None]

    if dry_run:
        log.info(f"[DARK BADGE] DRY RUN — would create case with {len(actor_roles)} actors")
        return {"case_id": None, "status": "dry_run"}

    case_id = _create_case(
        conn,
        title=TITLE,
        description=(
            "Command-level contamination of the South African Police Service. "
            "SAPS Commissioner Masemola is under dual pressure: potential suspension "
            "from the executive and an internal corruption climate evidenced by "
            "the murder of researcher Steven Gruzd (mastermind sought via phone records), "
            "a fourth accused added in the Gqeberha prosecutor murder case, and the "
            "Tshwane Metro police chief embroiled in a R2bn security contract scandal. "
            "Kaizer Kganyago — under NPA investigation — has moved laterally into the NPA "
            "itself, constituting either an intelligence capture or a structural conflict of "
            "interest at the investigative authority level. NPA freezes R8.5m in assets "
            "linked to a court official's fraud case in the same cluster space."
        ),
        hypothesis=(
            "The SAPS command structure has been systematically compromised through "
            "a combination of tender capture (Tshwane R2bn), asset seizure by criminal "
            "networks, institutional infiltration (Kganyago into NPA), and a pattern of "
            "accountability-actor homicide (Gruzd, Gqeberha prosecutor). The 'smear "
            "campaign' framing around the Tshwane metro chief is a counter-intelligence "
            "move by the network, not a political dispute."
        ),
        case_type="general",
    )

    _pin_actors(conn, case_id, actor_roles)

    # Primary cluster + keyword expansion
    _pin_signals(conn, case_id, ["geo_-26.0_28.0_10275"], max_signals=25,
                 note_prefix="[DARK_BADGE][cluster_10275] ")
    _pin_signals_by_keyword(conn, case_id,
        ["Masemola", "Kganyago", "Gruzd", "Tshwane metro police", "R2bn security",
         "court official", "R8.5m", "prosecutor murder", "Gqeberha prosecutor"],
        max_signals=20, note_prefix="[DARK_BADGE][kw] ")

    log.info(f"[DARK BADGE] ✓ Seeded as case_id={case_id}")
    return {"case_id": case_id, "status": "seeded", "title": TITLE}


def seed_prometheus(conn: sqlite3.Connection, resolver: _Resolver, dry_run: bool) -> dict:
    """CASE 2 — OPERATION PROMETHEUS: Health Sector Systematic Looting"""
    TITLE = "OPERATION PROMETHEUS — Health Sector Systematic Looting"
    log.info(f"\n{'═'*60}")
    log.info(f"[PROMETHEUS] Starting seed…")

    existing = _case_exists(conn, TITLE)
    if existing:
        log.info(f"[PROMETHEUS] Already exists as case_id={existing}, skipping.")
        return {"case_id": existing, "status": "skipped"}

    # ── Actors ─────────────────────────────────────────────────────
    health_dept_id  = resolver.find("The National Department of Health") or \
                      resolver.resolve_or_create("National Department of Health", "government")
    siu_id          = resolver.find("SIU")
    hawks_id        = resolver.find("Directorate for Priority Crime Investigation") or \
                      resolver.resolve_or_create("Hawks (DPCI)", "institution")
    idt_id          = resolver.find("IDT")
    buthelezi_id    = resolver.resolve_or_create("Sandile Buthelezi",          "person")
    lukhope_m_id    = resolver.resolve_or_create("Makhonzandile Lukhope",      "person")
    lukhope_n_id    = resolver.resolve_or_create("Naledi Lukhope",             "person")
    treasury_id     = resolver.find("National Treasury")
    ramaphosa_id    = resolver.find("Cyril Ramaphosa") or resolver.resolve_or_create("Cyril Ramaphosa", "person")
    limpopo_h_id    = resolver.resolve_or_create("Limpopo Department of Health", "government")
    ec_edu_id       = resolver.resolve_or_create("Eastern Cape Department of Education", "government")
    gauteng_h_id    = resolver.resolve_or_create("Gauteng Department of Health", "government")

    actor_roles = [r for r in [
        (health_dept_id,  "PRIMARY — SIU Proclamation target; PPE contracted hub"),
        (buthelezi_id,    "KEY — Director-General arrested by Hawks for R1m fraud"),
        (siu_id,          "AUTHORITY — Presidential Proclamation R55/2022 investigator"),
        (hawks_id,        "ENFORCEMENT — Arrested Buthelezi + two officials"),
        (idt_id,          "ENABLER — IDT CEO implicated in R836m oxygen plant contracts"),
        (lukhope_m_id,    "PERPETRATOR — Father in PPE father-daughter arrest (EC Education)"),
        (lukhope_n_id,    "PERPETRATOR — Daughter; shell company 'Naledi's entity', R4.3m fraud"),
        (limpopo_h_id,    "TARGET INSTITUTION — Waste management contracts under SIU Proclamation"),
        (ec_edu_id,       "TARGET INSTITUTION — Eastern Cape Education PPE tender fraud source"),
        (gauteng_h_id,    "TARGET INSTITUTION — Zakheni R221m corruption + DG fraud cluster"),
        (treasury_id,     "OVERSIGHT — National Treasury named in SIU EC Health Proclamation"),
        (ramaphosa_id,    "SIGNATORY — Signed Proclamation R55/2022 authorising SIU investigation"),
    ] if r[0] is not None]

    if dry_run:
        log.info(f"[PROMETHEUS] DRY RUN — would create case with {len(actor_roles)} actors")
        return {"case_id": None, "status": "dry_run"}

    case_id = _create_case(
        conn,
        title=TITLE,
        description=(
            "Systematic extraction across three provincial health departments "
            "(Gauteng, Eastern Cape, Limpopo) through overlapping PPE, oxygen, and "
            "hospital infrastructure procurement channels. SIU holds Presidential "
            "Proclamations against Limpopo and EC Health. Hawks arrested Director-General "
            "Sandile Buthelezi and two Gauteng Health officials for R1m fraud (Specialised "
            "Commercial Crimes Court). Father-daughter Lukhope pair arrested for R4,365,868 "
            "PPE fraud via shell company against Eastern Cape Department of Education. "
            "Amabhungane separately documents R836m oxygen plant contract irregularities "
            "linking the Health DG and IDT CEO. R1.6bn hospital refurbishment programme "
            "under due-diligence review. Hawks seized Sandton property and vehicles in "
            "a parallel R5m PPE scandal (ex-Mpumalanga official)."
        ),
        hypothesis=(
            "The Health Department is not experiencing isolated fraud — it is operating as "
            "a multi-tier extraction network with the DG as a permissive node. The PPE "
            "pipeline (Eastern Cape), oxygen infrastructure (IDT), and waste management "
            "(Limpopo) are three separate contractor networks using the same structural "
            "vulnerability: emergency procurement and single-source contracting under "
            "COVID-era rules that were never rescinded. The DG's arrest may be the "
            "triggering event that causes network members to destroy evidence."
        ),
        case_type="general",
    )

    _pin_actors(conn, case_id, actor_roles)

    # Clusters: 10257=Hawks+SAPS enforcement, 10277=PDF convergence, 10220+10033=amabhungane
    _pin_signals(conn, case_id,
                 ["geo_-26.0_28.0_10257", "geo_-26.0_28.0_10277",
                  "geo_-26.0_28.0_10220", "geo_-26.0_28.0_10033"],
                 max_signals=30,
                 note_prefix="[PROMETHEUS][cluster] ")
    _pin_signals_by_keyword(conn, case_id,
        ["Buthelezi", "health official", "health department arrest", "PPE",
         "R836m", "R1.6bn hospital", "IDT CEO", "R221m", "Limpopo health",
         "Eastern Cape education", "Hawks health", "R5m PPE", "Sandton"],
        max_signals=25, note_prefix="[PROMETHEUS][kw] ")

    # ── PDF Forensic Anchors ────────────────────────────────────────
    # artifact_id: source pdf mapping
    PROMETHEUS_ARTIFACTS = {
        564861: "SIU Proclamation — EC Health & Midvaal (INVESTIGATES: SIU→Health Dept)",
        564862: "SIU Media Statement — Gauteng Health Zakheni (PPE CONTRACTED edge; CFO)",
        564833: "SIU Proclamation — Limpopo Health waste management (Proc. R55/2022)",
        564834: "SIU Media Statement — EC Education PPE arrest (Lukhope father-daughter)",
    }
    _pin_artifacts(conn, case_id, list(PROMETHEUS_ARTIFACTS.keys()), notes=PROMETHEUS_ARTIFACTS)

    log.info(f"[PROMETHEUS] ✓ Seeded as case_id={case_id}")
    return {"case_id": case_id, "status": "seeded", "title": TITLE}


def seed_blue_light(conn: sqlite3.Connection, resolver: _Resolver, dry_run: bool) -> dict:
    """CASE 3 — OPERATION BLUE LIGHT: Matlala SAPS Tender Capture"""
    TITLE = "OPERATION BLUE LIGHT — Matlala R360m SAPS Tender Capture"
    log.info(f"\n{'═'*60}")
    log.info(f"[BLUE LIGHT] Starting seed…")

    existing = _case_exists(conn, TITLE)
    if existing:
        log.info(f"[BLUE LIGHT] Already exists as case_id={existing}, skipping.")
        return {"case_id": existing, "status": "skipped"}

    # ── Actors ─────────────────────────────────────────────────────
    # Matlala appears as both 175 (Matlala•) and 560 (Cat Matlala•) — use both
    matlala_id  = resolver.find("Cat Matlala") or resolver.find("Matlala") or \
                  resolver.resolve_or_create("Kagiso 'Cat' Matlala", "person")
    sibiya_id   = resolver.find("Sibiya")
    nkosi_id    = resolver.find("Fannie Nkosi")
    hawks_id    = resolver.find("Directorate for Priority Crime Investigation") or \
                  resolver.resolve_or_create("Hawks (DPCI)", "institution")
    saps_id     = resolver.find("South African Police Service") or resolver.find("SAPS")
    dpci_id     = resolver.find("Directorate for Priority Crime Investigation")
    mchunu_id   = resolver.resolve_or_create("Senzo Mchunu",                "person")
    ramaphosa_id= resolver.find("Cyril Ramaphosa") or resolver.resolve_or_create("Cyril Ramaphosa", "person")
    actt_id     = resolver.find("Anti-Corruption Task Team") or \
                  resolver.resolve_or_create("Anti-Corruption Task Team",   "institution")

    actor_roles = [r for r in [
        (matlala_id,   "PRIMARY — Alleged network hub; R360m tender + gifts to 4 senior cops"),
        (sibiya_id,    "KEY — Commissioner denying impala gifts; named in 3 clusters"),
        (nkosi_id,     "KEY — Alleged tip-off conduit; found with 6 CIT case files"),
        (hawks_id,     "ENFORCEMENT — Arrested 12 cops (anti-corruption directorate)"),
        (actt_id,      "ENFORCEMENT — Anti-Corruption Task Team running prosecution"),
        (mchunu_id,    "POLITICAL LINK — Named in ex-cellmate letter connecting to Matlala"),
        (saps_id,      "INSTITUTION — Internal corruption surface; command accountability"),
        (dpci_id,      "ENFORCEMENT — Hawks running the 12-arrest wave"),
        (ramaphosa_id, "POLITICAL — Presidential protection racket signal; R3m payment probe"),
    ] if r[0] is not None]

    if dry_run:
        log.info(f"[BLUE LIGHT] DRY RUN — would create case with {len(actor_roles)} actors")
        return {"case_id": None, "status": "dry_run"}

    case_id = _create_case(
        conn,
        title=TITLE,
        description=(
            "The largest single-incident internal SAPS arrest wave in the dataset: "
            "twelve police officers ('dirty dozen') arrested for a R360m tender fraud "
            "orchestrated by Kagiso 'Cat' Matlala. The network extends upward: "
            "four senior officers admitted to receiving gifts including Ozempic injections "
            "and a surgical procedure. Commissioner Sibiya denied receiving Matlala's "
            "impala gifts while Sergeant Fannie 'Nkosi was found with six cash-in-transit "
            "case files — suggesting network-level interference in active prosecutions. "
            "An ex-cellmate letter connects Matlala directly to Minister Mchunu. "
            "Parallel signal: criminal investigation into R3m paid to cops for 'protecting "
            "Ramaphosa' in the same cluster space. Cat Matlala is pushing for urgent trial "
            "date and a separate trial — indicating legal strategy to fragment prosecution."
        ),
        hypothesis=(
            "The Matlala network is not a single tender fraud — it is a protection "
            "economy embedded inside SAPS operational command, following the State Capture "
            "playbook (per Daily Maverick). The Mchunu connection elevates this to a "
            "political-executive nexus. The Fannie Nkosi case files seizure suggests active "
            "interference with parallel CIT investigations — the network protects itself "
            "by controlling which dockets progress. The 'Ozempic girlfriend' and 'job "
            "terminator' designations in the press indicate the Hawks have cooperating "
            "witnesses describing internal network roles."
        ),
        case_type="general",
    )

    _pin_actors(conn, case_id, actor_roles)

    # Five clusters across the Matlala signal wave
    _pin_signals(conn, case_id,
                 ["geo_-26.0_28.0_10264", "geo_-26.0_28.0_10265",
                  "geo_-26.0_28.0_10268", "geo_-26.0_28.0_10269",
                  "geo_-26.0_28.0_10272"],
                 max_signals=40,
                 note_prefix="[BLUE_LIGHT][cluster] ")
    _pin_signals_by_keyword(conn, case_id,
        ["Matlala", "dirty dozen", "Nkosi", "impala", "Ozempic", "R360m",
         "R360 million", "anti-corruption directorate", "Mchunu", "protecting Ramaphosa",
         "Sibiya", "cash-in-transit", "CIT"],
        max_signals=30, note_prefix="[BLUE_LIGHT][kw] ")

    log.info(f"[BLUE LIGHT] ✓ Seeded as case_id={case_id}")
    return {"case_id": case_id, "status": "seeded", "title": TITLE}


def seed_goldfinger(conn: sqlite3.Connection, resolver: _Resolver, dry_run: bool) -> dict:
    """CASE 4 — OPERATION GOLDFINGER: SIU Enforcement Surge"""
    TITLE = "OPERATION GOLDFINGER — SIU Enforcement Surge: Diamonds, Lotteries, Municipalities"
    log.info(f"\n{'═'*60}")
    log.info(f"[GOLDFINGER] Starting seed…")

    existing = _case_exists(conn, TITLE)
    if existing:
        log.info(f"[GOLDFINGER] Already exists as case_id={existing}, skipping.")
        return {"case_id": existing, "status": "skipped"}

    # ── Actors ─────────────────────────────────────────────────────
    siu_id          = resolver.find("SIU")
    mothibi_id      = resolver.find("Andy Mothibi") or \
                      resolver.resolve_or_create("Advocate Andy Mothibi",       "person")
    alex_bay_id     = resolver.resolve_or_create("Alexander Bay Diamonds Company", "institution")
    scarlet_sky_id  = resolver.resolve_or_create("Scarlet Sky Investments 60",  "institution")
    nlc_id          = resolver.resolve_or_create("National Lotteries Commission","institution")
    mphaphuli_id    = resolver.resolve_or_create("Mphaphuli Consulting",         "institution")
    tubatse_id      = resolver.resolve_or_create("Fetakgomo-Greater Tubatse Municipality", "government")
    gumede_id       = resolver.resolve_or_create("Robert Gumede",                "person")
    csir_id         = resolver.resolve_or_create("CSIR",                        "institution")
    treasury_id     = resolver.find("National Treasury")

    actor_roles = [r for r in [
        (siu_id,        "PRIMARY AUTHORITY — Running all three enforcement vectors simultaneously"),
        (mothibi_id,    "SIU Head — Signed CSIR MoU; leads all active Proclamations"),
        (alex_bay_id,   "TARGET — State diamond valuation fraud; formerly Scarlet Sky Investments"),
        (scarlet_sky_id,"ALIAS — Alexander Bay Diamonds former identity; name-change red flag"),
        (nlc_id,        "TARGET — NLC Northern Cape raided; NPO funding irregularities"),
        (mphaphuli_id,  "LEGAL OBSTRUCTOR — Filed court action against SIU to delay investigation"),
        (tubatse_id,    "MUNICIPALITY — Fetakgomo-Greater Tubatse; linked to Mphaphuli procurement"),
        (gumede_id,     "ACCUSED — SIU hard edge: ACCUSED_OF Robert Gumede (Tongaat Hulett nexus)"),
        (csir_id,       "PARTNER — Technology deployment for anti-corruption (MoU with SIU)"),
        (treasury_id,   "OVERSIGHT — SIU Proclamation; National Treasury named as respondent"),
    ] if r[0] is not None]

    if dry_run:
        log.info(f"[GOLDFINGER] DRY RUN — would create case with {len(actor_roles)} actors")
        return {"case_id": None, "status": "dry_run"}

    case_id = _create_case(
        conn,
        title=TITLE,
        description=(
            "Three simultaneous SIU enforcement actions running under Advocate Andy Mothibi: "
            "(1) Alexander Bay Diamonds Company (formerly Scarlet Sky Investments 60) raided "
            "by Johannesburg Magistrate Court warrant — state diamond valuation, marketing "
            "and sale fraud. The shell company name-change is itself a red flag. "
            "(2) National Lotteries Commission Northern Cape offices raided with Kimberley "
            "Magistrate Court warrant — NPO funding irregularities by the NLC. "
            "(3) Mphaphuli Consulting v SIU (Case No. 5232/2021, Limpopo High Court, "
            "Polokwane) — the respondent list includes the President, Minister of Justice, "
            "and Minister of Finance, indicating Mphaphuli is using constitutional challenge "
            "as a delay mechanism against procurement investigation at Fetakgomo-Greater "
            "Tubatse Municipality. Robert Gumede separately accused by SIU (conf=0.70), "
            "with Daily Maverick documenting his Tongaat Hulett corporate state-capture "
            "connection. CSIR-SIU MoU (Aug 2022) confirms institutional expansion."
        ),
        hypothesis=(
            "The SIU is executing a multi-front enforcement surge that targets three "
            "distinct extraction categories: natural resource capture (diamonds), public "
            "funding diversion (NLC/NPOs), and municipal procurement fraud (Tubatse). "
            "Mphaphuli's court challenge is a canonical delay tactic — the constitutional "
            "respondent list is designed to create jurisdiction confusion and exhaust "
            "SIU resources. Monitor for further Presidential Proclamations as Mothibi "
            "and the CSIR partnership indicate capacity is being built for a larger sweep."
        ),
        case_type="general",
    )

    _pin_actors(conn, case_id, actor_roles)

    # The PDF convergence cluster + SIU keyword sweep
    _pin_signals(conn, case_id, ["geo_-26.0_28.0_10277"],
                 max_signals=25, note_prefix="[GOLDFINGER][cluster_10277] ")
    _pin_signals_by_keyword(conn, case_id,
        ["Alexander Bay", "Scarlet Sky", "Lotteries Commission", "NLC", "Mphaphuli",
         "Tubatse", "Robert Gumede", "Tongaat Hulett", "SIU claws", "dodgy water",
         "CSIR", "Fort Hare", "diamond"],
        max_signals=20, note_prefix="[GOLDFINGER][kw] ")

    # ── PDF Forensic Anchors ────────────────────────────────────────
    GOLDFINGER_ARTIFACTS = {
        564836: "SIU warrant — Alexander Bay Diamonds (state diamond fraud)",
        564832: "SIU warrant — NLC Northern Cape raid (NPO funding irregularities)",
        564831: "SIU court order — Mphaphuli v SIU, Limpopo High Court (Tubatse municipality)",
        564837: "SIU-CSIR MoU — Technology deployment for anti-corruption (capacity signal)",
    }
    _pin_artifacts(conn, case_id, list(GOLDFINGER_ARTIFACTS.keys()), notes=GOLDFINGER_ARTIFACTS)

    log.info(f"[GOLDFINGER] ✓ Seeded as case_id={case_id}")
    return {"case_id": case_id, "status": "seeded", "title": TITLE}


def seed_iron_fist(conn: sqlite3.Connection, resolver: _Resolver, dry_run: bool) -> dict:
    """CASE 5 — OPERATION IRON FIST: Steinhoff Terminal Prosecution Arc"""
    TITLE = "OPERATION IRON FIST — Steinhoff Terminal Prosecution Arc"
    log.info(f"\n{'═'*60}")
    log.info(f"[IRON FIST] Starting seed…")

    existing = _case_exists(conn, TITLE)
    if existing:
        log.info(f"[IRON FIST] Already exists as case_id={existing}, skipping.")
        return {"case_id": existing, "status": "skipped"}

    # ── Actors ─────────────────────────────────────────────────────
    steinhoff_id = resolver.find("Steinhoff")
    jooste_id    = resolver.find("Jooste")
    grobler_id   = resolver.resolve_or_create("Ben Grobler",                  "person")
    fsca_id      = resolver.resolve_or_create("FSCA",                        "institution")
    hawks_id     = resolver.find("Directorate for Priority Crime Investigation") or \
                   resolver.resolve_or_create("Hawks (DPCI)",                 "institution")
    npa_id       = resolver.find("National Prosecuting Authority")
    batohi_id    = resolver.resolve_or_create("Shamila Batohi",              "person")
    saps_id      = resolver.find("South African Police Service") or resolver.find("SAPS")
    vbs_link_id  = resolver.resolve_or_create("VBS Mutual Bank",             "institution")

    actor_roles = [r for r in [
        (steinhoff_id, "PRIMARY — Corporate vehicle; R358m+ fines issued; convictions secured"),
        (jooste_id,    "APEX NODE — Former CEO; deceased after arrest warrant; apex testimony lost"),
        (grobler_id,   "CONVICTED — Former legal/treasury chief; R358.8m FSCA fine"),
        (fsca_id,      "REGULATOR — Enforcement arm; R358.8m fine issued; market-wide sweep signalled"),
        (hawks_id,     "ENFORCEMENT — Hawks confirming ongoing investigation alongside VBS (33 arrests)"),
        (batohi_id,    "NPA HEAD — Publicly defended R30m payment from under-investigation Steinhoff"),
        (npa_id,       "AUTHORITY — Criminal prosecution layer; Batohi conflict of interest flag"),
        (saps_id,      "INVESTIGATION — SAPS INVESTIGATES Steinhoff (hard edge, conf=0.70)"),
        (vbs_link_id,  "CROSS-CASE — 33 arrests in VBS in same Hawks press statement as Steinhoff"),
    ] if r[0] is not None]

    if dry_run:
        log.info(f"[IRON FIST] DRY RUN — would create case with {len(actor_roles)} actors")
        return {"case_id": None, "status": "dry_run"}

    case_id = _create_case(
        conn,
        title=TITLE,
        description=(
            "The Steinhoff corporate fraud case is entering its terminal prosecution phase "
            "with unprecedented financial penalties and a closed apex node (Jooste deceased "
            "post-warrant). FSCA has fined Ben Grobler — former legal and treasury chief — "
            "R358.8 million for false financial statements, the largest individual financial "
            "misconduct fine in SA regulatory history. A former audit executive was sentenced "
            "to four years or a R2m fine (second conviction). SAPS maintains an active "
            "INVESTIGATES hard edge against Steinhoff (conf=0.70). Hawks publicly confirmed "
            "the Steinhoff investigation continues in the same press statement that announced "
            "33 arrests in the VBS heist — suggesting coordinated multi-case financial crime "
            "prosecution. Critical conflict: NPA head Batohi publicly defended a R30m "
            "payment received FROM Steinhoff while the entity remained under NPA investigation."
        ),
        hypothesis=(
            "With Jooste deceased and Grobler fined at record levels, the Steinhoff network "
            "is under maximum terminal pressure — but the Batohi R30m conflict represents "
            "a structural integrity failure in the prosecution itself. If this payment is "
            "not fully disclosed and ring-fenced, it creates grounds for Grobler and any "
            "remaining accused to challenge the independence of the NPA prosecution. "
            "The VBS co-announcement suggests the Hawks are treating both cases as "
            "interlocking state-capture networks. Watch for additional FSCA fine notices "
            "— the 'whole-of-market' language signals further enforcement is planned."
        ),
        case_type="general",
    )

    _pin_actors(conn, case_id, actor_roles)

    _pin_signals(conn, case_id,
                 ["geo_-26.0_28.0_10262", "geo_-26.0_28.0_9902"],
                 max_signals=25, note_prefix="[IRON_FIST][cluster] ")
    _pin_signals_by_keyword(conn, case_id,
        ["Steinhoff", "Jooste", "Grobler", "FSCA", "R358", "R358m",
         "Steinhoff fraud", "Steinhoff conviction", "VBS", "Batohi",
         "Hawks head", "financial misconduct"],
        max_signals=25, note_prefix="[IRON_FIST][kw] ")

    # NPA annual reports are relevant context documents (NPA prosecution capacity)
    NPA_ARTIFACTS = {
        564838: "NPA Annual Report 2020-21 — Prosecution capacity baseline for Steinhoff era",
        564867: "NPA Annual Report 2025 — Current prosecution pipeline (Steinhoff status)",
    }
    _pin_artifacts(conn, case_id, list(NPA_ARTIFACTS.keys()), notes=NPA_ARTIFACTS)

    log.info(f"[IRON FIST] ✓ Seeded as case_id={case_id}")
    return {"case_id": case_id, "status": "seeded", "title": TITLE}


# ── Post-seed: Overlap cross-reference validation ─────────────────────────────

def validate_overlap(conn: sqlite3.Connection, case_ids: list[int]) -> dict:
    """
    Verify that shared actors (DPCI/Hawks, SIU, NPA, SAPS, Ramaphosa) appear
    across multiple cases — confirming the Overlap tab has real cross-case data.
    """
    log.info(f"\n{'═'*60}")
    log.info("[OVERLAP VALIDATION] Cross-referencing actor membership…")

    if not case_ids:
        return {}

    placeholders = ",".join("?" * len(case_ids))
    rows = conn.execute(f"""
        SELECT a.actor_id, a.name, a.type,
               COUNT(DISTINCT ca.case_id) AS case_count,
               GROUP_CONCAT(DISTINCT ca.case_id) AS in_cases
        FROM case_actors ca
        JOIN actors a ON ca.actor_id = a.actor_id
        WHERE ca.case_id IN ({placeholders})
        GROUP BY a.actor_id
        HAVING case_count >= 2
        ORDER BY case_count DESC, a.name
    """, case_ids).fetchall()

    log.info(f"[OVERLAP] Found {len(rows)} actors shared across 2+ cases:")
    results = {}
    for r in rows:
        log.info(f"  [{r['actor_id']}] {r['name']} ({r['type']}) "
                 f"→ {r['case_count']} cases: [{r['in_cases']}]")
        results[r['actor_id']] = {
            "name":       r["name"],
            "type":       r["type"],
            "case_count": r["case_count"],
            "in_cases":   [int(c) for c in str(r["in_cases"]).split(",")],
        }
    return results


def validate_signal_counts(conn: sqlite3.Connection, case_ids: list[int]) -> None:
    """Log final signal + actor counts per seeded case."""
    log.info(f"\n{'═'*60}")
    log.info("[VALIDATION] Final case inventory:")
    for cid in case_ids:
        row = conn.execute("SELECT name AS title FROM cases WHERE case_id=?", (cid,)).fetchone()
        n_sig = conn.execute("SELECT COUNT(*) FROM case_signals WHERE case_id=?", (cid,)).fetchone()[0]
        n_act = conn.execute("SELECT COUNT(*) FROM case_actors WHERE case_id=?", (cid,)).fetchone()[0]
        n_art = conn.execute("SELECT COUNT(*) FROM case_artifacts WHERE case_id=?", (cid,)).fetchone()[0]
        log.info(f"  case_id={cid} | {(row['title'] if row else '?')[:55]}")
        log.info(f"    signals={n_sig}  actors={n_act}  artifacts={n_art}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="FORGE — Seed 5 Latent Investigation Cases"
    )
    parser.add_argument("--db",      type=str, default=None, help="Path to database.db")
    parser.add_argument("--dry-run", action="store_true",    help="Inspect without writing")
    parser.add_argument("--case",    type=str, default=None,
                        help="Seed only one case: dark_badge|prometheus|blue_light|goldfinger|iron_fist")
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    log.info(f"Database: {db_path}")
    if not db_path.exists():
        log.error(f"Database not found: {db_path}")
        sys.exit(1)

    conn    = _open_db(db_path)
    resolver = _Resolver(conn)
    log.info(f"Entity resolver: {len(resolver._exact)} exact / {len(resolver._norm)} normalized entries")

    if args.dry_run:
        log.info("DRY RUN MODE — no writes will occur")

    # Sequential execution with a short breath between commits
    seeders = [
        ("dark_badge",  seed_dark_badge),
        ("prometheus",  seed_prometheus),
        ("blue_light",  seed_blue_light),
        ("goldfinger",  seed_goldfinger),
        ("iron_fist",   seed_iron_fist),
    ]

    if args.case:
        seeders = [(k, v) for k, v in seeders if k == args.case.lower()]
        if not seeders:
            log.error(f"Unknown case key: {args.case}")
            sys.exit(1)

    results   = {}
    case_ids  = []
    for key, seeder_fn in seeders:
        try:
            result = seeder_fn(conn, resolver, dry_run=args.dry_run)
            results[key] = result
            if result.get("case_id"):
                case_ids.append(result["case_id"])
        except Exception as exc:
            log.error(f"[{key.upper()}] FAILED: {exc}", exc_info=True)
            results[key] = {"status": "error", "error": str(exc)}
        # Brief pause between cases to release WAL pressure
        if not args.dry_run:
            time.sleep(0.3)

    if not args.dry_run and len(case_ids) >= 2:
        overlap = validate_overlap(conn, case_ids)
        validate_signal_counts(conn, case_ids)

        # Summary
        log.info(f"\n{'═'*60}")
        log.info("SEED COMPLETE")
        log.info(f"  Cases seeded:       {len([r for r in results.values() if r.get('status')=='seeded'])}")
        log.info(f"  Cases skipped:      {len([r for r in results.values() if r.get('status')=='skipped'])}")
        log.info(f"  Shared actors (≥2): {len(overlap)}")
        for actor_id, info in list(overlap.items())[:8]:
            log.info(f"    {info['name']} → cases {info['in_cases']}")

    conn.close()
    return results


if __name__ == "__main__":
    main()
