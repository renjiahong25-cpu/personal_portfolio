"""Milvus Lite 向量库操作封装：连接管理、写入、检索、内存优化"""

import time
from typing import Optional
from sqlalchemy.orm import Session

from config.settings import (
    MILVUS_COLLECTION,
    MILVUS_BATCH_INSERT_SIZE,
    MILVUS_CACHE_LIMIT,
    RETRIEVE_TOP_K,
    SIMILARITY_THRESHOLD,
    VECTOR_WEIGHT,
    BM25_WEIGHT,
)
from config.logging_config import get_logger
from db.models.base import DocParagraph, get_db
from core.vector_client import vector_client

logger = get_logger("vector_store")


class VectorStore:
    """
    Milvus Lite 向量库操作封装：
    - 连接管理与 Collection 初始化
    - 向量写入（支持批量、增量）
    - 向量检索（支持过滤条件）
    - 内存优化（4G 缓存上限、200 条批量入库）
    """

    def __init__(self):
        self._initialized = False
        logger.info("VectorStore 初始化")

    def ensure_initialized(self):
        """确保连接和 Collection 就绪"""
        if self._initialized:
            return
        start = time.time()
        vector_client.connect()
        vector_client.create_collection()
        self._initialized = True
        elapsed = round(time.time() - start, 3)
        logger.info(f"VectorStore 初始化完成 | elapsed={elapsed}s")

    # ------------------------------------------------------------------
    # 向量写入
    # ------------------------------------------------------------------
    def write_paragraphs(
        self,
        paragraphs: list[dict],
        collection_name: str = "",
    ) -> list[int]:
        """
        批量写入段落向量。
        paragraphs: [{"doc_uuid": str, "chapter_path": str, "paragraph_id": int,
                       "content_text": str, "embedding": list[float],
                       "source_url": str, "version": str, "is_draft": bool}]
        返回插入的 id 列表
        """
        start = time.time()
        self.ensure_initialized()
        try:
            ids = vector_client.batch_insert(
                paragraphs,
                batch_size=MILVUS_BATCH_INSERT_SIZE,
                collection_name=collection_name or MILVUS_COLLECTION,
            )
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"段落向量写入完成 | count={len(ids)} | elapsed={elapsed}s"
            )
            return ids
        except Exception as e:
            logger.error(
                f"段落向量写入失败 | count={len(paragraphs)} | error={e}",
                exc_info=True,
            )
            raise

    def upsert_paragraphs(
        self,
        paragraphs: list[dict],
        collection_name: str = "",
    ):
        """upsert 段落向量（用于局部更新）"""
        start = time.time()
        self.ensure_initialized()
        try:
            vector_client.upsert(
                paragraphs,
                collection_name=collection_name or MILVUS_COLLECTION,
            )
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"段落向量 upsert 完成 | count={len(paragraphs)} | elapsed={elapsed}s"
            )
        except Exception as e:
            logger.error(
                f"段落向量 upsert 失败 | count={len(paragraphs)} | error={e}",
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------
    # 向量检索
    # ------------------------------------------------------------------
    def search(
        self,
        query_vectors: list[list[float]],
        top_k: int = RETRIEVE_TOP_K,
        filter_expr: str = "",
        collection_name: str = "",
    ) -> list[list[dict]]:
        """
        向量检索，返回按相似度降序的结果。
        filter_expr 示例：'doc_uuid == "xxx" && is_draft == false'
        """
        start = time.time()
        self.ensure_initialized()
        try:
            results = vector_client.search(
                query_vectors=query_vectors,
                top_k=top_k,
                filter_expr=filter_expr,
                collection_name=collection_name or MILVUS_COLLECTION,
            )
            elapsed = round(time.time() - start, 3)
            total = sum(len(r) for r in results) if results else 0
            logger.info(
                f"向量检索完成 | query_count={len(query_vectors)} | "
                f"top_k={top_k} | hits={total} | elapsed={elapsed}s"
            )
            return results
        except Exception as e:
            logger.error(f"向量检索失败 | error={e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # 融合打分（向量 + BM25）
    # ------------------------------------------------------------------
    def fusion_search(
        self,
        query_vectors: list[list[float]],
        bm25_results: list[dict],
        top_k: int = RETRIEVE_TOP_K,
        vector_weight: float = VECTOR_WEIGHT,
        bm25_weight: float = BM25_WEIGHT,
        filter_expr: str = "",
    ) -> list[dict]:
        """
        向量检索 + BM25 融合打分。
        返回归一化加权分数降序的文档列表。
        """
        start = time.time()

        # 向量检索
        vector_results = self.search(
            query_vectors=query_vectors,
            top_k=top_k * 2,  # 多取一些以保证融合效果
            filter_expr=filter_expr,
        )

        # 归一化向量分数（cosine similarity 已在 0~1 范围）
        vector_score_map = {}
        for hits in vector_results:
            for hit in hits:
                doc_id = hit.get("entity", {}).get("paragraph_id", hit.get("id"))
                distance = hit.get("distance", 0.0)
                vector_score_map[str(doc_id)] = max(
                    vector_score_map.get(str(doc_id), 0.0), distance
                )

        # 归一化 BM25 分数
        bm25_max = max((r["score"] for r in bm25_results), default=1.0) or 1.0
        bm25_score_map = {}
        for r in bm25_results:
            doc_id = r["doc_id"]
            bm25_score_map[str(doc_id)] = r["score"] / bm25_max

        # 合并所有文档 ID
        all_doc_ids = set(vector_score_map.keys()) | set(bm25_score_map.keys())
        fused = []
        for doc_id in all_doc_ids:
            v_score = vector_score_map.get(doc_id, 0.0)
            b_score = bm25_score_map.get(doc_id, 0.0)
            final_score = vector_weight * v_score + bm25_weight * b_score
            if final_score >= SIMILARITY_THRESHOLD * 0.5:  # 宽松过滤
                fused.append({
                    "doc_id": doc_id,
                    "score": round(final_score, 4),
                    "vector_score": round(v_score, 4),
                    "bm25_score": round(b_score, 4),
                })

        fused.sort(key=lambda x: x["score"], reverse=True)
        result = fused[:top_k]
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"融合检索完成 | vector_hits={len(vector_score_map)} | "
            f"bm25_hits={len(bm25_score_map)} | fused={len(result)} | elapsed={elapsed}s"
        )
        return result

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------
    def delete_by_doc_uuid(self, doc_uuid: str, collection_name: str = ""):
        """删除指定文档的所有向量"""
        self.ensure_initialized()
        vector_client.delete_by_doc_uuid(
            doc_uuid,
            collection_name=collection_name or MILVUS_COLLECTION,
        )
        logger.info(f"文档向量已删除 | doc_uuid={doc_uuid}")

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        """向量库健康检查"""
        return vector_client.health_check()

    def get_collection_stats(self) -> dict:
        """获取 Collection 统计信息"""
        self.ensure_initialized()
        try:
            collection = vector_client.get_collection()
            collection.load()
            return {
                "name": collection.name,
                "num_entities": collection.num_entities,
            }
        except Exception as e:
            logger.error(f"获取 Collection 统计失败 | error={e}", exc_info=True)
            return {"error": str(e)}


# 全局单例
vector_store = VectorStore()
