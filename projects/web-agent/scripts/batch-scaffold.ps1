Add-Type -AssemblyName System.Drawing

$srcDir = "D:\yolosam\babytrain\stage3_original\images\train"
$dstDir = "D:\yolosam\babytrain\new\images\train"
$cli = "C:\Program Files\nodejs\node.exe"
$script = "packages/web-agent/web-agent.mjs"
$workdir = "D:\Program Files\web-agent"

function Resize-ToMatchSource {
  param([string]$outputPath, [string]$sourcePath)
  try {
    $srcImg = [System.Drawing.Image]::FromFile($sourcePath)
    $w = $srcImg.Width
    $h = $srcImg.Height
    $srcImg.Dispose()
    $outImg = [System.Drawing.Image]::FromFile($outputPath)
    if ($outImg.Width -eq $w -and $outImg.Height -eq $h) { $outImg.Dispose(); return }
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($outImg, 0, 0, $w, $h)
    $g.Dispose()
    $outImg.Dispose()
    $bmp.Save($outputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Host "  Resized to ${w}x${h}" -ForegroundColor Gray
  } catch {
    Write-Host "  Resize failed: $_" -ForegroundColor Yellow
  }
}

$prompt = @"
(((Strictly 1:1 pixel match all original metal scaffolding poles in the reference image, keep their position, angle, size, layout unchanged. No shift, no deformation, no adding/removing any poles. Preserve all thin rod ends and cross joints.))),

(((Strict classification color rules, NO color misassignment:
1.  All horizontal crossbars (parallel to the ground) and all vertical ground-standing poles: solid pure yellow (#FFD100), NO exceptions.
2.  Only diagonal/angled metal poles (scaffold braces/scissors cross braces): solid pure red (#FF3B30).
3.  The red-white striped safety belts/banners are NOT poles: they must be completely erased and not treated as metal scaffolding.))),

(((100% remove all non-pole elements:
- Erase all red-white striped safety belts/warning banners, protective mesh, concrete wall, ground, sky, crane, construction debris, wooden boards, wires, shadows, halos, and background noise.
- Only retain metal scaffolding poles, no other objects.))),

(((Pure solid white background (#FFFFFF), no shadows, no texture, no gradients, no residual details.))),

High-precision industrial schematic render, sharp clean pole edges, high contrast, no blur, no artifacts, ultra high quality.

--no red horizontal poles, red-white safety tape, warning strip, red banner, mesh, flexible belt, deformed pole, oversized pole, broken pole, missing pole, hollow pole, blurred edge, shadow, halo, background residue, noise, texture, mixed colors, color bleeding, gradients, crane, sky, concrete wall, ground debris
"@

$chatName = "scaffold-batch-" + (Get-Date -Format "yyyyMMddHHmmss")
$images = Get-ChildItem -LiteralPath $srcDir -File | Where-Object { $_.Extension -match '\.(jpg|jpeg|png|webp)$' } | Sort-Object Name
$total = $images.Count

Write-Host "Chat: $chatName, Total: $total images" -ForegroundColor Magenta

$index = 0
foreach ($img in $images) {
  $index++
  $base = [System.IO.Path]::GetFileNameWithoutExtension($img.Name)
  $outputFile = Join-Path $dstDir "$base.png"
  if (Test-Path -LiteralPath $outputFile) {
    Write-Host "[$index/$total] $base.png exists, skip" -ForegroundColor Gray
    continue
  }
  Write-Host "[$index/$total] $($img.Name) ..." -ForegroundColor Cyan

  $isFirst = $index -eq 1
  $taskPayload = @{
    chat = @{ name = $chatName; description = "" }
    task = @{ target = "all"; content = $prompt }
    options = @{
      waitForReplies = $true
      activateChat = $isFirst
      webAgentPage = $isFirst
      timeoutMs = 600000
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

  $result = & $cli $script run create-and-post --file $tmpJson --image-file $img.FullName 2>&1
  Remove-Item -LiteralPath $tmpJson -Force -ErrorAction SilentlyContinue

  $resultStr = "$result"
  if ($resultStr -match '"ok":\s*false') {
    Write-Host "  FAILED: $resultStr" -ForegroundColor Red
    continue
  }

  $firstOutput = Join-Path $dstDir "${base}_1.png"
  $targetFile = Join-Path $dstDir "${base}.png"

  if (Test-Path -LiteralPath $firstOutput) {
    Rename-Item -LiteralPath $firstOutput -NewName "$base.png" -Force
    Resize-ToMatchSource -outputPath $targetFile -sourcePath $img.FullName
    Write-Host "  Done: $base.png" -ForegroundColor Green
  } else {
    Write-Host "  No images yet, waiting 120s extra..." -ForegroundColor Yellow
    $extraWaited = 0
    while ($extraWaited -lt 120) {
      Start-Sleep -Seconds 10
      $extraWaited += 10
      if (Test-Path -LiteralPath $firstOutput) {
        Rename-Item -LiteralPath $firstOutput -NewName "$base.png" -Force
        Resize-ToMatchSource -outputPath $targetFile -sourcePath $img.FullName
        Write-Host "  Done (delayed): $base.png" -ForegroundColor Green
        break
      }
      Write-Host "  ... waited ${extraWaited}s" -ForegroundColor DarkYellow
    }
    if (-not (Test-Path -LiteralPath $outputFile)) {
      Write-Host "  Still no image for $base, check Doubao page" -ForegroundColor Red
    }
  }

  Get-ChildItem -LiteralPath $dstDir -Filter "${base}_*" | Remove-Item -Force -ErrorAction SilentlyContinue
}

$successCount = (Get-ChildItem -LiteralPath $dstDir -Filter "*.png").Count
Write-Host "`nDone! $successCount/$total images." -ForegroundColor Green