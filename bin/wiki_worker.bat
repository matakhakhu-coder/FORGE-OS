@echo off
title FORGE Wiki Pipeline
echo ==================================================
echo FORGE Wiki — Knowledge Base Synthesis Pipeline
echo ==================================================
echo.

:: Change to the project root (one level up from /bin)
cd /d "%~dp0.."

echo [0/3] Checking Database Integrity...
python tools\init_wiki.py
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: Schema initialization failed.
    pause
    exit /b %ERRORLEVEL%
)
echo [OK] Database ready.
echo.

echo [1/3] Initializing Wiki Compiler (Drafting Dossiers)...
python -m wiki.processors.wiki_compiler
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: Wiki Compiler failed.
    pause
    exit /b %ERRORLEVEL%
)
echo [OK] Compiler finished.
echo.

echo [2/3] Initializing Link Engine (Building Graph)...
python -m wiki.engines.wiki_link_engine
if %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: Link Engine failed.
    pause
    exit /b %ERRORLEVEL%
)
echo [OK] Graph built.
echo.

echo ==================================================
echo Pipeline Complete. FORGE Knowledge Base Updated.
echo ==================================================
pause
