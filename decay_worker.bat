@echo off
title FORGE Decay Engine Monitor
:loop
cls
echo [%date% %time%] Running Signal Decay Cycle...
python C:\Users\matam\Projects\FORGE\forage\engines\decay_engine.py
echo.
echo Cycle Complete. Sleeping for 6 hours...
timeout /t 21600 /nobreak
goto loop