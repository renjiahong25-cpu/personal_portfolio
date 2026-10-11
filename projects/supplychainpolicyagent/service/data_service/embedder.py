# -*- coding: utf-8 -*-
"""文本向量化服务（SentenceTransformer 惰性加载，供文档上传/检索共用）

P4：配置 EMBED_HTTP_URL 后优先走独立 Embed 微服务；微服务不可用自动降级本地模型。
"""

import asyncio
import threading
import time
from typing import Optional

import httpx

from config.logging_config import get_logger
from config import settings

logger = get_logger("embedder")

try:
    from sentence_transformers import SentenceTransformer

    _ST_AVAILABLE = True
except Exception:
    SentenceTransformer = None
    _ST_AVAILABLE = False
    logger.warning("sentence-transformers 不可用，向量化功能将降级")


class Embedder:
    """单例向量化器：优先远程微服务，回退本地模型（线程池推理避免阻塞事件循环）"""

    def __init__(self):
        self._model: Optional[SentenceTransformer] = None
        self._lock = threading.Lock()
        self._remote_last_fail = 0.0
        self._remote_fail_count = 0

    @property
    def remote_enabled(self) -> bool:
        return bool((settings.EMBED_HTTP_URL or "").strip())

    def _remote_embed(self, texts: list[str]):
        """调远程 Embed 微服务；失败返回 None 并降级本地。告警 60s 频控。"""
        url = settings.EMBED_HTTP_URL.rstrip("/") + "/embed"
        try:
            r = httpx.post(url, json={"texts": texts},
                           timeout=settings.EMBED_HTTP_TIMEOUT)
            r.raise_for_status()
            vectors = r.json().get("vectors")
            if vectors is not None:
                return vectors
            raise ValueError("远程嵌入返回异常")
        except Exception as e:
            self._remote_fail_count += 1
            now = time.time()
            if now - self._remote_last_fail > 60:
                self._remote_last_fail = now
                logger.warning(
                    f"远程向量化失败（累计 {self._remote_fail_count} 次），降级本地模型 | error={e}")
            return None

    def _ensure_model(self) -> bool:
        if not _ST_AVAILABLE:
            return False
        if self._model is None:
            with self._lock:
                if self._model is None:
                    logger.info(f"加载向量模型: {settings.EMBEDDING_MODEL}")
                    self._model = SentenceTransformer(settings.EMBEDDING_MODEL)
                    logger.info("向量模型加载完成")
        return self._model is not None

    def encode(self, texts: list[str]) -> list[list[float]]:
        """编码文本列表，返回向量列表（远程优先，失败/未配置时本地）。"""
        if self.remote_enabled:
            vecs = self._remote_embed(texts)
            if vecs is not None:
                return vecs
        if not self._ensure_model():
            raise RuntimeError("向量模型不可用，无法编码")
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() if hasattr(v, "tolist") else list(v) for v in vecs]

    def encode_text(self, text: str) -> list[float]:
        """编码单条文本"""
        return self.encode([text])[0]

    async def aencode(self, texts: list[str]) -> list[list[float]]:
        """异步编码：CPU 推理放入线程池"""
        if not texts:
            return []
        return await asyncio.to_thread(self.encode, texts)

    def available(self) -> bool:
        return _ST_AVAILABLE or self.remote_enabled


embedder = Embedder()