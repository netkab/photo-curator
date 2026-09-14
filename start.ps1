$base = $PSScriptRoot
$be   = "$base\backend"
$fe   = "$base\frontend"

# Refuse occupied ports. Never terminate unrelated Python/Node processes.
foreach ($port in @(8077, 5177)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Close the existing server before starting Photo Curator."
    }
}

# Backend — uvicorn in a new window
Write-Host "Starting backend on :8077 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command",
  "cd '$be' ; .\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8077" `
  -WindowStyle Normal

# Wait until port 8077 is actually listening before starting Vite
Write-Host "Waiting for backend to be ready..." -ForegroundColor DarkGray
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    $listening = netstat -ano | findstr "127.0.0.1:8077" | findstr "LISTENING"
    if ($listening) { $ready = $true; break }
}
if ($ready) { Write-Host "Backend ready." -ForegroundColor Green }
else         { Write-Host "Backend slow to start — continuing anyway." -ForegroundColor Yellow }

# Frontend — Vite in a new window (Normal so errors are visible)
Write-Host "Starting frontend on :5177 ..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command",
  "cd '$fe' ; cmd /c npm run dev" `
  -WindowStyle Normal

Write-Host ""
Write-Host "Backend  ->  http://127.0.0.1:8077/docs" -ForegroundColor Green
Write-Host "Frontend ->  http://localhost:5177"       -ForegroundColor Green
Write-Host "Close each terminal window to stop the servers."
