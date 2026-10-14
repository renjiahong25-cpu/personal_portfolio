# PianoFlow quality gate.
#
# Why this wrapper exists: pre-commit's `language: system` hooks run whatever
# `python` resolves to on PATH. Without this script that is the *base* conda
# interpreter, which has neither pytest nor the lint tooling. So we prepend the
# project environment, run the fast pre-commit loop, then run the heavier checks
# with backend_python as the working directory (that is where pyproject.toml
# lives, and mypy/bandit resolve their config relative to the cwd).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\gate.ps1"
#   powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\gate.ps1" -Governance
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
$envPython = 'D:\conda_envs\pianoflow\python.exe'
$envScripts = 'D:\conda_envs\pianoflow'
$aoci = 'D:\tools\aoci.exe'
# npm on Windows is a .cmd shim; a bare `npm` is not resolvable from PowerShell.
$npm = 'C:\Program Files\nodejs\npm.cmd'
$node = 'C:\Program Files\nodejs\node.exe'

if (-not (Test-Path -LiteralPath $envPython)) {
    throw "找不到项目解释器: $envPython（conda 环境 pianoflow 是否还在？）"
}

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
Push-Location $frontend
& $node tests/notation-highlight.test.mjs
$results['notation-highlight'] = $LASTEXITCODE
Pop-Location

Write-Host "`n=== 6/7  frontend build ===" -ForegroundColor Cyan
Push-Location $frontend
& $npm run build
$results['frontend-build'] = $LASTEXITCODE
Pop-Location

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
