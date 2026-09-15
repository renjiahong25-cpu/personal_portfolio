# -*- coding: utf-8 -*-
"""
问答工作流主编排器（SSE 流式输出 / 幂等去重 / 自适应降级）
=====================================================
完整链路：
    1. 实体抽取（EntityExtractor，口语纠错补全）
    2. HyDE 增强（HyDEGenerator，可降级关闭）
    3. 混合检索 + 四层兜底（HybridRetriever）
    4. Token 压缩（ContentCompressor）
    5. 多章节推理（基于检索上下文的跨章节聚合生成，支持 token 级流式）
    6. AI 事实校验 + 兜底重生成（FactChecker）
    7. 结构化输出 + 三级溯源（SSE 事件流）

能力：
    - SSE（StreamingResponse）事件流：start/entities/hyde/retrieve/compress/reasoning/check/answer/done
    - 幂等性：request_id 去重（内存缓存 + 并发复用同一 Future）
    - 降级：高并发下自动关闭 HyDE、精简摘要（硬截断）、关闭二次校验
"""
import asyncio
import json
import re
import time
import uuid
from typing import AsyncGenerator, Optional

import httpx

from config.logging_config import get_logger
from config import settings
from config.constants import MSG_NO_ANSWER
from core.middleware import generate_request_id
from core.llm_client import llm_client
from .entity_extractor import EntityExtractor
from .hyde_generator import HyDEGenerator
from .query_decomposer import QueryDecomposer
from .query_translator import QueryTranslator
from .retriever import HybridRetriever, norm_country
from .retrieval_gate import RetrievalGate
from .content_compressor import ContentCompressor
from .fact_checker import FactChecker
from service.data_service.kb_ingest_service import kb_ingest_service, extract_urls
from service.dah_rag.integration import DAHRAGIntegrator, _INTENT_STRATEGY

# SQLAlchemy ORM（用于对话落库，缺失时仅跳过持久化）
try:
    from db.models.base import SessionLocal, ChatConversation
except Exception:  # pragma: no cover
    SessionLocal = None
    ChatConversation = None

logger = get_logger("chat_flow")

# ============================================================
# 生成端 Prompt（含"禁止编造"强约束，配合 FactChecker 双重校验）
# ============================================================
_SYSTEM_PROMPT = (
    "你是跨境物流合规专家助手，专精中国出口至德国的清关政策与规则。请严格遵守以下约束：\n"
    "1. 必须只依据下面【检索资料】中给出的法规条款作答，严禁编造HS编码、税率、认证要求、"
    "时间节点或任何数值；\n"
    "2. 若【检索资料】中包含与问题相关或部分相关的章节，必须基于资料作答（中文概述并引用资料原文），"
    "资料未覆盖的细节明确标注“资料未明确”，不得强行编造；"
    "严禁以“暂无对应合规规则”搪塞资料中已有相关内容的问题；"
    "正常回答中严禁追加“建议咨询/请咨询/推荐咨询”等免责句；"
    "仅当全部资料与问题零相关时，才整句回复：“当前知识库暂无对应合规规则，"
    "建议咨询专业清关师或查阅官方网站”；\n"
    "3. 回答必须使用 Markdown 结构化排版：段落之间用空行分隔；章节用 `##`/`###` 小标题；"
    "要点用 `-` 列表；关键数值、条款、单证名用 `**加粗**`；"
    "涉及步骤/流程/申报顺序/架构类问题，必须在文字之后另附一个独立 mermaid 代码块"
    "（格式：三个反引号 + `mermaid` 换行 + `flowchart TD` 起始的图，节点用 [方括号] 与 --> 连接），"
    "实现文字概述 + 流程图并存的回答；"
    "涉及具体条款须标注来源文档/章节，便于溯源；\n"
    "4. 结合多条资料进行跨章节聚合推理，整合碎片化规则，输出完整闭环答案；\n"
    "5. 给出必要的风险提示（运输限制、认证前置、申报时效等），无风险点则为空字符串；\n"
    "6. 若问题超出跨境通关合规服务边界（如投资/股票建议、法律诉讼意见、商业谈判策略、"
    "医疗建议等），不得依据检索资料强行作答，应礼貌说明该问题超出本系统服务范围，"
    "并建议咨询相应领域的专业人士（如投资顾问、执业律师）；\n"
    "7. 只输出一个JSON对象，格式：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}"
)

_STRICT_SYSTEM_PROMPT = _SYSTEM_PROMPT.replace(
    "7. 只输出一个JSON对象，格式：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}",
    "7. 上次生成内容经事实校验未通过（可能存在编造），本次必须逐字核对【检索资料】，"
    "只引用资料原文中确实存在的条款与数值，剔除所有资料中找不到依据的内容，重新完整生成。"
    "只输出JSON：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}",
)


def _sse_frame(event: str, data: dict) -> str:
    """构造标准 SSE 数据帧"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_RECONTEXT_LIMIT = 3500  # 单条上下文进入模型的最大字符数（overlay 缝合后原段落较长，需足够留白）


def _extract_json_answer(raw: str, request_id: str) -> Optional[dict]:
    """从 LLM 输出中稳健解析 {answer, risk_tips} JSON"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 容忍尾部逗号等常见问题
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    # JSON 被截断（xhigh reasoning 吃光预算）时的兜底：正则提取 answer 字段，
    # 避免把裸 JSON 直接渲染到 UI
    m = re.search(r'"answer"\s*:\s*"', text)
    if m:
        seg = text[m.end():]
        rm = re.search(r'"\s*,\s*"risk_tips"\s*:', seg)
        body = seg[: rm.start()] if rm else seg
        body = body.strip()
        if body.endswith('"'):
            body = body[:-1]
        answer = None
        try:
            answer = json.loads('"' + body + '"')
        except json.JSONDecodeError:
            answer = body.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        if answer and answer.strip():
            logger.warning(f"[{request_id}] JSON 截断，正则提取 answer 字段 | len={len(answer)}")
            return {"answer": answer, "risk_tips": ""}
    logger.warning(f"[{request_id}] 回答 JSON 解析失败，回退整段文本 | raw_head={text[:120]}")
    return None


class ChatFlow:
    """问答工作流主编排器（单例）"""

    def __init__(self):
        self.llm = llm_client
        self.extractor = EntityExtractor()
        self.hyde = HyDEGenerator()
        self.decomposer = QueryDecomposer()
        self.translator = QueryTranslator()
        self.retriever = HybridRetriever()
        # DAH-RAG 决策层：注入共享检索实例，检索能力复用原引擎（可回退）
        self.dah_rag = DAHRAGIntegrator(original_retriever=self.retriever)
        self.gate = RetrievalGate()
        self.compressor = ContentCompressor()
        self.checker = FactChecker()

        # 幂等：request_id -> Future（并发去重）；request_id -> 缓存结果
        self._inflight: dict[str, asyncio.Future] = {}
        self._cache: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._active = 0
        logger.info(
            f"ChatFlow 初始化完成 | hyde={settings.HYDE_ENABLE} | "
            f"smart_summary={settings.SMART_SUMMARY_ENABLE} | llm_check={settings.LLM_CHECK_ENABLE} | "
            f"degrade_threshold={settings.DEGRADE_MAX_ACTIVE}"
        )

    # ----------------------------------------------------------
    # 幂等控制：并发复用 + 结果缓存（TTL）
    # ----------------------------------------------------------
    def _cache_get(self, request_id: str) -> Optional[dict]:
        entry = self._cache.get(request_id)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > settings.CACHE_TTL_SECONDS:
            self._cache.pop(request_id, None)
            return None
        return entry

    def _cache_put(self, request_id: str, result: dict, tail_events: list[str]):
        # 简单防膨胀：超过 500 条缓存时清理最旧的一半
        if len(self._cache) > 500:
            sorted_keys = sorted(self._cache, key=lambda k: self._cache[k].get("ts", 0))
            for k in sorted_keys[: len(sorted_keys) // 2]:
                self._cache.pop(k, None)
        self._cache[request_id] = {"result": result, "tail_events": tail_events, "ts": time.time()}

    async def _register(self, request_id: str):
        """幂等注册：已存在同 request_id 的并发请求则等待复用"""
        async with self._lock:
            fut = self._inflight.get(request_id)
            if fut is not None and not fut.done():
                logger.info(f"幂等命中(并发去重) | request_id={request_id}")
                return "wait", fut
            fut = asyncio.get_event_loop().create_future()
            self._inflight[request_id] = fut
            return "run", fut

    def _complete(self, request_id: str, fut: asyncio.Future):
        """管道结束：解除并发等待"""
        if not fut.done():
            fut.set_result(None)
        async def _cleanup():
            async with self._lock:
                if self._inflight.get(request_id) is fut:
                    self._inflight.pop(request_id, None)
        asyncio.ensure_future(_cleanup())

    # ----------------------------------------------------------
    # 公开入口：SSE 流式接口
    # ----------------------------------------------------------
    async def stream(
        self, question: str, session_id: str, request_id: str,
        action: str = "", action_data: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """
        问答流式入口：按 request_id 幂等去重，输出 SSE 事件帧
        action: 前端交互按钮结构化动作（确定性通道，优先于 LLM 意图识别）
        """
        request_id = request_id or generate_request_id()
        session_id = session_id or uuid.uuid4().hex[:16]

        cached = self._cache_get(request_id)
        if cached:
            logger.info(f"幂等命中(结果缓存) | request_id={request_id}")
            for frame in cached["tail_events"]:
                yield frame
            return

        mode, fut = await self._register(request_id)
        if mode == "wait":
            # 同 request_id 的并发请求：等待主请求完成后复用其缓存结果
            logger.info(f"幂等等待主请求完成 | request_id={request_id}")
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=settings.API_TIMEOUT * 2)
            except (asyncio.TimeoutError, Exception):
                pass
            cached = self._cache_get(request_id)
            if cached:
                for frame in cached["tail_events"]:
                    yield frame
            else:
                yield _sse_frame("error", {"message": "服务繁忙，请稍后重试", "request_id": request_id})
            return

        # mode == "run"：本请求为主请求，执行完整管道
        try:
            async for frame in self._pipeline(question, session_id, request_id, action, action_data or {}):
                yield frame
        finally:
            self._complete(request_id, fut)

    # ----------------------------------------------------------
    # 核心管道
    # ----------------------------------------------------------
    async def _pipeline(
        self, question: str, session_id: str, request_id: str,
        action: str = "", action_data: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        step_ctx: dict = {}
        t_total = time.time()
        self._active += 1
        # 高峰降级判定：延迟敏感场景牺牲体验保核心可用
        degraded = self._active > settings.DEGRADE_MAX_ACTIVE
        if degraded:
            logger.warning(
                f"[{request_id}] 进入降级模式 | active={self._active} > {settings.DEGRADE_MAX_ACTIVE} | "
                f"关闭HyDE/智能摘要/二次校验"
            )
        result: dict = {}
        try:
            yield _sse_frame("start", {"request_id": request_id, "session_id": session_id, "degraded": degraded})

            # ---------- 步骤0.5：前端交互按钮（确定性动作通道，零 LLM 耗时，优先于意图识别） ----------
            # ai_search_ingest=AI搜索官网 / confirm_ingest=确认入库 / decline=不需要 / ingest_progress=进度轮询
            if settings.CHAT_INGEST_ENABLE and action in (
                "ai_search_ingest", "confirm_ingest", "decline", "ingest_progress"
            ):
                action_result = await self._handle_ingest_action(
                    action, action_data or {}, question, session_id, request_id
                )
                if action_result is not None:
                    elapsed_ms = int((time.time() - t_total) * 1000)
                    action_result["elapsed_ms"] = elapsed_ms
                    # 进度轮询是 UI 刷新请求，不落对话历史（防历史被轮询刷屏）
                    if action != "ingest_progress":
                        await self._persist(session_id, question, action_result, {})
                    answer_frame = _sse_frame("answer", action_result)
                    done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
                    self._cache_put(request_id, action_result, [answer_frame, done_frame])
                    yield answer_frame
                    yield done_frame
                    logger.info(f"[{request_id}] 管道结束(结构化动作 {action}) | total={elapsed_ms}ms")
                    return

            # ---------- 步骤0：多轮历史加载（条件改写/生成端上下文用） ----------
            history: list[dict] = []
            if settings.QUERY_REWRITE_ENABLE:
                history = await self._load_history(session_id)

            # ---------- 步骤1：实体抽取（携带历史时同一次调用内完成条件改写+实体继承） ----------
            t = time.time()
            entities = await self.extractor.extract(question, request_id, history=history)
            step_ctx["entities"] = entities
            correlation_id = f"{entities.get('category','')}/{entities.get('country','')}/{entities.get('hs_code','') or '-'}"
            yield _sse_frame(
                "entities",
                {
                    "request_id": request_id,
                    "entities": entities,
                    "elapsed": round(time.time() - t, 3),
                    "correlation_id": correlation_id,
                },
            )
            logger.info(f"[{request_id}] 步骤1[实体抽取] 完成 | elapsed={round(time.time()-t,3)}s")

            # ---------- 步骤1.5：多轮条件改写生效（延续→自包含问题；新话题→原问题，防上下文污染） ----------
            is_followup = bool(entities.get("is_followup"))
            rewritten = str(entities.get("rewritten_query") or "").strip()
            effective_query = (
                rewritten if (is_followup and rewritten and rewritten != question) else question
            )
            step_ctx["effective_query"] = effective_query
            yield _sse_frame(
                "rewrite",
                {
                    "request_id": request_id,
                    "is_followup": is_followup,
                    "origin": question,
                    "rewritten": rewritten,
                    "effective": effective_query,
                },
            )
            if is_followup and effective_query != question:
                logger.info(f"[{request_id}] 步骤1.5[条件改写] 延续话题 | rewritten={effective_query[:80]}")

            # ---------- 步骤1.6：对话驱动知识库扩充（URL直给/国家搜索/确认入库/进度查询） ----------
            if settings.CHAT_INGEST_ENABLE:
                ingest_result = await self._handle_ingest_intent(question, entities, session_id, request_id)
                if ingest_result is not None:
                    elapsed_ms = int((time.time() - t_total) * 1000)
                    ingest_result["elapsed_ms"] = elapsed_ms
                    await self._persist(session_id, question, ingest_result, entities)
                    answer_frame = _sse_frame("answer", ingest_result)
                    done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
                    self._cache_put(request_id, ingest_result, [answer_frame, done_frame])
                    yield answer_frame
                    yield done_frame
                    logger.info(f"[{request_id}] 管道结束(知识库扩充交互) | total={elapsed_ms}ms")
                    return

            # ---------- 步骤2：HyDE 增强（可降级） ----------
            t = time.time()
            hyde_doc = None
            if settings.HYDE_ENABLE and not degraded:
                hyde_doc = await self.hyde.generate(effective_query, entities, request_id)
            step_ctx["hyde"] = hyde_doc
            logger.info(f"[{request_id}] 步骤2[HyDE增强] 完成 | enabled={bool(hyde_doc)} | elapsed={round(time.time()-t,3)}s")
            yield _sse_frame(
                "hyde",
                {"request_id": request_id, "enabled": bool(hyde_doc), "len": len(hyde_doc or "")},
            )

            # ---------- 步骤2.5：需求拆解（复杂问题→子问题，多路检索） ----------
            t = time.time()
            sub_queries = None
            if settings.DECOMPOSE_ENABLE and not degraded:
                sub_queries = await self.decomposer.decompose(effective_query, entities, request_id)
            step_ctx["sub_queries"] = sub_queries
            logger.info(
                f"[{request_id}] 步骤2.5[需求拆解] 完成 | sub_count={len(sub_queries) if sub_queries else 0} | "
                f"elapsed={round(time.time()-t,3)}s"
            )
            yield _sse_frame(
                "decompose",
                {
                    "request_id": request_id,
                    "enabled": bool(sub_queries),
                    "sub_queries": sub_queries or [],
                    "elapsed": round(time.time() - t, 3),
                },
            )

            # ---------- 步骤2.6：查询翻译（跨语言→目标国官方语言，弥补BM25词汇鸿沟） ----------
            t = time.time()
            query_for_retrieve = effective_query      # 检索用 query（默认条件改写后的自包含问题）
            translated_query = None
            if settings.QUERY_TRANSLATE_ENABLE and not degraded:
                translated_query = await self.translator.translate(effective_query, entities, request_id)
                if translated_query:
                    query_for_retrieve = translated_query
            step_ctx["translated_query"] = translated_query
            logger.info(
                f"[{request_id}] 步骤2.6[查询翻译] 完成 | translated={bool(translated_query)} | "
                f"elapsed={round(time.time()-t,3)}s"
            )
            yield _sse_frame(
                "translate",
                {
                    "request_id": request_id,
                    "enabled": bool(translated_query),
                    "origin": effective_query,
                    "translated": translated_query or "",
                    "elapsed": round(time.time() - t, 3),
                },
            )

            # ---------- 步骤3：混合检索 + 四层兜底（DAH-RAG 意图决策层） ----------
            t = time.time()
            dahrag_info = {"enabled": self.dah_rag.config.enable_dah_rag, "intent": None, "strategy": "", "routed": "default"}
            try:
                intent = await self.dah_rag.classify_intent(query_for_retrieve, entities.get("country", ""))
                dahrag_info["intent"] = intent
                if intent:
                    _s = _INTENT_STRATEGY.get(intent)
                    if _s:
                        dahrag_info["strategy"] = _s["label"]
                        dahrag_info["routed"] = "dah_rag" if self.dah_rag.config.enable_strategy else "passive"
            except Exception as e:  # pragma: no cover
                logger.warning(f"[{request_id}] DAH-RAG 意图识别失败 | error={e}")
            if sub_queries:
                # 拆解命中：多路检索（翻译问 + 子问题并行）+ 合并去重
                # 国家分区：意图识别的 entities.country 决定只召回该国管辖文档
                sub_items = await self.dah_rag.multi_search(
                    sub_queries,
                    request_id=request_id,
                    original_query=query_for_retrieve,
                    country=entities.get("country", ""),
                )
                retrieval = {"level": 1, "items": sub_items, "follow_question": "", "summary": {}}
            else:
                # 未拆解（简单问题/降级/失败）：走原单次检索 + 四层兜底（使用翻译后的query）
                retrieval = await self.dah_rag.retrieve_with_fallback(
                    query_for_retrieve, entities, hyde_doc, request_id
                )
            level = retrieval.get("level", 4)
            items = retrieval.get("items", [])
            follow_question = retrieval.get("follow_question", "")
            r_elapsed = round(time.time() - t, 3)

            # ---------- 步骤3.5：迭代检索（充分性判定 + 定向补查，最多 N 轮） ----------
            iterate_info = {"enabled": settings.ITERATIVE_ENABLE and not degraded, "rounds": []}
            if settings.ITERATIVE_ENABLE and not degraded and items:
                t_it = time.time()
                items, iterate_info["rounds"] = await self._iterative_refine(
                    effective_query, entities, items, request_id
                )
                iterate_info["elapsed"] = round(time.time() - t_it, 3)
                logger.info(
                    f"[{request_id}] 步骤3.5[迭代检索] 完成 | rounds={len(iterate_info['rounds'])} | "
                    f"items={len(items)} | elapsed={iterate_info['elapsed']}s"
                )
                yield _sse_frame(
                    "iterate",
                    {"request_id": request_id, **iterate_info},
                )

            # overlay 上下文缝合：命中片段按同文档同章节补相邻段落，返回原段落语义上下文
            stitch_t = time.time()
            items = await self.dah_rag.expand_with_context(items, request_id=request_id)
            stitch_elapsed = round(time.time() - stitch_t, 3)

            logger.info(
                f"[{request_id}] 步骤3[混合检索] 完成 | level={level} | count={len(items)} | "
                f"overlay_stitched={stitch_elapsed}s | elapsed={r_elapsed}s | "
                f"dahrag_intent={dahrag_info['intent'] or '-'}"
            )
            yield _sse_frame(
                "retrieve",
                {
                    "request_id": request_id,
                    "level": level,
                    "count": len(items),
                    "hit": len(items) > 0,
                    "elapsed": r_elapsed,
                    "summary": retrieval.get("summary", {}),
                    "dahrag": dahrag_info,
                },
            )

            # ---------- 步骤4：Token 压缩（可降级为硬截断） ----------
            t = time.time()
            contexts = await self.compressor.compress(items, request_id, degraded=degraded)
            logger.info(f"[{request_id}] 步骤4[Token压缩] 完成 | contexts={len(contexts)} | elapsed={round(time.time()-t,3)}s")
            yield _sse_frame(
                "compress",
                {"request_id": request_id, "count": len(contexts), "degraded": degraded},
            )

            # ---------- 四级兜底 / 无答案 分支 ----------
            # 四级兜底仅返回权威官网链接，禁用 LLM 解读（L4=全网官网兜底）
            if level == 4:
                if items:
                    links = [i for i in items if i.source_url]
                    answer_text = (
                        "当前知识库暂未检索到与该问题直接匹配的合规规则，"
                        "以下为权威官方来源链接，请据此进一步核实：\n" + "\n".join(
                            f"- {i.doc_title}：{i.source_url}" for i in links
                        )
                    )
                    sources = [
                        {"doc_title": i.doc_title, "source_url": i.source_url, "category": i.category}
                        for i in links
                    ]
                else:
                    answer_text = MSG_NO_ANSWER
                    sources = []

                # 知识库缺口引导：目标国家在 KB 零文档 → 展示"AI 搜索官网并入库"按钮（防上下文误触发）
                # 用户点"不需要"后 24h 内不再提示（declined 记忆）
                gap_actions: list[dict] = []
                if settings.CHAT_INGEST_ENABLE:
                    gap_country = norm_country(entities.get("country", ""))
                    if gap_country and await asyncio.to_thread(kb_ingest_service.country_doc_count, gap_country) == 0:
                        if await asyncio.to_thread(
                            kb_ingest_service.has_recent_decline, session_id, gap_country
                        ):
                            answer_text = MSG_NO_ANSWER
                        else:
                            answer_text, gap_actions = await self._ingest_gap_guidance(
                                gap_country, session_id, request_id
                            )
                result = {
                    "answer": answer_text,
                    "risk_tips": "",
                    "sources": sources,
                    "doc_count": len({s.get("doc_title") for s in sources}),
                    "segment_count": 0,
                    "level": level,
                    "follow_question": follow_question,
                    "request_id": request_id,
                    "session_id": session_id,
                }
                if gap_actions:
                    result["actions"] = gap_actions
                elapsed_ms = int((time.time() - t_total) * 1000)
                result["elapsed_ms"] = elapsed_ms
                await self._persist(session_id, question, result, entities)
                answer_frame = _sse_frame("answer", result)
                done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
                self._cache_put(request_id, result, [answer_frame, done_frame])
                yield answer_frame
                yield done_frame
                logger.info(f"[{request_id}] 管道结束(四级官网兜底) | total={elapsed_ms}ms")
                return

            # 检索无任何有效上下文（非四级）：明确无答案，禁止编造
            if not contexts:
                answer_text = MSG_NO_ANSWER
                sources = []
                result = {
                    "answer": answer_text,
                    "risk_tips": "",
                    "sources": sources,
                    "doc_count": 0,
                    "segment_count": 0,
                    "level": level,
                    "follow_question": follow_question,
                    "request_id": request_id,
                    "session_id": session_id,
                }
                elapsed_ms = int((time.time() - t_total) * 1000)
                result["elapsed_ms"] = elapsed_ms
                await self._persist(session_id, question, result, entities)
                answer_frame = _sse_frame("answer", result)
                done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
                self._cache_put(request_id, result, [answer_frame, done_frame])
                yield answer_frame
                yield done_frame
                logger.info(f"[{request_id}] 管道结束(无答案) | total={elapsed_ms}ms")
                return

            # ---------- 步骤5：多章节推理（跨上下文聚合生成，token 级流式） ----------
            t = time.time()
            messages = self._build_messages(effective_query, entities, contexts, follow_question, history)
            raw_full = ""
            async for kind, delta in self._stream_reason(messages, request_id, degraded):
                if not delta:
                    continue
                if kind == "think":
                    # Qwen3 思考过程单独推送，前端展示"AI 分析中"，不并入最终答案
                    yield _sse_frame("thinking", {"segment": delta, "request_id": request_id})
                else:
                    raw_full += delta
                    yield _sse_frame("reasoning", {"segment": delta, "request_id": request_id})
            parsed = _extract_json_answer(raw_full, request_id)
            if parsed and parsed.get("answer"):
                answer_text = str(parsed["answer"]).strip()
                risk_tips = str(parsed.get("risk_tips") or "").strip()
            else:
                answer_text = (parsed.get("answer") if parsed else raw_full) or MSG_NO_ANSWER
                risk_tips = ""
            r_elapsed = round(time.time() - t, 3)
            logger.info(f"[{request_id}] 步骤5[多章节推理] 完成 | answer_len={len(answer_text)} | elapsed={r_elapsed}s")

            # ---------- 步骤6：AI 事实校验（可降级为仅本地门控） ----------
            t = time.time()
            yield _sse_frame(
                "checking",
                {"request_id": request_id, "message": "正在核验答案与资料的吻合度，请稍候…"},
            )
            try:
                check = await asyncio.wait_for(
                    self.checker.check(
                        answer_text, contexts, request_id, degraded=degraded, lang=self._target_lang(entities)
                    ),
                    timeout=420.0,
                )
            except TimeoutError:
                # LLM 校验整体超时（xhigh 大输入 reasoning 可能很长）→ fail-closed 判不通过，走严格重生成
                check = {
                    "passed": False, "reason": "事实校验超时（fail-closed）", "local_pass": False,
                    "llm_pass": False, "coverage": 0.0, "llm_checked": False, "llm_unavailable": True,
                }
                logger.warning(f"[{request_id}] 事实校验整体超时(420s)，fail-closed 判不通过")
            check_elapsed = round(time.time() - t, 3)
            logger.info(f"[{request_id}] 步骤6[事实校验] 完成 | passed={check['passed']} | elapsed={check_elapsed}s")
            yield _sse_frame(
                "check",
                {
                    "request_id": request_id,
                    "passed": check["passed"],
                    "reason": check["reason"],
                    "coverage": check["coverage"],
                    "llm_checked": check["llm_checked"],
                },
            )

            # 校验未通过：严格模式重生成一次，仍失败则兜底回复（防乱答）
            if not check["passed"]:
                yield _sse_frame(
                    "checking",
                    {"request_id": request_id, "message": "首次校验未通过，正在严格复核并重新生成回答…"},
                )
                answer_text, risk_tips = await self._strict_recheck(
                    effective_query, entities, contexts, request_id, degraded, answer_text, risk_tips, history
                )

            # ---------- 步骤7：结构化输出（三级溯源） ----------
            sources = self._build_sources(contexts)
            result = {
                "answer": answer_text,
                "risk_tips": risk_tips,
                "sources": sources,
                "doc_count": len({s.get("doc_title") for s in sources if s.get("doc_title")}),
                "segment_count": len(contexts),
                "level": level,
                "follow_question": follow_question,
                "request_id": request_id,
                "session_id": session_id,
            }
            elapsed_ms = int((time.time() - t_total) * 1000)
            result["elapsed_ms"] = elapsed_ms
            # 落库历史
            await self._persist(session_id, question, result, entities)
            logger.info(
                f"[{request_id}] 步骤7[结构化输出] 完成 | sources={len(sources)} | 总耗时={elapsed_ms}ms"
            )

            answer_frame = _sse_frame("answer", result)
            done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
            self._cache_put(request_id, result, [answer_frame, done_frame])
            yield answer_frame
            yield done_frame

        except Exception as e:
            logger.error(f"[{request_id}] 问答管道异常 | error={e}", exc_info=True)
            result = {
                "answer": "系统繁忙，请稍后重试。",
                "risk_tips": "",
                "sources": [],
                "doc_count": 0,
                "segment_count": 0,
                "request_id": request_id,
                "session_id": session_id,
            }
            yield _sse_frame("error", {"message": "系统繁忙，请稍后重试", "request_id": request_id})
            yield _sse_frame("done", {"request_id": request_id, "error": True})
        finally:
            self._active -= 1

    # ----------------------------------------------------------
    # 推理与流式
    # ----------------------------------------------------------
    def _target_lang(self, entities: dict) -> str:
        """由实体国家推导目标资料语言（用于事实校验跨语言对齐），空则返回''"""
        from .query_translator import _country_to_lang

        return _country_to_lang(entities.get("country", ""))

    def _build_messages(self, question: str, entities: dict, contexts: list[dict], follow_question: str,
                        history: Optional[list[dict]] = None) -> list[dict]:
        """构造多章节推理输入：紧凑历史(可选) + 问题 + 实体 + 编号上下文（含三级元数据）"""
        ctx_lines = []
        for idx, c in enumerate(contexts, start=1):
            meta = c.get("meta", {})
            doc = meta.get("document", {})
            part = (
                f"【资料{idx}】文档={doc.get('title', '未知')}"
                f" | 来源={doc.get('source_url', '-')}"
                f" | 章节={meta.get('chapter', {}).get('title', '-')}"
                f" | 生效时间={doc.get('effective_time', '-')}\n"
                f"{c.get('content', '')[: _RECONTEXT_LIMIT]}"
            )
            ctx_lines.append(part)
        # 生成端紧凑历史（仅问题+实体摘要，不含长答案；作答仍必须基于检索资料）
        hist_block = ""
        if settings.GEN_HISTORY_ENABLE and history:
            hist_block = (
                "最近对话（仅供理解指代与省略要素，作答仍必须严格基于下方【检索资料】）：\n"
                + EntityExtractor._format_history(history)
                + "\n"
            )
        user_content = (
            hist_block
            + f"用户问题：{question}\n"
            + f"抽取实体：{json.dumps(entities, ensure_ascii=False)}\n"
            + ("补充追问信息：" + follow_question + "\n" if follow_question else "")
            + "【检索资料】\n"
            + "\n\n".join(ctx_lines)
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    async def _iterative_refine(
        self, question: str, entities: dict, items: list, request_id: str
    ) -> tuple[list, list]:
        """
        迭代检索：LLM 判定资料充分性，不足则用缺失点作为定向查询再检索，最多 N 轮。
        每轮把新命中并入 items（按 (doc_uuid, paragraph_id) 去重），超过上限按融合分截断。
        返回 (items, rounds_info)。
        """
        seen = {
            (it.doc_uuid, it.paragraph_id) if it.paragraph_id else (it.doc_uuid, it.chapter_id)
            for it in items
        }
        rounds: list[dict] = []
        for r in range(1, settings.ITERATIVE_MAX_ROUNDS + 1):
            verdict = await self.gate.judge(question, items, entities, f"{request_id}/iter{r}")
            missing = verdict.get("missing") or []
            if verdict.get("sufficient") or not missing:
                rounds.append({"round": r, "sufficient": True, "missing": [], "added": 0})
                break
            # 缺失点直接作为定向查询（已是资料语言短语），单轮多路检索
            try:
                new_items = await self.dah_rag.multi_search(
                    missing[:3], request_id=f"{request_id}/iter{r}",
                    country=(entities or {}).get("country", ""),
                )
            except Exception as e:
                logger.warning(f"[{request_id}] 迭代检索第{r}轮失败，终止迭代 | error={e}")
                rounds.append({"round": r, "sufficient": False, "missing": missing, "added": 0, "error": str(e)})
                break
            added = 0
            for it in new_items:
                key = (it.doc_uuid, it.paragraph_id) if it.paragraph_id else (it.doc_uuid, it.chapter_id)
                if key in seen:
                    continue
                seen.add(key)
                items.append(it)
                added += 1
            rounds.append({"round": r, "sufficient": False, "missing": missing, "added": added})
            if added == 0:
                # 补查无新内容，再迭代无益
                break
            # 超上限按融合分截断，保核心
            if len(items) > settings.DECOMPOSE_MERGE_LIMIT:
                items = sorted(items, key=lambda x: -x.fused_score)[:settings.DECOMPOSE_MERGE_LIMIT]
        return items, rounds

    async def _stream_reason(self, messages: list[dict], request_id: str, degraded: bool) -> AsyncGenerator[tuple[str, str], None]:
        """
        多章节推理：优先 OpenAI 兼容接口 token 流式；失败降级整段生成。
        yield ("think", 思考片段) 或 ("answer", 正文片段)。
        """
        if degraded:
            # 高峰期：跳过 token 流，直接整段生成，降低连接开销
            try:
                full = await asyncio.to_thread(self.llm.chat, messages, 0.3, 32768, "medium", no_think=True)
                yield "answer", (full or "")
            except Exception as e:
                logger.error(f"[{request_id}] 降级整段生成失败 | error={e}", exc_info=True)
                yield "answer", MSG_NO_ANSWER
            return

        content_any = False  # 是否已流式产出正式回答正文（思考内容不算）
        try:
            url = f"{settings.LLM_BASE_URL}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if settings.LLM_API_KEY:
                headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
            payload = {
                "model": settings.LLM_MODEL_NAME,
                "messages": messages,
                "temperature": 0.3,
                # 服务端强制 xhigh（不可调）：18 条上下文时 reasoning 实测 ~9k tokens，
                # 个别问题模型会过度"先写后答"灌满 16k（思考 ~16k → 正文仅剩 ~300 字被腰斩，
                # markdown/mermaid 全部丢失）→ 32768 覆盖最坏 reasoning(16k)+完整回答(3k+)。
                "max_tokens": 32768,
                "reasoning_effort": "medium",
                "stream": True,
            }
            timeout = httpx.Timeout(connect=settings.LLM_TIMEOUT, read=300.0, write=60.0, pool=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data or data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        # Qwen3 思考模式：reasoning_content=思维链，content=正式回答
                        think = delta.get("reasoning_content") or ""
                        content = delta.get("content") or ""
                        if think:
                            yield "think", think
                        if content:
                            content_any = True
                            yield "answer", content
        except Exception as e:
            logger.error(f"[{request_id}] 流式推理失败，降级整段生成 | error={e}", exc_info=True)
        if not content_any:
            # 关键修复：只有"思考"而无"正文"（连接中断/预算耗尽）也必须触发整段兜底，
            # 否则 raw_full 为空 → 落入"暂无规则"30字话术
            logger.warning(f"[{request_id}] 流式未产出正式正文，触发整段生成兜底")
            try:
                full = await asyncio.to_thread(self.llm.chat, messages, 0.3, 16384, "medium", no_think=True)
                yield "answer", (full or "")
            except Exception as e2:
                logger.error(f"[{request_id}] 整段生成兜底失败 | error={e2}", exc_info=True)
                yield "answer", MSG_NO_ANSWER

    async def _strict_recheck(
        self,
        question: str,
        entities: dict,
        contexts: list[dict],
        request_id: str,
        degraded: bool,
        prev_answer: str,
        prev_risk: str,
        history: Optional[list[dict]] = None,
    ) -> tuple[str, str]:
        """校验未通过的兜底重生成（严格模式，仅一次）"""
        if degraded:
            # 降级模式不做二次重生成，保留原回答并在风险中提示
            return prev_answer, (prev_risk + "\n【提示】本回答未通过AI事实校验，请以官方原文为准。").strip(" \n")
        logger.info(f"[{request_id}] 触发严格模式重生成（首轮校验未通过）")
        try:
            messages = self._build_messages(question, entities, contexts, "", history)
            messages[0] = {"role": "system", "content": _STRICT_SYSTEM_PROMPT}
            messages.append({"role": "user", "content": "上一版回答内容与资料存在出入，请严格依据资料重新生成。"})
            # 严格重生成整体限时（RECHECK_TIMEOUT）：xhigh + 18 条上下文 reasoning ~9k，
            # 预算 8192 与主生成对齐，避免重生成被 reasoning 吃光输出空
            raw = await asyncio.wait_for(
                asyncio.to_thread(self.llm.chat, messages, 0.1, 8192, "medium"),
                timeout=settings.RECHECK_TIMEOUT,
            )
            parsed = _extract_json_answer(raw, request_id)
            if parsed and parsed.get("answer"):
                new_answer = str(parsed["answer"]).strip()
                new_risk = str(parsed.get("risk_tips") or "").strip()
                recheck = await asyncio.wait_for(
                    self.checker.check(new_answer, contexts, request_id, degraded=False, lang=self._target_lang(entities)),
                    timeout=max(settings.RECHECK_TIMEOUT, 20.0),
                )
                if recheck["passed"]:
                    logger.info(f"[{request_id}] 严格重生成 通过校验")
                    return new_answer, new_risk
            logger.warning(f"[{request_id}] 严格重生成 仍未通过校验，回退安全兜底")
            # 首轮回答若有实质内容（非30字"暂无规则"话术），保留并加风险提示，
            # 避免"检索有料却只输出无规则话术"的反常识结果
            if prev_answer and prev_answer != MSG_NO_ANSWER and len(prev_answer) > 30:
                return prev_answer, (prev_risk + "\n【提示】本回答未通过AI事实双校验，请以官方原文为准。").strip(" \n")
            return MSG_NO_ANSWER, "检索到的资料无法支撑可靠回答，为避免误导已暂停生成具体法规内容。"
        except Exception as e:
            logger.error(f"[{request_id}] 严格重生成异常，保留原回答 | error={e}", exc_info=True)
            return prev_answer, (prev_risk + "\n【提示】本回答未通过AI事实校验，请以官方原文为准。").strip()

    # ----------------------------------------------------------
    # 溯源与持久化
    # ----------------------------------------------------------
    def _build_sources(self, contexts: list[dict]) -> list[dict]:
        """聚合上下文的完整三级溯源信息：文档 -> 章节 -> 段落(带内容)，供前端树展开"""
        sources, seen = [], set()
        for c in contexts:
            meta = c.get("meta", {})
            doc = meta.get("document", {})
            chapter = meta.get("chapter", {})
            para = meta.get("paragraph", {})
            # 段落主键：段落ID优先，退化到 (doc, chapter)
            pkey = para.get("id") or 0
            key = (doc.get("doc_uuid", ""), chapter.get("id", 0), pkey)
            if key in seen:
                continue
            seen.add(key)

            # 定位/新建该文档及其章节、段落节点
            doc_node = next((d for d in sources if d["doc_uuid"] == doc.get("doc_uuid", "")), None)
            if doc_node is None:
                doc_node = {
                    "doc_title": doc.get("title", ""),
                    "doc_uuid": doc.get("doc_uuid", ""),
                    "source_url": doc.get("source_url", ""),
                    "publish_time": doc.get("publish_time", ""),
                    "effective_time": doc.get("effective_time", ""),
                    "category": doc.get("category", ""),
                    "chapters": [],
                }
                sources.append(doc_node)

            chapter_node = next(
                (ch for ch in doc_node["chapters"] if ch.get("id") == chapter.get("id", 0)),
                None,
            )
            if chapter_node is None:
                chapter_node = {
                    "id": chapter.get("id", 0),
                    "title": chapter.get("title", ""),
                    "path": chapter.get("path", ""),
                    "paragraphs": [],
                }
                doc_node["chapters"].append(chapter_node)

            chapter_node["paragraphs"].append(
                {
                    "id": pkey,
                    "content": c.get("content", "") or c.get("content_raw", ""),
                    "page": para.get("offset_info", ""),
                }
            )
        return sources

    # ----------------------------------------------------------
    # 对话驱动知识库扩充（URL直给 / 国家自动搜索 / 确认入库 / 进度查询）
    # ----------------------------------------------------------
    def _meta_answer(self, text: str, request_id: str, session_id: str) -> dict:
        """知识库扩充交互的统一应答结构（level=0 标识元交互，不走生成链路）"""
        return {
            "answer": text,
            "risk_tips": "",
            "sources": [],
            "doc_count": 0,
            "segment_count": 0,
            "level": 0,
            "follow_question": "",
            "request_id": request_id,
            "session_id": session_id,
        }

    async def _handle_ingest_intent(self, question: str, entities: dict, session_id: str, request_id: str) -> Optional[dict]:
        """
        意图分支入口：命中入库交互返回应答 dict，否则返回 None（继续正常 RAG 管道）
        """
        intent = entities.get("intent", "normal")
        urls = extract_urls(question)
        country = entities.get("country", "")

        # 1) URL 直给（最高优先：消息里出现 URL 即触发，防 LLM 意图误判漏触发）
        if urls and intent not in ("ingest_confirm", "ingest_status"):
            url = urls[0]
            ok, why = kb_ingest_service.validate_url(url)
            if not ok:
                return self._meta_answer(
                    f"该链接暂不支持自动入库（{why}）。请提供可公开访问的 http/https 官方文档链接。",
                    request_id, session_id,
                )
            if await asyncio.to_thread(kb_ingest_service.has_running, session_id):
                return self._meta_answer(
                    "已有知识库入库任务正在进行中，请稍后问我“入库进度”。",
                    request_id, session_id,
                )
            task_id = await asyncio.to_thread(
                kb_ingest_service.create_task, session_id, "url", country, url[:500], None, "running"
            )
            asyncio.create_task(self._bg_ingest_url(url, country, task_id))
            logger.info(f"[{request_id}] 对话触发URL入库 | task_id={task_id} | url={url[:80]} | country={country}")
            return self._meta_answer(
                "已收到官方文档链接，开始自动解析入库（后台处理，预计 1~3 分钟）。\n"
                "完成后即可继续提问；回复“入库进度”可随时查看进展。",
                request_id, session_id,
            )

        # 2) 国家自动搜索（"加入法国政策" / "自动抓取德国"）
        if intent == "ingest_country":
            c = norm_country(country)
            if not c:
                return self._meta_answer(
                    "请告诉我要扩充哪个国家的政策知识库（例如：自动抓取法国）。",
                    request_id, session_id,
                )
            sources, _origin = await self._ingest_prepare_country(c, session_id, request_id)
            if not sources:
                return self._meta_answer(
                    f"暂时无法自动搜索到{c}的官方来源。您可以直接发送具体官方文档链接，我立即解析入库。",
                    request_id, session_id,
                )
            list_text = "\n".join(f"{i}. {s['name']} {s['url']}" for i, s in enumerate(sources, 1))
            result = self._meta_answer(
                f"已找到以下{c}官方政策来源：\n{list_text}\n"
                f"回复“确认抓取”即开始自动抓取入库（预计 1~3 分钟）；\n"
                f"也可直接发具体文档链接给我，我立即解析入库。",
                request_id, session_id,
            )
            result["actions"] = [
                {"type": "confirm_ingest", "label": "确认入库", "primary": True, "country": c},
                {"type": "decline", "label": "取消", "country": c},
            ]
            return result

        # 3) 确认抓取（三层取值：pending任务 → 上轮展示过列表+注册表 → 不命中则回落正常管道）
        if intent == "ingest_confirm":
            resolved = await self._ingest_resolve_confirm(session_id)
            if resolved is None:
                return None
            task_id, sources, c = resolved
            await kb_ingest_service.update_task(task_id, status="running", progress="开始入库...")
            asyncio.create_task(self._bg_ingest_sources(sources, c, task_id))
            logger.info(f"[{request_id}] 对话确认抓取 | task_id={task_id} | country={c} | sources={len(sources)}")
            result = self._meta_answer(
                f"已开始后台抓取入库 {len(sources)} 个官方来源（{c}），预计 1~3 分钟。\n"
                f"进度会自动刷新，回复“入库进度”也可随时查看。",
                request_id, session_id,
            )
            result["ingest_task_id"] = task_id
            result["ingest_status"] = "running"
            result["ingest_polling"] = True
            return result

        # 4) 进度查询（与前端按钮轮询共用应答结构）
        if intent == "ingest_status":
            t = await kb_ingest_service.get_latest_task(session_id=session_id)
            if t is None:
                return self._meta_answer(
                    "当前没有知识库入库任务。您可以直接发送官方文档链接，"
                    "或说“自动抓取+国家名”（如：自动抓取法国）触发知识库扩充。",
                    request_id, session_id,
                )
            return self._ingest_progress_answer(t, request_id, session_id)

        return None

    async def _ingest_prepare_country(self, country: str, session_id: str, request_id: str) -> tuple[list, str]:
        """
        国家官方源准备：pending任务缓存(TTL) → 注册表 → AI 搜索
        :return: (sources[{name,url,category}], origin: cache/registry/ai)
        """
        from config.constants import OFFICIAL_SOURCES_BY_COUNTRY

        # 1) 复用同 session 未过期 pending 任务（24h 内不重复搜索）
        t = await kb_ingest_service.get_latest_task(
            session_id=session_id, status="pending_confirm", country=country,
            ttl_hours=settings.INGEST_PENDING_TTL_HOURS,
        )
        if t and t.get("payload"):
            return t["payload"], "cache"

        # 2) 注册表命中（curated 官方源，零 LLM 耗时）
        reg = OFFICIAL_SOURCES_BY_COUNTRY.get(country)
        if reg:
            payload = [
                {"name": v["name"], "url": v["url"], "category": v["category"]}
                for v in reg.values()
            ]
            await asyncio.to_thread(
                kb_ingest_service.create_task, session_id, "country", country,
                f"{country}官方源(注册表)", payload, "pending_confirm",
            )
            return payload, "registry"

        # 3) AI 搜索（未注册国家；失败返回空，调用方降级为"仅直给链接"）
        payload = await kb_ingest_service.discover_official_sources(country)
        if payload:
            await asyncio.to_thread(
                kb_ingest_service.create_task, session_id, "country", country,
                f"{country}官方源(AI搜索)", payload, "pending_confirm",
            )
        return payload, "ai"

    async def _ingest_gap_guidance(self, country: str, session_id: str, request_id: str) -> tuple[str, list[dict]]:
        """
        L4 知识库缺口引导（该国 KB 零文档）：
        不预搜索，直接给按钮——用户点"AI 自动搜索官网并入库"后再执行搜索/列表/确认流程；
        点"不需要"则记 declined（TTL 内不再提示）。
        :return: (引导文案, actions 按钮列表)
        """
        text = (
            f"知识库暂无{country}规则，目前还没有该国的政策数据。\n"
            f"您可以点击下方按钮，让 AI 自动搜索{country}官方网站并入库；\n"
            f"也可以直接发送该国官方政策文档链接，我立即解析入库。"
        )
        actions = [
            {"type": "ai_search_ingest", "label": "AI 自动搜索官网并入库", "primary": True, "country": country},
            {"type": "decline", "label": "不需要", "country": country},
        ]
        return text, actions

    async def _handle_ingest_action(
        self, action: str, action_data: dict, question: str, session_id: str, request_id: str
    ) -> Optional[dict]:
        """
        前端交互按钮的确定性处理（零 LLM 耗时，不受意图识别误判影响）：
        - ai_search_ingest  AI 搜索该国官方源 → 列表 + [确认入库|取消] 按钮
        - confirm_ingest    确认后后台逐站入库（应答携带 ingest_task_id 供前端自动轮询）
        - decline           用户拒绝 → 记 declined（TTL 内 L4 缺口不再提示，流程结束）
        - ingest_progress   进度轮询（应答携带 ingest_status: running/done/failed 供前端停止轮询）
        返回 None 表示无法处理（回落正常管道）。
        """
        country = norm_country(str(action_data.get("country") or ""))

        if action == "ai_search_ingest":
            if not country:
                return self._meta_answer(
                    "请告诉我要扩充哪个国家的政策知识库（例如：自动抓取法国）。",
                    request_id, session_id,
                )
            if await asyncio.to_thread(kb_ingest_service.has_running, session_id):
                return self._meta_answer(
                    "已有知识库入库任务正在进行中，请稍后问我“入库进度”。",
                    request_id, session_id,
                )
            sources, _origin = await self._ingest_prepare_country(country, session_id, request_id)
            if not sources:
                return self._meta_answer(
                    f"暂时无法自动搜索到{country}的官方来源。您可以直接发送具体官方文档链接，我立即解析入库。",
                    request_id, session_id,
                )
            list_text = "\n".join(f"{i}. {s['name']} {s['url']}" for i, s in enumerate(sources, 1))
            result = self._meta_answer(
                f"已找到以下{country}官方政策来源：\n{list_text}\n"
                f"点击“确认入库”即开始自动抓取入库（预计 1~3 分钟）。",
                request_id, session_id,
            )
            result["actions"] = [
                {"type": "confirm_ingest", "label": "确认入库", "primary": True, "country": country},
                {"type": "decline", "label": "取消", "country": country},
            ]
            logger.info(f"[{request_id}] 按钮动作: AI搜索官方源 | country={country} | sources={len(sources)}")
            return result

        if action == "confirm_ingest":
            resolved = await self._ingest_resolve_confirm(session_id)
            if resolved is None:
                return self._meta_answer(
                    "没有找到待确认的入库任务。请重新触发：问我该国的政策问题或说“自动抓取+国家名”。",
                    request_id, session_id,
                )
            task_id, sources, c = resolved
            await kb_ingest_service.update_task(task_id, status="running", progress="开始入库...")
            asyncio.create_task(self._bg_ingest_sources(sources, c, task_id))
            logger.info(f"[{request_id}] 按钮动作: 确认入库 | task_id={task_id} | country={c} | sources={len(sources)}")
            result = self._meta_answer(
                f"已开始后台抓取入库 {len(sources)} 个官方来源（{c}），预计 1~3 分钟。\n"
                f"进度会自动刷新，完成后可直接提问{c}相关政策。",
                request_id, session_id,
            )
            result["ingest_task_id"] = task_id
            result["ingest_status"] = "running"
            result["ingest_polling"] = True
            return result

        if action == "decline":
            if country:
                await asyncio.to_thread(kb_ingest_service.mark_declined, session_id, country)
                logger.info(f"[{request_id}] 按钮动作: 用户拒绝扩充 | country={country}")
                return self._meta_answer(
                    f"好的，暂不扩充{country}知识库。之后如需，您随时可以发送该国官方文档链接，我立即解析入库。",
                    request_id, session_id,
                )
            return self._meta_answer("好的，暂不继续知识库扩充。", request_id, session_id)

        if action == "ingest_progress":
            task_id = int(action_data.get("task_id") or 0)
            if task_id:
                t = await kb_ingest_service.get_task_by_id(task_id)
            else:
                t = await kb_ingest_service.get_latest_task(session_id=session_id)
            if t is None:
                return self._meta_answer(
                    "当前没有知识库入库任务。您可以直接发送官方文档链接，"
                    "或说“自动抓取+国家名”（如：自动抓取法国）触发知识库扩充。",
                    request_id, session_id,
                )
            return self._ingest_progress_answer(t, request_id, session_id)

        return None

    def _ingest_progress_answer(self, t: dict, request_id: str, session_id: str) -> dict:
        """入库进度应答（文本意图"入库进度"与前端轮询共用）；ingest_status 供前端决定停止轮询"""
        status = t["status"]
        if status == "pending_confirm":
            lines = "\n".join(f"{i}. {s['name']} {s['url']}" for i, s in enumerate(t.get("payload") or [], 1))
            result = self._meta_answer(
                f"以下是{t['country']}官方来源待确认列表：\n{lines}\n"
                f"回复“确认抓取”即开始自动抓取入库。",
                request_id, session_id,
            )
            result["ingest_status"] = "running"
            result["ingest_task_id"] = t["id"]
            return result
        if status == "running":
            result = self._meta_answer(
                f"知识库入库进行中（{t['target'][:50]}）：{t['progress'] or '处理中'}",
                request_id, session_id,
            )
            result["ingest_status"] = "running"
            result["ingest_task_id"] = t["id"]
            return result
        if status == "done":
            extra = f"\n（部分来源失败：{(t.get('error') or '')[:100]}）" if t.get("error") else ""
            result = self._meta_answer(
                f"✅ 入库完成：成功入库 {t['doc_count']} 个文档，{t['country']} 分区已生效，"
                f"现在可以直接提问{t['country']}相关政策。{extra}",
                request_id, session_id,
            )
            result["ingest_status"] = "done"
            result["ingest_task_id"] = t["id"]
            return result
        result = self._meta_answer(
            f"入库任务失败：{(t.get('error') or '未知错误')[:120]}\n"
            f"您可以重新发送链接，或说“自动抓取+国家名”再次触发。",
            request_id, session_id,
        )
        result["ingest_status"] = "failed"
        result["ingest_task_id"] = t["id"]
        return result

    async def _ingest_resolve_confirm(self, session_id: str) -> Optional[tuple]:
        """
        “确认抓取”取值：
        a) session 有未过期 pending 任务 → 用其 payload
        b) 上一轮回答展示过“确认抓取”列表且该国注册表有源 → 直接构建（防"确认"误触发：必须有展示证据）
        c) 都不满足 → None（回落正常管道）
        :return: (task_id, sources, country) | None
        """
        from config.constants import OFFICIAL_SOURCES_BY_COUNTRY

        t = await kb_ingest_service.get_latest_task(
            session_id=session_id, status="pending_confirm",
            ttl_hours=settings.INGEST_PENDING_TTL_HOURS,
        )
        if t and t.get("payload"):
            return t["id"], t["payload"], t["country"]

        # b) 上轮展示证据 + 注册表
        def _last_turn():
            if SessionLocal is None or ChatConversation is None:
                return None
            with SessionLocal() as db:
                row = (
                    db.query(ChatConversation.response, ChatConversation.entities_json)
                    .filter(ChatConversation.session_id == session_id)
                    .order_by(ChatConversation.id.desc())
                    .first()
                )
                if row is None:
                    return None
                return (row[0] or "", row[1] or "")

        last = await asyncio.to_thread(_last_turn)
        if last and "确认抓取" in (last[0] or ""):
            ent = {}
            if last[1]:
                try:
                    ent = json.loads(last[1]) or {}
                except (json.JSONDecodeError, TypeError):
                    ent = {}
            c = norm_country(ent.get("country", ""))
            reg = OFFICIAL_SOURCES_BY_COUNTRY.get(c) if c else None
            if reg:
                payload = [
                    {"name": v["name"], "url": v["url"], "category": v["category"]}
                    for v in reg.values()
                ]
                task_id = await asyncio.to_thread(
                    kb_ingest_service.create_task, session_id, "country", c,
                    f"{c}官方源(注册表)", payload, "running",
                )
                return task_id, payload, c
        return None

    async def _bg_ingest_url(self, url: str, country: str, task_id: int):
        """后台任务：单 URL 入库"""
        try:
            await kb_ingest_service.update_task(task_id, progress="下载与解析中...")
            r = await kb_ingest_service.ingest_from_url(url, country, task_id=task_id)
            if r["status"] in ("ingested", "skipped_duplicated"):
                await kb_ingest_service.update_task(
                    task_id, status="done", doc_count=1, doc_uuids=[r["doc_uuid"]],
                    progress="入库完成",
                )
            else:
                await kb_ingest_service.update_task(task_id, status="failed", error=r.get("error", ""))
        except Exception as e:
            logger.error(f"后台URL入库异常 | task_id={task_id} | error={e}", exc_info=True)
            try:
                await kb_ingest_service.update_task(task_id, status="failed", error=str(e))
            except Exception:
                pass

    async def _bg_ingest_sources(self, sources: list[dict], country: str, task_id: int):
        """后台任务：官方源列表逐站入库"""
        try:
            r = await kb_ingest_service.ingest_sources(sources, country, task_id=task_id)
            n = r["ingested"] + r["duplicated"]
            failed_note = "; ".join(f"{f['name']}:{f['error'][:40]}" for f in r["failed"][:3])
            if n == 0:
                await kb_ingest_service.update_task(
                    task_id, status="failed", doc_count=0,
                    error=failed_note or "全部来源入库失败",
                )
            else:
                await kb_ingest_service.update_task(
                    task_id, status="done", doc_count=n, doc_uuids=r["doc_uuids"],
                    progress="入库完成",
                )
                if failed_note:
                    logger.warning(f"入库完成但部分失败 | task_id={task_id} | {failed_note}")
        except Exception as e:
            logger.error(f"后台来源入库异常 | task_id={task_id} | error={e}", exc_info=True)
            try:
                await kb_ingest_service.update_task(task_id, status="failed", error=str(e))
            except Exception:
                pass

    async def _persist(self, session_id: str, question: str, result: dict, entities: Optional[dict] = None):
        """对话落库（ChatConversation），失败仅告警不阻塞请求；同步落库改写问题与已确认实体供下轮继承"""
        if SessionLocal is None or ChatConversation is None:
            return
        entities = entities or {}
        ent_summary = {
            k: entities.get(k, "")
            for k in ("product", "country", "hs_code", "trade_term", "category")
        }
        rewritten = entities.get("rewritten_query", "") if entities.get("is_followup") else ""
        try:
            def _write():
                with SessionLocal() as db:
                    db.add(
                        ChatConversation(
                            session_id=session_id,
                            query=question,
                            response=result.get("answer", ""),
                            source_list=json.dumps(result.get("sources", []), ensure_ascii=False),
                            risk_tips=result.get("risk_tips", ""),
                            elapsed_ms=result.get("elapsed_ms", 0),
                            rewritten_query=rewritten or None,
                            entities_json=json.dumps(ent_summary, ensure_ascii=False),
                        )
                    )
                    db.commit()
            await asyncio.to_thread(_write)
            logger.info(f"对话记录已落库 | session_id={session_id} | query_len={len(question)} | is_followup={bool(rewritten)}")
        except Exception as e:
            logger.error(f"对话记录落库失败 | session_id={session_id} | error={e}", exc_info=True)

    async def _load_history(self, session_id: str) -> list[dict]:
        """加载最近 N 轮历史（旧→新）：问题 + 已确认实体摘要；DB 异常返回空列表（不阻塞主链路）"""
        if not session_id or SessionLocal is None or ChatConversation is None:
            return []
        limit = max(1, settings.HISTORY_MAX_TURNS)
        try:
            def _read():
                with SessionLocal() as db:
                    rows = (
                        db.query(ChatConversation.query, ChatConversation.rewritten_query, ChatConversation.entities_json)
                        .filter(ChatConversation.session_id == session_id)
                        .order_by(ChatConversation.create_time.desc(), ChatConversation.id.desc())
                        .limit(limit)
                        .all()
                    )
                    return [
                        (
                            r[0] or "",
                            r[1] or "",
                            r[2] or "",
                        )
                        for r in rows
                    ]
            rows = await asyncio.to_thread(_read)
            history = []
            for q, _rw, ent_json in reversed(rows):  # 反转为旧→新
                entities = {}
                if ent_json:
                    try:
                        entities = json.loads(ent_json) or {}
                    except (json.JSONDecodeError, TypeError):
                        entities = {}
                history.append({"query": q, "entities": entities})
            return history
        except Exception as e:
            logger.warning(f"历史加载失败，按单轮处理 | session_id={session_id} | error={e}")
            return []


# 全局单例（供 api/chat.py 使用）
chat_flow = ChatFlow()