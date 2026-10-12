# 在第二个 PowerShell 窗口里运行（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
# 这个窗口要一直开着；关掉窗口 = 关掉前端 dev server。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'

# 三级回退：PIANOFLOW_NPM 环境变量 -> PATH 上的 npm -> 报错并提示。
# 注意 Windows 上 npm 是 .cmd shim，必须拿到完整路径再调用。
$npm = $env:PIANOFLOW_NPM
if (-not $npm -or -not (Test-Path -LiteralPath $npm)) {
    $onPath = Get-Command npm -ErrorAction SilentlyContinue
    if ($onPath) { $npm = $onPath.Source }
}
if (-not $npm -or -not (Test-Path -LiteralPath $npm)) {
    throw "找不到 npm。请安装 Node.js（v20+）并加入 PATH，或设置 PIANOFLOW_NPM 指向 npm.cmd。"
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
