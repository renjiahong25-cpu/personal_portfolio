# -*- coding: utf-8 -*-
"""
DAH-RAG: Domain-Aware Hierarchical RAG
=======================================
为跨境物流合规领域设计的创新检索增强生成框架

核心创新：
1. Query-Adaptive Chunking - 根据查询动态构建语义完整的chunks
2. Multi-Granular Indexing - 多粒度索引（文档/章节/段落/实体）
3. Knowledge Graph Integration - 知识图谱增强检索
4. Intent-Aware Routing - 查询意图感知的动态路由
5. Adaptive Memory with Decay - 自适应记忆管理

Author: Cross-Border Logistics AI Team
Version: 0.1.0
"""

from service.dah_rag.core import DAHRAG, DAHConfig, RetrievalResult
from service.dah_rag.intent_classifier import IntentClassifier, QueryIntent
from service.dah_rag.knowledge_graph import KnowledgeGraph, KGNode, KGEdge
from service.dah_rag.multi_granular_index import MultiGranularIndex, IndexLevel
from service.dah_rag.query_adaptive_chunker import QueryAdaptiveChunker
from service.dah_rag.adaptive_memory import AdaptiveMemory
from service.dah_rag.integration import DAHRAGIntegrator, IntegrationConfig, dah_rag_integrator

__all__ = [
    "DAHRAG",
    "DAHConfig",
    "RetrievalResult",
    "IntentClassifier",
    "QueryIntent",
    "KnowledgeGraph",
    "KGNode",
    "KGEdge",
    "MultiGranularIndex",
    "IndexLevel",
    "QueryAdaptiveChunker",
    "AdaptiveMemory",
    "DAHRAGIntegrator",
    "IntegrationConfig",
    "dah_rag_integrator",
]

__version__ = "0.1.0"
