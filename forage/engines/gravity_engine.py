"""FORAGE Gravity Engine

Phase: Decision core weight for signal urgency/importance.

Input fields (numerical 0.0-1.0 expected):
- severity
- actor_importance
- frequency
- sentiment (negative is higher gravity)
- source_credibility

Output:
- gravity_score (0.0 .. 1.0)
"""

from typing import Dict, Any, List, Optional

from forage.engines.feedback_engine import actor_influence


def _clamp(value: float, minv: float = 0.0, maxv: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return minv
    if v < minv:
        return minv
    if v > maxv:
        return maxv
    return v


def calculate_gravity(inputs: Dict[str, Any]) -> float:
    """Compute gravity score from normalized signals.

    Caller should provide values in [0,1], map sentiment to 0..1.
    sentiment is expected positive for positive tone; negative tone
    reduces score by inversing and biasing towards 1.
    """
    severity = _clamp(inputs.get("severity", 0.0))
    actor_importance = _clamp(inputs.get("actor_importance", 0.0))
    frequency = _clamp(inputs.get("frequency", 0.0))
    sentiment = inputs.get("sentiment", 0.0)
    credibility = _clamp(inputs.get("source_credibility", 0.5))

    # normalize sentiment: -1 (negative) .. +1 (positive) to 0..1
    try:
        sentiment = float(sentiment)
    except (TypeError, ValueError):
        sentiment = 0.0
    sentiment = (sentiment + 1.0) / 2.0
    sentiment = _clamp(sentiment)

    # reverse positive sentiment: high urgency for low sentiment
    urgency_sentiment = 1.0 - sentiment

    # Mix factors with weights tuned for event gravity
    # severity and actor importance are most weighty.
    base = (0.35 * severity +
            0.25 * actor_importance +
            0.15 * frequency +
            0.15 * urgency_sentiment +
            0.10 * credibility)

    # optional dynamic multiplier for repeats / momentum
    momentum = 0.8 + 0.2 * frequency  # floor raised: 0.6→0.8 so base score isn't halved
    score = base * momentum

    return _clamp(score)


def score_signal(signal: Dict[str, Any], actors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return signal copy with gravity_score affected by actor feedback weights."""
    gravity_score = calculate_gravity({
        "severity": signal.get("severity", 0.0),
        "actor_importance": signal.get("actor_importance", 0.0),
        "frequency": signal.get("frequency", 0.0),
        "sentiment": signal.get("sentiment", 0.0),
        "source_credibility": signal.get("source_credibility", 0.5),
    })

    if actors:
        influence = actor_influence(actors)
        gravity_score = _clamp(gravity_score * influence)

    signal_out = dict(signal)
    signal_out["gravity_score"] = gravity_score
    signal_out["feedback_influence"] = actors and actor_influence(actors) or 1.0
    return signal_out

    """Return signal with gravity_score in output dict."""
    gravity_score = calculate_gravity({
        "severity": signal.get("severity", 0.0),
        "actor_importance": signal.get("actor_importance", 0.0),
        "frequency": signal.get("frequency", 0.0),
        "sentiment": signal.get("sentiment", 0.0),
        "source_credibility": signal.get("source_credibility", 0.5),
    })
    signal_out = dict(signal)
    signal_out["gravity_score"] = gravity_score
    return signal_out