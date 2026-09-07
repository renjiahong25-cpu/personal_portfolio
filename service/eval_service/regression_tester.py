# -*- coding: utf-8 -*-
"""
回归测试器（版本迭代自动回归）
- 加载评测集 → 逐条问答（检索 + LLM 作答）→ 对比标准答案
- 支持批量测试（多评测集）、并发控制（asyncio.Semaphore）
- 结果持久化到数据库（eval_result 表）
- 自动触发 BadCase 入库、评测报告生成
- 完整日志：入参/异常/关键节点
"""
import asyncio
import json
import time
import uuid

from config.logging_config import get_logger
from config.settings import (
    EVAL_SET_DIR, EVAL_RAW_ITEM_DIR, EVAL_MAX_CONCURRENCY, RETRIEVE_TOP_K,
    EVAL_LEVEL_RATIO,
)
from core.llm_client import llm_client as _default_llm
from db.models.base import SessionLocal, EvalResult, DocParagraph, DocMain, DocChapter

from ._text_utils import extract_keywords, is_refusal, truncate
from .metrics_calculator import MetricsCalculator
from .badcase_manager import BadCaseManager
from .report_generator import ReportGenerator

logger = get_logger("regression_tester")

# 系统 Prompt：约束回答仅基于参考资料并输出引用
_ANSWER_SYSTEM_PROMPT = (
    "你是跨境物流清关规则助手，专注中国出口德国的通关规则。回答要求：\n"
    "1. 只能依据【参考资料】作答，禁止编造、篡改规则、数值或条件；\n"
    "2. 回答的每个要点都要标注引用，引用格式为\"【来源N】\"，N 对应参考资料序号；\n"
    "3. 若参考资料不足以回答问题，明确说明当前知识库无该内容并建议咨询专业清关师；\n"
    "4. 若问题超出系统服务边界（如投资决策、商务谈判、法律诉讼意见），礼貌拒绝并说明原因。"
)

_ANSWER_USER_TMPL = (
    "【用户问题】\n{question}\n\n"
    "【参考资料】\n{contexts}\n"
    "{refuse_hint}"
)

_REFUSE_HINT = "\n【提示】该问题判定为系统应拒答范围，请礼貌说明无法提供此类服务并给出理由。"


def _new_run_id() -> str:
    return f"evalrun_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"


class RegressionTester:
    """版本迭代自动回归测试器"""

    def __init__(self, llm=None, max_concurrency: int = None):
        self._llm = llm or _default_llm
        self._max_concurrency = max_concurrency or EVAL_MAX_CONCURRENCY
        self._logger = get_logger("regression_tester")

    # ------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------
    async def run(self, eval_set_path: str = None, eval_set_id: str = None,
                  version: str = "v1.0", sample_count: int = None,
                  run_id: str = None, collect_badcase: bool = True,
                  background: bool = False) -> dict:
        """执行一次回归评测，返回评测报告（含指标与告警）"""
        start = time.time()
        run_id = run_id or _new_run_id()
        self._logger.info(
            f"回归评测开始 | run_id={run_id} | eval_set_id={eval_set_id} | "
            f"eval_set_path={eval_set_path} | version={version} | sample_count={sample_count} | "
            f"concurrency={self._max_concurrency}"
        )

        # 1. 加载评测集
        eval_set = self._load_eval_set(eval_set_path, eval_set_id)
        items = eval_set.get("items", [])
        if not items:
            self._logger.error(f"回归评测终止：评测集为空 | eval_set_id={eval_set_id}")
            raise ValueError(f"评测集为空: {eval_set_id or eval_set_path}")

        # 2. 采样（按三级配比抽样）
        items = self._sample_items(items, sample_count)
        self._logger.info(f"评测条目采样完成 | 参与条数={len(items)}")

        # 3. 并发逐条测试
        raw_results = await self._run_items(items, run_id)

        # 4. 原始结果落盘（raw 目录，便于复现/排查）
        raw_path = self._save_raw_results(raw_results, run_id)

        # 5. 指标计算（含恶化告警）
        metrics = await MetricsCalculator().calculate(raw_results)

        # 6. 报告生成
        report = ReportGenerator().build_single_report(
            run_meta={
                "run_id": run_id,
                "version": version,
                "eval_set_id": eval_set.get("eval_set_id"),
                "eval_set_description": eval_set.get("description"),
                "model": getattr(self._llm, "model", ""),
                "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
                "concurrency": self._max_concurrency,
                "raw_path": str(raw_path),
            },
            metrics=metrics,
            item_results=raw_results,
        )

        # 7. 持久化到数据库
        row_id = self._persist_result(report, version)

        # 8. BadCase 自动入库（回归失败案例）
        bad_case_count = 0
        if collect_badcase:
            bad_case_count = BadCaseManager().collect_from_regression(raw_results, run_id)

        elapsed = round(time.time() - start, 3)
        report["row_id"] = row_id
        report["bad_case_collected"] = bad_case_count
        report["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["elapsed_s"] = elapsed
        self._logger.info(
            f"回归评测完成 | run_id={run_id} | row_id={row_id} | 通过率={report['pass_rate']} | "
            f"事实错误率={report['metrics']['task_fact_error_rate']} | "
            f"BadCase入库={bad_case_count} | 总耗时={elapsed}s"
        )
        return report

    async def run_batch(self, eval_set_ids: list, version: str = "v1.0",
                        sample_count: int = None) -> list:
        """批量回归：依次运行多个评测集"""
        start = time.time()
        self._logger.info(
            f"批量回归评测开始 | 评测集数量={len(eval_set_ids)} | version={version} | "
            f"sample_count={sample_count}"
        )
        reports = []
        for idx, eval_set_id in enumerate(eval_set_ids, start=1):
            try:
                self._logger.info(f"批量回归进度 | {idx}/{len(eval_set_ids)} | eval_set_id={eval_set_id}")
                report = await self.run(eval_set_id=eval_set_id, version=version,
                                        sample_count=sample_count)
                reports.append(report)
            except Exception as e:
                self._logger.error(
                    f"批量回归单集失败 | eval_set_id={eval_set_id} | error={e}", exc_info=True)
        elapsed = round(time.time() - start, 3)
        self._logger.info(
            f"批量回归评测完成 | 成功={len(reports)}/{len(eval_set_ids)} | 总耗时={elapsed}s")
        return reports

    # ------------------------------------------------------------
    # 评测集加载与采样
    # ------------------------------------------------------------
    def _load_eval_set(self, eval_set_path: str = None, eval_set_id: str = None) -> dict:
        """加载评测集：优先 path，其次按 id 在 sets 目录中查找，最后取最新"""
        start = time.time()
        if eval_set_path:
            path = eval_set_path
        elif eval_set_id:
            path = EVAL_SET_DIR / f"{eval_set_id}.json"
        else:
            candidates = sorted(EVAL_SET_DIR.glob("*.json"), reverse=True)
            if not candidates:
                self._logger.error("回归评测终止：未指定评测集且 sets 目录为空")
                raise ValueError("未指定评测集且评测集目录为空，请先运行评测集生成")
            path = candidates[0]
        with open(path, "r", encoding="utf-8") as f:
            eval_set = json.load(f)
        self._logger.info(
            f"评测集加载完成 | eval_set_id={eval_set.get('eval_set_id')} | 条目数="
            f"{len(eval_set.get('items', []))} | path={path} | "
            f"耗时={round(time.time() - start, 3)}s"
        )
        return eval_set

    def _sample_items(self, items: list, sample_count: int = None) -> list:
        """按三级配比采样：sample_count 为空返回全部"""
        import random
        total = len(items)
        if sample_count is None or sample_count >= total:
            return items
        by_level = {lv: [it for it in items if it.get("level") == lv]
                    for lv in ("global", "chapter", "paragraph")}
        sampled = []
        used = 0
        for lv, ratio in EVAL_LEVEL_RATIO.items():
            pool = by_level.get(lv, [])
            take = min(int(sample_count * ratio), len(pool))
            if take > 0:
                sampled.extend(random.sample(pool, take))
                used += take
        # 剩余配额填充到未选中的条目
        if used < sample_count:
            rest = [it for it in items if it not in sampled]
            random.shuffle(rest)
            sampled.extend(rest[: sample_count - used])
        random.shuffle(sampled)
        return sampled

    # ------------------------------------------------------------
    # 逐条问答（并发）
    # ------------------------------------------------------------
    async def _run_items(self, items: list, run_id: str) -> list:
        """并发执行全部条目，返回原始结果列表"""
        semaphore = asyncio.Semaphore(self._max_concurrency)
        loop = asyncio.get_running_loop()
        total = len(items)
        self._logger.info(f"逐条问答开始 | 总条数={total} | 并发上限={self._max_concurrency}")

        async def limited(item):
            return await self._run_item(item, semaphore, loop, run_id)

        results = await asyncio.gather(*(limited(it) for it in items), return_exceptions=True)

        # 异常兜底（_run_item 已收敛异常，此处防御性处理）
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                self._logger.error(
                    f"条目执行异常兜底失败 | item={items[i].get('id')} | error={res}", exc_info=True)
                results[i] = {
                    "item_id": items[i].get("id", ""),
                    "question": items[i].get("question", ""),
                    "error": f"unhandled:{res}",
                }
        ok = [r for r in results if isinstance(r, dict) and not r.get("error")]
        self._logger.info(
            f"逐条问答完成 | 成功={len(ok)}/{total} | "
            f"失败={len(results) - len(ok)}")
        return [r for r in results if isinstance(r, dict)]

    async def _run_item(self, item: dict, semaphore: asyncio.Semaphore,
                        loop: asyncio.AbstractEventLoop, run_id: str) -> dict:
        """单条问答：检索 → 组织上下文 → LLM 作答 → 测量延迟"""
        item_id = item.get("id", "")
        start = time.time()
        self._logger.debug(
            f"[{run_id}] 单条测试开始 | item_id={item_id} | level={item.get('level')} | "
            f"question={truncate(item.get('question', ''), 50)}"
        )

        async with semaphore:
            # 1. 检索参考文档
            retrieved = await loop.run_in_executor(
                None, lambda: self._retrieve(item.get("question", "")))
            # 2. 构建上下文 + 调用 LLM
            contexts = self._build_contexts(retrieved)
            refuse_hint = _REFUSE_HINT if item.get("should_refuse") else ""
            user_msg = _ANSWER_USER_TMPL.format(
                question=item.get("question", ""), contexts=contexts, refuse_hint=refuse_hint)
            messages = [
                {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]
            try:
                answer = await loop.run_in_executor(
                    None, lambda: self._llm.chat(messages, temperature=0.2, max_tokens=1024))
            except Exception as e:
                latency = round((time.time() - start) * 1000, 1)
                self._logger.error(
                    f"[{run_id}] LLM 作答失败 | item_id={item_id} | error={e}", exc_info=True)
                return self._mk_result(item, retrieved, "", latency, f"llm_error:{e}")

        latency = round((time.time() - start) * 1000, 1)
        self._logger.debug(
            f"[{run_id}] 单条测试完成 | item_id={item_id} | 延迟={latency}ms | "
            f"回答长度={len(answer)}")
        return self._mk_result(item, retrieved, answer, latency, None)

    def _mk_result(self, item: dict, retrieved: list, answer: str,
                   latency_ms: float, error: str) -> dict:
        """组装单条原始结果（供指标计算使用）"""
        return {
            "item_id": item.get("id", ""),
            "level": item.get("level", ""),
            "question": item.get("question", ""),
            "standard_answer": item.get("standard_answer", ""),
            "keywords": item.get("keywords", []),
            "should_refuse": bool(item.get("should_refuse", False)),
            "category": item.get("category", ""),
            "model_answer": answer or "",
            "latency_ms": latency_ms,
            "refused": is_refusal(answer or ""),
            "error": error,
            "retrieved_docs": [{
                "doc_uuid": d.get("doc_uuid", ""),
                "doc_title": d.get("doc_title", ""),
                "paragraph_id": d.get("paragraph_id"),
                "content": truncate(d.get("content", ""), 300),
            } for d in retrieved],
            "expected_docs": [item.get("source_doc_uuid", "")],
            "expected_paragraph_ids": item.get("source_paragraph_ids", []),
            "expected_chapter_id": item.get("source_chapter_id"),
        }

    # ------------------------------------------------------------
    # 检索与上下文
    # ------------------------------------------------------------
    def _retrieve(self, query: str, top_k: int = RETRIEVE_TOP_K) -> list:
        """轻量关键词检索：命中段落按关键词命中数降序返回（Milvus 未接入前兜底实现）"""
        from sqlalchemy import or_
        start = time.time()
        keywords = extract_keywords(query, max_kw=6)
        if not keywords:
            return []
        try:
            with SessionLocal() as db:
                stmt = (
                    db.query(DocParagraph, DocMain)
                    .join(DocMain, DocParagraph.doc_uuid == DocMain.doc_uuid)
                    .filter(
                        or_(*[DocParagraph.content_raw.like(f"%{kw}%") for kw in keywords]),
                        DocParagraph.content_raw.isnot(None),
                    )
                    .limit(top_k * 3)
                    .all()
                )
        except Exception as e:
            self._logger.error(f"检索失败 | query={truncate(query, 50)} | error={e}", exc_info=True)
            return []

        scored = []
        for p, doc in stmt:
            content = (p.content_raw or "")
            hit = sum(1 for kw in keywords if kw in content)
            if hit > 0:
                scored.append((hit, {
                    "doc_uuid": p.doc_uuid,
                    "doc_title": doc.title if doc else "",
                    "paragraph_id": p.id,
                    "chapter_id": p.chapter_id,
                    "content": content,
                }))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [s for _, s in scored[:top_k]]
        self._logger.debug(
            f"检索完成 | 关键词={keywords} | 命中={len(picked)}/{len(scored)} | "
            f"耗时={round((time.time() - start) * 1000, 1)}ms")
        return picked

    def _build_contexts(self, retrieved: list) -> str:
        """把检索结果渲染为带序号的参考资料文本"""
        lines = []
        for i, doc in enumerate(retrieved, start=1):
            title = doc.get("doc_title") or "未知文档"
            head = f"[{i}][来源 {doc.get('doc_uuid')}]{title}\n"
            lines.append(head + truncate(doc.get("content", ""), 800))
        return "\n\n".join(lines) if lines else "（无相关资料）"

    # ------------------------------------------------------------
    # 结果落盘与持久化
    # ------------------------------------------------------------
    def _save_raw_results(self, results: list, run_id: str) -> str:
        """原始逐条结果落盘到 raw 目录"""
        EVAL_RAW_ITEM_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_RAW_ITEM_DIR / f"{run_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        self._logger.info(f"原始结果已落盘 | path={path} | 条数={len(results)}")
        return str(path)

    def _persist_result(self, report: dict, version: str) -> int:
        """评测结果持久化到 eval_result 表，返回主键 ID"""
        start = time.time()
        try:
            with SessionLocal() as db:
                row = EvalResult(
                    version=version,
                    total_count=report.get("total_items", 0),
                    pass_count=report.get("pass_count", 0),
                    fail_count=report.get("fail_count", 0),
                    pass_rate=round(float(report.get("pass_rate", 0.0)), 4),
                    error_rate=round(float(report["metrics"].get("task_fact_error_rate", 0.0)), 4),
                    avg_latency_ms=round(float(report["metrics"].get("avg_latency_ms", 0.0)), 1),
                    report_detail=json.dumps(report, ensure_ascii=False),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
            self._logger.info(
                f"评测结果已持久化 | row_id={row.id} | version={version} | "
                f"耗时={round((time.time() - start) * 1000, 1)}ms")
            return row.id
        except Exception as e:
            self._logger.error(f"评测结果持久化失败 | error={e}", exc_info=True)
            return 0


async def run_regression(eval_set_id: str = None, version: str = "v1.0",
                         sample_count: int = None, run_id: str = None,
                         collect_badcase: bool = True) -> dict:
    """便捷入口：触发一次回归评测"""
    return await RegressionTester().run(
        eval_set_id=eval_set_id, version=version, sample_count=sample_count,
        run_id=run_id, collect_badcase=collect_badcase)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_regression())