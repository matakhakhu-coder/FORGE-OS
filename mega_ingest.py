"""
FORGE Mega Runner with Conclave Progress Summary
Refactored for nested asyncio and coroutine execution.
"""
import asyncio
import nest_asyncio
import sqlite3
import time
import logging
from core.db.connection import DB_PATH
from core.pipeline.ingest import ingest_signal

# Patch asyncio to allow nested event loops from collectors
nest_asyncio.apply()

# Samaritan-style logging setup
logging.basicConfig(
    level=logging.INFO, 
    format="[SYSTEM_INQUIRY] %(asctime)s >> %(message)s", 
    datefmt="%H:%M:%S"
)
log = logging.getLogger("mega_runner")

# Import all collectors
from forage.collectors import (
    gdelt_collector,
    civic_intel_collector,
    firms_collector,
    rss_collector,
    earthquake_collector,
    usgs_collector
)
# Import engines & processors
from forage.engines import (
    anomaly_engine, cluster_engine, correlation_engine, decay_engine,
    evolution_engine, graph_engine
)
from forage.processors import (
    artifact_processor, forensic_processor,
    ner_processor, sentinel
)

# ------------------------
# 1️⃣ Run all collectors (Async Coroutines)
# ------------------------
async def run_all_collectors():
    log.info("S A M A R I T A N . O N L I N E")
    log.info("DETERMINING_RELEVANCE...")
    
    # We await .async_main() directly to stay in the same event loop
    tasks = [
        gdelt_collector.async_main(max_rows=5000), 
        civic_intel_collector.async_main(),
        firms_collector.async_main(),
        rss_collector.async_main(),
        earthquake_collector.async_main(),
        usgs_collector.async_main()
    ]
    
    log.info("Starting concurrent collection sequence...")
    # return_exceptions=True prevents one failing collector from killing the whole run
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Collection sequence finalized.")

# ------------------------
# 2️⃣ Run engines & processors (Synchronous)
# ------------------------
def run_engines_processors():
    log.info("Executing heuristic analysis engines...")
    artifact_processor.process_all()
    cluster_engine.run_all()
    ner_processor.process_all()
    anomaly_engine.run_all()
    correlation_engine.run_all()
    decay_engine.run_all()
    evolution_engine.run_all()
    graph_engine.build_graphs()
    sentinel.process_alerts()
    log.info("Heuristic analysis complete.")

# ------------------------
# 3️⃣ Full ingestion: Conclave + Entity + Escalation
# ------------------------
def run_full_ingest(batch_size=50, sleep_interval=0.1):
    # Use a longer timeout and WAL mode for concurrent DB access
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    signals = cur.execute("SELECT * FROM signals ORDER BY timestamp ASC").fetchall()
    # Convert to plain dicts immediately, then close — ingest_signal opens
    # its own connection internally; holding this one open causes "db locked".
    signals = [dict(row) for row in signals]
    conn.close()

    total = len(signals)
    log.info(f"CALCULATING_RESPONSE: Processing {total} signals...")

    actors_created = 0
    events_created = 0
    cases_created = 0

    for idx, row in enumerate(signals, 1):
        signal_id = row['signal_id']
        try:
            result = ingest_signal(row)
            meta = result.get('conclave_meta', {}) if result else {}
            
            actors_created += len(meta.get('actors', []))
            events_created += len(meta.get('events', []))
            cases_created += 1 if meta.get('case_id') else 0
            
        except Exception as e:
            log.error(f"FAILURE_AT_SIGNAL_{signal_id}: {e}")

        if idx % batch_size == 0:
            log.info(f"Ingest Progress: {idx}/{total} | Stabilizing...")
            time.sleep(sleep_interval)

    print(f"\n--- CONCLAVE PROGRESS SUMMARY ---")
    print(f"Signals Analyzed:    {total}")
    print(f"Actors Identified:   {actors_created}")
    print(f"Events Constructed:  {events_created}")
    print(f"Cases Escalated:     {cases_created}")
    print(f"---------------------------------\n")

# ------------------------
# 4️⃣ Runner entrypoint
# ------------------------
if __name__ == "__main__":
    start_time = time.time()
    
    # Run Async Phase
    asyncio.run(run_all_collectors())
    
    # Run Sync Phase
    run_engines_processors()
    
    # Run Conclave Logic Phase
    run_full_ingest(batch_size=50, sleep_interval=0.2)
    
    end_time = time.time()
    log.info(f"MEGA_RUNNER_COMPLETE in {end_time - start_time:.2f}s")