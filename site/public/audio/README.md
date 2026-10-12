# 背景音轨

把背景音乐 mp3 文件放到本目录（例如 `track.mp3`），然后修改 `src/data/siteConfig.ts` 的两个字段：

- `trackSrc`：mp3 的相对路径，如 `"audio/track.mp3"`。`public/` 目录会原样拷贝到 dist 根，配合 `base: './'` 用相对路径引用即可。
- `trackTitle`：曲目名（展示/无障碍用途），无则保持 `null`。

`trackSrc` 非空时，App.vue 会自动挂载一个 `loop` 音频元素；播放触发（如用户首次点击后 autoplay）由后续任务接入。mp3/wav 文件已被仓库根 `.gitignore` 排除，不入库。
