"""Milvus Lite 统一客户端封装（本地文件模式）"""

import time
from typing import Optional
from pathlib import Path

from pymilvus import MilvusClient, CollectionSchema, FieldSchema, DataType
from pymilvus.milvus_client.index import IndexParams
from config.settings import (
    MILVUS_URI,
    MILVUS_COLLECTION,
    EMBEDDING_DIM,
)
from config.logging_config import get_logger

logger = get_logger("vector_client")

# 向量维度（与 EMBEDDING_MODEL 输出维度一致）
VECTOR_DIM = EMBEDDING_DIM


class VectorClient:
    """Milvus Lite 统一客户端：本地文件模式，连接管理、Collection CRUD、健康检查"""

    def __init__(self):
        self._connected = False
        self._collection: Optional[MilvusClient] = None
        self.collection_name = MILVUS_COLLECTION
        logger.info(
            f"VectorClient 初始化 | uri={MILVUS_URI} | "
            f"collection={self.collection_name} | dim={VECTOR_DIM}"
        )

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self):
        """建立到 Milvus Lite 本地文件的连接"""
        start = time.time()
        try:
            if not MILVUS_URI.startswith(("http://", "https://", "tcp://")):
                Path(MILVUS_URI).parent.mkdir(parents=True, exist_ok=True)
            self._collection = MilvusClient(uri=MILVUS_URI)
            self._connected = True
            elapsed = round(time.time() - start, 3)
            logger.info(f"Milvus Lite 连接成功 | uri={MILVUS_URI} | elapsed={elapsed}s")
        except Exception as e:
            logger.error(f"Milvus Lite 连接失败 | error={e}", exc_info=True)
            raise

    def disconnect(self):
        """断开 Milvus 连接"""
        try:
            self._collection.close()
            self._connected = False
            logger.info("Milvus Lite 连接已断开")
        except Exception as e:
            logger.error(f"Milvus 断开失败 | error={e}", exc_info=True)

    def health_check(self) -> bool:
        """健康检查：能否访问集合"""
        try:
            if not self._connected:
                self.connect()
            # 仅探测集合存在性，轻量无副作用
            self._collection.has_collection(self.collection_name)
            logger.info("Milvus Lite 健康检查通过")
            return True
        except Exception as e:
            logger.error(f"Milvus Lite 健康检查失败 | error={e}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Collection CRUD
    # ------------------------------------------------------------------
    def _build_schema(self) -> CollectionSchema:
        """构建 Collection Schema（CollectionSchema 对象）"""
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="doc_uuid", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="chapter_path", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="paragraph_id", dtype=DataType.INT64),
            FieldSchema(name="content_type", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="content_text", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
            FieldSchema(name="source_url", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="version", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="is_draft", dtype=DataType.BOOL),
        ]
        return CollectionSchema(fields, description="跨境清关规则向量存储")

    def create_collection(self, collection_name: str = ""):
        """创建 Collection，若已存在则直接返回"""
        name = collection_name or self.collection_name
        if not self._collection:
            self.connect()
        start = time.time()
        try:
            if self._collection.has_collection(name):
                logger.info(f"Collection 已存在 | name={name}")
                return True
            schema = self._build_schema()
            index_params = IndexParams()
            index_params.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            self._collection.create_collection(
                collection_name=name,
                schema=schema,
                index_params=index_params,
            )
            elapsed = round(time.time() - start, 3)
            logger.info(f"Collection 创建完成 | name={name} | elapsed={elapsed}s")
            return True
        except Exception as e:
            logger.error(f"Collection 创建失败 | name={name} | error={e}", exc_info=True)
            raise

    def drop_collection(self, collection_name: str = ""):
        """删除 Collection"""
        name = collection_name or self.collection_name
        try:
            if not self._collection:
                self.connect()
            self._collection.drop_collection(name)
            logger.info(f"Collection 已删除 | name={name}")
        except Exception as e:
            logger.error(f"Collection 删除失败 | name={name} | error={e}", exc_info=True)
            raise

    def get_collection(self, collection_name: str = "") -> MilvusClient:
        """获取客户端；若 Collection 不存在则创建"""
        name = collection_name or self.collection_name
        if self._collection is None:
            self.connect()
        self.create_collection(name)
        return self._collection

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def insert(self, data: list[dict], collection_name: str = "") -> list[int]:
        """批量插入向量数据，返回插入的 id 列表"""
        start = time.time()
        collection = self.get_collection(collection_name)
        try:
            result = collection.insert(collection_name=collection_name or self.collection_name, data=data)
            elapsed = round(time.time() - start, 3)
            ids = list(result.get("ids", [])) if isinstance(result, dict) else []
            logger.info(
                f"向量写入完成 | count={len(ids)} | elapsed={elapsed}s | "
                f"collection={collection_name or self.collection_name}"
            )
            return ids
        except Exception as e:
            logger.error(
                f"向量写入失败 | count={len(data)} | error={e}", exc_info=True
            )
            raise

    def batch_insert(self, data_list: list[dict], batch_size: int = 200,
                     collection_name: str = "") -> list[int]:
        """分批插入，每批 batch_size 条"""
        start = time.time()
        all_ids = []
        for i in range(0, len(data_list), batch_size):
            batch = data_list[i : i + batch_size]
            ids = self.insert(batch, collection_name)
            all_ids.extend(ids)
        elapsed = round(time.time() - start, 3)
        logger.info(
            f"批量写入完成 | total={len(all_ids)} | batches={len(range(0, len(data_list), batch_size))} | elapsed={elapsed}s"
        )
        return all_ids

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(
        self,
        query_vectors: list[list[float]],
        top_k: int = 8,
        output_fields: list[str] = None,
        filter_expr: str = "",
        collection_name: str = "",
    ) -> list[list[dict]]:
        """向量检索，返回按相似度降序的结果列表"""
        start = time.time()
        self.get_collection(collection_name)
        name = collection_name or self.collection_name
        try:
            results = self._collection.search(
                collection_name=name,
                data=query_vectors,
                anns_field="embedding",
                limit=top_k,
                filter=filter_expr if filter_expr else None,
                output_fields=output_fields or [
                    "doc_uuid", "chapter_path", "paragraph_id",
                    "content_text", "source_url", "version", "is_draft",
                ],
            )
            elapsed = round(time.time() - start, 3)
            total_hits = sum(len(r) for r in results)
            logger.info(
                f"向量检索完成 | query_count={len(query_vectors)} | top_k={top_k} | "
                f"hits={total_hits} | elapsed={elapsed}s"
            )
            # 转换为统一 dict 结构：id / distance / entity
            output = []
            for hits in results:
                hit_list = []
                for hit in hits:
                    entity = dict(hit.get("entity", {}) or {})
                    hit_list.append({
                        "id": hit.get("id"),
                        "distance": hit.get("distance"),
                        "entity": entity,
                    })
                output.append(hit_list)
            return output
        except Exception as e:
            logger.error(f"向量检索失败 | error={e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # 删除 / 更新
    # ------------------------------------------------------------------
    def delete(self, expr: str, collection_name: str = ""):
        """按表达式删除向量"""
        start = time.time()
        self.get_collection(collection_name)
        name = collection_name or self.collection_name
        try:
            self._collection.delete(collection_name=name, filter=expr)
            elapsed = round(time.time() - start, 3)
            logger.info(f"向量删除完成 | expr={expr} | elapsed={elapsed}s")
        except Exception as e:
            logger.error(f"向量删除失败 | expr={expr} | error={e}", exc_info=True)
            raise

    def delete_by_doc_uuid(self, doc_uuid: str, collection_name: str = ""):
        """按文档 UUID 删除所有关联向量"""
        self.delete(f'doc_uuid == "{doc_uuid}"', collection_name)

    def upsert(self, data: list[dict], collection_name: str = ""):
        """upsert 向量数据（需要数据中包含 id 字段）"""
        start = time.time()
        self.get_collection(collection_name)
        name = collection_name or self.collection_name
        try:
            self._collection.upsert(collection_name=name, data=data)
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"向量 upsert 完成 | count={len(data)} | elapsed={elapsed}s"
            )
        except Exception as e:
            logger.error(f"向量 upsert 失败 | error={e}", exc_info=True)
            raise


# 全局单例
vector_client = VectorClient()