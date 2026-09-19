# -*- coding: utf-8 -*-
"""关税服务 API：双关(出/入)HS 分类 · 税率查询 · 按需抓取 · 数据同步 · 对美报价重算"""
import re
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config.logging_config import get_logger
from config import settings
from service.cache_service import (
    quote_cache, run_singleflight, rate_limiter, infra_stats, cache_key, quote_queue,
)
from service.tariff_service.tariff_service import tariff_service
from service.tariff_service.quote_engine import quote_engine
from service.tariff_service import world_tariff
from service.tariff_service import name_search

logger = get_logger("api_tariff")
router = APIRouter()

# 快路径软超时包装线程池：异步跑同步计算，避免占用 FastAPI 内置线程池导致堵请求
_quote_executor = ThreadPoolExecutor(
    max_workers=settings.QUOTE_FAST_EXECUTOR_SIZE, thread_name_prefix="quote_runner")


class PredictReq(BaseModel):
    description: str
    origin_country: Optional[str] = None      # 出口国(起运国)，默认 CN
    dest_country: Optional[str] = None        # 进口国(目的国)，默认 DE
    top_k: Optional[int] = 8


class QuoteReq(BaseModel):
    description: str
    value_usd: float = Field(gt=0, description="货值(美元)，须>0")
    mode: Optional[str] = "direct"            # direct / direct_sea / vn / mx / warehouse
    incoterm: Optional[str] = "FOB"
    origin_country: Optional[str] = "CN"
    dest_country: Optional[str] = "US"
    top_k: Optional[int] = 8
    with_llm: Optional[bool] = None           # None=跟随配置
    hs_code: Optional[str] = None             # 用户候选确认选定的 HS 码（跳过自动分类）


class FetchReq(BaseModel):
    hs_code: str
    origin_country: Optional[str] = "CN"
    dest_country: Optional[str] = "DE"
    force: Optional[bool] = False             # true=绕过缓存强制重新抓取


class SyncReq(BaseModel):
    source: Optional[str] = None      # hts / taric / None(全量，仅离线能力用)
    country: Optional[str] = None


class ManualRate(BaseModel):
    duty_type: str = "other"
    duty_rate: str = ""
    trade_partner: Optional[str] = ""


class ManualReq(BaseModel):
    hs_code: str
    country: str = "CN"
    direction: str = "export"
    rows: list[ManualRate] = []


@router.get("/search")
def search(q: str = "", country: Optional[str] = None, limit: int = 20):
    """按商品描述检索 HS 编码候选（离线关键词）"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="查询词不能为空")
    return tariff_service.search(q.strip(), country, limit)


@router.post("/predict")
async def predict(req: PredictReq):
    """商品描述 → HS 编码 + 出关/入关双侧税率面板（出关=出口税/退税，入关=MFN/VAT/反倾销）"""
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="商品描述不能为空")
    result = tariff_service.predict(req.description.strip(), req.origin_country,
                                    req.dest_country, req.top_k)
    if result.get("chosen") is None:
        raise HTTPException(status_code=404, detail=result.get("reason", "未匹配到 HS 编码"))
    return result


@router.get("/detail")
def detail(hs_code: str = "", country: Optional[str] = None, direction: Optional[str] = None):
    """HS 编码详情（父级链 + 指定方向税率；direction=import/export）"""
    if not hs_code.strip():
        raise HTTPException(status_code=400, detail="hs_code 不能为空")
    result = tariff_service.detail(hs_code.strip(), country, direction)
    if not result.get("found"):
        raise HTTPException(status_code=404,
                            detail=f"未找到 HS 编码 {hs_code} 的税率（country={country or '默认'}）")
    return result


@router.get("/duties")
def duties(hs_code: str = "", country: Optional[str] = None, direction: Optional[str] = None):
    """查询某 HS 编码指定方向的税率明细"""
    if not hs_code.strip():
        raise HTTPException(status_code=400, detail="hs_code 不能为空")
    return tariff_service.duties(hs_code.strip(), country, direction)


@router.post("/fetch")
async def fetch_rate(req: FetchReq):
    """手动更新(或强制刷新)某编码 出关/入关 双侧税率（查询即抓，TTL 内走缓存）"""
    if not req.hs_code.strip():
        raise HTTPException(status_code=400, detail="hs_code 不能为空")
    panels = tariff_service.fetch_for_code(req.hs_code.strip(),
                                           req.origin_country, req.dest_country, req.force)
    return {"hs_code": req.hs_code.strip(),
            "origin_country": req.origin_country or "CN",
            "dest_country": req.dest_country or "DE",
            "export_panel": panels["export"], "import_panel": panels["import"]}


@router.get("/stats")
def stats(country: Optional[str] = None):
    """HS 数据概况 / 最近更新 / 最近抓取日志"""
    return tariff_service.stats(country)


@router.post("/manual")
async def save_manual(req: ManualReq):
    """人工修正：手填某编码某一方向的税率落库(source=manual)，抓取不会覆盖"""
    rows = [r.model_dump() for r in req.rows]
    return tariff_service.save_manual(req.hs_code, req.country, req.direction, rows)


@router.post("/sync")
async def sync(req: SyncReq = None):
    """全量文件导入（可选离线能力，HTS/TARIC 大文件；默认走按需抓取即可）"""
    req = req or SyncReq()
    return await tariff_service.sync(req.source, req.country)


# ----------------------------------------------------------
# 对美报价成本重算（Quote Engine）
# ----------------------------------------------------------
@router.post("/quote")
def quote(req: QuoteReq, request: Request, response: Response):
    """商品描述+货值 → 综合关税/路径对比/HS自查/出口效益（定向 US）

    带 P3 Redis 响应缓存（X-Quote-Cache: hit/miss）+ 并发单飞 + 每 IP 限流。
    QUOTE_ASYNC_ENABLE=true 时启用"快同步/慢异步"混合模式：
      - 快路径超过软超时 → 任务写入 Redis Stream 排队，立即返回 {status:queued, job_id}，
        前端轮询 GET /api/tariff/quote/jobs/{job_id} 获取结果（计算不浪费，原计算继续写缓存）。
      - 队列积压超阈值 → 立即 429 快速失败，请求不傻等不崩。
    缓存键包含 extra/base 版本，数据更新后自动失效。
    """
    if rate_limiter.enabled:
        ip = request.client.host if request.client else ""
        if rate_limiter.over_limit(ip):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="商品描述不能为空")

    def _run() -> dict:
        return quote_engine.estimate(
            req.description.strip(), req.value_usd, mode=req.mode,
            incoterm=req.incoterm, origin=req.origin_country, dest=req.dest_country,
            top_k=req.top_k, with_llm=req.with_llm, hs_override=req.hs_code,
        )

    def _fast() -> tuple:
        """同步快路径：先直查共享缓存——命中立即返回，不占执行器、不受其排队/软超时影响；
        仅 miss 才提交执行器做单飞计算（冷键长算），超软超时转排队。"""
        if not settings.CACHE_ENABLE:
            return _run(), False
        try:
            versions = quote_engine.versions()
        except Exception:
            versions = {}
        key = cache_key(
            "q", req.description.strip().lower(), req.value_usd, req.mode or "",
            req.incoterm or "", req.origin_country or "", req.dest_country or "",
            req.top_k, bool(req.with_llm), req.hs_code or "",
            versions.get("extra_version", ""), versions.get("extra_rows", ""),
        )
        cached = quote_cache.get(key)
        if cached is not None:
            return cached, True
        if settings.QUOTE_ASYNC_ENABLE:
            fut = _quote_executor.submit(run_singleflight, quote_cache, key, _run)
            result, from_cache = fut.result(timeout=settings.QUOTE_SYNC_TIMEOUT_SEC)
            return result, from_cache
        return run_singleflight(quote_cache, key, _run)

    backlog = 0
    if settings.QUOTE_ASYNC_ENABLE:
        backlog = quote_queue.backlog_len()
        if backlog >= settings.QUOTE_QUEUE_BACKLOG_MAX:
            raise HTTPException(status_code=429,
                                detail=f"系统繁忙，排队任务已满({backlog})，请稍后再试")
    try:
        result, from_cache = _fast()
        response.headers["X-Quote-Cache"] = "hit" if from_cache else "miss"
    except TimeoutError:
        if not settings.QUOTE_ASYNC_ENABLE:
            raise HTTPException(status_code=503, detail="计算超时，请稍后重试")
        job_id = quote_queue.submit({
            "description": req.description.strip(), "value_usd": req.value_usd,
            "mode": req.mode or "direct", "incoterm": req.incoterm or "FOB",
            "origin_country": req.origin_country or "CN", "dest_country": req.dest_country or "US",
            "top_k": req.top_k or 8, "with_llm": bool(req.with_llm),
            "hs_code": req.hs_code or "",
        })
        response.headers["X-Quote-Cache"] = "async"
        logger.info(f"报价转入排队 | job_id={job_id[:8]} | backlog={backlog} | query={req.description.strip()[:40]}")
        return {"status": "queued", "job_id": job_id, "position": backlog,
                "message": "计算较慢，已转入后台排队，请轮询查询结果",
                "poll": f"/api/tariff/quote/jobs/{job_id}"}
    if result.get("status") in ("disabled", "bad_request", "unsupported", "hs_not_found"):
        raise HTTPException(status_code=404, detail=result.get("message", "无法报价"))
    # no_classify → 200 + status 字段 + candidates_suggest（前端渲染候选确认）
    return result


class QuoteRouteReq(BaseModel):
    description: str
    value_usd: float = Field(gt=0, description="货值(美元)，须>0")
    route: Optional[list] = None           # None=全部预置路径对比；如 ["CN","VN","US"]
    top_k: Optional[int] = 8
    with_llm: Optional[bool] = None
    hs_code: Optional[str] = None          # 用户候选确认选定的 HS 码（跳过自动分类）


@router.post("/quote/route")
def quote_route(req: QuoteRouteReq, request: Request, response: Response):
    """多中转路径报价：CN→{VN/MX/TH/SG/MY}*→US 任意路径（route 省略=预置路径对比）。

    逐段递推：每段运费(当前 CIF 比例) + 中转国进口税(官方税则快照) + 操作费；
    美国最终税双情景：保守(按 CN 原产,含 301/对等) vs 乐观(实质改变成立,301 不适用)。
    与 /quote 同缓存/单飞/排队机制（快路径超软超时转 Stream 排队）。
    """
    if rate_limiter.enabled:
        ip = request.client.host if request.client else ""
        if rate_limiter.over_limit(ip):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if not req.description.strip():
        raise HTTPException(status_code=400, detail="商品描述不能为空")

    def _run() -> dict:
        return quote_engine.estimate_route(
            req.description.strip(), req.value_usd, route=req.route,
            top_k=req.top_k, with_llm=req.with_llm, hs_override=req.hs_code,
        )

    def _fast() -> tuple:
        if not settings.CACHE_ENABLE:
            return _run(), False
        try:
            versions = quote_engine.versions()
        except Exception:
            versions = {}
        route_sig = "all" if not req.route else "->".join(str(c).upper() for c in req.route)
        key = cache_key(
            "qr", req.description.strip().lower(), req.value_usd, route_sig,
            req.top_k, bool(req.with_llm), req.hs_code or "",
            versions.get("extra_version", ""), versions.get("extra_rows", ""),
        )
        cached = quote_cache.get(key)
        if cached is not None:
            return cached, True
        if settings.QUOTE_ASYNC_ENABLE:
            fut = _quote_executor.submit(run_singleflight, quote_cache, key, _run)
            result, from_cache = fut.result(timeout=settings.QUOTE_SYNC_TIMEOUT_SEC)
            return result, from_cache
        return run_singleflight(quote_cache, key, _run)

    backlog = 0
    if settings.QUOTE_ASYNC_ENABLE:
        backlog = quote_queue.backlog_len()
        if backlog >= settings.QUOTE_QUEUE_BACKLOG_MAX:
            raise HTTPException(status_code=429,
                                detail=f"系统繁忙，排队任务已满({backlog})，请稍后再试")
    try:
        result, from_cache = _fast()
        response.headers["X-Quote-Cache"] = "hit" if from_cache else "miss"
    except TimeoutError:
        if not settings.QUOTE_ASYNC_ENABLE:
            raise HTTPException(status_code=503, detail="计算超时，请稍后重试")
        job_id = quote_queue.submit({
            "kind": "route", "description": req.description.strip(),
            "value_usd": req.value_usd, "route": req.route,
            "top_k": req.top_k or 8, "with_llm": bool(req.with_llm),
            "hs_code": req.hs_code or "",
        })
        response.headers["X-Quote-Cache"] = "async"
        logger.info(f"多中转报价转入排队 | job_id={job_id[:8]} | backlog={backlog} | query={req.description.strip()[:40]}")
        return {"status": "queued", "job_id": job_id, "position": backlog,
                "message": "计算较慢，已转入后台排队，请轮询查询结果",
                "poll": f"/api/tariff/quote/jobs/{job_id}"}
    if result.get("status") in ("disabled", "bad_request", "hs_not_found"):
        raise HTTPException(status_code=404, detail=result.get("message", "无法报价"))
    # no_classify → 200 + status 字段 + candidates_suggest（前端渲染候选确认）
    return result


# ----------------------------------------------------------
# 任意两国进口关税查询（多中转/独立工具）
# ----------------------------------------------------------
@router.get("/ab")
def tariff_ab(origin: str = "CN", dest: str = "US", hs: str = ""):
    """A→B 进口关税：返回 dest(目的)国对 hs 的进口(MFN)税率。

    - 中转国(VN/SG/MY/TH/MX) → world_mfn 静态快照（各国官方税则）。
    - US/EU/CN 等既有国 → DutyRate 基础 MFN/普通税率（美线 301/对等附加在报价里另计）。
    """
    code = re.sub(r"\D", "", hs or "")
    if not code:
        raise HTTPException(status_code=400, detail="HS 编码不能为空")
    dest = (dest or "").upper()
    out = {"from": (origin or "").upper(), "to": dest, "hs": code,
           "direction": "import"}

    if dest in world_tariff.supported_countries():
        r = world_tariff.import_duty(dest, code)
        out.update({k: r.get(k) for k in ("rate", "note", "source", "as_of", "desc", "sublines")})
        if r.get("rates"):
            out["sub_rates"] = r["rates"]
        return out

    # US/EU/CN 及已抓取国：取基础 MFN/普通进口税率
    base = None
    try:
        duties = tariff_service.duties(code, dest, direction="import").get("duties", [])
    except Exception:
        duties = []
    for t in ("import_mfn", "mfn", "import_general", "general"):
        for x in duties:
            if x.get("duty_type") == t:
                base = x
                break
        if base:
            break
    if base is None and duties:
        base = duties[0]
    if base:
        out.update({"rate": base.get("rate_value"), "rate_text": base.get("duty_rate"),
                    "duty_type": base.get("duty_type"),
                    "note": "基础 MFN/普通进口税率；美线另含 301/对等附加（见报价）",
                    "source": "DutyRate", "as_of": ""})
    else:
        out.update({"rate": None, "note": "该目的国暂无税率数据", "source": "", "as_of": ""})
    return out


@router.get("/world-sources")
def world_sources():
    """world_mfn 静态快照元信息（支持的各国 + 覆盖 + as_of），供 A→B 工具/多中转 UI。"""
    return world_tariff.version()


class SmartSearchReq(BaseModel):
    q: str
    dest: Optional[str] = "VN"
    limit: Optional[int] = 8


@router.post("/smart-search")
def smart_search(req: SmartSearchReq):
    """商品名模糊搜索：泛化词(摩托车/人偶/…) → HS 候选列表。

    三级策略：精确数字 → 关键词+中英同义词典倒排索引(快路径) → LLM 兜底
    （候选必须在官方税则中真实存在才保留）。dest 国进口税率随候选一并返回；
    precise=true 表示候选恰为 1（用户提问精准，前端可直接采用）。
    """
    if not req.q.strip():
        raise HTTPException(status_code=400, detail="查询词不能为空")
    return name_search.smart_search(req.q.strip(), req.dest or "VN", req.limit or 8)


@router.get("/quote/jobs/{job_id}")
def quote_job_status(job_id: str):
    """异步排队报价任务查询：{status, result, error}（done 后 TTL 内可取）。"""
    body = quote_queue.get(job_id)
    if body is None:
        raise HTTPException(status_code=404, detail="任务不存在或结果已过期")
    payload = body.get("payload") or {}
    return {
        "job_id": body.get("job_id"), "status": body.get("status", "unknown"),
        "result": body.get("result"), "error": body.get("error"),
        "query": payload.get("description", ""), "value_usd": payload.get("value_usd"),
        "created_at": body.get("created_at"), "updated_at": body.get("updated_at"),
    }


@router.get("/quote/admin/stats")
def quote_admin_stats():
    """报价引擎共享缓存/限流/分布式锁健康快照（运维/压测验收用）"""
    try:
        return infra_stats()
    except Exception as e:
        logger.error(f"缓存 stats 失败 | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"缓存 stats 失败: {e}")


@router.post("/quote/admin/cache/flush")
def quote_admin_flush():
    """清空报价响应缓存与进程内 LRU（数据版本更新后调用）"""
    try:
        removed = quote_cache.flush()
    except Exception as e:
        logger.error(f"缓存 flush 失败 | error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"缓存 flush 失败: {e}")
    return {"removed": removed}


@router.get("/quote/versions")
def quote_versions():
    """报价引擎当前版本与政策清单快照（审计/前端展示）"""
    return quote_engine.versions()