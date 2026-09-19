# Web Agent Desktop Client

`web_agent_ui` 是 Web Agent 的可选桌面客户端（Flutter，当前平台：Windows）。它连接本机 daemon（`127.0.0.1:19305`），把群聊工作台做成原生窗口，方便长期挂任务时不必一直开着浏览器。

## 前置条件

- [Flutter SDK](https://docs.flutter.dev/get-started/install/windows)（Windows 桌面开发支持）
- 已构建的 Web Agent 扩展（`npm run build` 生成 `dist/`）
- Node.js ≥ 18（运行 daemon）
- （可选）Chrome：配合无头浏览器后台保持 AI 站点会话

## 构建

```bash
cd packages/web-agent-ui
flutter pub get
flutter build windows --release
```

产物：`build/windows/x64/runner/Release/web_agent_ui.exe`。

## 运行

客户端依赖本地 daemon 和（可选的）opencode serve：

1. daemon：`web-agent daemon start`（幂等），监听 `127.0.0.1:19305`。
2. opencode（可选）：`opencode serve`，监听 `127.0.0.1:4096`。
3. 无头 Chrome（可选）：以独立 profile 加载 `dist/` 扩展并打开 DeepSeek 等站点，保持网页会话在线。
4. 启动客户端：`build/windows/x64/runner/Release/web_agent_ui.exe`。

`tools/start-ui.ps1` 是一个 Windows 启动助手，会按顺序检查/拉起 daemon、opencode serve、无头 Chrome，再启动客户端（需要按本机环境调整脚本顶部的路径变量）。

## 测试

```bash
flutter test
flutter analyze
```

## 说明

- 桌面客户端只做本机连接展示与操作，不引入任何云端服务。
- 群聊数据仍保存在浏览器本地存储（扩展侧）；客户端通过 daemon 读取。
