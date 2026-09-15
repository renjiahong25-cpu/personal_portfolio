# -*- coding: utf-8 -*-
"""检索充分性门控：LLM 判定当前检索资料能否完整回答问题，不足时给出定向补查短语。

设计约束（服务端强制 xhigh，reasoning_effort 不可调）：
- 判定任务必须短小：输入=问题+资料标题/摘要 digest（~1-2k tokens），输出=短 JSON；
- 预算 1024（短输出任务在 xhigh 下实测可完成）；
- 判定失败 fail-open（视为充分），不阻塞主流程。
"""
import asyncio
import json
import re
import time

from config.logging_config import get_logger
from core.llm_client import llm_client

logger = get_logger("retrieval_gate")

_SYSTEM = (
    "你是跨境合规检索质量评审员。判断【检索资料】是否足以完整回答【用户问题】。\n"
    "充分标准：资料覆盖问题所问的关键步骤/条件/数值，且没有明显缺失的关键环节"
    "（例如问流程却缺申报后环节、问条件却缺主体要求）。\n"
    "只输出一个 JSON 对象：{\"sufficient\": true或false, \"missing\": [缺失点的定向检索短语]}\n"
    "约束：\n"
    "1. sufficient=true 时 missing 必须为空数组；\n"
    "2. missing 最多 3 条，每条是可直接用于检索的短查询短语（15 词以内），"
    "必须使用目标国官方语言（目标国为德国时必须用德语，例如 "
    "'Ausfuhranmeldung Uberlassung Ausstellung ABD'、'Zollanmeldung Abgabe EDV Schritt'），"
    "禁止使用中文或英文；\n"
    "3. 不要输出 JSON 以外的任何内容。"
)

_JUDGE_BUDGET = 2048  # xhigh 下 1024 偶被 reasoning 吃光（实测 iter2 空输出），放宽
_SNIPPET_LEN = 100
_MAX_DIGEST_ITEMS = 20


def _pick_json(raw: str):
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _digest(items) -> str:
    lines = []
    for i, it in enumerate(items[:_MAX_DIGEST_ITEMS], 1):
        body = (it.content_raw or it.content_summary or "").replace("\n", " ")[:_SNIPPET_LEN]
        lines.append(f"[{i}] {it.chapter_path or it.chapter_title or ''} {body}")
    return "\n".join(lines)


class RetrievalGate:
    """检索充分性门控（迭代检索核心）"""

    async def judge(self, question: str, items, entities: dict, request_id: str) -> dict:
        """返回 {"sufficient": bool, "missing": [str]}；失败 fail-open 视为充分"""
        if not items:
            return {"sufficient": False, "missing": [question[:80]]}
        lang = (entities or {}).get("country") or "目标国"
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": (
                f"用户问题：{question}\n"
                f"目标国：{lang}\n"
                f"【检索资料】（章节路径 + 节选，共 {len(items)} 条）：\n"
                f"{_digest(items)}"
            )},
        ]
        t0 = time.time()
        try:
            raw = await asyncio.to_thread(llm_client.chat, messages, 0.0, _JUDGE_BUDGET, "medium", no_think=True)
            parsed = _pick_json(raw)
            if not parsed or "sufficient" not in parsed:
                logger.warning(f"[{request_id}] 充分性判定 JSON 解析失败，fail-open | raw_head={(raw or '')[:100]}")
                return {"sufficient": True, "missing": []}
            missing = [str(m).strip() for m in (parsed.get("missing") or []) if str(m).strip()][:3]
            sufficient = bool(parsed["sufficient"])
            result = {"sufficient": sufficient, "missing": [] if sufficient else missing}
            logger.info(
                f"[{request_id}] 充分性判定 | sufficient={sufficient} | "
                f"missing={result['missing']} | elapsed={round(time.time()-t0,1)}s"
            )
            return result
        except Exception as e:
            logger.warning(f"[{request_id}] 充分性判定异常，fail-open | error={e}")
            return {"sufficient": True, "missing": []}
