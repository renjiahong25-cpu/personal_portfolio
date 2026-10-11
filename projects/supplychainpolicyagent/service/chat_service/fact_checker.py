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
# LLM 校验的原文输入上限：contexts 按相关度排序，取头部即可覆盖答案引用的高相关段落；
# 全量原文（可达 6 万字符）会让 xhigh reasoning 爆预算→空输出（E2E 复现 4 连空）
_FACT_CHECK_SRC_CAP = 12000


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

        # 跨语言护栏：回答为中文、资料主要为外文（如德国ATLAS德文手册）时，
        # 中文字词必然匹配不上原文，覆盖率指标失真。此时只校验(1)数字有原文依据
        # (2)英文/数字专名(如 ATLAS/MRN/UZK/ARI/BIN)在原文出现，避免把"中文转述
        # 德文资料"误判为无依据。
        # 判定：答案含中文，且资料正文中文稀疏（中文占比 < 0.5，即主体为外文）；
        # 主/辅语言混合场景（如多文档）亦按"主体语料非中文"进入跨语言模式。
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", answer or ""))
        src_cjk = re.findall(r"[\u4e00-\u9fff]", sources_text or "")
        cjk_ratio = len(src_cjk) / max(1, len(list(iter(sources_text or ""))))
        cross_lang = has_cjk and cjk_ratio < 0.5
        if cross_lang:
            latin_tokens = {t for t in ans_tokens if re.match(r"^[a-z]", t)}
            missing_latin = sorted(latin_tokens - set(re.findall(r"[a-z][a-z0-9\-/]+", src_text)))[:10]
            num_ok = len(missing_numbers) <= max(1, len(ans_numbers) // 4)
            latin_ok = (not latin_tokens) or len(latin_tokens - set(missing_latin)) / len(latin_tokens) >= 0.3
            passed = num_ok and latin_ok
            return {
                "passed": passed,
                "coverage": round(coverage, 3),
                "cross_lang": True,
                "unsupported": unsupported,
                "missing_numbers": missing_numbers[:10],
                "missing_latin": missing_latin,
            }

        passed = coverage >= _COVERAGE_PASS_THRESHOLD and len(missing_numbers) <= max(1, len(ans_numbers) // 4)
        return {
            "passed": passed,
            "coverage": round(coverage, 3),
            "cross_lang": False,
            "unsupported": unsupported,
            "missing_numbers": missing_numbers[:10],
        }

    async def check(
        self,
        answer: str,
        contexts: list[dict],
        request_id: str = "",
        degraded: bool = False,
        lang: str = "",
    ) -> dict:
        """
        双重校验入口
        :return: {passed, reason, local_pass, llm_pass, coverage, llm_checked}
        :param lang: 兼容参数（调用方传入目标语言，如"德语"）。实际跨语言判定由
                     _local_gate 自动探测（资料中文占比<0.5）并走"语义等价性"比对，
                     不再需要翻译答案，故此处仅作日志/兼容占位。
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
            f"cross_lang={local.get('cross_lang', False)} | "
            f"unsupported={local['unsupported'][:5]}"
        )

        # ---- 校验2：LLM 独立二次核查 ----
        # fail-closed：LLM 校验被尝试但失败（异常/超时/空输出）→ 判不通过，触发严格重生成兜底，
        # 避免"防幻觉安全网因校验自身失败而形同虚设直接放行"（BC-08）。
        # 仅当 LLM_CHECK_ENABLE=false / degraded 时跳过校验，退回纯本地门控（保持降级语义）。
        # 原文输入限长 _FACT_CHECK_SRC_CAP：contexts 按相关度排序取头部，防 xhigh reasoning 爆预算。
        llm_pass, llm_checked = True, False
        llm_unavailable = False
        check_src = sources_text[:_FACT_CHECK_SRC_CAP]
        # 本地门控通过 → 跳过 LLM 二次核查。
        # 原因：本地已覆盖数字/专名/术语依据（跨语言模式另做专名+数字护栏），
        # 极长答案 + xhigh reasoning 下 LLM 核查的 YES/NO 极不稳定（E2E：40s 空输出重试、
        # 常判 FAIL 触发 ~92s 严格重生成、重生成几乎总是兜底回同一原文——成本 ~190s 无增益）。
        # 本地门控未通过时仍保留 LLM 核查 + fail-closed：防幻觉安全网不能因本地误判直接放行。
        if settings.LLM_CHECK_ENABLE and not degraded and sources_text.strip() and not local_pass:
            if local.get("cross_lang"):
                # 跨语言模式：不翻译，直接用"语义等价性"比对（德/法/英原文 vs 中文转述）。
                try:
                    ok = await asyncio.to_thread(self.llm.check_fact_cross_lang, check_src, answer)
                    llm_pass = ok
                    llm_checked = True
                except Exception as e:
                    llm_unavailable = True
                    logger.error(
                        f"[{req_tag}] LLM 跨语言事实校验失败，fail-closed 判不通过（触发严格重生成） | error={e}",
                        exc_info=True,
                    )
            else:
                try:
                    ok = await asyncio.to_thread(self.llm.check_fact, check_src, answer)
                    llm_pass = ok
                    llm_checked = True
                except Exception as e:
                    llm_unavailable = True
                    logger.error(
                        f"[{req_tag}] LLM 事实校验失败，fail-closed 判不通过（触发严格重生成） | error={e}",
                        exc_info=True,
                    )
        if llm_unavailable:
            llm_pass = False

        passed = local_pass and llm_pass
        reasons = []
        if not local_pass:
            reasons.append(f"本地覆盖率偏低({local['coverage']})，存在无依据词元: {local['unsupported'][:5]}")
        if llm_checked and not llm_pass:
            reasons.append("LLM 二次核查判定回答与原文不符")
        if llm_unavailable:
            reasons.append("LLM 二次核查不可用（fail-closed）")
        reason = "；".join(reasons) or "通过"
        if local.get("cross_lang"):
            reason = f"[跨语言语义比对] {reason}"

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"[{req_tag}] 事实校验完成 | passed={passed} | local={local_pass} | llm={llm_pass}(checked={llm_checked},unavail={llm_unavailable}) | "
            f"elapsed={elapsed}s | reason={reason[:120]}"
        )
        return {
            "passed": passed,
            "reason": reason,
            "local_pass": local_pass,
            "llm_pass": llm_pass,
            "coverage": local["coverage"],
            "llm_checked": llm_checked,
            "llm_unavailable": llm_unavailable,
        }