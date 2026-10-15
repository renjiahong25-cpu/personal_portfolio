# VRM 模型许可核实记录（C03）

核实日期：2026-10-15。核实人：C03 执行代理。本目录整体不进 git（见仓库根 `.gitignore`）。

## 本目录唯一内置模型

| 项 | 值 |
|---|---|
| 文件 | `VRM1_Constraint_Twist_Sample.vrm` |
| 大小 | 10,776,032 字节（与 docs/03 §3.4 记录的 three-vrm 仓库 API 值一致） |
| SHA256 | `12c2b97e95e700783a6a550dc0eee2d7880aeedccef9ae67bc4c5a2f0f2631a2` |
| 来源 | https://raw.githubusercontent.com/pixiv/three-vrm/dev/packages/three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm |
| 完整性 | 2026-10-15 从上述 source_url 重新下载，SHA256 与本地文件**完全一致**（大小一致） |
| 版本 | VRM 1.0（文件内 `VRMC_vrm.specVersion = "1.0"`） |

## 证据 1：模型文件内嵌许可元数据（最权威，直接从文件解析）

用 `app/pet3d/vrm_loader.py` 读取本文件 GLB JSON 块得到的 `extensions.VRMC_vrm.meta` 原文关键字段：

```json
{
  "name": "VRM1_Constraint_Twist_Sample",
  "authors": ["pixiv Inc."],
  "copyrightInformation": "(c) 2022 pixiv Inc.",
  "licenseUrl": "https://vrm.dev/licenses/1.0/",
  "version": "v1.0.1",
  "allowRedistribution": true,
  "modification": "allowModificationRedistribution",
  "commercialUsage": "corporation",
  "avatarPermission": "everyone",
  "creditNotation": "unnecessary"
}
```

即：**作者明确允许再分发（allowRedistribution=true），且允许修改后再分发
（allowModificationRedistribution）**，许可为 VRM Public License 1.0。桌宠把它作为
渲染目标文件使用并原样保留，属于许可范围内。

## 证据 2：VRM Public License 1.0 文本可达

- https://vrm.dev/licenses/1.0/ （HTTP 200，日文原文「VRMパブリック・ライセンス文書 1.0」）
- https://vrm.dev/en/licenses/1.0/ （HTTP 200，英文原文，2026-10-15 实际抓取）

英文版 §1 定义摘录（License Settings / Redistribution）：

> "**License Settings**" means the part of the Licensed Work Data that contains the
> Licensor's designation of the subject and terms of the license under this Public
> License. The License Setting constitutes, together with this Public License, the
> terms and conditions of the license of the Licensed Work Data granted to You.

> "**Redistribution**" means to provide a VRM file to the public by any means or
> process that requires permission under the Licensed Rights, such as reproduction,
> public display, public performance, distribution, dissemination, communication, or
> importation, and to make a VRM file available to the public including in ways that
> members of the public may access the material from a place and at a time
> individually chosen by them. For the avoidance of doubt, reproduction or otherwise
> using the Licensed Work Data to the extent necessary for Model Use or Avatar Use
> (if applicable) of the Licensed Work Data does not constitute Redistribution.

（注意：桌宠渲染该模型属于 Model Use 范畴；随附文件分发属于 Redistribution，但已被上面
meta 的 allowRedistribution=true 明确允许。）

## 证据 3：three-vrm 仓库 LICENSE（代码，MIT 原文摘录）

来源 https://raw.githubusercontent.com/pixiv/three-vrm/dev/LICENSE （2026-10-15 抓取）：

> Copyright (c) 2019-2026 pixiv Inc.
>
> MIT License
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of
> this software and associated documentation files (the "Software"), to deal in the
> Software without restriction, including without limitation the rights to use, copy,
> modify, merge, publish, distribute, sublicense, and/or sell copies of the Software …

该 MIT 许可覆盖 three-vrm 仓库代码（含前端依赖 `@pixiv/three-vrm@3.5.5`、示例资产所在
仓库）。

## 红线（docs/03 §3.4 / 卡 1 禁止事项）

- 本目录**只允许**登记在 `app/pet3d/vrm_loader.py` REGISTRY 且许可核实过的模型。
- `Seed-san`（vrm-c samples）**许可未核实，不得使用**。
- 任何用户自行导入的模型，其许可由用户自行负责；程序只渲染用户明确放置的文件。
