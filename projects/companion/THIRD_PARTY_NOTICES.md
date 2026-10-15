# 第三方声明（THIRD_PARTY_NOTICES）

C03 新增 VRM 渲染链路涉及的第三方组件。核实日期 2026-10-15；未核实项明确标注。

## VRM 模型（C03 新增条目）

- `VRM1_Constraint_Twist_Sample.vrm` - VRM1_Constraint_Twist_Sample，作者 (c) 2022 pixiv Inc.。许可：VRM Public License 1.0（https://vrm.dev/licenses/1.0/）。来源：https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm。SHA256 `12c2b97e95e700783a6a550dc0eee2d7880aeedccef9ae67bc4c5a2f0f2631a2`，10776032 字节。VRM 1.0，含 SpringBone / Constraints / Expressions。文件内嵌 meta 明确 `allowRedistribution: true` 与 `allowModificationRedistribution`（逐字段原文见 `assets/models/LICENSE-NOTE.md`）。该文件不进 git，按上述来源获取并校验哈希。

## 前端渲染依赖（packages/companion-pet-web，版本以 package-lock.json 为准）

- `three@0.180.0` — MIT（npm registry 元数据核实）
- `@pixiv/three-vrm@3.5.5` — MIT（npm registry 元数据核实；仓库 LICENSE 原文 Copyright (c) 2019-2026 pixiv Inc.，MIT，2026-10-15 抓取）
- `vite@7.3.7`（仅开发期构建工具，不随桌宠运行时分发） — MIT

## 运行时框架

- PySide6 6.12.0 — LGPL-3.0-only / GPL-3.0-only 双许可（docs/03 §3.2 已核实 pyside-setup LICENSES 目录）。
- QtWebEngine / Chromium（PySide6-Addons 内的 Qt WebEngine 模块）— 随 Qt 发行；其具体再分发条款本轮**未核实**（docs/03 §12 列为未知项），发布打包前需补核。

## 明确未使用

- HAST-SEC 的任何代码或模型（docs/03 §4.5 的诚实表述适用于本仓库当前状态）。
- `Seed-san` 等许可未核实的 VRM sample（docs/03 卡 1 红线）。
