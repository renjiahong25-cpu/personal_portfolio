# BUGS 记录

> 每次排错：追加一条；修复后写「解决方案」和「验证」。Agent 遇到同类问题先查本表。

## 格式

```markdown
### BUG-XXXX-标题
- 日期：
- 阶段：（对应 PLAN.md step）
- 现象：
- 原因：
- 解决方案：
- 验证：
- 状态：open / fixed
```

---

### BUG-0001-miniconda 损坏导致 python 无法启动
- 日期：2026-09-24
- 阶段：S1_ENV
- 现象：某系统 Python 报 `ModuleNotFoundError: No module named 'encodings'`，`Could not find platform independent libraries`
- 原因：该解释器的 `Lib` 下几乎只有 `site-packages`，标准库缺失（安装不完整/被裁剪）
- 解决方案：为项目创建专用 venv/conda 环境（见 ENV.md），不要直接依赖系统 Python
- 验证：`python -c "import sys; print(sys.version)"` 正常输出 3.12.x
- 状态：fixed

### BUG-0002-主会话直接跑长命令被中断
- 日期：2026-09-24
- 阶段：S1_ENV
- 现象：主会话 `python -m venv` + `pip install -r` 组合命令执行中被用户 abort；.venv 仅含 pip，依赖未装全
- 原因：违反 AGENTS.md 进程闸门——>15s 且有中间输出的安装任务应在 subagent 分批执行
- 解决方案：conda create + pip install 全部交给 `process-monitor` subagent；主会话只写文件/读状态
- 验证：subagent 返回 deps ok 后在 PLAN.md 勾选 S1
- 状态：fixed

### BUG-0006-会议间数/容量仍需 Cookie 或人工
- 日期：2026-09-24
- 阶段：P0/P4
- 现象：aggregate 能标「有会议厅」(code 6)，但无 meet_room_count / 容纳人数；meeting.ctrip.com 不可达
- 原因：会议室明细在会场频道或需登录的房型/会场 XHR（getHotelRoomListInland 等），匿名聚合接口只给设施旗标
- 解决方案：(1) 有会议厅无容量 → `needs_manual` + 占位会议室；(2) HTML 正则尽量抽容量；(3) 用户可先登录提供 Cookie 提升成功率；(4) 参考 greenzorro/hotel-comparer、OpenCLI hotel、ctrip-hotel-skill 浏览器态
- 验证：清泉湾等有宣传文案的种子经 suggest+aggregate 应有 hall=True；容量从 notes/人工补
- 状态：partial（Cookie 可选增强，不阻塞主流程）

### BUG-0005-携程详情页 URL 404
- 日期：2026-09-24
- 阶段：P0
- 现象：`https://hotels.ctrip.com/hotels/detail/347308.html` 返回 404，无 NEXT_DATA/IBU_HOTEL
- 原因：旧式 `hotels/detail/{id}.html` 不可用；有效格式为 `hotels/{id}.html` 或 `hotels/detail/?hotelid={id}` 或移动端 `m.ctrip.com/html5/hotel/hoteldetail/{id}.html`
- 解决方案：DETAIL_URL 改为 `hotels/{id}.html`，失败时回退 query 与 m 端
- 验证：513040 三种格式均 200（len 611k / 141k）
- 状态：fixed

### BUG-0007-详情页无 __NEXT_DATA__，改走 SOA getHotelDetailAggregate
- 日期：2026-09-24
- 阶段：P0
- 现象：新 NFES 架构详情 HTML 无 `__NEXT_DATA__`/`IBU_HOTEL`；设施区有「会议厅」但无间数/容量；`ctgetHotelDetailSEO` 校验 `head.locale/timezone` 失败
- 原因：携程详情改为 FaaS SSR + 前端 XHR；聚合数据在 `soa2/33278/json/getHotelDetailAggregate`
- 解决方案：主路径 SOA POST body `{"hotelId":id,"head":{"platform":"PC","group":"ctrip","cver":"1.0.0"}}`（无需 Cookie）；解析 `hotelFacilityBelt.facilityList`（code 6=会议厅）+ `detailDescPopTags[roomNum]` 客房数 + `hotelPositionInfo.lng/lat` + HTML 正则补 capacities；`gaHotelSearchEngine` 做名称→hotel_id
- 验证：513040 aggregate 200 Ack=Success，facility 含 code 6 会议厅，roomNum=174；suggest 翠岛/熹欢/清泉湾/熹乐谷 均返回 Hotel id；`scripts/test_ctrip_seeds.py` 批量复测
- 状态：fixed

### BUG-0008-PowerShell 内联 python -c 的 f-string 引号被吞
- 日期：2026-09-24
- 阶段：P0
- 现象：主会话用 `python -c "print(f\"...\")"` 测试种子时 PowerShell 报 `id=` CommandNotFoundException
- 原因：嵌套引号/`$` 在 PowerShell 双引号中被二次解析
- 解决方案：长测试脚本落盘为 `scripts/test_ctrip_seeds.py` 再执行，避免内联引号
- 验证：`python scripts/test_ctrip_seeds.py` 无引号报错
- 状态：fixed

### BUG-0009-suggest 名称搜索跨城错配（雅轩→忻州）
- 日期：2026-09-24
- 阶段：P0
- 现象：`广州乡约雅轩温泉民宿` suggest 命中 `忻州沐雅轩温泉民宿`（lat≈38.49），hall=False
- 原因：gaHotelSearchEngine 按关键词打分，不强制城市；重名「雅轩」全国多条
- 解决方案：`suggest_hotels(..., city_hint=city)`；排名优先 name+city；明确错城（忻州/山西）降权；无本地命中则标 `NO_LOCAL_MATCH` 不 enrich
- 验证：`scripts/test_ctrip_seeds.py` 重跑雅轩不再用忻州 id；其余 8/10 种子 hall=True+room_count
- 状态：fixed

### BUG-0010-Edge Cookie 为 v20 ABE，磁盘解密失败；改用户手贴
- 日期：2026-09-24
- 阶段：P6 配置
- 现象：`extract_edge_ctrip_cookie.py` 读到 ctrip 行但全是 `v20`，DPAPI `CryptUnprotectData` 报「数据无效」；Edge 运行时 Cookie 文件还被独占锁（Error 32），复制/esentutl/robocopy /B 均失败
- 原因：新版 Edge/Chrome 应用绑定加密（App-Bound Encryption），密钥不在普通 DPAPI `encrypted_key` 里
- 解决方案：(1) 用户手动粘贴携程/小红书 Cookie 到 `settings.yaml`；(2) 备用 CDP 脚本 `extract_edge_cdp_cookie.py`（Edge/Chrome 带 `--remote-debugging-port` 后用浏览器自身解码）
- 验证：settings.yaml 已写入 cticket/login_uid 等；11:04 全量管线用 cookie 正常跑通
- 状态：fixed（workaround：用户手贴）

### BUG-0011-会议室硬过滤苛刻致全灭；口径放宽「有会议厅即放行+needs_manual」
- 日期：2026-09-24
- 阶段：P6
- 现象：11:00 全量跑：183 候选 passed=0。breakdown 大量 `meeting:0.0`——所有会的场只有 1 个容量未知的会议厅旗标，或容量 800/600/100 远超 10–15 档
- 原因：`meeting_ok` 要求「≥2 间、每间 10–15 人」，匿名 SOA/高德数据无每间容量；该档位现实中也很少有酒店满足
- 解决方案（用户拍板口径）：有会议厅/会议室的场即算合规，容量未知或超档标 `needs_manual` 人工复核；scorer `_meeting_score` 给 40/50 部分分、unknown→true pass
- 验证：11:04 重跑 passed=27（≥10 ✓），stderr 0 字节
- 状态：fixed（口径由用户确认）

### BUG-0004-query.py f-string 语法错误
- 日期：2026-09-24
- 阶段：P5b
- 现象：`query.py` 中 print 的 f-string 后半段掉出引号，导致 SyntaxError
- 原因：多行 f-string 拼接时漏写第二个 `f"..."`
- 解决方案：改为两段完整 f-string 相邻拼接
- 验证：`python -m py_compile scripts/query.py` 通过
- 状态：fixed

### BUG-0003-（模板）
- 日期：
- 阶段：
- 现象：
- 原因：
- 解决方案：
- 验证：
- 状态：

### BUG-0012-cookie 获取方式升级：手动粘贴 → 前端一键登录自动抓取
- 日期：2026-09-26
- 阶段：P6 配置
- 现象：cookie 需手动复制粘贴进 settings.yaml；磁盘解密路径（BUG-0010）ABE 加密不可用，CDP 脚本需手动带调试参数启动浏览器，门槛高
- 原因：无浏览器会话内抓取通道；携程/小红书 cookie 只在浏览器运行时可用
- 解决方案：新增 `src/cookie_login.py`：Playwright `launch_persistent_context(data/browser-profile/{site})` 打开专用有头窗口（headless=False），登录后每秒轮询特征 cookie（ctrip=cticket/login_uid，xhs=web_session），命中即抓全量域名 cookie 拼 header 写入 config/settings.yaml（`write_cookie` 泛化支持任意 section，写前自动 .bak）；持久 profile 保登录态，下次失效再点通常免登录直接抓到。webapp 暴露 `POST /api/creds/login/{site}`（daemon 线程+状态机）与 `GET /api/creds/login/status`（前端轮询）；前端横幅升级为「一键获取/刷新 Cookie」按钮+进度显示；`creds.probe()` 做 cookie 有效性轻探针（missing/no_marker/valid/unreachable）
- 验证：`/api/creds/status?probe=1`、`/api/creds/login/notasite` 404、状态机 idle；`write_cookie` 临时文件测试（xiaohongshu 段替换成功、ctrip 段保留、.bak 生成）；服务重启后接口 200 零异常。真实登录扫码动作需用户本人在弹出的浏览器窗口完成
- 状态：fixed

### BUG-0012b-登录窗口误开 headless；携程 h5 登录页 404 失效
- 日期：2026-09-26
- 阶段：P6 配置
- 现象：启动自动登录拉起的 chromium 是 `chrome-headless-shell`（无可见窗口，用户无法扫码）；携程 `passport.ctrip.com/h5/login/userlogin` 打开即 404
- 原因：`cookie_login.login()` 默认分支 `headless=...not headless_windows()`（该函数恒返回 False，取反恒 True→headless）；h5 登录地址已下线
- 解决方案：默认 `headless=False`（仅显式 `headful=False` 才隐身）；登录页改为 `https://hotels.ctrip.com/`（首页恒 200，用户点登录后在窗口内完成即可）
- 验证：服务重启后 `login/status` ctrip=waiting_login，Chrome 进程 CommandLine 无 headless、MainWindowTitle「登录页面 - Google Chrome for Testing」；`Invoke-WebRequest hotels.ctrip.com`=200。随后用户在弹窗扫码，ctrip 登录成功 `signed_in_window cookies=39` 写入 settings.yaml（L21 含 cticket+login_uid）、xhs 成功 11 cookies（L24）
- 状态：fixed

### BUG-0013-plan 输出口径：总车程/返程折线/退化段/时间重叠
- 日期：2026-09-26
- 阶段：P6 规划
- 现象：端到端规划（api/plan，2 天 / 三英温泉）发现 4 个非致命问题：
  1. `summary.total_drive_min=196`（8 leg 求和）与 day slots `drive_min` 合计 180 不一致（差 16）
  2. 返程 access leg（map_legs[7]）`via` 为空，去程 via 1186 点——地图上返程画不出路径
  3. `map_legs[4]` 出现「白水寨→白水寨」零长度退化段（via 2 点、drive_min=1），Day2 slot 表却无对应项
  4. Day1 `overnight` slot 缺 `end`，且与 dinner start 同为 18:00（时间重叠）
- 原因：待排查（planner 对 legs/slots 口径、AMap 返程折线解析、剪裁/去重逻辑不统一）
- 解决方案：未修（不影响规划可用性）；列入观察，后续修 planner 时统一 leg→slot 对应与折线标准
- 验证：泄漏 demo 已记录（21.5s 求解、cached 一致性 OK）
- 状态：open

### BUG-0014-登录闸门（两段式）确认后无下一步；采集无界面提示
- 日期：2026-09-26
- 阶段：P6 配置 / 采集
- 现象：
  1. 用户登录携程/小红书后系统不自动继续（卡在 waiting_login，需点「确认」才收割——用户不点或没界面就无下一步）
  2. 启动时后端无条件弹登录窗口，用户已登录却无前端界面引导，感觉「没有界面出现」
- 原因：
  1. `open_login_window` 原实现只阻塞等待前端「确认」事件，不主动轮询窗口内登录态 → 登录了也不收割
  2. Playwright sync 对象线程亲和：confirm 线程跨线程碰 open 线程的 context 报 `cannot switch to a different thread`，收割失败 phase=error
  3. `_boot` 启动无条件 `_run_creds_open`，未先检查 cookie 有效性 → 已登录也弹窗打扰
- 解决方案：
  1. `open_login_window` 改为打开窗口后每 2s 轮询 `ctx.cookies()` 检测特征 cookie marker，命中即自动收割写入 settings.yaml（前端「确认」按钮仅作兜底触发）
  2. 收割逻辑整体放进 open 线程（线程亲和安全）；`confirm_and_save` 仅置 event 并等 result
  3. `_boot` 启动先 `probe` 每个 site：cookie 有效 → 跳过弹窗记录「已登录」；无效才开登录窗口
- 验证：
  - 端到端 API：cookie 有效时 `/api/creds/status` probe=valid、`/api/collect` 闸门 missing=[] 直接放行
  - 临时清空 xhs cookie → `/api/creds/status` probe=missing、`/api/collect` 返回 **428 + need_login**（前端收到即弹登录界面）
  - `/api/creds/open/xiaohongshu` → 200 → 状态机 poll：opening → **saved**（自动检测 profile 已有登录态，2 秒收割 14 cookie 写入 settings.yaml，无需点按钮）
  - 用户确认采用策略：**cookie 有效则跳过登录直接下一步；cookie 失效/缺失才弹登录界面**
  - 配置经备份恢复（ctrip cookie=1722、xhs=477 无误）
- 状态：fixed
