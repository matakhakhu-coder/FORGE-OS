from __future__ import annotations

import threading
from typing import Dict

_COLLECTOR_REGISTRY: Dict[str, Dict] = {}
_DEAD_NODES: list = []

_KILL_FLAGS: Dict[int, bool] = {}

_PIPELINE_LOCK = threading.Lock()
_PIPELINE_ACTIVE: Dict[str, bool] = {}
