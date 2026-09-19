# Contributing to Web Agent

Thank you for your interest in contributing! This guide helps you get started.

## Local development

```bash
# Install dependencies
npm install

# Build in watch mode (for development)
npm run dev

# Type check
npm run typecheck

# Run tests
npm test

# Full verification (typecheck + test + build)
npm run verify
```

Load the built extension in Chrome:
1. Go to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select the `dist/` directory

## Issue claim process

Before starting work, comment on the issue to let the maintainer know you're taking it. This avoids duplicate effort. Wait for acknowledgement before opening a PR on substantial changes.

## Pull request guidelines

- Open one PR per issue.
- Reference the issue number in your PR body (`Fixes #123`).
- Include test results and a description of what you changed.
- Keep changes focused — avoid unrelated cleanups in the same PR.
- Do **not** paste real API keys, tokens, or credentials anywhere in the PR.

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml) and fill in all required fields:
- Steps to reproduce
- Expected vs actual behavior
- Browser version, OS, and which AI sites are open
- Screenshots or console logs if available

## Security

Do not file public issues for security vulnerabilities. Contact the maintainer directly.
