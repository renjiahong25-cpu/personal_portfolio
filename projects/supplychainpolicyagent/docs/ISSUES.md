# 问题追踪与解决方案手册（ISSUES LOG）

> 本项目"跨境物流规则智能 Agent"运行过程中遇到的所有问题的收集与登记。
> 每条记录：问题现象 → 排查过程/尝试 → 根因 → 解决方案 → 验证结果。
> 新增条目统一追加到「3. 问题清单」最新区段，时间升序。

---

## 1. 环境与常用命令

- Python: `D:\conda_envs\cross-border-agent\python.exe`（`.venv` 是空壳，弃用）
- 后端启动：`python -m uvicorn main:app --host 127.0.0.1 --port 8000`（工作目录=项目根）
- 前端：`npm run dev` → vite 5173（`frontend\`）
- 远程 LLM：llama.cpp `http://<llama-cpp-host>:8000`（`.env` 的 `LLM_BASE_URL`），`Qwen3.8-27B`，服务端 `--reasoning on`
  （**共享进程，勿重启**；reasoning 会先占输出预算，`content` 在后）
- MySQL：localhost:3306，user / pass 见 `.env`（`MYSQL_USER` / `MYSQL_PASSWORD`），库 `cross_border_agent`
- 日志：`logs\uvicorn.out.log`（应用 stdout）、`logs\uvicorn.err.log`（uvicorn 启动 INFO/stderr）
- 离线环境：启动含 HF 依赖的进程前设 `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`
- 脚本输出 UTF-8：`$env:PYTHONIOENCODING='utf-8'`
- Milvus Lite 文件：`logs\milvus.db`（**单进程文件锁**）

---

## 2. 运行纪律（防"打转"）

- 主窗口**禁止**前台跑长进程（uvicorn / npm run dev / SSE 探针 / 采集脚本）——bash 会阻塞到 timeout，主窗口"打转"，用户只能手动打断。
- 双 subagent 分工（用户钦定）：
  1. **启动 subagent**：分离后台启动（`Start-Process ... -PassThru -WindowStyle Hidden` + 重定向双日志）+ 立刻返回 PID/health 快照，几秒结束。
  2. **追踪 subagent**：接手实时追踪，用**多次独立短命令**分批 tail（`Start-Sleep 3; Get-Content -Tail 20 <日志>; 探测health`），每批新日志随汇报带回；长脚本（SSE 探针）同样**后台分离启动输出到文件**再分批 tail，直到 `Uvicorn running` + `/health` 200 或探针 `DONE`。
- 两个 subagent **依次启动**（先启动、再追踪），避免根本没起来就追踪造成空转。
- **禁止**在单次 bash 调用内做"打包循环 tail + 等健康"或前台跑探针。
- 主窗口只发短命令（health ping、读日志尾部、重启、确认 subagent 汇报），长任务一律交 subagent。
- 追踪/启动 subagent 返回后，主窗口应立即停下 subagent、开始分析，不继续堆新 subagent。

---

## 3. 问题清单

### 3.0 通用背景（本会话主线任务）

目标：让中文提问"德国ATLAS电子报关流程需要哪些步骤？"能命中德文权威文档
`ATLAS Verfahrensanweisung zum IT-Verfahren ATLAS (Version 17.4)`（zoll.de 来源），
返回中文答案 + 可点击三级溯源树。

- va_atlas 文档 doc_uuid=`085898499f1e44feb11015599da4c511`，166 章 / 204 段，德文原文。
- 检索架构：`retriever.search` = 章节标题 BM25 粗筛(top5, score>0) → 段落级 BM25+向量
  双路召回 → `_fuse`(BM25=0.4 / VECTOR=0.6, threshold=0.65) → rerank(top5)。
- 向量模型：`paraphrase-multilingual-MiniLM-L12-v2`（384 维，支持中德跨语）。

---

### 3.1 【P1】va_atlas 完全检索不到（跨语言检索失效）

**现象**：问"德国ATLAS电子报关流程需要哪些步骤"，sources 全是 VAT 中文文档，va_atlas
一条不中；answer 答复"资料未提供完整步骤"。

**排查/尝试（记录每一步）**：
1. 先用 `scripts\probe_bm25_hit.py` 诊断：total paras=237（va_atlas 204 + 33 其他）；
   query 分词切出中文词；BM25 命中 va_atlas 有 74 段得分>0，但最高仅 **1.46**，
   而 vat_guide 中文段得分 **32.72** → 结论：**BM25 索引包含 va_atlas，问题在词汇鸿沟**
   （中文 query vs 德文正文，字面匹配彻底失效）。
2. 检查向量库：日志 `_fuse | bm25=5 | vector=0` → **va_atlas 在 Milvus 中完全没有向量**。
   查 `scripts\rebuild_va_atlas.py` 日志：重建时向量写入因 `DataDirLockedError`
   （Milvus Lite 单进程锁被当时的另一进程持有）失败，但脚本没终止、静默跳过了向量写入。
3. 结论：跨语言检索依赖向量语义匹配；BM25 无跨语能力；va_atlas 缺向量 = 跨语言检索死的根因。

**根因（定性）**：
- BM25 词表与语言强相关，中德跨语完全失效；
- va_atlas 向量重建失败（Milvus Lite 锁冲突，写向量必须由运行中的后端进程执行）；
- 单进程内并发初始化 Milvus 会再次触发抢锁（见 3.5）。

**解决方案**：
1. 新增 `POST /api/doc/reindex`（`api\doc.py`）：由持有 Milvus 文件锁的后端进程执行
   删向量 + 删 BM25 增量 + `_rebuild_vectors`，解决"写向量必须经后端进程"的锁约束
   （详见 3.2）。
2. 向量补齐后，检索终态 `bm25=5 | vector=3 | fused=6 | final=5`（向量命中生效）。

---

### 3.2 【P1】Milvus Lite 单进程锁 → 向量写入口缺失

**现象**：离线脚本直接写 Milvus 抛 `DataDirLockedError: another process holds the lock on '...\logs\milvus.db'`。

**根因**：Milvus Lite 是单文件单进程锁库；后端进程常驻持锁，任何独立脚本再 open 同一文件必然冲突。

**解决方案**：向量重建必须由**运行中的后端进程**执行，故新增后端 API 端点：
- `POST /api/doc/reindex`（Form `doc_uuid`）→ `delete_by_doc_uuid` + `bm25_index.remove_by_doc_uuid`
  + `_rebuild_vectors`，不动 MySQL 数据。
- 验证：va_atlas 重建成功，日志 `batch_insert total=204`、`写向量库 count=204`、
  `BM25 add_documents added=204 total=204`、`文档重建索引完成 elapsed=25.639s`。

**遗留注意**：此后任何向量写/重建一律走该 API或运行中后端，禁止另起进程直连 Milvus。

---

### 3.3 【P2】Qwen3 `--reasoning on` 输出空 content（max_tokens 预算被思维链吃光）

**现象**：需求拆解 LLM 调用返回 `raw= `（空串）→ `_extract_json_array('')` 返回 None →
静默回退单次检索，多路检索从未触发。

**排查/尝试**：
- 直接诊断脚本 `scripts\probe_decompose_llm.py` 调 Chat：`raw repr: ''`，`raw len: 0`。
- 用 `scripts\probe_qwen_reasoning.py` 验证原始响应结构，确认 Qwen3 服务端 `--reasoning on` 时
  模型把输出配额优先给 `reasoning_content`（思维链），当 `max_tokens` 太小（拆解用 300）
  时 `content` 为空串；实体抽取（512）/回答生成（4096）因配额较大仍能产出 content。

**根因**：拆解/摘要类 LLM 调用 `max_tokens` 不足，Qwen3 思考模式把预算耗尽在思维链上，
正式 `content` 输出为空。

**解决方案**：
- `query_decomposer.py`：拆解 `llm.chat(messages, 0.2, 300)` → **2048**。
- `hyde_generator.py`：HyDE `max_tokens` 从 300 → **2048**（同因：HyDE 曾"生成结果过短，视为无效"）。

**验证**：拆解出 5 个德语子问题（sub_count=5），HyDE 不再过短。

---

### 3.4 【P2】需求拆解不"拆"：只翻译不拆阶段

**现象**：加强前 `sub_count=1`，子问题=原问题直译德语，多路检索退化为单路，召回面未扩大，
sources 未命中核心报关流程章节。

**根因**：旧 prompt"若原问题足够简单，则只返回 1 个子问题"，给了模型偷懒空间；
且未强制流程类问题按阶段拆分。

**解决方案**（`query_decomposer.py` `_DECOMPOSE_PROMPT` 重写）：
- 强制 2~N 个（`min_sub=min(2, MAX_SUB_QUERIES)`）；
- 流程类问题优先按「准备/资格 → 提交/办理 → 审核/海关处理 → 结果/放行/后续义务」分段；
- 每个子问题用目标国官方语言（德国→德语）撰写，保留专有名词（ATLAS 等）。

**验证**：拆解产出 5 个子问题：接入/注册 → 申报步骤 → 单证 → 审查放行 → 后续义务，全德语。

---

### 3.5 【P1】multi_search 并发触发 Milvus 初始化 → 6 路并行抢锁 → 5 路 vector=0

**现象**：拆解增强后 `multi_search` 用 `asyncio.gather` 并行 6 路 `search`，
日志出现 `Milvus 初始化失败，回退本地检索 ×5`，仅 sub1 拿到 vector=2，其余 5 路 vector=0 →
退化为纯 BM25；且命中章节偏离核心流程段（7/4.5.6.2/4.9.6/2.1 而非 Anmeldung/Ausfuhrverfahren）。

**排查/尝试**：
- 复现观察：单路检索（拆解前）向量正常（vector=2~3）；一旦 6 路并行，只有第 1 路成功。
- 定位到 `retriever._ensure_milvus`：每次 search 都会探测连接，`_milvus_ok=False` 冷却期内
  仍可能被多路同时穿透，各自 `asyncio.to_thread(MilvusClient(uri))` 并发抢 `logs\milvus.db`
  单进程锁。

**根因**：Milvus Lite 单文件单进程锁 + `_ensure_milvus` 无并发护栏 + 未复用已建连接
（每次 search 都重新 open）。

**解决方案**（`service\chat_service\retriever.py`）：
- `__init__` 新增 `self._milvus_lock = asyncio.Lock()`；
- `_ensure_milvus` 重写：
  - 已连接且 `_milvus is not None` → 直接复用，跳过 open；
  - 冷却期内失败 → 跳过重试；
  - 否则 `async with self._milvus_lock` + **双检**（等锁后可能已由别的任务建好）；
- `_build_milvus` 开头复位 `self._milvus = None`，失败不留半成品。

**验证**：`py_compile` 通过；需重启后端后由追踪 subagent 复探（应恢复多路向量召回）。

---

### 3.6 【P2】fact_check 覆盖度跨语言误杀（coverage 0.07 / 0.176 判 FAIL）

**现象**：中文回答 + 德文资料时，本地统计门控 `coverage` 极低（0.07/0.176）→ `passed=False`
→ 触发严格重生成；且 LLM 二次核查也判 FAIL（unsupported 全是德文专名如
`asuma/wam`、`bin/aufschub-bin`、`verfahrensanweisung`）。

**排查/尝试**：
- 首版跨语言分支用 `src_has_cjk = bool(re.search(cjk, sources_text))` 判定"资料是否全外文"；
  但 contexts 混入 vat_guide 中文段后 `src_has_cjk=True` → 跨语言分支不触发 → 误杀。
- 结论1：判定条件过强（要求资料完全无中文），混合语料场景漏判。
- 结论2：中文字词对德文原文的覆盖率指标本身失真，不该作为跨语言判据。

**根因**：覆盖度指标是单语假设（答案词与原文同语言）；跨语言转述场景指标失效，
旧判定（`not src_has_cjk`）在混合语料下失效。

**解决方案**（`service\chat_service\fact_checker.py`）：
- 改判定：`cross_lang = has_cjk and cjk_ratio < 0.5`（资料中文占比低即进入跨语言模式，
  不要求"完全无中文"）；
- 跨语言模式只校验：(1) 数字是否有原文依据；(2) 英文/数字专名（ATLAS/MRN/UZK/BIN 等）
  在原文出现比例 ≥30%，同时通过 `missing_numbers` 防编造金额/税率。

**验证（自检）**：
- 混中德语料：`passed=True, cross_lang=True`（此前 0.176 误杀场景）。
- 纯中文场景：`passed=True, cross_lang=False`（原有行为不变，coverage=0.421）。

---

### 3.7 【P2】严格重生成拖垮总耗时（84s~93s，其中 recheck 超时 16s+）

**现象**：校验未通过触发 `_strict_recheck`，`llm.chat` 重生成 2048 tokens 后再二次校验，
LLM 超时（`TimeoutError`）空转 16s；总 pipeline 84~93s。

**根因**：严格重生成无整体限时，依赖 LLM_TIMEOUT=15s 单层超时；重生成 max_tokens 又大。

**解决方案**（`chat_flow.py` + `config\settings.py`）：
- 新增 `RECHECK_TIMEOUT`（默认 12s，env 可调）；
- `_strict_recheck` 重生成包 `asyncio.wait_for(..., timeout=RECHECK_TIMEOUT)`，max_tokens 2048→**1024**；
- 二次校验 `asyncio.wait_for(..., timeout=max(1, RECHECK_TIMEOUT-8))`；
- 任一超时→ 回退原回答并附"未通过AI事实校验，以官方原文为准"提示，不再等满 16s。

**验证**：追踪日志确认 recheck 在 ~12s 超时放弃、回退原回答（已不再拖 16s+）。

---

### 3.8 【P2】远程 LLM 偶发超时 / 降级

**现象**：整段推理 / HyDE / 严格重生成偶发 `LLM chat 超时 | timeout=15s`；
高峰期 `degraded=True` 跳流式走整段生成。

**根因**：共享远程 llama.cpp 27B 推理负载不稳，`LLM_TIMEOUT=15s` 偏短；
HyDE 场景 15s 常不够一次 27B 生成。

**方案（待处理）**：
- 可考虑按用途分级超时（实体抽取短、HyDE/reasoning 长）；
- degraded 分支降级策略已存在（整段生成兜底），暂可接受。

---

### 3.9 【P2】ordering 源头召回命中"文档结构章"而非"操作流程章"

**现象**：多路检索（纯 BM25 阶段）ATLAS 命中 7/4.5.6.2/4.9.6/2.1（准入/复出口/
事后出口申报/文档说明），而非 Anmeldung / Ausfuhrverfahren / Einfuhrverfahren 等
核心报关流程章节。

**根因**：向量检索失效（见 3.5）时纯 BM25 对德文文档的粗筛章节标题排序偏向结构词；
且 BM25 对词汇鸿沟无能为力。

**解决方案（待验证）**：Milvus 并发锁修复（3.5）后应恢复向量语义召回，由跨语言向量
重排章节。若仍不足，考虑：
- 对 ATLAS 文档提高向量权重 / 增加该文档向量 top_k；
- 或子问题拆分更贴章节标题用语。

### 3.10 【已实现】跨语言检索 query 主干仍为中文（BM25 对中文零命中）

**现象**：需求拆解产出的子问题是德语，但检索主干（原问/未拆解时整体 query）仍是中文；
中文 query 走 BM25 对德文正文几乎零命中，跨语言命中完全依赖向量语义。

**根因**：检索 query 未随目标国家语言统一。拆解场景虽把子问题德语化，但「路0原问」
与未拆解场景的 `retrieve_with_fallback(query=中文)` 仍用原语言。

**解决方案**（新增 `service\chat_service\query_translator.py` + `chat_flow` 步骤2.6）：
- 新建 `QueryTranslator`：按 `entities.country` 映射目标国官方语言（德国→德语等），
  调 LLM 把用户问题翻译成该语言，保留专名（ATLAS/UZK/EORI/BIN 不译），失败/降级回退原文；
- 步骤2.6 在拆解后执行：`query_for_retrieve = 译文(若有)`；
  - 拆解场景：`multi_search(original_query=query_for_retrieve)`（路0 变德语）；
  - 未拆解/降级场景：`retrieve_with_fallback(query_for_retrieve, ...)`（整体德语检索）；
- 新增开关 `QUERY_TRANSLATE_ENABLE=true`（config/settings.py）；
- 新增 SSE 事件帧 `translate`（含 origin / translated）。

**验证（实跑）**：`QueryTranslator.translate("德国ATLAS电子报关流程需要哪些步骤？",
{country:"德国"})` → `Welche Schritte sind für das elektronische Zollanmeldeverfahren
in ATLAS in Deutschland erforderlich?`；ChatFlow 初始化含 `translator: QueryTranslator`。
端到端检索提升待重启后端后 SSE 探针复验。

---

### 3.11 待办/观察项（非阻塞）

- LLM 兜底核验在跨语言模式下的权威性：本地门控已被跨语言模式正确放行，但 LLM
  二次核查仍可能判 FAIL（上轮 example answer 实为劣质兜底话术，LLM 判得对）；
- answer 稳定性：拆解+检索能力提升后，生成/JSON解析环节偶发波动（上一轮整段生成
  48s 出 30 字 + 解析失败）；建议观察 `_stream_reason` 流式 vs 整段降级路径。

---

### 3.12 【已实现】检索已命中但 answer 仍 30 字"暂无规则"话术（生成链路根因）

**现象**：query 翻译后 sources 已聚焦 ATLAS 德文核心章节（单文档 10 章），
但最终 answer 仍是 `MSG_NO_ANSWER`（30 字），且日志 `回答 JSON 解析失败，回退整段文本`。

**根因链**：`_stream_reason` 把 Qwen3「思考流」计为 `streamed_any=True`，
但思考片段不并入 `raw_full` → 模型若只出思考、正文因连接中断/预算耗尽未产出 →
`streamed_any=True` 使整段兜底被跳过 → `raw_full=""` → `_extract_json_answer(None)`
→ `answer_text=MSG_NO_ANSWER`。**检索料足 ≠ 生成料进 answer**。

**解决方案（3 处）**：
1. `chat_flow._stream_reason`：改用 `content_any`（专指正式正文，思考不算）；
   `if not content_any:` 才触发整段生成兜底 —— **根因修复，空正文必兜底**；
2. `query_translator` / `hyde_generator`：空输出加一次重试（远程 llama.cpp 偶发空，
   增确定性，逃避 P1「翻译为空触发回退中文」的非确定路径）；
3. `chat_flow._strict_recheck`：严格重生成失败时不再无脑回退 30 字话术；
   若首轮 answer 有实质内容（>30 字且非 `MSG_NO_ANSWER`）则保留并附加
   「未通过AI事实双校验」风险提示。

**验证**：COMPILE OK；待后端重启后 SSE 探针复验 answer 非劣质话术。

---

### 3.13 【已实现】跨语言事实校验误判（中文答案 vs 德文资料，LLM 二次核查恒 FAIL）

**现象**：answer 已是扎实的 1443 字实质回答、本地门控 `passed=True`，但
`事实校验完成 | llm=False(checked=True)` 每次仍必判 FAIL，触发严格重生成
12s 白烧超时。原因：`llm.check_fact` 直接把「德文资料 + 中文回答」交给 LLM，
跨语言比对天然失真，LLM 判"回答与原文不符"实为误报。

**解决方案**（用户提出：先把双方对齐到同一语言再比对）：
1. `query_translator.py`：抽取通用 `_call`（含 1 次重试）+ 新增 `translate_text(text, lang)`，
   供事实校验把中文回答翻译成资料语言；`translate()`（查询专用）精简复用 `_call`；
2. `fact_checker.check` 新增 `lang` 参数：当 `local.cross_lang=True` 且目标语言非中文时，
   先把中文回答翻译为该语言，再调 `check_fact(德文资料, 德文回答)` 同语言比对；
   翻译失败则保守判"未通过"（防静默放行）；
3. `chat_flow`：新增 `_target_lang(entities)`（国家→语言映射，复用 query_translator），
   主校验与严格重校验均传入 `lang`；严格重校验内层 `asyncio.wait_for` 超时
   从 `RECHECK_TIMEOUT-8` 放宽到 `max(RECHECK_TIMEOUT, 20)`，避免翻译被掐断。

**验证（实跑）**：`translate_text("1. 参与接入准备（GZD登记/EORI/BIN/X.509）","德语")`
→ `1. Teilnahme an der Anbindungsvorbereitung (GZD-Registrierung/EORI/BIN/X.509)`（专名保留）；
`_target_lang({'country':'德国'})`=德语、空国家=空串。COMPILE OK。
端到端（校验是否不再白烧 12s 且用例通过）待重启后端后 SSE 探针复验。

---

### 3.14 【已实现】跨语言校验的"回答翻译"两次空 + 15s 硬超时

**现象**：跨语言校验分支已生效（`跨语言校验：先将回答翻译为 德语 再比对`），
但对 2312 字回答的翻译两次返回空 → 保守判"未通过" → 触发严格重生成 15s 超时，
总耗时冲到 140s、`llm=False` 依旧。

**根因**（双重）：
1. 翻译 `max_tokens=1024` 对几百字回答不够：Qwen3 reasoning 先吃预算，
   长文本翻译在 1024 tokens 内必被截断为空（与 3.3 同源）；
2. `llm.chat` 底层 `httpx.Client(timeout=LLM_TIMEOUT=15s)` 硬读超时：
   10000 tokens 翻译 + reasoning 远超 15s，实验实跑 3 次全部
   `httpx.ReadTimeout: timed out`——即使把 max_tokens 调到 10000，
   全局 15s 超时也会掐断。

**解决方案**（用户提出：幂等重试 + max_tokens 提到 10000）：
1. `query_translator._call` 重写：幂等重试最多 `_TRANSLATE_MAX_RETRIES=3`
   （空结果/异常均重试，重试成功打 `翻译重试成功` 日志）；`_TRANSLATE_MAX_TOKENS=10000`；
2. **关键**：直连 `httpx.Client` 且 `timeout=httpx.Timeout(connect=10, read=120, write=60, pool=30)`，
   不再走 `llm.chat`（规避全局 15s 硬超时）；`asyncio.to_thread` 不阻塞事件循环；
   `_TRANSLATE_MAX_INPUT=4000` 限输入长度；
3. 顺带修复：编辑产生过一个重复的 `_country_to_lang`（引用未定义的 `_lang_map`），已删。

**验证（实跑）**：长文本切分内容翻译成功
`1. Systempositionierung: Die ATLAS-Anmeldung ... UZK ... Teilnahme und Anbindung ...
GZD-Registrierung ... EORI und BIN ... ENS/SumA`（专名 UZK/GZD/EORI/BIN 保留完整）。
端到端（跨语言校验 `llm=` 是否变 True、总耗时是否回落）待后端重启后 SSE 探针复验。

---

### 3.15 【已废弃→语义比对】"翻译答案再比对"方案根因证伪 + 方案 B

**现象**：3.14 方案（幂等重试+10000 tokens+120s 直连）实跑端到端仍失败：
`翻译结果为空 attempt=1/3/2/3/3/3 → 翻译最终失败(3次) → 保守未通过 → 严格重生成 15s 超时`，
总耗时 **390s**（比 3.14 的 140s 净增 250s）。

**根因（推翻 3.14 的"预算不足"假设）**：Qwen3 `--reasoning on` 会把 **10000 tokens
预算几乎全耗在 `reasoning_content`**，`content` 返回空——短文本（查询翻译 7.8s）能成功，
**长文本（2312 字回答）reasoning 吃掉全部预算→content 必空**。翻译本身不可靠，
且每轮 ~94s × 3 次重试 = 282s 纯浪费。翻译层是错误架构。

**解决方案（用户选 B）**：
1. `llm_client` 新增 `check_fact_cross_lang`：**不翻译**，德文/法文原文 + 中文转述
   直接按「语义等价性」核对（允许翻译表达差异，禁止编造），仅 YES/NO；
2. `fact_checker` 校验2：`cross_lang=True` 时不再翻译回答、不再判死，
   改调 `check_fact_cross_lang`（依旧 `asyncio.to_thread`；超时/异常按本地门控兜底通过）；
3. **max_tokens 教训（见 3.17 实测）**：不做翻译、不判死，校验只取 YES/NO；
4. `reason="[跨语言语义比对] ..."` 标注日志。

**验证（实跑）**：`check_fact_cross_lang` 语义等价正例判定 YES、
非等价（添加原文没有的 EORI/BIN 申请步骤）判 NO——机制调通；但真实规模用例判 NO
（短期原因未知，或回答确有原文外的表述——需结合 3.16 原段落返回后复验）。
端到端（重生成 15s 是否消除、总耗时是否落回 ~120s）待后端重启 + SSE 探针复验。

---

### 3.17 【已实现】max_tokens 预算实验：不是越大越好，"刚好+余量" + 空输出降预算

**问题**：3.15 曾把 `_chat_long` 提到 max_tokens=20000、read 超时 240s，但这是错误方向——
Qwen3 `--reasoning on` 在预算充足时会让 `reasoning_content` 无限蔓延（越长越耗时），
校验类任务（仅 YES/NO）20000 会让单次调用拖到几十秒级。

**实测（同一问题、同一正文，本会话脚本实测）**：

| 场景 | 实测最优点 | 说明 |
|---|---|---|
| 事实校验（YES/NO） | **1024** | 512 即可 3.2s 出 YES；2048/4096 同为 2.7s（无收益）；10/4096 空输出是 reasoning 预算不足的旧观测，1024 是"够 reasoning + 余量"拐点 |
| 查询翻译（~100字） | **512~1024** | 按输入估算，短查询 512 |
| HyDE 假想条文 | **4096** | 2048 空输出（25.3s reasoning 溢出）；4096/8192 均 28s、输出同长 1499 字 → 4096 即"刚好" |
| 生成回答(500-800字) | **4096** | 2048 空输出(22.7s)；4096→909字、8192→1057字，等价耗时，4096 足够 |
| 长文翻译(4000字) | ≤1584（估） | `_estimate_translate_budget = 字符×0.9/3 + 384 余量`，夹在 512..4096 |

**实现**：
1. `llm_client._chat_long` 默认降回 **max_tokens=4096 / timeout=90s**；返回空 content
   （reasoning 吃掉预算）→ **预算减半重试**（512→256），逼 reasoning 收敛；异常原预算重试；
   最多 4 次。校验调用显式 `max_tokens=1024`；
2. `query_translator`：新增 `_estimate_translate_budget()` 按输入长度估算输出预算；
   `_TRANSLATE_MAX_TOKENS` 10000→4096（作上限，不再直接作为请求预算）；
   `_call` 同样支持空输出降预算重试；
3. 原则固化：**max_tokens=实际输出量级 + 少量余量**；超量用输入压缩/关键句提取，
   而不是无限放大预算让 reasoning 蔓延。

**验证（实跑）**：`_chat_long(mt=1024)` 校验 YES 2.4s；预算估算
`19字→512 / 384字→512 / 4000字→1584`。端到端耗时待 3.16 支线重启后 SSE 探针复核。

---

### 3.18 【已实现】根因升级：不是 max_tokens 不是窗口，是 `reasoning_effort=xhigh`

**现象**：3.17 后重启 E2E 仍失败——事件序列 `thinking ×4096 → … → answer=45字兜底话术`，
主生成又空（`content_any=False → 整段兜底 → 15s 超时 → 暂无规则`）。
耗时 134s，仍是 reasoning 吞掉 4096 预算。

**排查/根因（决定性）**：查询 llama.cpp `/props` 发现
`n_ctx=262144`（262k 窗口，**窗口远不是瓶颈**）且
`reasoning_effort|default('xhigh')`——**服务端强制每轮 xhigh 思维**（"think carefully,
consider plausible alternatives"），4k 预算全被 reasoning_content 吃掉 → content 空/截断。
5.4k 上下文对 262k 窗口毫无压力；变量是思维强度而非预算/窗口。

**试验（真实 5.4k 上下文，max_tokens=4096）**：

| reasoning_effort | content | reasoning_len | comp_tokens | 耗时 |
|---|---|---|---|---|
| xhigh（默认） | 614 | 7628 | 4096（截断） | 41.0s |
| high | 空（截断） | 7676 | 4096 | 41.1s |
| **medium** | **1040（完整）** | 4283 | 2935 | **29.6s** |
| low | 2（截断） | 7008 | 4096 | 41.4s |

结论：**medium 是生成唯一正确档位**——reasoning 收敛(4283<4096)后 content 完整输出，
且最省时。校验场景（YES/NO + max_tokens=1024）三种 effort 全过、3.2-3.9s，
reasoning 均收敛 842 → 校验维持 1024 即可，effort 无关紧要。

**实现**：
1. `llm_client.chat()` 加 `reasoning_effort=None` 参数（None=保持服务端默认 xhigh，
   不破坏摘要/实体提取等既有调用），`_chat_long()` 同步支持；
2. `chat_flow._stream_reason` 生成 payload 加 `reasoning_effort="medium"`；
   降级整段兜底 `llm.chat(...,"medium")`；流式无正文兜底同步 medium；
3. `strict_recheck` 严格重生成 `llm.chat(...,0.1,1024,"medium")`（reasoning 收敛更快出 JSON）；
4. **max_tokens 治理总结**：校验=1024、HyDE/生成=4096、翻译=输入估算(≤4096)；
   不再无脑大预算——预算大 + xhigh 思维 = reasoning 无限蔓延（20000 的病根）。

**验证（实跑）**：medium 生成 5.4k 上下文 1040 字完整内容（29.6s）。
端到端（thinking 帧仍多但 answer 应完整、`provide` 的 45 字话术消失、耗时回落）待双 agent 重启 + SSE 探针复核。

---

### 3.16 【已实现】检索返回"片段/摘要"而非原段落 → overlay 缝合 + 关键句提取

**现象**：用户指出——向量检索命中片段、切片无 overlay 导致上下文语义断开；
返回 content 被 `ContentCompressor` 在超长段替换为 LLM 摘要/硬截断；
生成端 `_RECONTEXT_LIMIT=1200` 再二次砍断。上下文断了 → 回答无源可依 → 校验失真。
（探索确认：切片器无 overlap；超长段句切后原段落不可恢复；BM25 索引用 `content_summary or content_raw`。）

**解决方案（用户选 A：返回层完整版，不动索引）**：
1. `settings`：`CONTEXT_OVERLAY_ENABLE=true`、`CONTEXT_OVERLAY_SPAN=1`、
   `CONTEXT_OVERLAY_MIN_TOKENS=64`、`CONTEXT_KEY_KEYS_ENABLE=true`；
2. `retriever.expand_with_context`：检索命中后按 (doc_uuid, chapter_id) 从内存快照
   拼同章节相邻段落（前后各 span 条）成 `item.overlay`（原段落上下文，保序 + `[相邻段落]` 标记），
   `RetrievalItem` 加 `overlay / overlay_token_len / sub_query_id` 字段（零 SQL）；
3. `content_compressor`：overlay 非空时 **mode="overlay"** 直接返回原段落上下文；
   超长段（>1024 token）改用 `llm.extract_key_sentences`（**只抽取不改写**，
   保数值/条款/专名原字，允许原文比对），弃用 LLM 改写摘要；
4. `chat_flow`：步骤3 检索后先 `expand_with_context`（含兜底 L1-L4 全路径）；
   `_RECONTEXT_LIMIT` 1200→3500，避免二次截断毁掉缝合后长上下文；
5. 摘要化仍保留在**索引侧**（BM25/向量用 `content_summary or content_raw`），
   返回层一律给原段落——"摘要用于索引，返回用原段"。

**验证（实跑）**：短片段 → overlay 缝合成功（overlay_tok=3668，原段落完整拼入相邻段）；
compress 输出 `mode=overlay`、content_len=5502、meta.paragraph.overlay=True。
ATLAS 204 段实测全部 ≤1024 token（无超长段），故关键句提取路径需后续用超长文档复验。
端到端（回答是否开始基于完整原段落、跨语言校验是否不再因"无源上下文"误判 NO）待 SSE 探针复验。

---

## 4. 关键文件索引

| 文件 | 职责 |
|------|------|
| `service\chat_service\query_decomposer.py` | 需求拆解（子问题多路检索） |
| `service\chat_service\retriever.py` | 混合检索 / `multi_search` / Milvus 连接护栏 |
| `service\chat_service\chat_flow.py` | 管道编排（步骤1实体→2HyDE→2.5拆解→3检索→5推理→6校验→7输出） |
| `service\chat_service\fact_checker.py` | 事实校验（本地门控+LLM复核，跨语言模式） |
| `service\chat_service\hyde_generator.py` | HyDE 假想文档生成 |
| `config\settings.py` | DECOMPOSE_* / RECHECK_TIMEOUT / 权重等配置 |
| `api\doc.py` | `/api/doc/reindex` 向量重建端点 |
| `scripts\probe_sse_sources.py` | SSE 全链路验证探针 |
| `scripts\probe_bm25_hit.py` | 跨语言检索诊断（得分对比） |
| `logs\uvicorn.out.log` / `logs\uvicorn.err.log` | 后端双日志 |

---

## 5. 用户明确约束（不可违背）

1. 主窗口不跑长进程/长脚本，一律交 subagent；监控 agent 完成后主窗口停 subagent 转分析。
2. 两个 subagent 依次启动（先启动、后追踪）；并行常驻由用户指出时按用户说的来。
3. 不重启远程 llama.cpp（共享进程）。
4. 向量重建/写入只能走运行中后端（Milvus 单进程锁）。
5. 所有问题的现象、尝试过程、根因、方案都要录入本文档后再动手/收尾。