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
    - is_followup    多轮判定：当前问题是否延续前文话题
    - rewritten_query 条件改写后的自包含问句（新话题/降级时为空）

设计要点：
    - 优先走 LLM 抽取（llm_client），LLM 不可用时自动降级到词典/正则启发式抽取；
    - 基于行业词典做关键字段归一化（贸易条款、合规场景映射）；
    - 多轮：携带 history 时同一次 LLM 调用内完成"延续/新话题"判定 + slot 继承 + 问题改写；
      新话题/换国家零继承（防上下文污染），LLM 失败 fail-open 回退单轮行为；
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
        # 口语纠错补全 + 实体抽取 + 意图识别的统一 Prompt
        self._extract_prompt = (
            "你是跨境清关领域的实体抽取器。请对用户提问完成以下任务：\n"
            "1. 口语纠错补全：将口语化、残缺、有错别字的提问改写为结构完整、术语规范的问句（尽量保留原意，不新增事实）；\n"
            "2. 实体抽取：抽取 商品名称(product)、目标国家(country，缺失默认'德国')、"
            "HS编码(hs_code，无则空)、贸易条款(trade_term，如FOB/CIF/DAP/DDP/LDP)、\n"
            "合规场景(category，只能是：关税/税务/认证/禁限运/单证 之一，无法判定则填空)。\n"
            "3. 意图识别（intent，五选一）：\n"
            "   - normal：正常政策问答（默认）；\n"
            "   - ingest_url：消息中提供了政策文档网址/链接（含 URL 时优先此值，并从域名推断目标国家，"
            "如 impots.gouv.fr→法国、zoll.de→德国）；\n"
            "   - ingest_country：用户明确要求加入/抓取/收录某国的政策数据（如\"加入法国政策\"、"
            "\"自动抓取德国\"、\"收录意大利的海关规则\"）；\n"
            "   - ingest_confirm：用户确认开始抓取/入库（如\"确认\"、\"确认抓取\"、\"好的开始吧\"，"
            "仅当前文上下文是待确认的来源列表时才用此值）；\n"
            "   - ingest_status：用户询问入库/抓取进度（如\"入库进度\"、\"好了吗\"、\"抓完了吗\"）。\n"
            "仅返回JSON，格式：{\"normalized_query\":\"...\",\"product\":\"...\",\"country\":\"...\","
            "\"hs_code\":\"...\",\"trade_term\":\"...\",\"category\":\"...\",\"intent\":\"...\"}"
        )
        # 多轮条件改写规则（仅当携带历史时拼入 system prompt）
        self._multiturn_prompt = (
            "4. 多轮上下文：用户输入中带有本会话最近对话（每轮含用户问题与已确认实体）。"
            "请先判断当前问题与历史的关系，再决定实体抽取与改写：\n"
            "   a) 若当前问题与历史无关，或明确更换了国家/商品/合规场景：禁止继承任何历史上下文，"
            "is_followup=false，rewritten_query 直接等于当前问题原文；\n"
            "   b) 若是延续（当前问题省略了前文已提及的国家/商品/HS编码/贸易条款等要素）："
            "只补全历史中已明确确认的实体，严禁猜测或新增事实，继承的实体同样写入对应输出字段；"
            "is_followup=true，rewritten_query 改写为结构完整、自包含（不含指代词）的问句；\n"
            "   c) 当前问题与历史冲突时，以当前问题为准；\n"
            "   d) 意图识别同理结合历史：用户仅回复\"确认\"且上一轮展示了待确认的来源列表时，"
            "intent=ingest_confirm。\n"
            "输出JSON需额外包含两个字段：\"is_followup\":true或false, \"rewritten_query\":\"...\""
        )
        # intent 合法值
        self._INTENTS = {"normal", "ingest_url", "ingest_country", "ingest_confirm", "ingest_status"}
        logger.info("EntityExtractor 初始化完成")

    # 历史实体摘要的字段顺序与标签
    _HISTORY_ENTITY_KEYS = (
        ("country", "国家"),
        ("product", "商品"),
        ("hs_code", "HS"),
        ("trade_term", "贸易条款"),
        ("category", "场景"),
    )

    @classmethod
    def _format_history(cls, history: list[dict]) -> str:
        """历史轮压缩为紧凑文本（每轮问题截断200字，整体截断800字）"""
        lines = []
        for i, turn in enumerate(history or [], start=1):
            q = (str(turn.get("query") or "")[:200])
            ent = turn.get("entities") or {}
            parts = [
                f"{label}={str(ent.get(key) or '').strip()}"
                for key, label in cls._HISTORY_ENTITY_KEYS
                if str(ent.get(key) or "").strip()
            ]
            lines.append(f"第{i}轮 问题：{q} | 已确认：{' '.join(parts) if parts else '无'}")
        return "\n".join(lines)[:800]

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

    async def extract(self, query: str, request_id: str = "", history: Optional[list[dict]] = None) -> dict:
        """
        实体抽取入口（异步），携带历史时同时完成多轮条件改写 + 实体继承
        :param history: 最近历史轮（旧→新），元素 {"query": 用户问题, "entities": {...}}；
                        非空时启用条件改写（延续→继承实体并改写自包含问题；新话题→零继承）
        :return: {normalized_query, product, country, hs_code, trade_term, category,
                  is_followup, rewritten_query, source}
        """
        start = time.time()
        req_tag = request_id or "-"
        query = (query or "").strip()
        history = history or []
        logger.info(
            f"[{req_tag}] 实体抽取开始 | query_len={len(query)} | history_turns={len(history)} | query={query[:80]}"
        )

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
                "is_followup": False,
                "rewritten_query": "",
                "intent": "normal",
                "source": "empty",
            }

        result = None
        try:
            # LLM 抽取 + 口语纠错（+ 条件改写，携带历史时）；阻塞调用放入线程池，避免卡死事件循环
            if history:
                system = self._extract_prompt + "\n" + self._multiturn_prompt
                user_content = f"最近对话（旧→新）：\n{self._format_history(history)}\n当前问题：{query}"
            else:
                system = self._extract_prompt
                user_content = query
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]
            raw = await asyncio.to_thread(self.llm.chat, messages, 0.0, 768, no_think=True)
            parsed = _pick_json(raw)
            if parsed:
                # 无历史时强制不继承（防 LLM 臆造延续关系）
                is_followup = bool(parsed.get("is_followup")) if history else False
                rewritten = str(parsed.get("rewritten_query") or "").strip() if history else ""
                intent = str(parsed.get("intent") or "normal").strip().lower()
                if intent not in self._INTENTS:
                    intent = "normal"
                result = {
                    "normalized_query": str(parsed.get("normalized_query") or query).strip(),
                    "product": str(parsed.get("product") or "").strip(),
                    "country": str(parsed.get("country") or "德国").strip() or "德国",
                    "hs_code": _normalize_hs_code(parsed.get("hs_code")),
                    "trade_term": _normalize_trade_term(parsed.get("trade_term")),
                    "category": _normalize_category(parsed.get("category"), query),
                    "is_followup": is_followup,
                    "rewritten_query": rewritten,
                    "intent": intent,
                    "source": "llm",
                }
                logger.info(
                    f"[{req_tag}] 实体抽取(LLM) 成功 | is_followup={is_followup} | "
                    f"rewritten_len={len(rewritten)} | raw={raw[:200]}"
                )
            else:
                logger.warning(f"[{req_tag}] 实体抽取 LLM 输出无法解析，转词典兜底 | raw={raw[:120]}")
        except Exception as e:
            logger.error(f"[{req_tag}] 实体抽取 LLM 调用异常，转词典兜底 | error={e}", exc_info=True)

        # 降级兜底：词典/正则抽取（降级时不做多轮继承、不识别入库意图，安全默认）
        if result is None:
            result = self._keyword_fallback(query)
            result["normalized_query"] = query
            result["is_followup"] = False
            result["rewritten_query"] = ""
            result["intent"] = "normal"
            result["source"] = "keyword"

        elapsed = round(time.time() - start, 3)
        summary = {
            "product": result["product"][:50],
            "country": result["country"],
            "hs_code": result["hs_code"] or "-",
            "trade_term": result["trade_term"] or "-",
            "category": result["category"] or "-",
            "is_followup": result.get("is_followup", False),
        }
        logger.info(
            f"[{req_tag}] 实体抽取完成 | elapsed={elapsed}s | source={result['source']} | {summary}"
        )
        return result