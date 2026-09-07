# -*- coding: utf-8 -*-
"""
AI 幻觉双重校验模块
=====================================================
两道防线：
    1. 生成端 Prompt 约束（见 chat_flow 生成 Prompt：要求只依据资料、禁止编造）；
    2. 本模块独立校验：
       a) 本地统计门控（不依赖外部调用）：抽取答案中的关键词/数字，检查是否能在
          检索资料原文中找到依据，覆盖率过低即判警戒；
       b) LLM 独立校验（llm_client.check_fact）：以"原文 vs 回答"进行事实核对，
          仅接受 YES/NO 判定。

降级：LLM_CHECK_ENABLE=false 或高峰期 degraded=True 时，仅保留本地统计门控。
"""
import asyncio
import re
import time
from typing import Optional

from config.logging_config import get_logger
from config import settings
from core.llm_client import llm_client

logger = get_logger("fact_checker")

# 本地门控参数
_TOKEN_MIN_LEN = 2          # 参与覆盖率比对的词元最小长度
_STOPWORDS = {
    "的", "了", "和", "与", "或", "是", "在", "请", "需要", "可以", "应", "要",
    "我们", "您", "贵司", "如果", "因为", "所以", "但是", "并且", "建议", "可能",
    "相关", "以及", "请咨询", "建议咨询", "本", "该", "对", "为", "等", "将",
}
_COVERAGE_PASS_THRESHOLD = 0.2   # 覆盖率低于该值判定为"疑似无依据"
_NUMBERS_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")


def _tokenize(text: str) -> set[str]:
    """抽取用于覆盖率比对的业务词元（中文词组 + 英文/数字词）"""
    text = (text or "").lower()
    tokens = set(re.findall(r"[a-z]{2,}[a-z0-9\-/]*|\d{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for phrase in cjk:
        tokens.add(phrase)
        # 补充2字词组切分，提升匹配召回
        for i in range(len(phrase) - 1):
            tokens.add(phrase[i : i + 2])
    return {t for t in tokens if len(t) >= _TOKEN_MIN_LEN and t not in _STOPWORDS}


class FactChecker:
    """事实校验器：本地门控 + LLM 二次核查（双重校验）"""

    def __init__(self):
        self.llm = llm_client
        logger.info(f"FactChecker 初始化完成 | llm_check_enable={settings.LLM_CHECK_ENABLE}")

    def _local_gate(self, answer: str, sources_text: str) -> dict:
        """
        本地统计门控：回答中的业务词元/数字是否在资料原文中有依据。
        仅作软预警，不单独判死（避免误伤合理的转述表达）。
        """
        ans_tokens = _tokenize(answer)
        src_text = sources_text.lower()
        if not ans_tokens:
            return {"passed": True, "coverage": 1.0, "unsupported": []}
        covered = {t for t in ans_tokens if t in src_text}
        coverage = len(covered) / len(ans_tokens)
        unsupported = sorted(list(ans_tokens - covered))[:15]
        # 数字类词元必须逐字出现在原文，防止编造税率/金额
        ans_numbers = _NUMBERS_RE.findall(answer)
        missing_numbers = [n for n in ans_numbers if n not in src_text]
        passed = coverage >= _COVERAGE_PASS_THRESHOLD and len(missing_numbers) <= max(1, len(ans_numbers) // 4)
        return {
            "passed": passed,
            "coverage": round(coverage, 3),
            "unsupported": unsupported,
            "missing_numbers": missing_numbers[:10],
        }

    async def check(
        self,
        answer: str,
        contexts: list[dict],
        request_id: str = "",
        degraded: bool = False,
    ) -> dict:
        """
        双重校验入口
        :return: {passed, reason, local_pass, llm_pass, coverage, llm_checked}
        """
        start = time.time()
        req_tag = request_id or "-"
        logger.info(f"[{req_tag}] 事实校验开始 | answer_len={len(answer)} | contexts={len(contexts)} | degraded={degraded}")

        if not answer:
            logger.warning(f"[{req_tag}] 事实校验 空回答，直接判为不通过")
            return {"passed": False, "reason": "空回答", "local_pass": False, "llm_pass": False, "coverage": 0.0, "llm_checked": False}

        sources_text = "\n".join(c.get("content", "") for c in contexts)
        # ---- 校验1：本地统计门控 ----
        local = self._local_gate(answer, sources_text)
        local_pass = local["passed"]
        logger.info(
            f"[{req_tag}] 本地统计门控 完成 | passed={local_pass} | coverage={local['coverage']} | "
            f"unsupported={local['unsupported'][:5]}"
        )

        # ---- 校验2：LLM 独立二次核查 ----
        llm_pass, llm_checked = True, False
        if settings.LLM_CHECK_ENABLE and not degraded and sources_text.strip():
            try:
                ok = await asyncio.to_thread(self.llm.check_fact, sources_text, answer)
                llm_pass = ok
                llm_checked = True
            except TimeoutError:
                logger.warning(f"[{req_tag}] LLM 事实校验超时，按通过处理（本地门控兜底）")
            except Exception as e:
                logger.error(f"[{req_tag}] LLM 事实校验异常，按通过处理（本地门控兜底） | error={e}", exc_info=True)

        passed = local_pass and llm_pass
        reasons = []
        if not local_pass:
            reasons.append(f"本地覆盖率偏低({local['coverage']})，存在无依据词元: {local['unsupported'][:5]}")
        if llm_checked and not llm_pass:
            reasons.append("LLM 二次核查判定回答与原文不符")
        reason = "；".join(reasons) or "通过"

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"[{req_tag}] 事实校验完成 | passed={passed} | local={local_pass} | llm={llm_pass}(checked={llm_checked}) | "
            f"elapsed={elapsed}s | reason={reason[:120]}"
        )
        return {
            "passed": passed,
            "reason": reason,
            "local_pass": local_pass,
            "llm_pass": llm_pass,
            "coverage": local["coverage"],
            "llm_checked": llm_checked,
        }