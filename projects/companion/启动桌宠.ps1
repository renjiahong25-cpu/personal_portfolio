param([ValidateRange(1024,65535)][int]$Port = 8793, [string]$PythonPath = '<YOUR_PYTHON_PATH>')
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '未找到 Python 环境，请修改脚本中的 pythonPath。' }
$probe = New-Object System.Net.Sockets.TcpClient
try {
    $attempt = $probe.ConnectAsync('127.0.0.1', $Port)
    try { $null = $attempt.Wait(400) } catch { }
    if ($probe.Connected) { throw "端口 $Port 已被占用；请使用其他 -Port，现有服务不会被重启。" }
} finally { $probe.Dispose() }
$runtimePath = Join-Path $projectPath 'runtime'
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$errorLog = Join-Path $runtimePath "desktop-server-$Port-$stamp-error.log"
$outLog = Join-Path $runtimePath "desktop-server-$Port-$stamp.log"
$server = Start-Process -FilePath $pythonPath -ArgumentList @('-m','app','--port',"$Port") -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errorLog
try {
    $healthy = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        $server.Refresh()
        if ($server.HasExited) { break }
        try {
            $state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/state" -TimeoutSec 1
            if ($state.token -and $state.action -eq 'disabled') { $healthy = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 200
    }
    if (-not $healthy) { throw "后台启动失败，请检查 $errorLog" }
    $server.Id | Set-Content -LiteralPath (Join-Path $runtimePath "desktop-server-$Port.pid")
    # Native pet is the visible interactive UI. Keep the server hidden and stop only our owned PID on exit.
    Push-Location -LiteralPath $projectPath
    try {
        & $pythonPath -m app.desktop --port $Port
        if ($LASTEXITCODE -ne 0) { throw "桌宠启动或运行失败（退出码 $LASTEXITCODE）。" }
    } finally {
        Pop-Location
    }
} finally {
    $server.Refresh()
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
}
