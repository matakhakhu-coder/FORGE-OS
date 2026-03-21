"""
FORGE — Conclave Engine  (FMS-patched)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Minimal patch: run_conclave() is untouched.
Added: run_conclave_with_modules() which feeds module engine results
into the existing run_conclave() function.

ZERO breaking changes to existing callers of run_conclave().
"""

from typing import List
from .registry import AnalysisResult


def run_conclave(results: List[AnalysisResult]) -> AnalysisResult:
    """Original function — completely unchanged."""
    if not results:
        return AnalysisResult([], "unknown", 0.0, "IGNORE", 0.0, {})

    entities   = list(set(e for r in results for e in r.entities))
    gravity    = sum(r.gravity for r in results) / len(results)
    recs       = [r.recommendation for r in results]
    final_rec  = max(set(recs), key=recs.count)
    confidence = sum(r.confidence for r in results) / len(results)

    return AnalysisResult(
        entities=entities,
        intent=results[0].intent,
        gravity=gravity,
        recommendation=final_rec,
        confidence=confidence,
        provenance={"sources": [r.provenance for r in results]},
    )


def run_conclave_with_modules(
    results: List[AnalysisResult],
    signal: dict,
) -> AnalysisResult:
    """
    FMS extension of run_conclave.

    Collects AnalysisResult objects from all registered module engines,
    merges them with the existing results list, then passes the combined
    list to the original run_conclave() function.

    If no modules are loaded, behaviour is identical to run_conclave().
    If any module engine fails, its result is skipped — never propagated.
    """
    # Import here to avoid circular imports at module load time
    try:
        from core.conclave.context import get_context
        context = get_context()
        engines = context.get_engines()
    except Exception:
        engines = {}

    module_results: List[AnalysisResult] = []

    for engine_name, engine_fn in engines.items():
        try:
            result = engine_fn(signal)
            if result is not None and isinstance(result, AnalysisResult):
                module_results.append(result)
        except Exception as exc:
            import logging
            logging.getLogger("forge.conclave").error(
                f"[FMS] Module engine '{engine_name}' failed: {exc}"
            )

    combined = results + module_results
    return run_conclave(combined)