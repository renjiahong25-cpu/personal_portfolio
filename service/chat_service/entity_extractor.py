# -*- coding: utf-8 -*-
"""
实体抽取模块
=====================================================
从用户口语化问题中抽取结构化业务实体：
    - product      商品名称
    - country      目标国家（默认补全为"德国"）
    - hs_code      HS编码（正则校验，口述编码自动清洗）
    - trade_term   贸易条款（FOB/CIF/DAP/DDP/LDP 等）
    - category     合规场景（关税 / 税务 / 认证 / 禁限运 / 单证）
    - normalized_query  口语纠错补全后的规范问句

设计要点：
    - 优先走 LLM 抽取（llm_client），LLM 不可用时自动降级到词典/正则启发式抽取；
    - 基于行业词典做关键字段归一化（贸易条款、合规场景映射）；
    - 每个函数记录入参摘要、耗时与结果摘要，异常记录完整堆栈且不吞异常。
"""
import asyncio
import json
import re
import time
from typing import Optional

from config.logging_config import get_logger
from core.llm_client import llm_client

logger = get_logger("entity_extractor")

# ============================================================
# 行业词典（用于归一化与降级抽取）
# ============================================================
TRADE_TERM_MAP = {
    "fob": "FOB",
    "cif": "CIF",
    "cfr": "CFR",
    "dap": "DAP",
    "ddu": "DDU",
    "ddp": "DDP",
    "fca": "FCA",
    "exw": "EXW",
    "ldp": "LDP",
    "ex-factory": "EXW",
}

CATEGORY_KEYWORDS = {
    "关税": ["关税", "税率", "税号", "免税", "反倾销", "进口税", "customs duty", "tariff"],
    "税务": ["ioss", "vat", "eori", "报税", "税务", "增值税", "缴纳", "申报税费", "递延"],
    "认证": ["ce", "reach", "rohs", "epr", "认证", "证书", "准入认证", "能效标签", "wetee", "battg"],
    "禁限运": ["禁止", "限运", "禁运", "带电", "电池", "液体", "粉末", "危险品", "管制", "exps", "iberg"],
    "单证": ["单证", "单据", "报关单", "发票", "装箱单", "许可证", "原产地证", "coo", "声明书", "consignment"],
}

HS_CODE_RE = re.compile(r"^\d{4,10}$")
_COUNTRY_RE = re.compile(r"(德国|法德|欧盟|中国|法国|英国|荷兰|比利时|奥地利|瑞士)")


def _pick_json(raw: str) -> Optional[dict]:
    """尽可能从 LLM 原始输出中解析 JSON（容忍 markdown 代码块/前后缀杂讯）"""
    if not raw:
        return None
    text = raw.strip()
    # 去掉 ```json ... ``` 这类围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 兜底：抓取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _normalize_hs_code(raw: str) -> str:
    """清洗口述/LLM给出的HS编码：去掉点号、横线、多余空格，保留纯数字段"""
    if not raw:
        return ""
    cleaned = "".join(re.findall(r"\d", str(raw)))
    if HS_CODE_RE.match(cleaned):
        return cleaned
    return ""


def _normalize_category(raw: str, query: str) -> str:
    """合规场景归一化：优先 LLM 结果，其次行业词典关键词匹配"""
    text = (raw or query or "").lower()
    if raw:
        for cate, _kws in CATEGORY_KEYWORDS.items():
            if raw.strip().lower() == cate.lower() or cate in raw:
                return cate
    for cate, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                return cate
    return ""


def _normalize_trade_term(raw: str) -> str:
    """贸易条款归一化为三字大写形式"""
    if not raw:
        return ""
    key = str(raw).strip().lower().replace(" ", "")
    return TRADE_TERM_MAP.get(key, raw.strip().upper() if len(raw.strip()) <= 4 else "")


class EntityExtractor:
    """实体抽取器：LLM 优先，词典正则兜底"""

    def __init__(self):
        self.llm = llm_client
        # 口语纠错补全 + 实体抽取的统一 Prompt
        self._extract_prompt = (
            "你是跨境清关领域的实体抽取器。请对用户提问完成两件事：\n"
            "1. 口语纠错补全：将口语化、残缺、有错别字的提问改写为结构完整、术语规范的问句（尽量保留原意，不新增事实）；\n"
            "2. 实体抽取：抽取 商品名称(product)、目标国家(country，缺失默认'德国')、"
            "HS编码(hs_code，无则空)、贸易条款(trade_term，如FOB/CIF/DAP/DDP/LDP)、\n"
            "合规场景(category，只能是：关税/税务/认证/禁限运/单证 之一，无法判定则填空)。\n"
            "仅返回JSON，格式：{\"normalized_query\":\"...\",\"product\":\"...\",\"country\":\"...\","
            "\"hs_code\":\"...\",\"trade_term\":\"...\",\"category\":\"...\"}"
        )
        logger.info("EntityExtractor 初始化完成")

    def _keyword_fallback(self, query: str) -> dict:
        """LLM 不可用时的词典/正则启发式抽取（基础可用，不阻塞问答主链路）"""
        entities = {
            "product": "",
            "country": "德国",
            "hs_code": _normalize_hs_code(query),
            "trade_term": "",
            "category": _normalize_category("", query),
        }
        m = _COUNTRY_RE.search(query)
        if m:
            entities["country"] = m.group(1) if m.group(1) != "法德" else "德国"
        # 贸易条款启发式
        for key in TRADE_TERM_MAP:
            if key in query.lower():
                entities["trade_term"] = TRADE_TERM_MAP[key]
                break
        logger.info(f"实体降级抽取(词典正则) 完成 | entities={entities}")
        return entities

    async def extract(self, query: str, request_id: str = "") -> dict:
        """
        实体抽取入口（异步）
        :return: {normalized_query, product, country, hs_code, trade_term, category, source}
        """
        start = time.time()
        req_tag = request_id or "-"
        query = (query or "").strip()
        logger.info(f"[{req_tag}] 实体抽取开始 | query_len={len(query)} | query={query[:80]}")

        # 空问题直接返回空结构
        if not query:
            logger.warning(f"[{req_tag}] 实体抽取 入参为空，返回空实体")
            return {
                "normalized_query": "",
                "product": "",
                "country": "德国",
                "hs_code": "",
                "trade_term": "",
                "category": "",
                "source": "empty",
            }

        result = None
        try:
            # LLM 抽取 + 口语纠错（阻塞调用放入线程池，避免卡死事件循环）
            messages = [
                {"role": "system", "content": self._extract_prompt},
                {"role": "user", "content": query},
            ]
            raw = await asyncio.to_thread(self.llm.chat, messages, 0.0, 512)
            parsed = _pick_json(raw)
            if parsed:
                result = {
                    "normalized_query": str(parsed.get("normalized_query") or query).strip(),
                    "product": str(parsed.get("product") or "").strip(),
                    "country": str(parsed.get("country") or "德国").strip() or "德国",
                    "hs_code": _normalize_hs_code(parsed.get("hs_code")),
                    "trade_term": _normalize_trade_term(parsed.get("trade_term")),
                    "category": _normalize_category(parsed.get("category"), query),
                    "source": "llm",
                }
                logger.info(f"[{req_tag}] 实体抽取(LLM) 成功 | raw={raw[:200]}")
            else:
                logger.warning(f"[{req_tag}] 实体抽取 LLM 输出无法解析，转词典兜底 | raw={raw[:120]}")
        except Exception as e:
            logger.error(f"[{req_tag}] 实体抽取 LLM 调用异常，转词典兜底 | error={e}", exc_info=True)

        # 降级兜底：词典/正则抽取
        if result is None:
            result = self._keyword_fallback(query)
            result["normalized_query"] = query
            result["source"] = "keyword"

        elapsed = round(time.time() - start, 3)
        summary = {
            "product": result["product"][:50],
            "country": result["country"],
            "hs_code": result["hs_code"] or "-",
            "trade_term": result["trade_term"] or "-",
            "category": result["category"] or "-",
        }
        logger.info(
            f"[{req_tag}] 实体抽取完成 | elapsed={elapsed}s | source={result['source']} | {summary}"
        )
        return result