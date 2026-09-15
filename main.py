from fastapi import FastAPI
from contextlib import asynccontextmanager
from config.logging_config import setup_logging, get_logger
from config.settings import QUOTE_ONLY, SERVICE_ROLE
from db.models.base import init_db

# ============ 角色拆分（P5）：api / quote / policy / tariff ============
# 默认 api=全功能；quote=仅关税+SPA（水平扩容）；policy=知识库+问答+巡检；
# tariff=关税同步调度器（单 worker）。QUOTE_ONLY 兼容为 quote。
_ROLE = "quote" if QUOTE_ONLY else (SERVICE_ROLE or "api")

if _ROLE == "api":
    from api import tariff
    from api import spider
    from api import chat, doc
    from api import eval as eval_api
    _HAS_QUOTE = True
elif _ROLE in ("quote", "tariff"):
    from api import tariff
    _HAS_QUOTE = True
else:  # policy
    from api import spider
    from api import chat, doc
    from api import eval as eval_api
    _HAS_QUOTE = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("agent")
    logger = get_logger("main")
    logger.info(f"========== 跨境物流规则智能Agent 启动 ========== | role={_ROLE}")
    init_db()
    # 附加关税清单（301/对等）版本化 seed 落库，供报价引擎使用（全角色加载，成本低）
    try:
        from service.tariff_service.extra_tariff import extra_tariff_service
        extra_tariff_service.load_seed()
    except Exception as e:
        logger.warning(f"附加关税清单加载失败（不影响主服务） | error={e}")
    # 报价域：预热线上 HS 分类索引（每个 uvicorn worker 进程都要独立建内存索引）。
    # 关键：多 worker 同时启动会抢分布式锁 hs_index:US，只让一个建、其余跳过；
    # 若在 yield 前不建好，跳过的进程内存索引为空 → 命中它的首请求 no_classify(404)。
    # 给足 acquire_timeout 让排队进程等到锁并各自建好，确保每个进程都持完整索引。
    if _ROLE in ("api", "quote", "tariff"):
        try:
            from service.tariff_service.hs_classifier import hs_classifier
            hs_classifier.refresh("US", acquire_timeout=120.0)
            logger.info("HS 索引预热完成（US，供报价分类）")
        except Exception as e:
            logger.warning(f"HS 索引预热失败（首请求将按需重建） | error={e}")
    # policy 域：启动对账（上次进程遗留的 running 入库任务标记 failed）+ 每日巡检调度器
    if _ROLE in ("api", "policy"):
        try:
            from service.data_service.kb_ingest_service import kb_ingest_service
            kb_ingest_service.reconcile_stale_tasks()
        except Exception as e:
            logger.warning(f"入库任务启动对账失败 | error={e}")
        try:
            from service.spider_service.scheduler import spider_scheduler
            await spider_scheduler.start()
        except Exception as e:
            logger.warning(f"每日巡检调度器启动失败（不影响主服务） | error={e}")
    # 关税域：关税税率数据同步调度器（api + tariff 角色；quote 副本不开，避免重复调度）
    if _ROLE in ("api", "tariff"):
        try:
            from service.tariff_service.scheduler import tariff_scheduler
            await tariff_scheduler.start()
        except Exception as e:
            logger.warning(f"关税同步调度器启动失败（不影响主服务） | error={e}")
    logger.info("数据库初始化完成，服务就绪")
    yield
    if _ROLE in ("api", "policy"):
        try:
            from service.spider_service.scheduler import spider_scheduler
            await spider_scheduler.stop()
        except Exception:
            pass
    if _ROLE in ("api", "tariff"):
        try:
            from service.tariff_service.scheduler import tariff_scheduler
            await tariff_scheduler.stop()
        except Exception:
            pass
    logger.info("========== 服务关闭 ==========")


app = FastAPI(
    title="跨境物流规则智能Agent",
    description="RAG + 工作流轻量化Agent，专注中国出口跨境清关规则",
    version="1.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "cross-border-agent", "role": _ROLE}


# ============ 路由注册（各服务模块统一挂载） ============
if _HAS_QUOTE:
    app.include_router(tariff.router, prefix="/api/tariff", tags=["关税"])
if _ROLE in ("api", "policy"):
    app.include_router(chat.router, prefix="/api/chat", tags=["问答"])
    app.include_router(doc.router, prefix="/api/doc", tags=["知识库"])
    app.include_router(spider.router, prefix="/api/spider", tags=["爬虫"])
    app.include_router(eval_api.router, prefix="/api/eval", tags=["评测"])


# ============ quote 角色：同源托管前端单页（SPA fallback） ============
if _ROLE == "quote":
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    _dist = Path(__file__).resolve().parent / "frontend" / "dist"
    if _dist.exists():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="spa_assets")

        @app.get("/", include_in_schema=False)
        def spa_index():
            return FileResponse(_dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            target = (_dist / full_path).resolve()
            if target.is_file():
                return FileResponse(target)
            return FileResponse(_dist / "index.html")