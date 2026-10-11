"""BM25 索引构建和检索：支持中英文分词、增量更新、与向量检索融合打分"""

import time
from sqlalchemy.orm import Session

from config.settings import RETRIEVE_TOP_K
from config.logging_config import get_logger
from db.models.base import DocParagraph, get_db
from core.bm25_engine import bm25_engine
from service.data_service.vector_store import vector_store

logger = get_logger("bm25_index")


class BM25Index:
    """BM25 索引管理：构建、更新、检索、融合"""

    def __init__(self):
        logger.info("BM25Index 初始化")

    # ------------------------------------------------------------------
    # 全量构建
    # ------------------------------------------------------------------
    def build_full_index(self, db: Session = None) -> int:
        """
        从数据库全量构建 BM25 索引。
        返回索引的文档数量。
        """
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            paras = db.query(DocParagraph).all()
            if not paras:
                logger.warning("数据库中无段落数据，跳过索引构建")
                return 0

            doc_ids = []
            texts = []
            metadata_list = []

            for p in paras:
                doc_id = f"{p.doc_uuid}_{p.id}"
                text = p.content_summary or p.content_raw or ""
                doc_ids.append(doc_id)
                texts.append(text)
                metadata_list.append({
                    "doc_uuid": p.doc_uuid,
                    "paragraph_id": p.id,
                    "chapter_id": p.chapter_id,
                    "token_len": p.token_len,
                })

            bm25_engine.build_index(doc_ids, texts, metadata_list)
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"BM25 全量索引构建完成 | count={len(doc_ids)} | elapsed={elapsed}s"
            )
            return len(doc_ids)
        except Exception as e:
            logger.error(f"BM25 全量索引构建失败 | error={e}", exc_info=True)
            raise
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 增量更新
    # ------------------------------------------------------------------
    def add_paragraphs(self, doc_uuid: str, paragraph_ids: list[int], db: Session = None):
        """增量添加指定段落到 BM25 索引"""
        start = time.time()
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            paras = db.query(DocParagraph).filter(
                DocParagraph.doc_uuid == doc_uuid,
                DocParagraph.id.in_(paragraph_ids),
            ).all()

            doc_ids = []
            texts = []
            metadata_list = []

            for p in paras:
                doc_id = f"{p.doc_uuid}_{p.id}"
                text = p.content_summary or p.content_raw or ""
                doc_ids.append(doc_id)
                texts.append(text)
                metadata_list.append({
                    "doc_uuid": p.doc_uuid,
                    "paragraph_id": p.id,
                    "chapter_id": p.chapter_id,
                    "token_len": p.token_len,
                })

            if doc_ids:
                bm25_engine.add_documents(doc_ids, texts, metadata_list)
                elapsed = round(time.time() - start, 3)
                logger.info(
                    f"BM25 增量添加完成 | added={len(doc_ids)} | elapsed={elapsed}s"
                )
        except Exception as e:
            logger.error(
                f"BM25 增量添加失败 | doc_uuid={doc_uuid} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    def remove_by_doc_uuid(self, doc_uuid: str, db: Session = None):
        """删除指定文档的所有段落索引"""
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            paras = db.query(DocParagraph).filter(
                DocParagraph.doc_uuid == doc_uuid,
            ).all()

            doc_ids = [f"{p.doc_uuid}_{p.id}" for p in paras]
            if doc_ids:
                bm25_engine.remove_documents(doc_ids)
                logger.info(f"BM25 删除完成 | doc_uuid={doc_uuid} | count={len(doc_ids)}")
        except Exception as e:
            logger.error(
                f"BM25 删除失败 | doc_uuid={doc_uuid} | error={e}",
                exc_info=True,
            )
            raise
        finally:
            if close_db:
                db.close()

    def update_paragraphs(self, doc_uuid: str, paragraph_ids: list[int], db: Session = None):
        """更新指定段落的索引（先删后加）"""
        self.remove_paragraphs_by_ids(doc_uuid, paragraph_ids, db)
        self.add_paragraphs(doc_uuid, paragraph_ids, db)

    def remove_paragraphs_by_ids(self, doc_uuid: str, paragraph_ids: list[int], db: Session = None):
        """按 paragraph_ids 删除索引"""
        close_db = False
        if db is None:
            db = next(get_db())
            close_db = True

        try:
            doc_ids = [f"{doc_uuid}_{pid}" for pid in paragraph_ids]
            bm25_engine.remove_documents(doc_ids)
        finally:
            if close_db:
                db.close()

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def query(self, query_text: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
        """BM25 检索"""
        start = time.time()
        results = bm25_engine.query(query_text, top_k)
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"BM25 检索完成 | query={query_text[:50]} | hits={len(results)} | elapsed={elapsed}s"
        )
        return results

    # ------------------------------------------------------------------
    # 融合检索（BM25 + 向量）
    # ------------------------------------------------------------------
    def fusion_query(
        self,
        query_text: str,
        query_vectors: list[list[float]],
        top_k: int = RETRIEVE_TOP_K,
        filter_expr: str = "",
    ) -> list[dict]:
        """
        BM25 + 向量融合检索。
        返回归一化加权分数降序的文档列表。
        """
        start = time.time()
        bm25_results = self.query(query_text, top_k=top_k * 2)
        fused = vector_store.fusion_search(
            query_vectors=query_vectors,
            bm25_results=bm25_results,
            top_k=top_k,
            filter_expr=filter_expr,
        )
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"融合检索完成 | query={query_text[:50]} | hits={len(fused)} | elapsed={elapsed}s"
        )
        return fused

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def get_index_stats(self) -> dict:
        """获取索引统计信息"""
        return {
            "doc_count": bm25_engine.doc_count,
        }


# 全局单例
bm25_index = BM25Index()
