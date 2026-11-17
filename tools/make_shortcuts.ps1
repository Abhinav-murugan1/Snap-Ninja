# Creates "Snap Ninja" shortcuts (Start menu and desktop) that open the desktop app: no console, own icon.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "Run run.bat once first: it installs what Snap Ninja needs."
    exit 1
}
$shell = New-Object -ComObject WScript.Shell
$desktopScript = Join-Path $root "desktop.py"
$icon = Join-Path $root "static\snap-ninja-v1.ico"
if (-not (Test-Path -LiteralPath $icon)) { throw "Snap Ninja icon is missing: $icon" }
$places = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Snap Ninja.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Snap Ninja.lnk")
)
$failed = $false
foreach ($lnk in $places) {
    try {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + (Join-Path $root "desktop.py") + '"'
    $s.WorkingDirectory = $root
    $s.IconLocation = $icon + ",0"
    $s.Description = "Snap Ninja: find the best moments in long videos and turn them into captioned shorts"
    $s.Save()
    Write-Host "Made $lnk"
    # Retire only this project's old shortcut. Keep a reversible backup.
    $place = Split-Path -Parent $lnk
    $legacy = Join-Path $place "Clipper.lnk"
    if (Test-Path -LiteralPath $legacy) {
        $old = $shell.CreateShortcut($legacy)
        if ($old.Arguments -like ('*' + $desktopScript + '*') -and
            [IO.Path]::GetFullPath((Split-Path -Parent $legacy)) -eq [IO.Path]::GetFullPath($place)) {
            $backup = $legacy + '.backup-' + (Get-Date -Format 'yyyyMMddHHmmss')
            Move-Item -LiteralPath $legacy -Destination $backup
            Write-Host "Retired this project's old Clipper shortcut (backup kept)."
        }
    }
    } catch {
        $failed = $true
        Write-Warning ("Could not finish updating " + $lnk + ": " + $_.Exception.Message)
    }
}
if ($failed) {
    Write-Warning "Some shortcuts could not be updated. Run make_shortcuts.bat from File Explorer under your normal Windows account."
    exit 1
}
Write-Host "Snap Ninja shortcuts now use the green ninja logo. Restart the app to refresh its taskbar identity."
Write-Host "If an old Clipper icon was pinned manually, unpin it and pin the new Snap Ninja shortcut."
