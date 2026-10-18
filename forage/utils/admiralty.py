"""
forage/utils/admiralty.py — Admiralty Grading System
======================================================

Implements the NATO/Intelligence Community Admiralty Code for source and
information reliability assessment.

Source Reliability (A–F)
────────────────────────
  A — Completely reliable      (established, verified track record)
  B — Usually reliable         (minor doubts, mostly verified)
  C — Fairly reliable          (occasional doubts, some verified)
  D — Not usually reliable     (significant doubts, little verified)
  E — Unreliable               (unverified, consistently inaccurate)
  F — Reliability cannot be judged (new or unknown source)

Information Credibility (1–6)
──────────────────────────────
  1 — Confirmed by other sources
  2 — Probably true            (corroborated by other info)
  3 — Possibly true            (not corroborated)
  4 — Doubtful                 (contradicted by other info)
  5 — Improbable               (contradicted by reliable sources)
  6 — Truth cannot be judged

Usage
─────
  from forage.utils.admiralty import grade_source, grade_info, AdmiraltyCode
  code = AdmiraltyCode(source="B", info=2)
  print(code)           # "B2"
  print(code.label)     # "Usually reliable / Probably true"
  print(code.weight)    # 0.80 (combined numeric weight ∈ [0,1])
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Optional

# ── Source reliability mapping ────────────────────────────────────────────────

_SOURCE_GRADES: dict[str, tuple[str, float]] = {
    "A": ("Completely reliable",            1.00),
    "B": ("Usually reliable",               0.80),
    "C": ("Fairly reliable",                0.60),
    "D": ("Not usually reliable",           0.40),
    "E": ("Unreliable",                     0.10),
    "F": ("Reliability cannot be judged",   0.50),  # neutral — unknown
}

# ── Information credibility mapping ──────────────────────────────────────────

_INFO_GRADES: dict[int, tuple[str, float]] = {
    1: ("Confirmed by other sources",       1.00),
    2: ("Probably true",                    0.80),
    3: ("Possibly true",                    0.60),
    4: ("Doubtful",                         0.35),
    5: ("Improbable",                       0.10),
    6: ("Truth cannot be judged",           0.50),  # neutral — unknown
}

# ── Per-domain source tier defaults ──────────────────────────────────────────
# Domains rated by FORGE analysts based on track record.
# Override per-signal when better information is available.

_DOMAIN_SOURCE_GRADES: dict[str, str] = {
    # ── Tier A — primary government / official records (full domains) ─────────
    "siu.org.za":               "A",
    "npa.gov.za":               "A",
    "judiciary.org.za":         "A",
    "parliament.gov.za":        "A",
    "agsa.co.za":               "A",
    "hawksholdings.co.za":      "A",
    "saps.gov.za":              "A",
    "treasury.gov.za":          "A",
    "dpme.gov.za":              "A",
    # ── Tier B — established investigative / public interest (full domains) ───
    "dailymaverick.co.za":      "B",
    "groundup.org.za":          "B",
    "amabhungane.co.za":        "B",
    "timeslive.co.za":          "B",
    "businesslive.co.za":       "B",
    "ewn.co.za":                "B",
    "sabcnews.com":             "B",
    "news24.com":               "B",
    # ── Tier C — general SA news (full domains) ───────────────────────────────
    "iol.co.za":                "C",
    "citizen.co.za":            "C",
    "businesstech.co.za":       "C",
    "politicsweb.co.za":        "C",
    "sowetanlive.co.za":        "C",
    # ── Tier D — aggregators / secondary sources (full domains) ──────────────
    "gdeltproject.org":         "D",
    "acleddata.com":            "C",   # ACLED is methodologically rigorous
    # ── Collector short-names (artifacts.source column values) ────────────────
    # These are the canonical identifiers written by FORGE collectors when they
    # INSERT into the artifacts table. Mapping them here means grade_source()
    # works on both full domain strings (from live signals) and the short-names
    # stored in artifacts.source (from the PDF portal and structured collectors).
    #
    # Tier A — primary sovereign / prosecutorial sources
    "siu":                      "A",   # SIU portal PDF infiltrator
    "npa":                      "A",   # National Prosecuting Authority
    "hawks":                    "A",   # Directorate for Priority Crime Investigation
    "special_tribunal":         "A",   # Special Tribunal (SIU adjudication arm)
    "agsa":                     "A",   # Auditor-General South Africa
    "treasury":                 "A",   # National Treasury (irregular expenditure)
    "government":               "A",   # Generic government PDF portal (pdf_portal source_type)
    # Tier B — law enforcement / verified investigative
    "saps":                     "B",   # SA Police Service
    "daily_maverick":           "B",   # Daily Maverick (collector short-name variant)
    # Tier C — baseline aggregators
    "gdelt":                    "C",   # GDELT DOC API collector
    "acled":                    "C",   # ACLED conflict data collector
    "civic_intel":              "C",   # Civic intelligence RSS collector
    "rss":                      "C",   # Generic RSS collector
    # pdf_infiltrator: the PDF portal NER pipeline — documents sourced from
    # SIU/NPA/government portals, detonated and validated before ingestion.
    "pdf_infiltrator":          "A",
    # Note: 'unverified' is NOT mapped here — rescue_artifacts.py resolves it
    # contextually using source_type before calling grade_source().
}


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class AdmiraltyCode:
    """
    Immutable Admiralty code pairing source reliability + info credibility.

    Attributes
    ----------
    source : str  — one of A-F
    info   : int  — one of 1-6
    """
    source: str = "F"
    info:   int = 6

    def __post_init__(self):
        self.source = (self.source or "F").upper().strip()
        if self.source not in _SOURCE_GRADES:
            self.source = "F"
        try:
            self.info = int(self.info)
        except (TypeError, ValueError):
            self.info = 6
        if self.info not in _INFO_GRADES:
            self.info = 6

    def __str__(self) -> str:
        return f"{self.source}{self.info}"

    @property
    def source_label(self) -> str:
        return _SOURCE_GRADES[self.source][0]

    @property
    def info_label(self) -> str:
        return _INFO_GRADES[self.info][0]

    @property
    def label(self) -> str:
        return f"{self.source_label} / {self.info_label}"

    @property
    def source_weight(self) -> float:
        return _SOURCE_GRADES[self.source][1]

    @property
    def info_weight(self) -> float:
        return _INFO_GRADES[self.info][1]

    @property
    def weight(self) -> float:
        """
        Combined reliability weight ∈ [0.0, 1.0].
        Geometric mean of source and info weights — a single unreliable
        dimension pulls the combined score down proportionally.
        """
        return round((self.source_weight * self.info_weight) ** 0.5, 4)

    def to_dict(self) -> dict:
        return {
            "code":           str(self),
            "source":         self.source,
            "info":           self.info,
            "source_label":   self.source_label,
            "info_label":     self.info_label,
            "weight":         self.weight,
        }


# ── Public helpers ────────────────────────────────────────────────────────────

def _normalise_domain(domain: str) -> str:
    """'https://www.iol.co.za/capeargus/x' -> 'iol.co.za'; short-names pass through."""
    d = (domain or "").lower().strip()
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d)      # scheme
    d = d.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def grade_source(domain: str) -> Optional[str]:
    """
    Return the default source reliability grade (A-E) for a domain, or None
    when the domain is not on the tier list.

    Sprint 4 fix: an outlet missing from the list used to come back as 'F',
    which read as "unreliable" for every regional and community paper (10 of
    the 13 outlets behind the hand-checked pins). Not being on the list means
    nobody has graded it, so the answer is None, not a grade. Accepts full
    URLs and sub-domains (m.news24.com, www.iol.co.za/capeargus/...).
    """
    d = _normalise_domain(domain)
    if not d:
        return None
    parts = d.split(".")
    for i in range(len(parts) - 1):          # m.news24.com -> news24.com
        g = _DOMAIN_SOURCE_GRADES.get(".".join(parts[i:]))
        if g:
            return g
    return _DOMAIN_SOURCE_GRADES.get(d)       # collector short-names


# Grades are list defaults set by hand, not checked against any outlet's track
# record. Until they are, nothing public shows them.
GRADES_VALIDATED = False


def public_grade(domain: str) -> Optional[str]:
    """The grade a public page may show: None while grades are unvalidated."""
    return grade_source(domain) if GRADES_VALIDATED else None


def grade_info(corroborated: bool = False, contradicted: bool = False) -> int:
    """
    Heuristic info credibility grade.

      corroborated=True, contradicted=False → 2 (probably true)
      corroborated=False, contradicted=False → 3 (possibly true, unverified)
      corroborated=False, contradicted=True  → 4 (doubtful)
      corroborated=True, contradicted=True   → 6 (cannot judge — conflicting)
    """
    if corroborated and not contradicted:
        return 2
    if not corroborated and contradicted:
        return 4
    if corroborated and contradicted:
        return 6
    return 3


def code_for_domain(domain: str, corroborated: bool = False,
                    contradicted: bool = False) -> AdmiraltyCode:
    """
    Convenience: build an AdmiraltyCode from a domain + corroboration state.
    """
    return AdmiraltyCode(
        # Ungraded -> 'F' here only: in Admiralty terms F is "reliability
        # cannot be judged", which weights as neutral (0.5) in the engines.
        source=grade_source(domain) or "F",
        info=grade_info(corroborated, contradicted),
    )


# ── A-tier domain set (for rescue_artifacts fast lookup) ─────────────────────

A_TIER_DOMAINS: frozenset[str] = frozenset(
    d for d, g in _DOMAIN_SOURCE_GRADES.items() if g == "A"
)

B_TIER_DOMAINS: frozenset[str] = frozenset(
    d for d, g in _DOMAIN_SOURCE_GRADES.items() if g in ("A", "B")
)
