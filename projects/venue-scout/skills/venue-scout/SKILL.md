---
name: venue-scout
description: 团建/拓展/年会场地采集 + 小红书攻略级行程规划助手。当用户想找团建、拓展、年会、培训、公司活动的场地或目的地推荐，或要"小红书级别的游玩攻略/行程规划"时使用（如"给 50 人找个两天一夜的团建场地"、"广佛周边有哪些能开会的度假酒店"）。触发词：团建、拓展、年会、场地、场地推荐、场地筛选、场地采集、公司活动场地、场地调研、攻略规划、行程攻略。
---

# venue-scout：场地采集 + 攻略级规划 skill

把用户一句话需求 → 采集候选场地（高德/携程/小红书）→ 浏览器 Top10 复核 → **统一方案**（路线自动规划 + opencode 基于小红书研究的攻略细节层：玩多久/打卡点/避坑/餐食/预算）→ 采用 → 交付文档的完整流水线。

## 环境事实

- 项目根：`<REPO_ROOT>`（路径可能含空格，PowerShell 一律加引号）
- Python：`<PY>`（下称 `PY`）
- 服务：uvicorn `src.webapp:app`，端口 **8000**；前端 `http://127.0.0.1:8000`
- 访问密钥：`data/.web_token`（所有 API 需 `?key=`；`dialog_agent.py` 自动读取）
- 日志：`$env:TEMP\opencode\venue_uvi8.out.log` / `.err.log`
- 所有 bash 命令前先 `$env:PYTHONIOENCODING='utf-8'`
- 浏览器只用 **Playwright + Edge/Chrome**（禁用 tabbit）

## 硬规则：有界契约（零例外）

1. 每个 subagent 任务 = **明确完成条件 + 硬时间上限 + 必然返回**。禁止开放式持续监控。
2. 主会话白名单短命令：health ping、`Get-Content -Tail` 单日志、`netstat`/`Get-Process`、`Stop-Process`、<10s 一次性脚本。命中以下任一条必须派 subagent：
   - 启动/重启长驻进程（uvicorn 等）
   - 运行 >15s 的脚本（采集监控、watch 事件、浏览器 TEST_READY 探测）
   - 含轮询/等待逻辑的命令
3. 时间上限：ensure_service ≤100s；登录监控 15min；采集监控 25min；浏览器 TEST_READY 60s；文档渲染 ≤3min。
4. subagent 结束必须返回结构化结论（状态行、输出原文、报错原文）；失败只允许重试 1 次，仍失败则带日志原文上报主会话。
5. **事件跟进是服务端的确定性行为**（venues_confirmed→自动 AI 线；plan_adopted→自动生成交付文档），**禁止**派长时间 watch 事件 subagent（那是死循环+无兜底）。agent 侧兜底：每次接用户消息先跑 `dialog_agent.py events`（秒级，读未处理事件+推进游标），发现生成失败/漏处理立即补救。
6. **浏览器窗口守卫**：任何需要浏览器的动作前（或用户说窗口没了）先跑 `ensure_browser.py`（派 subagent，单条有界命令）；窗口在但用户看不到时用 `--force` 弹新窗口。
7. **前端防回归**：任何改 `web/index.html` 的回合结束前，必须跑 `& PY scripts\ui_smoke.py`（Playwright 无头冒烟：填方向→勾选→确认→断言弹窗打开+无 pageerror，拦截 POST 防误触发）。必须看到 `SMOKE_PASS`；`SMOKE_FAIL` 先修复再继续。
8. **XHS 爬取有界**：`xhs_research.py` 内部超时 1800s；派 subagent 监控上限 35min。首次运行弹浏览器二维码需**提示用户扫码**（登录态缓存到 `vendor/MediaCrawler/xhs_user_data_dir`，之后免扫码）。爬取超时但数据已部分落盘时，加 `--no-crawl` 复用当天数据，**不要盲目重爬**。

## 流程步骤

### Step 0a 服务就绪（≤100s 有界）

派**一个** subagent 跑单条命令：

```powershell
$env:PYTHONIOENCODING='utf-8'; & '<PY>' 'scripts\ensure_service.py' --timeout 90
```

脚本幂等：健康→`ALREADY_RUNNING` 秒回；否则杀旧进程→分离启动（日志落 Temp\opencode）→有界轮询→`READY`/`FAILED`（带 ERRLOG 尾部）。
**代码改过 webapp.py 后必须先杀旧服务再跑此命令**（健康 ≠ 新代码）：

```powershell
$pids = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique; if ($pids) { $pids | ForEach-Object { Stop-Process -Id $_ -Force } }
```

### Step 0b 账号登录门禁（先于一切需求确认）

```powershell
& '<PY>' scripts\dialog_agent.py login-status
```

每个 site 的 `phase` 必须是 `saved`（或 cookie 已配置）。若有失效站点：

1. 提示用户："需要登录 **携程/小红书**，马上会弹出浏览器登录窗口，请完成登录。"
2. `& PY scripts\dialog_agent.py login --site ctrip`（或 `xiaohongshu`）——服务端弹真实浏览器窗口并自动等待收割。
3. 派 subagent **有界监控**（15min 上限）：每 20s 跑一次 `login-status --site <site>` 直到 `phase=saved`（或 `error`），把状态原文带回。
4. `phase=error` 时向用户呈现 message 原文，请其手动完成后再查一次。

### Step 1 需求对话（opencode 对话框内完成，两分支）

**开场必问**（用 question 工具）：

> "这次场地筛选，你有大致规划吗？（比如'五山出发、100 人、两天一夜、要开会、预算舒适'这类；没有也可以，我带你一步步确认）"

- **分支 A（用户给了大致规划）**：解析 → **回显理解**（"我的理解：出发地 X，车程 ≤Y，N 人，D 天，会议 M，娱乐 E，候选 T 家——对吗？"）→ 对未覆盖字段**逐字段追问**，每问 2-3 个推荐值 + 自定义。
- **分支 B（没有规划）**：按 9 问顺序引导（每问 2-3 推荐 + 自定义，一次一问）：
  1. 想去哪？（从化 / 清远 / 增城番禺 / 不限）
  2. 出发地？（五山 / 珠江新城 / 其他）
  3. 车程上限？（按方向推荐 60 / 90 / 120 分钟）
  4. 人数？（50 / 100 / 150+）
  5. 会议需求？（无 / 半天 / 全天 → 全天再追问会议室数量与容量）
  6. 想玩什么？（漂流 / 卡丁车 / 游船 / 温泉 / 其他）
  7. 几天？（单日往返 / 两天一晚）
  8. 预算档？（经济 / 舒适 / 轻奢）
  9. 候选数？（10 / 15 / 20+）

**字段去向**：出发地/车程/人数(住宿容量)/会议室/娱乐类/候选数 → 采集 `Query`；天数/人数/会议/娱乐偏好/预算 → 规划 answers。

**对话结束后必须落盘 AI 线参数**（前端确认后服务端自动用它启动规划）：

1. 把 answers 写入临时文件（UTF-8 JSON）
2. `& PY scripts\dialog_agent.py save-answers --file <临时文件>` → 写入 `data/output/session_plan.json`
3. **方向已具体到城市时**（如"贺州"），往 `session_plan.json` 写入 `"review_focus": "<城市>"` → 前端"方向筛选"输入框**自动填入该值**（服务端 `GET /api/review-focus` 驱动，用户可手动改，回车/失焦即重筛）。对话已确认的条件一律预填（用户明确要求）。

### Step 2 触发采集

向用户确认条件后（提示词模板："按〔条件摘要〕开始采集，预计 5-15 分钟，好吗？"），执行：

```powershell
& '<PY>' scripts\dialog_agent.py collect --origin-name <名> --max-drive <分> --lodging <人> --meeting-rooms <数> --room-cap-min <n> --room-cap-max <n> --ent <类> --target <家> [--hub <目标城市> ...]
```

**远方向必须带 `--hub`**：发现阶段是从出发地以 25km 为壳逐轮扩圈（expand_rounds 轮），够不到几百公里外的目标城市（如五山→贺州）。用户方向远离出发地时，传目标城市名（自动地理编码，可多个：`--hub 贺州市 --hub 黄姚古镇`），锚点 25km 内住宿 POI 全部进入候选池（来源标记 `amap_hub`）。
返回 `ok:true` 即启动；`428 need_login` → 回 Step 0b；`409` → 已有任务，直接进 Step 3。

### Step 3 采集监控（subagent，25min 上限）

派 subagent 跑：`& PY scripts\dialog_agent.py wait --timeout 1500`，期间每 60s 用 `status --tail 10` 带回进度；`done` 后读 `status` 的 `last_run` 指向的 `data/output/venues_pipeline_*.json`，汇报：total / passed / 来源构成 / Top3 名称与分数。**主会话把结果摘要呈现给用户。**

### Step 4 自动打开前端（浏览器有头）

派 subagent 跑单条有界命令（幂等，最坏 ~80s 返回）：
`& PY scripts\ensure_browser.py`（加 `--force` 可强制弹新窗口）
脚本自动：窗口活着→AppActivate 还原置顶（`BROWSER_ALIVE RESTORED=n`）；窗口死掉且流程未结束→清残留自动重开（`BROWSER_REOPENED`）；流程已结束→只清残留（`BROWSER_NOT_NEEDED`）。
**用户反馈「窗口不见了」或任何需要浏览器之前，都先跑一次**（窗口最小化/切到虚拟桌面也靠它还原置顶）。
提示用户："浏览器已打开复核页。Top10 已按对话确认的方向（『方向筛选』输入框已自动填入，可改）筛选。核对后勾选 ≤5 家，选规划娱乐（chip 或输入自定义，如『温泉』），点『确认并自动规划』。"

**前端现状（2026-09 精简版）**：页面 = 下载交付物栏（**8 按钮**：行程方案 PDF/DOCX/MD + 推荐清单 Top10 PDF/全量 PDF/DOCX/MD/JSON。行程方案按钮按磁盘 `方案_*_ai_.*` 存在即点亮（`plan_done` 以磁盘兜底，服务重启不丢）；清单类需 plan_done+文件存在。全部走 **fetch-blob 下载**（服务端真实文件名，404/空文件弹明确原文，不出现"打开 JSON 页"））+ Top10 复核卡片 + 方案弹窗（内联攻略层，**✕ 按钮 sticky 吸顶**，滚多长都能关）。筛选面板/重新采集表单/大列表已移除。`/download/plan_{pdf,docx,md}` 服务最新 `方案_*_ai_.*`。guard（open_front_ui.py）在 TEST_READY 前会断言 `RCITY=非空`，空则自动 reload 一次仍空则 exit 3。

### Step 5a 规划泛化事实（planner v3）

- 活动候选 = **周边实际 POI 数据驱动**：`poi_kind()` 九类（温泉/漂流/水上乐园/游船/卡丁车/乐园/景区/休闲；汽车销售/餐饮/加油等剔除）；标签=「动词·真实POI名」（如"温泉·贺州园博园温泉山庄"），不再固定"漂流/卡丁车"。
- 用户娱乐词优先：已知类（rafting/karting/boating，boating 严格=游船类）+ 自定义词（如"温泉"按名称/类目模糊匹配）→ 按距离排前；其余按奖励+距离补。**缺失警告用用户原话**（"周边未找到「游船」对应项目"）。
- 返程时限警告区分"默认 15:30"vs"你设定的"。

### Step 5 统一方案 = 路线 + 攻略细节层（路线自动，细节 opencode 做）

用户点前端「确认并自动规划」后：

**① 路线（服务端确定性，自动）**：`venues_confirmed` 事件 → 服务端自动跑规划（OR-Tools 路线 + `session_plan.json` 参数 + 事件娱乐词模糊匹配周边 POI），参数缺失回退默认（日志有标注）。前端弹窗实时显示。

**② 攻略细节层（opencode 必须做，用户要求"小红书级别"）**：用户确认后，agent 立刻：

1. 派 subagent 跑（监控上限 35min；首次运行提示用户扫码）：
   ```powershell
   & '<PY>' scripts\xhs_research.py --venue-id <venue_id> --notes 15
   ```
   脚本按场地生成关键词（场地名/城市+区县+团建/两天一夜/娱乐偏好词/周边 POI top2 攻略）→ 跑 MediaCrawler（`vendor/MediaCrawler`，独立 venv）→ 解析笔记+评论 → **相关性打标**（venue 本场地 / nearby 附近地点 / type 同类玩法 / city 同城 / other）→ 落盘 `data/output/xhs/{venue_id}.json`。
   超时但已有数据 → `--no-crawl` 复用当天落盘数据。
2. **opencode 窗口内分析**（这是"AI 规划"的核心，不靠外部 LLM API）：读 `data/output/xhs/{venue_id}.json` 里 venue/nearby/type 高相关笔记的 desc+评论 → 结合该场地路线方案 → 重组写 `data/output/plan_detail_{venue_id 冒号换下划线}.json`，结构（**activities 按 POI 名 keyed，不按时间对齐**——路线重跑/改标签/变时间都不怕）：
   - `activities{ "<POI名>": {how_to 怎么做, tips[], photo_spots[], img 小红书首图URL, source[{title,url}]} }`（POI 名=路线 slot 标签「·」后的真实名字）
   - `overview`（2-3 句攻略总述）
   - `highlights`（亮点+时段）
   - `food`（本地餐食，来自攻略）
   - `pitfalls`（避坑，注明来源）
   - `budget_estimate`（人均区间+基准+分项）
   - `alternative_nearby`（**附近地点攻略标注适配**：是相邻场地/玩法的攻略时说明距离与何时换，不照搬）
   - `references`（参考笔记清单，带 url）
3. **服务端自动合并**：`/api/plan/batch?kind=ai` 的 item 带 `detail`（非 slot 段）+ 每个活动 slot 带 `slot.detail`（webapp `_match_activity_detail` 按 POI 名精确→双向子串匹配 activities）。前端时间轴卡片内联渲染（💡怎么做/tips/📸打卡点/📕来源笔记链接，外跳真实笔记）；交付文档（render_plan_doc）按天表格后内联同层。**注意**：小红书 CDN 图片有防盗链（403），前端不内联 `<img>`，只放笔记链接。

agent 侧**不派任何事件监控 subagent**。提示用户："路线生成中；攻略细节层正在基于小红书研究合并…（首次爬取会弹扫码窗口）。"

手动兜底（仅当服务自动触发失败时，查 `status --tail` 日志确认）：
`& PY scripts\dialog_agent.py plan-batch --kind ai --venues <id1,id2,...> --answers-file <answers.json 文件>`

### Step 6 对比 + 采用 + 自动交付（确定性，无 watcher）

用户在前端弹窗里对比（场地 tab × 统一方案：路线 + 📕攻略细节层），点「✅ 采用此方案」→ 服务端**自动生成交付文档**（`data/output/方案_{场地}_{kind}_{时间戳}.md/.docx/.pdf`，含攻略细节层，约 1-2 分钟）→ 前端按钮变为「📄 已生成：… [打开]」（生成失败显示错误原文，可让 opencode 重跑）。

agent 在用户回到窗口说话时：
1. 先跑 `& PY scripts\dialog_agent.py events`（补读未处理事件 + 推进游标，秒级）
2. `& PY scripts\dialog_agent.py delivery --kind <kind> --venue <id>` 查采用方案文档状态
3. 汇报文件路径；失败则重跑：POST `/api/delivery/run {kind, venue}`（带 `?key=`）

**后续改进循环**：用户可提调整（改日期/换娱乐/加环节/改人数）→ 更新 answers 文件 → `plan-batch --kind ai` 重跑（或单场地）→ 新方案在弹窗刷新 → 用户再次采用 → 生成新版本文档（新时间戳，旧版保留）。

### Step 7 全清单交付（采集完成后尽早跑，不必等采用）

**为什么提前**：前端下载栏的"推荐清单"按钮按文件存在点亮（`/api/deliverables`），旧数据会误导用户。采集 `done` 后立刻重建（派 subagent，≤8min 上限）：

```powershell
& '<PY>' scripts\build_summary_md.py --top 10
& '<PY>' scripts\build_summary_docx.py
& '<PY>' scripts\docx_to_pdf.py 'data\output\团建场地推荐清单.docx' 'data\output\团建场地推荐清单.pdf'
& '<PY>' scripts\docx_to_pdf.py 'data\output\团建场地推荐清单_全部27家.docx' 'data\output\团建场地推荐清单_全部27家.pdf'
```

产物：`data\output\venues_summary.md` + `团建场地推荐清单.docx/.pdf`（Top10）+ `团建场地推荐清单_全部27家.pdf`（全量）。**build_summary_docx 写的是 `团建场地推荐清单.docx`（不是 venues_summary.docx）**，docx_to_pdf 源文件名别搞错。

## 关键文件

| 路径 | 作用 |
|---|---|
| `data/.web_token` | API 访问密钥 |
| `data/output/venues_pipeline_*.json` | 采集产物（latest 即最新） |
| `data/output/ui_events.jsonl` | 前端事件流（venues_confirmed / plan_adopted）；`.ui_events_cursor` 为 agent 已读游标 |
| `data/output/session_plan.json` | 对话提炼的规划参数 + `review_focus`（save-answers 写入；review_focus 供浏览器自动预填方向） |
| `data/output/plan_batch_ai.json` | 统一规划结果（路线+会话参数） |
| `data/output/xhs/{venue_id}.json` | 小红书攻略研究（xhs_research.py 输出，含相关性打标+评论） |
| `data/output/plan_detail_{venue_id}.json` | 攻略细节层（opencode 分析研究结果后写入；文件名冒号→下划线） |
| `data/output/方案_{场地}_{kind}_{时间戳}.{md,docx,pdf}` | 采用方案交付文档（plan_adopted 自动生成，含攻略细节层） |
| `data/output/venues_summary.md` + `团建场地推荐清单.{docx,pdf}` + `_全部27家.pdf` | 全清单交付文档（Step 7，采集完成后重建） |
| `web/index.html` | 前端（下载栏+Top10 复核+方案弹窗内联攻略层），改它**无需**重启服务（服务端每请求重读磁盘），但**必须**跑 `ui_smoke.py` |
| `scripts/ui_smoke.py` | 前端冒烟回归（SMOKE_PASS/FAIL；断言下载栏 8 按钮、rCity 自动填入非空、勾选→弹窗、真下载验证（fetch-blob 落盘 >100KB+文件名正确）；拦截 POST 防误触发） |
| `scripts/open_front_ui.py` | 有头窗口 guard（过门禁、REVIEW_CARDS、**RCITY 非空断言**、TEST_READY 后常驻） |
| `scripts/xhs_research.py` | 小红书攻略研究封装（MediaCrawler：关键词生成/爬取/打标/落盘；`--no-crawl` 复用） |
| `vendor/MediaCrawler/` | NanmiCoder/MediaCrawler（独立 `.venv`；config 已改 `ENABLE_CDP_MODE=False`；登录态在 `xhs_user_data_dir/`） |
| `src/webapp.py` | 后端（改它**必须**走 Step 0a 重启） |

## 已知问题

- **旧 xhs_client 提取弱**（SSR 无正文）：发现阶段 xhs 源候选很少（正常，不影响 amap/ctrip 主链路）；**攻略层已改用 MediaCrawler**（真实搜索+正文+评论）。
- **前端 JS 静默失败**：异常/空值不到服务端，现象是"页面没反应/没自动填"。排查：`ui_smoke.py` + Playwright 无头复现（种 vs_key cookie、抓 response 原文、evaluate 手动跑关键步骤）。历史根因：`const` 二次赋值、`querySelector` 拿错框、**`(await httpJ(...)).json()).x` 漏 await（.json() 是 Promise，直接 .x 得 undefined）**。
- **Edge 用户 profile 会缓存旧版 index.html**：前端改完用户窗口可能仍是旧页面。服务端首页响应已带 `Cache-Control: no-store`；若用户报"没变"，先确认服务已重启+新代码，再 `ensure_browser.py --force` 弹新窗口，并核对 guard 日志断言行（如 RCITY）。
- **PowerShell 5.1 坑**：`$pid` 是只读保留变量（杀服务进程用 `$spid` 等别名）；原生进程参数里的 `"` 会被剥掉 → 所有 JSON 参数走 `--answers-file`。
- 改 `webapp.py` 后健康检查仍通过（旧进程活着）→ 必须先杀端口再 ensure_service。
- **MediaCrawler 同账号多网页端登录互踢**：其登录态（xhs_user_data_dir）与本项目 settings.yaml 里的 xiaohongshu cookie 可能互踢；若 xhs_client 侧 cookie 失效，重跑 Step 0b 登录即可。
- **batch 状态在内存**：服务重启后 `/api/plan/batch` 返回空（弹窗显示"未启动"），但 `plan_batch_*.json` 文件与 delivery 仍可用；用户重新勾选确认即会重新生成。
