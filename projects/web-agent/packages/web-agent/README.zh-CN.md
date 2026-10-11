# Web Agent CLI

**语言:** [English](README.md) | 简体中文

Web Agent CLI 让本机智能体可以通过本地 daemon 控制 Web Agent 浏览器扩展；同一个 daemon 还提供 OpenAI 兼容的模型网关。

## 安装

从 npm 安装 CLI：

```bash
npm install -g @afumu/web-agent
web-agent doctor
```

或从 [personal_portfolio](https://github.com/renjiahong25-cpu/personal_portfolio) 仓库的源码安装（本包位于 `web-agent/packages/web-agent`）：

```bash
cd personal_portfolio/web-agent
npm install -g ./packages/web-agent
web-agent doctor
```

## 安装 Agent Skill

这个包内置一个给本机智能体使用的 `web-agent-control` skill。它教会智能体如何启动 daemon、检查浏览器扩展连接、创建 Web Agent 群聊、添加临时角色、发布任务、等待回复，以及处理常见的本机控制错误。

使用标准 skills 安装器从 npm 包安装：

```bash
npx skills add afumu/web-agent --skill web-agent-control
```

从源码安装时，在 `web-agent` 仓库目录下执行：

```bash
cd personal_portfolio/web-agent
npx skills add . --skill web-agent-control
```

skills 安装器会询问要安装到哪个 agent、使用什么范围和安装方式。

安装 skill 后，重新打开智能体会话，然后检查本地连接：

```bash
web-agent daemon start
web-agent doctor
```

## 启动本地 daemon

```bash
web-agent daemon start
web-agent doctor
```

- `daemon start` 幂等——已在运行时只返回 `alreadyRunning`。
- daemon 只监听 `127.0.0.1:19305`。
- 首次启动会生成控制令牌并保存在 `~/.web-agent/control-token`；所有 `/command` 与 `/v1` 请求都必须携带 `Authorization: Bearer <token>`。
- 如果 `doctor` 显示 `extension.connected: false`，请打开 Web Agent 扩展页面并在设置里开启**本机智能体控制**。

其他命令：`web-agent daemon status|stop|restart|logs`、`web-agent chat ...`、`web-agent run create-and-post --file task.json --wait`、`web-agent doctor`。

## 模型网关（OpenAI 兼容）

daemon 同时提供小型 OpenAI 兼容 API，让本机智能体和其他客户端把扩展里的 DeepSeek / 豆包网页会话当模型使用：

| 端点 | 说明 |
| --- | --- |
| `GET /v1/models` | 列出 `deepseek`、`doubao` |
| `POST /v1/chat/completions` | 把请求转发给对应网页会话，并流式返回回答 |

两者都使用同一个 `Authorization: Bearer <控制令牌>` 请求头。

opencode 的 provider 配置示例（`~/.config/opencode/opencode.json`）：

```json
{
  "provider": {
    "web-agent": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://127.0.0.1:19305/v1",
        "setCacheKey": false,
        "apiKey": "<把本机 ~/.web-agent/control-token 文件的内容粘贴到这里>"
      },
      "models": {
        "deepseek": { "name": "DeepSeek (Web Agent)" },
        "doubao":   { "name": "豆包 (Web Agent)" }
      }
    }
  }
}
```

## 开发安装

在 `web-agent` 仓库目录下：

```bash
npm install -g ./packages/web-agent
# 或
npm link ./packages/web-agent
```

开发时，在仓库根目录安装本地 skill：

```bash
npx skills add . --skill web-agent-control
```

## 发布检查

发布新的 CLI 版本前，先更新 `packages/web-agent/package.json` 里的版本号；npm 不允许重复发布已经存在的版本。

```bash
cd packages/web-agent
npm version patch --no-git-tag-version
npm pack --dry-run
npm publish --dry-run --access public
npm login
npm whoami
npm publish --access public
```

发布 beta 版本：

```bash
npm publish --tag beta --access public
```
