# 岛屿音乐清单

默认关闭。每岛可以选择网易云官方外部播放器，或有明确站点使用授权的本地音频文件。点击音乐按钮后才载入外部播放器；默认不会向网易云发起播放器请求。小游戏、放映厅或浏览器后台会移除官方播放器并暂停本地音频。

编辑 `manifest.json`，最多配置首页与六座岛，每处一首，以下两种来源二选一。

官方外部播放器（请核对真实歌曲 ID）：

```json
{"version":1,"tracks":[{"island":"PIANO","title":"核实后的曲目名称","artist":"黄霄云","provider":"netease","songId":"123456"}]}
```

网站只构造 `https://music.163.com/outchain/player?type=2&id=歌曲ID&auto=0&height=66` 官方播放器，不下载或复制歌曲。访客需要在官方播放器中点按播放；能否播放取决于歌曲版权、账号和浏览器设置。网站不能读取跨站播放器的实际播放状态，不会把已打开播放器描述为本站音频已播放。切换岛屿会切换曲目，仍保持 `auto=0`。

有明确公开站点使用授权的本地音频：

```json
{"version":1,"tracks":[{"island":"PIANO","title":"曲目名称","artist":"黄霄云","provider":"file","src":"audio/piano.mp3","authorized":true}]}
```

支持首页 `HOME`（《盲选》）及岛屿 `DOCK`、`PIANO`、`HUB`、`JOURNEY`、`TARIFF`、`BUILD`；本地文件支持 mp3、m4a、ogg、wav。`src` 必须为本目录的站内相对路径，禁止远程 URL 或上级目录。设置 `authorized:true` 前应核对公开站点所需的录音及作品权利，个人喜欢、私人下载或试听不表示取得公开传播授权。外部播放器和本地文件字段不能混填。没有音源时保留空清单，界面会明确显示等待接入。

## 发布版音频说明

本目录（GitHub 发布版）内 7 首曲目为 **192 kbps MP3**（libmp3lame 转码），非原始 WAV。源文件为开发工作区 `D:\qi-workspace\site\public\audio` 下的本地 WAV；其中母版（*-master）部分本身即为有损来源转码而来，源信息见 `D:\qi-workspace\work\m02-SOURCES.md`。发布版 `manifest.json` 与本地版字段完全一致，仅 `src` 由 `.wav` 改为 `.mp3`。每曲的网易云官方链接（`officialSongId` / `officialUrl` / `officialPlayerUrl`）仍保留在 manifest 中。WAV 母版不随本仓库发布。
