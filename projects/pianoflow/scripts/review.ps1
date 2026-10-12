# Free AI code review backed by the local Qwen endpoint (Continue CLI).
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\review.ps1 -Paths "backend_python/src/services/cache"
#   powershell -ExecutionPolicy Bypass -File scripts\review.ps1 -Since HEAD~3
#
# Design notes:
#  * This file MUST stay UTF-8 with BOM. PowerShell 5.1 reads .ps1 as ANSI/GBK
#    otherwise, which mangles the Chinese text and breaks here-strings.
#  * Review one module at a time. A 27B model on one GPU answers a focused
#    request in ~1 minute; asking it to swallow a whole diff times out the HTTP
#    client and returns Continue's opaque
#    "the request failed and the interceptors did not return an alternative
#    response". Small scope also produces better findings.
#  * The transcript is teed to logs\review-<timestamp>.log so results can be
#    read later without re-running the model.

param(
    [string]$Paths = '',
    [string]$Since = '',
    [string]$Note = '',
    [int]$TimeoutMinutes = 12
)

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$cn = Join-Path $env:APPDATA 'npm\cn.cmd'
$config = Join-Path $root '.continue\config.yaml'
$logDir = Join-Path $root 'logs'

if (-not (Test-Path -LiteralPath $cn)) {
    throw "cn not found: $cn"
}
if (-not (Test-Path -LiteralPath $config)) {
    throw "review config not found: $config"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logDir "review-$stamp.log"

Push-Location $root

if ($Paths) {
    $scope = "只审查这些路径：$Paths"
} elseif ($Since) {
    $scope = "审查 git diff $Since..HEAD 涉及的改动"
} else {
    $scope = "审查当前仓库中与音频分析、音源、缓存相关的模块（自行定位相关文件，最多 5 个）"
}

$prompt = @"
只读代码审查，不要修改任何文件。

范围：$scope
补充：$Note

按 .continue/config.yaml 里的六点清单逐条检查：
async 正确性 / 超时与错误处理 / 含空格路径 / 限流与合规 / 密钥与误删 / 测试。

输出格式（务必简洁，总长度控制在 1200 字以内）：
- 每条发现一行：级别 | 文件:行号 | 问题 | 改法
- 级别只用 CRITICAL / HIGH / MEDIUM / LOW
- 六点里干净的直接写「clean」，不要展开
- 最后一行：结论 CRITICAL/HIGH 数量 + 是否可通过门禁
"@

Write-Host "[review] model  : local OpenAI-compatible endpoint (see .continue/config.yaml)" -ForegroundColor Cyan
Write-Host "[review] config : $config" -ForegroundColor DarkGray
Write-Host "[review] log    : $log" -ForegroundColor DarkGray
Write-Host "[review] scope  : $scope" -ForegroundColor Cyan
Write-Host ""

if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }

# Stream instead of `-p`: the response arrives incrementally, so a slow model
# does not look like a hung request.
$job = Start-Job -ScriptBlock {
    param($exe, $cfg, $text)
    & $exe -p $text --config $cfg --readonly 2>&1
} -ArgumentList $cn, $config, $prompt

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$lastLength = 0
while ($true) {
    Start-Sleep -Seconds 15
    $state = $job.State
    $output = Receive-Job -Job $job -ErrorAction SilentlyContinue
    if ($output) {
        $joined = ($output | Out-String).TrimEnd()
        if ($joined.Length -gt $lastLength) {
            Write-Host $joined.Substring($lastLength)
            $joined | Out-File -FilePath $log -Append -Encoding utf8
            $lastLength = $joined.Length
        }
    }
    if ($state -in @('Completed', 'Failed', 'Stopped')) {
        break
    }
    if ((Get-Date) -gt $deadline) {
        Write-Warning "timed out after $TimeoutMinutes minutes; keeping partial output"
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        break
    }
}

$failed = ($job.State -eq 'Failed')
Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
Pop-Location

Write-Host ""
if (Test-Path -LiteralPath $log) {
    Write-Host "[review] transcript: $log ($((Get-Item $log).Length) bytes)" -ForegroundColor Cyan
} else {
    Write-Warning "[review] no transcript was produced"
}
if ($failed) { exit 1 }
exit 0
