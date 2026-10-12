param([int]$Port = 8000)
# 分批次监控快照（短命令 <10s）：进程 / 卡住检测 / 日志内容 / 健康
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$outFile = Join-Path $logs 'uvicorn.quote.out.log'
$errFile = Join-Path $logs 'uvicorn.quote.err.log'
$pidFile = Join-Path $logs 'uvicorn.quote.pid'

$target = ''
if (Test-Path $pidFile) { $target = (Get-Content $pidFile).Trim() }
$alive = $false
if ($target -and [int]$target -gt 0) {
    if (Get-Process -Id ([int]$target) -ErrorAction SilentlyContinue) { $alive = $true }
}
$listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$listening = ($null -ne $listen)

Write-Output ('[1] PROCESS alive=' + $alive + ' port_' + $Port + '=listening:' + $listening + ' pid=' + $target)

function Snap($f) {
    if (-not (Test-Path $f)) { return 'missing' }
    $it = Get-Item $f
    $age = [Math]::Round(((Get-Date) - $it.LastWriteTime).TotalSeconds, 0)
    return ('mtime=' + $it.LastWriteTime.ToString('HH:mm:ss') + ' age=' + $age + 's size=' + $it.Length)
}
Write-Output ('[2] STUCK out  ' + (Snap $outFile))
Write-Output ('[2] STUCK err  ' + (Snap $errFile))

Write-Output '[3] -------- uvicorn.out.log (tail 18) --------'
if (Test-Path $outFile) { Get-Content $outFile -Tail 18 } else { Write-Output '(no out log yet)' }
Write-Output '[3] -------- uvicorn.err.log (tail 18) --------'
if (Test-Path $errFile) { Get-Content $errFile -Tail 18 } else { Write-Output '(no err log yet)' }

Write-Output '[4] HEALTH'
try {
    $res = Invoke-WebRequest -UseBasicParsing ('http://127.0.0.1:' + $Port + '/health') -TimeoutSec 4
    Write-Output ('[4] HEALTH status=' + $res.StatusCode)
} catch {
    $msg = $_.Exception.Message
    if ($msg.Length -gt 70) { $msg = $msg.Substring(0, 70) }
    Write-Output ('[4] HEALTH status=NO_HEALTH ' + $msg)
}
Write-Output '----- SNAPSHOT_END -----'