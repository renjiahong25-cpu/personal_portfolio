---
name: web-agent-control
description: 当智能体需要通过本机 web-agent 控制 Web Agent：创建 AI 群聊、添加临时角色、发布任务、等待回复、读取结果，或继续已有群聊时使用。
---

# Web Agent CLI 本机控制

## 概览

Web Agent 通过已安装的 `web-agent` 命令和本地 daemon 控制。不要直接点击浏览器界面，也不要绕过本地控制协议去调用 Chrome 扩展内部 API。

当任务需要多个 AI 角色在 Web Agent 里讨论、评审、批判、头脑风暴、比较方案，或分别给出独立观点时，使用这个 skill。

## 本机命令要求

默认直接调用已安装到本机 PATH 里的 `web-agent`。如果命令不存在，请提示用户先从 npm 安装：

```bash
npm install -g @afumu/web-agent
```

开发期可以在 Web Agent 仓库里执行：

```bash
npm install -g ./packages/web-agent
```

或者使用软链接安装：

```bash
npm link ./packages/web-agent
```

## 先启动并检查连接

任何操作前先运行：

```bash
web-agent daemon start
web-agent doctor
```

`daemon start` 是幂等命令。如果 daemon 已经运行，它只会返回 `alreadyRunning`，不会重复启动后台进程。

如果结果里 `extension.connected` 是 `false`：

1. 请用户打开 Web Agent 插件页面。
2. 确认设置里已开启 `本机智能体控制`。
3. 再运行一次 `doctor`。

如果 daemon 状态异常，优先运行：

```bash
web-agent daemon restart
web-agent doctor
```

## 推荐的新任务流程

新任务优先使用“一次性创建群聊、添加角色、发布任务、等待回复”的流程。这样最适合外部 Agent 或 CLI 调用。

先创建一个 JSON 文件，例如 `task.json`：

```json
{
  "chat": {
    "name": "方案评审",
    "mode": "independent"
  },
  "roles": [
    {
      "source": "temporary",
      "name": "工程师",
      "systemPrompt": "从工程实现、复杂度、风险和测试角度评估。"
    },
    {
      "source": "temporary",
      "name": "产品经理",
      "systemPrompt": "从用户价值、范围控制和交付节奏角度评估。"
    }
  ],
  "task": {
    "target": "all",
    "content": "请评估这个方案，给出风险、建议和最终判断。"
  },
  "options": {
    "waitForReplies": true,
    "activateChat": true,
    "webAgentPage": true
  }
}
```

然后运行：

```bash
web-agent run create-and-post --file task.json --wait
```

向用户汇报时，优先按角色保留观点差异，不要把所有回复压平成一段通用总结。

## 临时角色

如果人员库里没有现成模板，不要要求用户先手动创建模板。直接在任务 JSON 里使用临时角色：

```json
{
  "source": "temporary",
  "name": "研究员",
  "description": "负责查漏补缺和事实核对。",
  "chatSite": "deepseek",
  "systemPrompt": "你是研究员，负责核对事实、找出假设漏洞，并列出需要补充的信息。"
}
```

临时角色只属于当前群聊，不会写入人员库。`chatSite` 可用值：`deepseek`、`doubao`、`chatgpt`、`gemini`、`claude`、`grok`。

如果模拟真实公众人物、公司创始人或专家风格，必须在 `systemPrompt` 里明确：

- 这是公开思想风格的模拟角色。
- 不是真人本人。
- 不代表其公司或组织发言。
- 不声称拥有私人经历、实时信息或内部信息。

## 继续已有群聊

当用户给出 `chatId`，或希望继续之前的 Web Agent 群聊时，使用已有群聊流程：

```bash
web-agent task post --chat <chatId> --target all --content "继续追问的问题"
web-agent task wait --chat <chatId> --message <messageId> --timeout 300000
web-agent task read --chat <chatId> --message <messageId>
```

如果 `task post` 的 CLI 进程迟迟不返回，但 `chat get` 已经能看到用户消息和角色回复，说明群聊执行链路可能已经完成。此时用 `task read` 按消息 ID 读取结果，并检查 daemon 是否有悬挂请求：

```bash
web-agent daemon status
```

如果 `pending` 长时间不为 `0`，重启 daemon 清理悬挂状态：

```bash
web-agent daemon restart
```

## 停止任务

用户希望立即停止当前群聊正在回复中的任务时使用。停止已 `pending`/`sent`（未收到回复）的角色，未开始的角色不再投递；若未指定 `--message`，则回退停止该群聊中所有正在 `thinking` 的角色：

```bash
web-agent task stop --chat <chatId> --message <messageId> [--roles roleA,roleB]
web-agent task stop --chat <chatId>
```

返回 `{ chatId, messageId, targetRoleIds, stoppedRoleIds, failedRoleIds, roles }`。失败的 `roleIds` 会放入 `failedRoleIds`，不影响其他角色停止。停止后需要确认回复状态时用 `task read`。

## 常用命令

```bash
web-agent daemon start
web-agent doctor
web-agent daemon status
web-agent daemon restart
web-agent chat list
web-agent chat get <chatId>
web-agent chat create --name "群聊名" --mode independent
web-agent chat activate --chat <chatId>
web-agent chat initialize --chat <chatId>
web-agent role list --chat <chatId>
web-agent role batch-add --chat <chatId> --file roles.json
web-agent task post --chat <chatId> --target all --content "任务"
web-agent task wait --chat <chatId> --message <messageId> --timeout 300000
web-agent task read --chat <chatId> --message <messageId>
web-agent task stop --chat <chatId> --message <messageId>
```

`roles.json` 可以是数组：

```json
[
  {
    "source": "temporary",
    "name": "研究员",
    "systemPrompt": "负责查漏补缺和事实核对。"
  }
]
```

## 错误恢复

优先看机器可读的 `error.code`。

| 错误码 | 处理方式 |
| --- | --- |
| `extension_not_connected` | 请用户打开 Web Agent 插件页面，确认已开启本机智能体控制，然后重跑 `doctor`。 |
| `control_disabled` | 请用户在 Web Agent 设置里开启 `本机智能体控制`。 |
| `role_not_ready` | 运行 `chat initialize --chat <chatId>`，或请用户等待角色 iframe 初始化完成。 |
| `task_timeout` | 运行 `task read`，汇报已收到的部分回复和仍在等待的角色。 |
| `permission_denied` | 不要盲目重试。本地 token 或 daemon 状态可能异常，先运行 `daemon restart`。 |
| `fetch failed` | 先运行 `doctor` 和 `daemon status`。如果 daemon 退出或插件断开，重启 daemon 并重新打开 Web Agent 页面。 |

## 操作规则

- 新任务优先使用 `run create-and-post --file ... --wait`。
- 多角色、长提示词或复杂任务必须使用 JSON 文件承载输入。
- 不要把密钥、token、cookie 或用户隐私信息写进任务提示词。
- 本地 daemon 只能监听 `127.0.0.1`，不要暴露到公网或局域网。
- 除非用户明确希望浏览器会话里的 AI 角色行动，否则不要替用户向 Web Agent 发布任务。
- 汇报结果时说明哪些角色已回复、哪些角色仍在等待、是否超时。
