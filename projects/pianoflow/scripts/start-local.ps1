param(
    [int]$Port = 8890,
    [string]$Python = 'D:\conda_envs\pianoflow\python.exe',
    [string]$Node = 'C:\Program Files\nodejs\node.exe',
    [string]$DependencyRoot = '',
    [switch]$SkipSidecars
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $project 'runtime'
if (-not (Test-Path -LiteralPath $Python)) { throw "Python interpreter not found: $Python" }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$env:PIANOFLOW_KUGOU_COOKIE_FILE = Join-Path $runtime 'kugou-cookie.txt'
$env:PIANOFLOW_NETEASE_COOKIE_FILE = Join-Path $runtime 'netease-cookie.txt'
$env:PIANOFLOW_BILIBILI_COOKIE_FILE = Join-Path $runtime 'bilibili-cookie.txt'
$env:PIANOFLOW_NETEASE_SIDECAR_URL = 'http://127.0.0.1:4002'
$env:PIANOFLOW_KUGOU_SIDECAR_URL = 'http://127.0.0.1:5002'
if (-not $SkipSidecars) {
    if (-not $DependencyRoot -and -not (Test-Path -LiteralPath (Join-Path $project 'tools\sidecars\netease\node_modules'))) {
        $installedDependencies = 'D:\Program Files\pianoflow\tools\sidecars'
        if (Test-Path -LiteralPath (Join-Path $installedDependencies 'netease\node_modules')) { $DependencyRoot = $installedDependencies }
    }
    $portsRunning = @(@(4002,5002) | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue })
    if ($portsRunning.Count -eq 0) {
        & (Join-Path $PSScriptRoot 'start-music-sidecars.ps1') -Node $Node -DependencyRoot $DependencyRoot
    } elseif ($portsRunning.Count -ne 2) {
        throw 'Only one music sidecar is running. Check ports 4002 and 5002 before restarting.'
    }
}
Write-Host "[PianoFlow] http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "[PianoFlow] KuGou login: http://127.0.0.1:$Port/login/kugou"
Write-Host '[PianoFlow] Press Ctrl+C to stop the backend.'
$preview = Join-Path $project '启动预览.py'
if (Test-Path -LiteralPath $preview) {
    & $Python $preview --port $Port
} else {
    & $Python (Join-Path $project 'backend_python\app.py') --port $Port
}
