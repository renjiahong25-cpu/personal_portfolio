# 真实项目操作录像

将实际产品运行录屏剪辑为 mp4 或 webm，和对应海报一起放入本目录，再编辑 `manifest.json`：

```json
{"version":1,"videos":[{"id":"piano-real-demo","project":"PIANO","title":"真实操作影片标题","description":"影片中实际展示的功能和操作。","src":"media/piano-demo.mp4","poster":"media/piano-poster.jpg","duration":"01:23"}]}
```

`project` 为 `DOCK`、`PIANO`、`HUB`、`JOURNEY`、`TARIFF`、`BUILD` 之一。`src` 和可选 `poster` 必须为站内相对路径。没有真实录像时保留空清单；网站不会用预设结果模拟产品运行。
