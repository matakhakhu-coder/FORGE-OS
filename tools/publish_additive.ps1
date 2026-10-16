# Additive publish: build the site from the current database, then put every tracked
# file under dist/ back to its committed state. What is left (untracked files) is the
# new report's pages only, so the frozen timeline, feeds, search index and photos stay
# exactly as they are. Nothing is committed or pushed here.
#
# Run from anywhere:  powershell -File tools\publish_additive.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # tools\ -> repo root

if (git status --short dist) {
    Write-Host "dist/ has uncommitted changes. Commit or restore them first, then re-run."
    git status --short dist
    exit 1
}

Write-Host "--- building (no --deploy, nothing is pushed) ---"
python tools\publish.py
if ($LASTEXITCODE -ne 0) { Write-Host "publish.py failed"; exit 1 }

Write-Host "--- restoring the frozen front end ---"
git restore dist

Write-Host "--- new files left under dist/ (review, then git add only the ones you want) ---"
git status --short dist
Write-Host ""
Write-Host "Expect the article page and the case page. Entity pages named after unrelated people"
Write-Host "are build leftovers: do not add them."
