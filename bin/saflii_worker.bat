@echo off
REM FORGE SAFLII Court Record Collector
REM Queries SAFLII for court records involving active case actors
REM Schedule: daily or manually after new actors are added to cases
cd /d "%~dp0\.."
python forage\collectors\saflii_collector.py
