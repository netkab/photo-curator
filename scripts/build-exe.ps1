# Compiles scripts\PhotoCurator.cs into PhotoCurator.exe with the app icon
# embedded. The .NET Framework C# compiler (csc.exe) ships with Windows, so no
# extra tooling is required. Re-run after editing PhotoCurator.cs or the icon.

$base = "C:\photo-curator"
$src  = "$base\scripts\PhotoCurator.cs"
$ico  = "$base\scripts\photo-curator.ico"
$out  = "$base\scripts\PhotoCurator.exe"

if (-not (Test-Path $ico)) {
    Write-Host "Icon missing — generating it..." -ForegroundColor Yellow
    & "$base\backend\.venv\Scripts\python.exe" "$base\scripts\make-icon.py"
}

$csc = Get-ChildItem "C:\Windows\Microsoft.NET\Framework64" -Recurse -Filter csc.exe `
        -ErrorAction SilentlyContinue | Select-Object -Last 1 -ExpandProperty FullName
if (-not $csc) { Write-Error "csc.exe (.NET Framework C# compiler) not found."; exit 1 }

Write-Host "Compiling PhotoCurator.exe ..." -ForegroundColor Cyan
& $csc /nologo /target:exe /platform:x64 /optimize+ `
       /win32icon:"$ico" /out:"$out" "$src"

if ($LASTEXITCODE -eq 0 -and (Test-Path $out)) {
    Write-Host "Built: $out" -ForegroundColor Green
} else {
    Write-Error "Build failed (csc exit $LASTEXITCODE)."
    exit 1
}
