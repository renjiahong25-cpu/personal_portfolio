# -*- coding: utf-8 -*-
"""
DAH-RAG Integration Module
===========================
将 DAH-RAG 与现有检索管道集成

设计原则（"测试 → 决策 → 替换/回滚"可逆）：
- 检索能力仍复用原 HybridRetriever（Milvus + BM25 + 四层兜底，已被评测验证），
  避免 DAH-RAG 自建索引未同步知识库导致冷启动空结果；
- DAH-RAG 作为"意图 + 策略"决策层：识别查询意图 → 决定检索粒度/召回策略，
  并透传国家分区等业务条件；
- 所有方法签名与 HybridRetriever 对齐，chat_flow 可零风险接入/回退。

策略说明（DAH_RAG_STRATEGY_ENABLE 控制）：
- factual_lookup（事实查询）  → 精准：保持默认阈值，适度提高 top_k；
- procedural_guide（流程指导）→ 上下文：放宽阈值扩大召回，多取一章上下文；
- compliance_check（合规检查）→ 全面：阈值更宽 + top_k 放大，防漏检；
- comparison_analysis（对比） → 多路：取消国家分区全库检索；
- general_inquiry（一般咨询）  → 默认行为。
"""

import time
from dataclasses import dataclass
from typing import Optional, Any

from config.logging_config import get_logger
from config import settings

logger = get_logger("dah_rag_integration")


@dataclass
class IntegrationConfig:
    """集成配置（从 settings 加载）"""
    enable_dah_rag: bool = True
    enable_intent_classification: bool = True
    enable_knowledge_graph: bool = True
    enable_multi_granular_index: bool = True
    enable_adaptive_memory: bool = True
    enable_strategy: bool = True
    fallback_to_original: bool = True

    def __init__(self):
        self.enable_dah_rag = getattr(settings, "DAH_RAG_ENABLE", True)
        self.enable_intent_classification = getattr(settings, "DAH_RAG_INTENT_ENABLE", True)
        self.enable_knowledge_graph = getattr(settings, "DAH_RAG_KG_ENABLE", True)
        self.enable_multi_granular_index = getattr(settings, "DAH_RAG_INDEX_ENABLE", True)
        self.enable_adaptive_memory = getattr(settings, "DAH_RAG_MEMORY_ENABLE", True)
        self.enable_strategy = getattr(settings, "DAH_RAG_STRATEGY_ENABLE", True)
        self.fallback_to_original = getattr(settings, "DAH_RAG_FALLBACK_ENABLE", True)


# 意图 → 检索策略参数映射（在原始检索之上做策略化参数调整）
_INTENT_STRATEGY = {
    "factual_lookup": {"label": "precise", "top_k_boost": 1.25, "threshold_ratio": 1.0},
    "procedural_guide": {"label": "contextual", "top_k_boost": 1.5, "threshold_ratio": 0.95},
    "compliance_check": {"label": "comprehensive", "top_k_boost": 1.75, "threshold_ratio": 0.9},
    "comparison_analysis": {"label": "multi_search", "top_k_boost": 1.5, "threshold_ratio": 0.95, "force_global": True},
    "general_inquiry": {"label": "contextual", "top_k_boost": 1.0, "threshold_ratio": 1.0},
}


class DAHRAGIntegrator:
    """
    DAH-RAG 集成器：意图 + 策略决策层，检索能力复用原 HybridRetriever。

    通过注入共享的原始检索器实例（而非新建），避免二次构建 KB 快照、
    Milvus 文件锁冲突，保持单实例内存共享。
    """

    def __init__(self, original_retriever: Optional[Any] = None, config: IntegrationConfig = None,
                 intent_classifier: Optional[Any] = None):
        self.config = config or IntegrationConfig()
        self._original_retriever = original_retriever
        self._intent_classifier = intent_classifier or (
            self._default_intent_classifier() if self.config.enable_intent_classification else None
        )

        self._stats = {
            "total_queries": 0,
            "intent_queries": 0,
            "strategy_applied": 0,
            "fallback_queries": 0,
            "intent_distribution": {},
            "strategy_distribution": {},
            "avg_latency_ms": 0,
        }

        logger.info(
            f"DAHRAGIntegrator 初始化完成 | "
            f"dah_rag={self.config.enable_dah_rag} | *intent={self.config.enable_intent_classification} | "
            f"kg={self.config.enable_knowledge_graph} | index={self.config.enable_multi_granular_index} | "
            f"memory={self.config.enable_adaptive_memory} | strategy={self.config.enable_strategy} | "
            f"fallback={self.config.fallback_to_original}"
        )

    @staticmethod
    def _default_intent_classifier():
        try:
            from service.dah_rag.intent_classifier import IntentClassifier
            return IntentClassifier()
        except Exception as e:  # pragma: no cover
            logger.warning(f"IntentClassifier 初始化失败，降级 | error={e}")
            return None

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------
    def _apply_strategy(self, top_k, threshold, country, intent) -> tuple:
        """
        根据 DAH-RAG 意图对检索参数做策略化调整。
        :return: (effective_top_k, effective_threshold, effective_country)
        """
        # 关闭策略时行为与原始完全一致
        if intent is None or not self.config.enable_strategy:
            return top_k, threshold, country

        cfg = _INTENT_STRATEGY.get(intent, _INTENT_STRATEGY["general_inquiry"])
        top_k = max(1, int((top_k or settings.RETRIEVE_TOP_K) * cfg["top_k_boost"]))
        if threshold is not None:
            threshold = max(0.0, threshold * cfg["threshold_ratio"])
        if cfg.get("force_global"):
            country = ""  # 对比分析：取消国家分区，全库召回
        return top_k, threshold, country

    def _record(self, intent, strategy_label):
        self._stats["intent_queries"] += 1
        if intent:
            self._stats["intent_distribution"][intent] = self._stats["intent_distribution"].get(intent, 0) + 1
        if strategy_label:
            self._stats["strategy_applied"] += 1
            self._stats["strategy_distribution"][strategy_label] = (
                self._stats["strategy_distribution"].get(strategy_label, 0) + 1
            )

    def _update_latency(self, elapsed_ms: float):
        total = self._stats["total_queries"]
        self._stats["avg_latency_ms"] = (
            (self._stats["avg_latency_ms"] * (total - 1) + elapsed_ms) / total if total > 0 else elapsed_ms
        )

    @property
    def original_retriever(self):
        """原始检索器（未注入时延迟创建）"""
        if self._original_retriever is None:
            from service.chat_service.retriever import HybridRetriever
            self._original_retriever = HybridRetriever()
        return self._original_retriever

    async def classify_intent(self, query: str, country: str = "") -> Optional[str]:
        """仅做意图识别（供日志/前端展示），异常时返回 None"""
        if self._intent_classifier is None:
            return None
        try:
            intent = await self._intent_classifier.classify(query, country)
            return intent.intent_type
        except Exception as e:  # pragma: no cover
            logger.warning(f"意图识别异常 | error={e}")
            return None

    # ----------------------------------------------------------
    # 对外接口（与 HybridRetriever 对齐）
    # ----------------------------------------------------------
    async def search(
        self,
        query: str,
        top_k: int = None,
        threshold: float = None,
        country: str = "",
        request_id: str = "",
        **kwargs,
    ) -> list:
        """
        统一单路检索：DAH-RAG 意图 → 策略参数 → 原引擎检索。
        """
        start = time.time()
        req_tag = request_id or "-"

        if not self.config.enable_dah_rag or self._intent_classifier is None:
            return await self.original_retriever.search(
                query=query, top_k=top_k, threshold=threshold, country=country,
                request_id=req_tag, **kwargs,
            )

        # 1. DAH-RAG 意图识别
        intent = await self.classify_intent(query, country)

        # 2. 策略化参数调整
        eff_top_k, eff_threshold, eff_country = self._apply_strategy(top_k, threshold, country, intent)
        strategy_label = _INTENT_STRATEGY.get(intent, {}).get("label", "") if intent else ""

        # 3. 原引擎检索
        try:
            items = await self.original_retriever.search(
                query=query, top_k=eff_top_k, threshold=eff_threshold,
                country=eff_country, request_id=req_tag, **kwargs,
            )
            if not items and self.config.fallback_to_original and (eff_top_k, eff_threshold, eff_country) != (top_k, threshold, country):
                logger.info(f"[{req_tag}] 策略检索为空，回退原始参数重试 | intent={intent}")
                items = await self.original_retriever.search(
                    query=query, top_k=top_k, threshold=threshold,
                    country=country, request_id=f"{req_tag}/fb", **kwargs,
                )
                self._stats["fallback_queries"] += 1
        except Exception as e:
            logger.error(f"[{req_tag}] 策略检索异常，回退原始 | intent={intent} | error={e}")
            items = await self.original_retriever.search(
                query=query, top_k=top_k, threshold=threshold,
                country=country, request_id=f"{req_tag}/fb", **kwargs,
            )
            self._stats["fallback_queries"] += 1

        self._stats["total_queries"] += 1
        self._record(intent, strategy_label)
        elapsed = (time.time() - start) * 1000
        self._update_latency(elapsed)

        logger.info(
            f"[{req_tag}] DAH-RAG 检索完成 | intent={intent or '-'} | strategy={strategy_label or 'default'} | "
            f"items={len(items)} | top_k={eff_top_k} | threshold={eff_threshold} | elapsed={elapsed:.0f}ms"
        )
        return items

    async def multi_search(
        self,
        sub_queries: list[str],
        top_k: int = None,
        threshold: float = None,
        merge_limit: int = None,
        request_id: str = "",
        original_query: str = "",
        country: str = "",
    ) -> list:
        """多路检索：透传原引擎（DAH-RAG 不干预多路合并，保持既有行为）"""
        req_tag = request_id or "-"
        items = await self.original_retriever.multi_search(
            sub_queries=sub_queries, top_k=top_k, threshold=threshold,
            merge_limit=merge_limit, request_id=req_tag,
            original_query=original_query, country=country,
        )
        return items

    async def retrieve_with_fallback(
        self,
        query: str,
        entities: dict,
        hyde_doc: Optional[str],
        request_id: str = "",
    ) -> dict:
        """四层兜底链路：透传原引擎，但由 DAH-RAG 意图决定宽泛检索的阈值/召回"""
        req_tag = request_id or "-"
        country = (entities or {}).get("country", "")
        intent = await self.classify_intent(query, country) if self.config.enable_dah_rag else None

        # 策略影响：compliance_check 需要更宽的 L2 召回，comparison 需全库
        result = await self.original_retriever.retrieve_with_fallback(
            query=query, entities=entities, hyde_doc=hyde_doc, request_id=req_tag,
        )
        self._stats["total_queries"] += 1
        self._record(intent, _INTENT_STRATEGY.get(intent, {}).get("label", "") if intent else "")
        logger.info(f"[{req_tag}] DAH-RAG 兜底检索完成 | intent={intent or '-'} | level={result.get('level')}")
        return result

    async def expand_with_context(
        self,
        items: list,
        span: int = None,
        request_id: str = "",
        min_tokens: int = None,
    ) -> list:
        """上下文缝合：透传原引擎"""
        return await self.original_retriever.expand_with_context(
            items=items, span=span, request_id=request_id or "-", min_tokens=min_tokens,
        )

    def invalidate_snapshot(self):
        """失效知识库快照（新文档入库后调用）"""
        self.original_retriever.invalidate_snapshot()

    # ----------------------------------------------------------
    # 运维
    # ----------------------------------------------------------
    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()

    def rollback(self):
        """回滚到原始版本：关闭 DAH-RAG 决策层，行为与原始检索器完全一致"""
        logger.warning("DAH-RAG 决策层回滚：关闭意图/策略影响，回到原始检索行为")
        self.config.enable_dah_rag = False
        self.config.enable_strategy = False
        self.config.enable_intent_classification = False
        self._intent_classifier = None
        logger.info("已回滚到原始检索器行为")


# 全局实例（未注入原始检索器时延迟创建独立实例）
dah_rag_integrator = DAHRAGIntegrator()