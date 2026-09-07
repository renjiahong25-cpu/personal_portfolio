# -*- coding: utf-8 -*-
"""
评测服务 API 路由
- GET  /api/eval/metric    获取当前模型指标
- GET  /api/eval/badcase   BadCase 列表（分页）
- POST /api/eval/run       手动触发回归评测（后台执行）
- GET  /api/eval/report    获取评测报告（单次/对比/趋势）
- 统一异常处理 + 完整日志
"""
import json
import time

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy import desc

from config.logging_config import get_logger
from config.constants import (
    CODE_SUCCESS, CODE_PARAM_ERROR, CODE_SERVER_ERROR, CODE_NOT_FOUND,
    MSG_SUCCESS, MSG_PARAM_ERROR, MSG_SERVER_ERROR, MSG_NOT_FOUND,
)
from schemas.schemas import EvalRunReq, CommonResp
from db.models.base import SessionLocal, EvalResult, BadCase
from service.eval_service.badcase_manager import BadCaseManager
from service.eval_service.eval_set_generator import EvalSetGenerator
from service.eval_service.regression_tester import run_regression
from service.eval_service.report_generator import ReportGenerator

logger = get_logger("api.eval")

router = APIRouter()

# 自上次评测后新增未处理 BadCase 时，评测前自动采集用户反馈
_FEEDBACK_BADCASE_LIMIT = 200


def _ok(data=None, msg: str = MSG_SUCCESS) -> CommonResp:
    return CommonResp(code=CODE_SUCCESS, msg=msg, data=data)


def _err(code: int, msg: str) -> CommonResp:
    return CommonResp(code=code, msg=msg, data=None)


def _row_to_doc(row) -> dict:
    """EvalResult ORM 行转报告摘要"""
    detail = {}
    try:
        detail = json.loads(row.report_detail or "{}")
    except Exception as e:
        logger.warning(f"评测报告 JSON 解析失败 | row_id={row.id} | error={e}")
    return {
        "id": row.id,
        "run_id": detail.get("run_id"),
        "version": row.version,
        "total_count": row.total_count,
        "pass_count": row.pass_count,
        "fail_count": row.fail_count,
        "pass_rate": row.pass_rate,
        "error_rate": row.error_rate,
        "avg_latency_ms": row.avg_latency_ms,
        "metrics": detail.get("metrics", {}),
        "redline": detail.get("redline", {}),
        "alerts": detail.get("alerts", []),
        "level_breakdown": detail.get("level_breakdown", {}),
        "fail_items": detail.get("fail_items", []),
        "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
    }


@router.get("/metric", response_model=CommonResp)
async def get_metric():
    """获取当前模型指标（最近一次评测结果的完整指标 + 红线状态）"""
    start = time.time()
    logger.info("GET /api/eval/metric 开始")
    try:
        latest = ReportGenerator().load_latest_metrics()
        if not latest:
            logger.warning("当前暂无评测指标（尚未运行过回归评测）")
            return _ok(data={"has_record": False, "metrics": None,
                             "message": "尚无评测记录，请先发起 POST /api/eval/run"},
                       msg=MSG_SUCCESS)
        data = {"has_record": True, **latest}
        elapsed = round(time.time() - start, 3)
        logger.info(f"GET /api/eval/metric 完成 | 耗时={elapsed}s | "
                    f"error_rate={latest['metrics'].get('task_fact_error_rate')}")
        return _ok(data=data)
    except Exception as e:
        logger.error(f"GET /api/eval/metric 失败 | error={e}", exc_info=True)
        return _err(CODE_SERVER_ERROR, MSG_SERVER_ERROR)


@router.get("/badcase", response_model=CommonResp)
async def list_badcase(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页条数"),
    category: str = Query("", description="BadCase 分类过滤"),
    status: int = Query(None, description="状态过滤：0未处理/1已扩充/2已关闭"),
):
    """BadCase 列表查询（分页 + 分类/状态过滤）"""
    start = time.time()
    logger.info(
        f"GET /api/eval/badcase 开始 | page={page} | page_size={page_size} | "
        f"category={category} | status={status}")
    try:
        result = BadCaseManager().list_badcases(
            page=page, page_size=page_size, category=category, status=status)
        elapsed = round(time.time() - start, 3)
        logger.info(f"GET /api/eval/badcase 完成 | total={result['total']} | 耗时={elapsed}s")
        return _ok(data=result)
    except Exception as e:
        logger.error(f"GET /api/eval/badcase 失败 | error={e}", exc_info=True)
        return _err(CODE_SERVER_ERROR, MSG_SERVER_ERROR)


async def _run_regression_background(req: EvalRunReq) -> None:
    """后台执行回归评测（含 BadCase 自动入库）"""
    try:
        logger.info(
            f"后台回归评测开始 | version={req.version} | sample_count={req.sample_count} | "
            f"eval_set_id={req.eval_set_id}")
        sample = req.sample_count if req.sample_count and req.sample_count > 0 else None
        report = await run_regression(
            eval_set_id=req.eval_set_id or None, version=req.version,
            sample_count=sample, collect_badcase=True)
        # 报告落盘（本地文件 + DB 双写）
        try:
            ReportGenerator().save_report(report)
        except Exception as e:
            logger.error(f"评测报告落盘失败 | error={e}", exc_info=True)
        logger.info(
            f"后台回归评测完成 | run_id={report.get('run_id')} | "
            f"通过率={report.get('pass_rate')} | 事实错误率="
            f"{report['metrics'].get('task_fact_error_rate')}")
    except Exception as e:
        logger.error(f"后台回归评测异常 | error={e}", exc_info=True)


@router.post("/run", response_model=CommonResp)
async def run_eval(req: EvalRunReq, background_tasks: BackgroundTasks):
    """手动触发回归评测（后台异步执行，返回任务已受理）"""
    start = time.time()
    logger.info(
        f"POST /api/eval/run 开始 | version={req.version} | sample_count={req.sample_count} | "
        f"eval_set_id={req.eval_set_id or 'latest'}")
    try:
        if req.sample_count < 0:
            return _err(CODE_PARAM_ERROR, "sample_count 不能为负数")
        # 评测前自动采集用户反馈 BadCase，扩充反例库
        try:
            collected = BadCaseManager().collect_from_feedback(limit=_FEEDBACK_BADCASE_LIMIT)
            logger.info(f"评测前置 BadCase 采集 | 入库={collected}")
        except Exception as e:
            logger.warning(f"评测前置 BadCase 采集失败（不影响评测）| error={e}")

        background_tasks.add_task(_run_regression_background, req)
        elapsed = round(time.time() - start, 3)
        logger.info(f"POST /api/eval/run 已受理 | 耗时={elapsed}s")
        return _ok(data={
            "accepted": True,
            "message": "评测任务已提交，正在后台执行",
            "version": req.version,
            "sample_count": req.sample_count,
            "eval_set_id": req.eval_set_id or "latest",
        })
    except Exception as e:
        logger.error(f"POST /api/eval/run 失败 | error={e}", exc_info=True)
        return _err(CODE_SERVER_ERROR, MSG_SERVER_ERROR)


@router.get("/report", response_model=CommonResp)
async def get_report(
    run_id: int = Query(None, description="按评测结果 ID 查单次报告"),
    version: str = Query("", description="按版本查最近一次报告"),
    trend: bool = Query(False, description="趋势分析模式"),
    compare: bool = Query(False, description="对比模式：当前版本 vs 上一版本"),
    limit: int = Query(10, ge=1, le=100, description="趋势/列表条数上限"),
):
    """获取评测报告：单次 / 版本 + 对比 / 趋势"""
    start = time.time()
    logger.info(
        f"GET /api/eval/report 开始 | run_id={run_id} | version={version} | "
        f"trend={trend} | compare={compare} | limit={limit}")
    try:
        data = None
        if trend:
            with SessionLocal() as db:
                rows = db.query(EvalResult).order_by(desc(EvalResult.id)).limit(limit).all()
                docs = [{
                    "id": r.id, "version": r.version, "total_count": r.total_count,
                    "pass_rate": r.pass_rate, "error_rate": r.error_rate,
                    "avg_latency_ms": r.avg_latency_ms,
                    "ts": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
                    "report_detail": r.report_detail,
                } for r in rows]
            data = ReportGenerator().build_trend_analysis(docs)
        elif run_id:
            with SessionLocal() as db:
                row = db.query(EvalResult).filter(EvalResult.id == run_id).first()
            if not row:
                return _err(CODE_NOT_FOUND, MSG_NOT_FOUND)
            data = _row_to_doc(row)
        elif version:
            with SessionLocal() as db:
                row = db.query(EvalResult).filter(
                    EvalResult.version == version).order_by(desc(EvalResult.id)).first()
            if not row:
                return _err(CODE_NOT_FOUND, f"版本 {version} 暂无评测记录")
            doc = _row_to_doc(row)
            if compare:
                with SessionLocal() as db:
                    prev = db.query(EvalResult).filter(
                        EvalResult.id < row.id).order_by(desc(EvalResult.id)).first()
                if not prev:
                    return _err(CODE_NOT_FOUND, "暂无上一版本评测记录，无法对比")
                data = ReportGenerator().build_compare_report(
                    _row_to_doc(prev), doc)
            else:
                data = doc
        else:
            with SessionLocal() as db:
                row = db.query(EvalResult).order_by(desc(EvalResult.id)).first()
            if not row:
                return _err(CODE_NOT_FOUND, "暂无评测记录，请先运行评测")
            data = _row_to_doc(row)

        elapsed = round(time.time() - start, 3)
        logger.info(f"GET /api/eval/report 完成 | 耗时={elapsed}s | mode="
                    f"{'trend' if trend else ('run_id' if run_id else ('compare' if compare else 'latest'))}")
        return _ok(data=data)
    except Exception as e:
        logger.error(f"GET /api/eval/report 失败 | error={e}", exc_info=True)
        return _err(CODE_SERVER_ERROR, MSG_SERVER_ERROR)