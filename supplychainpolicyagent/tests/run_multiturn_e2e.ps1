# tests\run_multiturn_e2e.ps1
# 一键自动化测试：重启后端 → 等 health 就绪 → 多轮 SSE 四连探针 → 汇报
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_multiturn_e2e.ps1
# 退出码: 0=探针全通过  1=后端未就绪/进程死  其他=探针失败项
$ErrorActionPreference = "Continue"
$root = "D:\Program Files\supplychainpolicyagent"
$py   = "D:\conda_envs\cross-border-agent\python.exe"
$out  = "$root\logs\uvicorn.out.log"
$err  = "$root\logs\uvicorn.err.log"

Write-Output "==== [1/4] 清理旧后端 (端口 8000) ===="
$conns = netstat -ano | Select-String ":8000 " | Select-String "LISTENING"
$oldPids = @()
foreach ($line in $conns) {
    $parts = $line.ToString().Trim() -split "\s+"
    $p8000 = $parts[-1]
    if ($p8000 -and $p8000 -ne "0" -and $oldPids -notcontains $p8000) { $oldPids += $p8000 }
}
if ($oldPids.Count -gt 0) {
    foreach ($p in $oldPids) {
        Write-Output "  杀旧进程 PID=$p"
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
} else {
    Write-Output "  端口 8000 无旧进程"
}

Write-Output "==== [2/4] 分离启动后端 ===="
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
Set-Content -Path $out -Value ""
Set-Content -Path $err -Value ""
$bp = Start-Process -FilePath $py `
    -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -PassThru -WindowStyle Hidden
Write-Output "  后端 PID=$($bp.Id) 已分离启动"

Write-Output "==== [3/4] 等待后端就绪 (health 200, 上限 180s) ===="
$ready = $false
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    if (-not (Get-Process -Id $bp.Id -ErrorAction SilentlyContinue)) {
        Write-Output "  [ALERT] 后端进程已死亡！最后 30 行日志："
        Get-Content -Tail 30 $err -ErrorAction SilentlyContinue
        Write-Output "  ---- stdout ----"
        Get-Content -Tail 30 $out -ErrorAction SilentlyContinue
        Write-Output "RESULT: FAILED (后端进程死亡)"
        exit 1
    }
    $code = $null
    try { $code = (Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3).StatusCode } catch { $code = $null }
    if ($code -eq 200) { $ready = $true; break }
    $lastErr = (Get-Content -Tail 1 $err -ErrorAction SilentlyContinue)
    Write-Output "  [wait] health=$code | lastlog=$lastErr"
}
if (-not $ready) {
    Write-Output "  [ALERT] 后端 180s 未就绪！最后 50 行日志："
    Get-Content -Tail 50 $err -ErrorAction SilentlyContinue
    Write-Output "  ---- stdout ----"
    Get-Content -Tail 50 $out -ErrorAction SilentlyContinue
    Write-Output "RESULT: FAILED (后端未就绪)"
    exit 1
}
Write-Output "  后端就绪 (health=200)"

Write-Output "==== [4/4] 运行多轮 SSE 四连探针 (tests\multiturn_probe.py) ===="
& $py "tests\multiturn_probe.py"
$probeExit = $LASTEXITCODE
Write-Output ""
Write-Output "==== 探针退出码: $probeExit ===="
if ($probeExit -eq 0) {
    Write-Output "RESULT: PASS (多轮四连验证全部通过)"
} else {
    Write-Output "RESULT: FAILED (探针存在失败项，详见上方输出)"
}
Write-Output "后端保持运行 (PID=$($bp.Id))，日志: $out / $err"
exit $probeExit
