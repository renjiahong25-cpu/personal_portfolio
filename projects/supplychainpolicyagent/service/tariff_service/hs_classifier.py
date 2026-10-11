# -*- coding: utf-8 -*-
"""
HS 编码智能分类器（商品描述 → HS 编码）
=====================================================
两级方案：
  1. 离线关键词层级匹配（全部本地，秒级，不依赖 LLM）
     - 对每个国家的 hs_code 表构建”编码+父级链描述“的倒排索引
     - 查询词按稀有度加权打分，命中即返回候选与置信分数
  2. LLM 辅助裁决（TARIFF_LLM_FALLBACK_ENABLE=true 且顶层得分低于阈值时）
     - 把候选集塞给 Qwen，从中选出最贴合的 6/8 位编码
"""
import math
import re
import time

from config.settings import TARIFF_LLM_FALLBACK_ENABLE, TARIFF_DEFAULT_COUNTRY
from config.logging_config import get_logger
from core.dist_lock import lock_manager, LockBackendError

logger = get_logger("hs_classifier")

try:
    from sqlalchemy import or_
except Exception:  # pragma: no cover
    or_ = None

try:
    from db.models.base import SessionLocal, HsCode, DutyRate
except Exception:  # pragma: no cover
    SessionLocal = HsCode = DutyRate = None

try:
    from core.llm_client import llm_client
except Exception:  # pragma: no cover
    llm_client = None

_LATIN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"([\u4e00-\u9fff]+)")
# 与商品分类无关的干扰词（去掉，避免把买家电/工具类误命中到包装材料等）
_STOP = {
    "the", "of", "and", "or", "for", "with", "to", "in", "on", "a", "an", "is", "are",
    "other", "parts", "part", "including", "containing", "used", "use", "new", "made",
}
_LOW_CONF_THRESHOLD = 0.60  # 最优得分低于此值（中置信以下）则触发 LLM 裁决流程


def _tokenize(text: str) -> list:
    """分词：英文按词（轻量复数归一）、中文按单字+二字词（轻量，避免引入额外依赖）"""
    tokens = []
    t = (text or "").lower()
    for m in _LATIN_RE.finditer(t):
        w = m.group()
        if w not in _STOP and len(w) > 1:
            tokens.append(_stem_plural(w))
    for seg in _CJK_RE.findall(t):
        for ch in seg:
            if ch.strip():
                tokens.append(ch)
        for i in range(len(seg) - 1):
            bigram = seg[i:i + 2]
            if bigram.strip():
                tokens.append(bigram)
    return tokens


def _stem_plural(w: str) -> str:
    """轻量英文复数归一：ies→y、末尾 s→去 s（限长度>3 且非双 ss），
    使 motorcycle/motorcycles、moped/mopeds 等互通。"""
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


class HsClassifier:
    """按国家缓存 HS 编码描述索引，提供 search / classify"""

    CACHE_TTL = 600  # 秒

    def __init__(self):
        self._cache: dict = {}  # country -> {"ts":float,"posts":{token:{code:tf}},"docs":{code:{...}},"total":int}

    def refresh(self, country: str = None, acquire_timeout: float = 30.0):
        """从数据库重建索引（某国或全部国家）。数据导入后需主动调用。

        跨进程单飞：多 worker/多副本同时到 TTL 时只让一个实例重建，
        其余在 acquire_timeout 内让位（沿用旧索引，下一周期再追）。
        锁后端不可用时降级为直接重建（保证数据导入脚本可用）。
        """
        countries = [country] if country else ["US", "EU", "CN"]
        for c in countries:
            try:
                lock_manager.run_exclusive(
                    f"hs_index:{c}", lambda c=c: self._build_country(c),
                    timeout=acquire_timeout, on_busy=lambda c=c: self._skip_build(c))
            except LockBackendError as e:
                logger.warning(f"锁后端不可用，HS 索引直接重建 | country={c} | {e}")
                self._build_country(c)

    def _skip_build(self, country: str):
        logger.info(f"HS 索引重建由其他实例进行，本实例跳过本周期 | country={country}")
        return False

    def _build_country(self, c: str):
        with SessionLocal() as db:
            # 排除测试填充行（SYNTHETIC），避免垃圾描述污染关键词索引与分类结果
            rows = db.query(HsCode).filter(
                HsCode.country == c, HsCode.status == 1,
                or_(HsCode.description_en.is_(None),
                    HsCode.description_en.notlike("%SYNTHETIC%"))).all()
            doc_map = {}
            by_code = {}
            for r in rows:
                by_code[r.hs_code] = {
                    "description_cn": r.description_cn or "",
                    "description_en": r.description_en or "",
                    "level": r.level,
                    "parent_code": r.parent_code,
                }
            # 拼父级链描述，提升 8/10 位明细与 4/6 位品目都能命中
            for code, d in by_code.items():
                chain = [d["description_cn"] or d["description_en"] or ""]
                p = d["parent_code"]
                depth = 0
                while p and p in by_code and depth < 4:
                    pd = by_code[p]
                    chain.append(pd["description_cn"] or pd["description_en"] or "")
                    p = pd["parent_code"]
                    depth += 1
                doc_map[code] = {
                    **d,
                    "text": " ".join(x for x in chain if x).lower(),
                }
            posts = {}
            total = len(doc_map)
            for code, doc in doc_map.items():
                tokens = _tokenize(doc["text"])
                local = {}
                for tok in tokens:
                    local[tok] = local.get(tok, 0) + 1
                for tok, tf in local.items():
                    posts.setdefault(tok, {})[code] = tf
            self._cache[c] = {"ts": time.time(), "posts": posts, "docs": doc_map, "total": total}
            logger.info(f"HS 索引重建 | country={c} | codes={total} | tokens={len(posts)}")
            return True

    def _ensure(self, country: str):
        cache = self._cache.get(country)
        if not cache or time.time() - cache["ts"] > self.CACHE_TTL:
            self.refresh(country, acquire_timeout=0.0)
            cache = self._cache.get(country)
        return cache or {"posts": {}, "docs": {}, "total": 0}

    def search(self, description: str, country: str = None, top_k: int = 20) -> list:
        country = country or TARIFF_DEFAULT_COUNTRY
        cache = self._ensure(country)
        posts, docs, total = cache["posts"], cache["docs"], cache["total"]
        if not docs:
            return []
        # 稀有度权重
        q_tokens = _tokenize(description)
        weights = {}
        for tok in q_tokens:
            df = len(posts.get(tok, {}))
            if df:
                weights[tok] = max(0.3, math.log((total + 2) / (df + 1)))
        if not weights:
            return []
        scores = {}
        for tok, w in weights.items():
            for code in posts.get(tok, {}):
                scores[code] = scores.get(code, 0.0) + w
        # 归一化：按描述词数平方根 + 编码位数奖励（越细越贴近商品）
        results = []
        for code, raw in scores.items():
            doc = docs[code]
            nt = max(1, len(_tokenize(doc["text"])))
            norm = raw / math.sqrt(nt)
            level_bonus = (doc["level"] - 2) * 0.02 if doc["level"] <= 8 else 0.03
            results.append({
                "hs_code": code,
                "level": doc["level"],
                "description_cn": doc["description_cn"],
                "description_en": doc["description_en"],
                "parent": doc["parent_code"],
                "score": round(norm + level_bonus, 4),
            })
        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def classify(self, description: str, country: str = None, top_k: int = 8) -> dict:
        """返回 {top, candidates, needs_llm}；needs_llm=true 表示置信不足"""
        country = country or TARIFF_DEFAULT_COUNTRY
        candidates = self.search(description, country, top_k)
        if not candidates:
            return {"top": None, "candidates": [], "needs_llm": False, "msg": "该国暂无 HS 数据"}
        top = candidates[0]
        # 得分近似的候选中倾向更特化（更深位）且自身描述命中查询词更多的编码：
        # HS 分类应落到最贴切细目，而非只和父级品目文本重合。
        q_tokens = set(_tokenize(description))
        near = [c for c in candidates if c["score"] >= top["score"] * 0.6]
        if near:
            top = max(near, key=lambda c: (
                c["level"],
                sum(1 for t in q_tokens if t in _tokenize(
                    c["description_cn"] or c["description_en"] or "")),
                c["score"],
            ))
        needs_llm = bool(TARIFF_LLM_FALLBACK_ENABLE) and top["score"] < _LOW_CONF_THRESHOLD
        return {"top": top, "candidates": candidates, "needs_llm": needs_llm,
                "msg": "置信不足，建议 LLM 裁决" if needs_llm else ""}


hs_classifier = HsClassifier()