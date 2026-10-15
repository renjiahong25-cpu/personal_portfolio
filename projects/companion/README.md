# 星澜 · Game Companion Desktop v0.3

这是办公与游戏的 Windows 原生旁观桌宠第一版。默认跟随前台应用，无需每次选择窗口；必须明确点击“开始观察”才采集。默认 **本地测试 provider** 可以快速验证聊天、记忆、字幕、打断与窗口跟随，它不是 AI，也不理解画面。它不会把真实截图发送到远程。当前对话中的助手不会在后台常驻看图。

## 启动

使用已安装 PySide6/httpx 的 Python 3.11+ 环境。本机配置为 `python`。在项目目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\启动桌宠.ps1 -Port 8793
```

脚本使用 UTF-8 BOM，兼容 Windows PowerShell 5.1；默认8793，与原版服务隔离。可用 `-PythonPath "C:\path\python.exe"` 更换环境。端口占用时拒绝启动，不结束已有服务。后台进程隐藏，桌宠是可见交互界面；退出桌宠只结束本次脚本创建的后台进程。

也可分开运行（两个终端，同一项目目录）：

```powershell
python -m app --port 8793
python -m app.desktop --port 8793
```

双击桌宠打开设置与聊天，右键暂停、打断、静音或退出。关闭设置只收起设置，桌宠继续运行。设置页可滚动，底部有输入框、发送与新会话按钮。中文本地语音不可用时显示字幕；正常 Windows 账号可重新检测 SAPI/WinRT 中文声音。

## 使用与边界

默认“自动跟随前台窗口”；可切换“锁定游戏窗口”。只截当前前台目标窗口的矩形，每5秒检查一次。桌面、任务栏、无标题窗口、自有桌宠/设置窗口、最小化、失效、锁屏、安全桌面、排除标题/应用都会暂停。锁定模式不会观察其他前台应用。跨屏或超出单屏的窗口拒绝采集，没有全桌面截图回退。截图前后重新确认窗口状态；每300ms检查前台身份，用 generation 丢弃旧回复、清理最新帧和语音。

默认排除常见密码、登录、支付、银行、凭据及密码管理器关键词。设置页可逐行编辑并保存到 `runtime/exclusions.json`。规则依据窗口标题/进程名，**不能识别全部隐私**；普通标题下的敏感页面也可能被截到。可随时暂停观察。系统支持时请求将桌宠和设置排除出截图；不支持时界面会提示把桌宠移出目标区域。

只提供解释、流程提醒、聊天与旁观建议，应用没有键鼠执行接口。安静/平衡/活跃自动评论冷却为45/20/8秒；忙碌只保留最新1帧。失败最多5/15/30秒退避重试，切窗、停观察、换上下文、换provider会取消旧帧与回复。自动评论要求有画面证据；本地测试自动观察安静返回 `[[SILENT]]`，手工回放只返回预写文字，不识别真实画面。

## Provider 与真实模型

抽象接口位于 `app/providers/__init__.py`：`ProviderRequest` 与 `Provider.stream(request, cancel)`。HTTP/SSE服务、记忆和桌宠不依赖具体模型。替换本地模型可以使用 OpenAI兼容服务，或实现此协议并注册新provider。

项目 `provider.json` 默认禁用真实provider。它不读取原D盘项目配置、不读取或复制 `auth.json`，不暴露配置内容到 `/api/state`；状态接口只给provider名称与配置是否存在。默认回放 `replay-responses.json` 仅用于测试，可以按完整问题字符串映射到手工答案。

**OpenAI兼容 / Qwen：** 在 `provider.json` 的 `openai` 中填写 `base_url`（含 `/v1`）、`model`，然后显式设置 `enabled: true`。本地Qwen无需API key时可以为空；需要时从所配置的环境变量读取，例如 `GAME_COMPANION_API_KEY`，不要将密钥写进README或界面。网络请求使用 `trust_env=False`。`max_tokens` 可配置为128–1024，默认768；长度截断仍会明确报错，不保存为完整回答。Qwen3.8仍只是可配置示例，不假定本机存在这个模型。

**Codex CLI（需正常账号登录）：** 在 `codex` 配置真实可执行文件路径并设 `enabled: true`。本机已发现示例路径写入配置；正常子进程中的 CLI login status 可确认登录，但合成图片探针在本地 in-process app-server 初始化时被拒绝访问，未进入模型请求，不能宣称真实模型识图成功或接入正在进行的助手对话。请在正常 Windows 账号中安装、登录CLI，随后用该账号启动桌宠。CLI使用其自身正常登录，不复制凭据，也不改变HOME/CODEX_HOME。CLI的云端可用模型和延迟依赖账号、网络与服务。

CLI调用使用参数数组、`shell=False`，不运行拼接shell命令。固定 `exec --ignore-user-config --ephemeral --sandbox read-only --skip-git-repo-check`，禁用shell工具、shell snapshot、JS REPL、web search、默认apps，使用空MCP配置，独立临时cwd，无项目配置；保留CLI安全规则，不使用绕过沙盒参数。提示词要求只生成答复，不执行任何动作。CLI版本若不支持这些参数应失败，不会自动降级到更宽权限。可设置5–180秒timeout；取消/超时终止本轮进程并清理临时目录。

真实provider的文字聊天与截图发送是不同状态。选择真实provider后，**默认仅文字**。必须另外勾选“允许当前真实provider发送目标窗口截图”才分析画面；UI显示provider与截图发送状态，服务端也拒绝未授权的真实发图请求。Codex可能使用云端；OpenAI兼容发送至你配置的endpoint。勾选不代表模型已连接成功，失败会显示错误。

## 记忆与数据

记忆树：领域 → 办公应用或游戏 → 任务 → 版本 → 场景 → 问题 → 步骤/条件/建议/来源。办公通过进程名隔离应用；用户可填写任务以进一步隔离。游戏按游戏与版本隔离。最多读取3个相关叶子，约2400字符预算。长教学按预算裁剪，原文保留在记忆树。输入“记住：经验”明确保存教学，或使用记忆页保存、纠正、忘记。

切到另一个应用会取消旧结果、清语音与聊天显示，建立新会话，并恢复该应用本次运行中独立的任务字段。不会混用办公与游戏、不同办公应用或不同任务的记忆。不同网页仍可能属于同一浏览器进程；需要用任务/场景区分。窗口标题变化也取消旧画面结果，避免换文档时旧回复被接收。返回设置窗暂停画面采集；手工文字聊天仍可使用。

`runtime/companion.sqlite3` 持久化文本聊天和明确教学；不持久化截图历史。普通provider截图仅在内存。本地测试不向远程发图。CLI的 `--image` 要求文件，因此本轮JPEG、prompt、输出和诊断临时文件会写入 `runtime/cli-temp/companion-cli-*`，正常完成、失败、取消和超时的 finally 清理。**不能称“画面从不落盘”**：强制结束整个Python进程、断电等情况可能留下该临时目录，退出服务后可手动删除。诊断内容不会打印到聊天或服务器日志。provider可能有自身的数据处理/保留政策。

## 公开攻略网址教学

在分层记忆页填写公开攻略网址，明确点击“教学导入网址正文”。领域、应用/游戏、任务、版本、场景、问题使用聊天页字段；游戏版本由你明确填写，不会从网页自动认定为当前版本。导入可读 HTML/纯文本正文，拆成最多24段记忆叶子，并保留完整来源URL、网页标题、抓取时间、内容SHA256及最多64000字符来源正文。重新导入同一网址仅更新当前分支内的该来源，其他版本和手工教学保留。树中点击导入叶子可检查正文片段与来源。 手工保存导入叶子（包括只改问题名/分支）会明确改为用户编辑教学，清除该叶子的网页URL、来源ID、标题与抓取时间，避免让原网页支持新正文；其他叶子及原来源记录保持独立，不会随此编辑改变。

抓取只接受无账号密码的HTTP(S)公开地址与默认端口，拒绝本机/私网/链路本地/保留地址/文件URL，检查全部DNS地址并将实际TCP连接固定到已验证的公网IP；HTTPS仍验证原域名。每次跳转重新检查，最多3跳；2MB响应上限、DNS等待上限、15秒抓取时限和2个并行导入名额。没有浏览器脚本执行、cookie或代理继承；拒绝压缩正文、PDF等非HTML/纯文本内容。动态、登录、付费或无法读取的网页会失败并提示手动教学。系统网络权限不足也可能无法导入。

导入网页是未经独立验证的参考资料，脚本/隐藏文本/常见推荐区会排除，但无法保证所有网站的正文提取质量。TapTap明确只读取当前post主区域内的rich-content正文，不回退到旁栏/推荐卡；正文区域未识别或图片为主且正文不足时拒绝导入。**本版不自动识别网页内图片里的知识**，纯图片攻略可能只有标题/说明可读，不能声称已学会整张图里的阵容。超出索引预算时界面标明只载入受限片段。查询在同领域/应用/任务/游戏/版本/场景内按当前问题关键词选择相关叶子，仍最多3叶与2400字符预算；模型应核对记忆与当前画面是否匹配，不硬套攻略。历史攻略应填写历史版本，不能据此保证当前游戏的指导准确。

新增测试覆盖明确教学授权、URL来源/抓取时间、同来源更新、版本隔离、问题关键词检索、DNS私网混合记录拒绝、实际连接固定公网IP及TLS原域名校验、私网跳转拒绝、无Content-Length的超限响应、压缩响应拒绝、脚本/隐藏文字/推荐内容排除，以及TapTap当前post与无标题旁栏隔离、图片为主不回退到其他攻略、挑战页拒绝。

## 兼容迁移与验证

此源码复制不带旧runtime/cache，未修改原D盘项目或已运行的8791服务。启动时SQLite幂等迁移旧 `messages` / `memories` 增加 `scope`, `task`, `application`，并为网址教学新增来源字段与 `guide_sources` 表；旧记忆归入游戏scope，其余字段默认空，旧游戏/版本/场景数据保留。保留原业务模块与 legacy `Settings`/storage接口；桌面服务使用项目provider.json和runtime，不依赖legacy默认模型配置。旧API `mode=sample/real` 分别映射demo/openai，真实截图增加明确 `allow_images: true` 要求。

需要迁移旧文本记忆时，先关闭新旧服务、备份旧SQLite，再自行将旧 `companion.sqlite3` 复制到新项目runtime。不要复制旧provider配置或凭据。项目代码不会自动去别处搜集数据。

```powershell
python -m pytest -q --basetemp .test-temp
```

开发自查：47测试通过，覆盖旧17业务测试与办公/游戏/应用/任务隔离、相同窗口句柄/PID/标题下的应用身份变化及PID回退拒绝、未授权远程截图拒绝、demo明确非AI、CLI固定只读参数和临时图清理、预取消不启动进程。另用原生UI离屏构建验证默认provider、跨应用generation与context清理、设置前台时手工聊天可继续。CLI真实模型、本机正常桌面游戏截图、中文系统语音仍需在正常Windows账号实际验收；没有将demo测试称为真实AI识图成功。

可选网页 `http://127.0.0.1:8793` 是手动分享入口，只接受浏览器报告为应用窗口的分享，拒绝屏幕/标签页；自动前台跟随和排除规则在原生桌宠中实现。网页语音识别由浏览器提供，可能使用在线服务。

## C03 · 无主窗口 VRM 桌宠（2026-10-15）

`python -m app.desktop` 启动后**没有主窗口**：屏幕上是 VRM 角色本体（无边框、透明、置顶、不占任务栏/Alt+Tab），设置与聊天收在托盘图标与角色双击/右键菜单里。角色由 `packages/companion-pet-web`（three@0.180.0 + @pixiv/three-vrm@3.5.5，构建产物 `dist/`）经 QtWebEngine 渲染，代码在 `app/pet3d/`。旧 2D 角色保留为降级路径（`app/pet3d/fallback2d.py`），WebGL 或模型失败时带可见错误横幅回退，不会白屏。

窗口机制（docs/03 §3.3 的穿透方案经 2026-10-15 真机探针修正，过程见 `work/c03-REPORT.md`）：Qt 用 `UpdateLayeredWindow`（逐像素 alpha）绘制 `WA_TranslucentBackground` 窗口，与 `SetLayeredWindowAttributes(LWA_COLORKEY)` 互斥（同开必致 Qt 重绘失败），而 Qt6 WebEngine 子窗口对整窗逐点参与 hit-test、不随 alpha 放行——两条“逐像素”路线都被真机证伪。实际生效的穿透是 `WS_EX_LAYERED` 之上切换 `WS_EX_TRANSPARENT`：开启后整个窗口不接收鼠标（含角色本体），与 docs/03「穿透态角色不一定可点、恢复靠热键+托盘」的产品口径一致；可交互态该位必被清除（单测断言 `WS_EX_APPWINDOW` 恒无、交互态 `WS_EX_TRANSPARENT` 恒无）。恢复通道双保险：全局热键 `Ctrl+Alt+Space`（切换穿透/可交互）与托盘图标；退出热键 `Ctrl+Alt+Shift+Q`，配置在 `pet.json`（见 `app/pet3d/config.py` 默认值，损坏的配置不阻止启动）。多显示器/混合 DPI 的位置换算全部走 `app/pet3d/geometry.py` 的纯函数（物理像素为唯一真值，有单测），角色尺寸按各屏缩放比取逻辑尺寸。

VRM 行为：idle 呼吸 + 眨眼、视线跟随光标（越界裁剪到 ±1）、左键点触 happy 反应、按住拖动窗口（按下位移 >6px 判为拖动、`drag_end` 收尾）、右键弹出与托盘一致的菜单。

**默认模型**：`assets/models/VRM1_Constraint_Twist_Sample.vrm`，作者 (c) 2022 pixiv Inc.，许可 VRM Public License 1.0（https://vrm.dev/licenses/1.0/ ，内嵌 meta 允许再分发与修改后再分发）；逐条证据见 `assets/models/LICENSE-NOTE.md` 与 `THIRD_PARTY_NOTICES.md`。该目录**不进 git**；缺失时程序报明确错误并回退 2D，不会静默空白。

**如何加新模型**：把 `.vrm` 放入 `assets/models/`，在 `pet.json` 写 `{"model": "文件名.vrm"}` 后重启（或托盘“重新加载 3D 角色”）。注册在 `app/pet3d/vrm_loader.py` REGISTRY 之外的模型按“用户自行导入”处理：许可责任归导入者，程序不做许可背书；未核实许可的官方 sample（如 Seed-san）不得内置。

前端改动后需重建：`cd packages/companion-pet-web` → `npm.cmd ci` → `npm.cmd run build`（产物 `dist/` 由本地回环 HTTP 服务加载）。开发期想让 vite 代理生效，先设 `$env:COMPANION_PET_ASSETS_PORT='8799'` 再启动桌宠。C03 新增测试：`tests/test_geometry.py`、`tests/test_passthrough.py`、`tests/test_vrm_loader.py`（纯逻辑 + Win32 调用录制断言，无需真机）。100%/150%/200% DPI 与穿透手感属真机验收（B6），本轮未验证。
