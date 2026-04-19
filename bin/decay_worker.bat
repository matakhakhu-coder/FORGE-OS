@echo off
title FORGE Decay Engine Monitor

:: Change to the project root (one level up from /bin)
cd /d "%~dp0.."

:loop
cls
echo [%date% %time%] Running Signal Decay Cycle...
python forage\engines\decay_engine.py
echo.
echo Cycle Complete. Sleeping for 6 hours...
timeout /t 21600 /nobreak
goto loop
