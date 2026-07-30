# Creates a Desktop shortcut "Photo Curator.lnk" -> scripts\PhotoCurator.exe.
#
# PhotoCurator.exe is a single-window launcher that owns both dev servers and has
# the app icon embedded, so it's directly pinnable and shows the custom icon on
# the taskbar. The shortcut is a convenience (Desktop entry + working dir); you
# can also pin the .exe directly. Run scripts\build-exe.ps1 first.

$base     = "C:\photo-curator"
$exe      = "$base\scripts\PhotoCurator.exe"
$linkPath = [Environment]::GetFolderPath("Desktop") + "\Photo Curator.lnk"

if (-not (Test-Path $exe)) {
    Write-Host "PhotoCurator.exe not found — building it first..." -ForegroundColor Yellow
    & "$base\scripts\build-exe.ps1"
}

$shell = New-Object -ComObject WScript.Shell
$sc    = $shell.CreateShortcut($linkPath)
$sc.TargetPath       = $exe
$sc.WorkingDirectory = $base
$sc.IconLocation     = "$exe,0"
$sc.Description       = "Launch Photo Curator backend (:8077) + frontend (:5177) in one window"
$sc.Save()

Write-Host "Created shortcut:" -ForegroundColor Green
Write-Host "  $linkPath"
Write-Host ""
Write-Host "Pin to taskbar: right-click the Desktop icon (or PhotoCurator.exe) -> Pin to taskbar." -ForegroundColor Cyan
Write-Host "On Windows 11 it's under 'Show more options'." -ForegroundColor DarkGray
