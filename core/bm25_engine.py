"""BM25 引擎统一封装：索引构建、查询、增量更新"""

import time
import math
import re
from typing import Optional
from rank_bm25 import BM25Okapi
import jieba
from config.logging_config import get_logger

logger = get_logger("bm25_engine")


def _tokenize_chinese(text: str) -> list[str]:
    """中英文混合分词：中文用 jieba，英文按空格切分并转小写"""
    text = text.lower()
    # 英文单词用正则切出
    eng_tokens = re.findall(r'[a-z][a-z0-9]+', text)
    # 中文部分用 jieba 切分
    zh_text = re.sub(r'[a-z0-9]+', ' ', text)
    zh_tokens = [t.strip() for t in jieba.cut(zh_text) if t.strip() and len(t.strip()) > 1]
    return zh_tokens + eng_tokens


class BM25Engine:
    """BM25 检索引擎：支持增量更新、中英文分词"""

    def __init__(self):
        self._corpus_tokens: list[list[str]] = []
        self._doc_ids: list[str] = []
        self._metadata: list[dict] = []
        self._bm25: Optional[BM25Okapi] = None
        self._dirty = True  # 标记索引是否需要重建
        logger.info("BM25Engine 初始化完成")

    @property
    def doc_count(self) -> int:
        return len(self._doc_ids)

    def build_index(
        self,
        doc_ids: list[str],
        texts: list[str],
        metadata: list[dict] = None,
    ):
        """全量构建 BM25 索引"""
        start = time.time()
        self._doc_ids = list(doc_ids)
        self._corpus_tokens = [_tokenize_chinese(t) for t in texts]
        self._metadata = list(metadata) if metadata else [{} for _ in doc_ids]
        self._bm25 = BM25Okapi(self._corpus_tokens)
        self._dirty = False
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"BM25 索引构建完成 | doc_count={len(doc_ids)} | elapsed={elapsed}s"
        )

    def add_documents(
        self,
        doc_ids: list[str],
        texts: list[str],
        metadata: list[dict] = None,
    ):
        """增量添加文档到索引，触发重建"""
        start = time.time()
        new_meta = list(metadata) if metadata else [{} for _ in doc_ids]
        self._doc_ids.extend(doc_ids)
        self._corpus_tokens.extend([_tokenize_chinese(t) for t in texts])
        self._metadata.extend(new_meta)
        self._bm25 = BM25Okapi(self._corpus_tokens)
        self._dirty = False
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"BM25 索引增量更新 | added={len(doc_ids)} | total={len(self._doc_ids)} | elapsed={elapsed}s"
        )

    def remove_documents(self, doc_ids: list[str]):
        """按 doc_id 列表移除文档"""
        start = time.time()
        id_set = set(doc_ids)
        new_indices = [i for i, did in enumerate(self._doc_ids) if did not in id_set]
        self._doc_ids = [self._doc_ids[i] for i in new_indices]
        self._corpus_tokens = [self._corpus_tokens[i] for i in new_indices]
        self._metadata = [self._metadata[i] for i in new_indices]
        if self._doc_ids:
            self._bm25 = BM25Okapi(self._corpus_tokens)
        else:
            self._bm25 = None
        self._dirty = False
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"BM25 索引移除文档 | removed={len(doc_ids)} | remaining={len(self._doc_ids)} | elapsed={elapsed}s"
        )

    def update_documents(
        self,
        doc_ids: list[str],
        texts: list[str],
        metadata: list[dict] = None,
    ):
        """更新指定 doc_id 的文本（先删后加）"""
        self.remove_documents(doc_ids)
        self.add_documents(doc_ids, texts, metadata)

    def query(
        self,
        query_text: str,
        top_k: int = 8,
    ) -> list[dict]:
        """BM25 检索，返回按得分降序的文档列表"""
        start = time.time()
        if self._bm25 is None or len(self._doc_ids) == 0:
            logger.warning("BM25 索引为空，返回空结果")
            return []

        query_tokens = _tokenize_chinese(query_text)
        scores = self._bm25.get_scores(query_tokens)

        # 取 top_k
        indexed_scores = list(enumerate(scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        top_results = indexed_scores[:top_k]

        results = []
        for idx, score in top_results:
            if score <= 0:
                continue
            results.append({
                "doc_id": self._doc_ids[idx],
                "score": float(score),
                "metadata": self._metadata[idx],
            })

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"BM25 检索完成 | query={query_text[:50]} | hits={len(results)} | elapsed={elapsed}s"
        )
        return results

    def get_tokens_by_doc_id(self, doc_id: str) -> Optional[list[str]]:
        """根据 doc_id 获取分词结果"""
        try:
            idx = self._doc_ids.index(doc_id)
            return self._corpus_tokens[idx]
        except ValueError:
            return None


# 全局单例
bm25_engine = BM25Engine()
