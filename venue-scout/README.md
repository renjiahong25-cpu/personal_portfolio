# venue-scout · 团建场地采集 + 智能行程规划

> 给城市团建做**场地采集 → 智能筛选 → 行程规划 → 攻略细节 → 文档交付**的全自动流水线。
> 输入一行"合肥团建一天 20 人"，输出一份《团建场地推荐清单》+ 一份《一日行程方案》(PDF/Word/Markdown)。
> 核心链路（采集/筛选/规划）**完全离线、无 LLM 依赖**；小红书攻略细节层为可插拔增强。

**English quick start** → [below](#venue-scout---team-building-venue-scout--itinerary-planner) · 先看中文小白指南。

---

## 目录

- [一、你会得到什么](#一你会得到什么)
- [二、准备工作（一次性）](#二准备工作一次性)
- [三、安装（10 分钟）](#三安装10-分钟)
- [四、申请高德 API Key（必做）](#四申请高德-api-key必做)
- [五、启动并使用](#五启动并使用)
- [六、进阶：用 AI 助手（opencode）对话式使用](#六进阶用-ai-助手opencode对话式使用)
- [七、可选：小红书攻略细节层](#七可选小红书攻略细节层)
- [八、常见问题 FAQ](#八常见问题-faq)
- [九、隐私与合规](#九隐私与合规)
- [十、已知限制](#十已知限制)
- [License](#license)

---

## 一、你会得到什么

| 交付物 | 说明 |
|---|---|
| 📊 `团建场地推荐清单.xlsx` | 全部合格场地，含设施/场地/价格/来源链接，可筛选排序 |
| 📄 `团建场地推荐清单.docx` / `.pdf` | 清单文档版（PDF 需要电脑装有 Microsoft Word） |
| 🗺️ `方案_<场地名>_ai.pdf` / `.docx` / `.md` | 选定场地的一日行程方案：逐时段安排 + 费用估算 + 风险提示 |
| 📕 攻略细节（可选） | 若启用小红书层：方案中内嵌真实游玩攻略（玩法/价格/坑），带来源链接 |

## 二、准备工作（一次性）

| 需要 | 用途 | 怎么确认已有 |
|---|---|---|
| **Windows 10/11** | 项目在 Windows 上开发与验证（macOS/Linux 代码层面兼容，但浏览器/Word 自动化未验证） | — |
| **Microsoft Edge** | 打开操作页面 + 可选的登录自动化 | 系统自带，无 |
| **Python 3.10+** | 运行流水线 | 见下方安装方法 |
| **Git**（可选） | 克隆仓库；没有 Git 也可以直接下载 ZIP | 见下方安装方法 |
| **高德开放平台账号** | 地图/POI 数据源（免费额度足够个人使用） | 没有就去[申请](#四申请高德-api-key必做) |
| Microsoft Word（可选） | PDF 导出依赖 Word 转换；没有也能用，只是交付物没有 PDF | — |
| Node.js（可选） | 仅当你想用 [opencode](#六进阶用-ai-助手opencode对话式使用) 对话式使用时需要 | — |

**安装 Python**（没有的话）：
1. 打开 <https://www.python.org/downloads/>，点黄色按钮下载最新版
2. 运行安装程序，**第一屏底部务必勾选 "Add python.exe to PATH"**，再点 "Install Now"
3. 验证：新开一个 PowerShell 窗口输入 `python --version`，显示 `Python 3.x.x` 即成功

**安装 Git**（可选，想用 `git clone` 才需要）：
1. 打开 <https://git-scm.com/download/win> 下载安装
2. 一路默认选项即可
3. 验证：`git --version`

## 三、安装（10 分钟）

以下命令都在 **PowerShell**（Win+X → Windows PowerShell）里执行。

### 第 1 步：拿到代码

```powershell
# 方式 A：用 Git 克隆（推荐）
git clone https://github.com/<你的账号>/venue-scout.git
cd venue-scout

# 方式 B：没有 Git —— 在 GitHub 仓库页面点 "Code" → "Download ZIP"，
# 下载解压后 cd 进 venue-scout 目录
```

### 第 2 步：创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

> ⚠️ 如果 `Activate.ps1` 报"禁止运行脚本"：先执行一次
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，输入 `Y` 回车，再重试上面两条。
> 安装成功标志：最后一行出现 `Successfully installed ... fastapi ... ortools ...`。

### 第 3 步：（可选）准备示例数据

仓库自带 `data/seeds.json` 示例场地，**不配置任何东西也能启动看页面**。
想跑真实采集请继续下一步。

## 四、申请高德 API Key（必做）

采集依赖高德地图的 POI 数据，需要一个免费的 Web 服务 Key：

1. 打开 <https://console.amap.com>，用手机号登录（没有账号会自动注册）
2. 左侧 **应用管理** → **创建新应用**：名称随意（如 `demo`），类型选"其它"，确认
3. 点进刚创建的应用 → **添加 Key**：名称随意，**服务平台只勾选 "Web服务"**，完成安全设置后创建
4. 复制生成的 32 位 Key

## 五、启动并使用

### 1. 写配置

```powershell
copy config\settings.example.yaml config\settings.yaml
```

用记事本打开 `config\settings.yaml`，把刚才的 Key 填进去：

```yaml
amap:
  key: "在这里粘贴你的高德Key"
  city: 合肥市            # 默认采集城市（页面上可以改）
  radius_km: 60           # 采集半径
  max_drive_min: 90       # 单程车程上限（分钟）
```

其它项都可以先不动（留空 = 不启用）。

### 2. 一键启动

```powershell
python scripts\ensure_service.py
```

它会自动：检查依赖 → 启动服务（端口 8000）→ **自动打开 Edge 浏览器并自动填入访问密钥**。
看到浏览器里的页面 = 成功。以后每次启动都跑这一条命令即可。

### 3. 使用流程（页面上点）

1. 顶部输入框填**方向**（如 `合肥市`），点 **开始采集**
2. 等采集结束（约 1-5 分钟，页面自动提示"已入池 N 家"）
3. **Top 10 复核**：逐个看候选卡片，✅ 选用 / ❌ 淘汰（可以全看，也可以只看前几个）
4. 底部"已选 N 家"勾选最终要出清单的场地 → 点 **确认选择（触发 AI 批次）**
5. 自动生成《一日行程方案》：点开看逐时段安排与风险提示，没毛病就 **确认方案（触发细节层）**
6. 页面底部"下载交付物"区：推荐清单（Excel/Word/PDF）+ 行程方案（PDF/Word/Markdown），
   文件生成好按钮即可点，点一下用系统默认程序直接打开
7. （可选）若已装小红书层，方案里会自动内嵌真实攻略细节

### 4. 命令行方式（不想开浏览器时）

```powershell
# 采集（参数全可选，看帮助）
python scripts\dialog_agent.py collect --origin-name 合肥 --target 5

# 看当前进度 / 事件
python scripts\dialog_agent.py status
```

## 六、进阶：用 AI 助手（opencode）对话式使用

装好 [OpenCode](https://opencode.ai)（终端里的 AI 编程助手）后，你可以直接说
**"帮我规划一个合肥一日团建 20 人"**，它会调用本仓库内置的 skill（`skills/venue-scout/SKILL.md`）
按约定自动跑完整流程（采集→筛选→规划→细节→交付），不用记任何命令。

### 1. 安装 OpenCode

先装 **Node.js**（<https://nodejs.org> 下载 LTS 版，一路默认）——或选下面任意一种：

| 系统 | 命令 |
|---|---|
| Windows（Chocolatey） | `choco install opencode` |
| Windows（Scoop） | `scoop install opencode` |
| Windows（npm） | `npm install -g opencode-ai` |
| macOS / Linux（官方脚本，推荐） | `curl -fsSL https://opencode.ai/install \| bash` |
| macOS（Homebrew） | `brew install anomalyco/tap/opencode` |

> 官方建议 Windows 用户优先考虑 WSL（`wsl --install` 后在 WSL 里装），兼容性最好。

验证：`opencode --version`

### 2. 首次配置（选一个 AI 模型）

```powershell
cd venue-scout
opencode
```

进入界面后输入 `/connect`：选一个模型服务商（如 OpenCode Zen / Anthropic / OpenAI 等），
按提示填 API key（各服务商官网申请，多数有免费额度）。

### 3. 安装本项目的 skill

把仓库里的 skill 复制到 OpenCode 的项目 skill 目录：

```powershell
# PowerShell 里执行（在项目根目录）
mkdir .opencode\skills -Force
copy skills\venue-scout .opencode\skills\venue-scout -Recurse -Force
```

### 4. 使用

重新 `opencode` 进入，直接说人话：

```
帮我采集合肥的团建场地，规划一个一日行程，20 人，下午 3 点前返程
```

它会自己读 skill、起服务、跑采集、出方案。中途需要人工确认的环节（复核 Top10、
确认选择）它会等你在页面上点完再继续。

## 七、可选：小红书攻略细节层

默认关闭。启用后，"确认方案"会自动搜小红书真实游玩笔记，把玩法/价格/坑内嵌进方案文档。

```powershell
# 1. 克隆 MediaCrawler（第三方，见 vendor/README.md）
git clone https://github.com/NanmiCoder/MediaCrawler vendor\MediaCrawler

# 2. 安装它的依赖（在同一个 venv 激活状态下）
python -m pip install -r vendor\MediaCrawler\requirements.txt

# 3. 准备 Cookie：Edge 登录 xiaohongshu.com → F12 → Application → Cookies →
#    复制 web_session 的值，填到 config\settings.yaml 的 xiaohongshu.cookie（或 xhs.cookie）

# 4. 手动试跑（或直接让 agent 在流程里跑）
python scripts\xhs_research.py --venue-id amap:B0M6M6U9KK --notes 1
```

> ⚠️ 请遵守小红书 ToS，控制频率，仅个人低频使用；详见[隐私与合规](#九隐私与合规)。

## 八、常见问题 FAQ

**Q: 提示"禁止运行脚本"？**
A: PowerShell 执行策略限制。运行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 后重试。

**Q: 端口 8000 被占用？**
A: 多半是上次服务没退干净：`Get-Process python | Where-Object {$_.Path -like '*venue-scout*'} | Stop-Process` 再重启。

**Q: PDF 生成失败？**
A: PDF 导出用 Microsoft Word 转换，未装 Word 会跳过 PDF（MD/DOCX/XLSX 不受影响）。装 Word 后重新"重新生成"。

**Q: 小红书 Cookie 过期？**
A: 重新登录 xiaohongshu.com，按第七节第 3 步重新抓 `web_session`。

**Q: 控制台中文乱码？**
A: PowerShell 先执行 `[Console]::OutputEncoding = [Text.Encoding]::UTF8`。

**Q: 小城市候选很少？**
A: 正常。可加大 `radius_km` / `max_drive_min`，或用 `--hub` 指定周边城市扩采（agent 模式直接说"周边也看看"）。

**Q: 数据存在哪？能重置吗？**
A: 全部在 `data/`（场地库 `data/venues.json`、会话 `data/output/session_plan.json`）。删 `data/venues.json` 即重置场地库（下次采集重建）。

## 九、隐私与合规

- **高德 Key**：只存本地 `config/settings.yaml`（已被 .gitignore 排除，不会进仓库）；走官方 Web 服务 API，不爬取高德。
- **携程/美团/点评**：不爬、不存任何价格或用户数据。
- **小红书**：默认关闭；启用即视为你已获得相应 Cookie 授权，请自行控制频率并遵守其服务条款。
- **本仓库不含任何真实采集数据**：`data/venues.json` 不入库，只带 2KB 的 `data/seeds.json` 示例。

## 十、已知限制

- 价格/设施字段来自公开 POI 与网页，时效性有限；建议把推荐清单当"初筛"，出行前电话确认
- 行程规划为规则引擎（无 LLM）：质量稳定但创意有限；文案可后续接任意 LLM 润色
- 平台自动化基于 Windows + Edge 验证；macOS/Linux 需自行适配浏览器与 Word 转换
- 小红书层为第三方 MediaCrawler 的封装，其可用性受平台风控影响

---

<a id="venue-scout---team-building-venue-scout--itinerary-planner"></a>
# venue-scout — Team-building venue scout & itinerary planner (EN)

An offline-first pipeline for city team-building trips: **venue collection → smart filtering →
itinerary planning → (optional) real-trip guide enrichment → document delivery**.
Type one line — "Hefei, 1 day, 20 people" — and get a venue shortlist (Excel/Word/PDF) plus a
day-plan (PDF/Word/Markdown). The core chain (collect / filter / plan) is **LLM-free and
deterministic**; the Xiaohongshu (XHS) enrichment layer is a pluggable add-on.

## Features

| Area | What you get |
|---|---|
| Multi-source collection | Amap POI (hub + expand + nearby-rescan), web seed expansion, manual/XHS sources; unified `Venue` model |
| Price verification | Ctrip team-building page parse (optional, cookie-gated) |
| Rule-based filtering | Origin distance / drive time / price band / facility match / junk-title veto / capacity heuristic |
| Deterministic planner | Day-plan builder (per-venue `poi_kind` mapping: meal, boating, hot spring, water park, sight, activity, lodging…), time-budget trimming, clean warnings in the user's own words |
| Deliverables | `团建场地推荐清单.xlsx/.docx/.pdf`, `方案_<venue>_ai.pdf/.docx/.md`; plan PDF embeds per-slot XHS guide details with source links |
| Web UI | Top-10 review (✅/❌), selection → auto AI batch → plan → details; "open deliverables" buttons |
| Event-driven | `venues_confirmed` → auto AI plan; `plan_adopted` → auto XHS detail layer |
| LLM-free core | No LLM in collect/filter/plan; the detail layer is agent-driven analysis (no model API needed in the repo) |

## Install (Windows; ~10 min)

Prereqs: **Python 3.10+** (python.org — check *"Add python.exe to PATH"*), **Git** (optional;
you can download the ZIP from GitHub instead), an **Amap (Gaode) account**.

```powershell
git clone https://github.com/<you>/venue-scout.git
cd venue-scout
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy config\settings.example.yaml config\settings.yaml
# edit config\settings.yaml → paste your Amap "Web services" key (console.amap.com → 应用管理 → 添加 Key)
```

Browser automation uses the system **Microsoft Edge** (`channel="msedge"`); no
`playwright install` required.

## Run

```powershell
python scripts\ensure_service.py        # starts :8000, auto-opens Edge with the access key
# or, headless:
python scripts\dialog_agent.py collect --origin-name Hefei --target 5
python scripts\dialog_agent.py status
```

Web flow: enter direction → **start collection** → review Top-10 (✅/❌) → **confirm selection**
(auto day-plan) → **adopt plan** (auto XHS detail layer, if enabled) → **open deliverables**.

## Use with OpenCode (optional, conversational mode)

1. Install [OpenCode](https://opencode.ai): `npm i -g opencode-ai` (Node) /
   `curl -fsSL https://opencode.ai/install | bash` (macOS & Linux) / `choco install opencode` or
   `scoop install opencode` (Windows; WSL recommended).
2. `cd venue-scout && opencode`, then `/connect` to configure a model provider + API key.
3. Install this repo's skill:
   ```powershell
   mkdir .opencode\skills -Force
   copy skills\venue-scout .opencode\skills\venue-scout -Recurse -Force
   ```
4. Just ask: *"Scout team-building venues in Hefei and plan a 1-day trip for 20 people, back by 15:00."*
   The agent loads `skills/venue-scout/SKILL.md` and drives the whole pipeline, pausing at the
   human-review gates (Top-10 review, plan adoption) on the web page.

## Optional: Xiaohongshu detail layer

```powershell
git clone https://github.com/NanmiCoder/MediaCrawler vendor\MediaCrawler
python -m pip install -r vendor\MediaCrawler\requirements.txt
# put your XHS web_session cookie into config\settings.yaml (xiaohongshu.cookie / xhs.cookie)
python scripts\xhs_research.py --venue-id amap:B0M6M6U9KK --notes 1
```

Respect XHS ToS — personal, low-frequency use only (see Privacy).

## Architecture

```
src/
├─ models.py            Venue / Plan dataclasses
├─ amap_client.py       Amap POI (place/text, place/around, distance matrix)
├─ amap_collector.py    hub + expand + nearby-rescan collection
├─ seed_expansion.py    web-seed expansion (manual / xhs entries)
├─ ctrip_client.py      Ctrip price verification (cookie-gated, optional)
├─ filter_rules.py      deterministic filtering rules
├─ planner.py           day-plan builder (per-venue poi_kind slots, time budgets, warnings)
├─ plan_intake.py       plan answers → session state
├─ webapp.py            FastAPI: review UI, /api/*, /download/*, event pipeline
├─ events.py            event log (venues_confirmed / plan_adopted / …)
├─ creds.py / cookie_login.py   local credential store + login helpers (never committed)
└─ utils.py
scripts/
├─ run_pipeline.py      full pipeline entry (collect → filter → plan → docs)
├─ refresh_facilities.py├─ rescan_nearby.py
├─ xhs_research.py      XHS search + crawl → guide detail JSON (agent analyzes notes)
├─ render_plan_doc.py   plan → md/docx/pdf (embeds per-slot XHS details + source links)
├─ dialog_agent.py      agent CLI: collect / plan-batch / status / events
├─ ensure_service.py    one-shot service launch (+ auto-open Edge with access key)
├─ open_front_ui.py     real Edge launch, CDP gate pass, readiness guard
├─ ensure_browser.py    browser lifecycle helper
├─ ui_smoke.py          headless UI smoke test
└─ watch_events.py      event watcher
skills/venue-scout/    OpenCode skill (conversational workflow contract)
vendor/                MediaCrawler (third-party, not in repo)
```

## Privacy & compliance

- Amap key lives only in local `config/settings.yaml` (gitignored). Official Web-service API only.
- No Ctrip/Meituan/Dianping crawling or data storage.
- XHS layer disabled by default; enabling it presumes you hold the required cookies —
  keep frequency low and respect their ToS.
- No real collected data ships in this repo (only a 2 KB sample in `data/seeds.json`).

## Known limitations

- Price/facility fields come from public POIs/web pages; treat the shortlist as a first pass and confirm by phone before the trip.
- Planner is rule-based (no LLM): stable but conservative.
- Platform automation validated on Windows + Edge.

## License

MIT — see [LICENSE](LICENSE).
