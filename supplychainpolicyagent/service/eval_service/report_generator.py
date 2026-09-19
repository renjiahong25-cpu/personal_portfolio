# -*- coding: utf-8 -*-
"""
评测报告生成器
- 单次评测报告（JSON 格式）
- 版本迭代对比报告
- 指标趋势分析
- 完整日志：入参/异常/关键节点
"""
import json
import time

from config.logging_config import get_logger
from config.settings import EVAL_REPORT_DIR
from db.models.base import SessionLocal, EvalResult

from ._text_utils import truncate

logger = get_logger("report_generator")


class ReportGenerator:
    """评测报告组装与持久化"""

    def __init__(self):
        self._logger = get_logger("report_generator")

    # ------------------------------------------------------------
    # 单次评测报告
    # ------------------------------------------------------------
    def build_single_report(self, run_meta: dict, metrics: dict,
                            item_results: list = None) -> dict:
        """组装单次评测报告（含红线状态、等级拆分、失败条目摘要）"""
        start = time.time()
        fail_items = []
        for it in item_results or []:
            if isinstance(it, dict) and not it.get("item_pass", True):
                fail_items.append({
                    "item_id": it.get("item_id"),
                    "level": it.get("level"),
                    "question": truncate(it.get("question", ""), 200),
                    "should_refuse": it.get("should_refuse"),
                    "refused": it.get("refused"),
                    "model_answer": truncate(it.get("model_answer", ""), 300),
                    "coverage": it.get("coverage"),
                    "reason": "；".join(it.get("fail_reasons") or []) or it.get("error"),
                    "latency_ms": it.get("latency_ms"),
                })

        report = {
            "report_id": f"report_{run_meta.get('run_id', '')}",
            "run_id": run_meta.get("run_id"),
            "version": run_meta.get("version"),
            "model": run_meta.get("model"),
            "eval_set_id": run_meta.get("eval_set_id"),
            "eval_set_description": run_meta.get("eval_set_description"),
            "start_time": run_meta.get("start_time"),
            "concurrency": run_meta.get("concurrency"),
            "total_items": metrics.get("total_items", 0),
            "pass_count": metrics.get("pass_count", 0),
            "fail_count": metrics.get("fail_count", 0),
            "pass_rate": metrics.get("pass_rate", 0.0),
            "metrics": {
                "task_fact_error_rate": metrics.get("task_fact_error_rate", 0.0),
                "key_info_coverage": metrics.get("key_info_coverage", 0.0),
                "citation_accuracy": metrics.get("citation_accuracy", 0.0),
                "refusal_accuracy": metrics.get("refusal_accuracy", 0.0),
                "retrieval_relevance_recall": metrics.get("retrieval_relevance_recall", 0.0),
                "retrieval_precision": metrics.get("retrieval_precision", 0.0),
                "avg_latency_ms": metrics.get("avg_latency_ms", 0.0),
                "p95_latency_ms": metrics.get("p95_latency_ms", 0.0),
                "avg_rounds_per_task": metrics.get("avg_rounds_per_task", 1.0),
            },
            "redline": metrics.get("redline", {}),
            "alerts": metrics.get("alerts", []),
            "level_breakdown": metrics.get("level_breakdown", {}),
            "fact_checked_count": metrics.get("fact_checked_count", 0),
            "fail_item_count": len(fail_items),
            "fail_items": fail_items,
            "full_items": item_results,
            "raw_path": run_meta.get("raw_path"),
        }
        self._logger.info(
            f"单次报告组装完成 | report_id={report['report_id']} | "
            f"条目={report['total_items']} | 通过率={report['pass_rate']} | "
            f"失败条目={len(fail_items)} | 耗时={round(time.time() - start, 3)}s")
        return report

    def build_compare_report(self, old_report: dict, new_report: dict) -> dict:
        """版本迭代对比报告：对比两版本指标并给出变化结论"""
        start = time.time()
        m_new = new_report.get("metrics", {})
        m_old = old_report.get("metrics", {})
        keys = [
            "task_fact_error_rate", "key_info_coverage", "citation_accuracy",
            "refusal_accuracy", "retrieval_relevance_recall", "retrieval_precision",
            "avg_latency_ms", "p95_latency_ms", "pass_rate",
        ]

        def good_when_up(k: str) -> bool:
            """指标是否越大越好"""
            return k != "task_fact_error_rate" and "latency" not in k and k != "pass_rate" \
                or k == "pass_rate"

        deltas = []
        for k in keys:
            old_v = m_old.get(k, old_report.get(k)) if k == "pass_rate" else m_old.get(k)
            new_v = m_new.get(k, new_report.get(k)) if k == "pass_rate" else m_new.get(k)
            if old_v is None or new_v is None:
                continue
            diff = round(new_v - old_v, 4)
            if k == "pass_rate":
                old_v = old_report.get("pass_rate")
                new_v = new_report.get("pass_rate")
                diff = round(new_v - old_v, 4) if old_v is not None else None
            if diff is None:
                continue
            # 延迟类指标数值越大越差，其余越大越好
            bigger_better = not ("latency" in k or k in ("task_fact_error_rate",))
            trend = "improved" if (bigger_better and diff > 0) or \
                (not bigger_better and diff < 0) else ("degraded" if diff else "flat")
            deltas.append({
                "metric": k,
                "old": old_v,
                "new": new_v,
                "delta": diff,
                "trend": trend,
            })

        improved = [d for d in deltas if d["trend"] == "improved"]
        degraded = [d for d in deltas if d["trend"] == "degraded"]
        report = {
            "report_id": f"compare_{old_report.get('version')}_{new_report.get('version')}"
                         f"_{int(time.time())}",
            "type": "compare",
            "old_run": {"run_id": old_report.get("run_id"),
                        "version": old_report.get("version"),
                        "pass_rate": old_report.get("pass_rate")},
            "new_run": {"run_id": new_report.get("run_id"),
                        "version": new_report.get("version"),
                        "pass_rate": new_report.get("pass_rate")},
            "delta_count": len(deltas),
            "improved_count": len(improved),
            "degraded_count": len(degraded),
            "deltas": deltas,
            "conclusion": ("整体恶化，建议回滚或修复"
                           if len(degraded) > len(improved) else "整体平稳或向好"),
            "old_alerts": old_report.get("alerts", []),
            "new_alerts": new_report.get("alerts", []),
        }
        self._logger.info(
            f"版本对比报告组装完成 | {report['old_run']['version']} vs "
            f"{report['new_run']['version']} | 恶化指标={len(degraded)} | "
            f"耗时={round(time.time() - start, 3)}s")
        return report

    def build_trend_analysis(self, rows: list) -> dict:
        """指标趋势分析：对最近 N 次评测结果做时序分析"""
        start = time.time()
        if not rows:
            return {"count": 0, "message": "暂无评测历史数据"}
        rows = sorted(rows, key=lambda r: r["ts"])
        series = []
        for r in rows:
            detail = {}
            try:
                detail = json.loads(r.get("report_detail") or "{}")
                metrics = detail.get("metrics", {})
            except Exception:
                metrics = {}
            series.append({
                "run_id": detail.get("run_id") or f"row_{r['id']}",
                "version": r.get("version"),
                "ts": r.get("ts"),
                "total_count": r.get("total_count"),
                "pass_rate": r.get("pass_rate"),
                "task_fact_error_rate": metrics.get("task_fact_error_rate",
                                                     r.get("error_rate")),
                "key_info_coverage": metrics.get("key_info_coverage"),
                "refusal_accuracy": metrics.get("refusal_accuracy"),
                "citation_accuracy": metrics.get("citation_accuracy"),
                "avg_latency_ms": metrics.get("avg_latency_ms", r.get("avg_latency_ms")),
                "p95_latency_ms": metrics.get("p95_latency_ms"),
            })

        first = series[0]
        last = series[-1]
        analysis = {
            "count": len(series),
            "series": series,
            "summary": {
                "pass_rate": {"first": first["pass_rate"], "last": last["pass_rate"],
                              "delta": round((last["pass_rate"] or 0) - (first["pass_rate"] or 0), 4),
                              "best": max((s["pass_rate"] or 0) for s in series),
                              "worst": min((s["pass_rate"] or 0) for s in series)},
                "task_fact_error_rate": {
                    "first": first["task_fact_error_rate"],
                    "last": last["task_fact_error_rate"],
                    "delta": round((last["task_fact_error_rate"] or 0)
                                   - (first["task_fact_error_rate"] or 0), 4),
                    "avg": round(sum(s["task_fact_error_rate"] or 0 for s in series)
                                 / len(series), 4)},
            },
        }
        self._logger.info(
            f"趋势分析完成 | 样本数={len(series)} | 通过率 {first['pass_rate']}→"
            f"{last['pass_rate']} | 耗时={round(time.time() - start, 3)}s")
        return analysis

    # ------------------------------------------------------------
    # 持久化 / 读取
    # ------------------------------------------------------------
    def save_report(self, report: dict) -> str:
        """评测报告 JSON 落盘"""
        EVAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_REPORT_DIR / f"{report['report_id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self._logger.info(f"评测报告已落盘 | path={path}")
        return str(path)

    def load_report(self, report_id: str) -> dict:
        """按 report_id 读取评测报告"""
        path = EVAL_REPORT_DIR / f"{report_id}.json"
        if not path.exists():
            self._logger.warning(f"评测报告不存在 | report_id={report_id}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
        self._logger.info(f"评测报告读取成功 | report_id={report_id}")
        return report

    def load_latest_metrics(self) -> dict:
        """读取最近一次评测的指标（用于指标对比/展示）"""
        try:
            with SessionLocal() as db:
                row = db.query(EvalResult).order_by(EvalResult.id.desc()).first()
            if not row:
                return {}
            detail = json.loads(row.report_detail or "{}")
            return {"id": row.id, "version": row.version, "metrics": detail.get("metrics", {}),
                    "redline": detail.get("redline", {}), "alerts": detail.get("alerts", []),
                    "pass_rate": row.pass_rate, "total_count": row.total_count,
                    "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S")
                    if row.create_time else ""}
        except Exception as e:
            self._logger.error(f"最近评测指标读取失败 | error={e}", exc_info=True)
            return {}


def build_single_report(run_meta: dict, metrics: dict, item_results: list = None) -> dict:
    """便捷入口：单次评测报告"""
    return ReportGenerator().build_single_report(run_meta, metrics, item_results)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    gen = ReportGenerator()
    print(gen.load_latest_metrics())