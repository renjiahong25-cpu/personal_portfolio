#requires -Version 5.1
<#
.PSYOPSIS
    PianoFlow first-run asset bootstrap.

.DESCRIPTION
    Checks (and with -Fetch, fetches) the third-party assets the backend runs
    around. Nothing here is mandatory for the core pipeline: when an asset is
    missing the feature degrades and /api/health reports it honestly.

    Asset                                Size     Where it must end up
    ------------------------------------ ------   -----------------------------------------
    basic-pitch ONNX (Apache-2.0)        230 KB   models/basic_pitch/nmp.onnx
                                                 (committed in this repository)
    ByteDance high-res model (cc-by-4.0) 154 MB   models/bytedance/piano_transcription.onnx
                                                 (not committed; manifest records
                                                 URL + licence + sha256)
    MuseScore_General SoundFont (GPL)    206 MB   models/soundfonts/MuseScore_General.sf2
                                                 (not committed)
    fluidSynth                           ~10 MB   tools/fluidsynth/bin/fluidsynth(.exe)
    ffmpeg                               varies   PATH, or tools/ffmpeg/bin

    The two ONNX models are handled by the existing fetch_models.py, which keeps
    models/manifest.json (URL, declared licence, sha256). A hash recorded next
    to a file lets a local copy be verified rather than merely trusted.
    The SoundFont / fluidSynth / ffmpeg are third-party binaries this repository
    does not redistribute; this script verifies what is already present and
    points at the upstream sources for the rest. It never swaps bytes behind
    your back - run -Fetch to download.

    Usage (from the repository root or anywhere; the script finds its own root):

        powershell -ExecutionPolicy Bypass -File backend_python/scripts/bootstrap.ps1
        powershell -ExecutionPolicy Bypass -File backend_python/scripts/bootstrap.ps1 -Fetch

    The backend interpreter is located the same three-tier way as everywhere
    else: PIANOFLOW_PYTHON -> python on PATH -> error.
#>
param(
    [switch]$Fetch
)

$ErrorActionPreference = 'Stop'

# scripts -> backend_python -> <repo root>
$backendRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $backendRoot
$modelsDir = Join-Path $repoRoot 'models'
$toolsDir = Join-Path $repoRoot 'tools'
$fetchScript = Join-Path $backendRoot 'scripts\fetch_models.py'

Write-Host "PianoFlow bootstrap ($repoRoot)" -ForegroundColor Cyan
if ($Fetch) { Write-Host "mode: -Fetch (missing assets will be downloaded)" -ForegroundColor Yellow }

# --- interpreter -------------------------------------------------------------
$python = $env:PIANOFLOW_PYTHON
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $python = $onPath.Source }
}
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "Python interpreter not found. Set PIANOFLOW_PYTHON or put python (3.11+) on PATH."
}
Write-Host "python: $python" -ForegroundColor DarkGray

$ok = 0
$missing = 0
$skip = 0

function Write-Status([string]$name, [string]$state, [string]$detail) {
    $color = switch ($state) { 'OK' { 'Green' } 'MISSING' { 'Yellow' } default { 'DarkGray' } }
    Write-Host ("  [{0,-7}] {1}  {2}" -f $state, $name, $detail) -ForegroundColor $color
}

# --- 1. ONNX models (committed basic-pitch + optional 154 MB bytedance) ------
Write-Host "`n== ONNX models (fetch_models.py + models/manifest.json) ==" -ForegroundColor Cyan
$modelsDir = Join-Path $repoRoot 'models'

$basicPitch = Join-Path $modelsDir 'basic_pitch\nmp.onnx'
if (Test-Path -LiteralPath $basicPitch) {
    $size = [math]::Round((Get-Item -LiteralPath $basicPitch).Length / 1KB)
    Write-Status 'basic-pitch' 'OK' "$size KB (committed; manifest sha256 in models/manifest.json)"
    $ok++
} else {
    Write-Status 'basic-pitch' 'MISSING' 'run:  python backend_python/scripts/fetch_models.py --only basic_pitch'
    $missing++
}

$bytedance = Join-Path $modelsDir 'bytedance\piano_transcription.onnx'
if (Test-Path -LiteralPath $bytedance) {
    $size = [math]::Round((Get-Item -LiteralPath $bytedance).Length / 1MB)
    Write-Status 'bytedance' 'OK' "${size} MB present (run --verify below to check the sha256)"
    $ok++
} elseif ($Fetch) {
    Write-Status 'bytedance' 'FETCH' "downloading 154 MB from the manifest URL..."
    & $python $fetchScript --only bytedance
    if ($LASTEXITCODE -eq 0) { $ok++ } else { $missing++ }
} else {
    Write-Status 'bytedance' 'MISSING' 'optional (high-res pedal analysis); re-run with -Fetch or: python backend_python/scripts/fetch_models.py --only bytedance'
    $skip++
}

# Verify everything that is present (ONNX-ness + sha256 against the manifest).
Write-Host "`n== model verification (fetch_models.py --verify) ==" -ForegroundColor Cyan
& $python $fetchScript --verify
if ($LASTEXITCODE -eq 0) { Write-Host "  all present models valid" -ForegroundColor Green }
else { Write-Host "  see above (absent optional models are listed as failures by design)" -ForegroundColor Yellow }

# --- 2. SoundFont (GPL, 206 MB, not redistributed) ---------------------------
Write-Host "`n== SoundFont (follow-along render) ==" -ForegroundColor Cyan
$soundfont = Join-Path $modelsDir 'soundfonts\MuseScore_General.sf2'
if ($env:PIANOFLOW_SOUNDFONT_PATH -and (Test-Path -LiteralPath $env:PIANOFLOW_SOUNDFONT_PATH)) {
    Write-Status 'soundfont' 'OK' "PIANOFLOW_SOUNDFONT_PATH -> $env:PIANOFLOW_SOUNDFONT_PATH"
    $ok++
} elseif (Test-Path -LiteralPath $soundfont) {
    $size = [math]::Round((Get-Item -LiteralPath $soundfont).Length / 1MB)
    if ($size -lt 100) {
        Write-Status 'soundfont' 'MISSING' "${size} MB is too small to be the real file (expected ~206 MB)"
        $missing++
    } else {
        Write-Status 'soundfont' 'OK' "${size} MB"
        $ok++
    }
} else {
    Write-Status 'soundfont' 'MISSING' "optional; place MuseScore_General.sf2 at $soundfont"
    Write-Host "  source: MuseScore 3.x (GPL) - ships inside the MuseScore 3 installer," -ForegroundColor DarkGray
    Write-Host "  download page: https://musescore.com/en/download (archive) or any of the" -ForegroundColor DarkGray
    Write-Host "  3.x release zips; the file is share/soundfont/MuseScore_General.sf2 inside." -ForegroundColor DarkGray
    Write-Host "  licence is GPL: use it locally, do not redistribute it in this repository." -ForegroundColor DarkGray
    $skip++
}

# --- 3. fluidSynth ------------------------------------------------------------
Write-Host "`n== fluidSynth (follow-along render) ==" -ForegroundColor Cyan
$fluidsynthDir = Join-Path $toolsDir 'fluidsynth\bin'
$found = $null
if ($env:PIANOFLOW_FLUIDSYNTH_LOCATION -and (Test-Path -LiteralPath $env:PIANOFLOW_FLUIDSYNTH_LOCATION)) {
    $exeName = if ($env:OS) { 'fluidsynth.exe' } else { 'fluidsynth' }
    if (Test-Path -LiteralPath (Join-Path $env:PIANOFLOW_FLUIDSYNTH_LOCATION $exeName)) { $found = $env:PIANOFLOW_FLUIDSYNTH_LOCATION }
}
if (-not $found) {
    $onPath = Get-Command fluidsynth -ErrorAction SilentlyContinue
    if ($onPath) { $found = Split-Path -Parent $onPath.Source }
}
if (-not $found) {
    foreach ($exeName in @('fluidsynth.exe', 'fluidsynth')) {
        if (Test-Path -LiteralPath (Join-Path $fluidsynthDir $exeName)) { $found = $fluidsynthDir; break }
    }
}
if ($found) {
    Write-Status 'fluidsynth' 'OK' "$found"
    $ok++
} else {
    Write-Status 'fluidsynth' 'MISSING' "optional; place the release so that $fluidsynthDir\fluidsynth.exe exists"
    Write-Host "  source: https://github.com/fluidsynth/fluidsynth/releases (LGPL)." -ForegroundColor DarkGray
    Write-Host "  Windows: download the win64 zip, extract it, and keep the bin/ layout:" -ForegroundColor DarkGray
    Write-Host "  $toolsDir\fluidsynth\bin\fluidsynth.exe (+ the DLLs beside it)." -ForegroundColor DarkGray
    Write-Host "  Or set PIANOFLOW_FLUIDSYNTH_LOCATION to a directory containing fluidsynth." -ForegroundColor DarkGray
    $skip++
}

# --- 4. ffmpeg ----------------------------------------------------------------
Write-Host "`n== ffmpeg (upload transcode / yt-dlp post-processing) ==" -ForegroundColor Cyan
$ffmpegDir = Join-Path $toolsDir 'ffmpeg\bin'
$ffmpegFound = $null
if ($env:PIANOFLOW_FFMPEG_LOCATION -and (Test-Path -LiteralPath $env:PIANOFLOW_FFMPEG_LOCATION)) {
    $exeName = if ($env:OS) { 'ffmpeg.exe' } else { 'ffmpeg' }
    if (Test-Path -LiteralPath (Join-Path $env:PIANOFLOW_FFMPEG_LOCATION $exeName)) { $ffmpegFound = $env:PIANOFLOW_FFMPEG_LOCATION }
}
if (-not $ffmpegFound) {
    $onPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($onPath) { $ffmpegFound = Split-Path -Parent $onPath.Source }
}
if (-not $ffmpegFound) {
    foreach ($exeName in @('ffmpeg.exe', 'ffmpeg')) {
        if (Test-Path -LiteralPath (Join-Path $ffmpegDir $exeName)) { $ffmpegFound = $ffmpegDir; break }
    }
}
if ($ffmpegFound) {
    Write-Status 'ffmpeg' 'OK' "$ffmpegFound"
    $ok++
} else {
    Write-Status 'ffmpeg' 'MISSING' "optional; put a build on PATH or under $ffmpegDir"
    Write-Host "  source: https://www.ffmpeg.org/download.html (or conda-forge: conda install -c conda-forge ffmpeg)." -ForegroundColor DarkGray
    Write-Host "  Or set PIANOFLOW_FFMPEG_LOCATION to a directory containing ffmpeg." -ForegroundColor DarkGray
    $skip++
}

# --- summary -------------------------------------------------------------------
Write-Host ""
Write-Host ("summary: {0} ok, {1} missing-required, {2} optional-skip" -f $ok, $missing, $skip) -ForegroundColor Cyan
if ($missing -gt 0) {
    Write-Host "re-run with -Fetch to download what can be downloaded automatically." -ForegroundColor Yellow
    exit 1
}
Write-Host "done. start the backend with scripts\start-backend.ps1" -ForegroundColor Green
