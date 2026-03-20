"""
signal_enrichment — Engine  (v2.0 — Reference Standard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Produces an AnalysisResult from a signal using South African entity
detection, type inference, and source-aware confidence scoring.

Changes from v1.0:
    - Pattern keys (e.g. "location_ZA") never reach the actor registry
    - Each pattern maps to a canonical entity name + semantic type
    - Confidence derived from match strength + source credibility
    - AnalysisResult.entities contains clean names only
    - Provenance includes per-entity type map for downstream use

This module is the reference standard for all future FMS modules.
"""

from __future__ import annotations
import re
from typing import Dict, Any, List, Tuple

from core.conclave.registry import AnalysisResult


# ── Entity registry ───────────────────────────────────────────────────────────
# Structure: pattern_key → {name, type, pattern}
#
# name  = canonical actor name written to the actors table
# type  = one of: person | institution | political_party | location | government
# pattern = compiled regex
#
# To extend: add a new entry. Nothing else changes.

ENTITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "NPA": {
        "name":    "National Prosecuting Authority",
        "type":    "government",
        "pattern": re.compile(r"\b(NPA|National Prosecuting Authority)\b", re.I),
    },
    "SAPS": {
        "name":    "South African Police Service",
        "type":    "government",
        "pattern": re.compile(r"\b(SAPS|South African Police|police service)\b", re.I),
    },
    "Hawks": {
        "name":    "Directorate for Priority Crime Investigation",
        "type":    "government",
        "pattern": re.compile(r"\b(Hawks|DPCI|Directorate.*Priority.*Crime)\b", re.I),
    },
    "Eskom": {
        "name":    "Eskom",
        "type":    "institution",
        "pattern": re.compile(r"\bEskom\b", re.I),
    },
    "SARB": {
        "name":    "South African Reserve Bank",
        "type":    "institution",
        "pattern": re.compile(r"\b(SARB|Reserve Bank|South African Reserve Bank)\b", re.I),
    },
    "Treasury": {
        "name":    "National Treasury",
        "type":    "government",
        "pattern": re.compile(r"\b(National Treasury|Treasury)\b", re.I),
    },
    "ANC": {
        "name":    "African National Congress",
        "type":    "political_party",
        "pattern": re.compile(r"\b(ANC|African National Congress)\b", re.I),
    },
    "DA": {
        "name":    "Democratic Alliance",
        "type":    "political_party",
        "pattern": re.compile(r"\b(DA|Democratic Alliance)\b", re.I),
    },
    "EFF": {
        "name":    "Economic Freedom Fighters",
        "type":    "political_party",
        "pattern": re.compile(r"\b(EFF|Economic Freedom Fighters)\b", re.I),
    },
    "Ramaphosa": {
        "name":    "Cyril Ramaphosa",
        "type":    "person",
        "pattern": re.compile(r"\bRamaphosa\b", re.I),
    },
    "Municipalities": {
        "name":    "South African Municipalities",
        "type":    "government",
        "pattern": re.compile(
            r"\b(municipality|municipal|metro|Tshwane|Joburg|eThekwini|"
            r"Buffalo City)\b", re.I
        ),
    },
    "South Africa": {
        "name":    "South Africa",
        "type":    "location",
        "pattern": re.compile(
            r"\b(South Africa|SA\b|Johannesburg|Cape Town|Pretoria|Durban|"
            r"Soweto|Sandton|Limpopo|Mpumalanga|KwaZulu|Western Cape|Gauteng)\b",
            re.I
        ),
    },
}

# ── Source credibility ────────────────────────────────────────────────────────

SOURCE_CREDIBILITY: Dict[str, float] = {
    "NPA": 0.92, "GOVERNMENT": 0.88, "SAPS": 0.85,
    "GDELT": 0.72, "CIVIC": 0.78, "AMABHUNGANE": 0.90,
    "DAILY_MAVERICK": 0.88, "GROUNDUP": 0.85, "MYBROADBAND": 0.75,
    "USGS": 0.97, "FIRMS": 0.93, "GDACS": 0.82, "RSS": 0.65,
}

# ── Severity keywords ─────────────────────────────────────────────────────────

SEVERITY_MAP: Dict[str, float] = {
    "murder": 1.0, "hijack": 1.0, "bomb": 1.0, "explosion": 0.95,
    "assassination": 1.0, "massacre": 1.0,
    "attack": 0.7, "arrest": 0.65, "raid": 0.65, "violence": 0.7,
    "corruption": 0.6, "fraud": 0.55, "looting": 0.7,
    "investigation": 0.35, "protest": 0.35, "sanction": 0.30,
    "load shedding": 0.40, "outage": 0.35, "strike": 0.35,
    "report": 0.10, "meeting": 0.08, "statement": 0.08,
}


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_entities(text: str) -> List[Dict[str, str]]:
    """
    Return list of matched entities as {name, type} dicts.
    Uses canonical name from registry — pattern keys never leak out.
    """
    matched = []
    for key, entry in ENTITY_REGISTRY.items():
        if entry["pattern"].search(text):
            matched.append({
                "name": entry["name"],
                "type": entry["type"],
            })
    return matched


def _score_severity(text: str) -> float:
    txt = text.lower()
    return min(sum(w for t, w in SEVERITY_MAP.items() if t in txt), 1.0)


def _source_credibility(signal: Dict[str, Any]) -> float:
    source = str(signal.get("source", "")).upper().replace(" ", "_")
    return SOURCE_CREDIBILITY.get(source, 0.5)


def _confidence(
    entities: List[Dict],
    credibility: float,
    severity: float,
    is_priority: int,
) -> float:
    """
    Confidence derived from three factors:

    1. Match strength — how many distinct entities were found
       Each entity adds 0.12, capped at 0.4
    2. Source credibility — how reliable is the signal source
       Contributes up to 0.35
    3. Severity boost — high-severity signals are more likely real
       Adds up to 0.15
    4. Priority flag — is_priority=1 adds a flat 0.10 bonus

    Total range: ~0.12 (one entity, unknown source) to 1.0
    """
    match_strength  = min(len(entities) * 0.12, 0.40)
    source_weight   = credibility * 0.35
    severity_weight = severity * 0.15
    priority_bonus  = 0.10 * is_priority

    raw = match_strength + source_weight + severity_weight + priority_bonus
    return round(min(raw, 1.0), 3)


# ── Main engine function ──────────────────────────────────────────────────────

def run(signal: Dict[str, Any]) -> AnalysisResult:
    """
    Produce a hardened AnalysisResult with clean entity names,
    correct types, and meaningful confidence.

    Returns None if no SA entities are detected — no point adding
    a zero-weight result to the Conclave merge.
    """
    text = " ".join([
        str(signal.get("title", "")),
        str(signal.get("content", "")),
    ])

    entities    = _extract_entities(text)
    if not entities:
        return None  # no SA content — skip Conclave contribution

    severity    = _score_severity(text)
    credibility = _source_credibility(signal)
    is_priority = int(signal.get("is_priority", 0) or 0)

    actor_importance = min(len(entities) * 0.25, 1.0)
    source           = str(signal.get("source", "")).upper()
    freq_base        = 0.5 if source in SOURCE_CREDIBILITY else 0.2
    frequency        = min(freq_base + 0.3 * is_priority, 1.0)

    urgency  = 0.5
    base     = (
        0.35 * severity +
        0.25 * actor_importance +
        0.15 * frequency +
        0.15 * urgency +
        0.10 * credibility
    )
    momentum = 0.8 + 0.2 * frequency
    gravity  = round(min(base * momentum, 1.0), 4)

    if gravity >= 0.55:
        recommendation = "ESCALATE"
    elif gravity >= 0.35:
        recommendation = "MONITOR"
    else:
        recommendation = "IGNORE"

    confidence = _confidence(entities, credibility, severity, is_priority)

    # entity names only — types stored in provenance for downstream use
    entity_names = [e["name"] for e in entities]
    entity_types = {e["name"]: e["type"] for e in entities}

    return AnalysisResult(
        entities=entity_names,
        intent="signal_enrichment",
        gravity=gravity,
        recommendation=recommendation,
        confidence=confidence,
        provenance={
            "module":        "signal_enrichment",
            "engine":        "signal_enrichment_engine",
            "entity_types":  entity_types,
            "severity":      round(severity, 3),
            "credibility":   credibility,
            "match_count":   len(entities),
        },
    )