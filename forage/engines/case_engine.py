"""FORAGE Case Engine (Autonomous escalation)

Decides whether to create a case from gravity-scored signals.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def construct_case(signal: Dict[str, Any], linked_actors: List[Dict[str, Any]], linked_events: List[Dict[str, Any]], cluster_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "case_id": str(uuid.uuid4()) if "case_id" not in signal else signal["case_id"],
        "title": signal.get("title", "Case from GDELT signal"),
        "gravity_score": signal.get("gravity_score", 0.0),
        "status": "new",
        "linked_actors": linked_actors,
        "linked_events": linked_events,
        "signal_cluster": cluster_id or signal.get("cluster_id"),
        "timeline_start": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "source_signal_id": signal.get("signal_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_case(signal: Dict[str, Any], linked_actors: List[Dict[str, Any]] = None, linked_events: List[Dict[str, Any]] = None, cluster_id: Optional[str] = None) -> Dict[str, Any]:
    """Return representation of escalation decision."""
    if linked_actors is None:
        linked_actors = []
    if linked_events is None:
        linked_events = []

    gravity = float(signal.get("gravity_score", 0.0))

    if gravity > 0.8:
        decision = "create_case"
        case = construct_case(signal, linked_actors, linked_events, cluster_id)
        case["decision"] = "CREATE CASE"
        return case

    if gravity > 0.6:
        return {
            "decision": "FLAG MONITOR",
            "gravity_score": gravity,
            "signal_id": signal.get("signal_id"),
            "linked_actors": linked_actors,
            "linked_events": linked_events,
            "signal_cluster": cluster_id or signal.get("cluster_id"),
            "timeline_start": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        }

    return {
        "decision": "STORE ONLY",
        "gravity_score": gravity,
        "signal_id": signal.get("signal_id"),

        "linked_actors": linked_actors,
        "linked_events": linked_events,
        "signal_cluster": cluster_id or signal.get("cluster_id"),
        "timeline_start": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
    }
