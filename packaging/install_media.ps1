# Setup downloads FFmpeg directly from its distributor. Keep the archive's notices.
param([Parameter(Mandatory=$true)][string]$Archive,
      [Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$unpack = Join-Path (Split-Path -Parent $Destination) 'snap-ninja-media-unpack'
Expand-Archive -LiteralPath $Archive -DestinationPath $unpack -Force
$ffmpeg = @(Get-ChildItem -LiteralPath $unpack -Filter ffmpeg.exe -Recurse)
if ($ffmpeg.Count -ne 1) { throw 'FFmpeg archive layout is invalid.' }
$providerRoot = Split-Path -Parent (Split-Path -Parent $ffmpeg[0].FullName)
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
    $source = Join-Path (Join-Path $providerRoot 'bin') $name
    if (-not (Test-Path -LiteralPath $source)) { throw "Missing media tool: $name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $Destination $name)
}
foreach ($item in @(Get-ChildItem -LiteralPath $providerRoot | Where-Object Name -ne 'bin')) {
    Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse
}
& (Join-Path $Destination 'ffmpeg.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg could not run on this computer.' }
& (Join-Path $Destination 'ffprobe.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'FFprobe could not run on this computer.' }
