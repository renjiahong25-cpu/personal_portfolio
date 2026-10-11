# -*- coding: utf-8 -*-
"""
QueryDecomposer 需求拆解模块
============================
将复杂用户问题拆解为多个可独立检索的子问题，分别检索后合并结果，
提升跨章节/跨文档（尤其中文问题命中德文原文）的召回率。

设计要点：
    - 子问题粒度：按"事实维度"拆分（主体/前置条件/流程步骤/单证/时限/
      异常处理…），保证每个子问题聚焦单一检索意图；
    - 语言匹配：解析抽取的国家实体映射为官方语言，子问题使用该语言撰写，
      与目标国官方文档用语对齐，弥补 BM25 词表鸿沟；
    - 并行度上限：strict 控制子问题数量，避免检索膨胀；
    - 容错：LLM 失败或解析失败返回 None，主链路回退到原始问题单次检索。
"""
import asyncio
import json
import re
import time

from config.logging_config import get_logger
from config.settings import (
    DECOMPOSE_ENABLE,
    MAX_SUB_QUERIES,
)
from core.llm_client import llm_client

logger = get_logger("query_decomposer")

_DECOMPOSE_PROMPT = (
    "你是跨境清关领域的问题分析专家。用户会提出一个复杂的合规/流程问题，"
    "单独一次检索通常只能覆盖其中一部分。请把原问题拆解为 **{min_sub}~{max_sub} 个**"
    "**可独立检索**的子问题，务必 2 个及以上——即使看起来不复杂也要从不同事实维度拆分，"
    "以扩大检索覆盖面。\n"
    "要求：\n"
    "1. 每个子问题聚焦单一检索意图（参与条件/资质、操作流程/步骤、所需单证、"
    "时限与截止日期、异常或驳回处理、费用或罚则等），不要交叉；\n"
    "2. 子问题之间尽量不重叠，共同覆盖原问题的全部问点，且合并起来应能还原完整流程；\n"
    "3. 若原问题本身是一个流程/步骤问题（如报关、认证、申报、准入），请优先按"
    "「准备与资格 → 提交/办理 → 审核/海关处理 → 结果/放行/后续义务」等阶段拆分，"
    "让每个阶段成为一个子问题；\n"
    "4. 使用目标国家官方语言撰写子问题（目标语言：{lang}），保留原问题里的专有名词"
    "（机构名、法规名、IT系统名如 ATLAS 等）；\n"
    "5. 每个子问题应自包含（携带足够的上下文词汇），不要用指代（「这个/它」）。\n"
    "直接输出 JSON 数组，不要任何解释或 Markdown 标记。格式：\n"
    '["子问题1", "子问题2", ...]'
)

_LANG_MAP = {
    "德国": "德语", "Deutschland": "德语", "Germany": "德语",
    "奥地利": "德语", "Austria": "德语", "Österreich": "德语", "Oesterreich": "德语",
    "瑞士": "德语", "Switzerland": "德语", "Schweiz": "德语",
    "法国": "法语", "France": "法语", "Frankreich": "法语",
    "荷兰": "荷兰语", "Netherlands": "荷兰语", "Niederlande": "荷兰语",
    "比利时": "荷兰语/法语", "Belgium": "荷兰语/法语", "Belgien": "荷兰语/法语",
    "美国": "英语", "USA": "英语", "United States": "英语",
    "英国": "英语", "UK": "英语", "United Kingdom": "英语",
    "欧盟": "德语", "EU": "德语", "European Union": "德语",
    "中国": "中文", "China": "中文",
}


def _country_to_lang(country: str) -> str:
    c = (country or "").strip()
    if not c:
        return "中文"
    return _LANG_MAP.get(c, "中文")


def _extract_json_array(text: str) -> list[str] | None:
    """从 LLM 输出中容错提取字符串数组（容忍 ```json 包裹、前后说明）"""
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        # 容错：非严格 JSON（如单引号），尝试简单清洗
        cleaned = re.sub(r"[\"'“”‘’]", '"', m.group(0))
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return None
    if not isinstance(parsed, list):
        return None
    sub = [str(s).strip() for s in parsed if isinstance(s, str) and str(s).strip()]
    return sub or None


class QueryDecomposer:
    """问题拆解器：原问题 → 若干子问题（可按官方语言撰写）"""

    def __init__(self):
        self.llm = llm_client
        logger.info(
            f"QueryDecomposer 初始化完成 | enable={DECOMPOSE_ENABLE} | "
            f"max_sub_queries={MAX_SUB_QUERIES}"
        )

    async def decompose(
        self, query: str, entities: dict, request_id: str = ""
    ) -> list[str] | None:
        """拆解为子问题列表；降级关闭/失败时返回 None"""
        start = time.time()
        req_tag = request_id or "-"
        if not DECOMPOSE_ENABLE:
            logger.info(f"[{req_tag}] 需求拆解已由配置关闭，跳过")
            return None

        lang = _country_to_lang(entities.get("country", ""))
        user_content = (
            f"用户问题：{query}\n"
            f"抽取实体：商品={entities.get('product','')} 国家={entities.get('country','')} "
            f"HS编码={entities.get('hs_code','')} 贸易条款={entities.get('trade_term','')} "
            f"合规场景={entities.get('category','')}\n"
        )
        messages = [
            {"role": "system", "content": _DECOMPOSE_PROMPT.format(
                min_sub=min(2, MAX_SUB_QUERIES), max_sub=MAX_SUB_QUERIES, lang=lang
            )},
            {"role": "user", "content": user_content},
        ]
        try:
            raw = await asyncio.to_thread(self.llm.chat, messages, 0.2, 2048, no_think=True)
            subs = _extract_json_array(raw)
            if not subs:
                logger.warning(f"[{req_tag}] 需求拆解解析失败，回退单次检索 | raw={raw[:100]}")
                return None
            # 去重且尊重上限
            seen: set[str] = set()
            dedup = []
            for s in subs:
                k = s.lower()
                if k not in seen:
                    seen.add(k)
                    dedup.append(s)
            subs = dedup[:MAX_SUB_QUERIES]
            elapsed = round(time.time() - start, 3)
            logger.info(
                f"[{req_tag}] 需求拆解完成 | sub_count={len(subs)} | lang={lang} | "
                f"elapsed={elapsed}s | subs={subs}"
            )
            return subs
        except TimeoutError:
            logger.error(f"[{req_tag}] 需求拆解超时，回退单次检索")
            return None
        except Exception as e:
            logger.error(f"[{req_tag}] 需求拆解异常，回退单次检索 | error={e}", exc_info=True)
            return None


# 全局单例
query_decomposer = QueryDecomposer()