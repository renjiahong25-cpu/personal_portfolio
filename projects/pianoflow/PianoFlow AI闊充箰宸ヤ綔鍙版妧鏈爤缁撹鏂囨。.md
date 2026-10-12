# PianoFlow AI音乐工作台技术栈结论文档

# ⚠️ 2026-10-04 实测修正（基于真实网络探测，优先于下文所有内容）

> 下文是**原始设计文档**，保留作为设计思路记录。但其中两条关键选型已被实测推翻，
> 详见 `docs/spike-2026-10-04-source-availability.md`（含完整探测数据）。

## 修正 1：Listen1 不可用，改为自研 Extractor

| 原方案 | 实测结论 |
|---|---|
| 复用 `listen1/listen1` 的 Python 平台解析代码 | ❌ master 停更于 2019-05，代码是 **Python 2**（`urllib2`/`xrange`/`iteritems`），在 Python 3.11 里 import 即 SyntaxError；本质是 PyInstaller 打包的 macOS 托盘程序，Tornado 只是内嵌 webview，不是可部署后端；4 个 provider 里虾米 2018 关停、网易老接口下线、QQ CDN 下线、豆瓣需图形验证码登录 |

**现状**：只借鉴其 track schema（平台前缀 id + 显式 `source`）与 provider 统一接口的抽象约定，
代码全部自研（`backend_python/src/services/music_source/`），以 `Extractor` 抽象隔离平台差异，
为将来可能的 Node sidecar 预留扩展位。

## 修正 2：音源平台实测可用性

2026-10-04 活体探测结果：

| 途径 | 实测 | 结论 |
|---|---|---|
| Bilibili（yt-dlp 搜索 + 直链 + ffmpeg） | ✅ 搜索到真实结果，直链转 wav 0.71s | **一期唯一主力平台** |
| 网易云 legacy 免签名搜索 | ✅ 200，`result.songs` 有数据 | 搜索层可用，**下载需自实现 AES+RSA 签名** |
| 酷狗 `song_search_v2` | ✅ 200，`data.lists` 有数据 | 同上，下载需 hash 签名 |
| 网易云 weapi 播放直链 | ⚠️ 空 body | 必须自己实现签名 |
| QQ 音乐 | ❌ HTTP 500 空 body | 彻底死，重试无意义 |
| YouTube | ❌ `WinError 10061` 本机网络层封锁 | 不支持 |

**最终实现**：不用 yt-dlp 的 `bilisearch`（它 flat 模式元数据全空、逐条解析慢、相关性差），
改为社区标准做法——`/x/frontend/finger/spi` 取真 `buvid3/buvid4` + 一次 `/x/web-interface/search/type`
拿到全部卡片字段；风控 `HTTP 412` 转成可读的 503 提示并配合 2 秒搜索节流。

## 修正 3：Magenta → basic-pitch（二期）

Magenta 原版基于 TF1，已停止维护，在 Windows 上依赖极重。
**改用 Spotify basic-pitch（ONNX）+ onnxruntime**，不引入 TensorFlow。
乐谱输出用 `music21` 生成 musicxml，前端用 **OpenSheetMusicDisplay** 渲染。

## 修正 4：几个必须纠正的实现细节

下文示例代码里存在实际会出错的地方，本项目已修正：

| 下文写法 | 问题 | 本项目做法 |
|---|---|---|
| `async def` 里用同步 `requests` | 阻塞 Tornado event loop | `httpx.AsyncClient` + 统一节流/重试 |
| 直接在 handler 里调 yt-dlp | 阻塞事件循环 | `asyncio.to_thread` 丢到工作线程，进度经 SSE 回传 |
| 字符串拼路径 | 项目根路径**含空格**（如 `Program Files\pianoflow`） | 全量 `pathlib`，yt-dlp `outtmpl` 显式转义 |
| 前端调 `/api/search` | Listen1 无 `/api` 命名空间 | 统一 `/api/music/*` |
| 封面直连 CDN | B 站图床防盗链，403 | 后端 `/api/media/cover` 代理（带 Referer + SSRF 白名单） |

## 修正 5：进度机制

下文未提进度。下文采用 **SSE**，但实现上做了关键修正：
**整个页面只开一条多路复用事件流 `GET /api/events`**，而不是每个任务一条连接——
浏览器每域名并发连接上限约 6 条，逐任务开流会耗尽连接导致整页请求排队（表现为"页面卡死"）。
另配 2 秒轮询兜底，所有 HTTP 请求带 `AbortController` 超时。

---

# Listen1 技术栈结论

> **原版 Listen1 后端（网页服务端）：Python \+ Tornado Web 框架**
>
> - 仓库：`listen1/listen1`（初代网页后端），Tornado 异步 Web，Python 爬虫做各大音乐平台的接口代理、搜索、获取播放链接、歌词。
>
> - `listen1-api` 库（JS 版本）：是**浏览器 / Electron 端 JS 工具库**，不是后端服务，只在前端 / Node 环境调用，不是服务端。
>
> - 桌面版 listen1\_desktop：Electron（JS / 前端），它是客户端，后端逻辑复用上面 Python 服务或者 JS api 库。
>
>

✅ 所以：**Listen1 的后端服务本体语言是 Python（Tornado）**。
你想要复用同语言，那我们项目后端直接用 Python 做整体后端，而不是 Rust/JS。

> 优势：
>
> 1. 同源：直接阅读、复用 Listen1 的 Python 爬虫 / 平台解析代码，不用跨语言调用；
>
> 2. 生态天然适配：yt\-dlp、ffmpeg、Magenta、音频 AI、LLM 调用全部都是 Python 生态，不用子进程跨语言折腾；
>
> 3. 学生开发成本最低，OpenCode 原生支持 Python；
>
> 4. 后续音频处理、MIDI、乐理知识库全部统一 Python，不用多语言混编。
>
>

> 缺点：相比 Rust，CPU 密集音频解析性能弱一点；但 MVP 阶段完全够用，后续如果性能瓶颈，可以把重计算模块单独用 Rust 写成扩展，Python 主服务调用。
>
>

# 更新文档：后端改为 Python \(Tornado\)，对齐 Listen1 后端语言

```markdown
# PianoFlow 一站式AI音乐工作台 产品&技术完整文档
> 项目定位：学生开源项目，**MVP 全部本地免费运行于 OpenCode**，内置 Listen1 多平台音源搜索 + yt-dlp 音频拉取，AI 音频制谱、旋律审美解析；高级版可外接付费音乐 AI API 作为可选增值路由；电子琴硬件接入放在最后迭代版本。
> 核心原则：零基础成本，优先复用开源项目，不重复造轮子；所有音频处理、模型推理本地执行；仅在用户主动选择时调用外部付费 API。
> 版权声明：本项目仅供个人学习、音乐研究使用。通过 Listen1 获取的音源地址、yt-dlp 下载的音频文件仅限本地个人分析，禁止商用、分发、二次传播，使用人自行承担相关版权责任。

## 目录
1. [产品概述](#1-产品概述)
2. [竞品分析](#2-竞品分析)
3. [产品核心竞争力](#3-产品核心竞争力)
4. [功能规划 & MVP 定义](#4-功能规划--mvp定义)
5. [技术栈选型（更新：后端 Python + Tornado，对齐Listen1后端）](#5-技术栈选型更新后端-python--tornado对齐listen1后端)
6. [代码架构设计（Python后端）](#6-代码架构设计python后端)
7. [接口文档（接口协议保持不变）](#7-接口文档接口协议保持不变)
8. [核心模块设计：感性审美 ↔ 乐理特征映射](#8-核心模块设计感性审美--乐理特征映射)
9. [Listen1 + yt-dlp 音源模块集成方案（Python实现，复用Listen1同语言）](#9-listen1--yt-dlp-音源模块集成方案python实现复用listen1同语言)
10. [产品迭代路线](#10-产品迭代路线)
11. [实施文档（Python本地部署）](#11-实施文档python本地部署)
12. [风险与约束](#12-风险与约束)

---

## 1. 产品概述
### 1.1 产品名称
PianoFlow AI 音乐工作台
### 1.2 产品简介
一款本地运行的开源音乐工作台，面向音乐爱好者、学琴用户。
核心 MVP 链路：**跨平台搜索歌曲 → 获取音源地址 → 下载提取音频 → AI 音频分析 → 自动制谱 + 旋律 / 装饰音解析 + 感性审美标签映射**
后续迭代扩展：听歌、写歌、AI 编曲、练歌、人声分离；**电子琴硬件 MIDI / 音频接入为最终迭代版本**。

### 1.3 核心目标
1. MVP 完全免费，依托 OpenCode 本地执行，不强制依赖任何付费云端 API；
2. 复用成熟开源项目：Listen1（Python后端，多平台音乐搜索代理）、yt-dlp（音频下载提取）、Magenta（MIDI / 乐谱生成）；**后端语言和Listen1保持一致：Python**，可直接复用Listen1平台解析代码；
3. 实现「感性描述 → 乐理特征」双向映射：用户输入 “宏大、悲伤克制” 等描述，AI 转化为可解析旋律、装饰音、编曲参数；
4. 高级版可选路由：接入外部音乐 AI API 作为增值能力，非必须。

### 1.4 用户画像
学生、业余钢琴爱好者、独立音乐学习者；预算有限，希望从现有歌曲学习编曲、旋律写法，自动生成乐谱，辅助练琴。

---

## 2. 竞品分析
|竞品|核心能力|短板|PianoFlow 差异化|
|---|---|---|---|
|MuseScore|专业制谱软件，支持导入 MIDI 制谱|无跨平台搜歌；不能从音频自动解析旋律审美；付费增值功能多|内置搜歌 + 音频提取 + 审美解析一体化，本地开源免费；后端Python，复用Listen1成熟音乐平台爬虫逻辑|
|网易云音乐 / QQ 音乐|听歌、曲库庞大|无制谱、旋律分析能力，无法导出音频做乐理研究|打通 “搜索 - 下载 - 分析 - 制谱” 完整流水线|
|剪映 AI 音乐 / 部分在线 AI 编曲|文字生成音乐，音频分离|在线服务，收费；无法深度解析旋律走向、装饰音、审美特征；不能对接本地曲库|本地离线运行，可解析现有歌曲的乐理与审美逻辑|
|Magenta 官方 Demo|AI 生成 MIDI、简单旋律|无音源搜索、音频导入，缺少感性词到乐理的映射层|增加音频解析、审美标签映射，接入 Listen1 曲库|
|各类在线 K 歌工具|伴奏、人声播放|无制谱、编曲学习能力，无法自定义解析旋律细节|练歌作为后续迭代，核心侧重乐理学习与制谱|

---

## 3. 产品核心竞争力
1. **全链路本地免费（MVP）**
依托 OpenCode 运行，Listen1、yt-dlp、Magenta 全部开源，学生零成本；高级 API 仅作为可选增值，不强制。后端Python，和Listen1同源，可直接复用多平台音乐解析代码，减少重复造轮子。

2. **一站式流水线**
跨平台搜歌 → 音频提取 → AI 音轨分析 → 自动制谱 → 旋律走向 / 装饰音解析 → 感性审美标签提取，市面上同类工具很少做到端到端闭环。

3. **感性审美与乐理双向映射**
把 “宏大、悲伤克制、轻快灵动” 这类人类感性描述，映射成 AI 可识别的量化乐理参数；也能反向解析一首歌，输出对应的感性标签。

4. **模块化可扩展**
架构松耦合，音源模块、制谱模块、审美解析模块、写歌 / 练歌模块独立；电子琴硬件接入单独放在最后迭代，不干扰 MVP 开发。Python生态对MIDI、音频库支持丰富。

5. **开源可二次修改**
全部代码在 OpenCode 内维护，学生可自由修改模型、规则、提示词、乐理知识库。

---

## 4. 功能规划 & MVP 定义
> MVP：最小可用版本，**不包含电子琴连接**；电子琴硬件接入为最后迭代。

### ✅ MVP 包含功能
1. 多平台歌曲搜索模块：基于Listen1 Python逻辑，支持网易云、QQ 音乐、Bilibili、咪咕等平台搜索，获取歌曲元信息、播放链接、歌词。**直接复用Listen1的平台解析代码**。
2. 音频下载 & 提取模块：Python调用yt-dlp，输入播放链接，下载并转码为 WAV，本地缓存。
3. 音频制谱：音频转 MIDI，生成乐谱（钢琴谱为主，可扩展其他乐器谱）。
4. 旋律解析：提取旋律走向、节奏、装饰音、和弦序列。
5. 审美映射模块：
    - 正向：感性词 → 乐理参数（例：悲伤克制 → 调式、速度、音程、装饰音规则）
    - 反向：解析歌曲音频，输出对应的审美标签 + 对应的乐理特征说明
6. 本地文件管理、音频缓存自动清理；版权提示弹窗。
7. 可选高级路由开关：用户可手动开启，调用外部音乐 AI API（DeepSeek 等）作为增值能力，不影响基础免费功能。

### ❌ MVP 不包含功能（全部放到后续迭代）
- 写歌、AI 完整编曲生成
- 练歌、人声分离、伴奏提取
- 视频音轨提取（V1.0.1 迭代）
- 电子琴 MIDI / 音频硬件接入（**最终迭代**）

---

## 5. 技术栈选型（更新：后端 Python + Tornado，对齐Listen1后端语言）
> 全部优先开源免费，适配 OpenCode 本地运行
### 后端（Python，与Listen1后端语言完全一致）
- Python 3.10+
- Tornado：异步Web服务框架（Listen1原版后端框架），提供REST HTTP接口
- 核心包：
  - `requests`：HTTP请求，音乐平台接口请求
  - `tornado`：Web服务、异步IO
  - `yt-dlp`：Python包，音频流下载，ffmpeg做音频转码
  - `serpy`/`json`：JSON序列化，接口数据处理
  - `Magenta`：Google开源音乐AI，MIDI生成、旋律分析、乐谱生成
  - `soundfile`/`librosa`：音频读取、旋律提取、乐理分析
  - `watchdog`：本地文件监控、缓存清理
- ffmpeg：音频格式转换、音频预处理（降噪、音量归一化）

### 大模型层（OpenCode 本地调用）
- LLM：本地大模型，维护乐理知识库，负责「感性审美词 ↔ 乐理参数」映射，约束 AI 编曲规则
- 高级增值路由：可选调用 DeepSeek 等音乐能力 API（用户主动开启，非必需）

### 前端
- Web前端：Vue / React 静态页面，Tornado直接托管静态资源
- 前后端通过JSON HTTP接口通信

### 开发&版本管理
- OpenCode：项目脚本编排、工作流管理，直接运行Python后端
- Git：版本管理
- pip：Python包管理

> 选型对比说明：
> - Python(Tornado)：Listen1原生后端语言，**可以直接复用Listen1的音乐平台爬虫/解析代码**；yt-dlp、Librosa、Magenta全部原生Python，不用跨语言调用；学生上手简单。缺点：CPU密集型任务性能弱，后续可把重计算模块用Rust扩展做优化。
> - Node.js：JS生态，适合前端，但是无法直接复用Listen1后端Python爬虫。
> - Rust：高性能，但需要重写全部音乐平台解析逻辑，学习成本高，MVP阶段没必要。

---

## 6. 代码架构设计（Python后端）
```

PianoFlow/
├── backend\_python/                \# Python 后端（Tornado），对齐 Listen1 技术栈
│   ├── \[app\.py\]\(app\.py\)                     \# Tornado Web 服务入口，路由注册
│   ├── requirements\.txt
│   └── src/
│       ├── services/
│       │   ├── music\_source/      \# Listen1 音源模块（复用 Listen1 Python 逻辑）
│       │   │   ├── listen1\\\[\_service\.py\]\(\_service\.py\)   \# 复用 Listen1 平台爬虫，歌曲搜索、获取 playUrl、歌词
│       │   │   ├── ytdlp\\\[\_service\.py\]\(\_service\.py\)     \# 调用 yt\-dlp 下载音频并转码 WAV
│       │   │   └── cache\\\[\_manager\.py\]\(\_manager\.py\)     \# 本地音频缓存管理、自动清理
│       │   ├── audio\_analysis/     \# 音频解析模块
│       │   │   ├── midi\\\[\_transcribe\.py\]\(\_transcribe\.py\)   \# 音频转 MIDI，生成乐谱
│       │   │   ├── melody\\\[\_parser\.py\]\(\_parser\.py\)     \# 提取旋律走向、和弦、装饰音
│       │   │   └── aesthetic\\\[\_mapping\.py\]\(\_mapping\.py\) \# 审美 \- 乐理双向映射核心
│       │   └── ai\_router/          \# AI 路由模块（本地模型 \+ 可选外部 API 路由）
│       │       ├── local\_ai\\\[\_module\.py\]\(\_module\.py\)
│       │       └── external\_api\\\[\_router\.py\]\(\_router\.py\)
│       ├── utils/
│       │   ├── file\\\[\_helper\.py\]\(\_helper\.py\)
│       │   └── rule\\\[\_helper\.py\]\(\_helper\.py\)
│       └── config/
│           └── \[config\.py\]\(config\.py\)           \# 配置：缓存上限、API 开关、请求限流
├── frontend/                      \# 前端静态网页
├── cache/                         \# 本地音频缓存目录
├── output/                        \# 输出 MIDI、乐谱文件
├── docs/                          \# 项目文档
└── \[README\.md\]\(README\.md\)

```Plain Text
### 核心数据流（MVP 主链路）
```

用户输入歌曲名称 → listen1\_service（复用 Listen1 逻辑）搜索，返回歌曲列表与 playUrl
→ ytdlp\_service 调用 yt\-dlp，下载音频，转码 WAV 存入本地 cache
→ audio\_analysis 读取 WAV，音频转 MIDI，解析旋律 / 装饰音 / 和弦
→ aesthetic\_mapping 模块：提取乐理特征，映射为感性审美标签
→ 前端展示：乐谱、旋律走向图、审美说明

> 可选分支：用户开启高级路由，调用外部音乐 AI API 做增强生成
>
>

```Plain Text
---

## 7. 接口文档（接口协议完全不变）
> 全部为内部REST接口，Tornado提供HTTP服务，OpenCode/前端调用，JSON入参出参不变

### 7.1 音乐搜索接口（listen1）
`POST /api/music/search`
**入参**
```json
{
  "keyword": "歌曲名称",
  "platform": "netease|qq|kugou|bilibili|migu|all"
}
```

**返回**

```json
{
  "list": [
    {
      "title": "歌名",
      "artist": "歌手",
      "album": "专辑",
      "duration": 240,
      "source": "netease",
      "playUrl": "试听播放地址",
      "lyric": "歌词文本"
    }
  ]
}
```

### 7\.2 音频下载转码接口（yt\-dlp）

`POST /api/music/download-audio`
**入参**

```json
{
  "playUrl": "listen1返回的播放链接",
  "format": "wav|mp3",
  "savePath": "./cache/xxx.wav"
}
```

**返回**

```json
{
  "audioPath": "本地音频绝对路径",
  "duration": 240,
  "status": "success"
}
```

### 7\.3 音频制谱 \& 旋律解析接口

`POST /api/audio/analyze`
**入参**

```json
{
  "audioPath": "./cache/song.wav"
}
```

**返回**

```json
{
  "midiPath": "./output/song.mid",
  "scorePath": "./output/song.musicxml",
  "melodyInfo": {
    "chordSequence": ["C", "Am", "F", "G"],
    "melodyContour": "上行-平缓-下行",
    "ornaments": ["倚音", "滑音"],
    "tempo": 78,
    "scale": "自然小调"
  }
}
```

### 7\.4 审美双向映射接口

`POST /api/aesthetic/mapping`
**模式 1：反向解析（音频乐理 → 感性标签）**
入参：旋律解析结果 melodyInfo
输出：

```json
{
  "tags": ["悲伤克制"],
  "explanation": "小调，速度78，长音为主，少量装饰音，大跨度音程少，情绪压抑内敛",
  "musicParams": {"mode":"minor", "tempo":78, "ornamentDensity":0.15}
}
```

**模式 2：正向生成（感性词 → 乐理参数）**
入参：`{"prompt":"宏大史诗"}`
输出：乐理参数配置，可送入 Magenta 生成旋律 MIDI

```json
{
  "musicParams": {
    "mode": "自然大调",
    "tempo": 82,
    "range": "宽音域",
    "ornamentDensity": 0.35,
    "chordType": "大七和弦为主"
  }
}
```

### 7\.5 高级 AI 路由开关接口

`POST /api/ai/set-route`
入参：`{"useExternalApi": true/false}`
作用：切换是否启用外部付费 API，关闭则全部本地运行。

---

## 8\. 核心模块设计：感性审美 ↔ 乐理特征映射

### 8\.1 核心思路

维护一份**乐理知识库**（存储在本地 JSON / 文本，Python 读取，转发给 OpenCode 本地 LLM），包含乐理规则、编曲范式、审美对应关系。

- 反向解析：解析音频得到量化乐理特征 → 匹配知识库，输出感性描述；

- 正向生成：用户输入感性词语（宏大、悲伤克制、温柔治愈）→ LLM 查询知识库，输出结构化乐理参数，交给 Magenta 生成旋律 / 装饰音。

### 8\.2 映射样例（知识库示例）

|感性标签|对应的乐理量化特征|
|---|---|
|悲伤但克制|自然小调 / 和声小调；慢速 70\~80；旋律起伏小；装饰音少（少量倚音）；和弦多用小三、减和弦；避免大跳音程|
|宏大史诗|大调；中速偏慢；宽音域；附点节奏；较多八度、大跨度和弦；装饰音密度中等；长音持续铺垫|
|轻快灵动|大调；速度 110\~140；十六分音符多；短音为主；少量短装饰音（颤音、短倚音）|

### 8\.3 知识库实现方式

纯文本 \+ JSON 规则库，本地存放，可人工编辑扩充；OpenCode 本地 LLM 读取知识库做推理，**完全免费，不需要联网训练**。

> MVP 内直接实现该模块，不需要依赖外部网页版模型。
>
>

---

## 9\. Listen1 \+ yt\-dlp 音源模块集成方案（Python 实现，同 Listen1 后端语言）

### 9\.1 开源项目信息

1. Listen1：[https://github\.com/listen1/listen1](https://github.com/listen1/listen1)，**原版后端 Python\+Tornado**，MIT 协议，可直接复用平台爬虫、搜索、解析播放链接、歌词的代码。

2. yt\-dlp：[https://github\.com/yt\-dlp/yt\-dlp](https://github.com/yt-dlp/yt-dlp)，Unlicense 协议，Python 包，调用 ffmpeg 提取音频。

### 9\.2 模块职责边界

- listen1\\\[\_service\.py\]\(\_service\.py\)：复用 Listen1 源码，搜索歌曲，拿到试听 playUrl；**只做搜索解析，不做下载**

- ytdlp\\\[\_service\.py\]\(\_service\.py\)：接收 playUrl，调用 yt\-dlp 下载流并转码 WAV

- cache\\\[\_manager\.py\]\(\_manager\.py\)：限制缓存总大小，自动删除超过 N 天的音频，防止磁盘占满

### 9\.3 Python 最小代码示例

> backend\_python/src/services/music\_source/listen1\\\[\_service\.py\]\(\_service\.py\)
>
>

```python
import requests
import json


class Listen1Service:
    def __init__(self):
        self.session = requests.Session()

    async def search(self, keyword: str, platform: str):
        # 复用Listen1原版平台解析逻辑，搜索歌曲
        payload = {"keyword": keyword, "platform": platform}
        resp = self.session.post("http://127.0.0.1:8888/api/search", json=payload)
        return resp.json()
```

> backend\_python/src/services/music\_source/ytdlp\\\[\_service\.py\]\(\_service\.py\)
>
>

```python
import yt_dlp


def download_audio(url: str, output_path: str):
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "outtmpl": output_path,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return True
```

### 9\.4 容错策略

1. playUrl 失效：自动切换下一个平台源重试；

2. 请求限流：增加请求间隔，防止平台封禁 IP；

3. 下载失败：返回错误信息，前端提示用户更换音源平台。

---

## 10\. 产品迭代路线

> 电子琴硬件接入放在最后迭代，严格遵守规划。
>
>

### 🟢 MVP（V0\.9，核心免费本地版本）

- Listen1 Python 后端多平台搜歌，获取播放链接、歌词

- yt\-dlp 本地下载音频，转码 WAV，本地缓存自动清理

- 音频转 MIDI，生成钢琴乐谱，解析旋律走向、和弦、装饰音

- 审美双向映射模块（感性词 ↔ 乐理知识库）

- 可选高级 API 路由开关，外接 DeepSeek 等音乐 AI 增值能力

- 前端乐谱预览、版权提示弹窗

> 限制：yt\-dlp 需要 Python 环境；无视频音轨提取；无写歌 / 练歌；无电子琴接入。
>
>

### 🟡 V1\.0\.1（小迭代，优化音源链路）

- 音源链接失效自动切换平台源

- 缓存策略优化，自定义缓存上限

- 音频预处理：降噪、音量归一化，提升制谱精度

- 新增视频文件提取音轨功能

### 🟡 V1\.1（练歌模块，电子琴之前）

- 歌词同步播放、歌词高亮

- 开源人声分离模型，分离伴奏与人声

- 练歌打分、音高检测

### 🟡 V1\.2（写歌 / 编曲模块）

- 基于审美知识库 \+ Magenta 生成多乐器 MIDI

- 多乐器乐谱生成，不止钢琴

- AI 辅助编曲、装饰音自动生成

### 🔵 V1\.3（最终迭代：电子琴硬件接入）

> MVP 完全不碰这个模块
>
>

- MIDI 协议对接电子琴

- 音频链路：软件播放人声，电子琴输出伴奏，耳机监听

- MIDI 实时输入识别，实时乐谱反馈

---

## 11\. 实施文档（Python 本地部署）

### 11\.1 环境准备

1. Python 3\.10\+

2. 安装依赖：`pip install -r requirements.txt`

3. 安装 ffmpeg（yt\-dlp 音频转码依赖）

4. OpenCode 环境配置，可直接调用本项目 Python 脚本

### 11\.2 开发步骤

1. 搭建 Tornado 项目目录，移植 Listen1 的平台解析代码

2. 开发 listen1\_service，测试多平台歌曲搜索

3. 开发 ytdlp\_service，测试音频下载转码

4. 开发缓存管理模块

5. 音频解析模块：Librosa \+ Magenta，音频转 MIDI，提取旋律 / 装饰音

6. 审美知识库开发，实现双向映射

7. AI 路由模块：本地 LLM / 外部 API 切换

8. 前端页面开发，对接后端 HTTP 接口

9. 单元测试：搜索、下载、音频解析、审美映射

10. 本地内测，修复链接失效、转码失败问题

### 11\.3 测试清单

- 多平台歌曲搜索测试

- 播放链接失效自动切换平台源

- yt\-dlp 下载 \+ wav 转码

- 音频解析，提取旋律、和弦、装饰音

- 审美标签双向映射测试

- 缓存自动清理

### 11\.4 打包（后续迭代）

可使用 PyInstaller 打包 Python 项目，生成独立 exe，免去用户安装 Python 环境。

---

## 12\. 风险与约束

1. **版权风险（最高优先级）**
所有音频仅用于个人学习研究；UI 强制弹窗版权提示；禁止批量下载、分发、商用；音频全部保存在本地，不上传到云端。

2. **音源链接不稳定**
各大音乐平台会更新鉴权，Listen1 本身已经停止维护；我们基于其 Python 代码做适配，多平台兜底切换。

3. 平台反爬：增加请求间隔，限流，避免 IP 封禁。

4. AI 制谱误差：音频转 MIDI 存在识别错误，UI 提示 AI 生成乐谱仅作为参考。

5. 硬件：MVP 不依赖任何外部硬件；电子琴 MIDI 接入放到最后版本迭代。

```Plain Text
如果你需要，我可以单独把 `requirements.txt` 一次性写好，直接给你复制进OpenCode。
```

> （注：部分内容由豆包工作 AI 生成）
