# 音源可用性实测报告（2026-10-04）

> 这份报告记录**真实网络探测**的结果，而不是文档推测。
> 结论直接推翻了原技术栈文档里「复用 Listen1 Python 后端」和「用 Magenta」两条关键选型。

## 1. 结论速览

| 原文档选型 | 实测结论 | 处置 |
|---|---|---|
| 复用 Listen1 的 Python 平台解析代码 | ❌ 代码是 Python 2，2019 年停更，4 个平台里 3 个已死 | **废弃**，只借鉴其 track schema 与 provider 抽象 |
| Magenta 做音频转 MIDI | ❌ 原版是 TF1，已停维 | 二期改用 **basic-pitch（ONNX）** |
| 多平台搜索（网易云/QQ/酷狗/咪咕/B站） | ⚠️ 只有**免签名搜索**活着，下载全需逆向签名 | 一期只做 **Bilibili**；其余平台抽象预留，二期走 Node sidecar |
| YouTube 作为音源 | ❌ 本机出口被网络层封锁 | 不支持 |

---

## 2. Listen1 为什么不行

仓库：`https://github.com/listen1/listen1`（MIT）。以 tarball 方式取得快照（`codeload.github.com` 可达，`github.com:443` 被封）。

### 2.1 代码形态

- master 最后一次提交 **2019-05-26**，内容是改 README；代码树停在 **2016–2017 年的桌面版架构**。
- **没有 `package.json`**，没有 `api/` 目录，没有 Vue；前端是 AngularJS 1.x + jQuery + soundmanager2(Flash)。
- 服务端确实是 Python + Tornado（`listen1/app.py`、`handlers/base.py`），但它是 **PyInstaller 打包的 macOS 托盘程序**：
  `trayshell/launcher.py` 起 PySide → `shell_pyside.py` 在子线程里跑 Tornado IOLoop，监听 8888 端口。
  **它不是可部署的后端服务。**
- 服务端总量约 1900 行；`requirements/{common,production,dev}.txt` 三个文件内容完全一样，只有 `tornado` 和 `pyaes`，无版本约束。

### 2.2 平台解析器（`listen1/replay/`）

| 模块 | 目标接口 | 死因 |
|---|---|---|
| `netease.py` | `music.163.com/api/*` + weapi | 老接口下线；weapi 需 AES+RSA 签名，`csrf` 为空、无 cookie |
| `qq.py` | `i.y.qq.com/*.fcg`、`cc.stream.qqmusic.qq.com` | `guid`/`g_tk` 全部硬编码（无签名计算），CDN 域名已下线 |
| `xiami.py` | `api.xiami.com/web` | **虾米音乐 2018 关停**，整模块作废 |
| `douban.py` | `douban.fm/j/*` | 需图形验证码登录，token/ck 明文存 `media/user/douban_userinfo.json`，cookie 是 2015 年的 |

### 2.3 六层硬阻塞

1. **Python 2 专属语法**：整个 `replay/` 是 `urllib2`、`xrange`、`.iteritems()`、`text.encode('hex')`。在 Python 3.11 里 import 即 SyntaxError。
2. **顶层平铺 import**：`from settings import ...`、`from urls import url_patterns` 依赖 `sys.path` 含 `listen1/`，作为库导入会污染全局命名空间（`settings` 尤其危险）。
3. **同步阻塞**：`replay/replay.py:67` `urllib2.urlopen()` 无超时无重试，直接调用会卡死 Tornado IOLoop。
4. **加密依赖**：`pyaes` 是 Py2 时代包；`_create_secret_key()` 在 Py3 下会**静默生成错误密钥**（比报错更危险）。
5. **cookie / 登录态**：桌面单机状态模型，无法搬到多用户 Web 后端。
6. **版本冲突**：`handlers/trackfile.py:73` 用 `@tornado.web.asynchronous`（Tornado 4 写法，Tornado 6 已移除），而 requirements 不锁版本。

> **唯一值得借鉴的**：`_convert_song()` 的 track schema（平台前缀 id + 显式 `source`），以及 provider 统一接口
> `filetype / search_track / list_playlist / get_playlist / get_artist / get_album / get_url_by_id` 这个抽象约定。
> 我们丢弃了它的 `url` 字段——播放链接会过期，正确的设计是「稳定 source_id + 每次重新解析」。

---

## 3. 活体网络探测（第一轮）

关键词「茉莉花」，逐个端点实测：

| 途径 | HTTP | 关键字段 | 结论 |
|---|---|---|---|
| yt-dlp `bilisearch3:`（Bilibili） | — | 3 条真实结果（44s~22min） | ✅ 可用 |
| 拿到直链 → ffmpeg 转 wav | — | 3.37 MB / 20.0s / `format_name=wav` | ✅ 0.71s 完成 |
| yt-dlp `ytsearch`（YouTube） | — | 0 条 | ❌ `WinError 10061` 连接被拒（**本机网络层封锁**，非代码问题） |
| 网易云 legacy 免签名搜索 `api/search/get/web` | 200 | `result.songs` 有 5 条 | ✅ 搜索层活着 |
| 酷狗 `songsearch.kugou.com/song_search_v2` | 200 | `data.lists` 有 5 条，`total=480` | ✅ 搜索层活着 |
| 网易云 weapi `weapi/search/get` 空 body | 200 | **空 body** | ⚠️ 必须自己实现 AES+RSA |
| QQ `c.y.qq.com/soso/fcgi-bin/client_search_cp` | 500 | 空 body（0 字节） | ❌ 彻底死，重试无意义 |
| Bilibili 裸 httpx 搜索 | 200 | 返回**人机校验 HTML** | ⚠️ 被风控（但 yt-dlp 能过） |

**第一轮结论**：「用户输入歌名 → 拿到可下载音频」这条主链路，靠 yt-dlp + Bilibili 当天就能跑通，不需要任何签名、不维护任何 cookie。

---

## 4. 三个必须记录的坑

### 4.1 `extract_flat` 在 B 站搜索上不可用

yt-dlp 的 `extract_flat: True` 对 `bilisearch` 只返回裸 `id`，**`title` / `duration` / `thumbnails` 全为空**：

```
COUNT 5
116352241112065 |  |dur None | None
115491133726261 |  |dur None | None
...
```

我们的第一版就踩了这个坑——前端把「无标题」的结果全过滤掉，表现为**搜得到但什么都不显示**。
去掉 flat 后（5 条约 5.9s）才有真实标题、时长、封面。

### 4.2 B 站风控：连续搜索会返回 HTTP 412

连着探测几次之后，搜索接口直接返回：

```
ERROR: ...: Unable to download JSON metadata: HTTP Error 412: Precondition Failed
```

`412` 是 B 站风控码。**必须**配套三件事：请求节流、`buvid` 设备标识、把 412 转成可读提示而不是 500。

### 4.3 `bilisearch` 相关性很差

同一时刻搜「星辰大海」返回的是：一条标题为哈希串的视频、零跑 B05 汽车评测、LG 电视评测、国际象棋。
**这不是代码问题**，是 yt-dlp 的 B 站搜索路径本身质量不足。

---

## 5. 最终方案：社区标准的 B 站搜索客户端

参考对象：`yt-dlp` 的 `BiliBiliSearchIE`、`bilibili-API-collect`（社区逆向文档）、`bbdown` / `biliup` / `TikTokDownloader`。

### 5.1 yt-dlp 自己是怎么做的（源码事实）

`yt_dlp/extractor/bilibili.py` 的 `BiliBiliSearchIE._search_results`：

```python
if not self._get_cookies("https://api.bilibili.com").get("buvid3"):
    self._set_cookie(".bilibili.com", "buvid3", f"{uuid.uuid4()}infoc")  # 假设备号
"https://api.bilibili.com/x/web-interface/search/type"  # 搜索不走 WBI 签名
```

关键点：
- **搜索接口不需要 WBI 签名**（WBI 只用于 `playurl` / `view/detail` / `space`）。
- yt-dlp 只是塞了一个**假的** `buvid3`，而社区做法是先取**真的** `buvid3` + `buvid4`。
- 它对 `search/type` 的结果逐条再做完整解析（每条约 1 秒），并且 flat 模式下元数据为空。

### 5.2 我们的实现

```
1) GET https://api.bilibili.com/x/frontend/finger/spi   → 真 buvid3 + buvid4
2) 写成 Netscape cookie 文件持久化（后续搜索与 yt-dlp 下载共用）
3) GET https://api.bilibili.com/x/web-interface/search/type
   参数：search_type=video, keyword, page=1, duration=0, tids=0
   请求头：桌面 UA + Referer/Origin = bilibili.com
```

实测（修复 412 之后）：

```
CODE 0 MSG OK
COUNT 20
- BV1qp4y1u7Jq | [星辰大海</em | 3:29 | 知己音乐 | pic: True | play: 672287
- BV19vaB6yEFw | 黄霄雲《[星辰大海 | 3:30 | 经典果冻音乐 | pic: True | play: 1538
- BV1CD39zMEpm | 【无损音质】黄霄雲-星辰大海 | 3:28 | 栖止之时 | pic: True | play: 47472
...
```

**一次请求**就拿到卡片需要的全部字段（标题 / 封面 / 时长 / UP 主 / 播放量），比 yt-dlp 逐条解析快一个数量级，风控暴露面也小得多。

### 5.3 封面防盗链

B 站图床校验 `Referer`，非 bilibili.com 来源返回 403（实测：`Referer: http://localhost:5173/` → 403）。
因此封面走后端代理 `GET /api/media/cover?url=...`，带正确 Referer 取图再转发，并做 `*.hdslb.com` 白名单校验防 SSRF。

---

## 6. 环境事实（本机）

| 项 | 值 |
|---|---|
| conda | `D:\miniconda3`（`C:\Users\14837\miniconda3` 是指向它的 junction） |
| 项目环境 | `D:\conda_envs\pianoflow`（Python 3.11.17） |
| ffmpeg | `D:\miniconda3\envs\ffmpeg-gpl\Library\bin\ffmpeg.exe`（需显式配置，不在 PATH） |
| Node | v24.15.0（npm 需用 `npm.cmd`） |
| qodercli | 1.1.65（`C:\Users\14837\AppData\Roaming\npm\qodercli.cmd`） |
| GitHub | `github.com:443` 被封，`codeload.github.com` 可达 → 用 tarball 取源码 |

---

## 7. 后续扩平台的建议

今天还能维护的中文音乐平台解析**几乎全是 JS/Node**：

| 项目 | 状态 | 语言 | 许可 |
|---|---|---|---|
| `NeteaseCloudMusicApi`（原版） | GitHub 2024-04 已归档只读，npm 仍发版 | Node | MIT |
| `NeteaseCloudMusicApiEnhanced/api-enhanced` | 活跃（2026-09 更新） | Node | MIT |
| `qqmusic-api-python` | 活跃（2026-08 发布） | Python | **GPLv3+（传染）** |
| `lx-music` 音源脚本 | 活跃 | JS 脚本 | — |

eapi/weapi 逆向维护成本高，Python 侧只剩 QQ 一家小维护者。
**建议**：需要扩平台时，在本机起一个 Node 小服务当 sidecar（ncm api-enhanced，端口 8899），
Python 后端用 `httpx` 调它。源码层已预留 `Extractor` 抽象，插入新平台不需要改业务代码。
