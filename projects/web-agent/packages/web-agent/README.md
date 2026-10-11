# Web Agent CLI

**Language:** English | [简体中文](README.zh-CN.md)

Web Agent CLI lets local agents control the Web Agent browser extension through a local daemon. The package also runs an OpenAI-compatible model gateway on the same daemon.

## Install

Install the CLI from npm:

```bash
npm install -g @afumu/web-agent
web-agent doctor
```

Or install from a source checkout of the [personal_portfolio](https://github.com/renjiahong25-cpu/personal_portfolio) repository (this package lives in `web-agent/packages/web-agent`):

```bash
cd personal_portfolio/web-agent
npm install -g ./packages/web-agent
web-agent doctor
```

## Install the Agent Skill

The package ships a `web-agent-control` skill for local agents. The skill teaches an agent how to start the daemon, check the browser extension connection, create Web Agent chats, add temporary roles, post tasks, wait for replies, and recover from common local-control errors.

Install from the npm package with the standard skills installer:

```bash
npx skills add afumu/web-agent --skill web-agent-control
```

From a source checkout, install from the `web-agent` repository directory:

```bash
cd personal_portfolio/web-agent
npx skills add . --skill web-agent-control
```

The skills installer will ask which agent, scope, and install method to use.

Restart the agent session after installing the skill, then verify the local bridge:

```bash
web-agent daemon start
web-agent doctor
```

## Start the Local Daemon

```bash
web-agent daemon start
web-agent doctor
```

- `daemon start` is idempotent — it returns `alreadyRunning` if the daemon is up.
- The daemon listens on `127.0.0.1:19305` only.
- On first start it generates a control token and stores it in `~/.web-agent/control-token`. All `/command` and `/v1` requests require `Authorization: Bearer <token>`.
- If `doctor` reports `extension.connected: false`, open the Web Agent extension page and enable **Local agent control** in settings.

Other commands: `web-agent daemon status|stop|restart|logs`, `web-agent chat ...`, `web-agent run create-and-post --file task.json --wait`, and `web-agent doctor`.

## Model Gateway (OpenAI-compatible)

The daemon exposes a small OpenAI-compatible API so local agents and other clients can use the extension's DeepSeek / Doubao web sessions as models:

| Endpoint | Description |
| --- | --- |
| `GET /v1/models` | Lists `deepseek` and `doubao` |
| `POST /v1/chat/completions` | Forwards the request to the matching web session and streams the answer back |

Both require the same `Authorization: Bearer <control-token>` header.

Example opencode provider config (`~/.config/opencode/opencode.json`):

```json
{
  "provider": {
    "web-agent": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://127.0.0.1:19305/v1",
        "setCacheKey": false,
        "apiKey": "<paste the content of ~/.web-agent/control-token>"
      },
      "models": {
        "deepseek": { "name": "DeepSeek (Web Agent)" },
        "doubao":   { "name": "Doubao (Web Agent)" }
      }
    }
  }
}
```

## Development Install

From the `web-agent` repository directory:

```bash
npm install -g ./packages/web-agent
# or
npm link ./packages/web-agent
```

For development, install the local skill from the repository root:

```bash
npx skills add . --skill web-agent-control
```

## Publish Checklist

Before publishing a new CLI release, bump `packages/web-agent/package.json`; npm does not allow republishing an existing version.

```bash
cd packages/web-agent
npm version patch --no-git-tag-version
npm pack --dry-run
npm publish --dry-run --access public
npm login
npm whoami
npm publish --access public
```

For a beta release:

```bash
npm publish --tag beta --access public
```
