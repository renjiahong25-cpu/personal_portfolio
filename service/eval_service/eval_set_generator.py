# -*- coding: utf-8 -*-
"""
评测集生成器（AI 自动生成）
- 基于已入库文档（doc_main / doc_chapter / doc_paragraph）自动生成评测问题
- 三级配比：全局文档 20% + 章节大类 30% + 段落细节 50%
- 生成可复查的评测集：问题 + 标准答案 + 关键词 + 来源文档 + 是否应拒答
- 评测集以 JSON 文件存储，支持列表与读取
- 完整日志：入参/异常/关键节点
"""
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import distinct, func

from config.logging_config import get_logger
from config.settings import (
    EVAL_SET_DIR, EVAL_LEVEL_RATIO, EVAL_DEFAULT_TARGET_COUNT,
    EVAL_QUESTION_SAMPLE_PER_UNIT, EVAL_GEN_RETRY_TIMES,
)
from config.constants import DocStatus
from core.llm_client import llm_client as _default_llm
from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph

from ._text_utils import truncate

logger = get_logger("eval_set_generator")

# 三级评测层级
LEVEL_GLOBAL = "global"
LEVEL_CHAPTER = "chapter"
LEVEL_PARAGRAPH = "paragraph"

_LEVEL_NAME = {
    LEVEL_GLOBAL: "全局文档",
    LEVEL_CHAPTER: "章节大类",
    LEVEL_PARAGRAPH: "段落细节",
}

# 段落细节级别的最小/最大内容长度（字符），过短无信息量、过长超模型窗口
_PARAGRAPH_MIN_LEN = 30
_PARAGRAPH_MAX_LEN = 1600

# LLM 单条评测条目生成提示词
_LLM_ITEM_PROMPT_TMPL = """你是跨境物流合规评测专家，负责为评测集生成高质量"问题-标准答案"对。

下面是{level}层级的一段{src_type}材料：
【来源文档】{doc_overview}
【材料内容】
{content}

请生成一条测评条目，要求：
1. 问题紧扣材料，可依据材料直接作答，禁止引入材料外背景；
2. 标准答案逐条覆盖材料中的关键规则、数值、条件、时间节点与来源；
3. 提取 3~8 个能表征标准答案的关键词（规则词/数值/概念）；
4. 判断该问题是否属于系统应拒答范围（超出跨境清关服务边界、知识库无对应内容）；
5. 分类到以下之一：关税 / 税务 / 认证 / 禁限运 / 单证。

仅输出一个 JSON 对象，不要输出任何多余字符：
{{
  "question": "问题",
  "standard_answer": "标准答案",
  "keywords": ["关键词1", "关键词2"],
  "should_refuse": false,
  "category": "关税"
}}"""

# 兜底生成：LLM 连续失败时用材料直取，保证评测集可用
_FALLBACK_QUESTION_TMPL = "请根据以下合规材料回答：材料中关于「{topic}」的核心规则是什么？"


def _now_ts() -> str:
    return time.strftime("%Y%m%d%H%M%S")


class EvalSetGenerator:
    """基于已入库文档 + LLM 自动生成可复查评测集"""

    def __init__(self, llm=None):
        self._llm = llm or _default_llm
        self._logger = get_logger("eval_set_generator")

    # ------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------
    def generate(self, target_count: Optional[int] = None,
                 version: str = "1.0", description: str = "") -> dict:
        """生成并保存评测集，返回评测集元数据"""
        start = time.time()
        target = target_count or EVAL_DEFAULT_TARGET_COUNT
        self._logger.info(
            f"评测集生成开始 | target_count={target} | ratio={EVAL_LEVEL_RATIO} | version={version}"
        )
        # 1. 加载三级材料单元
        units = self._load_units()
        if not units:
            self._logger.error("评测集生成失败：知识库无可用材料单元（文档/章节/段落均为空）")
            raise ValueError("知识库无可用材料单元，无法生成评测集")

        # 2. 按配比采样
        sampled = self._sample_by_ratio(units, target)
        self._logger.info(f"三级材料采样完成 | 计划条目数={target} | 实际单元数={len(sampled)}")

        # 3. 逐单元生成评测条目
        items, fail_count = self._gen_items(sampled)

        # 4. 组装评测集
        eval_set = {
            "eval_set_id": f"evalset_{_now_ts()}_{uuid.uuid4().hex[:8]}",
            "version": version,
            "description": description or f"AI 自动生成评测集（{_now_ts()}）",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "level_ratio": EVAL_LEVEL_RATIO,
            "model": getattr(self._llm, "model", ""),
            "item_count": len(items),
            "gen_fail_count": fail_count,
            "items": items,
        }
        path = self.save_set(eval_set)
        elapsed = round(time.time() - start, 3)
        self._logger.info(
            f"评测集生成完成 | eval_set_id={eval_set['eval_set_id']} | 条目数={len(items)} | "
            f"失败条数={fail_count} | 保存路径={path} | 耗时={elapsed}s"
        )
        return eval_set

    def save_set(self, eval_set: dict) -> Path:
        """评测集 JSON 持久化"""
        EVAL_SET_DIR.mkdir(parents=True, exist_ok=True)
        path = EVAL_SET_DIR / f"{eval_set['eval_set_id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(eval_set, f, ensure_ascii=False, indent=2)
        self._logger.info(f"评测集已保存 | eval_set_id={eval_set['eval_set_id']} | path={path}")
        return path

    def load_set(self, path: Path) -> dict:
        """读取评测集 JSON"""
        start = time.time()
        try:
            with open(path, "r", encoding="utf-8") as f:
                eval_set = json.load(f)
            self._logger.info(
                f"评测集读取成功 | path={path} | 条目数={len(eval_set.get('items', []))} | "
                f"耗时={round(time.time() - start, 3)}s"
            )
            return eval_set
        except Exception as e:
            self._logger.error(f"评测集读取失败 | path={path} | error={e}", exc_info=True)
            raise

    def list_sets(self, limit: int = 20) -> list:
        """枚举已生成的评测集（元数据摘要）"""
        if not EVAL_SET_DIR.exists():
            return []
        results = []
        for path in sorted(EVAL_SET_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "eval_set_id": data.get("eval_set_id"),
                    "version": data.get("version"),
                    "item_count": data.get("item_count", len(data.get("items", []))),
                    "level_ratio": data.get("level_ratio"),
                    "generated_at": data.get("generated_at"),
                    "description": data.get("description"),
                    "path": str(path),
                })
            except Exception as e:
                self._logger.warning(f"评测集元数据读取失败 | path={path} | error={e}")
        self._logger.info(f"评测集列表查询 | count={len(results)}")
        return results

    # ------------------------------------------------------------
    # 材料加载与采样
    # ------------------------------------------------------------
    def _load_units(self) -> list:
        """从库中加载三级材料单元：全局文档/章节大类/段落细节"""
        start = time.time()
        units = []
        try:
            with SessionLocal() as db:
                # 1) 全局文档层：已生效文档（status=NORMAL）
                docs = db.execute(
                    DocMain.__table__.select().where(DocMain.status == DocStatus.NORMAL.value)
                ).fetchall()
                for doc in docs:
                    units.append({
                        "level": LEVEL_GLOBAL,
                        "doc_uuid": doc.doc_uuid,
                        "doc_title": doc.title,
                        "category": doc.category or "",
                        "source_url": doc.source_url or "",
                        "version": doc.version or "",
                    })

                # 2) 章节大类层：章节 + 所属文档
                rows = db.query(DocChapter, DocMain).join(
                    DocMain, DocChapter.doc_uuid == DocMain.doc_uuid
                ).all()
                seen_chapters = set()
                for ch, doc in rows:
                    key = (ch.doc_uuid, ch.chapter_id)
                    if key in seen_chapters:
                        continue
                    seen_chapters.add(key)
                    units.append({
                        "level": LEVEL_CHAPTER,
                        "doc_uuid": ch.doc_uuid,
                        "doc_title": doc.title if doc else "",
                        "chapter_id": ch.id,
                        "chapter_title": ch.chapter_title,
                        "chapter_level": ch.chapter_level,
                        "chapter_path": ch.chapter_path or "",
                    })

                # 3) 段落细节层：内容长度适中的段落
                paras = db.query(DocParagraph, DocMain).join(
                    DocMain, DocParagraph.doc_uuid == DocMain.doc_uuid
                ).filter(
                    DocParagraph.content_raw.isnot(None),
                    func.length(DocParagraph.content_raw) >= _PARAGRAPH_MIN_LEN,
                ).all()
                for p, doc in paras:
                    content = (p.content_raw or "").strip()
                    if _PARAGRAPH_MIN_LEN <= len(content) <= _PARAGRAPH_MAX_LEN:
                        units.append({
                            "level": LEVEL_PARAGRAPH,
                            "doc_uuid": p.doc_uuid,
                            "doc_title": doc.title if doc else "",
                            "chapter_id": p.chapter_id,
                            "paragraph_id": p.id,
                            "content": content,
                            "content_summary": p.content_summary or "",
                        })
        except Exception as e:
            self._logger.error(f"知识库材料加载失败 | error={e}", exc_info=True)
            raise

        level_count = {}
        for u in units:
            level_count[u["level"]] = level_count.get(u["level"], 0) + 1
        self._logger.info(
            f"材料单元加载完成 | 总量={len(units)} | 分布={level_count} | "
            f"耗时={round(time.time() - start, 3)}s"
        )
        return units

    def _sample_by_ratio(self, units: list, target_count: int) -> list:
        """按三级配比（20/30/50）从各级材料中采样"""
        import random
        start = time.time()
        by_level = {LEVEL_GLOBAL: [], LEVEL_CHAPTER: [], LEVEL_PARAGRAPH: []}
        for u in units:
            by_level[u["level"]].append(u)

        n_global = int(target_count * EVAL_LEVEL_RATIO.get(LEVEL_GLOBAL, 0.2))
        n_chapter = int(target_count * EVAL_LEVEL_RATIO.get(LEVEL_CHAPTER, 0.3))
        n_paragraph = target_count - n_global - n_chapter

        sampled = []
        for level, n in ((LEVEL_GLOBAL, n_global), (LEVEL_CHAPTER, n_chapter),
                         (LEVEL_PARAGRAPH, n_paragraph)):
            pool = by_level[level]
            if not pool:
                self._logger.warning(f"层级 {level} 无可采样单元，跳过")
                continue
            take = min(n, len(pool))
            chosen = random.sample(pool, take)
            # 每个单元生成 EVAL_QUESTION_SAMPLE_PER_UNIT 条问题（默认 1）
            sampled.extend([u for u in chosen for _ in range(EVAL_QUESTION_SAMPLE_PER_UNIT)])

        # 随机打乱，避免评测顺序偏好
        random.shuffle(sampled)
        self._logger.info(
            f"按配比采样完成 | 目标={target_count} | 实际={len(sampled)} | "
            f"global={n_global} | chapter={n_chapter} | paragraph={n_paragraph} | "
            f"耗时={round(time.time() - start, 3)}s"
        )
        return sampled

    # ------------------------------------------------------------
    # 单条生成
    # ------------------------------------------------------------
    def _gen_items(self, units: list) -> (list, int):
        """逐单元调用 LLM 生成评测条目，统计失败数"""
        items = []
        fail_count = 0
        total = len(units)
        for idx, unit in enumerate(units, start=1):
            try:
                item = self._gen_item(unit)
                items.append(item)
            except Exception as e:
                fail_count += 1
                self._logger.error(
                    f"条目生成失败 | unit={unit.get('level')} | doc={unit.get('doc_uuid')} | "
                    f"error={e}", exc_info=True
                )
            # 关键节点：每 10 条或末尾打印一次进度
            if idx % 10 == 0 or idx == total:
                self._logger.info(f"评测条目生成进度 | {idx}/{total} | 累计失败={fail_count}")
        return items, fail_count

    def _gen_item(self, unit: dict) -> dict:
        """生成单条评测条目（LLM + 重试 + 兜底）"""
        level = unit["level"]
        src_type = "整份政策文档" if level == LEVEL_GLOBAL else (
            "章节" if level == LEVEL_CHAPTER else "段落")
        content, doc_overview = self._unit_to_text(unit)
        prompt = _LLM_ITEM_PROMPT_TMPL.format(
            level=_LEVEL_NAME[level], src_type=src_type,
            doc_overview=doc_overview, content=content,
        )
        messages = [
            {"role": "system", "content": "你只输出严格合法的 JSON，不附加任何说明。"},
            {"role": "user", "content": prompt},
        ]

        parsed = None
        for attempt in range(EVAL_GEN_RETRY_TIMES + 1):
            try:
                raw = self._llm.chat(messages, temperature=0.3 if attempt == 0 else 0.1,
                                     max_tokens=1024)
                parsed = self._parse_llm_json(raw)
                if parsed:
                    break
                self._logger.warning(
                    f"LLM 输出非合法 JSON，第{attempt + 1}次重试 | level={level}")
            except Exception as e:
                self._logger.warning(
                    f"LLM 调用失败，第{attempt + 1}次重试 | level={level} | error={e}")

        if not parsed:
            self._logger.warning(
                f"LLM 重试{EVAL_GEN_RETRY_TIMES}次仍失败，使用材料兜底生成 | level={level}")
            parsed = self._fallback_item(unit, content)

        return self._build_item(unit, parsed)

    def _unit_to_text(self, unit: dict) -> (str, str):
        """把材料单元转成 LLM 上下文：内容 + 来源文档描述"""
        doc_overview = (
            f"《{unit['doc_title']}》(分类:{unit.get('category', '') or '未知'},"
            f"来源:{unit.get('source_url', '') or '未知'})"
        )
        if unit["level"] == LEVEL_GLOBAL:
            content = unit.get("category", "") or unit.get("doc_title", "")
            doc_overview = f"《{unit['doc_title']}》分类:{unit.get('category', '')} 来源:{unit.get('source_url', '')}"
            return content, doc_overview
        if unit["level"] == LEVEL_CHAPTER:
            # 章节加上所属文档标题作为上下文
            return unit["chapter_title"], (f"《{unit['doc_title']}》章节:{unit['chapter_title']}")
        return unit["content"], doc_overview

    def _build_item(self, unit: dict, parsed: dict) -> dict:
        """根据 LLM 解析结果 + 材料单元信息组装标准评测条目"""
        item = {
            "id": f"q_{uuid.uuid4().hex[:12]}",
            "level": unit["level"],
            "question": (parsed.get("question") or "").strip(),
            "standard_answer": (parsed.get("standard_answer") or "").strip(),
            "keywords": parsed.get("keywords") or [],
            "should_refuse": bool(parsed.get("should_refuse", False)),
            "category": parsed.get("category") or "关税",
            "source_doc_uuid": unit.get("doc_uuid"),
            "source_doc_title": unit.get("doc_title"),
            "source_chapter_id": unit.get("chapter_id"),
            "source_chapter_title": unit.get("chapter_title"),
            "source_paragraph_ids": [unit["paragraph_id"]] if unit.get("paragraph_id") else [],
            "created_at": _now_ts(),
        }
        if not item["question"] or not item["standard_answer"]:
            raise ValueError(f"LLM 输出缺少问题或标准答案 | level={unit['level']}")
        return item

    def _fallback_item(self, unit: dict, content: str) -> dict:
        """兜底：LLM 不可用时从材料直取，生成可复查条目"""
        import re as _re
        content = truncate(content, 800)
        topic = _re.sub(r"[\s，。；：、,.!?]+", "", (content or "")[:20])
        keywords = []
        for num in _re.findall(r"\d+(?:[.,]\d+)*", content):
            keywords.append(num)
        items = _re.findall(r"[\u4e00-\u9fff]{2,}", content)
        for w in items:
            if len(keywords) >= 5:
                break
            if w not in keywords:
                keywords.append(w)
        return {
            "question": _FALLBACK_QUESTION_TMPL.format(topic=topic),
            "standard_answer": content,
            "keywords": keywords or [topic],
            "should_refuse": False,
            "category": "关税",
        }

    def _parse_llm_json(self, text: str) -> Optional[dict]:
        """从 LLM 输出中稳健解析 JSON 对象"""
        if not text:
            return None
        text = text.strip()
        # 去掉 ```json ``` 代码块
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"): text.rfind("}") + 1] if "{" in text else text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start: end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as e:
            self._logger.debug(f"JSON 解析失败 | error={e} | raw={truncate(text, 300)}")
        return None


def generate_eval_set(target_count: Optional[int] = None,
                      version: str = "1.0", description: str = "") -> dict:
    """便捷入口：生成评测集"""
    return EvalSetGenerator().generate(target_count, version, description)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    gen = EvalSetGenerator()
    result = gen.generate(version="v1.0")
    print(f"生成完成，评测集: {result['eval_set_id']}，条目数: {result['item_count']}")