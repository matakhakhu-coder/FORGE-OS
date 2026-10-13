"""FORAGE Case Engine (Autonomous escalation)

Decides whether to create a case from gravity-scored signals.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Sprint 6 (2026-08-02): this used to hardcode its own 0.6/0.8 thresholds,
# independently of escalation_engine.py's 0.35/0.55 -- the two drifted
# apart and were never reconciled. escalation_engine.py's thresholds are
# the ones actually calibrated to the real gravity_score range this
# pipeline produces (see its own header comment: "calibrated to FORAGE
# signal gravity range 0.0-0.70 typical"); this engine's stale bar sat
# upstream of it in the pipeline and silently discarded almost every
# signal's recommendation before escalation_engine's thresholds ever got
# a chance to run. Traced live against a 5,000-signal sample: gravity >
# 0.8 ("CREATE CASE") was reachable by 0.000% of signals, gravity > 0.6
# ("FLAG MONITOR") by only 0.62%. Importing the same constants instead of
# duplicating the numbers here means the two can't drift apart again.
from forage.engines.escalation_engine import ESCALATE_THRESHOLD, MONITOR_THRESHOLD


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

    if gravity >= ESCALATE_THRESHOLD:
        decision = "create_case"
        case = construct_case(signal, linked_actors, linked_events, cluster_id)
        case["decision"] = "CREATE CASE"
        return case

    if gravity >= MONITOR_THRESHOLD:
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
