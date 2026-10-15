param([string]$Npm = 'C:\Program Files\nodejs\npm.cmd')
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
foreach ($service in @('netease','kugou')) {
    $folder = Join-Path $project "tools\sidecars\$service"
    & $Npm --prefix $folder ci --omit=dev --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Cannot install $service dependencies ($LASTEXITCODE)" }
}
