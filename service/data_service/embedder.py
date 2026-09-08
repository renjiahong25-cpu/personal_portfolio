# -*- coding: utf-8 -*-
"""文本向量化服务（SentenceTransformer 惰性加载，供文档上传/检索共用）"""

import asyncio
import threading
from typing import Optional

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
    """单例向量化器：CPU 推理走线程池避免阻塞事件循环"""

    def __init__(self):
        self._model: Optional[SentenceTransformer] = None
        self._lock = threading.Lock()

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
        """编码文本列表，返回向量列表"""
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
        return _ST_AVAILABLE


embedder = Embedder()