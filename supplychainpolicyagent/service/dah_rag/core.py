# -*- coding: utf-8 -*-
"""
DAH-RAG Core Module
===================
Domain-Aware Hierarchical RAG 的核心实现

整合所有子模块，提供统一的检索接口
"""

import time
from dataclasses import dataclass, field, asdict
from enum import Enum

from config.logging_config import get_logger

logger = get_logger("dah_rag")


class RetrievalStrategy(Enum):
    """检索策略"""
    PRECISE = "precise"           # 精确检索（实体级）
    CONTEXTUAL = "contextual"     # 上下文检索（段落级）
    COMPREHENSIVE = "comprehensive"  # 全面检索（文档级）
    MULTI_SEARCH = "multi_search"  # 多路检索
    KNOWLEDGE_GRAPH = "knowledge_graph"  # 知识图谱检索


@dataclass
class DAHConfig:
    """DAH-RAG 配置"""
    # 检索参数
    enable_query_adaptive_chunking: bool = True
    enable_multi_granular_index: bool = True
    enable_knowledge_graph: bool = True
    enable_intent_classification: bool = True
    enable_adaptive_memory: bool = True
    
    # 粒度选择
    default_granularity: str = "auto"  # auto/document/chapter/paragraph/entity
    
    # 意图阈值
    intent_confidence_threshold: float = 0.7
    
    # 记忆参数
    memory_decay_days: int = 30
    memory_decay_factor: float = 0.9
    
    # 性能参数
    max_chunks_per_level: int = 10
    enable_parallel_retrieval: bool = True


@dataclass
class RetrievalResult:
    """DAH-RAG 检索结果"""
    items: list = field(default_factory=list)
    strategy_used: str = ""
    intent_detected: str = ""
    confidence: float = 0.0
    levels_searched: list = field(default_factory=list)
    kg_expansions: int = 0
    elapsed_ms: float = 0.0
    
    def to_dict(self) -> dict:
        return asdict(self)


class DAHRAG:
    """
    Domain-Aware Hierarchical RAG
    
    跨境物流合规领域的创新检索增强生成框架
    
    主要特性：
    1. 查询自适应分块 - 根据查询动态构建语义完整的chunks
    2. 多粒度索引 - 同时索引文档/章节/段落/实体多个层级
    3. 知识图谱增强 - 结构化数据与非结构化文本融合
    4. 意图感知路由 - 根据查询意图选择最优检索策略
    5. 自适应记忆 - 频繁检索的内容保持活跃，不常用的逐渐衰减
    """
    
    def __init__(self, config: DAHConfig = None):
        self.config = config or DAHConfig()
        
        # 延迟加载子模块
        self._intent_classifier = None
        self._knowledge_graph = None
        self._multi_granular_index = None
        self._query_adaptive_chunker = None
        self._adaptive_memory = None
        
        # 统计
        self._stats = {
            "total_queries": 0,
            "avg_latency_ms": 0,
            "intent_distribution": {},
            "strategy_distribution": {},
        }
        
        logger.info(
            f"DAH-RAG 初始化完成 | "
            f"query_adaptive={self.config.enable_query_adaptive_chunking} | "
            f"multi_granular={self.config.enable_multi_granular_index} | "
            f"knowledge_graph={self.config.enable_knowledge_graph} | "
            f"intent={self.config.enable_intent_classification} | "
            f"memory={self.config.enable_adaptive_memory}"
        )
    
    @property
    def intent_classifier(self):
        """延迟加载意图分类器"""
        if self._intent_classifier is None:
            from service.dah_rag.intent_classifier import IntentClassifier
            self._intent_classifier = IntentClassifier()
        return self._intent_classifier
    
    @property
    def knowledge_graph(self):
        """延迟加载知识图谱"""
        if self._knowledge_graph is None:
            from service.dah_rag.knowledge_graph import KnowledgeGraph
            self._knowledge_graph = KnowledgeGraph()
        return self._knowledge_graph
    
    @property
    def multi_granular_index(self):
        """延迟加载多粒度索引"""
        if self._multi_granular_index is None:
            from service.dah_rag.multi_granular_index import MultiGranularIndex
            self._multi_granular_index = MultiGranularIndex()
        return self._multi_granular_index
    
    @property
    def query_adaptive_chunker(self):
        """延迟加载查询自适应分块器"""
        if self._query_adaptive_chunker is None:
            from service.dah_rag.query_adaptive_chunker import QueryAdaptiveChunker
            self._query_adaptive_chunker = QueryAdaptiveChunker()
        return self._query_adaptive_chunker
    
    @property
    def adaptive_memory(self):
        """延迟加载自适应记忆"""
        if self._adaptive_memory is None:
            from service.dah_rag.adaptive_memory import AdaptiveMemory
            self._adaptive_memory = AdaptiveMemory(
                decay_days=self.config.memory_decay_days,
                decay_factor=self.config.memory_decay_factor,
            )
        return self._adaptive_memory
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        country: str = "",
        request_id: str = "",
        **kwargs,
    ) -> RetrievalResult:
        """
        DAH-RAG 核心检索接口
        
        Args:
            query: 用户查询
            top_k: 返回结果数量
            country: 目标国家
            request_id: 请求ID
            **kwargs: 额外参数
            
        Returns:
            RetrievalResult: 检索结果
        """
        start = time.time()
        req_tag = request_id or "-"
        
        logger.info(f"[{req_tag}] DAH-RAG 检索开始 | query={query[:60]} | country={country}")
        
        # 1. 意图识别
        intent = None
        if self.config.enable_intent_classification:
            intent = await self.intent_classifier.classify(query, country)
            logger.info(
                f"[{req_tag}] 意图识别完成 | "
                f"type={intent.intent_type} | "
                f"confidence={intent.confidence:.2f} | "
                f"strategy={intent.recommended_strategy}"
            )
        
        # 2. 选择检索策略
        strategy = self._select_strategy(intent, kwargs)
        logger.info(f"[{req_tag}] 检索策略 | strategy={strategy.value}")
        
        # 3. 执行检索
        result = await self._execute_retrieval(
            query=query,
            strategy=strategy,
            top_k=top_k,
            country=country,
            intent=intent,
            request_id=req_tag,
            **kwargs,
        )
        
        # 4. 知识图谱扩展（如果启用）
        if self.config.enable_knowledge_graph and result.items:
            kg_expanded = await self.knowledge_graph.expand_results(
                result.items, query, country
            )
            result.kg_expansions = len(kg_expanded) - len(result.items)
            result.items = kg_expanded
            logger.info(f"[{req_tag}] 知识图谱扩展 | expansions={result.kg_expansions}")
        
        # 5. 更新自适应记忆
        if self.config.enable_adaptive_memory:
            for item in result.items:
                chunk_id = getattr(item, 'paragraph_id', None) or getattr(item, 'doc_uuid', '')
                if chunk_id:
                    self.adaptive_memory.update_access(str(chunk_id))
        
        # 6. 计算统计
        elapsed = (time.time() - start) * 1000
        result.elapsed_ms = round(elapsed, 2)
        result.strategy_used = strategy.value
        if intent:
            result.intent_detected = intent.intent_type
            result.confidence = intent.confidence
        
        # 更新全局统计
        self._stats["total_queries"] += 1
        self._stats["avg_latency_ms"] = (
            (self._stats["avg_latency_ms"] * (self._stats["total_queries"] - 1) + elapsed)
            / self._stats["total_queries"]
        )
        
        logger.info(
            f"[{req_tag}] DAH-RAG 检索完成 | "
            f"items={len(result.items)} | "
            f"strategy={strategy.value} | "
            f"elapsed={result.elapsed_ms}ms"
        )
        
        return result
    
    def _select_strategy(self, intent, kwargs) -> RetrievalStrategy:
        """根据意图和参数选择检索策略"""
        if intent is None:
            return RetrievalStrategy.CONTEXTUAL
        
        # 根据意图类型选择策略
        strategy_map = {
            "factual_lookup": RetrievalStrategy.PRECISE,
            "procedural_guide": RetrievalStrategy.CONTEXTUAL,
            "compliance_check": RetrievalStrategy.COMPREHENSIVE,
            "comparison_analysis": RetrievalStrategy.MULTI_SEARCH,
            "general_inquiry": RetrievalStrategy.CONTEXTUAL,
        }
        
        strategy = strategy_map.get(intent.intent_type, RetrievalStrategy.CONTEXTUAL)
        
        # 如果明确要求知识图谱检索
        if kwargs.get("use_knowledge_graph"):
            strategy = RetrievalStrategy.KNOWLEDGE_GRAPH
        
        return strategy
    
    async def _execute_retrieval(
        self,
        query: str,
        strategy: RetrievalStrategy,
        top_k: int,
        country: str,
        intent,
        request_id: str,
        **kwargs,
    ) -> RetrievalResult:
        """执行检索"""
        from dataclasses import dataclass, field
        
        @dataclass
        class InternalResult:
            items: list = field(default_factory=list)
            
        result = InternalResult()
        
        if strategy == RetrievalStrategy.PRECISE:
            # 精确检索：实体级
            result.items = await self._precise_retrieval(query, top_k, country, request_id)
            
        elif strategy == RetrievalStrategy.CONTEXTUAL:
            # 上下文检索：段落级
            result.items = await self._contextual_retrieval(query, top_k, country, request_id)
            
        elif strategy == RetrievalStrategy.COMPREHENSIVE:
            # 全面检索：文档级
            result.items = await self._comprehensive_retrieval(query, top_k, country, request_id)
            
        elif strategy == RetrievalStrategy.MULTI_SEARCH:
            # 多路检索
            result.items = await self._multi_search_retrieval(query, top_k, country, request_id)
            
        elif strategy == RetrievalStrategy.KNOWLEDGE_GRAPH:
            # 知识图谱检索
            result.items = await self._kg_retrieval(query, top_k, country, request_id)
        
        # 转换为 RetrievalResult
        return RetrievalResult(
            items=result.items,
            levels_searched=[strategy.value],
        )
    
    async def _precise_retrieval(
        self, query: str, top_k: int, country: str, request_id: str
    ) -> list:
        """精确检索：实体级"""
        # 使用查询自适应分块获取最相关的实体级chunks
        chunks = await self.query_adaptive_chunker.chunk(query, level="entity")
        
        # 多粒度索引检索
        if self.config.enable_multi_granular_index:
            indexed_chunks = await self.multi_granular_index.retrieve(
                query, level="entity", top_k=top_k, country=country
            )
            # 合并结果
            chunks = self._merge_chunks(chunks, indexed_chunks)
        
        return chunks[:top_k]
    
    async def _contextual_retrieval(
        self, query: str, top_k: int, country: str, request_id: str
    ) -> list:
        """上下文检索：段落级"""
        chunks = await self.query_adaptive_chunker.chunk(query, level="paragraph")
        
        if self.config.enable_multi_granular_index:
            indexed_chunks = await self.multi_granular_index.retrieve(
                query, level="paragraph", top_k=top_k * 2, country=country
            )
            chunks = self._merge_chunks(chunks, indexed_chunks)
        
        return chunks[:top_k]
    
    async def _comprehensive_retrieval(
        self, query: str, top_k: int, country: str, request_id: str
    ) -> list:
        """全面检索：文档级"""
        # 多层级检索
        all_chunks = []
        
        # 文档级
        doc_chunks = await self.multi_granular_index.retrieve(
            query, level="document", top_k=top_k, country=country
        )
        all_chunks.extend(doc_chunks)
        
        # 章节级
        chapter_chunks = await self.multi_granular_index.retrieve(
            query, level="chapter", top_k=top_k, country=country
        )
        all_chunks.extend(chapter_chunks)
        
        # 段落级
        paragraph_chunks = await self.multi_granular_index.retrieve(
            query, level="paragraph", top_k=top_k, country=country
        )
        all_chunks.extend(paragraph_chunks)
        
        # 去重并按相关性排序
        unique_chunks = self._deduplicate_chunks(all_chunks)
        return unique_chunks[:top_k]
    
    async def _multi_search_retrieval(
        self, query: str, top_k: int, country: str, request_id: str
    ) -> list:
        """多路检索：拆分查询并行检索"""
        # 拆分查询
        sub_queries = await self.intent_classifier.decompose_query(query)
        
        all_chunks = []
        for sub_q in sub_queries:
            chunks = await self.query_adaptive_chunker.chunk(sub_q, level="auto")
            all_chunks.extend(chunks)
        
        # 合并去重
        unique_chunks = self._deduplicate_chunks(all_chunks)
        return unique_chunks[:top_k]
    
    async def _kg_retrieval(
        self, query: str, top_k: int, country: str, request_id: str
    ) -> list:
        """知识图谱检索"""
        # 从知识图谱检索相关实体
        kg_results = await self.knowledge_graph.query(query, country)
        
        # 转换为chunk格式
        chunks = []
        for result in kg_results:
            chunk = {
                "content": result.get("content", ""),
                "source": "knowledge_graph",
                "entity_type": result.get("entity_type", ""),
                "relevance_score": result.get("score", 0.0),
            }
            chunks.append(chunk)
        
        return chunks[:top_k]
    
    def _merge_chunks(self, chunks1: list, chunks2: list) -> list:
        """合并两个chunk列表，去重并按相关性排序"""
        seen = set()
        merged = []
        
        for chunk in chunks1 + chunks2:
            key = chunk.get("content", "")[:100]  # 使用内容前100字符作为key
            if key not in seen:
                seen.add(key)
                merged.append(chunk)
        
        # 按相关性排序
        merged.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return merged
    
    def _deduplicate_chunks(self, chunks: list) -> list:
        """去重"""
        seen = set()
        unique = []
        
        for chunk in chunks:
            key = chunk.get("content", "")[:100]
            if key not in seen:
                seen.add(key)
                unique.append(chunk)
        
        return unique
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()
    
    def rollback(self):
        """回滚到原始版本"""
        logger.warning("DAH-RAG 回滚到原始版本")
        # 这里可以实现回滚逻辑
        # 比如重新加载原始的 retriever.py
        pass


# 全局实例
dah_rag = DAHRAG()
