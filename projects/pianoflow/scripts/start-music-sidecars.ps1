param(
    [int]$NeteasePort = 4002,
    [int]$KugouPort = 5002,
    [string]$Node = 'C:\Program Files\nodejs\node.exe',
    [string]$DependencyRoot = ''
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$sidecars = Join-Path $project 'tools\sidecars'
$runtime = Join-Path $project 'runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
foreach ($port in @($NeteasePort,$KugouPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop this PianoFlow instance or choose another port."
    }
}
# Fingerprint only, no account credentials; preserve across our restarts.
$deviceFile = Join-Path $runtime 'kugou-device.json'
if (-not (Test-Path -LiteralPath $deviceFile)) {
    @{guid=[guid]::NewGuid().ToString(); dev=([guid]::NewGuid().ToString('N').Substring(0,10))} |
        ConvertTo-Json | Set-Content -LiteralPath $deviceFile -Encoding utf8
}
$device = Get-Content -LiteralPath $deviceFile -Raw | ConvertFrom-Json
$env:KUGOU_API_GUID = $device.guid
$env:KUGOU_API_DEV = $device.dev
$env:HOST = '127.0.0.1'
# Use the platform's ordinary entitlement response; never enable sidecar unblock routes.
$env:ENABLE_GENERAL_UNBLOCK = 'false'
$env:ENABLE_UNBLOCK = 'false'
$pids = @{}
foreach ($service in @('netease','kugou')) {
    $folder = Join-Path $sidecars $service
    $dependency = if ($DependencyRoot) { Join-Path $DependencyRoot "$service\node_modules" } else { Join-Path $folder 'node_modules' }
    if (-not (Test-Path -LiteralPath $dependency)) { throw "Missing dependencies for $service. Run scripts/install-music-sidecars.ps1 first." }
    $env:NODE_PATH = $dependency
    $env:PORT = if ($service -eq 'netease') { "$NeteasePort" } else { "$KugouPort" }
    $proc = Start-Process -FilePath $Node -ArgumentList 'app.js' -WorkingDirectory $folder -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtime "$service.out.log") -RedirectStandardError (Join-Path $runtime "$service.err.log")
    $pids[$service] = $proc.Id
}
$pids | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'sidecar-pids.json') -Encoding utf8
Write-Host "NetEase: http://127.0.0.1:$NeteasePort ; KuGou: http://127.0.0.1:$KugouPort"
Write-Host 'Set PIANOFLOW_NETEASE_SIDECAR_URL and PIANOFLOW_KUGOU_SIDECAR_URL to these URLs before starting the backend.'
