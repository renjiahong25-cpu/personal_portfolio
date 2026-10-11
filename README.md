# STARHARBOR · rjh-ghost's Archipelago

星港 · rjh-ghost 的作品群岛

## 定位 / About

STARHARBOR 是一个个人作品集：一张等距群岛地图（isometric archipelago map）把五个作品连成一片群岛——每个项目是一座岛，站点本身是海图。
STARHARBOR is a personal portfolio: an isometric archipelago map ties five works into one archipelago — each project is an island, and the site itself is the chart.

## 目录结构 / Layout

| 目录 / Directory | 说明 / Description |
| --- | --- |
| `site/` | 作品集站点源码（构建产物部署到 GitHub Pages）/ portfolio site source (deployed to GitHub Pages) |
| `projects/` | 各作品项目 / the works |
| `tools/` | 构建工具 / build tooling |

## 作品 / Projects

| 项目 / Project | 说明 / Description |
| --- | --- |
| [supplychainpolicyagent](projects/supplychainpolicyagent/) | 跨境关税与政策智能体（政策问答 RAG / 关税报价 / 政策采集 / 知识库 / 评测）/ Cross-border tariff & policy agent |
| [venue-scout](projects/venue-scout/) | 团建场地智能规划（场地采集 + 攻略级行程规划）/ Team-building venue scouting & itinerary planning |
| [web-agent](projects/web-agent/) | 多 AI 网页会话协作工作台 / Multi-AI web session collaboration workbench |
| pianoflow | 本地 AI 音乐工作台（待同步入库 / pending sync）/ Local AI music workbench |

## 约定 / Conventions

- 每个子项目独立维护自己的 README、依赖与构建脚本，子项目之间互不依赖。
  Each project keeps its own README, dependencies, and build scripts; projects do not depend on each other.
- 各子项目的敏感值一律走各自的 `.env`（已被各自 `.gitignore` 排除，永不入库）。
  Per-project secrets live in each project's own `.env` (excluded by its `.gitignore`, never committed).
