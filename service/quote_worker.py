# -*- coding: utf-8 -*-
"""报价异步 worker：消费 Redis Stream 排队 job 并回写结果。

启动：python -m service.quote_worker        (compose 服务 srv-quote-worker)
消费组语义：XREADGROUP；进程崩溃未 ACK 的任务留在 pending，组内重投。
单 job 硬超时（QUOTE_JOB_TIMEOUT_SEC）→ 标记 failed 并 ACK 出队（计算线程继续仅写缓存，不落 job）。

运行前提（compose 已注入）：MYSQL_*/REDIS_* 与 deploy srv-quote 一致。
"""
import concurrent.futures
import json
import signal
import threading
import time

from config import settings
from config.logging_config import setup_logging, get_logger
from db.models.base import init_db
from service.cache_service import quote_queue
from service.tariff_service.quote_engine import quote_engine

logger = get_logger("quote_worker")

_stop = threading.Event()

# 跨 consumer 共享的计算线程池（job 超时不取消线程，仅出队；结果只进共享缓存）
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(4, settings.QUOTE_WORKER_CONCURRENCY * 2),
    thread_name_prefix="quote_job")


def _construct_job(body: dict) -> tuple[str, dict]:
    job_id = (body.get("job_id") or "")
    payload_raw = body.get("payload", {})
    if isinstance(payload_raw, str):
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {}
    else:
        payload = payload_raw or {}
    return job_id, payload


def _ack(r, msg_id: str):
    try:
        r.xack(settings.QUOTE_QUEUE_STREAM, settings.QUOTE_QUEUE_GROUP, msg_id)
    except Exception:
        pass


def _requeue(r, msg_id: str, body: dict):
    """Redis 异常未处理成功 → 副本写回流尾 + ACK 原消息，保证不丢且可重投。"""
    logger.warning(f"job 因 Redis 异常转入重投 | msg={msg_id}")
    try:
        r.xadd(settings.QUOTE_QUEUE_STREAM, {k: v for k, v in body.items()},
               maxlen=settings.QUOTE_QUEUE_BACKLOG_MAX * 500, approximate=True)
        _ack(r, msg_id)
    except Exception as e:
        logger.error(f"重投入队失败（消息留在 pending） | msg={msg_id} | {e}")


def _process_one(r, msg_id: str, body: dict) -> str:
    job_id, payload = _construct_job(body)
    if not job_id:
        _ack(r, msg_id)
        return "skip"
    try:
        quote_queue.update(job_id, status="processing")
    except Exception as e:
        logger.warning(f"job 状态预写失败（重投待 Redis 恢复） | id={job_id[:8]} | {e}")
        _requeue(r, msg_id, body)
        return "retry"
    started = time.time()
    try:
        if str(payload.get("kind") or "estimate") == "route":
            result = quote_engine.estimate_route(
                str(payload.get("description", "")),
                float(payload.get("value_usd") or 0),
                route=payload.get("route"),
                top_k=int(payload.get("top_k") or 8),
                with_llm=bool(payload.get("with_llm")),
                hs_override=payload.get("hs_code") or None,
            )
        else:
            result = quote_engine.estimate(
                str(payload.get("description", "")),
                float(payload.get("value_usd") or 0),
                mode=str(payload.get("mode") or "direct"),
                incoterm=str(payload.get("incoterm") or "FOB"),
                origin=str(payload.get("origin_country") or "CN"),
                dest=str(payload.get("dest_country") or "US"),
                top_k=int(payload.get("top_k") or 8),
                with_llm=bool(payload.get("with_llm")),
                hs_override=payload.get("hs_code") or None,
            )
        quote_queue.update(job_id, status="done", result=result)
        logger.info(
            f"job 完成 | id={job_id[:8]} | {result.get('status')} | "
            f"elapsed={round(time.time() - started, 2)}s | "
            f"desc={str(payload.get('description', ''))[:40]}"
        )
        _ack(r, msg_id)
    except Exception as e:
        logger.error(f"job 失败 | id={job_id[:8]} | {e}", exc_info=True)
        try:
            quote_queue.update(job_id, status="failed", error=str(e)[:500])
            _ack(r, msg_id)
        except Exception:
            _requeue(r, msg_id, body)
        return "failed"
    return "done"


def _dispatch(r, msg_id: str, fields: dict):
    """提交单 job 到计算池并等待硬超时；超时/异常 → 标 failed 并 XACK 出队。"""
    fut = _executor.submit(_process_one, r, msg_id, fields)
    try:
        fut.result(timeout=settings.QUOTE_JOB_TIMEOUT_SEC)
    except Exception as e:
        job_id = fields.get("job_id", "")
        logger.error(f"job 执行超时/异常 | id={str(job_id)[:8]} | {e}")
        try:
            quote_queue.update(str(job_id), status="failed",
                               error=f"执行超时（>{settings.QUOTE_JOB_TIMEOUT_SEC}s）")
            r.xack(settings.QUOTE_QUEUE_STREAM, settings.QUOTE_QUEUE_GROUP, msg_id)
        except Exception:
            pass


def _claim_stale(r, name: str, min_idle_ms: int) -> int:
    """XAUTOCLAIM 回收僵尸 consumer（崩溃/卡死未 ACK）的 pending 消息，实现组内重投。

    min_idle_ms 须 > 单 job 硬超时，避免抢占仍在正常处理的消息。返回回收条数。
    """
    try:
        res = r.xautoclaim(
            settings.QUOTE_QUEUE_STREAM, settings.QUOTE_QUEUE_GROUP, name,
            min_idle_time=min_idle_ms, start_id="0-0",
            count=settings.QUOTE_QUEUE_CLAIM_COUNT,
        )
    except Exception as e:
        logger.warning(f"XAUTOCLAIM 异常（Redis<6.2 或不可用，跳过回收）| {e}")
        return 0
    claimed = res[1] if isinstance(res, (list, tuple)) and len(res) >= 2 else []
    n = 0
    for msg_id, fields in (claimed or []):
        if _stop.is_set():
            break
        logger.info(f"回收僵尸 pending | msg={msg_id} → consumer={name}")
        _dispatch(r, msg_id, fields)
        n += 1
    return n


def _consumer(r, name: str):
    logger.info(f"consumer 启动 | {name}")
    min_idle_ms = max(1, settings.QUOTE_QUEUE_CLAIM_MIN_IDLE_SEC) * 1000
    while not _stop.is_set():
        # 1) 先回收其它 consumer 崩溃/卡死遗留的 pending（组内重投）
        _claim_stale(r, name, min_idle_ms)
        # 2) 阻塞等新消息
        if _stop.is_set():
            break
        try:
            out = r.xreadgroup(
                settings.QUOTE_QUEUE_GROUP, name,
                {settings.QUOTE_QUEUE_STREAM: ">"}, count=8, block=5000,
            )
        except Exception as e:
            logger.warning(f"XREADGROUP 异常 | {e}")
            time.sleep(1)
            continue
        if not out:
            continue
        for _stream, entries in out:
            for msg_id, fields in entries:
                if _stop.is_set():
                    break
                _dispatch(r, msg_id, fields)


def main():
    setup_logging("agent")
    init_db()
    try:
        from service.tariff_service.hs_classifier import hs_classifier
        hs_classifier.refresh(country="US")
        logger.info("HS 索引预热完成（US 20万）")
    except Exception as e:
        logger.warning(f"HS 索引预热失败（首个 job 按需重建） | error={e}")

    if not quote_queue.ensure_ready():
        logger.warning("启动自检：quote_queue redis 不可用（job 回写将重投重试）")

    r = quote_queue.blocking_redis()
    try:
        r.xgroup_create(settings.QUOTE_QUEUE_STREAM, settings.QUOTE_QUEUE_GROUP,
                        id="0", mkstream=True)
    except Exception:
        pass
    logger.info(
        f"quote worker 就绪 | stream={settings.QUOTE_QUEUE_STREAM} | "
        f"group={settings.QUOTE_QUEUE_GROUP} | consumers={settings.QUOTE_WORKER_CONCURRENCY}"
    )

    def _sig(_signum, _frame):
        logger.info("收到退出信号，优雅停止")
        _stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    threads = [threading.Thread(target=_consumer, args=(r, f"w-{i}"), daemon=True)
               for i in range(settings.QUOTE_WORKER_CONCURRENCY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _executor.shutdown(wait=False)
    logger.info("quote worker 退出")


if __name__ == "__main__":
    main()