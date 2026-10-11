# 环境说明（每个新窗口必读）

## 1. Python 环境（Python 3.10+，推荐 3.12）

下文以 `<REPO_ROOT>` 指代本项目根目录，`<PY>` 指代项目环境内的 python 可执行文件。

### venv（推荐，跨平台）

```powershell
Set-Location "<REPO_ROOT>"
python -m venv .venv
& "<REPO_ROOT>\.venv\Scripts\activate"
pip install -r requirements.txt
$py = "<REPO_ROOT>\.venv\Scripts\python.exe"
& $py -c "import httpx; print('ok')"
```

### Conda（可选）

```powershell
conda create -n venue-scout python=3.12 -y
conda activate venue-scout
pip install -r requirements.txt
$py = "$env:CONDA_PREFIX\python.exe"
```

> Windows 下 PDF 交付（docx→PDF）依赖 Word COM（pywin32），已按平台条件写入 requirements.txt；Linux/macOS 上该步骤不可用，PDF 交付需自备转换器。

## 2. 配置文件

```powershell
Copy-Item config\settings.example.yaml config\settings.yaml
# 编辑 settings.yaml：填 amap.key（必填）、ctrip.cookie / xiaohongshu.cookie（可选）
```

### 高德 Key（P6 必填）

1. 打开 https://console.amap.com/dev/key/app
2. 创建应用 → Key 类型选 **Web服务**（不要选 Web 端 JS）
3. 填入 `config\settings.yaml` → `amap.key`

### 携程 Cookie（可选，提升详情页成功率）

1. 浏览器登录 https://hotels.ctrip.com/
2. F12 → Network → 任意请求 → Request Headers → 复制 `Cookie`
3. 填入 `settings.yaml` → `ctrip.cookie`
4. 若需要登录态采集，告诉 agent：「携程已登录，cookie 在 settings.yaml」

### 小红书 Cookie（可选，P3）

1. 登录 https://www.xiaohongshu.com/ 后复制 Cookie
2. 填入 `xiaohongshu.cookie` 并将 `xiaohongshu.enabled: true`

## 3. 运行入口

```powershell
& $py scripts\run_pipeline.py          # 全流程（需 amap.key）
& $py scripts\probe_ctrip_meeting.py   # P0 探测（--mode all / --keyword 名称）
& $py scripts\test_ctrip_seeds.py      # 种子名 → 携程 suggest+aggregate 批量
& $py scripts\query.py --top 10        # 查询主库
& $py scripts\ensure_service.py        # 启动/守护 Web 服务（端口 8000）
& $py scripts\ensure_browser.py        # 打开有头前端窗口
```

**PowerShell 注意**：不要把复杂 `python -c "f\"...\""` 写进内联命令（引号会被吞，见 BUG-0008）；测试一律落盘 `.py` 再执行。

## 4. 故障时

1. 查 `BUGS.md` 是否已有同类问题
2. 查 `PLAN.md` 当前 step 与 checkpoint 回退方式
3. 确认 `$py` 指向项目环境（`& $py -c "import fastapi, ortools"` 无 ImportError）

## 5. 依赖清单

见 `requirements.txt`：fastapi, uvicorn, httpx, pydantic, pyyaml, playwright, tenacity, rich, python-docx, ortools（+ Windows 下 pywin32 用于 docx→PDF）
