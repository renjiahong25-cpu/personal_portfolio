# -*- coding: utf-8 -*-
"""
BadCase 管理器
- BadCase 自动入库：用户反馈（chat_feedback） + 回归测试失败案例
- BadCase 分类：幻觉 / 漏召 / 格式错误 / 拒答错误
- BadCase → 评测集自动扩充（生成含 badcase 的评测集 JSON）
- BadCase 列表查询、分页、状态管理
- 完整日志：入参/异常/关键节点
"""
import json
import time
import uuid

from sqlalchemy import or_, func

from config.logging_config import get_logger
from config.settings import EVAL_SET_DIR
from db.models.base import SessionLocal, BadCase, ChatFeedback

from ._text_utils import extract_keywords

logger = get_logger("badcase_manager")

# BadCase 分类枚举
CATEGORY_HALLUCINATION = "hallucination"    # 幻觉：回答存在编造/无依据内容
CATEGORY_MISSING_RECALL = "missing_recall"  # 漏召：真值资料未召回，导致回答缺失
CATEGORY_FORMAT_ERROR = "format_error"      # 格式错误：输出格式/引用异常
CATEGORY_WRONG_REFUSAL = "wrong_refusal"    # 拒答错误：应拒却答 / 不应拒却拒

CATEGORY_LABELS = {
    CATEGORY_HALLUCINATION: "幻觉",
    CATEGORY_MISSING_RECALL: "漏召",
    CATEGORY_FORMAT_ERROR: "格式错误",
    CATEGORY_WRONG_REFUSAL: "拒答错误",
}

# BadCase 来源枚举
SOURCE_FEEDBACK = "user_feedback"
SOURCE_REGRESSION = "regression"
SOURCE_MANUAL = "manual"

# 覆盖阈值：低于该值视为覆盖不足（用于幻觉/漏召判别）
_COVERAGE_LOW = 0.5


class BadCaseManager:
    """BadCase 全生命周期管理"""

    def __init__(self):
        self._logger = get_logger("badcase_manager")

    # ------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------
    def add_badcase(self, *, source: str, query: str, model_answer: str = "",
                    standard_answer: str = "", category: str = "",
                    reason: str = "", source_id: str = "",
                    eval_item_id: str = "") -> int:
        """新增 BadCase 入库（自动去重），返回主键 ID"""
        start = time.time()
        try:
            with SessionLocal() as db:
                # 去重：同一来源 + 同一问题文本
                d = db.query(BadCase).filter(
                    BadCase.source == source,
                    BadCase.query == query.strip(),
                ).first()
                if d:
                    self._logger.info(
                        f"BadCase 已存在，跳过 | id={d.id} | source={source} | "
                        f"query={query[:30]}...")
                    return d.id
                bc = BadCase(
                    source=source, source_id=source_id or "",
                    query=query.strip(), model_answer=model_answer,
                    standard_answer=standard_answer, category=category,
                    reason=reason[:500], eval_item_id=eval_item_id or "",
                    status=0,
                )
                db.add(bc)
                db.commit()
                db.refresh(bc)
            self._logger.info(
                f"BadCase 入库成功 | id={bc.id} | source={source} | category={category} | "
                f"耗时={round((time.time() - start) * 1000, 1)}ms")
            return bc.id
        except Exception as e:
            self._logger.error(f"BadCase 入库失败 | error={e}", exc_info=True)
            raise

    def collect_from_feedback(self, limit: int = None) -> int:
        """从用户负面反馈（chat_feedback.error）自动入库 BadCase"""
        start = time.time()
        count = 0
        try:
            with SessionLocal() as db:
                q = db.query(ChatFeedback).filter(ChatFeedback.feedback_type == 2)
                if limit:
                    q = q.limit(limit)
                feed_list = q.all()
            for f in feed_list:
                try:
                    category, reason = self.classify(
                        query=f.query or "", answer=f.response or "",
                        standard_answer="", should_refuse=False, refused=False,
                        coverage=None, is_feedback=True, bad_reason=f.bad_reason or "")
                    self.add_badcase(
                        source=SOURCE_FEEDBACK, source_id=str(f.id),
                        query=f.query or "", model_answer=f.response or "",
                        standard_answer="", category=category, reason=reason,
                        eval_item_id="")
                    count += 1
                except Exception as e:
                    self._logger.error(
                        f"反馈 BadCase 转换失败 | feedback_id={f.id} | error={e}", exc_info=True)
        except Exception as e:
            self._logger.error(f"用户反馈采集失败 | error={e}", exc_info=True)
            raise
        self._logger.info(
            f"用户反馈 BadCase 采集完成 | 入库={count} | feedback总={len(feed_list)} | "
            f"耗时={round(time.time() - start, 3)}s")
        return count

    def collect_from_regression(self, item_results: list, run_id: str) -> int:
        """回归测试失败案例自动入库 BadCase"""
        start = time.time()
        failed = [it for it in item_results
                  if isinstance(it, dict) and not it.get("item_pass", True)]
        count = 0
        for it in failed:
            try:
                category, reason = self.classify(
                    query=it.get("question", ""), answer=it.get("model_answer", ""),
                    standard_answer=it.get("standard_answer", ""),
                    should_refuse=bool(it.get("should_refuse")),
                    refused=bool(it.get("refused")),
                    coverage=it.get("coverage"),
                    retrieved_uuids={d.get("doc_uuid")
                                     for d in (it.get("retrieved_docs") or [])},
                    expected_docs=it.get("expected_docs") or [],
                    has_error=bool(it.get("error")),
                    fail_reasons=it.get("fail_reasons") or [])
                self.add_badcase(
                    source=SOURCE_REGRESSION,
                    source_id=f"{run_id}-{it.get('item_id', '')}",
                    query=it.get("question", ""),
                    model_answer=it.get("model_answer", ""),
                    standard_answer=it.get("standard_answer", ""),
                    category=category, reason=reason,
                    eval_item_id=it.get("item_id", ""))
                count += 1
            except Exception as e:
                self._logger.error(
                    f"回归 BadCase 转换失败 | item={it.get('item_id')} | error={e}",
                    exc_info=True)
        self._logger.info(
            f"回归失败 BadCase 采集完成 | 失败案例={len(failed)} | 入库={count} | "
            f"耗时={round(time.time() - start, 3)}s")
        return count

    # ------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------
    def classify(self, *, query: str, answer: str, standard_answer: str,
                 should_refuse: bool, refused: bool, coverage=None,
                 retrieved_uuids=None, expected_docs=None, is_feedback=False,
                 has_error=False, fail_reasons=None) -> (str, str):
        """BadCase 自动分类，返回 (category, reason)"""
        reasons = fail_reasons or []
        # 1) 拒答错误优先
        if should_refuse and not refused:
            return CATEGORY_WRONG_REFUSAL, "应拒答却正常作答"
        if (not should_refuse) and refused:
            return CATEGORY_WRONG_REFUSAL, "不应拒答却拒绝回答"

        # 2) LLM 调用失败 / 输出异常 → 格式错误
        if has_error:
            return CATEGORY_FORMAT_ERROR, f"问答流水线异常：{reasons}"

        if is_feedback:
            # 用户反馈无法获知标准答案：无召回的拒答 → 漏召；否则默认幻觉
            if refused:
                return CATEGORY_MISSING_RECALL, "用户反馈错误：知识库可能缺内容"
            return CATEGORY_HALLUCINATION, "用户反馈错误：回答存在错误或编造"

        # 3) 真值文档未召回 → 漏召
        if expected_docs and retrieved_uuids is not None and expected_docs:
            missing = [d for d in expected_docs if d not in retrieved_uuids]
            if missing:
                return CATEGORY_MISSING_RECALL, f"真值文档未召回，缺失覆盖来源"

        # 4) 覆盖率不足 → 按是否编造倾向区分
        if standard_answer and coverage is not None and coverage < _COVERAGE_LOW:
            if coverage == 0.0:
                return CATEGORY_MISSING_RECALL, "关键信息完全未覆盖（漏答）"
            return CATEGORY_HALLUCINATION, "关键信息覆盖不足，存在事实偏差或编造风险"

        return CATEGORY_FORMAT_ERROR, "；".join(reasons) or "格式或引用不符合规范"

    # ------------------------------------------------------------
    # BadCase → 评测集扩充
    # ------------------------------------------------------------
    def expand_eval_set(self, category: str = "", version: str = "1.0",
                        limit: int = 50, output_id: str = "") -> dict:
        """将未扩充的 BadCase 生成评测集 JSON，并标记已扩充"""
        start = time.time()
        with SessionLocal() as db:
            q = db.query(BadCase).filter(BadCase.status == 0)
            if category:
                q = q.filter(BadCase.category == category)
            badcases = q.order_by(BadCase.id.desc()).limit(limit).all()
            if not badcases:
                self._logger.info(
                    f"BadCase 评测集扩充：无可扩充未处理案例 | category={category or '全部'}")
                return {}

        items = []
        for bc in badcases:
            standard = bc.standard_answer or ""
            keywords = extract_keywords(standard or bc.query, max_kw=8)
            items.append({
                "id": f"bc_{bc.id}",
                "level": "badcase",
                "question": bc.query,
                "standard_answer": standard or "待人工补充：该案例标准答案缺失",
                "keywords": keywords,
                "should_refuse": False,
                "category": CATEGORY_LABELS.get(bc.category, bc.category),
                "related_badcase_id": bc.id,
                "created_at": time.strftime("%Y%m%d%H%M%S"),
                "source_doc_uuid": "",
                "source_doc_title": "",
                "source_paragraph_ids": [],
            })

        eval_set = {
            "eval_set_id": output_id or
            f"evalset_badcase_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
            "version": version,
            "description": "BadCase 自动扩充评测集（回归对抗验证）",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "badcase_expand": True,
            "item_count": len(items),
            "items": items,
        }
        EVAL_SET_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_SET_DIR / f"{eval_set['eval_set_id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(eval_set, f, ensure_ascii=False, indent=2)

        # 标记已扩充
        try:
            with SessionLocal() as db:
                for bc in badcases:
                    db.query(BadCase).filter(BadCase.id == bc.id).update(
                        {"status": 1})
                db.commit()
        except Exception as e:
            self._logger.error(f"BadCase 状态更新失败 | error={e}", exc_info=True)

        self._logger.info(
            f"BadCase 评测集扩充完成 | 评测集={eval_set['eval_set_id']} | 条目={len(items)} | "
            f"path={path} | 耗时={round(time.time() - start, 3)}s")
        return eval_set

    # ------------------------------------------------------------
    # 查询与状态
    # ------------------------------------------------------------
    def list_badcases(self, page: int = 1, page_size: int = 10,
                      category: str = "", status: int = None) -> dict:
        """BadCase 分页查询，支持分类/状态过滤"""
        start = time.time()
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        try:
            with SessionLocal() as db:
                q = db.query(BadCase)
                if category:
                    q = q.filter(BadCase.category == category)
                if status is not None:
                    q = q.filter(BadCase.status == status)
                total = q.count()
                rows = (q.order_by(BadCase.id.desc())
                        .offset((page - 1) * page_size).limit(page_size).all())
                data = [{
                    "id": r.id,
                    "source": r.source,
                    "source_id": r.source_id,
                    "query": r.query,
                    "model_answer": r.model_answer,
                    "standard_answer": r.standard_answer,
                    "category": r.category,
                    "category_label": CATEGORY_LABELS.get(r.category, r.category),
                    "reason": r.reason,
                    "eval_item_id": r.eval_item_id,
                    "status": r.status,
                    "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S")
                    if r.create_time else "",
                } for r in rows]
            result = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": data,
            }
            self._logger.info(
                f"BadCase 列表查询 | total={total} | page={page}/{page_size} | "
                f"category={category or '全部'} | 耗时={round(time.time() - start, 3)}s")
            return result
        except Exception as e:
            self._logger.error(f"BadCase 列表查询失败 | error={e}", exc_info=True)
            raise

    def update_status(self, badcase_id: int, status: int) -> bool:
        """更新 BadCase 处理状态（0 未处理 / 1 已扩充 / 2 已关闭）"""
        try:
            with SessionLocal() as db:
                row = db.query(BadCase).filter(BadCase.id == badcase_id).first()
                if not row:
                    self._logger.warning(f"BadCase 不存在 | id={badcase_id}")
                    return False
                row.status = status
                db.commit()
            self._logger.info(f"BadCase 状态已更新 | id={badcase_id} | status={status}")
            return True
        except Exception as e:
            self._logger.error(
                f"BadCase 状态更新失败 | id={badcase_id} | error={e}", exc_info=True)
            raise


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    mgr = BadCaseManager()
    result = mgr.list_badcases(page=1, page_size=5)
    print(result)