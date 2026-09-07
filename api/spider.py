"""
爬虫服务 FastAPI 路由
- POST /api/spider/site/add   新增站点（冷启动）
- POST /api/spider/run        手动触发抓取
- GET  /api/spider/candidate  获取 AI 候选配置
- POST /api/spider/audit      审核转正配置
- GET  /api/spider/site/list  站点列表
- 统一异常处理
- 完整日志记录

说明：受控接口待接入网关鉴权；耗时/入库操作在后台任务或线程池执行，避免阻塞事件循环。
"""
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from config.constants import (
    CODE_NOT_FOUND,
    CODE_PARAM_ERROR,
    CODE_SERVER_ERROR,
    CODE_SUCCESS,
    MSG_NOT_FOUND,
    MSG_PARAM_ERROR,
    MSG_SERVER_ERROR,
    MSG_SUCCESS,
)
from config.logging_config import get_logger
from db.models.base import SpiderSite, get_db
from schemas.schemas import CommonResp, SpiderAuditReq, SpiderSiteReq
from service.spider_service.crawler_engine import crawler_engine
from service.spider_service.site_manager import site_manager

logger = get_logger("api_spider")

router = APIRouter()


# ============================================================
# 入参结构体（run 接口需指定站点ID，独立定义，不侵入全局 schemas）
# ============================================================
class SpiderRunReq(BaseModel):
    site_id: int


# ============================================================
# 统一响应工具
# ============================================================
def _ok(data=None) -> CommonResp:
    return CommonResp(code=CODE_SUCCESS, msg=MSG_SUCCESS, data=data)


def _error(code: int, msg: str) -> CommonResp:
    return CommonResp(code=code, msg=msg, data=None)


def _raise_exc(err: Exception) -> CommonResp:
    """统一异常记录与响应"""
    logger.error(f"接口处理异常 | error={err}", exc_info=True)
    return _error(CODE_SERVER_ERROR, f"{MSG_SERVER_ERROR}: {err}")


# ============================================================
# POST /api/spider/site/add - 新增站点（冷启动）
# ============================================================
@router.post("/site/add", response_model=CommonResp)
async def add_site(
    req: SpiderSiteReq,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
) -> CommonResp:
    start = time.time()
    logger.info(
        f"新增站点接口开始 | site_name={req.site_name} | site_url={req.site_url} "
        f"| has_yaml={bool(req.yaml_config)}"
    )
    if not req.site_name or not req.site_url:
        logger.warning(f"新增站点参数缺失 | site_name={req.site_name} | site_url={req.site_url}")
        return _error(CODE_PARAM_ERROR, f"{MSG_PARAM_ERROR}: site_name 与 site_url 必填")

    try:
        # 查重：同一 URL 禁止重复新增
        exists = await run_in_threadpool(site_manager.get_site_by_url, db, req.site_url)
        if exists:
            logger.warning(f"站点已存在,拒绝新增 | site_url={req.site_url} | id={exists.id}")
            return _error(CODE_PARAM_ERROR, f"站点已存在(id={exists.id})")

        site: SpiderSite = await run_in_threadpool(
            site_manager.add_site, db, req.site_name, req.site_url, req.yaml_config
        )
        # 无配置 → 后台异步 AI 冷启动（低优先级任务，不阻塞接口）
        triggered = not req.yaml_config.strip()
        if triggered:
            background_tasks.add_task(crawler_engine.run_site_by_id, site.id)
            logger.info(f"AI冷启动已投递后台 | site_id={site.id}")

        elapsed = round(time.time() - start, 3)
        data = site_manager.site_to_dict(site)
        data["cold_start_task"] = triggered
        logger.info(f"新增站点接口完成 | site_id={site.id} | cold_start={triggered} | elapsed={elapsed}s")
        return _ok(data)
    except Exception as e:
        return _raise_exc(e)


# ============================================================
# POST /api/spider/run - 手动触发抓取（后台异步）
# ============================================================
@router.post("/run", response_model=CommonResp)
async def run_spider(
    req: SpiderRunReq,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
) -> CommonResp:
    start = time.time()
    logger.info(f"手动触发抓取接口开始 | site_id={req.site_id}")
    try:
        site = await run_in_threadpool(site_manager.get_site, db, req.site_id)
        if site is None:
            logger.warning(f"手动触发抓取失败,站点不存在 | site_id={req.site_id}")
            return _error(CODE_NOT_FOUND, MSG_NOT_FOUND)

        background_tasks.add_task(crawler_engine.run_site_by_id, req.site_id)
        elapsed = round(time.time() - start, 3)
        logger.info(f"手动触发抓取接口完成 | site_id={req.site_id} | task=已提交后台 | elapsed={elapsed}s")
        return _ok({"site_id": req.site_id, "task_status": "submitted", "message": "抓取任务已提交后台执行"})
    except Exception as e:
        return _raise_exc(e)


# ============================================================
# GET /api/spider/candidate - 获取 AI 候选配置（待审核）
# ============================================================
@router.get("/candidate", response_model=CommonResp)
async def list_candidates(db=Depends(get_db)) -> CommonResp:
    start = time.time()
    logger.info("获取AI候选配置接口开始")
    try:
        candidates = await run_in_threadpool(site_manager.list_candidates, db)
        elapsed = round(time.time() - start, 3)
        logger.info(f"获取AI候选配置接口完成 | count={len(candidates)} | elapsed={elapsed}s")
        return _ok(candidates)
    except Exception as e:
        return _raise_exc(e)


# ============================================================
# POST /api/spider/audit - 审核转正配置
# ============================================================
@router.post("/audit", response_model=CommonResp)
async def audit_candidate(req: SpiderAuditReq, db=Depends(get_db)) -> CommonResp:
    start = time.time()
    logger.info(f"审核候选配置接口开始 | site_id={req.site_id} | approve={req.approve}")
    try:
        result = await run_in_threadpool(site_manager.audit, db, req.site_id, req.approve)
        elapsed = round(time.time() - start, 3)
        logger.info(f"审核候选配置接口完成 | site_id={req.site_id} | approve={req.approve} | elapsed={elapsed}s")
        return _ok(result)
    except ValueError as e:
        logger.warning(f"审核候选配置参数错误 | error={e}")
        return _error(CODE_PARAM_ERROR, str(e))
    except Exception as e:
        return _raise_exc(e)


# ============================================================
# GET /api/spider/site/list - 站点列表
# ============================================================
@router.get("/site/list", response_model=CommonResp)
async def list_sites(
    status: Optional[int] = None,
    db=Depends(get_db),
) -> CommonResp:
    start = time.time()
    logger.info(f"站点列表接口开始 | status={status}")
    try:
        sites = await run_in_threadpool(site_manager.list_sites, db, status)
        elapsed = round(time.time() - start, 3)
        logger.info(f"站点列表接口完成 | count={len(sites)} | elapsed={elapsed}s")
        return _ok(sites)
    except Exception as e:
        return _raise_exc(e)