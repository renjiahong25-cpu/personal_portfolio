# 在你自己的 PowerShell 窗口里运行（项目根目录下）：
#   powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
# 这个窗口要一直开着；关掉窗口 = 关掉后端。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'backend_python\app.py'

# 三级回退：PIANOFLOW_PYTHON 环境变量 -> PATH 上的 python -> 报错并提示。
$python = $env:PIANOFLOW_PYTHON
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $python = $onPath.Source }
}
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "找不到 Python 解释器。请设置 PIANOFLOW_PYTHON 指向项目环境（Python 3.11，见 README 快速开始）的 python.exe，或将其加入 PATH。"
}

Write-Host "[PianoFlow] 后端启动中... http://127.0.0.1:8890" -ForegroundColor Cyan
Write-Host "[PianoFlow] 日志文件: $(Join-Path $root 'logs\pianoflow.log')" -ForegroundColor DarkGray
Write-Host "[PianoFlow] 按 Ctrl+C 停止`n" -ForegroundColor DarkGray

& $python $entry --port 8890
