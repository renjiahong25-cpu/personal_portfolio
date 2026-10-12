# PianoFlow · AI 音乐工作台

本地运行的音乐工作台（MIT 许可，见 [LICENSE](LICENSE)）。核心链路：
**搜歌 → 下载音频 → 缓存 → 钢琴转录 → 乐理分析与配器 → 可跟弹的乐谱**。

> ⚠️ 版权声明：本项目仅供个人学习与音乐研究使用。下载的音频只保存在本机缓存目录，
> 禁止批量下载、二次分发、转载或商用，使用者自行承担版权责任。
> UI 首次打开会强制弹出该声明。

当前状态：

- **第一期（音源链路）**：搜索、下载、内容寻址缓存 —— 已完成。
- **第二期（钢琴转录 + 乐理解析）**：basic-pitch / Transkun 转录、调性/和弦/力度分析、
  双手划分、配器（忠实度 × 难度 × 织体）、MusicXML/MIDI 输出 —— 已完成并有测试覆盖。
- **第三期（流行转钢琴 + 跟弹）**：旋律保留 + 左手重建、fluidSynth 渲染跟弹音轨、
  前端 OSMD 五线谱与琴键高亮 —— 已完成并有测试覆盖。
- **LLM 审美映射**：尚未上线。代码里只有 `enable_external_ai` 开关（默认 `False`），
  不接入任何外部模型。
- 音源目前**只注册了 Bilibili**（Extractor 抽象已就绪，见下）。

## 快速开始

### 前置

| 依赖 | 要求 | 必需性 |
|---|---|---|
| Python | 3.11+（venv 或 conda 环境均可） | 必需 |
| Node.js | v20+ | 必需（前端） |
| ffmpeg | 任意发行版 | 可选：上传转码、下载后处理需要它 |
| fluidSynth + SoundFont | 见 `backend_python/scripts/bootstrap.ps1` | 可选：渲染跟弹钢琴音轨 |
| basic-pitch ONNX（230 KB） | 已随仓库提交，`models/basic_pitch/nmp.onnx` | 必需（转录默认模型） |
| ByteDance 154 MB 模型 | `bootstrap.ps1` 下载并校验 | 可选：高分辨率踏板分析 |

### 环境搭建

```powershell
# 1. Python 环境（二选一；把该环境的 python 记为 $PYTHON）
python -m venv .venv
#    或：conda create -n pianoflow python=3.11

# 2. 后端依赖（最小集；开发/测试用 dev 集）
& $PYTHON -m pip install -r backend_python/requirements.txt
#    或：& $PYTHON -m pip install -e "backend_python[dev]"

# 3. 可选资产（154MB 模型 / 206MB SoundFont / fluidSynth / ffmpeg 的检查与获取指引）
powershell -ExecutionPolicy Bypass -File backend_python/scripts/bootstrap.ps1
```

### 启动（两个窗口，各保持不关）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
```

解释器定位走三级回退：`PIANOFLOW_PYTHON` 环境变量 → PATH 上的 `python` → 报错提示。

打开 **http://127.0.0.1:5173** （注意是 `127.0.0.1`：Vite 显式绑定 IPv4，默认只监听 `[::1]` 会导致连不上）。

生产模式（后端直接托管构建产物，只需后端一个进程）：

```powershell
cd frontend; npm run build
# 然后只启动后端，访问 http://127.0.0.1:8890
```

### 测试

```powershell
cd backend_python
& $PYTHON -m pytest
```

缺 ffmpeg / fluidSynth / 大模型权重时，相关测试会**自动 skip**（不失败）。

---

## 架构

```
浏览器 (Vue 3 + Vite + OSMD 五线谱)
   │  /api/*            ← 开发期由 Vite 代理到 8890
   ▼
Tornado 后端 (127.0.0.1:8890)
   ├── core/           配置、错误类型、日志、节流
   ├── tasks/          任务状态机 + SSE 广播（含多路复用会话流）
   ├── services/
   │   ├── music_source/     Extractor 抽象 · Bilibili 客户端（当前唯一音源）
   │   ├── download/         下载 + 转码流水线（进度、队列、合规限速）
   │   ├── cache/            内容寻址音频缓存（TTL + LRU + 容量上限）
   │   ├── audio_analysis/   basic-pitch / ByteDance 高分辨率 / Transkun 转录，
   │   │                     调性、和弦、力度、踏板（ONNX + 子进程隔离）
   │   ├── arrangement/      小节网格、双手划分、和声适配、配器：
   │   │                     5 种模式 (patterns) + 8 种织体 (textures)、忠实度 × 难度
   │   └── score/            流水线入口 generate_score()、上传归一化、
   │                         fluidSynth 跟弹音轨渲染（踏板 + 房间混响）
   └── api/            handlers / routes（music、cache、media、score、health、events）
```

关键设计：

- **Extractor 抽象**：平台差异全部收敛在 `Extractor` 实现里，业务代码只认 `Track`。
  目前只注册了 Bilibili；将来接其他平台（Node sidecar）不需要改业务代码。
- **搜索不用 yt-dlp**：B 站搜索走自研客户端（`spi` 取真 buvid → 一次 `search/type`），
  原因见 `docs/spike-2026-10-04-source-availability.md`。下载仍用 yt-dlp（它已实现签名的 `playurl`）。
- **yt-dlp 永不在 event loop 上运行**：统一 `asyncio.to_thread`。
- **全页面单条事件流**：`GET /api/events`，避免逐任务开流耗尽浏览器连接数。
- **内容寻址缓存**：文件名是 `sha1(source:sourceId:format)`，天然去重且不含空格/中文。

---

## HTTP 接口

### 第一期 · 音源链路

| 方法 | 路径 | 请求 | 响应 | 用途 |
|---|---|---|---|---|
| GET | `/api/health` | — | `{status, version, sources[], ffmpeg{configured,resolved,onPath}, frontendBuilt, externalAiEnabled}` | 后端、音源可用性、ffmpeg 是否解析到 |
| GET | `/api/events` | — | **SSE** 事件流 | 一次连接推送所有任务状态变化（全页面共用一条流） |
| POST | `/api/music/search` | `{keyword, platform?, limit?}` | 预览卡片数组（标题/封面/时长/UP 主） | 搜索（默认 platform=`bilibili`，已注册音源只有它） |
| POST | `/api/music/download-audio` | `{source, sourceId, format, title?}` | `202` + `{taskId}` | 发起单曲下载（队列上限 5，超出 429） |
| GET | `/api/music/download-audio/<task_id>` | — | 任务快照 | 轮询兜底 |
| GET | `/api/music/download-audio/stream?task_id=` | — | **SSE** 单任务流 | 兼容/调试用 |
| GET | `/api/music/download-audio/queue` | — | 队列快照 | 查看排队情况 |
| GET | `/api/media/cover?url=` | — | 图片字节 | 封面代理（B 站防盗链，`*.hdslb.com` SSRF 白名单） |
| GET | `/api/cache/status` | — | 条目与占用 | 缓存状态 |
| GET | `/api/cache/audio/<key>` | — | 音频字节（`Range` 支持） | 播放缓存音频，可拖动进度 |
| POST | `/api/cache/clean` | `{mode: "cleanup" \| "clear"}` | 清理结果 | 按 TTL/LRU 清理或清空缓存 |

### 第二期/第三期 · 转录与配器（`/api/score/*`）

一条流水线两个入口：缓存里的歌（`cacheKey`）或本地文件（multipart 上传，先归一化进缓存）。
分析在线程池执行（CPU 密集，秒级），整体超时 900 s；`previewSeconds`（0–600，0=整曲）
截断音频，响应里 `truncated` 会如实说明。

| 方法 | 路径 | 请求 | 响应 | 用途 |
|---|---|---|---|---|
| GET | `/api/score/options` | — | 见下 | UI 可选项的单一事实来源 |
| POST | `/api/score/analyse` | JSON，见下 | `ScoreResult` + `request` + `cacheKey` | 对已缓存的音频出谱 |
| POST | `/api/score/upload` | multipart：`audio` 文件 + 同 JSON 的表单项 + `analyse`（默认 1） | 仅存文件：`{upload:{cacheKey,...}}`；一步到位：`ScoreResult` + `upload` | 本地文件出谱（wav/mp3/m4a/flac/ogg/opus/aac/wma，≤100 MB） |
| GET | `/api/score/musicxml` | — | `application/vnd.recordare.musicxml+xml` | 最新一份 MusicXML（给 OSMD 渲染） |
| GET | `/api/score/midi` | — | `audio/midi`（附件下载） | 最新一份双轨 MIDI（可导入制谱软件） |
| GET | `/api/score/piano` | — | `audio/wav`（内联，`Accept-Ranges`） | 最新一份跟弹钢琴音轨；未装 fluidSynth/SoundFont 时 404，UI 回退到原曲 |

**`GET /api/score/options` 响应字段**

| 字段 | 值 |
|---|---|
| `fidElities` | `["as-played", "hybrid", "arranged", "transcribed"]`（忠实度；字段名是服务端原样） |
| `difficulties` | `{1,2,3,4}` → 每级的说明（低音/柱式/分解/阿尔贝蒂 + 右手补弦规则） |
| `patterns` | `["root_only", "root_octave", "block_chord", "broken_chord", "alberti_bass"]` |
| `textures` | 8 种织体 → 中文标签（`block` 柱式、`perc332` 打击感 3-3-2、`rhythm` 节奏动机、`half` 半分解、`bass` 低音线条、`arplong` 全分解长线条、`arpfull` 全分解琶音、`riff` 乐段动机） |
| `maxPreviewSeconds` | `600` |
| `transcribers` | `["basic_pitch", "transkun"]` |
| `transcriberHints` | 每个模型的取舍说明（速度 vs 时值来源） |
| `defaultTranscriber` | `"basic_pitch"` |
| `transkunAvailable` | 本机是否装好 Transkun 解释器（没装就不给 UI 提供这个选项） |

**`POST /api/score/analyse` 请求体**（`upload` 的表单项与之同义；`fillChords` 只走 JSON 路径）

| 字段 | 类型 / 取值 | 默认 | 含义 |
|---|---|---|---|
| `cacheKey` | 40 位 hex | 必填 | 缓存音频 key |
| `fidelity` | `as-played` \| `transcribed` \| `hybrid` \| `arranged` | `arranged` | 忠实度（见下） |
| `difficulty` | `1`–`4`（也接受 `"L1"`–`"L4"`） | `3` | 配器难度预算（和弦宽度、织体上限） |
| `pattern` | 上表 5 种之一 | 空 = 按难度自动 | 左手伴奏模式 |
| `texture` | 上表 8 种之一 | 空 = 用 pattern 系统 | 左手织体（与 `texturePlan` 互斥） |
| `texturePlan` | 字符串 | 空 | 分段织体计划（已校验；`arrange()` 的织体计划入口已就绪，当前 API 尚未接线，传了会被忽略） |
| `transcriber` | `basic_pitch` \| `transkun` | `basic_pitch` | 转录模型；请求 transkun 而未安装时明确报错，不静默回退 |
| `fillChords` | bool | `false` | 单音补成三和弦（忠实模式下才用） |
| `previewSeconds` | 0–600 | `0`（整曲） | 截断时长 |
| `title` | 字符串（≤200 字符） | 缓存里的歌名 | 谱面标题 |

**忠实度四档**（右手旋律在任何档位都不改写——生成的旋律不如转录的）：

| 值 | 行为 |
|---|---|
| `as-played` | 每小节保持转录原样，不做任何重写 |
| `transcribed` | 忠实转谱：保留演奏的全部音符，只按和弦做声部重排（不删音、不折八度；超手宽的和弦在 `fidelityReport.unreachableGroups` 里报告而不改写） |
| `hybrid` | 质量分高的小节保留，低分小节按同一模式重写（风格一致性：模式整曲只选一次） |
| `arranged` | 左手全部重建（5 种 pattern 或 8 种 texture），右手保留转录旋律 |

**响应**：`score.notes`（每个音的起始/时值/MIDI 音高/手，跟弹高亮的唯一时间轴）、
`key`（调性 + 置信度）、`chords`（和弦）、`quality`（小节质量与重配器统计）、
`handSplit`、`grid`（小节网格/速度）、`dynamics`（从录音实测的力度分布）、
`fidelityReport`、`musicxmlUrl` / `midiUrl` / `pianoTrackUrl`（带 mtime 版本号的输出地址，
没有产物时为 `null`，UI 不会给出空播放器）。

---

## 配置（全部通过环境变量，前缀 `PIANOFLOW_`）

后端（`backend_python/src/core/config.py`，frozen dataclass + `from_env`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PIANOFLOW_HOST` / `PIANOFLOW_PORT` | `127.0.0.1` / `8890` | 监听地址 |
| `PIANOFLOW_CACHE_DIR` | `<repo>/cache/audio` | 音频缓存目录（`uploads/`、`scores/` 在其旁侧） |
| `PIANOFLOW_CACHE_MAX_BYTES` | `2147483648`（2 GiB） | 缓存容量上限，超出按 LRU 淘汰 |
| `PIANOFLOW_CACHE_TTL_DAYS` | `7` | 缓存过期天数 |
| `PIANOFLOW_LOG_DIR` / `PIANOFLOW_LOG_LEVEL` | `<repo>/logs` / `INFO` | 日志 |
| `PIANOFLOW_FFMPEG_LOCATION` | 三级回退，见下 | ffmpeg 目录（yt-dlp 后处理与上传转码需要） |
| `PIANOFLOW_FLUIDSYNTH_LOCATION` | 三级回退，见下 | fluidsynth 可执行文件所在目录（跟弹音轨渲染） |
| `PIANOFLOW_SOUNDFONT_PATH` | `<repo>/models/soundfonts/MuseScore_General.sf2` | SoundFont（GPL，需自行下载，见 bootstrap） |
| `PIANOFLOW_TRANSCRIBER` | `basic_pitch` | 默认转录模型（`basic_pitch` / `transkun`） |
| `PIANOFLOW_TRANSKUN_PYTHON` | 空 = 按常见 conda 位置探测 | Transkun 独立环境的解释器（它需要 torch） |
| `PIANOFLOW_SEARCH_MIN_INTERVAL_SECONDS` | `2.0` | 两次搜索最小间隔（B 站风控） |
| `PIANOFLOW_DOWNLOAD_MIN_INTERVAL_SECONDS` | `5.0` | 两次下载最小间隔（合规限速） |
| `PIANOFLOW_DOWNLOAD_MAX_CONCURRENCY` | `1` | 并发下载数（保持 1：串行限速） |
| `PIANOFLOW_MAX_AUDIO_SECONDS` | `3600` | 单曲时长上限，超过拒绝下载 |
| `PIANOFLOW_BILIBILI_COOKIE_FILE` | `<repo>/data/cookies/bilibili.txt` | 自动维护的 buvid cookie jar |
| `PIANOFLOW_YTDLP_COOKIE_FILE` | 空 | 可选：浏览器导出的 Netscape cookie（登录态） |
| `PIANOFLOW_ENABLE_EXTERNAL_AI` | `false` | LLM 审美映射开关。**默认关闭，当前无任何外部 AI 接入** |

外部工具的三级回退（环境变量优先，其次自动探测，最后文档化默认值；
探测不到且目录不存在时，相关功能降级而不是报错——`/api/health` 会如实报告）：

1. `PIANOFLOW_FFMPEG_LOCATION` / `PIANOFLOW_FLUIDSYNTH_LOCATION` 显式指定；
2. 自动探测：`ffmpeg` / `fluidsynth` 在 `PATH` 上，或当前 Python 环境的
   conda 风格目录（`<env>\Library\bin` / `<env>\bin`）；
3. 文档化默认值：`<repo>/tools/ffmpeg/bin`、`<repo>/tools/fluidsynth/bin`
   （`bootstrap.ps1` 的放置位置）。

启动脚本用的变量（`scripts/*.ps1`）：`PIANOFLOW_PYTHON`（后端解释器）、
`PIANOFLOW_NPM`（npm.cmd 路径）、`PIANOFLOW_AOCI`（aoci 工具，可选）。

---

## 风控应对（重要）

B 站风控会对高频请求返回 `HTTP 412` 或 `code -412`。表现为搜索报
「平台风控拦截了本次请求」。

处理方式：

1. **别连续快速点搜索**（已内置 2 秒节流，但仍受整体请求频率影响）。
2. 被拦后**等 1–2 分钟**再试，重试只会延长封禁。
3. 仍频繁被拦，就配置登录 cookie：
   浏览器登录 B 站 → DevTools → Application → Cookies → 导出为 Netscape 格式，
   然后设 `PIANOFLOW_YTDLP_COOKIE_FILE` 指向它。

后台会自动维护 `data/cookies/bilibili.txt`（来自 `/x/frontend/finger/spi` 的真实 `buvid3/buvid4`），
搜索与下载共用，一般情况不需要手工配置。

---

## 合规设计

- 单曲下载，**不做批量**；队列深度上限 5，超出返回 429。
- 下载串行 + 最小间隔限速（默认 5 秒）。
- 单曲时长超过 `PIANOFLOW_MAX_AUDIO_SECONDS` 直接拒绝。
- 音频只写本机缓存目录，不上传任何云端服务。
- UI 首次使用强制版权声明。

---

## 项目约定

- 项目根路径可能**含空格**（如 `Program Files\pianoflow`）：代码里一律用 `pathlib`，禁止字符串拼路径。
- 每完成一个模块，跑一次非交互代码审查（`scripts/review.ps1` + `qodercli`），critical/high 必须清零。
- 仓库级认知索引由 `aoci` 维护（`aoci.txt` + `.aoci/`，纯离线 + 人工策展）。
- 开发流程与硬性约定见 `AGENTS.md`。

## 文档

- `README.en.md` —— 英文版（与中文版对齐）
- `docs/spike-2026-10-04-source-availability.md` —— **音源可用性实测报告**（Listen1 为何废弃、平台实测数据、B 站风控与解法）
- `PianoFlow AI音乐工作台技术栈结论文档.md` —— 原始技术栈文档（顶部已加实测修正区块）
- `AGENTS.md` —— 仓库治理与项目约定（aoci 托管）
- `backend_python/scripts/bootstrap.ps1` —— 首次运行的资产获取（模型 / SoundFont / fluidSynth / ffmpeg）

## 许可

- 本项目代码：**MIT**，见 [LICENSE](LICENSE)。
- 模型权重与第三方组件**不适用**本许可，各自许可记录在 `models/manifest.json`
  （basic-pitch ONNX：Apache-2.0，已随仓库提交；ByteDance 高分辨率模型：154 MB，
  未提交，清单记录 URL/许可/sha256 以便校验；MuseScore_General.sf2：GPL，未提交）。
- 下载的音频版权归原权利方，本项目只在本机缓存，禁止二次分发。
