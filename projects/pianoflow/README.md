# PianoFlow · AI 音乐工作台

本地运行的开源音乐工作台。核心链路：**搜歌 → 下载音频 → 缓存 → （二期）转谱与乐理解析**。

> ⚠️ 版权声明：本项目仅供个人学习与音乐研究使用。下载的音频只保存在本机缓存目录，
> 禁止批量下载、二次分发、转载或商用，使用者自行承担版权责任。
> UI 首次打开会强制弹出该声明。

当前进度：**第一期（音源链路）已完成**。二期（AI 转谱 + 审美映射）进行中。

---

## 快速开始

### 前置

| 依赖 | 版本 / 位置 |
|---|---|
| conda | `D:\miniconda3`（项目环境 `D:\conda_envs\pianoflow`，Python 3.11.17） |
| ffmpeg | `D:\miniconda3\envs\ffmpeg-gpl\Library\bin`（**不在 PATH**，需配置） |
| Node.js | v20+（实测 v24.15.0） |

### 启动（两个窗口，各保持不关）

```powershell
powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\start-backend.ps1"
```
```powershell
powershell -ExecutionPolicy Bypass -File "D:\Program Files\pianoflow\scripts\start-frontend.ps1"
```

打开 **http://127.0.0.1:5173** （注意是 `127.0.0.1`：Vite 显式绑定 IPv4，默认只监听 `[::1]` 会导致连不上）。

生产模式（后端直接托管构建产物，只需后端一个进程）：

```powershell
cd "D:\Program Files\pianoflow\frontend"; & "C:\Program Files\nodejs\npm.cmd" run build
# 然后只启动后端，访问 http://127.0.0.1:8890
```

### 测试

```powershell
cd "D:\Program Files\pianoflow\backend_python"
& "D:\conda_envs\pianoflow\python.exe" -m pytest
```

---

## 架构

```
浏览器 (Vue 3 + Vite)
   │  /api/*            ← 开发期由 Vite 代理到 8890
   ▼
Tornado 后端 (127.0.0.1:8890)
   ├── core/           配置、错误类型、日志、节流
   ├── tasks/          任务状态机 + SSE 广播（含多路复用会话流）
   ├── services/
   │   ├── music_source/   Extractor 抽象 · Bilibili 客户端 · yt-dlp 下载封装
   │   ├── download/       下载 + 转码流水线（进度、队列、合规限速）
   │   └── cache/          内容寻址音频缓存（TTL + LRU + 容量上限）
   └── api/            handlers / routes
```

关键设计：

- **Extractor 抽象**：平台差异全部收敛在 `Extractor` 实现里，业务代码只认 `Track`。
  将来接网易云/QQ（Node sidecar）不需要改业务代码。
- **搜索不用 yt-dlp**：B 站搜索走自研客户端（`spi` 取真 buvid → 一次 `search/type`），
  原因见 `docs/spike-2026-10-04-source-availability.md`。下载仍用 yt-dlp（它已实现签名的 `playurl`）。
- **yt-dlp 永不在 event loop 上运行**：统一 `asyncio.to_thread`。
- **全页面单条事件流**：`GET /api/events`，避免逐任务开流耗尽浏览器连接数。
- **内容寻址缓存**：文件名是 `sha1(source:sourceId:format)`，天然去重且不含空格/中文。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 后端、音源可用性、ffmpeg 是否解析到 |
| GET | `/api/events` | **SSE**，一次连接推送所有任务状态变化 |
| POST | `/api/music/search` | `{keyword, platform?, limit?}` → 预览卡片数据 |
| POST | `/api/music/download-audio` | `{source, sourceId, format, title?}` → `202` + `taskId` |
| GET | `/api/music/download-audio/<task_id>` | 任务快照（轮询兜底用） |
| GET | `/api/music/download-audio/stream?task_id=` | 单任务 SSE（兼容/调试用） |
| GET | `/api/music/download-audio/queue` | 当前下载队列快照 |
| GET | `/api/media/cover?url=` | 封面代理（B 站防盗链，带 SSRF 白名单） |
| GET | `/api/cache/status` | 缓存条目与占用 |
| GET | `/api/cache/audio/<key>` | 播放缓存音频（支持 Range，可拖动进度） |
| POST | `/api/cache/clean` | `{mode: "cleanup" \| "clear"}` |

---

## 配置（全部通过环境变量，前缀 `PIANOFLOW_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PIANOFLOW_HOST` / `PIANOFLOW_PORT` | `127.0.0.1` / `8890` | 监听地址 |
| `PIANOFLOW_CACHE_DIR` | `<repo>/cache/audio` | 音频缓存目录 |
| `PIANOFLOW_CACHE_MAX_BYTES` | `2147483648`（2 GiB） | 缓存容量上限，超出按 LRU 淘汰 |
| `PIANOFLOW_CACHE_TTL_DAYS` | `7` | 缓存过期天数 |
| `PIANOFLOW_FFMPEG_LOCATION` | `D:\miniconda3\envs\ffmpeg-gpl\Library\bin` | ffmpeg 目录（不在 PATH，必须显式指定） |
| `PIANOFLOW_SEARCH_MIN_INTERVAL_SECONDS` | `2.0` | 两次搜索最小间隔（B 站风控） |
| `PIANOFLOW_DOWNLOAD_MIN_INTERVAL_SECONDS` | `5.0` | 两次下载最小间隔（合规限速） |
| `PIANOFLOW_DOWNLOAD_MAX_CONCURRENCY` | `1` | 并发下载数（保持 1：串行限速） |
| `PIANOFLOW_MAX_AUDIO_SECONDS` | `3600` | 单曲时长上限，超过拒绝下载 |
| `PIANOFLOW_BILIBILI_COOKIE_FILE` | `<repo>/data/cookies/bilibili.txt` | 自动维护的 buvid cookie jar |
| `PIANOFLOW_YTDLP_COOKIE_FILE` | 空 | 可选：浏览器导出的 Netscape cookie（登录态） |

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

- 项目根路径**含空格**（`D:\Program Files\pianoflow`）：一律用 `pathlib`，禁止字符串拼路径。
- 每完成一个模块，跑一次 `qodercli` 非交互审查，critical/high 必须清零。
- 仓库级认知索引由 `aoci` 维护（`aoci.txt` + `.aoci/`，纯离线 + 人工策展）。
- 开发流程与硬性约定见 `AGENTS.md`；全局环境事实见 `C:\Users\14837\.config\opencode\AGENTS.md`。

## 文档

- `docs/spike-2026-10-04-source-availability.md` —— **音源可用性实测报告**（Listen1 为何废弃、平台实测数据、B 站风控与解法）
- `PianoFlow AI音乐工作台技术栈结论文档.md` —— 原始技术栈文档（顶部已加实测修正区块）
- `AGENTS.md` —— 仓库治理与项目约定（aoci 托管）
