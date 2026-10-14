# 在第二个 PowerShell 窗口里运行：powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\start-frontend.ps1"
# 这个窗口要一直开着；关掉窗口 = 关掉前端 dev server。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'
$npm = 'C:\Program Files\nodejs\npm.cmd'

if (-not (Test-Path -LiteralPath $npm)) {
    throw "找不到 npm: $npm"
}

if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
    Write-Host "[PianoFlow] 首次运行，先安装前端依赖..." -ForegroundColor Yellow
    Push-Location $frontend
    try { & $npm install } finally { Pop-Location }
}

Write-Host "[PianoFlow] 前端启动中... http://localhost:5173" -ForegroundColor Cyan
Write-Host "[PianoFlow] /api 已代理到后端 8890，按 Ctrl+C 停止`n" -ForegroundColor DarkGray

Push-Location $frontend
try {
    & $npm run dev
} finally {
    Pop-Location
}
