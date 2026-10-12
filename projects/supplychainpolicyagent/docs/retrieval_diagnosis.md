# 检索质量问题诊断与选型（德国ATLAS电子报关流程问题）

## 1. 诊断结论：知识库有答案，检索没召回 → 检索问题（retrieval problem）

**证据链：**

1. **库内存在完整答案**（MySQL, doc_uuid=085898499f1e44feb11015599da4c511, 166章节）：
   - `/4.9 Ausfuhrverfahren`（出口报关程序）整章
   - `/4.9.1.1 Anlegen von Zollanmeldungen`——创建报关单
   - `/4.9.1.4 Überlassung und Ausstellung des ABD`——放行+出具随附单证
   - `/4.9.2 Überwachung`、`/4.9.2.1.7 Ausgang/Abbruch/Abschluss`——出境/终结
   - `/4.9.3 Erledigung`、`/4.9.6 Nachträgliche Ausfuhranmeldung`、`/4.9.4 Einstufiges`
   - `/4.9.7 Ausfuhr im Zusammenhang mit Versandverfahren`、`/4.9.5.1.2 Ausgangsbestätigung`

2. **检索 Top5 零命中 4.9**，全是：
   - `4.7 Ergänzende Zollanmeldung`(EZA 补充申报, fused=2.08, 但仅 BM25=0/vec=0.67)
   - `4.23 CCI`（进口集中清关，**方向性误召**，与出口无关，BM25=11.6）
   - `1.2 Begriffsbestimmungen`（术语定义，仅 vec=0.72）
   - `2.2 Dokumentationen`（文档列表）
   - `7 Voraussetzungen für die Teilnahme`（参与前置条件，**最接近答案**，但 **fused=-2.01 几乎被丢弃**）

3. **LLM 用命中段审计 → AGAINST**（无法回答）。但它是在"只给 Top5"的约束下判断的；库中 4.9 明明有步骤。

## 2. 结论
- **不是"知识库没答案"**，是**召回链路函数有问题**：相关段（4.9 系）进不了 Top5，而 CCI/术语/文档列表等弱相关甚至负分段落被排上去。
- 这直接解释了线上 E2E 为何答 39 字 fallback："没有上下文 → LLM 就说库没有"。

## 3. 检索质量选型（按 ROI 排序）

### A. 章节级粗筛增强（最低成本，立即可做）
**问题根因**：`search()` 第一步粗筛用章节标题 BM25 选 Top5 章节，但只选 `score>0` 的。翻译 query 是 "Welche Schritte sind für das elektronische Zollanmeldungsverfahren in ATLAS in Deutschland erforderlich?"，标题分词后 "Ausfuhrverfahren? nein" → 4.9 ("Ausfuhrverfahren") 与 "Zollanmeldungsverfahren" 重叠度不足，粗筛没把 4.9 圈进候选。
- 方案：粗筛 Top-N 扩大（5→10-15），并允许 score>0 以外再保底"最相关章节"（识别 `Zollanmeldung/Ausfuhr/Verfahren` 关键词所在章节）。
- 效果：直接决定 4.9 系段落能否进入段落级召回。**全链路最大瓶颈在此**。

### B. Rerank 排序修复（高 ROI）
**问题**：`_rerank` 用的 CrossEncoder 对译文长句打分不敏感，Top5 里 `4.7 EZA`(fused=2.08) vs `7 参与条件`(fused=-2.01) 排序不合理；且 4.9 系根本没进入候选池（见 A），rerank 无法补。
- 方案：确认 `_rerank` 输入是 fused 后全量 top_k*2（BM25+vec），若 Rerank 直接用 fused 排，则问题在 metric 偏差。改为"BM25/向量分在高分时优先"或加 min-max 归一化后再 rerank。

### C. 检索 query 形态（中 ROI）
- HyDE（已有）帮了向量，但 BM25 对德语长问句分词效率差（"Welche Schritte sind für das elektronische Zollanmeldungsverfahren"→ 德语复合词切分问题）。
- 方案：为 BM25 单独用**关键词化查询**（从 query 抽取名词短语：Zollanmeldung, Ausfuhr, Schritte, ATLAS），而非整句。

### D. 复合词/切词优化（中 ROI，德语特有）
- 德语复合词 `Zollanmeldungsverfahren = Zoll + Anmeldung + Verfahren`。当前 `tokenize()`（rank_bm25）对复合词不做拆解，`Anmeldung`、`Verfahren` 在 BM25 里单独分可能没出现。
- 方案：tokenize 加德语复合词拆分/词干化（如用 `de-stemmer`/拆头尾），显著提升 BM25 命中 `Zollanmeldung`、`Verfahrenanweisung`。

### E. 召回段本身结构问题（低 ROI）
- 库段落 token 500-700，命中段是"整章打包"（4.9 的一个章节=1 段），步骤分散在不同段。当前内容提取器按段处理，缺乏跨章节衔接。**非急迫**。

### F. 评估机制（长期）
- 建 5-10 条已知问答对（本问题 + 4.9 段落为金标准），每次检索改动跑 recall@5/@10，防止回归。

## 4. 推荐立即动作
1. **A（章节粗筛 Top 扩大 + 保底关键词章节）**——成本最低，直接修复 4.9 进候选。
2. **B（rerank 排序修正）**——配合 A 观察是否还需归一化。
3. **D（德语复合词拆分）**——BM25 召回质量的根本提升。
4. **F（回归用 query×4.9 金标准）**——立一个评估集边改边测。

实现前先小规模验证：A 改后跑 `tmp_kb_audit.py`，确认 4.9 系进入 Top5 且 fused>0。