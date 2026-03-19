from typing import List
from .registry import AnalysisResult


def run_conclave(results: List[AnalysisResult]) -> AnalysisResult:
    if not results:
        return AnalysisResult([], "unknown", 0.0, "IGNORE", 0.0, {})

    # Merge entities
    entities = list(set(e for r in results for e in r.entities))

    # Average gravity
    gravity = sum(r.gravity for r in results) / len(results)

    # Majority vote
    recs = [r.recommendation for r in results]
    final_rec = max(set(recs), key=recs.count)

    # Average confidence
    confidence = sum(r.confidence for r in results) / len(results)

    return AnalysisResult(
        entities=entities,
        intent=results[0].intent,
        gravity=gravity,
        recommendation=final_rec,
        confidence=confidence,
        provenance={
            "sources": [r.provenance for r in results]
        }
    )
