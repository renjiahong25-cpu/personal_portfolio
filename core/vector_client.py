"""Milvus Lite 统一客户端封装"""

import time
from typing import Optional
from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)
from config.settings import MILVUS_HOST, MILVUS_PORT, MILVUS_COLLECTION, MILVUS_CACHE_LIMIT
from config.logging_config import get_logger

logger = get_logger("vector_client")

# 向量维度（与 sentence-transformers 默认模型一致）
VECTOR_DIM = 1024


class VectorClient:
    """Milvus Lite 统一客户端：连接管理、Collection CRUD、健康检查"""

    def __init__(self):
        self._connected = False
        self._collection: Optional[Collection] = None
        self.collection_name = MILVUS_COLLECTION
        logger.info(
            f"VectorClient 初始化 | host={MILVUS_HOST} | port={MILVUS_PORT} | "
            f"collection={self.collection_name}"
        )

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self):
        """建立到 Milvus 的连接"""
        start = time.time()
        try:
            connections.connect(
                alias="default",
                host=MILVUS_HOST,
                port=MILVUS_PORT,
            )
            self._connected = True
            elapsed = round(time.time() - start, 3)
            logger.info(f"Milvus 连接成功 | elapsed={elapsed}s")
        except Exception as e:
            logger.error(f"Milvus 连接失败 | error={e}", exc_info=True)
            raise

    def disconnect(self):
        """断开 Milvus 连接"""
        try:
            connections.disconnect("default")
            self._connected = False
            logger.info("Milvus 连接已断开")
        except Exception as e:
            logger.error(f"Milvus 断开失败 | error={e}", exc_info=True)

    def health_check(self) -> bool:
        """健康检查：能否正常 ping 到 Milvus"""
        try:
            if not self._connected:
                self.connect()
            result = utility.get_server_version()
            logger.info(f"Milvus 健康检查通过 | version={result}")
            return True
        except Exception as e:
            logger.error(f"Milvus 健康检查失败 | error={e}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Collection CRUD
    # ------------------------------------------------------------------
    def _build_schema(self) -> CollectionSchema:
        """构建 Collection Schema"""
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
        schema = CollectionSchema(fields, description="跨境清关规则向量存储")
        logger.debug("Collection Schema 构建完成")
        return schema

    def create_collection(self, collection_name: str = "") -> Collection:
        """创建 Collection，若已存在则直接返回"""
        name = collection_name or self.collection_name
        start = time.time()
        try:
            if utility.has_collection(name):
                logger.info(f"Collection 已存在 | name={name}")
                self._collection = Collection(name)
                return self._collection

            schema = self._build_schema()
            collection = Collection(name=name, schema=schema)
            # 为 embedding 字段创建 IVF_FLAT 索引
            index_params = {
                "metric_type": "COSINE",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128},
            }
            collection.create_index(field_name="embedding", index_params=index_params)
            self._collection = collection
            elapsed = round(time.time() - start, 3)
            logger.info(f"Collection 创建完成 | name={name} | elapsed={elapsed}s")
            return collection
        except Exception as e:
            logger.error(f"Collection 创建失败 | name={name} | error={e}", exc_info=True)
            raise

    def drop_collection(self, collection_name: str = ""):
        """删除 Collection"""
        name = collection_name or self.collection_name
        try:
            utility.drop_collection(name)
            self._collection = None
            logger.info(f"Collection 已删除 | name={name}")
        except Exception as e:
            logger.error(f"Collection 删除失败 | name={name} | error={e}", exc_info=True)
            raise

    def get_collection(self, collection_name: str = "") -> Collection:
        """获取已有 Collection，若不存在则创建"""
        name = collection_name or self.collection_name
        if self._collection is not None:
            return self._collection
        return self.create_collection(name)

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def insert(self, data: list[dict], collection_name: str = "") -> list[int]:
        """批量插入向量数据，返回插入的 id 列表"""
        start = time.time()
        collection = self.get_collection(collection_name)
        try:
            result = collection.insert(data)
            collection.flush()
            elapsed = round(time.time() - start, 3)
            ids = list(result.primary_keys)
            logger.info(
                f"向量写入完成 | count={len(ids)} | elapsed={elapsed}s | "
                f"collection={collection.name}"
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
        collection = self.get_collection(collection_name)
        try:
            collection.load()
            search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
            results = collection.search(
                data=query_vectors,
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                expr=filter_expr if filter_expr else None,
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
            # 转换为 dict 列表
            output = []
            for hits in results:
                hit_list = []
                for hit in hits:
                    hit_dict = {
                        "id": hit.id,
                        "distance": hit.distance,
                        "entity": hit.entity.to_dict() if hasattr(hit.entity, "to_dict") else {},
                    }
                    hit_list.append(hit_dict)
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
        collection = self.get_collection(collection_name)
        try:
            collection.delete(expr)
            collection.flush()
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
        collection = self.get_collection(collection_name)
        try:
            collection.upsert(data)
            collection.flush()
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"向量 upsert 完成 | count={len(data)} | elapsed={elapsed}s"
            )
        except Exception as e:
            logger.error(f"向量 upsert 失败 | error={e}", exc_info=True)
            raise


# 全局单例
vector_client = VectorClient()
