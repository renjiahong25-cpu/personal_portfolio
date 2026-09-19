# Repository Guidelines

## Project Structure & Module Organization

Web Agent is a Manifest V3 Chrome extension built with Vite and TypeScript. Core source lives in `src/`: `background/` contains the service worker, routing, runtime, and control handlers; `content/` contains AI-site content scripts and adapters; `group/` contains group state, prompts, roles, and mention parsing; `teamPage/` contains the workspace UI. Shared utilities are in `src/shared/`, and protocol types are in `src/control/`.

Static extension files are in `public/`. Design docs and images are in `docs/`. The local agent CLI package is in `packages/web-agent/`. `dist/` is generated build output; do not edit it directly.

## Environment

- Node.js: `C:\Program Files\nodejs\node.exe`
- npm: `C:\Program Files\nodejs\npm.cmd`
- Always use full paths for npm/node commands. The shell is PowerShell 5.1, not cmd.
- Example: `& "C:\Program Files\nodejs\npm.cmd" test`
- Working directory for project: `D:\Program Files\web-agent`

## Build, Test, and Development Commands

- `npm install`: install dependencies.
- `npm run dev`: watch-build the extension in development mode.
- `npm run build`: create a production extension build in `dist/`.
- `npm run typecheck`: run strict TypeScript checks.
- `npm test`: run Vitest unit tests across `src/` and package tests.
- `npm run verify`: run typecheck, unit tests, and build before a PR.
- `npm run web-agent -- doctor`: run the local CLI.
- `node diagnostics/daemon-e2e.mjs`: start the control daemon on a test port, run checks, shut it down, and exit. Always run this (or an equivalent bounded script) instead of starting the daemon bare.
- `node diagnostics/vitest-e2e.mjs [test files...]`: bounded Vitest runner. Spawns `vitest run` as a child, streams output to console + `vitest-e2e.log`, force-kills the whole process tree on a hard timeout (default 180s, override `VITEST_E2E_TIMEOUT_MS`), sweeps orphan vitest node processes, and ALWAYS exits. Always run this (or an equivalent bounded script) instead of bare `npx vitest` / `npm test`, which can hang forever on Windows.

## Coding Style & Naming Conventions

Use TypeScript ES modules, 2-space indentation, single quotes, and no semicolons, matching existing files. Prefer explicit exported interfaces/types for shared contracts. Keep filenames camelCase for source and tests, with tests named beside code as `*.test.ts`. The project has no separate lint or formatting script; `tsconfig.json` enforces `strict`, `noUnusedLocals`, and `noUnusedParameters`.

## Testing Guidelines

Vitest is the test framework. Add focused unit tests beside changed modules, especially for routing, prompt construction, storage, UI state, and site adapters. Run `npm run verify` before changes that affect runtime behavior or packaging.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, sometimes with Conventional Commit prefixes such as `feat:` or `docs:`. Keep commits scoped and descriptive, for example `feat: add group chat template` or `Fix duplicate orchestration retry handling`.

PRs should include a clear summary, test results, linked issues when relevant, and screenshots or recordings for UI changes. Call out permission, manifest, DNR rule, or AI-site adapter changes explicitly.

## Daemon & Long-Running Processes

- NEVER start the control daemon (`web-agent-daemon.mjs`) or any server/watch process "bare": always launch it through a script that has an end condition, cleans up, and exits — typically `node diagnostics/daemon-e2e.mjs` (spawns the daemon as a child, validates endpoints, calls `POST /shutdown`, force-kills on timeout). Run as a subagent worker while a monitor subagent tails output in parallel. Do not leave a daemon running after a task; logs must never go to a black hole.
- The daemon exposes `POST /shutdown` (Bearer control token) for graceful stop; a 60s global timeout in the e2e script guarantees it cannot hang forever.
- NEVER run Vitest "bare" (`npx vitest`, `npm test`): vitest 1.6.1 can hang in its Windows worker pool with no output and no exit. Always run `node diagnostics/vitest-e2e.mjs <files>` (or an equivalent bounded script with the same start+test+timeout-kill+always-exit shape). The script must own its lifetime — never leave a runner that can block indefinitely.

## Local Control Payload Contract (CLI / daemon)

When driving the browser through the `web-agent` CLI or the daemon control API, payload shapes are validated strictly. Never invent fields — read `packages/web-agent/skills/web-agent-control/SKILL.md` first.

- Every role item in `roles` / `items` MUST carry `source`:
  - `"source": "temporary"` + `"name"` (+ optional `description`, `systemPrompt`, `chatSite`)
  - `"source": "library"` + `"roleTemplateId"` (from the people library)
  - Omitting `source` (or using any other value) fails with `添加人员来源无效`.
- `chatSite` accepts exactly: `deepseek`, `doubao`, `chatgpt`, `gemini`, `claude`, `grok`.
- `roles.batchAdd` reads `items`; `run create-and-post` reads `roles` inside `task.json`. Do not mix the two keys.
- Prefer `web-agent run create-and-post --file task.json --wait` for new tasks and copy the shape from the skill's example verbatim.

## Security & Configuration Tips

Review `public/manifest.json` and `public/web-agent-frame-rules.json` carefully when changing permissions or frame behavior. Do not commit local browser data, generated `test-artifacts/`, or secrets.
