# PianoFlow quality gate.
#
# Why this wrapper exists: pre-commit's `language: system` hooks run whatever
# `python` resolves to on PATH. Without this script that is the *base* conda
# interpreter, which has neither pytest nor the lint tooling. So we prepend the
# project environment, run the fast pre-commit loop, then run the heavier checks
# with backend_python as the working directory (that is where pyproject.toml
# lives, and mypy/bandit resolve their config relative to the cwd).
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\gate.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\gate.ps1 -Governance
#
# Two tiers:
#   default     code must be clean: ruff, ruff-format, mypy, bandit, pytest,
#               secrets, model integrity
#   -Governance additionally requires the aoci cognition layer to be aligned.
#               It reports `authoring_required` until every managed object has a
#               hand-authored cognition Entry. That is deliberate authoring work,
#               not something a linter can pass, so it is advisory by default.

param(
    [switch]$Governance
)

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend_python'
$frontend = Join-Path $root 'frontend'

# 三级回退：PIANOFLOW_PYTHON 环境变量 -> PATH 上的 python -> 报错并提示。
$envPython = $env:PIANOFLOW_PYTHON
if (-not $envPython -or -not (Test-Path -LiteralPath $envPython)) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $envPython = $onPath.Source }
}
if (-not $envPython -or -not (Test-Path -LiteralPath $envPython)) {
    throw "找不到项目解释器。请设置 PIANOFLOW_PYTHON 指向项目环境（Python 3.11）的 python.exe，或将其加入 PATH。"
}
# 环境根 = 解释器父目录（conda env 与 venv 都是这个布局），pre-commit 钩子靠它解析 python。
$envScripts = Split-Path -Parent $envPython

# aoci（认知索引工具）：PIANOFLOW_AOCI -> PATH -> 找不到则跳过该阶段。
$aoci = $env:PIANOFLOW_AOCI
if (-not $aoci -or -not (Test-Path -LiteralPath $aoci)) {
    $aociCmd = Get-Command aoci -ErrorAction SilentlyContinue
    if ($aociCmd) { $aoci = $aociCmd.Source }
}

# npm on Windows is a .cmd shim; a bare `npm` is not resolvable from PowerShell.
$npm = $env:PIANOFLOW_NPM
if (-not $npm -or -not (Test-Path -LiteralPath $npm)) {
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if ($npmCmd) { $npm = $npmCmd.Source }
}
$node = $null
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if ($nodeCmd) { $node = $nodeCmd.Source }

# `python` inside hook entries must resolve to the project environment.
$env:PATH = "$envScripts;$envScripts\Scripts;" + $env:PATH
$env:PYTHON = $envPython
$env:PYTHONIOENCODING = 'utf-8'

$results = [ordered]@{}

Write-Host "`n=== 1/7  pre-commit: ruff, pytest, secrets, models ===" -ForegroundColor Cyan
& $envPython -m pre_commit run --all-files
$results['pre-commit'] = $LASTEXITCODE

Write-Host "`n=== 2/7  ruff format --check ===" -ForegroundColor Cyan
Push-Location $backend
& $envPython -m ruff format --check src tests scripts
$results['ruff-format'] = $LASTEXITCODE
Pop-Location

Write-Host "`n=== 3/7  mypy (types) ===" -ForegroundColor Cyan
Push-Location $backend
& $envPython -m mypy
$results['mypy'] = $LASTEXITCODE
Pop-Location

Write-Host "`n=== 4/7  bandit (security) ===" -ForegroundColor Cyan
Push-Location $backend
& $envPython -m bandit --recursive --quiet --configfile pyproject.toml src scripts
$results['bandit'] = $LASTEXITCODE
Pop-Location

# The notation highlight is the one feature that only exists in the browser: the
# follow-along view lights the note-heads, the keyboard and the audio from the same
# timeline, and none of that is exercised by a Python test. It runs the real OSMD bundle
# in jsdom against real MusicXML, which is the only way to catch the class of bug that
# lived here for so long - a call to an OSMD API that does not exist, and a MusicXML
# writer whose tick stream did not match the score's own beats. Both shipped green.
Write-Host "`n=== 5/7  notation highlight (OSMD in jsdom) ===" -ForegroundColor Cyan
if (-not $node) {
    Write-Warning "node not found; skipping notation highlight"
    $results['notation-highlight'] = 0
} else {
    Push-Location $frontend
    & $node tests/notation-highlight.test.mjs
    $results['notation-highlight'] = $LASTEXITCODE
    Pop-Location
}

Write-Host "`n=== 6/7  frontend build ===" -ForegroundColor Cyan
if (-not $npm) {
    Write-Warning "npm not found; skipping frontend build"
    $results['frontend-build'] = 0
} else {
    Push-Location $frontend
    & $npm run build
    $results['frontend-build'] = $LASTEXITCODE
    Pop-Location
}

Write-Host "`n=== 7/7  aoci governance check ===" -ForegroundColor Cyan
$aociExit = 0
if (-not (Test-Path -LiteralPath $aoci)) {
    Write-Warning "aoci not found at $aoci, skipping governance check"
} else {
    Push-Location $root
    & $aoci check
    $aociExit = $LASTEXITCODE
    Pop-Location
    if ($aociExit -ne 0 -and -not $Governance) {
        Write-Warning "aoci reports outstanding work (exit $aociExit)."
        Write-Warning "That is the cognition layer (Entries for managed objects), not a code defect."
        Write-Warning "Re-run with -Governance once it is aligned to make it mandatory."
        $aociExit = 0
    }
}
$results['aoci'] = $aociExit

$failed = @($results.GetEnumerator() | Where-Object { $_.Value -ne 0 })

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "GATE PASSED  ($($results.Keys -join ', '))" -ForegroundColor Green
    exit 0
}

Write-Host "GATE FAILED:" -ForegroundColor Red
foreach ($entry in $failed) {
    Write-Host ("  {0,-14} exit {1}" -f $entry.Key, $entry.Value) -ForegroundColor Red
}
exit 1
