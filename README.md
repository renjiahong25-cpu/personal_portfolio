# 跨境物流规则智能 Agent

Cross-Border Logistics Rules Intelligent Agent

面向跨境物流/报关场景的一体化智能中台：**政策法规问答（RAG）**、**HS 商品名称模糊搜索与关税报价**、**政策爬虫采集**、**知识库管理**、**离线评测** 五大模块。后端 FastAPI 微服务化（api / policy / quote / tariff 多角色），前端 Vue3 + Vite，向量检索 Milvus + BM25 混合。

## 功能一览

| 模块 | 说明 |
| --- | --- |
| 政策问答 `/api/chat` | 多语言 → 实体识别 → HyDE 假设文档 → 子问题分解 → 多阶段检索（向量+BM25+融合）→ Rerank → 引用分段流式输出（LLM 思考/回答双流，Markdown + Mermaid 流程图）→ 事实核查 |
| 关税报价 `/api/tariff` | 商品名模糊搜索/智能搜索、HS 编码预测（LLM + 本地分类器）、关税税率（MFN/对等税率）、关税计算、并给出报关合规建议与风险提示 |
| 政策采集 `/api/spider` | AI 爬虫（playwright 采集 + 翻译 + AI 修复）、变更检测、每日巡检调度、parquet 转档与知识库入库 |
| 知识库 `/api/doc` | 文档上传（PDF/DOCX/HTML/Markdown）→ 章节/段落解析 → 切块 → 向量化（Embedding）→ 入 Milvus；BM25 索引重建；文档树管理 |
| 评测 `/api/eval` | 评测集生成、回归评测（metric）、badcase 管理、报告生成 |

## 技术栈

- **后端**: Python 3.11 · FastAPI · SQLAlchemy 2 · uvicorn（可多 worker）
- **模型服务**: OpenAI 兼容 `/v1/chat/completions`（Qwen 系，`--reasoning`），本地 Embed（`MiniLM-L12-v2`，384 维）+ Rerank 微服务
- **存储**: MySQL 8.4 · Redis · MinIO（附件/快照）· Milvus（向量 或 本地 MilvusLite）
- **检索**: Milvus（COSINE/AUTOINDEX）+ jieba BM25 混合召回 + RRF 融合
- **前端**: Vue3 · Pinia · Vue Router · ECharts · Playwright（UI 测试）
- **部署**: Docker Compose（MySQL/Redis/MinIO/Milvus/embed/quote×N/policy/tariff/nginx）

## 目录结构

```
api/                  路由层（chat / doc / spider / eval / tariff）
core/                 LLM 客户端、向量库客户端、BM25、分布式锁
service/              业务服务
  chat_service/       问答管线（实体识别/HyDE/分解/多路检索/事实核查/流式）
  tariff_service/     关税报价（商品名模糊搜索/HS/税率/报价引擎）
  data_service/       文档处理、切块、向量入库
  spider_service/     政策采集与巡检
  embed_service/      Embedding / Rerank 微服务入口
  eval_service/       评测
config/               settings + 日志配置（敏感值全部走 .env）
db/models/            SQLAlchemy 模型
schemas/              Pydantic 请求/响应模型
deploy/               Docker Compose / Dockerfile / nginx / mysql-init
frontend/             Vue3 前端（Chat / Knowledge / Tariff / Quote / Eval 视图）
main.py               服务入口（按 SERVICE_ROLE 挂载对应域路由）
tests/                后端单测      frontend/tests/ui/  Playwright UI 测试
```

## 环境要求

- Python ≥ 3.11（建议 conda/venv 独立环境）
- Node.js ≥ 18（前端）
- MySQL 8.x（本机或 Docker）
- 向量库二选一：
  - **Milvus Server**（`MILVUS_DB_URI=http://<host>:19530`）
  - **Milvus Lite**（默认 `logs/milvus.db`，零额外依赖）
- 可选：Redis、MinIO（不配时走进程内降级）
- OpenAI 兼容的 LLM 服务（如 Qwen3 带 `--reasoning`）与 Embedding 模型

## 快速开始

### 1. 环境配置

```bash
cp .env.example .env
```

按需修改 `.env`（关键项）：

```ini
# LLM（OpenAI 兼容接口）
LLM_BASE_URL=http://127.0.0.1:8000/v1     # 或你的远程推理服务
LLM_MODEL_NAME=Qwen3.8-27B

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=cross_border_agent

# 向量库：本地 MilvusLite 文件 or 远程 Milvus server
MILVUS_DB_URI=logs/milvus.db
# MILVUS_DB_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=customs_rule_v1
```

### 2. 安装依赖

```bash
# 后端
pip install -r requirements.txt

# 前端
cd frontend && npm install
```

Embedding/Rerank 模型首次使用会从 HuggingFace 下载（`paraphrase-multilingual-MiniLM-L12-v2`、`cross-encoder/ms-marco-MiniLM-L-6-v2`）；离线环境需预置模型到本地缓存。

### 3. 初始化数据库

启动时自动建表（`init_db()`）。首次部署先建库：

```sql
CREATE DATABASE IF NOT EXISTS cross_border_agent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> docker compose 方式启动会自动完成建库（见 deploy/mysql-init）。

### 4. 启动（二选一）

#### 方案一：一键脚本（推荐，同时起前后端 + 有头浏览器）

```bash
# Windows
start_dev.bat                # 起后端(:8000)+前端(:5173)，等就绪后用"有头浏览器"打开页面
start_dev.bat --demo         # 起前后端 + 跑"有头 Playwright"对话演示（可见浏览器窗口）

# Linux / macOS
./start_dev.sh               # 同上
./start_dev.sh --demo        # 同上，有头 Playwright 演示
```

脚本会：① 同时启动后端与前端（两个独立窗口，日志可见）→ ② 轮询 `/health` 与前端直到就绪 → ③ 用有头浏览器打开 `http://127.0.0.1:5173`（`--demo` 时改为运行有头 Playwright）。
`PW_HOLD=1800000 start_dev.bat --demo` 可在演示结束后保持浏览器打开 30 分钟便于手动查看。

#### 方案二：Docker Compose 全栈（零本地依赖）

```bash
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
# 访问 http://127.0.0.1:8080（nginx 反向代理，自动完成建库/建表）
```

> 全栈含 MySQL / Redis / MinIO / Milvus / embed 微服务 / 多角色后端 / nginx。
> `quote` 域可水平扩容：`docker compose -f deploy/docker-compose.yml up -d --scale srv-quote=3`。

#### 方案三：conda / venv 自配（开发调试）

```bash
# 后端（工作目录=项目根）
python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 前端（另开终端）→ http://127.0.0.1:5173
cd frontend && npm run dev
```

> 独立 Embedding/Rerank 微服务（多副本共享时）：
> `python -m uvicorn service.embed_service.embed_server:app --host 0.0.0.0 --port 8600`，并在 `.env` 设置 `EMBED_HTTP_URL=http://127.0.0.1:8600`（不设置则进程内加载模型）。

### 5. 使用

浏览器访问前端（方案一/三 见 5173；方案二 见 nginx 映射端口 8080）。

- **Chat（政策问答）**：输入问题（支持中英），实时流式展示 LLM 思考与分段回答，回答末尾附 Mermaid 流程图与引用来源。
- **Knowledge（知识库）**：上传政策文档（PDF/DOCX/HTML/MD），查看文档树、重新向量化/重建 BM25 索引。
- **Tariff / Quote（关税）**：输入商品名称（支持模糊/中英混合），返回候选 HS 编码、适用税率与关税金额；可发起完整报价流程并查看路由。
- **Eval（评测）**：跑回归评测、看指标与 badcase。

**常用 API 示例**

```bash
# 政策问答（SSE 流式）
curl -N -X POST http://127.0.0.1:8000/api/chat/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"德国ATLAS进口清关需要准备哪些文件？","session_id":"demo"}'

# 文档列表
curl http://127.0.0.1:8000/api/doc/list

# 上传文档建库
curl -X POST http://127.0.0.1:8000/api/doc/upload \
  -F "file=@policy.pdf" -F "title=示例政策"

# 商品名模糊搜索
curl -G http://127.0.0.1:8000/api/tariff/search \
  --data-urlencode "keyword=不锈钢阀门"

# 关税报价
curl -X POST http://127.0.0.1:8000/api/tariff/quote \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"不锈钢焊管","country":"US","qty":100,"unit":"piece"}'
```

### 6. 测试

```bash
# 后端：ruff + 单元测试 + 编译检查（失败即退出非 0）
./run_ci.sh          # Linux/部署机
run_ci.bat           # Windows

# 前端 UI 测试（默认 headless，BASE_URL 指向运行中的服务）
cd frontend
BASE_URL=http://127.0.0.1:8080 npx playwright test --config playwright.config.js

# 有头（可见浏览器窗口）演示 —— 观察完整问答过程
BASE_URL=http://127.0.0.1:8080 npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
# 演示结束后保持浏览器打开 30 分钟便于手动查看：
PW_HOLD=1800000 BASE_URL=http://127.0.0.1:8080 npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
```

## 部署说明

生产多角色横向部署（`deploy/docker-compose.yml`）：

- 每个服务按 `SERVICE_ROLE` 只挂载单一域路由（`api`/`quote`/`policy`/`tariff`），`quote` 可 `--scale srv-quote=3` 水平扩容
- nginx 分流：`/api/chat|doc|spider|eval` → policy；`/api/tariff|/health` → quote；静态资源 → 前端 dist
- 关键环境变量通过 `.env` 注入（不要提交真实口令到仓库）

> 说明：上传前已对整个仓库做脱敏——真实口令 / 内网地址 / PRD / 过程文档 / 日志 / 探针脚本一律不入库（见 `.gitignore`）。

## 相关设计要点

- **Qwen3 `--reasoning` 模式**：LLM 先思考再作答，`max_tokens` 需同时覆盖思考与正文预算（管线按 32768 设置，避免正文被思考腰斩）。
- **混合检索**：向量（COSINE、AUTOINDEX）+ BM25 双路召回，RRF 融合后 Rerank，保证德国等外语长文本政策命中率。
- **流式双通道**：`thinking`（LLM 思考片段，灰色）与 `answer`（正文，Markdown 渲染）双流输出，适配长回答与流程图。

## License

私有项目，未授权请勿用于商业分发。