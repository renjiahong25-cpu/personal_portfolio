# -*- coding: utf-8 -*-
"""
独立 Embed / Rerank 微服务（SERVICE_ROLE=embed）
=====================================================
模型单驻本进程（MiniLM-384d 编码 + CrossEncoder 精排），
policy/chat 多副本不再各自加载模型，只通过 HTTP 调用本服务。
本服务/模型挂掉时，调用方自动降级本地 BM25-only（BM25 索引仍在业务进程内）。

接口：
    GET  /health    健康检查（embed_ok / rerank_ok）
    POST /embed     文本批量向量化 {texts:[...]} → {vectors:[[...]]}
    POST /rerank    查询相关性重排 {query, documents, top_k} → {results:[...]}
"""
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config.logging_config import setup_logging, get_logger
from config import settings
from service.data_service.embedder import Embedder

logger = get_logger("embed_server")

_embedder = Embedder()
_rerank_model = None
_rerank_ok = False
_rerank_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("embed")
    logger.info("========== Embed 微服务启动 ==========")
    try:
        if _embedder.available():
            _embedder._ensure_model()
            logger.info(f"Embed 模型就绪 | model={settings.EMBEDDING_MODEL}")
    except Exception as e:
        logger.warning(f"Embed 模型加载失败（覆盖率脚本仍可用） | error={e}")
    yield
    logger.info("========== Embed 微服务关闭 ==========")


app = FastAPI(title="embed-service", version="1.0.0", lifespan=lifespan)


class EmbedReq(BaseModel):
    texts: list[str] = []


class RerankReq(BaseModel):
    query: str
    documents: list[str] = []
    top_k: int = 0


def _ensure_rerank() -> bool:
    global _rerank_model, _rerank_ok
    if _rerank_ok or not settings.RERANK_ENABLE:
        return _rerank_ok
    with _rerank_lock:
        if _rerank_ok:
            return True
        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"加载 Rerank 模型: {settings.RERANK_MODEL}")
            _rerank_model = CrossEncoder(settings.RERANK_MODEL)
            _rerank_ok = True
            logger.info("Rerank 模型加载完成")
        except Exception as e:
            logger.error(f"Rerank 模型加载失败 | error={e}")
            _rerank_model = None
            _rerank_ok = False
    return _rerank_ok


@app.get("/health")
def health():
    """健康检查：报告两端模型就绪状态（调用方据此降级）。"""
    return {"status": "ok", "service": "embed",
            "embed_ok": _embedder.available(), "rerank_ok": _rerank_ok}


@app.post("/embed")
def embed(req: EmbedReq):
    """文本批量向量化（单条/批量统一走 encode）。"""
    if not req.texts:
        return {"vectors": []}
    try:
        vecs = _embedder.encode(req.texts)
        logger.info(f"embed | count={len(vecs)} | dim={len(vecs[0]) if vecs else 0}")
        return {"vectors": [list(v) for v in vecs]}
    except Exception as e:
        logger.error(f"embed 失败 | error={e}")
        raise HTTPException(status_code=500, detail=f"embed 失败: {e}")


@app.post("/rerank")
def rerank(req: RerankReq):
    """查询-文档相关性打分并重排（top_k=0 返回全部）。"""
    if not req.documents:
        return {"results": []}
    if not _ensure_rerank():
        raise HTTPException(status_code=503, detail="rerank 未启用或模型不可用")
    try:
        pairs = [[req.query, d] for d in req.documents]
        scores = _rerank_model.predict(pairs)
        scored = sorted(zip(req.documents, (float(s) for s in scores)),
                        key=lambda x: x[1], reverse=True)
        if req.top_k > 0:
            scored = scored[: req.top_k]
        logger.info(f"rerank | in={len(req.documents)} | top={req.top_k or len(req.documents)}")
        return {"results": [{"document": d, "score": s} for d, s in scored]}
    except Exception as e:
        logger.error(f"rerank 失败 | error={e}")
        raise HTTPException(status_code=500, detail=f"rerank 失败: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.EMBED_SERVICE_PORT)