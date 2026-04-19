# -*- coding: utf-8 -*-
"""
FORGE -- South African Entity Ruler  (forage/processors/sa_entity_ruler.py)
===========================================================================
Supplements spaCy's en_core_web_sm NER with patterns for SA government
entities, state-owned enterprises, law-enforcement bodies, and political
parties that the base model consistently mislabels or misses entirely.

Loaded by triple_extractor.py BEFORE the NER pipe so EntityRuler patterns
take precedence over the statistical model.

Usage:
    from forage.processors.sa_entity_ruler import build_sa_ruler
    ruler = build_sa_ruler(nlp)   # insert before 'ner' in pipeline
"""

from __future__ import annotations
from typing import List, Dict, Any

# ---------------------------------------------------------------------------
# Pattern list — each entry is a spaCy EntityRuler pattern dict.
# Use string 'pattern' for exact phrase match (case-insensitive via LOWER).
# Use list 'pattern' for token-level patterns.
# ---------------------------------------------------------------------------

SA_ENTITY_PATTERNS: List[Dict[str, Any]] = [

    # ── Law enforcement & investigative bodies ─────────────────────────────
    {"label": "ORG", "pattern": "SIU"},
    {"label": "ORG", "pattern": "Special Investigating Unit"},
    {"label": "ORG", "pattern": [{"LOWER": "special"}, {"LOWER": "investigating"}, {"LOWER": "unit"}]},

    {"label": "ORG", "pattern": "NPA"},
    {"label": "ORG", "pattern": "National Prosecuting Authority"},
    {"label": "ORG", "pattern": [{"LOWER": "national"}, {"LOWER": "prosecuting"}, {"LOWER": "authority"}]},

    {"label": "ORG", "pattern": "NDPP"},
    {"label": "ORG", "pattern": "National Director of Public Prosecutions"},

    {"label": "ORG", "pattern": "Hawks"},
    {"label": "ORG", "pattern": "HAWKS"},
    {"label": "ORG", "pattern": "DPCI"},
    {"label": "ORG", "pattern": "Directorate for Priority Crime Investigation"},
    {"label": "ORG", "pattern": [{"LOWER": "directorate"}, {"LOWER": "for"}, {"LOWER": "priority"}, {"LOWER": "crime"}, {"LOWER": "investigation"}]},

    {"label": "ORG", "pattern": "SAPS"},
    {"label": "ORG", "pattern": "South African Police Service"},
    {"label": "ORG", "pattern": [{"LOWER": "south"}, {"LOWER": "african"}, {"LOWER": "police"}, {"LOWER": "service"}]},

    {"label": "ORG", "pattern": "SSA"},
    {"label": "ORG", "pattern": "State Security Agency"},
    {"label": "ORG", "pattern": [{"LOWER": "state"}, {"LOWER": "security"}, {"LOWER": "agency"}]},

    {"label": "ORG", "pattern": "AFU"},
    {"label": "ORG", "pattern": "Asset Forfeiture Unit"},
    {"label": "ORG", "pattern": [{"LOWER": "asset"}, {"LOWER": "forfeiture"}, {"LOWER": "unit"}]},

    {"label": "ORG", "pattern": "SCCU"},
    {"label": "ORG", "pattern": "Serious Commercial Crime Unit"},

    {"label": "ORG", "pattern": "FIC"},
    {"label": "ORG", "pattern": "Financial Intelligence Centre"},
    {"label": "ORG", "pattern": [{"LOWER": "financial"}, {"LOWER": "intelligence"}, {"LOWER": "centre"}]},

    # ── Revenue & audit ─────────────────────────────────────────────────────
    {"label": "ORG", "pattern": "SARS"},
    {"label": "ORG", "pattern": "South African Revenue Service"},
    {"label": "ORG", "pattern": [{"LOWER": "south"}, {"LOWER": "african"}, {"LOWER": "revenue"}, {"LOWER": "service"}]},

    {"label": "ORG", "pattern": "AGSA"},
    {"label": "ORG", "pattern": "Auditor-General"},
    {"label": "ORG", "pattern": "Auditor General"},
    {"label": "ORG", "pattern": [{"LOWER": "auditor"}, {"OP": "?"}, {"LOWER": "general"}]},

    # ── Treasury & procurement ──────────────────────────────────────────────
    {"label": "ORG", "pattern": "National Treasury"},
    {"label": "ORG", "pattern": "Treasury"},
    {"label": "ORG", "pattern": "OCPO"},
    {"label": "ORG", "pattern": "Office of the Chief Procurement Officer"},
    {"label": "ORG", "pattern": [{"LOWER": "chief"}, {"LOWER": "procurement"}, {"LOWER": "officer"}]},

    # ── State-owned enterprises ─────────────────────────────────────────────
    {"label": "ORG", "pattern": "Eskom"},
    {"label": "ORG", "pattern": "Transnet"},
    {"label": "ORG", "pattern": "SABC"},
    {"label": "ORG", "pattern": "South African Broadcasting Corporation"},
    {"label": "ORG", "pattern": "Denel"},
    {"label": "ORG", "pattern": "PRASA"},
    {"label": "ORG", "pattern": "Prasa"},
    {"label": "ORG", "pattern": "Passenger Rail Agency"},
    {"label": "ORG", "pattern": [{"LOWER": "passenger"}, {"LOWER": "rail"}, {"LOWER": "agency"}]},
    {"label": "ORG", "pattern": "SAA"},
    {"label": "ORG", "pattern": "South African Airways"},
    {"label": "ORG", "pattern": "SANRAL"},
    {"label": "ORG", "pattern": "Land Bank"},
    {"label": "ORG", "pattern": "PIC"},
    {"label": "ORG", "pattern": "Public Investment Corporation"},
    {"label": "ORG", "pattern": [{"LOWER": "public"}, {"LOWER": "investment"}, {"LOWER": "corporation"}]},
    {"label": "ORG", "pattern": "Armscor"},
    {"label": "ORG", "pattern": "CSIR"},

    # ── Key departments (corruption hotspots) ───────────────────────────────
    {"label": "ORG", "pattern": "DPWI"},
    {"label": "ORG", "pattern": "Department of Public Works"},
    {"label": "ORG", "pattern": [{"LOWER": "department"}, {"LOWER": "of"}, {"LOWER": "public"}, {"LOWER": "works"}]},
    {"label": "ORG", "pattern": "DWS"},
    {"label": "ORG", "pattern": "Department of Water and Sanitation"},
    {"label": "ORG", "pattern": "NLC"},
    {"label": "ORG", "pattern": "National Lotteries Commission"},
    {"label": "ORG", "pattern": [{"LOWER": "national"}, {"LOWER": "lotteries"}, {"LOWER": "commission"}]},
    {"label": "ORG", "pattern": "COGTA"},
    {"label": "ORG", "pattern": "Department of Health"},
    {"label": "ORG", "pattern": "DOH"},
    {"label": "ORG", "pattern": "DIRCO"},

    # ── Courts & tribunals ──────────────────────────────────────────────────
    {"label": "ORG", "pattern": "Constitutional Court"},
    {"label": "ORG", "pattern": "Supreme Court of Appeal"},
    {"label": "ORG", "pattern": "High Court"},
    {"label": "ORG", "pattern": "Special Tribunal"},
    {"label": "ORG", "pattern": [{"LOWER": "special"}, {"LOWER": "tribunal"}]},

    # ── Political parties ───────────────────────────────────────────────────
    {"label": "ORG", "pattern": "ANC"},
    {"label": "ORG", "pattern": "African National Congress"},
    {"label": "ORG", "pattern": "EFF"},
    {"label": "ORG", "pattern": "Economic Freedom Fighters"},
    {"label": "ORG", "pattern": "DA"},
    {"label": "ORG", "pattern": "Democratic Alliance"},
    {"label": "ORG", "pattern": "MK Party"},
    {"label": "ORG", "pattern": [{"LOWER": "mk"}, {"LOWER": "party"}]},
    {"label": "ORG", "pattern": "PAC"},
    {"label": "ORG", "pattern": "IFP"},

    # ── Locations frequently confused by base model ─────────────────────────
    {"label": "GPE", "pattern": "Gauteng"},
    {"label": "GPE", "pattern": "Limpopo"},
    {"label": "GPE", "pattern": "Mpumalanga"},
    {"label": "GPE", "pattern": "KwaZulu-Natal"},
    {"label": "GPE", "pattern": "KZN"},
    {"label": "GPE", "pattern": "Western Cape"},
    {"label": "GPE", "pattern": "Eastern Cape"},
    {"label": "GPE", "pattern": "Northern Cape"},
    {"label": "GPE", "pattern": "North West"},
    {"label": "GPE", "pattern": "Free State"},
]


def build_sa_ruler(nlp):
    """
    Create and return a configured EntityRuler for SA government entities.

    Call this BEFORE the 'ner' component so these patterns take precedence
    over the statistical model:

        ruler = build_sa_ruler(nlp)
        nlp.add_pipe("entity_ruler", before="ner")  # already added inside

    Actually, this function adds the ruler to the pipeline directly and
    returns the nlp object so the caller can proceed transparently.
    """
    # spaCy 3.x EntityRuler API
    ruler = nlp.add_pipe(
        "entity_ruler",
        before="ner",
        config={"overwrite_ents": False},  # statistical NER wins on conflicts
    )
    ruler.add_patterns(SA_ENTITY_PATTERNS)
    return nlp
