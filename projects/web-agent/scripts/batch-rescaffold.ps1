Add-Type -AssemblyName System.Drawing

$srcDir = "D:\yolosam\babytrain\stage3_original\images\train"
$dstDir = "D:\yolosam\babytrain\new\images\train"
$cli = "C:\Program Files\nodejs\node.exe"
$script = "packages/web-agent/web-agent.mjs"
$workdir = "D:\Program Files\web-agent"

$missing = @(
  "1773197474.061871",
  "1773197491.862665",
  "1773197510.599729",
  "1773197583.116767",
  "1773197666.051036",
  "1773197743.510285",
  "1773197786.412267"
)

$prompt = @"
Trace the scaffolding frame from the reference. Exact copy of the pole structure - every pole at its same position, angle, thickness. Color horizontal and vertical poles YELLOW #FFD100. Color diagonal braces RED #FF3B30. Background pure white. Remove curtain, mesh, netting, banner, ground, sky. Only metal poles visible. No text.
"@

$chatName = "scaffold-retry-" + (Get-Date -Format "yyyyMMddHHmmss")
$total = $missing.Count

Write-Host "New chat: $chatName, Total: $total images" -ForegroundColor Magenta

Set-Location -LiteralPath $workdir

function Process-Image {
  param($base, $isFirst, $timeoutMs)

  $srcFile = Join-Path $srcDir "${base}.jpg"
  $outputFile = Join-Path $dstDir "${base}.png"

  if (-not (Test-Path -LiteralPath $srcFile)) {
    Write-Host "  Source not found, skip" -ForegroundColor Red
    return $false
  }

  $taskPayload = @{
    chat = @{ name = $chatName; description = "" }
    task = @{ target = "all"; content = $prompt }
    options = @{
      waitForReplies = $true
      activateChat = $isFirst
      webAgentPage = $false
      timeoutMs = $timeoutMs
      downloadDir = $dstDir
      namePrefix = $base
    }
  }

  if ($isFirst) {
    $taskPayload.roles = @(@{
      source = "temporary"
      name = "豆包画师"
      description = ""
      chatSite = "doubao"
      modelSource = "site"
      systemPrompt = ""
    })
  } else {
    $taskPayload.chat.reuse = @{ strategy = "by-name" }
    $taskPayload.roles = @()
  }

  $tmpJson = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "scaffold-$base.json")
  $json = ConvertTo-Json -InputObject $taskPayload -Depth 10
  [System.IO.File]::WriteAllText($tmpJson, $json, [System.Text.UTF8Encoding]::new($false))

  $result = & $cli $script run create-and-post --file $tmpJson --image-file $srcFile 2>&1
  Remove-Item -LiteralPath $tmpJson -Force -ErrorAction SilentlyContinue

  $resultStr = "$result"
  if ($resultStr -match '"ok":\s*false') {
    Write-Host "  FAILED: $resultStr" -ForegroundColor Red
    return $false
  }

  # Wait for the output file to appear (generated images take time)
  $secondOutput = Join-Path $dstDir "${base}_2.png"
  $firstOutput = Join-Path $dstDir "${base}_1.png"
  $waited = 0
  $maxWait = 300  # 5 min extra wait
  while ($waited -lt $maxWait) {
    $chosen = if (Test-Path -LiteralPath $secondOutput) { $secondOutput }
              elseif (Test-Path -LiteralPath $firstOutput) { $firstOutput }
              else { $null }
    if ($chosen) {
      if (Test-Path -LiteralPath $outputFile) { Remove-Item -LiteralPath $outputFile -Force }
      Rename-Item -LiteralPath $chosen -NewName "${base}.png" -Force
      if ($chosen -eq $secondOutput -and (Test-Path -LiteralPath $firstOutput)) {
        Remove-Item -LiteralPath $firstOutput -Force
      }
      Write-Host "  Done: $base.png" -ForegroundColor Green
      Get-ChildItem -LiteralPath $dstDir -Filter "${base}_*" | Remove-Item -Force -ErrorAction SilentlyContinue
      return $true
    }
    if ($waited -eq 0) { Write-Host "  Waiting for file..." -ForegroundColor Yellow }
    Start-Sleep -Seconds 10
    $waited += 10
    if ($waited % 60 -eq 0) { Write-Host "  ... waited ${waited}s" -ForegroundColor DarkYellow }
  }
  Write-Host "  Still no image for $base" -ForegroundColor Red
  return $false
}

# --- Pass 1: try each with 8 min timeout ---
$retry = @()
$index = 0
foreach ($base in $missing) {
  $index++
  Write-Host "[$index/$total] $base ..." -ForegroundColor Cyan
  $ok = Process-Image -base $base -isFirst ($index -eq 1) -timeoutMs 480000
  if (-not $ok) { $retry += $base }
}

# --- Pass 2: retry failed ones with longer timeout ---
if ($retry.Count -gt 0) {
  Write-Host "`n=== Retrying $($retry.Count) failed images with longer timeout ===" -ForegroundColor Magenta
  $rIndex = 0
  foreach ($base in $retry) {
    $rIndex++
    Write-Host "[$rIndex/$($retry.Count)] $base (retry) ..." -ForegroundColor Cyan
    $ok = Process-Image -base $base -isFirst $false -timeoutMs 900000
  }
}

$successCount = (Get-ChildItem -LiteralPath $dstDir -Filter "*.png").Count
Write-Host "`nDone! $successCount images in output." -ForegroundColor Green
