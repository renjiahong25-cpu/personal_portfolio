# backend.ps1 - 后端生命周期管理（启动/终止/状态/重启）
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\backend.ps1 start    启动并等待健康
#   powershell -ExecutionPolicy Bypass -File scripts\backend.ps1 stop     终止并等待端口释放
#   powershell -ExecutionPolicy Bypass -File scripts\backend.ps1 restart  重启
#   powershell -ExecutionPolicy Bypass -File scripts\backend.ps1 status   查询状态
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$ProjectRoot   = Split-Path -Parent $PSScriptRoot
$Python        = if ($env:SCP_PYTHON) { $env:SCP_PYTHON } else { "python" }
$Port          = 8000
$HostAddr      = "127.0.0.1"
$LogDir        = Join-Path $ProjectRoot "logs"
$OutLog        = Join-Path $LogDir "uvicorn.out.log"
$ErrLog        = Join-Path $LogDir "uvicorn.err.log"
$PidFile       = Join-Path $LogDir "backend.pid"
$HealthUrl     = "http://${HostAddr}:${Port}/health"
$WaitHealthyMs = 150000
$WaitPortFreeMs = 30000

function Write-Step([string]$msg) { Write-Host "[backend] $msg" -ForegroundColor Cyan }

function Get-BackendPidFile {
    if (Test-Path -LiteralPath $PidFile) {
        try { return [int](Get-Content -LiteralPath $PidFile -Raw).Trim() } catch { return $null }
    }
    return $null
}

function Get-PortOwnerPid {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Test-ProcessAlive([int]$ProcId) {
    if (-not $ProcId) { return $false }
    return [bool](Get-Process -Id $ProcId -ErrorAction SilentlyContinue)
}

function Remove-Pids($Pids) {
    foreach ($p in $Pids) {
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    }
}

function Get-HealthStatus {
    try {
        $h = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        $body = $h.Content
        if ($h.StatusCode -eq 200) { return $body }
        return "HTTP $($h.StatusCode): $body"
    } catch {
        return "DOWN"
    }
}

function Print-Status {
    $filePid  = Get-BackendPidFile
    $portPid  = Get-PortOwnerPid
    $alive    = Test-ProcessAlive $filePid
    $health   = Get-HealthStatus
    Write-Host "PID文件: $filePid (存活=$alive)"
    Write-Host "端口占用: $portPid"
    Write-Host "健康检查: $health"
    if (Test-Path -LiteralPath $OutLog) {
        Write-Host "stdout日志尾部:"
        Get-Content -LiteralPath $OutLog -Tail 5 -ErrorAction SilentlyContinue
    }
}

function Start-Backend {
    $filePid = Get-BackendPidFile
    $portPid = Get-PortOwnerPid
    if ((Test-ProcessAlive $filePid) -or (Test-ProcessAlive $portPid)) {
        Write-Step "已有后端进程运行 (pid 文件=$filePid, 端口=$portPid)，请先执行 stop 或 restart"
        Print-Status
        exit 1
    }

    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Remove-Item -LiteralPath $OutLog, $ErrLog -ErrorAction SilentlyContinue

    $env:PYTHONIOENCODING = "utf-8"
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"

    Write-Step "启动 uvicorn (pid 文件将在启动后写入 $PidFile)"
    $p = Start-Process -FilePath $Python `
        -ArgumentList "-m", "uvicorn", "main:app", "--host", $HostAddr, "--port", $Port `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog `
        -PassThru -WindowStyle Hidden

    Set-Content -LiteralPath $PidFile -Value $p.Id -Encoding ascii
    Write-Step "PID=$($p.Id) 已分离启动，等待健康检查..."

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $WaitHealthyMs) {
        Start-Sleep -Seconds 3
        if (-not (Test-ProcessAlive $p.Id)) { break }
        $health = Get-HealthStatus
        if ($health -ne "DOWN" -and $health -notmatch "HTTP") {
            $owner = Get-PortOwnerPid
            if ($owner -ne $p.Id) {
                Write-Step "异常 | /health 可达但端口 $Port 属主 PID=$owner 与启动 PID=$($p.Id) 不符，判定启动失败"
                Remove-Pids @($p.Id)
                Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
                exit 1
            }
            Write-Step "就绪 | health=$health | PID=$($p.Id) | 端口属主校验通过 | 耗时=$([math]::Round($sw.Elapsed.TotalSeconds,1))s"
            Write-Host "stdout日志:"
            Get-Content -LiteralPath $OutLog -Tail 8 -ErrorAction SilentlyContinue
            exit 0
        }
    }

    Write-Step "启动失败或超时 ($WaitHealthyMs ms)，日志如下"
    Write-Host "----- stderr -----"
    Get-Content -LiteralPath $ErrLog -Tail 30 -ErrorAction SilentlyContinue
    Write-Host "----- stdout -----"
    Get-Content -LiteralPath $OutLog -Tail 15 -ErrorAction SilentlyContinue
    Remove-Pids @($p.Id)
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
    exit 1
}

function Stop-Backend {
    $filePid = Get-BackendPidFile
    $portPid = Get-PortOwnerPid
    $targets = @($filePid, $portPid) | Sort-Object -Unique
    $targets = @($targets | Where-Object { $_ })
    if ($targets.Count -eq 0) {
        Write-Step "没有发现后端进程（无 PID 文件、端口 $Port 空闲）"
        exit 0
    }
    Write-Step "终止进程: $($targets -join ', ')"
    Remove-Pids $targets

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $WaitPortFreeMs) {
        Start-Sleep -Seconds 1
        if (-not (Get-PortOwnerPid)) { break }
    }
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue

    $leftover = Get-PortOwnerPid
    if ($leftover) {
        Write-Step "端口 $Port 仍被 PID=$leftover 占用"
        exit 1
    }
    Write-Step "已终止，端口 $Port 已释放"
    exit 0
}

if ($Action -eq "start") {
    Start-Backend
} elseif ($Action -eq "stop") {
    Stop-Backend
} elseif ($Action -eq "restart") {
    Stop-Backend
    if (-not (Get-PortOwnerPid)) {
        Start-Backend
    } else {
        Write-Step "stop 后端口仍未释放，已中止 restart"
        exit 1
    }
} elseif ($Action -eq "status") {
    Print-Status
}