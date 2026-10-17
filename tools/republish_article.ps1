# Re-seed the report from docs\reports, rebuild, keep ONLY the article page, verify. Does not commit or push.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$page = "dist\articles\limpopo-procurement-arrests-v2.html"
$seed = python tools\seed_limpopo_v2.py --write
if (-not $seed -or (($seed -join "`n") -notmatch "WROTE")) { Write-Host "seed did not report WROTE - stop"; $seed; exit 1 }
$seed | Select-String -Pattern 'N_PINNED|GRAVITY|COMMIT|WROTE'
python tools\publish.py | Select-String -Pattern 'articles/|cases/'
Copy-Item $page "$env:TEMP\v2page.html" -Force
git restore dist
Copy-Item "$env:TEMP\v2page.html" $page -Force
python tools\unwrap_entities.py $page
git status --short dist