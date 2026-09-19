# personal_portfolio

个人项目集合仓库。每个子项目独立维护自己的 README、依赖与构建脚本，子项目之间互不依赖。

## 子项目

| 子项目 | 目录 | 说明 |
| --- | --- | --- |
| 跨境物流规则智能 Agent | [supplychainpolicyagent/](supplychainpolicyagent/) | 面向跨境物流/报关场景的智能中台：政策问答（RAG）、HS 商品模糊搜索与关税报价、政策爬虫采集、知识库管理、离线评测 |
| Web Agent | [web-agent/](web-agent/) | 基于 openteam 二次开发的多 AI 站点协作 Chrome 扩展 + 本机智能体控制 CLI + OpenAI 兼容模型网关 |

## 约定

- 请勿在仓库根目录放置跨项目文件；根目录只保留本 README、`.github/`（CI）与根 `.gitignore`。
- 各子项目的敏感值一律走各自的 `.env`（已被各自 `.gitignore` 排除，永不入库）。
