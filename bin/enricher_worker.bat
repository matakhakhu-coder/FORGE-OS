@echo off
REM FORGE Content Enricher Worker
REM Drains enrichment_queue — fetch full article text, re-score gravity
REM Schedule: every 2 hours (or run manually after heavy ingest)
cd /d "%~dp0\.."
python forage\processors\content_enricher.py --limit 100
