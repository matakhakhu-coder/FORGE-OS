"""
FORAGE — GDELT 2.0 Signal Collector (Async/RAM-only)
====================================================
Phase 15.5 refactor to high-concurrency with aiohttp/BytesIO/zipfile/pandas.

Requirements implemented:
- Zero-disk temp (zip -> BytesIO -> pandas)
- SA filtering: country code 'SF' in columns 44 and 53
- DB integration uses open_db + loop.run_in_executor (batch commits)
- Logging includes _log_run_safe/log_run
- BASE_DIR from core/db/connection.py
- Class-based: GDELTForgeCollector
- Methods: fetch_latest_url, download_and_process, integrate_to_forge
"""

import argparse
import asyncio
import hashlib
import io
import json
import logging
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root in path for module import
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiohttp
import pandas as pd

from forage.processors.artifact_processor import ProcessorManager
from forage.processors.signal_interpreter import SignalInterpreter
from forage.processors.entity_resolver import EntityResolver
from forage.processors.event_constructor import EventConstructor
from forage.engines.gravity_engine import score_signal
from forage.engines.case_engine import evaluate_case
from forage.engines.feedback_engine import apply_feedback

# Pipeline logger helper (keeps existing "safe" behavior)
def _log_run_safe(*args, **kwargs):
    import importlib.util as _ilu
    from pathlib import Path as _P

    _logger_path = (
        _P(__file__).resolve().parent.parent.parent
        / "forage"
        / "utils"
        / "pipeline_logger.py"
    )
    if str(_logger_path.parent.parent) not in sys.path:
        sys.path.insert(0, str(_logger_path.parent.parent))
    try:
        _spec = _ilu.spec_from_file_location("pipeline_logger", str(_logger_path))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.log_run(*args, **kwargs)
    except Exception:
        pass  # telemetry must not break run

log_run = _log_run_safe

# Core path logic using shared BASE_DIR
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db.connection import BASE_DIR as CORE_BASE_DIR

DB_PATH = CORE_BASE_DIR / "database.db"

logging.basicConfig(
    level=logging.INFO,
    format="[GDELT %(levelname)s %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gdelt_collector")

GDELT_V2_DATA = "https://data.gdeltproject.org/gdeltv2/"
GDELT_LASTUPDATE = GDELT_V2_DATA + "lastupdate.txt"
GDELT_ZIP_TEMPLATE = GDELT_V2_DATA + "gdeltv2-{update_stamp}.export.CSV.zip"

SOURCE_NAME = "gdelt"
STREAM = "GLOBAL"
SOURCE_TYPE = "live"


def open_db(path: Optional[Path] = None) -> sqlite3.Connection:
    db = Path(path or DB_PATH)
    if not db.exists():
        raise FileNotFoundError(
            f"[FORGE ERROR] Database missing at {db}. Run 'python app.py --init-db'"
        )
    conn = sqlite3.connect(str(db), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class GDELTForgeCollector:
    def __init__(self, db_path: Optional[Path] = None, verify_ssl: bool = True):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.last_update_url = GDELT_LASTUPDATE
        self.verify_ssl = verify_ssl  # config: avoid SSL cert errors

    async def fetch_latest_url(self) -> Optional[str]:
        """Non-blocking check of GDELT lastupdate.txt; returns zip URL or None."""
        log.info("Fetching GDELT lastupdate URL: %s", self.last_update_url)
        try:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.get(self.last_update_url, timeout=20) as resp:
                    resp.raise_for_status()
                    raw = (await resp.text()).strip()
            if not raw:
                raise ValueError("Empty lastupdate.txt payload")

            # lastupdate.txt format: "<size> <sha1> <url>"
            # Choose the primary export URL to avoid invalid template mapping.
            zip_url = None
            for line in raw.splitlines():
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                candidate = parts[2]
                if candidate.lower().endswith(".export.csv.zip"):
                    zip_url = candidate
                    break

            if not zip_url:
                raise ValueError(f"Could not extract export ZIP URL from lastupdate contents: {raw[:256]}")

            log.info("Latest GDELT zip URL: %s", zip_url)
            return zip_url
        except aiohttp.ClientConnectorCertificateError as exc:
            if self.verify_ssl:
                log.warning(
                    "Certificate validation failure: %s; retry with --insecure or install certifi",
                    exc,
                )
                log.warning("Try: pip install certifi")
            log.error("Error fetching latest URL: %s", exc)
            return None
        except Exception as exc:
            log.error("Error fetching latest URL: %s", exc)
            return None

    async def download_and_process(self, zip_url: str, max_rows: Optional[int] = None):
        """
        Download ZIP into memory and parse CSV with pandas.
        Keep only SA rows where col44 or col53 is 'SF'.
        """
        log.info("Downloading GDELT ZIP: %s", zip_url)
        try:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.get(zip_url, timeout=120) as resp:
                    resp.raise_for_status()
                    raw_bytes = await resp.read()
        except aiohttp.ClientConnectorCertificateError as exc:
            if self.verify_ssl:
                log.warning(
                    "Certificate validation failure: %s; retry with --insecure or install certifi",
                    exc,
                )
                log.warning("Try: pip install certifi")
            log.error("Download failure: %s", exc)
            return pd.DataFrame()
        except Exception as exc:
            log.error("Download failure: %s", exc)
            return pd.DataFrame()

        try:
            buf = io.BytesIO(raw_bytes)
            with zipfile.ZipFile(buf, "r") as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not names:
                    log.error("No CSV file inside GDELT ZIP")
                    return pd.DataFrame()
                csv_name = names[0]
                with zf.open(csv_name) as csv_file:
                    df = pd.read_csv(
                        csv_file,
                        sep="\t",
                        header=None,
                        low_memory=False,
                        dtype=str,
                        keep_default_na=False,
                        na_values=[],
                    )
        except Exception as exc:
            log.error("CSV parse failure in-memory: %s", exc)
            return pd.DataFrame()

        log.info("Loaded %d rows from CSV", len(df))
        if max_rows:
            df = df.head(max_rows)

        # filter by SA codes in columns 44 and 53 (0-based)
        df_sa = df[(df.iloc[:, 44].fillna("").str.upper() == "SF") | (df.iloc[:, 53].fillna("").str.upper() == "SF")]
        log.info("Filtered South African signals: %d rows", len(df_sa))
        return df_sa

    async def integrate_to_forge(self, sa_df: pd.DataFrame, batch_size: int = 512):
        """Batch-insert OR IGNORE into signals table using run_in_executor to avoid SQLite blocking."""
        if sa_df.empty:
            log.warning("No SA records to integrate")
            return {"inserted": 0, "skipped": 0, "errors": 0}

        EVENT_CODE_LABELS = {
            141: "PROTEST",
            143: "STRIKE",
            145: "VIOLENT PROTEST",
            180: "ASSAULT",
            190: "COUP",
            193: "FIREBOMBING",
            200: "MASS VIOLENCE",
        }

        def _execute_batch():
            stats = {"inserted": 0, "skipped": 0, "errors": 0}
            conn = open_db(self.db_path)
            try:
                rows = []
                for idx, row in sa_df.iterrows():
                    try:
                        global_id = str(row.iloc[0] if 0 in row.index else "")
                        if global_id:
                            external_id = f"gdelt:{global_id}"
                            signal_id = "gdelt-" + hashlib.sha256(global_id.encode("utf-8")).hexdigest()[:36]
                        else:
                            external_seed = str(row.iloc[0] if 0 in row.index else idx) + str(row.iloc[1] if 1 in row.index else "")
                            external_id = "gdelt:" + hashlib.sha1(external_seed.encode("utf-8")).hexdigest()[:20]
                            signal_id = "gdelt-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:36]

                        # status / timestamp fallback
                        timestamp = datetime.now(timezone.utc).isoformat()
                        if 1 in row.index and row.iloc[1]:
                            try:
                                dt = datetime.strptime(str(row.iloc[1]), "%Y%m%d%H%M%S")
                                timestamp = dt.replace(tzinfo=timezone.utc).isoformat()
                            except Exception:
                                pass

                        raw_title = (row.iloc[2] if 2 in row.index and row.iloc[2] else "GDELT SA Signal").strip()

                        # Event code mapping
                        event_label = "UNKNOWN"
                        if 26 in row.index and row.iloc[26]:
                            try:
                                code_val = int(float(row.iloc[26]))
                                event_label = EVENT_CODE_LABELS.get(code_val, "UNKNOWN")
                            except Exception:
                                event_label = "UNKNOWN"

                        title = f"[{event_label}] {raw_title}" if event_label else raw_title

                        # Goldstein scale
                        goldstein = 0.0
                        if 30 in row.index and row.iloc[30]:
                            try:
                                goldstein = float(row.iloc[30])
                            except Exception:
                                goldstein = 0.0

                        is_priority = 1 if goldstein < -5.0 else 0

                        content = (
                            f"actor1={row.iloc[44] if 44 in row.index else ''}; "
                            f"actor2={row.iloc[53] if 53 in row.index else ''}"
                        )

                        lat = None
                        lng = None
                        if 8 in row.index and row.iloc[8]:
                            try:
                                lat = float(row.iloc[8])
                            except Exception:
                                lat = None
                        if 9 in row.index and row.iloc[9]:
                            try:
                                lng = float(row.iloc[9])
                            except Exception:
                                lng = None

                        meta = {
                            "row_index": int(idx),
                            "global_id": global_id,
                            "event_code": row.iloc[26] if 26 in row.index else None,
                            "event_label": event_label,
                            "goldstein": goldstein,
                            "actor1country": row.iloc[44] if 44 in row.index else None,
                            "actor2country": row.iloc[53] if 53 in row.index else None,
                        }

                        rows.append(
                            (
                                signal_id,
                                SOURCE_NAME,
                                external_id,
                                title[:500],
                                content[:1000],
                                lat,
                                lng,
                                timestamp,
                                "raw",
                                json.dumps(meta, ensure_ascii=False),
                                is_priority,
                                STREAM,
                                SOURCE_TYPE,
                            )
                        )
                        if len(rows) >= batch_size:
                            conn.executemany(
                                """
                                INSERT OR IGNORE INTO signals
                                    (signal_id, source, external_id, title, content,
                                     lat, lng, timestamp, status, metadata_json,
                                     is_priority, stream, source_type)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                rows,
                            )
                            stats["inserted"] += conn.total_changes
                            rows.clear()
                    except sqlite3.IntegrityError:
                        stats["skipped"] += 1
                    except Exception:
                        stats["errors"] += 1

                if rows:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO signals
                            (signal_id, source, external_id, title, content,
                             lat, lng, timestamp, status, metadata_json,
                             is_priority, stream, source_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                    stats["inserted"] += conn.total_changes

                conn.commit()
            finally:
                conn.close()
            return stats

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _execute_batch)

    async def run(self, max_rows: Optional[int] = None, dry_run: bool = False):
        started_at = datetime.now(timezone.utc).isoformat()
        zip_url = await self.fetch_latest_url()
        if not zip_url:
            log.error("Unable to resolve latest GDELT ZIP URL.")
            return 1

        sa_df = await self.download_and_process(zip_url, max_rows=max_rows)
        if dry_run:
            log.info("[DRY-RUN] %d SA rows ready (no DB write)", len(sa_df))
            log_run(
                pipeline="gdelt_collector",
                status="dry-run",
                start_time=started_at,
                records_processed=len(sa_df),
            )
            return 0

        # self-describing enrichment step
        interpreter = SignalInterpreter()
        sample_signals = []
        for index, row in sa_df.iterrows():
            sample_signals.append({
                "signal_id": f"row-{index}",
                "title": row.iloc[2] if 2 in row.index else "",
                "content": row.iloc[26] if 26 in row.index else "",
                "metadata_json": "",
            })
        interpreted = interpreter.batch_interpret(sample_signals)
        log.info("Interpreted sample signals: %s", interpreted[:3])

        # Resolve actors into DB and apply gravity/case/feedback control loop
        conn = open_db(self.db_path)
        resolver = EntityResolver(conn)
        for i, metadata in enumerate(interpreted):
            resolved = resolver.resolve_actors(metadata.get("actors", []))
            log.info("Actor resolution for signal %s: %s", metadata.get("raw_signal_id"), resolved)

            g = score_signal(metadata, actors=resolved)
            c = evaluate_case(g, linked_actors=resolved, linked_events=[])
            fb = apply_feedback(g, resolved, c, conn=conn)
            log.info("Gravity/case/feedback for signal %s: gravity=%s; case=%s; feedback=%s",
                     metadata.get("raw_signal_id"), g.get("gravity_score"), c.get("decision"), fb)

        # Turn interpreted signals into artifacts for continuity
        manager = ProcessorManager(db_path=self.db_path)
        for signal in sample_signals:
            artifact_id = manager.signal_to_artifact(signal)
            log.info("Signal %s mapped to artifact %s", signal.get("signal_id"), artifact_id)

        # Maintain event continuity from signal semantics
        eventer = EventConstructor()
        events = eventer.batch_construct(sample_signals, interpreted)
        log.info("Constructed events: %s", events)

        manager.close()
        conn.close()

        stats = await self.integrate_to_forge(sa_df)
        log.info("Integration stats: %s", stats)
        log_run(
            pipeline="gdelt_collector",
            status="success",
            start_time=started_at,
            records_processed=len(sa_df),
            inserted=stats.get("inserted", 0),
            skipped=stats.get("skipped", 0),
            errors=stats.get("errors", 0),
        )
        return 0


def parse_args():
    p = argparse.ArgumentParser(description="Async GDELT collector (FORGE).")
    p.add_argument("--db", type=str, default=None, help="Optional override DB path")
    p.add_argument("--max-rows", type=int, default=None, help="Read max CSV rows while testing")
    p.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification (for self-signed or tunneled environments)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    collector = GDELTForgeCollector(
        db_path=Path(args.db) if args.db else None,
        verify_ssl=not args.insecure,
    )
    return asyncio.run(collector.run(max_rows=args.max_rows, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

# --- MEGA RUNNER ADAPTER ---
async def async_main(max_rows=5000, **kwargs):
    """
    Async entry point for mega_ingest.py.
    Calls collector.run() directly — bypasses main() which uses asyncio.run()
    and would conflict with the already-running event loop.
    """
    try:
        collector = GDELTForgeCollector(db_path=None, verify_ssl=True)
        await collector.run(max_rows=max_rows)
    except Exception as e:
        print(f"[ERROR] async_main failed in gdelt_collector.py: {e}")