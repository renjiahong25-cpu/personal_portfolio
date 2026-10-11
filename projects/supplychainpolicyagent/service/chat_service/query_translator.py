# -*- coding: utf-8 -*-
"""
QueryTranslator 查询翻译模块
=============================
将用户中文/多语言查询统一翻译为目标国家的官方语言，再进入 BM25/向量检索，
弥补 BM25 的中外词汇鸿沟（中文 query 对德文正文的命中率几乎为 0）。

设计要点：
    - 仅当目标国家官方语言非中文（由 entities.country 判定）时触发；
    - 保留专有名词（法规名、IT系统名如 ATLAS/UZK/EORI 等原样输出）；
    - 输出与官方文档用语一致的译文，作为检索 query 主干；
    - 失败/超时返回 None，主链路回退到原始语言检索（不阻塞）。
"""
import asyncio
import time

import httpx

from config.logging_config import get_logger
from config.settings import QUERY_TRANSLATE_ENABLE, LLM_BASE_URL, LLM_MODEL_NAME, LLM_API_KEY, IS_CLOUD_LLM
from core.llm_client import llm_client

logger = get_logger("query_translator")

# 翻译调用参数
_TRANSLATE_MAX_TOKENS = 4096    # 翻译输出预算上限（含 reasoning 余量）；实际预算按输入长度估算，不超此上限
_TRANSLATE_MAX_RETRIES = 3      # 幂等重试次数：空结果/异常均重试，直到成功或达到上限
_TRANSLATE_TIMEOUT = 120.0      # 翻译读超时（全局 LLM_TIMEOUT=15s 是短任务口径）
_TRANSLATE_MAX_INPUT = 4000     # 翻译输入文本长度上限（字符）


def _estimate_translate_budget(input_text: str, max_in: int = _TRANSLATE_MAX_INPUT) -> int:
    """按输入长度估算翻译输出预算（token）：
    - 启发式：译文 ≈ 输入有效字符数 ×0.9（中→欧系语言大体相当）；
    - token ≈ 字符 / 3（欧系语言 ~3.2 字符/token），加 reasoning 余量 ~384；
    - 结果夹在 512 与 _TRANSLATE_MAX_TOKENS 之间。
    """
    text = (input_text or "")[: max_in]
    chars = len(text.strip())
    if chars <= 0:
        return 512
    est_out_chars = int(chars * 0.9)
    est_tokens = max(64, int(est_out_chars / 3))
    budget = est_tokens + 384
    return max(512, min(int(_TRANSLATE_MAX_TOKENS), budget))

_TRANSLATE_PROMPT = (
    "你是跨境清关领域的专业翻译。请把下面的用户问题翻译成 {lang}，"
    "用于检索该国的官方政策法规文档。要求：\n"
    "1. 忠实表达原问题的意图（包括商品、HS编码、贸易条款、合规场景、流程等）；\n"
    "2. 保留专有名词不译（机构名、法规名、IT系统名如 ATLAS、UZK、EORI、BIN 等）；\n"
    "3. 使用该国官方文件常用的书面语，不要口语化；\n"
    "4. 直接输出译文，不要任何解释、引号或 Markdown 标记。"
)

# 与 hyde_generator 保持一致的国家→官方语言映射（补充少数国家）
_lang_map = {
    "德国": "德语", "Deutschland": "德语", "Germany": "德语", "Österreich": "德语", "Oesterreich": "德语",
    "奥地利": "德语", "Austria": "德语",
    "瑞士": "德语", "Switzerland": "德语", "Schweiz": "德语",
    "法国": "法语", "France": "法语", "Frankreich": "法语",
    "荷兰": "荷兰语", "Netherlands": "荷兰语", "Niederlande": "荷兰语",
    "比利时": "荷兰语/法语/德语", "Belgium": "荷兰语/法语/德语", "Belgien": "荷兰语/法语/德语",
    "美国": "英语", "USA": "英语", "United States": "英语",
    "英国": "英语", "UK": "英语", "United Kingdom": "英语",
    "欧盟": "德语", "EU": "德语", "European Union": "德语",
}


def _country_to_lang(country: str) -> str:
    c = (country or "").strip()
    return _lang_map.get(c, "")


class QueryTranslator:
    """查询翻译器：用户查询 → 目标国官方语言（检索前调用）；亦可翻译校验用的回答片段"""

    def __init__(self):
        self.llm = llm_client
        logger.info(f"QueryTranslator 初始化完成 | translate_enable={QUERY_TRANSLATE_ENABLE}")

    async def _call(self, messages: list[dict], req_tag: str, lang: str, max_tokens: int = None) -> str | None:
        """底层 LLM 翻译调用：幂等重试（最多 _TRANSLATE_MAX_RETRIES 次）+ 空输出降预算，返回译文或 None。
        直连 httpx 且读超时放宽（全局 LLM_TIMEOUT=15s 会掐断长文本翻译）。
        预算策略：max_tokens 由调用方按输入长度评估（译文≈输入字数的 45%~60%，留余量）；
        未传则用 _TRANSLATE_MAX_TOKENS 兜底。空输出时降级预算重试（reasoning 吃光预算场景）。"""
        last_err = ""
        attempts = 0
        url = f"{LLM_BASE_URL}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        budget = max(256, int(max_tokens or _TRANSLATE_MAX_TOKENS))

        def _raw_chat(_budget: int) -> str:
            msgs = []
            for m in messages:
                c = dict(m)
                if c.get("role") == "user":
                    c["content"] = "/no_think\n" + c["content"]
                msgs.append(c)
            payload = {
                "model": LLM_MODEL_NAME,
                "messages": msgs,
                "temperature": 0.2,
                "max_tokens": _budget,
            }
            # 云端兼容分支（provider=local 时 no-op）；/no_think 注入在云端分支内还原
            from core.llm_client import apply_cloud_compat
            payload = apply_cloud_compat(payload)
            if IS_CLOUD_LLM:
                for m in msgs:
                    cc = m.get("content")
                    if isinstance(cc, str) and cc.startswith("/no_think\n"):
                        m["content"] = cc[len("/no_think\n"):]
            timeout = httpx.Timeout(connect=10.0, read=_TRANSLATE_TIMEOUT, write=60.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    return ""
                return (choices[0].get("message", {}).get("content") or "")

        while attempts < _TRANSLATE_MAX_RETRIES:
            attempts += 1
            try:
                raw = await asyncio.to_thread(_raw_chat, budget)
                text = (raw or "").strip().strip('"\'“”‘’')
                if text:
                    if attempts > 1:
                        logger.info(f"[{req_tag}] 翻译重试成功 | lang={lang} | attempt={attempts} | max_tokens={budget}")
                    return text
                last_err = "empty_result"
                logger.warning(f"[{req_tag}] 翻译结果为空 | lang={lang} | attempt={attempts}/{_TRANSLATE_MAX_RETRIES} | max_tokens={budget}")
                budget = max(256, budget // 2)
            except Exception as e:
                last_err = str(e)
                logger.error(
                    f"[{req_tag}] 翻译调用异常 | lang={lang} | attempt={attempts}/{_TRANSLATE_MAX_RETRIES} | error={e}",
                    exc_info=True,
                )
        logger.error(f"[{req_tag}] 翻译最终失败（{_TRANSLATE_MAX_RETRIES} 次） | lang={lang} | last_err={last_err[:120]}")
        return None

    async def translate_text(self, text: str, lang: str, request_id: str = "") -> str | None:
        """通用文本翻译：把任意正文翻译为目标语言（用于事实校验对齐语言）"""
        start = time.time()
        req_tag = request_id or "-"
        if not QUERY_TRANSLATE_ENABLE or not text or not text.strip():
            return None
        messages = [
            {"role": "system", "content": _TRANSLATE_PROMPT.format(lang=lang)},
            {"role": "user", "content": f"待翻译文本：\n{text[:_TRANSLATE_MAX_INPUT]}"},
        ]
        budget = _estimate_translate_budget(text)
        translated = await self._call(messages, req_tag, lang, max_tokens=budget)
        if translated:
            elapsed = round(time.time() - start, 3)
            logger.info(f"[{req_tag}] 文本翻译完成 | lang={lang} | len={len(translated)} | max_tokens={budget} | elapsed={elapsed}s | head={translated[:40]}")
        return translated

    async def translate(self, query: str, entities: dict, request_id: str = "") -> str | None:
        """
        将查询翻译为目标国官方语言；无需翻译（中国/无国家）或失败时返回 None。
        :return: 译文（去掉首尾引号/空白）；不需要翻译时返回 None
        """
        start = time.time()
        req_tag = request_id or "-"
        if not QUERY_TRANSLATE_ENABLE:
            return None
        lang = _country_to_lang(entities.get("country", ""))
        if not lang or lang == "中文":
            return None
        if not query or not query.strip():
            return None

        user_content = (
            f"用户问题：{query}\n"
            f"抽取实体：商品={entities.get('product','')} 国家={entities.get('country','')} "
            f"HS编码={entities.get('hs_code','')} 贸易条款={entities.get('trade_term','')} "
            f"合规场景={entities.get('category','')}\n"
        )
        messages = [
            {"role": "system", "content": _TRANSLATE_PROMPT.format(lang=lang)},
            {"role": "user", "content": user_content},
        ]
        budget = _estimate_translate_budget(user_content)
        translated = await self._call(messages, req_tag, lang, max_tokens=budget)
        if translated:
            elapsed = round(time.time() - start, 3)
            logger.info(f"[{req_tag}] 查询翻译完成 | lang={lang} | max_tokens={budget} | elapsed={elapsed}s | translated={translated[:120]}")
        return translated


# 全局单例
query_translator = QueryTranslator()