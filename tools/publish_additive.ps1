# Additive publish: build the site from the current database, then put every tracked
# file under dist/ back to its committed state. What is left (untracked files) is the
# new report's pages only, so the frozen timeline, feeds, search index and photos stay
# exactly as they are. Nothing is committed or pushed here.
#
# The Graph panel charts live inside index.html and count the pins in records.json.
# Both are restored together, so the charts stay frozen, still matching the frozen
# records. They only change in a normal publish (tools/publish.py --deploy).
#
# New files are listed with git ls-files and the repo's own .gitignore files, not
# git status: a machine-wide git ignore that lists dist/ hides them from git status
# (the same thing that made plain `git add dist/` fail in tools/publish.py --deploy).
#
# Run from anywhere:  powershell -File tools\publish_additive.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # tools\ -> repo root

function Get-NewDistFiles { git ls-files --others --exclude-per-directory=.gitignore -- dist }

$changed = git status --short --untracked-files=no dist
$new = Get-NewDistFiles
if ($changed -or $new) {
    Write-Host "dist/ has uncommitted changes. Commit or restore them first, then re-run."
    $changed; $new
    exit 1
}

Write-Host "--- building (no --deploy, nothing is pushed) ---"
python tools\publish.py
if ($LASTEXITCODE -ne 0) { Write-Host "publish.py failed"; exit 1 }

Write-Host "--- restoring the frozen front end ---"
git restore dist
Write-Host "Map, records and Graph charts are back to the committed versions (charts not refreshed)."

Write-Host "--- new files left under dist/ (review, then git add -f only the ones you want) ---"
Get-NewDistFiles
Write-Host ""
Write-Host "Expect the article page and the case page. Entity pages named after unrelated people"
Write-Host "are build leftovers: do not add them."
