"""Archive index size, today's pins and made-up pin counts (time-aware pins, Batch 6).

Reads the month files of a built dist (e.g. the oct/ build from
tests/browser/time_aware_dists.py), writes the archive again into a scratch
folder with this checkout's tools/map_archive.py, and reports the size of
archive/index.json: for the real pins, and for 1,000, 5,000 and 10,000
made-up pins copied from them (new ids, same October dates). Made-up pins
repeat real titles, so only the real pins' compressed size is meaningful.

Nothing here touches dist/ or the database.

    python tests/browser/archive_index_size.py BUILT_DIST [OUT.json]
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import map_archive as ma  # noqa: E402

TODAY = date(2026, 12, 15)   # every October pin is archived
COUNTS = (1000, 5000, 10000)


def _size(entries: list[dict]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        ma.write_archive(Path(tmp), entries, today=TODAY)
        body = (Path(tmp) / ma.ARCHIVE_DIR / ma.INDEX_NAME).read_bytes()
    return {"pins": len(entries), "bytes": len(body), "gzip_bytes": len(gzip.compress(body, 9))}


def measure(dist: Path) -> dict:
    real = ma.all_entries(Path(dist))
    out = {"real": _size(real), "made_up": []}
    for n in COUNTS:
        made = []
        for i in range(n):
            p = json.loads(json.dumps(real[i % len(real)]))
            p["signal_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"forge-ta6-size-{i}"))
            made.append(p)
        row = _size(made)
        row.pop("gzip_bytes")
        out["made_up"].append(row)
    return out


if __name__ == "__main__":
    res = measure(Path(sys.argv[1]))
    text = json.dumps(res, indent=1)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text + "\n", encoding="utf-8")
    print(text)
