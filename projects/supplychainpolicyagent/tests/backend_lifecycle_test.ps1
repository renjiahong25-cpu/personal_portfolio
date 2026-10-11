# backend_lifecycle_test.ps1 - 后端生命周期自动化测试
# 验证: stop(清理) -> start -> health 200 -> 端口监听 -> API 可服务 -> stop -> 端口释放 -> restart 可再启
# 用法:
#   powershell -ExecutionPolicy Bypass -File tests\backend_lifecycle_test.ps1
# 参数:
#   -KeepRunning   测试通过后保持后端运行（默认 false，测完即停）
param(
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$Script   = "D:\Program Files\supplychainpolicyagent\scripts\backend.ps1"
$Base     = "http://127.0.0.1:8000"
$Passed   = @()
$Failed   = @()

function Check([string]$name, [bool]$cond, [string]$detail = "") {
    if ($cond) { $script:Passed += $name; Write-Host "  [PASS] $name" -ForegroundColor Green }
    else { $script:Failed += @($name); Write-Host "  [FAIL] $name | $detail" -ForegroundColor Red }
}

function Invoke-BackendScript([string]$action) {
    $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $Script $action 2>&1 | Out-String
    return @{ exit = $LASTEXITCODE; output = $output }
}

function Test-PortListen {
    return [bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
}

function Get-PortOwner {
    $c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

function Wait-PortUp([int]$MaxSec = 30) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt ($MaxSec * 1000)) {
        if (Test-PortListen) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-Health([int]$retries = 6) {
    for ($i = 0; $i -lt $retries; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "$Base/health" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return 200 }
        } catch { Start-Sleep -Seconds 2 }
    }
    return 0
}

Write-Host "===== 后端生命周期自动化测试 =====" -ForegroundColor Cyan

Write-Host "`n-- 准备：确保旧实例已清理 --"
$r = Invoke-BackendScript "stop"
Check "stop(清理残留) 返回0" ($r.exit -eq 0)
Start-Sleep -Seconds 2
Check "停后端口已释放" (-not (Test-PortListen))

Write-Host "`n-- 阶段1: start --"
$r = Invoke-BackendScript "start"
Check "start 返回0" ($r.exit -eq 0) "exit=" + $r.exit
if ($r.exit -ne 0) {
    Write-Host $r.output
    Write-Host "start 失败，测试终止" -ForegroundColor Yellow
} else {
    Check "start 输出含 PID=" ($r.output -match "PID=")
    Check "start 输出含 health 就绪/200" ($r.output -match "就绪|200")
    Check "stdout 日志生成" (Test-Path "D:\Program Files\supplychainpolicyagent\logs\uvicorn.out.log")

    Write-Host "`n-- 阶段2: health 与端口 --"
    Check "端口 8000 被监听(最多等30s)" (Wait-PortUp 30)
    $h = Get-Health
    Check "GET /health == 200" ($h -eq 200) "status=$h"
    $owner = Get-PortOwner
    $pidFile = "D:\Program Files\supplychainpolicyagent\logs\backend.pid"
    if (Test-Path $pidFile) {
        $filePid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        Check "端口属主 == PID文件" ($owner -eq $filePid) "owner=$owner pidfile=$filePid"
    }

    Write-Host "`n-- 阶段3: API 可服务（轻量真实请求）--"
    $apiOk = $false; $apiDetail = "not run"
    try {
        $doc = Invoke-WebRequest -Uri "$Base/api/doc/list?page=1&page_size=2" -UseBasicParsing -TimeoutSec 20
        $code = $null
        try { $code = ($doc.Content | ConvertFrom-Json).code } catch {}
        $apiOk = ($doc.StatusCode -eq 200 -and $code -eq 200)
        $apiDetail = "http=$($doc.StatusCode) code=$code"
    } catch {
        $apiDetail = "$($_.Exception.Message)"
    }
    Check "GET /api/doc/list 可服务" $apiOk $apiDetail

    Write-Host "`n-- 阶段4: stop --"
    $r = Invoke-BackendScript "stop"
    Check "stop 返回0" ($r.exit -eq 0)
    if ($r.exit -ne 0) { Write-Host $r.output }
    Start-Sleep -Seconds 2
    Check "停后端口已释放" (-not (Test-PortListen))
    Check "停后 /health 不可达" ((Get-Health) -eq 0)
    Check "PID 文件已删除" (-not (Test-Path "D:\Program Files\supplychainpolicyagent\logs\backend.pid"))

    Write-Host "`n-- 阶段5: restart 可再启 --"
    $r = Invoke-BackendScript "restart"
    Check "restart 返回0" ($r.exit -eq 0)
    if ($r.exit -ne 0) { Write-Host $r.output } else {
        Wait-PortUp 30 | Out-Null
        Check "重启后 /health == 200" ((Get-Health) -eq 200)
        if (-not $KeepRunning) {
            Write-Host "`n-- 阶段6: 测试完成后清理 --"
            $r = Invoke-BackendScript "stop"
            Check "最终 stop 返回0" ($r.exit -eq 0)
            Start-Sleep -Seconds 2
            Check "最终端口已释放" (-not (Test-PortListen))
        } else {
            Write-Host "`n-- 保持后端运行（-KeepRunning）--"
        }
    }
}

Write-Host "`n----------------------------------------"
Write-Host "=== 通过 $($Passed.Count) / $($Passed.Count + $Failed.Count) ==="
if ($Failed.Count -gt 0) { Write-Host "失败项: $($Failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "ALL GREEN" -ForegroundColor Green
exit 0