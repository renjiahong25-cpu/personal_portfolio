# -*- coding: utf-8 -*-
"""
问答 API 路由
=====================================================
接口清单：
    POST /api/chat/query    智能问答流式接口（SSE StreamingResponse）
    POST /api/chat/feedback 用户反馈/报错提交
    GET  /api/chat/history  历史对话获取

统一异常处理、参数校验、请求日志。
"""
import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from config.logging_config import get_logger
from config import settings
from config.constants import CODE_PARAM_ERROR, CODE_SERVER_ERROR
from core.middleware import generate_request_id
from schemas.schemas import ChatQueryReq, ChatFeedbackReq, CommonResp
from service.chat_service.chat_flow import chat_flow
from service.cache_service import (
    chat_semantic_cache, chat_coalescer, rate_limiter, cache_key,
)

logger = get_logger("api.chat")

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_FEEDBACK_TYPES = {0, 1, 2}  # 0=无 1=有用 2=报错


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_error(message: str, request_id: str) -> str:
    return f"event: error\ndata: {json.dumps({'message': message, 'request_id': request_id}, ensure_ascii=False)}\n\n"


def _parse_sse(frame: str):
    """解析 SSE 文本帧 → {event, data}，用于语义缓存收集 answer/done 帧。"""
    event = ""
    data_parts = []
    for line in frame.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].lstrip())
    if not data_parts:
        return None
    try:
        payload = json.loads("".join(data_parts))
    except Exception:
        payload = {"raw": "".join(data_parts)}
    return {"event": event, "data": payload}


def _normalize_question(q: str) -> str:
    return " ".join(q.strip().lower().split())


async def _replay_cached(cached: dict, request_id: str):
    """语义缓存命中回放：start → answer → done（与完整管道事件契约一致）。"""
    sess = str(cached.get("session_id") or "")
    yield _sse_frame("start", {"request_id": request_id, "session_id": sess,
                               "degraded": False, "cached": True})
    if cached.get("answer"):
        yield _sse_frame("answer", cached["answer"])
    if cached.get("done"):
        yield _sse_frame("done", cached["done"])


async def _stream_semantic_cached(
    question: str, session_id: str, request_id: str, action: str = "", action_data: Optional[dict] = None,
):
    """语义共享缓存流式路径（P3）：
    - 命中：直接回放 start→answer→done（零 LLM，压测缓存命中路径目标 ≥100 req/s）
    - miss：进程内闸 + Redis 单飞只让一个实例跑完整管道，其余轮询等首发结果
    """
    nkey = cache_key("sem", _normalize_question(question))
    async with chat_coalescer.gate(nkey):
        hit = chat_semantic_cache.get(nkey)
        if hit is not None:
            logger.info(f"[{request_id}] 语义缓存命中 | key={nkey[:12]}")
            async for frame in _replay_cached(hit, request_id):
                yield frame
            return
        owner = await asyncio.to_thread(chat_semantic_cache.claim, nkey, 60)
        if not owner:
            chat_semantic_cache.counters["singleflight_wait"] += 1
            deadline = asyncio.get_running_loop().time() + settings.SINGLEFLIGHT_WAIT_SEC
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
                hit = chat_semantic_cache.get(nkey)
                if hit is not None:
                    logger.info(f"[{request_id}] 单飞等待命中首发结果 | key={nkey[:12]}")
                    async for frame in _replay_cached(hit, request_id):
                        yield frame
                    return
        chat_semantic_cache.counters["singleflight_compute"] += 1
        sess = session_id or ""
        answer_payload = None
        done_payload = None
        async for frame in chat_flow.stream(question, session_id, request_id,
                                            action=action, action_data=action_data or {}):
            parsed = _parse_sse(frame)
            if parsed:
                if parsed["event"] == "answer":
                    answer_payload = parsed["data"]
                    sid = str(parsed["data"].get("session_id") or "").strip()
                    if sid:
                        sess = sid
                elif parsed["event"] == "done":
                    done_payload = parsed["data"]
            yield frame
        if answer_payload is not None:
            chat_semantic_cache.set(nkey, {"session_id": sess, "answer": answer_payload,
                                           "done": done_payload, "ts": time.time()})
            logger.info(f"[{request_id}] 语义答案已写入共享缓存 | key={nkey[:12]}")


@router.post("/query")
async def chat_query(req: ChatQueryReq, request: Request):
    """
    智能问答流式接口（SSE）
    - 事件流：start → entities → hyde → retrieve → compress → reasoning*(流式内容) → check → answer → done
    - 幂等：客户端可通过 request_id 去重（相同 request_id 并发/重复请求复用结果）
    - P3：纯问答（无 action）走语义共享缓存 + 单飞合并 + 每 IP 限流
    """
    if rate_limiter.enabled:
        ip = request.client.host if request.client else ""
        if rate_limiter.over_limit(ip):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    start = time.time()
    question = (req.question or "").strip()
    # 参数校验
    if not question:
        raise HTTPException(status_code=CODE_PARAM_ERROR, detail="问题不能为空")
    if len(question) > settings.MAX_QUESTION_LEN:
        raise HTTPException(
            status_code=CODE_PARAM_ERROR,
            detail=f"问题过长，请控制在 {settings.MAX_QUESTION_LEN} 字以内",
        )
    request_id = req.request_id or generate_request_id()
    action = (req.action or "").strip()
    action_data = req.action_data or {}
    logger.info(
        f"[{request_id}] 收到问答请求 | session_id={req.session_id or '-'} | question_len={len(question)} | "
        f"action={action or '-'} | question_head={question[:60]}"
    )

    async def event_gen():
        try:
            if not action and not action_data and settings.CACHE_ENABLE:
                async for frame in _stream_semantic_cached(
                    question, req.session_id, request_id, action, action_data):
                    yield frame
                return
            async for frame in chat_flow.stream(question, req.session_id, request_id,
                                                action=action, action_data=action_data):
                yield frame
        except Exception as e:
            logger.error(f"[{request_id}] 问答流式处理异常 | error={e}", exc_info=True)
            yield _sse_error("服务异常，请稍后重试", request_id)

    logger.info(f"[{request_id}] 问答请求受理耗时={round(time.time()-start,3)}s，开始流式响应")
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/feedback")
async def chat_feedback(req: ChatFeedbackReq):
    """用户反馈/报错提交"""
    start = time.time()
    if req.feedback_type not in _FEEDBACK_TYPES:
        raise HTTPException(status_code=CODE_PARAM_ERROR, detail="feedback_type 必须为 0/1/2")
    logger.info(
        f"收到反馈 | session_id={req.session_id or '-'} | type={req.feedback_type} | "
        f"query_len={len(req.query or '')} | bad_reason={ (req.bad_reason or '')[:80] }"
    )
    try:
        from db.models.base import SessionLocal, ChatFeedback

        def _write():
            with SessionLocal() as db:
                db.add(
                    ChatFeedback(
                        session_id=req.session_id,
                        query=req.query,
                        response=req.response,
                        feedback_type=req.feedback_type,
                        bad_reason=req.bad_reason,
                    )
                )
                db.commit()

        await asyncio.to_thread(_write)
    except Exception as e:
        logger.error(f"反馈落库失败 | error={e}", exc_info=True)
        raise HTTPException(status_code=CODE_SERVER_ERROR, detail="反馈保存失败")
    logger.info(f"反馈落库完成 | elapsed={round(time.time()-start,3)}s")
    return CommonResp(code=200, msg="反馈已记录")


@router.get("/history")
async def chat_history(
    session_id: str = Query(default="", description="会话ID（必填）"),
    limit: int = Query(default=20, ge=1, le=100, description="返回条数 1~100"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
):
    """历史对话获取（按时间倒序）"""
    if not session_id:
        raise HTTPException(status_code=CODE_PARAM_ERROR, detail="session_id 不能为空")
    logger.info(f"查询历史对话 | session_id={session_id} | limit={limit} | offset={offset}")
    try:
        from db.models.base import SessionLocal, ChatConversation

        def _fetch():
            with SessionLocal() as db:
                rows = (
                    db.query(ChatConversation)
                    .filter(ChatConversation.session_id == session_id)
                    .order_by(ChatConversation.create_time.desc())
                    .offset(offset)
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": r.id,
                        "session_id": r.session_id,
                        "query": r.query,
                        "response": r.response,
                        "source_list": json.loads(r.source_list) if r.source_list else [],
                        "risk_tips": r.risk_tips or "",
                        "elapsed_ms": r.elapsed_ms,
                        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
                    }
                    for r in rows
                ]

        data = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"历史对话查询失败 | error={e}", exc_info=True)
        raise HTTPException(status_code=CODE_SERVER_ERROR, detail="历史查询失败")
    logger.info(f"历史对话查询完成 | count={len(data)}")
    return CommonResp(code=200, msg="操作成功", data=data)