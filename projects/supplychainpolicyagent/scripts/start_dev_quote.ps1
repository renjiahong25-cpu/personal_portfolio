# -*- coding: utf-8 -*-
param(
    [int]$WatchdogSec = 900,
    [int]$Port = 8000
)
# =========================================================
# 受保护的后端启动（联调用）——自带停止条件
#   · 看门狗：启动后 WatchdogSec 秒自动 Stop-Process，杜绝僵尸
#   · 手动停止：stop_dev_quote.ps1
#   · 端口占用即失败退出，不擅自杀自有进程
# 用法：.\scripts\start_dev_quote.ps1 -WatchdogSec 900 -Port 8000
# =========================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'
$PY = if ($env:SCP_PYTHON) { $env:SCP_PYTHON } else { "python" }
$out = Join-Path $logs "uvicorn.quote.out.log"
$err = Join-Path $logs "uvicorn.quote.err.log"
$pidFile = Join-Path $logs "uvicorn.quote.pid"

# 端口占用/同项目旧进程检查 → 带停止条件，绝不重复起
$probe = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($probe) {
    $own = ($probe | Select-Object -First 1).OwningProcess
    Write-Output "PORT_BUSY pid=$own port=$Port (run stop_dev_quote.ps1 first)"
    exit 3
}
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile
    if (Get-Process -Id $old -ErrorAction SilentlyContinue) {
        Write-Output "OLD_RUNNING pid=$old (run stop_dev_quote.ps1 first)"
        exit 3
    }
    Remove-Item $pidFile -Force
}

$p = Start-Process -FilePath $PY `
    -ArgumentList '-m','uvicorn','main:app','--host','127.0.0.1','--port',"$Port" `
    -WorkingDirectory $root -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Set-Content -Path $pidFile -Value $p.Id

# ---- 看门狗：超时有停止条件 ----
$stopScript = Join-Path $PSScriptRoot 'stop_dev_quote.ps1'
$wd = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$stopScript`"",'-WatchdogOf',"$($p.Id)",'-KillAfterSec',"$WatchdogSec" `
    -WindowStyle Hidden -PassThru

Write-Output "UVICORN_PID=$($p.Id) WATCHDOG_PID=$($wd.Id) TTL=${WatchdogSec}s PORT=$Port"
Write-Output "STOP_CONDITION: auto-stop after $WatchdogSec sec; manual stop: .\scripts\stop_dev_quote.ps1"
exit 0