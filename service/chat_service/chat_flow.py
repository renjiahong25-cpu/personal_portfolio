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
from schemas.schemas import ChatQueryReq
from .entity_extractor import EntityExtractor
from .hyde_generator import HyDEGenerator
from .retriever import HybridRetriever
from .content_compressor import ContentCompressor
from .fact_checker import FactChecker

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
    "2. 若【检索资料】不足以完整回答，必须明确说明："
    "“当前知识库暂无对应合规规则，建议咨询专业清关师或查阅官方网站”，不得强行作答；\n"
    "3. 回答使用中文，分点清晰，涉及具体条款须标注来源文档/章节，便于溯源；\n"
    "4. 结合多条资料进行跨章节聚合推理，整合碎片化规则，输出完整闭环答案；\n"
    "5. 给出必要的风险提示（运输限制、认证前置、申报时效等），无风险点则为空字符串；\n"
    "6. 只输出一个JSON对象，格式：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}"
)

_STRICT_SYSTEM_PROMPT = _SYSTEM_PROMPT.replace(
    "6. 只输出一个JSON对象，格式：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}",
    "6. 上次生成内容经事实校验未通过（可能存在编造），本次必须逐字核对【检索资料】，"
    "只引用资料原文中确实存在的条款与数值，剔除所有资料中找不到依据的内容，重新完整生成。"
    "只输出JSON：{\"answer\":\"完整回答\",\"risk_tips\":\"风险提示\"}",
)


def _sse_frame(event: str, data: dict) -> str:
    """构造标准 SSE 数据帧"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_RECONTEXT_LIMIT = 1200  # 单条上下文进入模型的最大字符数


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
    logger.warning(f"[{request_id}] 回答 JSON 解析失败，回退整段文本 | raw_head={text[:120]}")
    return None


class ChatFlow:
    """问答工作流主编排器（单例）"""

    def __init__(self):
        self.llm = llm_client
        self.extractor = EntityExtractor()
        self.hyde = HyDEGenerator()
        self.retriever = HybridRetriever()
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
    async def stream(self, question: str, session_id: str, request_id: str) -> AsyncGenerator[str, None]:
        """
        问答流式入口：按 request_id 幂等去重，输出 SSE 事件帧
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
            async for frame in self._pipeline(question, session_id, request_id):
                yield frame
        finally:
            self._complete(request_id, fut)

    # ----------------------------------------------------------
    # 核心管道
    # ----------------------------------------------------------
    async def _pipeline(self, question: str, session_id: str, request_id: str) -> AsyncGenerator[str, None]:
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

            # ---------- 步骤1：实体抽取 ----------
            t = time.time()
            entities = await self.extractor.extract(question, request_id)
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

            # ---------- 步骤2：HyDE 增强（可降级） ----------
            t = time.time()
            hyde_doc = None
            if settings.HYDE_ENABLE and not degraded:
                hyde_doc = await self.hyde.generate(question, entities, request_id)
            step_ctx["hyde"] = hyde_doc
            logger.info(f"[{request_id}] 步骤2[HyDE增强] 完成 | enabled={bool(hyde_doc)} | elapsed={round(time.time()-t,3)}s")
            yield _sse_frame(
                "hyde",
                {"request_id": request_id, "enabled": bool(hyde_doc), "len": len(hyde_doc or "")},
            )

            # ---------- 步骤3：混合检索 + 四层兜底 ----------
            t = time.time()
            retrieval = await self.retriever.retrieve_with_fallback(question, entities, hyde_doc, request_id)
            level = retrieval.get("level", 4)
            items = retrieval.get("items", [])
            follow_question = retrieval.get("follow_question", "")
            r_elapsed = round(time.time() - t, 3)
            logger.info(
                f"[{request_id}] 步骤3[混合检索] 完成 | level={level} | count={len(items)} | elapsed={r_elapsed}s"
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
                elapsed_ms = int((time.time() - t_total) * 1000)
                result["elapsed_ms"] = elapsed_ms
                await self._persist(session_id, question, result)
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
                await self._persist(session_id, question, result)
                answer_frame = _sse_frame("answer", result)
                done_frame = _sse_frame("done", {"request_id": request_id, "elapsed_ms": elapsed_ms})
                self._cache_put(request_id, result, [answer_frame, done_frame])
                yield answer_frame
                yield done_frame
                logger.info(f"[{request_id}] 管道结束(无答案) | total={elapsed_ms}ms")
                return

            # ---------- 步骤5：多章节推理（跨上下文聚合生成，token 级流式） ----------
            t = time.time()
            messages = self._build_messages(question, entities, contexts, follow_question)
            raw_full = ""
            async for delta in self._stream_reason(messages, request_id, degraded):
                if delta:
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
            check = await self.checker.check(answer_text, contexts, request_id, degraded=degraded)
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
                answer_text, risk_tips = await self._strict_recheck(
                    question, entities, contexts, request_id, degraded, answer_text, risk_tips
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
            await self._persist(session_id, question, result)
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
    def _build_messages(self, question: str, entities: dict, contexts: list[dict], follow_question: str) -> list[dict]:
        """构造多章节推理输入：问题 + 实体 + 编号上下文（含三级元数据）"""
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
        user_content = (
            f"用户问题：{question}\n"
            f"抽取实体：{json.dumps(entities, ensure_ascii=False)}\n"
            f"{'补充追问信息：' + follow_question + '\n' if follow_question else ''}"
            f"【检索资料】\n" + "\n\n".join(ctx_lines)
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    async def _stream_reason(self, messages: list[dict], request_id: str, degraded: bool) -> AsyncGenerator[str, None]:
        """
        多章节推理：优先 OpenAI 兼容接口 token 流式；失败降级整段生成。
        """
        if degraded:
            # 高峰期：跳过 token 流，直接整段生成，降低连接开销
            try:
                full = await asyncio.to_thread(self.llm.chat, messages, 0.3, 2048)
                yield full or ""
            except Exception as e:
                logger.error(f"[{request_id}] 降级整段生成失败 | error={e}", exc_info=True)
                yield MSG_NO_ANSWER
            return

        streamed_any = False
        try:
            url = f"{settings.LLM_BASE_URL}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if settings.LLM_API_KEY:
                headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
            payload = {
                "model": settings.LLM_MODEL_NAME,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 2048,
                "stream": True,
            }
            timeout = httpx.Timeout(connect=settings.LLM_TIMEOUT, read=180.0, write=60.0, pool=30.0)
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
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            streamed_any = True
                            yield delta
        except Exception as e:
            logger.error(f"[{request_id}] 流式推理失败，降级整段生成 | error={e}", exc_info=True)
        if not streamed_any:
            try:
                full = await asyncio.to_thread(self.llm.chat, messages, 0.3, 2048)
                yield full or ""
            except Exception as e2:
                logger.error(f"[{request_id}] 整段生成兜底失败 | error={e2}", exc_info=True)
                yield MSG_NO_ANSWER

    async def _strict_recheck(
        self,
        question: str,
        entities: dict,
        contexts: list[dict],
        request_id: str,
        degraded: bool,
        prev_answer: str,
        prev_risk: str,
    ) -> tuple[str, str]:
        """校验未通过的兜底重生成（严格模式，仅一次）"""
        if degraded:
            # 降级模式不做二次重生成，保留原回答并在风险中提示
            return prev_answer, (prev_risk + "\n【提示】本回答未通过AI事实校验，请以官方原文为准。").strip(" \n")
        logger.info(f"[{request_id}] 触发严格模式重生成（首轮校验未通过）")
        try:
            messages = self._build_messages(question, entities, contexts, "")
            messages[0] = {"role": "system", "content": _STRICT_SYSTEM_PROMPT}
            messages.append({"role": "user", "content": "上一版回答内容与资料存在出入，请严格依据资料重新生成。"})
            raw = await asyncio.to_thread(self.llm.chat, messages, 0.1, 2048)
            parsed = _extract_json_answer(raw, request_id)
            if parsed and parsed.get("answer"):
                new_answer = str(parsed["answer"]).strip()
                new_risk = str(parsed.get("risk_tips") or "").strip()
                recheck = await self.checker.check(new_answer, contexts, request_id, degraded=False)
                if recheck["passed"]:
                    logger.info(f"[{request_id}] 严格重生成 通过校验")
                    return new_answer, new_risk
            logger.warning(f"[{request_id}] 严格重生成 仍未通过校验，回退安全兜底")
            return MSG_NO_ANSWER, "检索到的资料无法支撑可靠回答，为避免误导已暂停生成具体法规内容。"
        except Exception as e:
            logger.error(f"[{request_id}] 严格重生成异常，保留原回答 | error={e}", exc_info=True)
            return prev_answer, (prev_risk + "\n【提示】本回答未通过AI事实校验，请以官方原文为准。").strip()

    # ----------------------------------------------------------
    # 溯源与持久化
    # ----------------------------------------------------------
    def _build_sources(self, contexts: list[dict]) -> list[dict]:
        """聚合上下文的完整三级溯源信息"""
        sources, seen = [], set()
        for c in contexts:
            meta = c.get("meta", {})
            doc = meta.get("document", {})
            key = (doc.get("doc_uuid", ""), meta.get("chapter", {}).get("id", 0), meta.get("paragraph", {}).get("id", 0))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "doc_title": doc.get("title", ""),
                    "doc_uuid": doc.get("doc_uuid", ""),
                    "source_url": doc.get("source_url", ""),
                    "publish_time": doc.get("publish_time", ""),
                    "effective_time": doc.get("effective_time", ""),
                    "chapter_title": meta.get("chapter", {}).get("title", ""),
                    "paragraph_id": meta.get("paragraph", {}).get("id", 0),
                }
            )
        return sources

    async def _persist(self, session_id: str, question: str, result: dict):
        """对话落库（ChatConversation），失败仅告警不阻塞请求"""
        if SessionLocal is None or ChatConversation is None:
            return
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
                        )
                    )
                    db.commit()
            await asyncio.to_thread(_write)
            logger.info(f"对话记录已落库 | session_id={session_id} | query_len={len(question)}")
        except Exception as e:
            logger.error(f"对话记录落库失败 | session_id={session_id} | error={e}", exc_info=True)


# 全局单例（供 api/chat.py 使用）
chat_flow = ChatFlow()