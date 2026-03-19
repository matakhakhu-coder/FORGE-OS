import re
from datetime import datetime
from typing import Any, Dict, List

EVENT_TRIGGERS = [
    "arrested", "charged", "signed", "launched", "attacked", "detained", "released"
]

def _normalize_text(text: str) -> str:
    return (text or "").lower().strip()


def _contains_trigger(text: str) -> bool:
    lo = _normalize_text(text)
    return any(trigger in lo for trigger in EVENT_TRIGGERS)


def _timestamp_from_signal(signal: Dict[str, Any]) -> str:
    ts = signal.get("timestamp") or signal.get("date") or signal.get("created_at")
    if not ts:
        return datetime.utcnow().isoformat() + "Z"
    return ts


class EventConstructor:
    """Build events from interpreted signal metadata."""

    def __init__(self):
        self.version = "1.0"

    def construct(self, signal: Dict[str, Any], interpreted: Dict[str, Any]) -> Dict[str, Any] | None:
        signal_type = interpreted.get("type", "unknown")
        text = " ".join([str(signal.get("title", "")), str(signal.get("content", ""))])

        if signal_type == "event" or _contains_trigger(text):
            return {
                "type": interpreted.get("event_type", "unknown"),
                "involved_actors": interpreted.get("actors", []),
                "timestamp": _timestamp_from_signal(signal),
                "linked_artifacts": [signal.get("source_artifact_id")] if signal.get("source_artifact_id") else [],
                "origin_signal_id": signal.get("signal_id"),
                "severity": interpreted.get("severity", 0.0),
                "raw_title": signal.get("title"),
            }

        return None

    def batch_construct(self, signals: List[Dict[str, Any]], interpreteds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = []
        for signal, interpreted in zip(signals, interpreteds):
            ev = self.construct(signal, interpreted)
            if ev:
                events.append(ev)
        return events
