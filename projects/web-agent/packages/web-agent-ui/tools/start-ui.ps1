# Web Agent 桌面客户端启动器
# 1. 检测本地守护进程(19305)，未运行则自动拉起
# 2. 检测 opencode serve(4096)，未监听则自动拉起
# 3. 启动编译好的桌面客户端
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$DaemonScript = Join-Path $Root 'packages\web-agent\web-agent-daemon.mjs'
$Node = if (Get-Command node -ErrorAction SilentlyContinue) { (Get-Command node).Source } else { 'C:\Program Files\nodejs\node.exe' }
$Opencode = $env:WEB_AGENT_OPENCODE
if (-not $Opencode) {
    $ocCmd = Get-Command opencode -ErrorAction SilentlyContinue
    if ($ocCmd) { $Opencode = $ocCmd.Source }
}
if (-not $Opencode) { $Opencode = Join-Path $env:APPDATA 'npm\node_modules\opencode-ai\bin\opencode.exe' }
$Exe = Join-Path $PSScriptRoot '..\build\windows\x64\runner\Release\web_agent_ui.exe'
$DaemonLog = Join-Path $env:TEMP 'web-agent-daemon.log'
$DaemonErr = Join-Path $env:TEMP 'web-agent-daemon.err.log'
$OpencodeLog = Join-Path $env:TEMP 'opencode-serve.log'
$OpencodeErr = Join-Path $env:TEMP 'opencode-serve.err.log'

function Test-PortReady {
  param([string]$Uri, [int]$TimeoutSec = 2)
  try {
    $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec $TimeoutSec
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Test-Daemon {
  Test-PortReady -Uri 'http://127.0.0.1:19305/status'
}

function Ensure-OpencodeServe {
  $ready = Test-PortReady -Uri 'http://127.0.0.1:4096/global/health'
  if ($ready) {
    Write-Host 'opencode serve 已在监听 4096。'
    return
  }
  if (-not (Test-Path $Opencode)) {
    Write-Host "找不到 opencode: $Opencode" -ForegroundColor Yellow
    Write-Host 'opencode 模式不可用，将只启动 web-agent 模式。' -ForegroundColor Yellow
    return
  }
  Write-Host 'opencode serve 未监听，正在拉起（工作目录: 项目根）…'
  Start-Process -FilePath $Opencode -ArgumentList 'serve' -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $OpencodeLog -RedirectStandardError $OpencodeErr
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-PortReady -Uri 'http://127.0.0.1:4096/global/health') {
      Write-Host 'opencode serve 已就绪。'
      return
    }
  }
  Write-Host 'opencode serve 启动超时，查看日志:' -ForegroundColor Yellow
  if (Test-Path $OpencodeErr) { Get-Content $OpencodeErr -Tail 20 }
  if (Test-Path $OpencodeLog) { Get-Content $OpencodeLog -Tail 20 }
}

if (-not (Test-Path $DaemonScript)) {
  Write-Host "找不到启动脚本: $DaemonScript" -ForegroundColor Red
  exit 1
}

if (-not (Test-Daemon)) {
  Write-Host '守护进程未运行，正在启动…'
  if (-not (Test-Path $Node)) {
    Write-Host "找不到 Node.js: $Node" -ForegroundColor Red
    exit 1
  }
  Start-Process -FilePath $Node -ArgumentList $DaemonScript -WindowStyle Hidden -RedirectStandardOutput $DaemonLog -RedirectStandardError $DaemonErr
  $ready = $false
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Daemon) {
      $ready = $true
      break
    }
  }
  if ($ready) {
    Write-Host '守护进程已就绪。'
  } else {
    Write-Host '守护进程启动超时，查看日志:' -ForegroundColor Yellow
    if (Test-Path $DaemonErr) { Get-Content $DaemonErr -Tail 20 }
    exit 1
  }
} else {
  Write-Host '守护进程已在运行。'
}

Ensure-OpencodeServe

# 无头 Chrome + 扩展（复用独立 profile，让扩展后台连接守护进程）
$Dist = Join-Path $Root 'dist'
$Chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$ProfileDir = Join-Path $env:LOCALAPPDATA 'web-agent-headless'

if (-not (Test-Path $Chrome)) {
  Write-Host "找不到 Chrome: $Chrome，跳过无头浏览器。" -ForegroundColor Yellow
} elseif (-not (Test-Path (Join-Path $Dist 'manifest.json'))) {
  Write-Host "扩展未构建: $Dist，请先运行 npm run build。" -ForegroundColor Yellow
} else {
  $already = @()
  try {
    $already = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction Stop |
      Where-Object { $_.CommandLine -match [regex]::Escape($ProfileDir) })
  } catch { }
  if ($already.Count -gt 0) {
    Write-Host '无头 Chrome 已在运行。'
  } else {
    Write-Host '启动无头 Chrome 并加载扩展…'
    # headless=new 不支持多个目标 URL，只开主站点标签页；
    # 豆包 / ChatGPT 等站点由用户后续打开即可（扩展会在新标签注入）。
    $ChromeArgs = '--headless=new --disable-gpu --no-first-run --no-default-browser-check ' +
      '"--user-data-dir=' + $ProfileDir + '" ' +
      '"--load-extension=' + $Dist + '" ' +
      '"https://chat.deepseek.com/chat/"'
    Start-Process -FilePath $Chrome -ArgumentList $ChromeArgs
  }
}

if (-not (Test-Path $Exe)) {
  Write-Host "找不到客户端程序: $Exe" -ForegroundColor Red
  Write-Host '请先运行 flutter build windows --release' -ForegroundColor Yellow
  exit 1
}

Write-Host "启动客户端: $Exe"
Start-Process -FilePath $Exe