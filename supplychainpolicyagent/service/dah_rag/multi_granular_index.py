# -*- coding: utf-8 -*-
"""
Multi-Granular Index Module
============================
多粒度索引，同时索引文档/章节/段落/实体多个层级

索引层级：
1. DOCUMENT - 文档级（完整文档摘要）
2. CHAPTER - 章节级（章节标题+首段）
3. PARAGRAPH - 段落级（标准段落）
4. ENTITY - 实体级（HS编码、法规名称等）
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

from config.logging_config import get_logger
from config import settings

logger = get_logger("multi_granular_index")


class IndexLevel(Enum):
    """索引层级"""
    DOCUMENT = "document"
    CHAPTER = "chapter"
    PARAGRAPH = "paragraph"
    ENTITY = "entity"


@dataclass
class IndexItem:
    """索引项"""
    id: str
    level: IndexLevel
    content: str
    embedding: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    parent_id: str = ""  # 父级ID（段落->章节->文档）
    children_ids: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level": self.level.value,
            "content": self.content,
            "metadata": self.metadata,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
        }


class MultiGranularIndex:
    """
    多粒度索引
    
    同时维护多个层级的索引，根据查询复杂度选择最优层级
    """
    
    def __init__(self):
        # 多层级索引
        self._indexes: dict[IndexLevel, dict[str, IndexItem]] = {
            level: {} for level in IndexLevel
        }
        
        # 向量索引（简化版，实际应用中应使用 Milvus）
        self._vectors: dict[IndexLevel, dict[str, list]] = {
            level: {} for level in IndexLevel
        }
        
        # 关系索引
        self._parent_index: dict[str, str] = {}  # child_id -> parent_id
        self._children_index: dict[str, list[str]] = defaultdict(list)  # parent_id -> [child_ids]
        
        # 嵌入模型（延迟加载）
        self._embed_model = None
        
        logger.info("MultiGranularIndex 初始化完成")
    
    @property
    def embed_model(self):
        """延迟加载嵌入模型"""
        if self._embed_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)
                logger.info(f"嵌入模型加载完成 | model={settings.EMBEDDING_MODEL}")
            except Exception as e:
                logger.error(f"嵌入模型加载失败 | error={e}")
        return self._embed_model
    
    def add_item(self, item: IndexItem):
        """添加索引项"""
        # 添加到对应层级索引
        self._indexes[item.level][item.id] = item
        
        # 计算嵌入
        if item.content and self.embed_model:
            try:
                embedding = self.embed_model.encode(item.content)
                item.embedding = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
                self._vectors[item.level][item.id] = item.embedding
            except Exception as e:
                logger.warning(f"嵌入计算失败 | item_id={item.id} | error={e}")
        
        # 更新关系索引
        if item.parent_id:
            self._parent_index[item.id] = item.parent_id
            self._children_index[item.parent_id].append(item.id)
        
        logger.debug(f"添加索引项 | level={item.level.value} | id={item.id}")
    
    def add_document(self, doc_uuid: str, title: str, content: str, metadata: dict = None):
        """添加文档级索引"""
        item = IndexItem(
            id=f"doc_{doc_uuid}",
            level=IndexLevel.DOCUMENT,
            content=f"{title}\n{content[:1000]}",  # 截断过长内容
            metadata=metadata or {},
        )
        self.add_item(item)
        return item
    
    def add_chapter(self, chapter_id: str, doc_uuid: str, title: str, content: str, metadata: dict = None):
        """添加章节级索引"""
        item = IndexItem(
            id=f"ch_{chapter_id}",
            level=IndexLevel.CHAPTER,
            content=f"{title}\n{content[:500]}",
            metadata=metadata or {},
            parent_id=f"doc_{doc_uuid}",
        )
        self.add_item(item)
        return item
    
    def add_paragraph(self, paragraph_id: str, chapter_id: str, content: str, metadata: dict = None):
        """添加段落级索引"""
        item = IndexItem(
            id=f"para_{paragraph_id}",
            level=IndexLevel.PARAGRAPH,
            content=content,
            metadata=metadata or {},
            parent_id=f"ch_{chapter_id}",
        )
        self.add_item(item)
        return item
    
    def add_entity(self, entity_id: str, entity_type: str, name: str, content: str, metadata: dict = None):
        """添加实体级索引"""
        item = IndexItem(
            id=f"entity_{entity_id}",
            level=IndexLevel.ENTITY,
            content=f"{entity_type}: {name}\n{content}",
            metadata={"entity_type": entity_type, "name": name, **(metadata or {})},
        )
        self.add_item(item)
        return item
    
    async def retrieve(
        self,
        query: str,
        level: IndexLevel = None,
        top_k: int = 10,
        country: str = "",
    ) -> list[dict]:
        """
        多粒度检索
        
        Args:
            query: 查询文本
            level: 指定层级（None=自动选择）
            top_k: 返回数量
            country: 国家过滤
            
        Returns:
            检索结果列表
        """
        start = time.time()
        
        # 如果指定层级，只在该层级检索
        if level:
            levels_to_search = [level]
        else:
            # 自动选择层级（基于查询复杂度）
            levels_to_search = self._select_levels(query)
        
        all_results = []
        
        for level in levels_to_search:
            results = await self._search_level(query, level, top_k, country)
            all_results.extend(results)
        
        # 去重并按相关性排序
        unique_results = self._deduplicate(all_results)
        unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        elapsed = (time.time() - start) * 1000
        logger.info(
            f"多粒度检索完成 | levels={[lv.value for lv in levels_to_search]} | "
            f"results={len(unique_results)} | elapsed={elapsed:.2f}ms"
        )
        
        return unique_results[:top_k]
    
    def _select_levels(self, query: str) -> list[IndexLevel]:
        """根据查询复杂度选择检索层级"""
        query_lower = query.lower()
        
        # 简单查询 -> 实体级
        simple_patterns = [
            r"^what is",
            r"^how much",
            r"税率",
            r"关税",
            r"编码",
        ]
        for pattern in simple_patterns:
            if pattern in query_lower:
                return [IndexLevel.ENTITY, IndexLevel.PARAGRAPH]
        
        # 流程查询 -> 章节级
        procedure_patterns = [
            r"怎么",
            r"如何",
            r"步骤",
            r"流程",
            r"how to",
            r"step",
        ]
        for pattern in procedure_patterns:
            if pattern in query_lower:
                return [IndexLevel.CHAPTER, IndexLevel.PARAGRAPH]
        
        # 复杂查询 -> 多层级
        complex_patterns = [
            r"对比",
            r"比较",
            r"区别",
            r"compare",
            r"difference",
        ]
        for pattern in complex_patterns:
            if pattern in query_lower:
                return [IndexLevel.DOCUMENT, IndexLevel.CHAPTER, IndexLevel.PARAGRAPH]
        
        # 默认：段落级
        return [IndexLevel.PARAGRAPH, IndexLevel.ENTITY]
    
    async def _search_level(
        self,
        query: str,
        level: IndexLevel,
        top_k: int,
        country: str,
    ) -> list[dict]:
        """在指定层级检索"""
        results = []
        
        # 获取该层级的所有索引项
        items = self._indexes.get(level, {})
        
        if not items:
            return results
        
        # 计算查询嵌入
        if self.embed_model and query:
            try:
                query_embedding = self.embed_model.encode(query)
                query_vec = query_embedding.tolist() if hasattr(query_embedding, 'tolist') else list(query_embedding)
            except Exception as e:
                logger.warning(f"查询嵌入计算失败 | error={e}")
                query_vec = None
        else:
            query_vec = None
        
        # 计算相似度
        for item_id, item in items.items():
            # 国家过滤
            if country and item.metadata.get("country"):
                if country.lower() not in item.metadata["country"].lower():
                    continue
            
            # 计算分数
            if query_vec and item.embedding:
                score = self._cosine_similarity(query_vec, item.embedding)
            else:
                # 回退到关键词匹配
                score = self._keyword_match_score(query, item.content)
            
            if score > 0.1:  # 最小阈值
                results.append({
                    "id": item_id,
                    "level": level.value,
                    "content": item.content,
                    "score": score,
                    "metadata": item.metadata,
                    "parent_id": item.parent_id,
                })
        
        # 按分数排序
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return results[:top_k]
    
    def _cosine_similarity(self, vec1: list, vec2: list) -> float:
        """计算余弦相似度"""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def _keyword_match_score(self, query: str, content: str) -> float:
        """关键词匹配分数"""
        query_words = set(query.lower().split())
        content_words = set(content.lower().split())
        
        if not query_words:
            return 0.0
        
        intersection = query_words & content_words
        return len(intersection) / len(query_words)
    
    def _deduplicate(self, results: list[dict]) -> list[dict]:
        """去重"""
        seen = set()
        unique = []
        
        for result in results:
            key = result.get("id", "") or result.get("content", "")[:100]
            if key not in seen:
                seen.add(key)
                unique.append(result)
        
        return unique
    
    def get_context(self, item_id: str, max_depth: int = 2) -> dict:
        """
        获取项的上下文（父级和子级）
        """
        context = {
            "item": None,
            "parents": [],
            "children": [],
        }
        
        # 获取当前项
        for level in IndexLevel:
            if item_id in self._indexes[level]:
                context["item"] = self._indexes[level][item_id].to_dict()
                break
        
        if not context["item"]:
            return context
        
        # 获取父级
        current_id = item_id
        for _ in range(max_depth):
            parent_id = self._parent_index.get(current_id)
            if not parent_id:
                break
            
            for level in IndexLevel:
                if parent_id in self._indexes[level]:
                    context["parents"].append(self._indexes[level][parent_id].to_dict())
                    break
            
            current_id = parent_id
        
        # 获取子级
        children_ids = self._children_index.get(item_id, [])
        for child_id in children_ids[:10]:  # 限制子级数量
            for level in IndexLevel:
                if child_id in self._indexes[level]:
                    context["children"].append(self._indexes[level][child_id].to_dict())
                    break
        
        return context
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "levels": {
                level.value: len(items)
                for level, items in self._indexes.items()
            },
            "total_items": sum(len(items) for items in self._indexes.values()),
            "total_embeddings": sum(
                len(vectors) for vectors in self._vectors.values()
            ),
        }
