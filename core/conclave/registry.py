from dataclasses import dataclass
from typing import List, Literal, Dict, Any


@dataclass
class AnalysisResult:
    entities: List[str]
    intent: str
    gravity: float
    recommendation: Literal["IGNORE", "MONITOR", "ESCALATE"]
    confidence: float
    provenance: Dict[str, Any]
