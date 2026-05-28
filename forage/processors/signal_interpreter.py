import re
from typing import Any, Dict, List

# Simple lexicon-based signal classification/extraction.
EVENT_KEYWORDS = {
    "protest": ["protest", "demonstration", "strike"],
    "conflict": ["shooting", "firefight", "clash", "battle", "attack", "bomb"],
    "economic": ["market", "stocks", "price", "inflation", "economy"],
    "legal": ["court", "indictment", "investigation", "conviction", "lawsuit"],
    "anomaly": ["explosion", "outage", "failure", "vulnerability", "breach"],
}

ACTOR_PATTERNS = {
    "NPA": re.compile(r"\b(NPA|National Prosecuting Authority)\b", re.I),
    "government": re.compile(r"\b(government|minister|department)\b", re.I),
    "company": re.compile(r"\b(inc\.?|ltd\.?|corporation|company|firm)\b", re.I),
    "location": re.compile(r"\b(South Africa|SA|Johannesburg|Cape Town|Pretoria)\b", re.I),
}

SEVERITY_WEIGHTS = {
    "critical": ["hijack", "bomb", "murder", "assault"],
    "high": ["attack", "violence", "arrest", "raid"],
    "medium": ["investigation", "protest", "sanction"],
    "low": ["report", "meeting", "statement"],
    # Phase 64 Epsilon-III — Investigative Journalism Tier
    # These keywords signal deep OSINT/investigative content that the kinetic
    # scoring model previously underweighted. Each maps to a gravity boost:
    #   inv_critical (+0.85): structural criminal enterprise indicators
    #   inv_high     (+0.55): corruption / judicial interference signals
    #   inv_medium   (+0.35): financial crime / accountability journalism
    "inv_critical": [
        "racketeering", "money-laundering", "money laundering",
        "shell company", "shell structure", "judicial interference",
        "trafficking", "syndicate",
    ],
    "inv_high": [
        "bribes", "bribery", "convicted", "smuggling", "unaccounted for",
        "fraudulent contract", "corrupt", "poaching syndicate",
        "elite capture", "forged documents",
    ],
    "inv_medium": [
        "charges", "indicted", "laundering", "corporate opacity",
        "transparency deficit", "no eia", "legislative vacuum",
        "ministerial interference", "community revenue",
    ],
}


def _score_severity(text: str) -> float:
    """Return clamped severity score. See score_severity_detailed() for audit trail."""
    return score_severity_detailed(text)["severity"]


def score_severity_detailed(text: str) -> dict:
    """
    Phase 68 — Auditable severity scoring.

    Returns a dict:
      severity              float   clamped [0.0, 1.0]
      investigative_uplift  float   sum of inv_* tier contributions (pre-clamp)
      investigative_tier    str     'inv_critical' | 'inv_high' | 'inv_medium' | ''
      matched_inv_keywords  list    which investigative keywords fired
    """
    if not text:
        return {
            "severity": 0.0,
            "investigative_uplift": 0.0,
            "investigative_tier": "",
            "matched_inv_keywords": [],
        }

    score = 0.0
    inv_uplift = 0.0
    matched_inv: list[str] = []
    highest_inv_tier = ""
    tier_rank = {"inv_critical": 3, "inv_high": 2, "inv_medium": 1, "": 0}

    txt = text.lower()
    for weight, terms in SEVERITY_WEIGHTS.items():
        for term in terms:
            if term in txt:
                if weight == "critical":
                    score += 1.0
                elif weight == "high":
                    score += 0.6
                elif weight == "medium":
                    score += 0.3
                # Phase 64 Epsilon-III — Investigative Journalism Tier
                elif weight == "inv_critical":
                    contribution = 0.85
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_critical"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_critical"
                elif weight == "inv_high":
                    contribution = 0.55
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_high"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_high"
                elif weight == "inv_medium":
                    contribution = 0.35
                    score     += contribution
                    inv_uplift += contribution
                    matched_inv.append(term)
                    if tier_rank["inv_medium"] > tier_rank.get(highest_inv_tier, 0):
                        highest_inv_tier = "inv_medium"
                else:
                    score += 0.1

    return {
        "severity":              round(min(score, 1.0), 4),
        "investigative_uplift":  round(min(inv_uplift, 1.0), 4),
        "investigative_tier":    highest_inv_tier,
        "matched_inv_keywords":  matched_inv,
    }


# Strings matched by ACTOR_PATTERNS that are category indicators, not proper
# actor names. These must not be inserted as actor records in the graph.
_GENERIC_ACTOR_TERMS = frozenset({
    "government", "minister", "department",
    "inc", "inc.", "ltd", "ltd.", "corporation", "company", "firm",
})


def _extract_actors(text: str) -> List[str]:
    """
    Extract proper actor name strings from text using ACTOR_PATTERNS.

    Uses findall() to capture the actual matched text (e.g. "Cape Town",
    "National Prosecuting Authority") rather than the dict key ("location",
    "NPA"). Generic category words are filtered via _GENERIC_ACTOR_TERMS.
    """
    actors: set[str] = set()
    for pattern in ACTOR_PATTERNS.values():
        for match in pattern.findall(text):
            if match and match.lower().rstrip(".") not in _GENERIC_ACTOR_TERMS:
                actors.add(match)
    return sorted(actors)


def _infer_event_type(text: str) -> str:
    txt = text.lower()
    for event_type, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in txt:
                return event_type
    return "unknown"


class SignalInterpreter:
    """Translate a raw signal into structured self-describing metadata."""

    def __init__(self) -> None:
        self.version = "1.0"

    def interpret(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Returns metadata map from a signal dict."""
        text = "".join(
            [
                str(signal.get("title", "")),
                " ",
                str(signal.get("content", "")),
            ]
        )
        encoded = signal.get("metadata_json")
        if not text and encoded:
            try:
                import json

                m = json.loads(encoded)
                text = " ".join(str(v) for v in m.values() if isinstance(v, str))
            except Exception:
                text = ""

        actors = _extract_actors(text)
        ev_type = _infer_event_type(text)

        # Phase 68: use detailed scoring to capture investigative uplift audit trail
        severity_detail = score_severity_detailed(text)
        severity = severity_detail["severity"]

        # actor_importance: more actors = higher importance, capped at 1.0
        actor_importance = min(len(actors) * 0.3, 1.0)

        # frequency: use is_priority flag and known high-value sources
        source = str(signal.get("source", "")).upper()
        is_priority = int(signal.get("is_priority", 0) or 0)
        # Phase 64 Epsilon-III: OXPECKERS added — award-winning investigative
        # environmental journalism; treated as high-value analytical source.
        high_value_sources = {"NPA", "GDELT", "CIVIC", "SAPS", "GOVERNMENT", "OXPECKERS"}
        freq_base = 0.4 if source in high_value_sources else 0.2
        frequency = min(freq_base + (0.3 * is_priority), 1.0)

        # source_credibility: known sources score higher
        credibility_map = {
            "NPA": 0.9, "GOVERNMENT": 0.85, "GDELT": 0.7,
            "CIVIC": 0.75, "USGS": 0.95, "FIRMS": 0.9,
            "RSS": 0.65, "GDACS": 0.8,
            # Phase 64 Epsilon-III: Oxpeckers — Africa's first investigative
            # environmental journalism unit; ACE Award 2025 winner.
            "OXPECKERS": 0.85,
        }
        source_credibility = credibility_map.get(source, 0.5)

        result = {
            "type": "event" if ev_type != "unknown" else "unknown",
            "actors": actors,
            "event_type": ev_type,
            "severity": round(severity, 2),
            "actor_importance": round(actor_importance, 2),
            "frequency": round(frequency, 2),
            "source_credibility": round(source_credibility, 2),
            "raw_signal_id": signal.get("signal_id"),
        }
        # Phase 68: propagate investigative uplift metadata for conclave_meta audit
        if severity_detail["investigative_tier"]:
            result["investigative_uplift"]      = severity_detail["investigative_uplift"]
            result["investigative_tier"]         = severity_detail["investigative_tier"]
            result["matched_inv_keywords"]       = severity_detail["matched_inv_keywords"]
        return result

    def batch_interpret(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.interpret(s) for s in signals]