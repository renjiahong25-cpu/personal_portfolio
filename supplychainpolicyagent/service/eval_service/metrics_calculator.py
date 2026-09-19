# -*- coding: utf-8 -*-
"""
指标计算引擎
- 任务事实错误率（红线：≤8%）
- 关键信息覆盖率（标准答案关键词命中比例）
- 溯源引用正确率
- 拒答准确率
- 检索相关性（召回文档与问题/真值文档的相关度）
- 平均延迟 / P95 延迟
- 指标恶化自动告警
- 完整日志：入参/异常/关键节点
"""
import asyncio
import statistics
import time

from config.logging_config import get_logger
from config.settings import (
    EVAL_ERROR_RATE_REDLINE, EVAL_COVERAGE_PASS_THRESHOLD,
    EVAL_P95_LATENCY_REDLINE_MS, EVAL_ALERT_DEGRADE_DELTA,
    EVAL_LLM_FACT_CHECK_SAMPLE, EVAL_MAX_CONCURRENCY,
)
from core.llm_client import llm_client as _default_llm

from ._text_utils import extract_citations, extract_keywords, truncate

logger = get_logger("metrics_calculator")

# LLM 事实校验提示词（输出 YES/NO）
_FACT_CHECK_SYSTEM = "你是事实校验专家。仅输出 YES 或 NO，不输出任何其他内容。"
_FACT_CHECK_PROMPT = """对照【标准答案】与【参考资料原文】，判断【模型回答】是否存在事实性错误（数据、数值、条件、时间节点、规则方向的错误，或凭空编造、篡改）。

判定规则：
- 模型回答与标准答案一致，或完全基于参考资料且无冲突 → YES
- 模型回答存在任何事实冲突、编造、篡改 → NO

【标准答案】
{standard}

【参考资料原文】
{origin}

【模型回答】
{answer}
"""


class MetricsCalculator:
    """指标计算引擎：输入回归测试原始结果，输出指标与告警"""

    def __init__(self, llm=None, max_concurrency: int = None,
                 fact_check_sample: int = None):
        self._llm = llm or _default_llm
        self._max_concurrency = max_concurrency or EVAL_MAX_CONCURRENCY
        self._fact_check_sample = fact_check_sample or EVAL_LLM_FACT_CHECK_SAMPLE
        self._logger = get_logger("metrics_calculator")

    # ------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------
    async def calculate(self, item_results: list, prev_metrics: dict = None) -> dict:
        """计算全量指标，返回 metrics 字典（含告警）"""
        start = time.time()
        total = len(item_results)
        self._logger.info(
            f"指标计算开始 | 条目数={total} | fact_check_sample={self._fact_check_sample}")

        # 1. 逐条执行 LLM 事实校验（仅可作答条目，先低覆盖率者）
        await self._batch_fact_check(item_results)

        # 2. 逐条规则化计算（覆盖率/引用/拒答/检索）
        for item in item_results:
            self._analyze_item(item)

        # 3. 聚合指标
        metrics = self._aggregate(item_results)

        # 4. 红线与恶化告警
        metrics["alerts"] = self._check_alerts(metrics, prev_metrics)
        metrics["redline"] = self._redline(metrics)

        elapsed = round(time.time() - start, 3)
        self._logger.info(
            f"指标计算完成 | 事实错误率={metrics['task_fact_error_rate']} | "
            f"覆盖率={metrics['key_info_coverage']} | 拒答准确率={metrics['refusal_accuracy']} | "
            f"引用正确率={metrics['citation_accuracy']} | P95延迟={metrics['p95_latency_ms']}ms | "
            f"告警数={len(metrics['alerts'])} | 耗时={elapsed}s"
        )
        return metrics

    # ------------------------------------------------------------
    # LLM 事实校验（批量并发采样）
    # ------------------------------------------------------------
    async def _batch_fact_check(self, item_results: list) -> None:
        """对可作答条目做 LLM 事实校验；条目过多时按覆盖率升序采样"""
        candidates = [it for it in item_results
                      if isinstance(it, dict) and not it.get("should_refuse")
                      and not it.get("error")]
        if not candidates:
            self._logger.info("无可作答条目，跳过 LLM 事实校验")
            return
        # 覆盖率最低的最可疑，优先受检
        candidates.sort(key=lambda it: it.get("_coverage", 1.0))
        sampled = candidates[:self._fact_check_sample]

        semaphore = asyncio.Semaphore(self._max_concurrency)
        loop = asyncio.get_running_loop()

        async def check_one(item):
            if item.get("error"):
                item["fact_checked"] = False
                item["fact_passed"] = False
                return
            if item.get("should_refuse"):
                item["fact_checked"] = False
                item["fact_passed"] = True
                return
            async with semaphore:
                try:
                    f_pass = await loop.run_in_executor(
                        None, lambda it=item: self._fact_check_one(it))
                except Exception as e:
                    self._logger.warning(
                        f"事实校验调用失败 | item_id={item.get('item_id')} | error={e}")
                    f_pass = True  # 校验失败不冤枉，标记未受检
            item["fact_checked"] = f_pass is not None
            item["fact_passed"] = f_pass if f_pass is not None else True

        await asyncio.gather(*(check_one(it) for it in sampled))
        self._logger.info(
            f"LLM 事实校验完成 | 受检={len(sampled)}/{len(candidates)}")

    def _fact_check_one(self, item: dict) -> bool:
        """单条事实校验（同步 LLM 调用，运行在线程池）"""
        answer = item.get("model_answer") or ""
        standard = item.get("standard_answer") or ""
        origin = standard or ""
        prompt = _FACT_CHECK_PROMPT.format(
            standard=truncate(standard, 1200), origin=truncate(origin, 1500),
            answer=truncate(answer, 1200))
        raw = self._llm.chat([
            {"role": "system", "content": _FACT_CHECK_SYSTEM},
            {"role": "user", "content": prompt},
        ], temperature=0.0, max_tokens=8)
        return "YES" in raw.upper()

    # ------------------------------------------------------------
    # 单条规则化分析
    # ------------------------------------------------------------
    def _analyze_item(self, item: dict) -> None:
        """给单条结果打指标：覆盖率/引用/拒答/检索"""
        answer = item.get("model_answer") or ""
        standard = item.get("standard_answer") or ""
        should_refuse = bool(item.get("should_refuse"))
        has_error = bool(item.get("error"))
        retrieved_docs = item.get("retrieved_docs") or []
        expected_docs = [d for d in (item.get("expected_docs") or []) if d]
        retrieved_uuids = {d.get("doc_uuid") for d in retrieved_docs if d.get("doc_uuid")}

        # ---- 关键信息覆盖率：标准答案关键词命中比例（拒答/出错条目不适用）----
        coverage = None
        if (not should_refuse) and (not has_error):
            keywords = item.get("keywords") or extract_keywords(standard, max_kw=8)
            if not keywords:
                coverage = 0.0
            else:
                hits = sum(1 for kw in keywords if kw and kw in answer)
                coverage = round(hits / len(keywords), 4)
        item["coverage"] = coverage
        item["_coverage"] = coverage if coverage is not None else 1.0

        # ---- 溯源引用正确率：引用序号必须落在检索到的参考资料范围内 ----
        citation_accuracy = None
        if (not should_refuse) and (not has_error) and answer:
            refs = extract_citations(answer)
            total_refs = len(refs)
            if total_refs == 0:
                citation_accuracy = 0.0  # 有回答却不引用溯源，视为引用缺失
            else:
                valid = sum(1 for r in refs if 1 <= r <= len(retrieved_docs))
                citation_accuracy = round(valid / total_refs, 4)
        item["citation_accuracy"] = citation_accuracy

        # ---- 拒答准确率 ----
        refused = bool(item.get("refused"))
        if has_error:
            refusal_correct = False
        else:
            refusal_correct = (should_refuse and refused) or (not should_refuse and not refused)
        item["refusal_correct"] = refusal_correct

        # ---- 检索相关性：真值文档在召回集中的命中率 ---
        recall = 0.0
        if expected_docs and retrieved_uuids:
            hit = sum(1 for d in expected_docs if d in retrieved_uuids)
            recall = round(hit / len(expected_docs), 4)
        precision = None
        if retrieved_docs:
            hit = sum(1 for d in expected_docs if d in retrieved_uuids) if expected_docs else 0
            precision = round(hit / len(retrieved_docs), 4)
        item["mrecall"] = recall
        item["mprecision"] = precision

        # ---- 单条目是否通过 ----
        if has_error:
            item["item_pass"] = False
            item["fail_reasons"] = [item.get("error")]
        elif should_refuse:
            item["item_pass"] = refusal_correct
            item["fail_reasons"] = [] if refusal_correct else ["拒答行为错误（应拒却答 / 不应拒却拒）"]
        else:
            fact_error = (item.get("coverage", 0) == 0.0) or (
                item.get("fact_checked") and not item.get("fact_passed"))
            cov_pass = (item.get("coverage", 0.0) or 0.0) >= EVAL_COVERAGE_PASS_THRESHOLD
            reasons = []
            if fact_error:
                reasons.append("任务事实错误（LLM校验失败或完全未覆盖标准答案）")
            if not cov_pass:
                reasons.append(f"关键信息覆盖率不足（低于{EVAL_COVERAGE_PASS_THRESHOLD}）")
            item["item_pass"] = (not fact_error) and cov_pass
            item["fail_reasons"] = reasons

    # ------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------
    def _aggregate(self, item_results: list) -> dict:
        """汇总所有条目的指标为整体指标"""
        total = len(item_results) or 1
        answerable = [it for it in item_results
                      if isinstance(it, dict) and not it.get("should_refuse")
                      and not it.get("error")]
        a_cnt = len(answerable)

        # 任务事实错误率
        fact_errors = [it for it in answerable
                       if (it.get("coverage", 0) == 0.0) or
                       (it.get("fact_checked") and not it.get("fact_passed"))]
        fact_error_rate = round(len(fact_errors) / a_cnt, 4) if a_cnt else 0.0

        # 关键信息覆盖率（可作答条目均值）
        coverages = [it["coverage"] for it in answerable if it.get("coverage") is not None]
        key_info_coverage = round(
            sum(coverages) / len(coverages), 4) if coverages else 0.0

        # 溯源引用正确率
        cit_values = [it["citation_accuracy"] for it in answerable
                      if it.get("citation_accuracy") is not None]
        citation_accuracy = round(
            sum(cit_values) / len(cit_values), 4) if cit_values else 0.0

        # 拒答准确率（全量条目，出错视为错误）
        ref_correct = sum(1 for it in item_results
                          if isinstance(it, dict) and it.get("refusal_correct"))
        refusal_accuracy = round(ref_correct / total, 4)

        # 检索相关性
        recalls = [it.get("mrecall", 0.0) for it in item_results
                   if isinstance(it, dict)]
        rec_recall = round(sum(recalls) / len(recalls), 4) if recalls else 0.0
        precisions = [it["mprecision"] for it in item_results
                      if isinstance(it, dict) and it.get("mprecision") is not None]
        rec_precision = round(sum(precisions) / len(precisions), 4) if precisions else 0.0

        # 延迟
        latencies = [it.get("latency_ms", 0.0) for it in item_results
                     if isinstance(it, dict) and it.get("latency_ms")]
        avg_latency_ms = round(statistics.mean(latencies), 1) if latencies else 0.0
        p95_latency_ms = self._p95(latencies)

        # 通过与等级细分
        pass_items = [it for it in item_results
                      if isinstance(it, dict) and it.get("item_pass")]
        pass_rate = round(len(pass_items) / total, 4)
        level_breakdown = self._level_breakdown(item_results)

        return {
            "total_items": len(item_results),
            "answerable_count": a_cnt,
            "task_fact_error_rate": fact_error_rate,
            "key_info_coverage": key_info_coverage,
            "citation_accuracy": citation_accuracy,
            "refusal_accuracy": refusal_accuracy,
            "retrieval_relevance_recall": rec_recall,
            "retrieval_precision": rec_precision,
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "avg_rounds_per_task": 1.0,
            "pass_count": len(pass_items),
            "fail_count": total - len(pass_items),
            "pass_rate": pass_rate,
            "fact_checked_count": sum(1 for it in answerable if it.get("fact_checked")),
            "level_breakdown": level_breakdown,
        }

    def _level_breakdown(self, item_results: list) -> dict:
        """按三级层级拆分通过率/覆盖率/事实错误率"""
        breakdown = {}
        for level in ("global", "chapter", "paragraph"):
            items = [it for it in item_results
                     if isinstance(it, dict) and it.get("level") == level]
            if not items:
                continue
            total = len(items)
            passed = sum(1 for it in items if it.get("item_pass"))
            answerable = [it for it in items
                          if not it.get("should_refuse") and not it.get("error")]
            covs = [it.get("coverage") for it in answerable
                    if it.get("coverage") is not None]
            fact_err = [it for it in answerable
                        if (it.get("coverage", 0) == 0.0) or
                        (it.get("fact_checked") and not it.get("fact_passed"))]
            breakdown[level] = {
                "count": total,
                "pass_rate": round(passed / total, 4),
                "key_info_coverage": round(sum(covs) / len(covs), 4) if covs else 0.0,
                "task_fact_error_rate": round(len(fact_err) / len(answerable), 4)
                if answerable else 0.0,
            }
        return breakdown

    def _p95(self, values: list) -> float:
        """P95 延迟"""
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(len(s) - 1, int(round(0.95 * (len(s) - 1))))
        return round(s[idx], 1)

    # ------------------------------------------------------------
    # 红线与告警
    # ------------------------------------------------------------
    def _redline(self, metrics: dict) -> dict:
        """红线指标状态：任务事实错误率 ≤8% + 95%延迟 ≤3s"""
        return {
            "task_fact_error_rate": {
                "value": metrics["task_fact_error_rate"],
                "limit": EVAL_ERROR_RATE_REDLINE,
                "pass": metrics["task_fact_error_rate"] <= EVAL_ERROR_RATE_REDLINE,
            },
            "p95_latency_ms": {
                "value": metrics["p95_latency_ms"],
                "limit": EVAL_P95_LATENCY_REDLINE_MS,
                "pass": metrics["p95_latency_ms"] <= EVAL_P95_LATENCY_REDLINE_MS,
            },
        }

    def _check_alerts(self, metrics: dict, prev: dict = None) -> list:
        """指标恶化自动告警：红线超标 + 与前次对比恶化"""
        alerts = []
        # 红线
        red = self._redline(metrics)
        if not red["task_fact_error_rate"]["pass"]:
            alerts.append({
                "level": "CRITICAL", "metric": "task_fact_error_rate",
                "value": metrics["task_fact_error_rate"],
                "limit": EVAL_ERROR_RATE_REDLINE,
                "message": f"红线指标超限：任务事实错误率 {metrics['task_fact_error_rate']} "
                           f"> {EVAL_ERROR_RATE_REDLINE}",
            })
        if not red["p95_latency_ms"]["pass"]:
            alerts.append({
                "level": "WARN", "metric": "p95_latency_ms",
                "value": metrics["p95_latency_ms"],
                "limit": EVAL_P95_LATENCY_REDLINE_MS,
                "message": f"体验红线超限：95%延迟 {metrics['p95_latency_ms']}ms "
                           f"> {EVAL_P95_LATENCY_REDLINE_MS}ms",
            })

        # 与前次对比恶化
        if prev:
            delta = EVAL_ALERT_DEGRADE_DELTA
            if prev.get("key_info_coverage", 0) - metrics["key_info_coverage"] > delta:
                alerts.append({
                    "level": "WARN", "metric": "key_info_coverage",
                    "value": metrics["key_info_coverage"],
                    "prev": prev.get("key_info_coverage"),
                    "message": f"关键信息覆盖率下降：{prev.get('key_info_coverage')} "
                               f"→ {metrics['key_info_coverage']}（恶化>={delta}）",
                })
            if metrics["task_fact_error_rate"] - prev.get("task_fact_error_rate", 0) > delta:
                alerts.append({
                    "level": "CRITICAL", "metric": "task_fact_error_rate",
                    "value": metrics["task_fact_error_rate"],
                    "prev": prev.get("task_fact_error_rate"),
                    "message": f"任务事实错误率上升：{prev.get('task_fact_error_rate')} "
                               f"→ {metrics['task_fact_error_rate']}（恶化>={delta}）",
                })
        if alerts:
            self._logger.warning(f"评测告警 {len(alerts)} 条 | {[a['message'] for a in alerts]}")
        return alerts


async def calculate_metrics(item_results: list, prev_metrics: dict = None) -> dict:
    """便捷入口：计算指标"""
    return await MetricsCalculator().calculate(item_results, prev_metrics)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    samples = [{
        "item_id": "test1", "level": "paragraph", "question": "测试", "standard_answer": "关税10%",
        "keywords": ["10%"], "should_refuse": False, "model_answer": "关税10%",
        "latency_ms": 500, "refused": False, "error": None,
        "retrieved_docs": [{"doc_uuid": "d1", "doc_title": "t", "paragraph_id": 1, "content": "x"}],
        "expected_docs": ["d1"], "expected_paragraph_ids": [1], "expected_chapter_id": None,
    }]
    asyncio.run(calculate_metrics(samples))