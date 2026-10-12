# -*- coding: utf-8 -*-
param(
    [int]$WatchdogOf = 0,
    [int]$KillAfterSec = 0,
    [int]$Port = 8000
)
# =========================================================
# 停止联调后端（手动）或看门狗超时自动停止
#   - 有 WatchdogOf/KillAfterSec：也就是看门狗模式，睡够再杀目标
#   - 无参数：立即停止（按 pid 文件 + 端口归属）
# =========================================================
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$pidFile = Join-Path $logs "uvicorn.quote.pid"

if ($WatchdogOf -gt 0) {
    Write-Output "WATCHDOG armed target=$WatchdogOf sleep=${KillAfterSec}s"
    Start-Sleep -Seconds $KillAfterSec
    Stop-Process -Id $WatchdogOf -Force -ErrorAction SilentlyContinue
    if (Test-Path $pidFile) {
        $cur = Get-Content $pidFile
        if ("$cur" -eq "$WatchdogOf") { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    }
    Write-Output "WATCHDOG fired: stopped pid=$WatchdogOf after ${KillAfterSec}s"
    exit 0
}

$killed = @()
if (Test-Path $pidFile) {
    $targetPid = [int](Get-Content $pidFile)
    if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
        $killed += $targetPid
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
$probe = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($probe) {
    foreach ($own in ($probe | Select-Object -ExpandProperty OwningProcess -Unique)) {
        Stop-Process -Id $own -Force -ErrorAction SilentlyContinue
        $killed += $own
    }
}
Write-Output "STOPPED pid=$(($killed | Sort-Object -Unique) -join ',') port=$Port"
exit 0