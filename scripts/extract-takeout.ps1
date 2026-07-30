# Extract Google Takeout tgz archives in-place.
# Extracts all *.tgz in $TakeoutDir into the same folder, then updates backend\.env with the
# correct TAKEOUT_DIR path.

param(
  [string]$TakeoutDir = "D:\Takeout"
)

$ErrorActionPreference = "Stop"
$envFile = "C:\photo-curator\backend\.env"

# --- Space check ---
$archives = Get-ChildItem $TakeoutDir -Filter "*.tgz" | Sort-Object Name
if ($archives.Count -eq 0) { Write-Host "No .tgz files found in $TakeoutDir"; exit 1 }

$totalGb  = [math]::Round(($archives | Measure-Object Length -Sum).Sum / 1GB, 1)
$freeGb   = [math]::Round((Get-PSDrive ($TakeoutDir[0])).Free / 1GB, 1)
$neededGb = $totalGb   # photos/videos barely compress — extracted ≈ archive size

Write-Host ""
Write-Host "=== Google Takeout Extraction ===" -ForegroundColor Cyan
Write-Host "Archives : $($archives.Count) files, $totalGb GB total"
Write-Host "Free on  : $($TakeoutDir[0]): — $freeGb GB"
Write-Host "Needed   : ~$neededGb GB  (margin: $([math]::Round($freeGb - $neededGb, 1)) GB)"
Write-Host ""

if ($freeGb - $neededGb -lt 5) {
  Write-Host "WARNING: Less than 5 GB headroom — extraction may run out of space." -ForegroundColor Yellow
  $ans = Read-Host "Continue anyway? (y/N)"
  if ($ans -ne "y") { exit 1 }
}

# Check tar is available (built into Windows 10 1803+)
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
  Write-Host "ERROR: 'tar' not found. Install Windows 10 v1803+ or add 7-zip to PATH." -ForegroundColor Red
  exit 1
}

# --- Extract each archive ---
$i = 0
$startTime = Get-Date
foreach ($arc in $archives) {
  $i++
  $sizeGb = [math]::Round($arc.Length / 1GB, 1)
  Write-Host ""
  Write-Host "[$i/$($archives.Count)] Extracting $($arc.Name) ($sizeGb GB)..." -ForegroundColor Cyan

  $t = Get-Date
  # tar -xzf extracts into $TakeoutDir, creating Takeout\Google Photos\... there
  tar -xzf $arc.FullName -C $TakeoutDir

  $elapsed = [math]::Round(((Get-Date) - $t).TotalSeconds)
  Write-Host "  Done in $($elapsed)s" -ForegroundColor Green

  # Show remaining free space after each archive
  $nowFree = [math]::Round((Get-PSDrive ($TakeoutDir[0])).Free / 1GB, 1)
  Write-Host "  Free space remaining: $nowFree GB"
}

$totalElapsed = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
Write-Host ""
Write-Host "=== Extraction complete in $totalElapsed min ===" -ForegroundColor Green

# --- Locate the extracted Google Photos folder ---
# Google Takeout always extracts to: <dest>\Takeout\Google Photos\
$photosDir = Join-Path $TakeoutDir "Takeout"
if (Test-Path (Join-Path $photosDir "Google Photos")) {
  Write-Host "Photos found at: $photosDir"
} else {
  # Some exports use locale-specific names — find the first subfolder
  $sub = Get-ChildItem $photosDir -Directory | Select-Object -First 1
  if ($sub) { $photosDir = $sub.FullName }
  Write-Host "Photos found at: $photosDir"
}

# --- Update backend\.env ---
if (Test-Path $envFile) {
  $content = Get-Content $envFile -Raw
  if ($content -match "TAKEOUT_DIR=") {
    $content = $content -replace "TAKEOUT_DIR=.*", "TAKEOUT_DIR=$photosDir"
  } else {
    $content = "TAKEOUT_DIR=$photosDir`n" + $content
  }
  Set-Content $envFile $content -NoNewline
  Write-Host ""
  Write-Host "Updated backend\.env  →  TAKEOUT_DIR=$photosDir" -ForegroundColor Green
} else {
  Write-Host ""
  Write-Warning "backend\.env not found — set TAKEOUT_DIR=$photosDir manually."
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start the app:   cd photo-curator ; .\start.ps1"
Write-Host "  2. Click 'Ingest Takeout' on the Catalog page"
Write-Host "     OR run: cd backend ; .\.venv\Scripts\python -m app.cli ingest"
