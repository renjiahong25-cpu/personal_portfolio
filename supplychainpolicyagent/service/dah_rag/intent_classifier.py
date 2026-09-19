# -*- coding: utf-8 -*-
"""
Intent Classifier Module
========================
查询意图识别与分类

支持的意图类型：
1. factual_lookup - 事实查询（税率、HS编码、法规条款）
2. procedural_guide - 流程指导（清关步骤、申报流程）
3. compliance_check - 合规检查（产品能否进口、是否需要许可）
4. comparison_analysis - 对比分析（不同国家税率对比）
5. general_inquiry - 一般咨询
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from config.logging_config import get_logger

logger = get_logger("intent_classifier")


class QueryIntentType(Enum):
    """查询意图类型"""
    FACTUAL_LOOKUP = "factual_lookup"
    PROCEDURAL_GUIDE = "procedural_guide"
    COMPLIANCE_CHECK = "compliance_check"
    COMPARISON_ANALYSIS = "comparison_analysis"
    GENERAL_INQUIRY = "general_inquiry"


@dataclass
class QueryIntent:
    """查询意图"""
    intent_type: str
    confidence: float
    entities: dict = field(default_factory=dict)
    recommended_strategy: str = ""
    sub_queries: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "intent_type": self.intent_type,
            "confidence": self.confidence,
            "entities": self.entities,
            "recommended_strategy": self.recommended_strategy,
            "sub_queries": self.sub_queries,
        }


class IntentClassifier:
    """
    查询意图分类器
    
    基于规则和关键词匹配的轻量级意图分类器
    适用于跨境物流合规领域
    """
    
    # 意图关键词模式
    INTENT_PATTERNS = {
        QueryIntentType.FACTUAL_LOOKUP: {
            "keywords": [
                "税率", "关税", "增值税", "duty", "tariff", "vat",
                "HS编码", "hs code", "编码", "code",
                "条款", "规定", "regulation", "clause",
                "是多少", "什么", "多少", "what", "how much",
                "查询", "查", "search", "lookup",
            ],
            "patterns": [
                r"(.+?)的(税率|关税|增值税|税)",
                r"(.+?)(关税|税率|vat)",
                r"what is the (.+?) (duty|tariff|vat)",
                r"how much (duty|tariff|tax)",
            ],
            "strategy": "precise",
        },
        QueryIntentType.PROCEDURAL_GUIDE: {
            "keywords": [
                "步骤", "流程", "怎么", "如何", "step", "procedure",
                "清关", "申报", "报关", "customs clearance",
                "需要", "需要什么", "准备", "prepare",
                "流程", "process", "workflow",
            ],
            "patterns": [
                r"(怎么|如何|怎样)(做|办|操作|处理)",
                r"(步骤|流程|procedure|step)",
                r"需要(什么|哪些|准备)",
                r"how to (.+)",
            ],
            "strategy": "contextual",
        },
        QueryIntentType.COMPLIANCE_CHECK: {
            "keywords": [
                "能不能", "可以吗", "允许", "禁止", "限制",
                "can i", "is it allowed", "prohibited", "restricted",
                "合规", "合法性", "compliance", "legal",
                "需要许可", "需要证", "需要批",
                "认证", "证书", "许可", "证照", "批准", "管制",
            ],
            "patterns": [
                r"(能不能|可以|允许)(进口|出口|运输|携带)",
                r"(禁止|限制|不允许)(进口|出口|运输)",
                r"is (.+?) (allowed|prohibited|restricted)",
                r"can I (import|export|ship|carry)",
                r"需要(什么|哪些)?(认证|许可|证书|证照|批准)",
                r"是否(需要|允许|可以)(认证|许可|进口|出口)",
            ],
            "strategy": "comprehensive",
        },
        QueryIntentType.COMPARISON_ANALYSIS: {
            "keywords": [
                "对比", "比较", "区别", "差异",
                "compare", "difference", "vs", "versus",
                "不同国家", "多个", "各国",
                "哪个", "哪个更", "best",
            ],
            "patterns": [
                r"(.+?)和(.+?)(对比|比较|区别|差异)",
                r"(.+?) vs (.+?)",
                r"(不同|多个)国家",
                r"which (country|one) is",
            ],
            "strategy": "multi_search",
        },
    }
    
    # 国家实体识别
    COUNTRY_KEYWORDS = {
        "德国": ["德国", "germany", "de", "deutschland"],
        "中国": ["中国", "china", "cn", "中国"],
        "美国": ["美国", "usa", "us", "united states", "america"],
        "日本": ["日本", "japan", "jp"],
        "英国": ["英国", "uk", "united kingdom", "britain"],
        "法国": ["法国", "france", "fr"],
        "荷兰": ["荷兰", "netherlands", "nl", "holland"],
    }
    
    # HS编码模式
    HS_CODE_PATTERN = re.compile(r'\b\d{4,10}\b')
    
    def __init__(self, confidence_threshold: float = 0.2):
        self.confidence_threshold = confidence_threshold
        logger.info(f"IntentClassifier 初始化 | threshold={confidence_threshold}")
    
    async def classify(self, query: str, country: str = "") -> QueryIntent:
        """
        分类查询意图
        
        Args:
            query: 用户查询
            country: 目标国家（可选）
            
        Returns:
            QueryIntent: 分类结果
        """
        query_lower = query.lower()
        
# 计算每个意图类型的得分
        scores = {}
        for intent_type, config in self.INTENT_PATTERNS.items():
            score = self._calculate_score(query_lower, config)
            scores[intent_type] = score
        
        # ============ 领域语义纠偏 ============
        # 1) 对比分析提升：
        #    a. 同时含"对比/区别/差异/比较/相比/有何不同/哪个"与"和/与/vs/versus"（两主体对照）
        #    b. 或同一问题中出现 ≥2 个国家/区域实体（隐式对照，如"美国欧盟"）
        # 两种情形都优先判为对比分析，避免被"关税/税率/怎么/流程"等强词带走
        _contrast_words = ["对比", "区别", "差异", "比较", "相比", "有何不同", "有什么区别", "哪个", "哪个更"]
        _conjunction_words = ["和", "与", "vs", "versus", "、", ",", "跟"]
        is_contrast = any(w in query_lower for w in _contrast_words) and any(w in query_lower for w in _conjunction_words)
        
        # 国家/区域实体配对检测（隐式对照）
        _region_pairs = [
            ("美国", "欧盟"), ("欧盟", "美国"), ("美国", "德国"), ("德国", "美国"),
            ("中国", "德国"), ("德国", "中国"), ("中国", "美国"), ("美国", "中国"),
            ("德国", "荷兰"), ("荷兰", "德国"), ("德国", "法国"), ("法国", "德国"),
            ("德国", "日本"), ("日本", "德国"), ("美国", "欧盟"), ("欧盟", "美国"),
        ]
        has_entity_pair = any(a in query_lower and b in query_lower for a, b in _region_pairs)
        
        if is_contrast or has_entity_pair:
            scores[QueryIntentType.COMPARISON_ANALYSIS] = (
                scores.get(QueryIntentType.COMPARISON_ANALYSIS, 0.0) + 0.35
            )
            # 压住被同类强词带走的 factual / procedural 分数
            for _win in (QueryIntentType.FACTUAL_LOOKUP, QueryIntentType.PROCEDURAL_GUIDE):
                scores[_win] = max(0.0, scores.get(_win, 0.0) - 0.15)
        
        # 2) 合规检查增强：出现认证类关键词时提升 compliance，避免被"需要什么"归类到流程
        _compliance_boost_words = ["认证", "许可", "证书", "证照", "批准", "禁运", "限制进口", "不让进口"]
        if any(w in query_lower for w in _compliance_boost_words):
            scores[QueryIntentType.COMPLIANCE_CHECK] = (
                scores.get(QueryIntentType.COMPLIANCE_CHECK, 0.0) + 0.25
            )
        
        # 3) 虚助词触发 general_inquiry，防"是什么/什么是"误入其它类
        _what_words = ["是什么", "什么是", "是什么意思", "有哪些类别", "有哪些类型"]
        if any(w in query_lower for w in _what_words) and scores.get(QueryIntentType.FACTUAL_LOOKUP, 0.0) < 0.5:
            scores[QueryIntentType.GENERAL_INQUIRY] = (
                scores.get(QueryIntentType.GENERAL_INQUIRY, 0.0) + 0.30
            )
        
        # 选择得分最高的意图
        if not scores:
            return QueryIntent(
                intent_type=QueryIntentType.GENERAL_INQUIRY.value,
                confidence=0.5,
                recommended_strategy="contextual",
            )
        
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]
        
        # 如果最高分低于阈值，返回一般咨询
        if best_score < self.confidence_threshold:
            return QueryIntent(
                intent_type=QueryIntentType.GENERAL_INQUIRY.value,
                confidence=best_score,
                recommended_strategy="contextual",
            )
        
        # 提取实体
        entities = self._extract_entities(query, country)
        
        # 获取推荐策略（general_inquiry 无独立配置，默认 contextual）
        pattern_cfg = self.INTENT_PATTERNS.get(best_intent)
        strategy = pattern_cfg["strategy"] if pattern_cfg else "contextual"
        
        return QueryIntent(
            intent_type=best_intent.value,
            confidence=best_score,
            entities=entities,
            recommended_strategy=strategy,
        )
    
    def _calculate_score(self, query: str, config: dict) -> float:
        """计算意图得分"""
        # 关键词匹配（权重0.6）
        keywords = config.get("keywords", [])
        keyword_hits = sum(1 for kw in keywords if kw.lower() in query)
        # 只要有1个关键词命中就算有效，命中越多分数越高
        keyword_score = min(keyword_hits / 3, 1.0) if keyword_hits > 0 else 0.0
        
        # 模式匹配（权重0.4）
        patterns = config.get("patterns", [])
        pattern_hits = 0
        for pattern in patterns:
            try:
                if re.search(pattern, query, re.IGNORECASE):
                    pattern_hits += 1
            except re.error:
                continue
        pattern_score = min(pattern_hits / 2, 1.0) if pattern_hits > 0 else 0.0
        
        # 加权组合（不是除以总权重，而是直接加权）
        score = keyword_score * 0.6 + pattern_score * 0.4
        
        return min(score, 1.0)
    
    def _extract_entities(self, query: str, country: str = "") -> dict:
        """提取实体"""
        entities = {}
        
        # 国家实体
        if country:
            entities["country"] = country
        else:
            for country_name, keywords in self.COUNTRY_KEYWORDS.items():
                if any(kw in query.lower() for kw in keywords):
                    entities["country"] = country_name
                    break
        
        # HS编码实体
        hs_codes = self.HS_CODE_PATTERN.findall(query)
        if hs_codes:
            entities["hs_codes"] = hs_codes
        
        # 产品实体（简单提取）
        product_keywords = ["手机", "电脑", "服装", "食品", "化妆品", "电子产品"]
        for product in product_keywords:
            if product in query:
                entities.setdefault("products", []).append(product)
        
        return entities
    
    async def decompose_query(self, query: str) -> list[str]:
        """
        分解复杂查询为子查询
        
        用于多路检索
        """
        sub_queries = []
        
        # 简单的查询分解策略
        # 如果查询包含多个主题，分解为子查询
        if "和" in query or "与" in query or "vs" in query.lower():
            parts = re.split(r'(和|与|vs)', query, flags=re.IGNORECASE)
            for part in parts:
                part = part.strip()
                if part and len(part) > 5:
                    sub_queries.append(part)
        
        # 如果没有分解，返回原查询
        if not sub_queries:
            sub_queries = [query]
        
        return sub_queries
    
    def get_supported_intents(self) -> list[str]:
        """获取支持的意图类型"""
        return [intent.value for intent in QueryIntentType]
