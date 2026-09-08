from fastapi import FastAPI
from contextlib import asynccontextmanager
from config.logging_config import setup_logging, get_logger
from db.models.base import init_db
from api import spider
from api import chat, doc
from api import eval as eval_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("agent")
    logger = get_logger("main")
    logger.info("========== 跨境物流规则智能Agent 启动 ==========")
    init_db()
    logger.info("数据库初始化完成，服务就绪")
    yield
    logger.info("========== 服务关闭 ==========")


app = FastAPI(
    title="跨境物流规则智能Agent",
    description="RAG + 工作流轻量化Agent，专注中国出口德国跨境清关规则",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "cross-border-agent"}


# ============ 路由注册（各服务模块统一挂载） ============
app.include_router(chat.router, prefix="/api/chat", tags=["问答"])
app.include_router(doc.router, prefix="/api/doc", tags=["知识库"])
app.include_router(spider.router, prefix="/api/spider", tags=["爬虫"])
app.include_router(eval_api.router, prefix="/api/eval", tags=["评测"])
