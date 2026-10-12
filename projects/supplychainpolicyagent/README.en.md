# Cross-Border Logistics Rules Agent

An all-in-one intelligent platform for cross-border logistics / customs scenarios: **policy & regulation Q&A (RAG)**, **fuzzy HS-commodity search & tariff quoting**, **policy web-crawling**, **knowledge-base management**, and **offline evaluation**. Backend is a FastAPI micro-service layout (api / policy / quote / tariff roles), frontend is Vue3 + Vite, retrieval is a Milvus + BM25 hybrid.

## Features

| Module | Description |
| --- | --- |
| Policy Q&A `/api/chat` | Multilingual input → entity extraction → HyDE hypothetical document → sub-question decomposition → multi-stage retrieval (vector + BM25 + fusion) → Rerank → cited streaming output (dual LLM thinking/answer streams, Markdown + Mermaid flowcharts) → fact checking |
| Tariff quoting `/api/tariff` | Fuzzy / smart commodity-name search, HS-code prediction (LLM + local classifier), duty rates (MFN / reciprocal), tariff calculation, customs-compliance advice and risk tips |
| Policy crawling `/api/spider` | AI crawler (playwright collection + translation + AI self-repair), change detection, daily patrol scheduler, parquet conversion and KB ingestion |
| Knowledge base `/api/doc` | Document upload (PDF/DOCX/HTML/Markdown) → chapter/paragraph parsing → chunking → embedding → Milvus; BM25 index rebuild; document tree management |
| Evaluation `/api/eval` | Eval-set generation, regression testing (metrics), badcase management, report generation |

## Tech Stack

- **Backend**: Python 3.11 · FastAPI · SQLAlchemy 2 · uvicorn (multi-worker capable)
- **Model serving**: OpenAI-compatible `/v1/chat/completions` (Qwen family, `--reasoning`), local Embed (`MiniLM-L12-v2`, 384-dim) + Rerank microservice
- **Storage**: MySQL 8.4 · Redis · MinIO (attachments/snapshots, via compose) · Milvus (server or local MilvusLite)
- **Retrieval**: Milvus (COSINE/AUTOINDEX) + jieba BM25 hybrid recall + weighted fusion + CrossEncoder rerank
- **Frontend**: Vue3 · Pinia · Vue Router · ECharts · Playwright (UI tests)
- **Deployment**: Docker Compose (MySQL/Redis/MinIO/Milvus/embed/quote×N/policy/tariff/nginx)

## Directory Layout

```
api/                  route layer (chat / doc / spider / eval / tariff)
core/                 LLM client, vector-store client, BM25, distributed lock
service/              business services
  chat_service/       Q&A pipeline (entity / HyDE / decomposition / multi-path retrieval / fact check / streaming)
  tariff_service/     tariff quoting (commodity search / HS / rates / quote engine)
  data_service/       document processing, chunking, vector ingestion
  spider_service/     policy crawling & patrol
  embed_service/      Embedding / Rerank microservice entry
  eval_service/       offline evaluation
config/               settings + logging config (secrets all via .env)
db/models/            SQLAlchemy models
schemas/              Pydantic request/response models
deploy/               Docker Compose / Dockerfile / nginx / mysql-init
frontend/             Vue3 frontend (Chat / Knowledge / Tariff / Quote / Eval views)
main.py               service entry (mounts role-specific routes via SERVICE_ROLE)
tests/                backend unit tests      frontend/tests/ui/  Playwright UI tests
scripts/              one-off probe / ops scripts (connection info via env vars)
```

## Requirements

- Python ≥ 3.11 (dedicated conda/venv environment recommended)
- Node.js ≥ 18 (frontend)
- MySQL 8.x (local or Docker)
- Vector store, pick one:
  - **Milvus Server** (`MILVUS_DB_URI=http://<host>:19530`)
  - **Milvus Lite** (default `logs/milvus.db`, zero extra dependencies)
- Optional: Redis, MinIO (in-process degradation when not configured)
- An OpenAI-compatible LLM service (e.g. Qwen3 with `--reasoning`) and the embedding models

## Quick Start

### 1. Environment configuration

```bash
cp .env.example .env
```

Adjust `.env` as needed (key items):

```ini
# LLM (OpenAI-compatible endpoint)
LLM_BASE_URL=http://127.0.0.1:8000/v1     # or your remote inference service
LLM_MODEL_NAME=Qwen3.8-27B

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=cross_border_agent

# Vector store: local MilvusLite file or remote Milvus server
MILVUS_DB_URI=logs/milvus.db
# MILVUS_DB_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=customs_rule_v1
```

#### Switching to a cloud LLM API (optional: Doubao / DeepSeek / OpenAI)

All LLM calls go through an OpenAI-compatible endpoint; switching to the cloud only requires editing `.env`, no code changes. Default is `LLM_PROVIDER=local` (local / self-hosted llama.cpp); for cloud, add or modify the 4 lines below in `.env`:

```ini
LLM_PROVIDER=deepseek          # or doubao / openai
LLM_BASE_URL=...               # see table below
LLM_MODEL_NAME=...             # see table below
LLM_API_KEY=sk-...             # cloud key (can stay empty for local)
```

| Vendor | LLM_PROVIDER | LLM_BASE_URL | LLM_MODEL_NAME |
| --- | --- | --- | --- |
| DeepSeek | `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat` (or `deepseek-reasoner`) |
| Doubao (Volcano Ark) | `doubao` | `https://ark.cn-beijing.volces.com/api/v3` | endpoint ID `ep-xxxx` (or Ark model name) |
| OpenAI | `openai` | `https://api.openai.com/v1` | `gpt-4o` (or `gpt-4o-mini`) |

Two optional switches (defaults work, tune as needed):

```ini
# Reasoning effort: empty = keep server default. Self-hosted llama.cpp may set
# minimal|low|medium|high|xhigh; OpenAI reasoning models (o3/gpt-5) low|medium|high;
# leave empty for non-reasoning models (gpt-4o / DeepSeek / Doubao).
LLM_REASONING_EFFORT=

# Cloud output budget cap (only when LLM_PROVIDER != local):
# DeepSeek/Doubao 8192, OpenAI gpt-4o 16384; default 8192 is usually fine.
LLM_CLOUD_MAX_TOKENS=8192
```

Notes:

- Embedding / Rerank (384-dim MiniLM + ms-marco CrossEncoder) are always served by this project's built-in model service, independent of the LLM choice.
- Cloud API latency and billing follow the vendor; the Q&A main pipeline makes multiple LLM calls per conversation (entity extraction → retrieval gating → streaming generation → fact checking).
- Vendor endpoints / model names follow official docs; a model rename only requires changing `LLM_MODEL_NAME`.

### 2. Install dependencies

```bash
# Backend
pip install -r requirements.txt

# Frontend
cd frontend && npm install
```

Embedding/Rerank models are downloaded from HuggingFace on first use (`paraphrase-multilingual-MiniLM-L12-v2`, `cross-encoder/ms-marco-MiniLM-L-6-v2`); offline environments need the models pre-cached locally.

### 3. Initialize the database

Tables are created automatically at startup (`init_db()`). Create the database first for a fresh deployment:

```sql
CREATE DATABASE IF NOT EXISTS cross_border_agent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> Starting via docker compose handles database creation automatically (see deploy/mysql-init).

### 4. Run (pick one)

#### Option A: one-click script (recommended, starts frontend + backend + headed browser)

```bash
# Windows
start_dev.bat                # backend(:8000) + frontend(:5173), then opens the page in a headed browser
start_dev.bat --demo         # frontend/backend + headed Playwright chat demo (visible browser window)

# Linux / macOS
./start_dev.sh               # same as above
./start_dev.sh --demo        # same, headed Playwright demo
```

The script: ① starts backend and frontend (two windows, logs visible) → ② polls `/health` and the frontend until ready → ③ opens `http://127.0.0.1:5173` in a headed browser (`--demo` runs a headed Playwright chat instead).
`PW_HOLD=1800000 start_dev.bat --demo` keeps the browser open for 30 minutes after the demo.

#### Option B: full Docker Compose stack (zero local dependencies)

```bash
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
# visit http://127.0.0.1:8080 (nginx reverse proxy; DB/table init is automatic)
```

> The full stack includes MySQL / Redis / MinIO / Milvus / embed microservice / multi-role backends / nginx.
> The `quote` domain scales horizontally: `docker compose -f deploy/docker-compose.yml up -d --scale srv-quote=3`.

#### Option C: conda / venv (development)

```bash
# backend (working directory = project root)
python -m uvicorn main:app --host 127.0.0.1 --port 8000

# frontend (separate terminal) → http://127.0.0.1:5173
cd frontend && npm run dev
```

> Standalone Embedding/Rerank microservice (for multi-replica sharing):
> `python -m uvicorn service.embed_service.embed_server:app --host 0.0.0.0 --port 8600`, and set `EMBED_HTTP_URL=http://127.0.0.1:8600` in `.env` (without it, models load in-process).

### 5. Usage

Open the frontend in a browser (Option A/C: port 5173; Option B: nginx port 8080).

- **Chat (policy Q&A)**: type a question (Chinese/English), see the LLM's thinking and the answer stream in real time; answers end with a Mermaid flowchart and cited sources.
- **Knowledge**: upload policy documents (PDF/DOCX/HTML/MD), browse the document tree, re-embed / rebuild BM25 index.
- **Tariff / Quote**: enter a commodity name (fuzzy / mixed language), get candidate HS codes, applicable rates and duty amounts; run a full quote flow and inspect routes.
- **Eval**: run regression evaluation, view metrics and badcases.

**Common API examples**

```bash
# Policy Q&A (SSE streaming)
curl -N -X POST http://127.0.0.1:8000/api/chat/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which documents are needed for ATLAS import clearance in Germany?","session_id":"demo"}'

# Document list
curl http://127.0.0.1:8000/api/doc/list

# Upload a document into the KB
curl -X POST http://127.0.0.1:8000/api/doc/upload \
  -F "file=@policy.pdf" -F "title=Sample policy"

# Fuzzy commodity search
curl -G http://127.0.0.1:8000/api/tariff/search \
  --data-urlencode "keyword=stainless steel valve"

# Tariff quote
curl -X POST http://127.0.0.1:8000/api/tariff/quote \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"stainless steel welded pipe","country":"US","qty":100,"unit":"piece"}'
```

### 6. Tests

```bash
# Backend: ruff + unit tests + compile check (non-zero exit on failure)
./run_ci.sh          # Linux / deployment machine
run_ci.bat           # Windows

# Frontend UI tests (headless by default; BASE_URL points at a running service)
cd frontend
BASE_URL=http://127.0.0.1:8080 npx playwright test --config playwright.config.js

# Headed demo (visible browser window) — watch the full Q&A process
BASE_URL=http://127.0.0.1:8080 npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
# Keep the browser open for 30 minutes after the demo:
PW_HOLD=1800000 BASE_URL=http://127.0.0.1:8080 npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
```

CI: `.github/workflows/ci.yml` runs lint (ruff + unit tests + compile) and the frontend build (`npm ci` + `npm run build`) on every push/PR. Playwright UI tests require live services and are manual-only: `.github/workflows/ui-tests.yml` (`workflow_dispatch`).

## Deployment

Production multi-role horizontal deployment (`deploy/docker-compose.yml`):

- Each service mounts only its role's routes via `SERVICE_ROLE` (`api`/`quote`/`policy`/`tariff`); `quote` can be scaled with `--scale srv-quote=3`
- nginx routing: `/api/chat|doc|spider|eval` → policy; `/api/tariff|/health` → quote; static assets → frontend dist
- Sensitive env values are injected via `.env` (never commit real credentials to the repo)

> Note: the repo contains no real credentials, intranet addresses, PRDs, local runtime data (`data/downloads`, eval raw/reports) or logs (see `.gitignore`); the `data/` whitelist keeps only 3 small safe fixtures (offline HS seed, derived route-cost model, human-labeled golden eval set). Connection info in `scripts/` is read from environment variables (`MYSQL_PASSWORD`, `LLM_API_KEY`, `SCP_PYTHON`, …) — no real secrets.

## Design Highlights

- **Qwen3 `--reasoning` mode**: the LLM thinks before answering, so `max_tokens` must cover both the reasoning and the answer budget (the pipeline sets 32768 to avoid the answer being truncated by the chain of thought).
- **Hybrid retrieval**: dual-path recall (COSINE vectors + BM25), weighted fusion then CrossEncoder rerank, keeping recall high for long-form foreign-language (German) policy texts.
- **Dual streaming channels**: `thinking` (LLM reasoning fragments, gray) and `answer` (main text, Markdown rendering) streams, suited to long answers and flowcharts.

## Known Issues

- **Milvus Lite file-lock concurrency limit**: Milvus Lite uses a single-file, single-process lock. In dev mode with multiple uvicorn workers (or several processes sharing the same `logs/milvus.db`), concurrent Milvus initializations race for the lock, so some retrieval paths get zero vector hits and degrade to BM25-only (the main pipeline is not interrupted and evaluation still runs). Use a Milvus Server instance in production (`MILVUS_DB_URI=http://<host>:19530`), which is not subject to the file lock.

## License

Private project. Do not redistribute commercially without permission.
