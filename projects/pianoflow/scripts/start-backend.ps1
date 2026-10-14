# 在你自己的 PowerShell 窗口里运行：powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\start-backend.ps1"
# 这个窗口要一直开着；关掉窗口 = 关掉后端。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = 'D:\conda_envs\pianoflow\python.exe'
$entry = Join-Path $root 'backend_python\app.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 Python 解释器: $python （conda 环境 pianoflow 是否还在？）"
}

Write-Host "[PianoFlow] 后端启动中... http://127.0.0.1:8890" -ForegroundColor Cyan
Write-Host "[PianoFlow] 日志文件: $(Join-Path $root 'logs\pianoflow.log')" -ForegroundColor DarkGray
Write-Host "[PianoFlow] 按 Ctrl+C 停止`n" -ForegroundColor DarkGray

& $python $entry --port 8890
