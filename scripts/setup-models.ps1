# Photo Curator — one-time environment setup (Windows / PowerShell).
# Creates the Python venv, installs deps (CUDA 11.8 torch for the GTX 1070), installs the frontend,
# pulls the Ollama vision model, and verifies external tools. Optional steps warn instead of failing.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venv = Join-Path $backend ".venv"
$py = Join-Path $venv "Scripts\python.exe"

Write-Host "== Photo Curator setup ==" -ForegroundColor Cyan
Write-Host "Root: $root"

# --- 1. Python venv ---
if (-not (Test-Path $py)) {
  Write-Host "`n[1/6] Creating Python venv..." -ForegroundColor Cyan
  python -m venv $venv
} else {
  Write-Host "`n[1/6] venv already exists." -ForegroundColor Green
}
& $py -m pip install --upgrade pip | Out-Null

# --- 2. Core deps ---
Write-Host "`n[2/6] Installing core backend dependencies..." -ForegroundColor Cyan
& $py -m pip install -r (Join-Path $backend "requirements.txt")

# --- 3. GPU / model deps (CUDA 11.8 for Pascal) ---
Write-Host "`n[3/6] Installing GPU + model stack (this is large)..." -ForegroundColor Cyan
try {
  & $py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
  & $py -m pip install transformers einops timm onnxruntime-gpu insightface scikit-learn open-clip-torch
  # Enhancement stack — basicsr/realesrgan/gfpgan can need a torchvision shim on newer versions.
  & $py -m pip install realesrgan gfpgan basicsr
} catch {
  Write-Warning "GPU/model install hit an error: $($_.Exception.Message)"
  Write-Warning "You can re-run, or install the failing package manually. The API still runs without it."
}

# --- 4. Ollama vision model ---
Write-Host "`n[4/6] Ollama model (moondream)..." -ForegroundColor Cyan
if (Get-Command ollama -ErrorAction SilentlyContinue) {
  ollama pull moondream
} else {
  Write-Warning "ollama not on PATH — install from https://ollama.com then run: ollama pull moondream"
}

# --- 5. Frontend ---
Write-Host "`n[5/6] Installing frontend..." -ForegroundColor Cyan
if (Get-Command npm -ErrorAction SilentlyContinue) {
  Push-Location $frontend ; npm install ; Pop-Location
} else {
  Write-Warning "npm not on PATH — install Node 18+ then run 'npm install' in frontend/."
}

# --- 6. Tool checks + .env ---
Write-Host "`n[6/6] Verifying external tools..." -ForegroundColor Cyan
function Check($name, $cmd, $url) {
  if (Get-Command $cmd -ErrorAction SilentlyContinue) { Write-Host "  [OK]  $name" -ForegroundColor Green }
  else { Write-Warning "  [MISSING] $name — get it from $url" }
}
Check "NVIDIA driver (nvidia-smi)" "nvidia-smi" "https://www.nvidia.com/Download/index.aspx"
Check "exiftool" "exiftool" "https://exiftool.org"
Check "ffmpeg"   "ffmpeg"   "https://ffmpeg.org/download.html"
Check "ffprobe"  "ffprobe"  "https://ffmpeg.org/download.html"

$envFile = Join-Path $backend ".env"
if (-not (Test-Path $envFile)) {
  Copy-Item (Join-Path $backend ".env.example") $envFile
  Write-Host "  Created backend\.env — edit TAKEOUT_DIR (and optional Google keys)." -ForegroundColor Yellow
}

Write-Host "`nEnvironment report:" -ForegroundColor Cyan
& $py -m app.cli env 2>$null

Write-Host "`nDone. Next:" -ForegroundColor Green
Write-Host '  cd "' $backend '" ; .\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8077'
Write-Host '  cd "' $frontend '" ; npm run dev'
