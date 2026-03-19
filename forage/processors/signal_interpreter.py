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
}


def _score_severity(text: str) -> float:
    if not text:
        return 0.0
    score = 0.0
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
                else:
                    score += 0.1
    return min(score, 1.0)


def _extract_actors(text: str) -> List[str]:
    actors = set()
    for name, pattern in ACTOR_PATTERNS.items():
        if pattern.search(text):
            actors.add(name)
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
        severity = _score_severity(text)

        # actor_importance: more actors = higher importance, capped at 1.0
        actor_importance = min(len(actors) * 0.3, 1.0)

        # frequency: use is_priority flag and known high-value sources
        source = str(signal.get("source", "")).upper()
        is_priority = int(signal.get("is_priority", 0) or 0)
        high_value_sources = {"NPA", "GDELT", "CIVIC", "SAPS", "GOVERNMENT"}
        freq_base = 0.4 if source in high_value_sources else 0.2
        frequency = min(freq_base + (0.3 * is_priority), 1.0)

        # source_credibility: known sources score higher
        credibility_map = {
            "NPA": 0.9, "GOVERNMENT": 0.85, "GDELT": 0.7,
            "CIVIC": 0.75, "USGS": 0.95, "FIRMS": 0.9,
            "RSS": 0.65, "GDACS": 0.8,
        }
        source_credibility = credibility_map.get(source, 0.5)

        return {
            "type": "event" if ev_type != "unknown" else "unknown",
            "actors": actors,
            "event_type": ev_type,
            "severity": round(severity, 2),
            "actor_importance": round(actor_importance, 2),
            "frequency": round(frequency, 2),
            "source_credibility": round(source_credibility, 2),
            "raw_signal_id": signal.get("signal_id"),
        }

    def batch_interpret(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.interpret(s) for s in signals]