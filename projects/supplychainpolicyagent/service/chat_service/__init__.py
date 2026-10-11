# -*- coding: utf-8 -*-
"""
问答核心服务包
=====================================================
跨境物流规则智能Agent - 问答核心工作流（feat/chat-service）

模块组成：
    1. entity_extractor   - 实体抽取（商品/国家/HS编码/贸易条款/合规场景）+ 口语纠错补全
    2. hyde_generator     - HyDE 假想文档生成，增强向量召回（可降级关闭）
    3. retriever          - BM25 + 向量双路混合检索 / 分层检索 / Rerank精排 / 四层兜底
    4. content_compressor - 超长内容 Token 压缩与智能摘要，强制三级元数据绑定
    5. fact_checker       - AI 幻觉双重校验（本地统计门控 + LLM 二次核查，可降级）
    6. chat_flow          - 问答工作流主编排器（SSE 流式输出 / 幂等去重 / 降级策略）

对外统一入口为 `chat_flow`（ChatFlow 单例），供 api/chat.py 调用。
"""
from config.logging_config import get_logger

logger = get_logger("chat_service")

# 依赖说明：重依赖（pymilvus / rank_bm25 / sentence-transformers）均在
# retriever 内部惰性加载并按需降级，避免包导入阶段阻塞服务启动。

from .entity_extractor import EntityExtractor
from .hyde_generator import HyDEGenerator
from .retriever import HybridRetriever, RetrievalItem
from .content_compressor import ContentCompressor
from .fact_checker import FactChecker
from .chat_flow import ChatFlow, chat_flow

__all__ = [
    "EntityExtractor",
    "HyDEGenerator",
    "HybridRetriever",
    "RetrievalItem",
    "ContentCompressor",
    "FactChecker",
    "ChatFlow",
    "chat_flow",
]

logger.info("问答核心服务包加载完成")