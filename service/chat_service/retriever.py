# -*- coding: utf-8 -*-
"""
混合检索核心模块（BM25 + 向量 + 四层兜底 + Rerank精排）
=====================================================
职责：
    1. 双路召回：BM25 关键词检索（硬词：HS编码/认证名/法规名/专业术语）
                 + 向量语义检索（口语化/模糊/多条件复合）
    2. 加权融合排序：BM25_WEIGHT * bm25 + VECTOR_WEIGHT * vector（settings 配置）
    3. 分层检索：先章节级粗筛（章节标题BM25锁定法规范围），再段落级精排
    4. Rerank精排：sentence-transformers CrossEncoder 交叉编码器重排
    5. 四层兜底：精准检索 → 宽泛检索 → 追问重检索 → 全网官网检索

容错设计：
    - 向量库（Milvus Lite）不可用 → 自动降级纯 BM25 关键词检索；
    - 知识库快照 / 重依赖缺失 → 逐级降级并记录日志，绝不阻塞主链路；
    - 每个层级记录检索结果数、耗时、命中率。
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

import httpx

from config.logging_config import get_logger
from config import settings

logger = get_logger("retriever")

# ============================================================
# 可选重依赖（惰性加载，缺失自动降级）
# ============================================================
try:
    from rank_bm25 import BM25Okapi

    _BM25_AVAILABLE = True
except Exception:
    BM25Okapi = None
    _BM25_AVAILABLE = False
    logger.warning("rank_bm25 不可用，BM25 检索将降级为关键词包含匹配")

try:
    from pymilvus import MilvusClient

    _MILVUS_AVAILABLE = True
except Exception:
    MilvusClient = None
    _MILVUS_AVAILABLE = False
    logger.warning("pymilvus 不可用，向量检索将降级")

try:
    from sentence_transformers import SentenceTransformer, CrossEncoder

    _ST_AVAILABLE = True
except Exception:
    SentenceTransformer = None
    CrossEncoder = None
    _ST_AVAILABLE = False
    logger.warning("sentence-transformers 不可用，向量/Rerank 将降级")

try:
    from db.models.base import SessionLocal, DocMain, DocChapter, DocParagraph
except Exception as e:  # pragma: no cover - 数据库驱动缺失时兜底
    SessionLocal = None
    DocMain = DocChapter = DocParagraph = None
    logger.warning(f"数据库 ORM 导入异常，MySQL 知识库快照不可用 | error={e}")


# ============================================================
# 检索结果数据结构
# ============================================================
@dataclass
class RetrievalItem:
    """单条检索结果：正文 + 完整三级溯源元数据 + 双路分数"""
    doc_uuid: str = ""
    doc_title: str = ""
    source_url: str = ""
    doc_version: str = ""
    publish_time: str = ""
    effective_time: str = ""
    category: str = ""
    country: str = ""  # 文档管辖国家（检索国家分区键，空=全局）
    chapter_id: int = 0
    chapter_title: str = ""
    chapter_path: str = ""
    paragraph_id: int = 0
    vector_id: str = ""
    offset_info: str = ""
    content_raw: str = ""
    content_summary: str = ""
    token_len: int = 0
    bm25_score: float = 0.0
    vector_score: float = 0.0
    fused_score: float = 0.0
    source: str = "bm25"  # bm25 / vector / mixed / official
    sub_query_id: int = -1  # 多路检索来源子问题编号（-1=单路）
    overlay: str = ""  # overlay 缝合后的原段落上下文（含相邻段落），只读不溯源
    overlay_token_len: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    # 融合排序用的去重主键：段落ID优先，退化为 (doc_uuid, chapter_id)
    def _key(self):
        if self.paragraph_id:
            return ("p", self.paragraph_id)
        return ("c", self.doc_uuid, self.chapter_id)

    def __hash__(self):
        return hash(self._key())

    def __eq__(self, other):
        return isinstance(other, RetrievalItem) and self._key() == other._key()


# ============================================================
# 通用工具
# ============================================================
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 德文扫描：含变元音（äöü）与 ß，避免 "für"/"Überlassung" 被切碎
_DE_WORD_RE = re.compile(r"[a-z0-9äöüß]+(?:[\-\.\x2f][a-z0-9äöüß]+)*")

# 德语海关域复合词词干（用于复合词拆分，提升 BM25 召回）
_DE_COMPOUND_STEMS = frozenset([
    "zoll", "anmeldung", "anmeldungen", "ausfuhr", "ausgang", "eingang", "abgang",
    "verfahren", "abwicklung", "gestellung", "wartezeit", "überlassung", "überwachung",
    "abrechnung", "erledigung", "versand", "beendigung", "teilnahme", "kontrolle",
    "nachricht", "nachrichten", "abfertigung", "zugang", "bearbeitung", "wiederausfuhr",
    "bestimmung", "mitteilung", "genehmigung", "stornierung", "überführung", "zollstelle",
    "mitgliedstaat", "anwendung", "fachanwendung", "verzeichnis", "lagerbestand",
    "kennzeichen", "anschreibung", "bewilligung", "vereinfachung", "herkunft",
    "empfänger", "ausfuhranmeldung", "ausgangsbestätigung", "endverwendung", "auflage",
    "anmelder", "zollvertreter", "zollabwicklung", "abgangszollstelle", "ausfuhrzollstelle",
    "ausgangszollstelle", "gestellungszollstelle", "vorgang", "vorgänge", "erfassung",
    "abgabe", "übermittlung", "übernahme", "getrenntstellung", "freiverkehr",
])


def _de_compound_expand(tokens: list) -> list:
    """德语复合词补充词干（如 Zollanmeldungsverfahren → zoll/anmeldung/verfahren），提升 BM25 命中"""
    extra = []
    for tok in tokens:
        if len(tok) < 5:
            continue
        for stem in _DE_COMPOUND_STEMS:
            if len(stem) >= 5 and stem in tok and stem != tok:
                extra.append(stem)
    return extra


def tokenize(text: str) -> list:
    """轻量分词：英/德文（含变元音）词元 + 中文单字与二元组；德语复合词补充词干词元"""
    text = (text or "").lower()
    words = _DE_WORD_RE.findall(text)
    words += _de_compound_expand(words)
    cjk_seq = "".join(_CJK_RE.findall(text))
    chars = list(cjk_seq)
    bigrams = [cjk_seq[i : i + 2] for i in range(max(0, len(cjk_seq) - 1))]
    return words + chars + bigrams


def estimate_tokens(text: str) -> int:
    """Token 估算（中英文混合的启发式）：约 1.5 字符 / token"""
    if not text:
        return max(1, round(len(text) / 1.5))
    return max(1, round(len(text) / 1.5))


# 国家别名归一：意图识别/文档标签都映射到统一键（与 DocMain.country 一致）
_COUNTRY_ALIAS = {
    "德国": "德国", "germany": "德国", "de": "德国",
    "中国": "中国", "china": "中国", "cn": "中国",
    "荷兰": "荷兰", "netherlands": "荷兰", "nl": "荷兰",
    "法国": "法国", "france": "法国", "fr": "法国",
    "美国": "美国", "usa": "美国", "united states": "美国", "us": "美国",
}


def norm_country(country: str) -> str:
    """国家别名归一化；空串返回空（空=不做国家分区，全库检索）"""
    c = (country or "").strip()
    if not c:
        return ""
    return _COUNTRY_ALIAS.get(c.lower(), c)


# ============================================================
# 混合检索器
# ============================================================
class HybridRetriever:
    """BM25 + 向量混合检索引擎（含四层兜底、分层检索、Rerank）"""

    def __init__(self):
        self._bm25_rows: list[RetrievalItem] = []
        self._bm25_corpus: list[list] = []
        self._bm25: object = None
        self._chapter_rows: list[tuple] = []  # (chapter_id, title文本)
        self._chapter_bm25: object = None
        self._kb_loaded_at: Optional[datetime] = None

        self._embedded_q: str = ""  # 缓存查询串（避免重复embedding）
        self._embedded_vec: Optional[list] = None
        self._embed_model = None

        self._reranker = None
        self._rerank_ok = False

        self._milvus: object = None
        self._milvus_ok = False
        self._milvus_checked_at: Optional[datetime] = None
        self._milvus_lock = asyncio.Lock()   # 并发护栏：防多路检索同时抢建 Milvus Lite 文件锁
        self._vec_out_fields: list = []
        self._vec_metric: str = "COSINE"
        self._vec_scalar_fields: set = set()

        self._db_lock = asyncio.Lock()
        self._init_logged = False
        logger.info(
            f"HybridRetriever 初始化完成 | bm25={'OK' if _BM25_AVAILABLE else '降级'} | "
            f"milvus={'OK' if _MILVUS_AVAILABLE else '降级'} | "
            f"st={'OK' if _ST_AVAILABLE else '降级'} | "
            f"bm25_weight={settings.BM25_WEIGHT} | vector_weight={settings.VECTOR_WEIGHT} | "
            f"top_k={settings.RETRIEVE_TOP_K} | threshold={settings.SIMILARITY_THRESHOLD}"
        )

    # ----------------------------------------------------------
    # 知识库快照加载（MySQL 三级切片：文档-章节-段落）
    # ----------------------------------------------------------
    def _raw_datetime(self, val) -> str:
        if not val:
            return ""
        return val.strftime("%Y-%m-%d") if isinstance(val, datetime) else str(val)

    def _load_kb_snapshot(self):
        """从 MySQL 拉取三级切片，构建段落级 BM25 语料与章节标题语料"""
        if DocParagraph is None or SessionLocal is None:
            raise RuntimeError("ORM 不可用，无法加载知识库快照")
        start = time.time()
        with SessionLocal() as db:
            paragraphs = db.query(DocParagraph).all()
            chapters = {c.id: c for c in db.query(DocChapter).all()}
            docs = {d.doc_uuid: d for d in db.query(DocMain).all()}
            logger.info(
                f"知识库快照加载(MySQL) | docs={len(docs)} chapters={len(chapters)} paragraphs={len(paragraphs)}"
            )
            rows = []
            for p in paragraphs:
                ch = chapters.get(p.chapter_id)
                doc = docs.get(p.doc_uuid)
                item = RetrievalItem(
                    doc_uuid=p.doc_uuid or "",
                    doc_title=doc.title if doc else "",
                    source_url=doc.source_url if doc else "",
                    doc_version=doc.version if doc else "",
                    publish_time=self._raw_datetime(doc.publish_time) if doc else "",
                    effective_time=self._raw_datetime(doc.effective_time) if doc else "",
                    category=doc.category if doc else "",
                    country=getattr(doc, "country", "") or "",
                    chapter_id=p.chapter_id or 0,
                    chapter_title=ch.chapter_title if ch else "",
                    chapter_path=ch.chapter_path if ch else "",
                    paragraph_id=p.id or 0,
                    vector_id=p.vector_id or "",
                    offset_info=p.offset_info or "",
                    content_raw=p.content_raw or "",
                    content_summary=p.content_summary or "",
                    token_len=p.token_len or estimate_tokens(p.content_raw or ""),
                )
                rows.append(item)

        self._bm25_rows = rows
        self._bm25_corpus = [tokenize(r.content_raw + " " + (r.content_summary or "")) for r in rows]
        self._bm25 = BM25Okapi(self._bm25_corpus) if _BM25_AVAILABLE else None

        # 章节级语料：标题 + 路径 + 章节摘要（对应一层粗筛）
        chapter_map: dict[int, list[str]] = {}
        for r in rows:
            if r.chapter_id:
                chapter_map.setdefault(r.chapter_id, []).append(r)
        self._chapter_rows = []
        for cid, members in chapter_map.items():
            title = members[0].chapter_title or ""
            joined = members[0].chapter_path or ""
            self._chapter_rows.append((cid, title, joined))
        self._chapter_bm25 = (
            BM25Okapi([tokenize(t + " " + p) for _c, t, p in self._chapter_rows])
            if _BM25_AVAILABLE and self._chapter_rows
            else None
        )
        self._kb_loaded_at = datetime.now()
        logger.info(
            f"知识库快照构建完成 | rows={len(self._bm25_rows)} | elapsed={round(time.time()-start, 3)}s"
        )

    async def _ensure_kb(self, force: bool = False):
        """确保知识库快照就绪（带 TTL 缓存）"""
        if not force and self._bm25_rows and self._kb_loaded_at:
            if datetime.now() - self._kb_loaded_at < timedelta(seconds=settings.KB_CACHE_TTL_SECONDS):
                return True
        async with self._db_lock:
            # 双重检查，防止并发重复加载
            if not force and self._bm25_rows and self._kb_loaded_at:
                if datetime.now() - self._kb_loaded_at < timedelta(seconds=settings.KB_CACHE_TTL_SECONDS):
                    return True
            try:
                await asyncio.to_thread(self._load_kb_snapshot)
                return True
            except Exception as e:
                logger.error(f"知识库快照加载失败 | error={e}", exc_info=True)
                return False

    # ----------------------------------------------------------
    # BM25 关键词检索
    # ----------------------------------------------------------
    async def _bm25_search(self, query: str, top_k: int, chapter_ids: Optional[set[int]] = None) -> list[RetrievalItem]:
        """BM25精确关键词召回，可选按章节集合过滤（分层检索的段落级精排）"""
        if not _BM25_AVAILABLE or self._bm25 is None:
            return []
        toks = tokenize(query)
        if not toks:
            return []
        start = time.time()
        scores = await asyncio.to_thread(self._bm25.get_scores, toks)
        ranked = sorted(
            (i for i, s in enumerate(scores) if s > 0 and (not chapter_ids or self._bm25_rows[i].chapter_id in chapter_ids)),
            key=lambda i: scores[i],
            reverse=True,
        )[:top_k]
        items = []
        for i in ranked:
            item = self._bm25_rows[i]
            item.bm25_score = scores[i]
            item.source = "bm25"
            items.append(item)
        elapsed = round(time.time() - start, 3)
        logger.info(f"BM25 检索完成 | top_k={top_k} | hit={len(items)} | elapsed={elapsed}s | query={query[:40]}")
        return items

    # ----------------------------------------------------------
    # 向量语义检索（Milvus，可自动降级）
    # ----------------------------------------------------------
    def _build_milvus(self):
        """创建 MilvusClient 并探测集合 schema（自动适配字段名）"""
        self._milvus_ok = False  # 先复位，失败时不留陈旧状态
        self._milvus = None
        if not _MILVUS_AVAILABLE:
            return False
        try:
            # 本地文件模式（Milvus Lite）需确保父目录存在
            uri = settings.MILVUS_URI
            if not uri.startswith(("http://", "https://", "tcp://")):
                from pathlib import Path
                Path(uri).parent.mkdir(parents=True, exist_ok=True)
            client = MilvusClient(uri=uri)
            if not client.has_collection(settings.MILVUS_COLLECTION):
                logger.warning(f"Milvus 集合不存在: {settings.MILVUS_COLLECTION}")
                return False
            schema = client.describe_collection(settings.MILVUS_COLLECTION)
            fields = [f for f in schema.get("fields", []) if isinstance(f, dict)]
            if not fields:
                # 兼容旧版返回结构（fields 为数组但需转换）
                fields = schema.get("fields", [])
            names = {f.get("name") for f in fields}
            # 探测向量字段与主键字段（兼容 DataType 枚举 name / 整数 / 字符串表示）
            def _type_name(f):
                t = f.get("type")
                return getattr(t, "name", t)

            vec_field = next(
                (f.get("name") for f in fields if str(_type_name(f)).find("VECTOR") != -1),
                None,
            )
            pk_field = next((f.get("name") for f in fields if f.get("is_primary")), None)
            if not vec_field:
                logger.warning(f"Milvus 集合未发现向量字段 | fields={sorted(names)}")
                return False
            # collection 是否已建索引（无索引无法 search）
            output_fields = [
                n for n in ("paragraph_id", "vector_id", "chapter_id", "doc_uuid", "id")
                if n in names
            ]
            metric = "COSINE"
            try:
                index_info = client.describe_index(settings.MILVUS_COLLECTION)
                params = index_info[0].get("params", {}) if index_info else {}
                metric = params.get("metric_type", "COSINE")
            except Exception:
                pass
            self._milvus = client
            self._milvus_ok = True
            self._vec_field = vec_field
            self._vec_out_fields = output_fields
            self._vec_metric = metric
            self._vec_scalar_fields = names
            self._milvus_checked_at = datetime.now()
            logger.info(
                f"Milvus 连接成功 | collection={settings.MILVUS_COLLECTION} | metric={metric} | "
                f"vec_field={vec_field} | pk={pk_field} | output_fields={output_fields}"
            )
            return True
        except Exception as e:
            logger.error(f"Milvus 初始化失败，向量检索将降级 | error={e}", exc_info=True)
            return False

    async def _ensure_milvus(self):
        """确保 Milvus 连接可用（带冷却期，避免反复失败重连）

        并发护栏：多路检索（multi_search）会并行触发多个 _ensure_milvus，
        Milvus Lite 是单进程文件锁（logs/milvus.db），并发的 MilvusClient(uri=...)
        会互相抢锁导致 DataDirLockedError。此处用 asyncio.Lock 串行化首次初始化，
        后续直接复用已建连接，避免每次 search 反复建连。
        """
        if not _MILVUS_AVAILABLE:
            return False
        # 已连接且在有效期内：直接复用，不再探测
        if self._milvus_ok and self._milvus is not None:
            return True
        # 冷却期内失败过：跳过本轮再次尝试（避免高频重连）
        if not self._milvus_ok and self._milvus_checked_at:
            if datetime.now() - self._milvus_checked_at < timedelta(seconds=settings.KB_CACHE_TTL_SECONDS):
                return False
        async with self._milvus_lock:
            # 双检：等锁期间可能已有别的任务建好
            if self._milvus_ok and self._milvus is not None:
                return True
            try:
                ok = await asyncio.to_thread(self._build_milvus)
                # 失败也刷新标记时间，进入冷却期，避免高频重连
                self._milvus_checked_at = datetime.now()
                return ok
            except Exception as e:
                logger.error(f"Milvus 探测异常 | error={e}", exc_info=True)
                self._milvus_ok = False
                self._milvus_checked_at = datetime.now()
                return False

    async def _embed_query(self, query: str) -> Optional[list]:
        """查询向量化（惰性加载 sentence-transformer 模型）"""
        if not _ST_AVAILABLE or SentenceTransformer is None:
            return None
        try:
            if self._embed_model is None:
                logger.info(f"加载向量模型: {settings.EMBEDDING_MODEL}")
                self._embed_model = await asyncio.to_thread(
                    SentenceTransformer, settings.EMBEDDING_MODEL
                )
            if self._embedded_q == query and self._embedded_vec is not None:
                return self._embedded_vec
            vec = await asyncio.to_thread(self._embed_model.encode, query)
            self._embedded_q = query
            self._embedded_vec = vec.tolist() if hasattr(vec, "tolist") else list(vec)
            return self._embedded_vec
        except Exception as e:
            logger.error(f"查询向量化失败 | error={e}", exc_info=True)
            return None

    @staticmethod
    def _metric_to_sim(distance: float, metric: str) -> float:
        """Milvus distance 转换为相似度（越大越相关，0~1 归一）"""
        metric = (metric or "COSINE").upper()
        if metric == "L2":
            return 1.0 / (1.0 + distance)
        return distance  # COSINE / IP 已满足"越大越相关"

    async def _vector_search(self, query: str, top_k: int, chapter_ids: Optional[set[int]] = None) -> list[RetrievalItem]:
        """向量语义召回（Milvus）；任一环节失败返回空列表并触发 BM25 降级"""
        if not self._milvus_ok:
            return []
        vec = await self._embed_query(query)
        if vec is None:
            logger.warning("查询向量不可用，跳过向量检索")
            return []

        # 章节粗筛后的精细化过滤（仅在集合存在 chapter_id 字段时生效）
        expr = ""
        if chapter_ids and "chapter_id" in self._vec_scalar_fields:
            ids = ", ".join(str(c) for c in chapter_ids)
            expr = f"chapter_id in [{ids}]"

        start = time.time()
        try:
            hits = await asyncio.to_thread(
                self._milvus.search,
                settings.MILVUS_COLLECTION,
                data=[vec],
                limit=top_k,
                output_fields=self._vec_out_fields,
                filter=expr or None,
            )
        except Exception as e:
            logger.error(f"Milvus search 异常，本轮向量检索降级 | error={e}", exc_info=True)
            return []

        # 向量命中的段落 → 匹配知识库快照，绑定完整元数据
        id_to_row = {r.paragraph_id: r for r in self._bm25_rows}
        vec_id_to_row = {r.vector_id: r for r in self._bm25_rows if r.vector_id}
        items: list[RetrievalItem] = []
        if hits:
            for hit in hits[0]:
                sim = self._metric_to_sim(hit.get("distance", 0.0), self._vec_metric)
                entity = hit.get("entity", {}) or {}
                para_id = entity.get("paragraph_id") or 0
                vec_id = entity.get("vector_id") or hit.get("id") or ""
                row = id_to_row.get(para_id) or vec_id_to_row.get(vec_id or str(hit.get("id", "")))
                if row is None:
                    continue  # 向量命中但快照无法溯源 → 丢弃，保证溯源可靠
                item = RetrievalItem(**asdict(row))
                item.vector_score = sim
                item.source = "vector"
                items.append(item)
        elapsed = round(time.time() - start, 3)
        logger.info(f"向量检索完成 | top_k={top_k} | hit={len(items)} | elapsed={elapsed}s | metric={self._vec_metric}")
        return items

    # ----------------------------------------------------------
    # 加权融合 + Rerank精排
    # ----------------------------------------------------------
    def _fuse(self, bm25_items: list[RetrievalItem], vec_items: list[RetrievalItem], threshold: float) -> list[RetrievalItem]:
        """
        双路加权融合：BM25_WEIGHT * bm25_norm + VECTOR_WEIGHT * vec_sim
        - 排序依据：加权融合分 fused_score；
        - 阈值门控依据：单路最强信号 effective = max(bm25_norm, vec_sim)，
          避免单路召回时因权重缩放而整体被误杀。
        - bm25_norm 用批内排名归一（1.0→0.0 线性衰减）：s/(s+1) 会把 10-17 分
          压成 0.91-0.94，对同域查询（整库都是 ATLAS 流程）无区分度，融合分
          挤在 0.37-0.39 噪声带，排序近似随机（E2E 复现 4.9 主干章节被挤出）。
        """
        # 批内排名归一：BM25 分降序排名 → 1.0, 1-(1/(n-1)), ..., 0.0
        ranked = sorted((b for b in bm25_items if b.bm25_score > 0), key=lambda x: -x.bm25_score)
        n = len(ranked)
        rank_norm: dict[tuple, float] = {}
        for r, b in enumerate(ranked):
            rank_norm[b._key()] = (1.0 - r / (n - 1)) if n > 1 else 1.0

        merged: dict[tuple, RetrievalItem] = {}
        for item in bm25_items:
            abs_norm = item.bm25_score / (item.bm25_score + 1.0)  # 绝对值：用于阈值门控
            # 排序值：批内排名归一（有区分度）；零分或单条时回退绝对值
            rank_val = rank_norm.get(item._key(), abs_norm) if item.bm25_score > 0 and n > 1 else abs_norm
            clone = RetrievalItem(**asdict(item))
            clone.fused_score = settings.BM25_WEIGHT * rank_val
            clone._bm25_norm = rank_val  # type: ignore[attr-defined]
            clone._bm25_abs = abs_norm  # type: ignore[attr-defined]
            clone._effective = abs_norm  # type: ignore[attr-defined]
            merged[clone._key()] = clone
        for item in vec_items:
            key = item._key()
            target = merged.get(key)
            if target is None:
                clone = RetrievalItem(**asdict(item))
                clone.fused_score = settings.VECTOR_WEIGHT * item.vector_score
                clone._bm25_norm = 0.0  # type: ignore[attr-defined]
                clone._effective = item.vector_score  # type: ignore[attr-defined]
                merged[key] = clone
            else:
                # 双路命中：融合 = 两路加权和（排序用排名值），门控用绝对值
                target.vector_score = item.vector_score
                target.source = "mixed"
                target.fused_score = (
                    settings.BM25_WEIGHT * target._bm25_norm  # type: ignore[attr-defined]
                    + settings.VECTOR_WEIGHT * item.vector_score
                )
                # type: ignore[attr-defined]
                target._effective = max(target._bm25_abs, item.vector_score)  # type: ignore[attr-defined]
        results = [v for v in merged.values() if v._effective >= threshold]  # type: ignore[attr-defined]
        results.sort(key=lambda x: x.fused_score, reverse=True)
        logger.info(
            f"融合排序完成 | bm25={len(bm25_items)} | vector={len(vec_items)} | "
            f"merged={len(merged)} | keep={len(results)} | threshold={threshold}"
        )
        return results

    @staticmethod
    def _looks_german(query: str) -> bool:
        """检测查询是否为德语：含变元音 äöüß 或常见德语词即判德语"""
        if not query:
            return False
        q = query.lower()
        if any(ch in q for ch in ("ä", "ö", "ü", "ß")):
            return True
        de_hints = ("für", "sind", "ist", "eine", "einen", "einem", "eine", "der", "die", "das",
                    "und", "über", "nicht", "wie", "welche", "welches", "schritte", "verfahren",
                    "zollanmeldung", "anmeldung", "ausfuhr", "einfuhr", "bei", "wird", "wurde")
        words = set(q.split())
        return bool(words & set(de_hints))

    async def _remote_rerank(self, query: str, items: list[RetrievalItem],
                             top_k: int):
        """调远程 Embed 微服务精排；不可用返回 None（调用方回退本地模型）。"""
        url = settings.EMBED_HTTP_URL.rstrip("/") + "/rerank"
        try:
            docs = [(i.content_raw or "")[:500] for i in items]
            r = httpx.post(
                url,
                json={"query": query, "documents": docs, "top_k": top_k},
                timeout=settings.EMBED_HTTP_TIMEOUT,
            )
            r.raise_for_status()
            results = r.json().get("results") or []
            if not results:
                return None
            by_idx = {d["document"]: float(d["score"]) for d in results}
            scored = sorted(items, key=lambda i: by_idx.get((i.content_raw or "")[:500], 0.0), reverse=True)
            logger.info(f"Rerank(远程) 完成 | in={len(items)} | top={top_k}")
            for item in scored[: settings.RERANK_TOP_K]:
                item.fused_score = by_idx.get((item.content_raw or "")[:500], 0.0)
            return scored[: settings.RERANK_TOP_K]
        except Exception as e:
            logger.warning(f"Rerank 远程调用失败，回退本地模型 | error={e}")
            return None

    async def _rerank(self, query: str, items: list[RetrievalItem], top_k: int) -> list[RetrievalItem]:
        """CrossEncoder Rerank 精排结果"""
        if not items:
            return items
        if not settings.RERANK_ENABLE:
            return items[:top_k]
        # 德文（含变元音）查询跳过英文 CrossEncoder 精排——ms-marco 系模型对德语
        # 排序失真（实测把 Verwahrungsorte 等无关段排前、丢失 4.9 Ausfuhrverfahren）。
        if getattr(settings, "RERANK_SKIP_DE", True) and self._looks_german(query):
            logger.info(f"Rerank 跳过（德语查询，英文模型降级）| query={query[:40]}")
            return items[:top_k]
        # P4：远程 Embed 微服务精排（多副本共享模型；失败自动回退本地加载）
        if getattr(settings, "EMBED_HTTP_URL", ""):
            remote = await self._remote_rerank(query, items, top_k)
            if remote is not None:
                return remote
        if not self._rerank_ok:
            try:
                if _ST_AVAILABLE and CrossEncoder is not None:
                    logger.info(f"加载 Rerank 模型: {settings.RERANK_MODEL}")
                    self._reranker = await asyncio.to_thread(CrossEncoder, settings.RERANK_MODEL)
                    self._rerank_ok = True
            except Exception as e:
                logger.warning(f"Rerank 模型加载失败，跳过精排 | error={e}")
                self._rerank_ok = False
                return items[:top_k]
        if not self._rerank_ok:
            return items[:top_k]
        start = time.time()
        try:
            pairs = [(query, (i.content_raw or "")[:500]) for i in items]
            scores = await asyncio.to_thread(self._reranker.predict, pairs)
            scored = list(zip(items, scores))
            scored.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"Rerank 精排完成 | in={len(items)} | top={settings.RERANK_TOP_K} | elapsed={round(time.time()-start,3)}s")
            for item, s in scored[: settings.RERANK_TOP_K]:
                item.fused_score = float(s)
            return [i for i, _ in scored[: settings.RERANK_TOP_K]]
        except Exception as e:
            logger.warning(f"Rerank 执行失败，返回融合序结果 | error={e}")
            return items[:top_k]

    # ----------------------------------------------------------
    # 主检索入口（含分层检索）
    # ----------------------------------------------------------
    async def search(
        self,
        query: str,
        top_k: int = None,
        threshold: float = None,
        chapter_ids: Optional[set[int]] = None,
        request_id: str = "",
        country: str = "",
    ) -> list[RetrievalItem]:
        """
        核心检索：章节级粗筛 → 段落级 BM25 + 向量双路召回 → 加权融合 → Rerank
        country：目标国家分区（意图识别确定）；只召回该国管辖的文档，跨国互不干扰；
        空=全库检索。未标注国家的文档视为全局保留（防漏标误杀）；
        分区为空不在此回退，交由四层兜底处理。
        """
        start = time.time()
        req_tag = request_id or "-"
        top_k = top_k or settings.RETRIEVE_TOP_K
        threshold = settings.SIMILARITY_THRESHOLD if threshold is None else threshold
        country = norm_country(country)
        logger.info(
            f"[{req_tag}] 检索开始 | query={query[:60]} | top_k={top_k} | threshold={threshold} | "
            f"country={country or '(全库)'} | chapter_ids={list(chapter_ids)[:6] if chapter_ids else None}"
        )

        kb_ready = await self._ensure_kb()
        await self._ensure_milvus()

        # 1) 章节级粗筛：先通过章节标题 BM25 锁定法规范围
        coarse_chapters: Optional[set[int]] = None
        if chapter_ids is None and self._chapter_bm25 is not None:
            cb_toks = tokenize(query)
            if cb_toks:
                try:
                    cb_scores = await asyncio.to_thread(self._chapter_bm25.get_scores, cb_toks)
                    top_indices = sorted(
                        range(len(self._chapter_rows)),
                        key=lambda i: cb_scores[i],
                        reverse=True,
                    )[:12]
                    positive = {self._chapter_rows[i][0] for i in top_indices if cb_scores[i] > 0}
                    # 强制纳入与查询相关的「报关流程章节族」：命中核心词干即整族纳入，
                    # 防止 4.9 Ausfuhrverfahren 因章节标题措辞差异（不含 zoll/anmeldung）被漏筛。
                    forced: set[int] = set()
                    core_stems = [
                        s for s in ("ausfuhr", "anmeldung", "einfuhr", "verfahren", "zoll", "abfertigung")
                        if s in cb_toks
                    ]
                    if core_stems:
                        for cid, ctitle, cpath in self._chapter_rows:
                            joint = (ctitle + " " + cpath).lower()
                            # 章节族路径前缀命中即整族纳入（/4 报关流程全族、/7 参与条件、/9 单证编码）
                            if cpath.startswith("/4") or cpath.startswith("/7") or cpath.startswith("/9"):
                                forced.add(cid)
                            elif any(s in joint for s in core_stems):
                                forced.add(cid)
                    coarse_chapters = (positive | forced) or None
                    if coarse_chapters:
                        logger.info(
                            f"[{req_tag}] 章节级粗筛命中 | chapters={len(coarse_chapters)} "
                            f"(bm25_score>0={len(positive)}, 词干强制={len(forced)})"
                        )
                except Exception as e:
                    logger.warning(f"[{req_tag}] 章节级粗筛失败，跳过 | error={e}")
                    coarse_chapters = None

        effective_chapters = chapter_ids if chapter_ids is not None else coarse_chapters

        # 2) 段落级双路召回（并行执行 BM25 与向量检索）
        bm25_fut, vec_fut = None, None
        if kb_ready:
            bm25_fut = asyncio.ensure_future(self._bm25_search(query, top_k * 2, effective_chapters))
        if self._milvus_ok:
            vec_fut = asyncio.ensure_future(self._vector_search(query, top_k, effective_chapters))
        bm25_items = await bm25_fut if bm25_fut else []
        vec_items = await vec_fut if vec_fut else []

        # 2.5) 国家分区：只保留目标国家管辖的文档（跨国互不干扰）。
        # 语义：未标注国家的文档视为全局（保留）；仅排除明确标注为其他国家者。
        # 分区为空不在此回退——交由 retrieve_with_fallback 的 L2/L3/L4 兜底，
        # 避免把异国家文档灌回上下文造成跨语言干扰。
        if country:
            b_all, v_all = len(bm25_items), len(vec_items)
            bm25_items = [x for x in bm25_items if not x.country or x.country == country]
            vec_items = [x for x in vec_items if not x.country or x.country == country]
            if not bm25_items and not vec_items:
                logger.warning(
                    f"[{req_tag}] 国家分区 '{country}' 无管辖文档 (bm25={b_all}, vec={v_all})，本轮召回为空"
                )
            elif b_all != len(bm25_items) or v_all != len(vec_items):
                logger.info(
                    f"[{req_tag}] 国家分区 | country={country} | bm25 {b_all}→{len(bm25_items)} | "
                    f"vec {v_all}→{len(vec_items)}"
                )

        # 3) 加权融合 + 4) Rerank
        fused = self._fuse(bm25_items, vec_items, threshold)
        final_items = await self._rerank(query, fused, top_k)

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"[{req_tag}] 检索完成 | bm25={len(bm25_items)} | vector={len(vec_items)} | "
            f"fused={len(fused)} | final={len(final_items)} | elapsed={elapsed}s"
        )
        return final_items

    # ----------------------------------------------------------
    # ----------------------------------------------------------
    # 流程主干补全（spine completion）
    # ----------------------------------------------------------
    @staticmethod
    def _chapter_num(chapter_path: str) -> str:
        """从章节路径提取编号（如 '/4.9.1.1 Anlegen ...' → '4.9.1.1'）"""
        seg = chapter_path.strip().lstrip("/")
        parts = seg.split(" ")
        return parts[0] if parts else ""

    def _complete_procedure_spine(self, merged: dict, request_id: str) -> dict:
        """
        对命中≥2 的流程章节族，注入其 1-2 级子章节的首段（生命周期主干）。
        例：4.9 族命中 → 按编号序注入 4.9.1.1 Anlegen / 4.9.1.3 Bearbeitung /
        4.9.1.4 Überlassung+ABD / 4.9.2 Überwachung / 4.9.3 Erledigung / 4.9.4 ...
        注入段落 fused = 0.9 × 该族最高命中分，排在族内命中附近。
        """
        MAX_SPINE_PER_FAMILY = 6
        if not merged or not self._bm25_rows:
            return merged
        # 统计每个 (doc, 章节族) 的命中数与最高分
        fam_hits: dict[tuple, int] = {}
        fam_max: dict[tuple, float] = {}
        for item in merged.values():
            num = self._chapter_num(item.chapter_path)
            parts = num.split(".")
            if len(parts) < 2:
                continue
            fam = (item.doc_uuid, ".".join(parts[:2]))
            fam_hits[fam] = fam_hits.get(fam, 0) + 1
            fam_max[fam] = max(fam_max.get(fam, 0.0), item.fused_score)
        added = 0
        for (doc_uuid, fam), hits in fam_hits.items():
            if hits < 2:
                continue
            fam_depth = len(fam.split(".")[:2])  # '4.9' → 2
            # 候选主干章节：同文档、编号以 fam. 开头、深度 = fam 深度+1 或 +2，
            # 每章取段落 id 最小的首段
            ch_rows: dict[int, list[RetrievalItem]] = {}
            for row in self._bm25_rows:
                if row.doc_uuid != doc_uuid:
                    continue
                num = self._chapter_num(row.chapter_path)
                if not num.startswith(fam + "."):
                    continue
                if len(num.split(".")) not in (fam_depth + 1, fam_depth + 2):
                    continue
                ch_rows.setdefault(row.chapter_id, []).append(row)
            cands: list[tuple[str, RetrievalItem]] = []
            for cid, rows in ch_rows.items():
                rows.sort(key=lambda r: r.paragraph_id or 0)
                cands.append((self._chapter_num(rows[0].chapter_path), rows[0]))
            # 编号按数字元组排序（防 "4.9.11.1" < "4.9.2" 的字符串序错误）
            def _num_key(item: tuple) -> tuple:
                try:
                    return tuple(int(p) for p in item[0].split("."))
                except ValueError:
                    return (9999,)

            cands.sort(key=lambda x: (_num_key(x), x[1].chapter_id))
            # 主干与族内最强命中同权（0.9 会被同族碎片命中挤出，向量波动下不稳定）
            base_score = 1.0 * fam_max.get((doc_uuid, fam), 0.0)
            for idx, (num, first_row) in enumerate(cands[:MAX_SPINE_PER_FAMILY]):
                key = (first_row.doc_uuid, first_row.paragraph_id) if first_row.paragraph_id else (first_row.doc_uuid, first_row.chapter_id)
                if key in merged:
                    continue
                clone = RetrievalItem(**asdict(first_row))
                # 位次轻微衰减，保证主干顺序（Anlegen→Bearbeitung→Überlassung→Überwachung→Erledigung）
                clone.fused_score = base_score * (1.0 - 0.02 * idx)
                clone.source = "spine"  # type: ignore
                clone.sub_query_id = -1  # type: ignore  # 非子问题命中，不参与"原问"降权
                merged[key] = clone
                added += 1
        if added:
            logger.info(f"[{request_id}] 流程主干补全 | 注入={added} 段 (source=spine)")
        return merged

    # ----------------------------------------------------------
    # 需求拆解多路检索（子问题并行检索后合并去重）
    # ----------------------------------------------------------
    async def multi_search(
        self,
        sub_queries: list[str],
        top_k: int = None,
        threshold: float = None,
        merge_limit: int = None,
        request_id: str = "",
        original_query: str = "",
        country: str = "",
    ) -> list[RetrievalItem]:
        """
        对拆解出的多个子问题分别执行 search，再按段落主键合并去重。
        - 各子问题检索 top_k 默认取 DECOMPOSE_TOP_K，避免多路检索结果膨胀；
        - 若传入 original_query，将其作为"路0"一并检索（中问→德文翻译全失败时兜底）；
        - 合并时保留每段最高 fused_score 记录及其来源子问题；
        - 最终按 fused_score 降序截断到 merge_limit。
        """
        import asyncio as _aio

        from config.settings import DECOMPOSE_TOP_K, DECOMPOSE_MERGE_LIMIT

        start = time.time()
        req_tag = request_id or "-"
        # 路0：原始问题（保中文召回通道）；其余为子问题
        queries = ([original_query] if original_query else []) + list(sub_queries)
        if not queries:
            return []
        top_k = top_k or DECOMPOSE_TOP_K
        threshold = settings.SIMILARITY_THRESHOLD if threshold is None else threshold
        merge_limit = merge_limit or DECOMPOSE_MERGE_LIMIT
        country = norm_country(country)

        tasks = [
            self.search(q, top_k=top_k, threshold=threshold, request_id=f"{req_tag}/sub{i}", country=country)
            for i, q in enumerate(queries)
        ]
        results_per_sub = await _aio.gather(*tasks, return_exceptions=True)

        # 合并去重：主键 (doc_uuid, paragraph_id) 兜底 (doc_uuid, chapter_id)
        merged: dict[tuple, RetrievalItem] = {}
        sub_summary: list[dict] = []
        for i, q in enumerate(queries):
            sub_items = results_per_sub[i]
            tag = "原问" if i == 0 and original_query else f"sub#{i}"
            if isinstance(sub_items, BaseException):
                logger.warning(f"[{req_tag}] {tag} 检索异常 | query={q[:40]} | error={sub_items}")
                sub_summary.append({"sub_id": i, "query": q, "hit": 0, "error": str(sub_items)})
                continue
            sub_summary.append({"sub_id": i, "query": q, "hit": len(sub_items)})
            for item in sub_items:
                key = (item.doc_uuid, item.paragraph_id) if item.paragraph_id else (item.doc_uuid, item.chapter_id)
                cur = merged.get(key)
                if cur is None or item.fused_score > cur.fused_score:
                    clone = RetrievalItem(**asdict(item))
                    clone.sub_query_id = i  # type: ignore[attr-defined]
                    merged[key] = clone

        # 流程主干补全：步骤类问题（"哪些步骤/Ablauf/Schritte"）靠关键词匹配不到
        # 具体操作文本（4.9.1.1 Anlegen 等主干章节 BM25 仅 21-23 分被挤出），
        # 对命中≥2 的流程章节族主动注入其 1-2 级子章节首段（生命周期主干）
        merged = self._complete_procedure_spine(merged, request_id)

        merged_items = list(merged.values())
        # 子问题优先：路0（原始问题）跨语言向量易召回噪声（如中问命中中文VAT文档），
        # 只允许它补齐 merge_limit 剩余槽位，不挤占子问题命中
        def _merge_key(x):
            is_orig = bool(original_query) and getattr(x, "sub_query_id", -1) == 0
            return (1 if is_orig else 0, -x.fused_score)

        merged_items.sort(key=_merge_key)
        if len(merged_items) > merge_limit:
            logger.info(f"[{req_tag}] 多路检索合并截断 | in={len(merged_items)} → keep={merge_limit} | 子问题优先")
        merged_items = merged_items[:merge_limit]

        elapsed = round(time.time() - start, 3)
        logger.info(
            f"[{req_tag}] 需求拆解多路检索完成 | subs={len(sub_queries)} | "
            f"merged={len(merged_items)} | per_sub={sub_summary} | elapsed={elapsed}s"
        )
        return merged_items

    # ----------------------------------------------------------
    # 上下文缝合（overlay）：命中后把同文档同章节的相邻段落并入原段落返回
    # ----------------------------------------------------------
    async def expand_with_context(
        self,
        items: list[RetrievalItem],
        span: int = None,
        request_id: str = "",
        min_tokens: int = None,
    ) -> list[RetrievalItem]:
        """
        对检索命中片段做"原段落 overlay 缝合"：
        - 按 (doc_uuid, chapter_id) 分组，命中段落所在的同一章节内，向前后各取 span 个相邻段落，
          拼接成一个更完整的"原段落上下文"存入 item.overlay；
        - 不改变原始 item 列表顺序与分数（overlay 只用于下游生成/校验，不做重排依据）；
        - 段落 token 已 ≥ 阈值（单段即完整原段落）时不做缝合，直接用 content_raw。
        :return: 就地增强后的 items（每个 item 带 overlay / overlay_token_len）
        """
        req_tag = request_id or "-"
        if not items:
            return items
        if not settings.CONTEXT_OVERLAY_ENABLE:
            return items
        # 确保快照就绪（拼接依赖同章节相邻段落的 content_raw）
        if not self._bm25_rows:
            await self._ensure_kb()
        if not self._bm25_rows:
            logger.warning(f"[{req_tag}] 上下文缝合跳过：知识库快照为空")
            return items

        span = settings.CONTEXT_OVERLAY_SPAN if span is None else span
        min_tokens = settings.CONTEXT_OVERLAY_MIN_TOKENS if min_tokens is None else min_tokens
        t0 = time.time()
        stitched = 0

        # 按章节建立段落索引（doc_uuid, chapter_id) -> [(paragraph_id, content_raw), ...] 保序
        chapter_paras: dict[tuple, list] = {}
        for r in self._bm25_rows:
            if not r.content_raw:
                continue
            key = (r.doc_uuid, r.chapter_id)
            chapter_paras.setdefault(key, []).append((r.paragraph_id, r.content_raw))
        for paras in chapter_paras.values():
            paras.sort(key=lambda x: x[0])

        for it in items:
            if not it.content_raw:
                continue
            cur_tokens = it.token_len or estimate_tokens(it.content_raw)
            if cur_tokens >= min_tokens and not it.overlay:
                # 单段已足够完整：overlay 空串，直接用原段
                it.overlay_token_len = cur_tokens
                continue
            paras = chapter_paras.get((it.doc_uuid, it.chapter_id))
            if not paras:
                continue
            ids = [p[0] for p in paras]
            try:
                pos = ids.index(it.paragraph_id)
            except ValueError:
                continue
            lo = max(0, pos - span)
            hi = min(len(paras), pos + span + 1)
            neighbors = [p for p in paras[lo:hi]]
            # 相邻段落拼接（相邻段落之间用换行分隔，保留序号提醒）
            parts = []
            for pid, text in neighbors:
                if pid == it.paragraph_id:
                    parts.append(text)
                else:
                    parts.append(f"[相邻段落]\n{text}")
            overlay_text = "\n\n".join(parts).strip()
            it.overlay = overlay_text
            it.overlay_token_len = estimate_tokens(overlay_text)
            stitched += 1

        elapsed = round(time.time() - t0, 3)
        logger.info(
            f"[{req_tag}] 上下文缝合完成 | items={len(items)} | stitched={stitched} | "
            f"span={span} | min_tokens={min_tokens} | elapsed={elapsed}s"
        )
        return items

    # ----------------------------------------------------------
    # 四层兜底
    # ----------------------------------------------------------
    async def retrieve_with_fallback(
        self,
        query: str,
        entities: dict,
        hyde_doc: Optional[str],
        request_id: str = "",
    ) -> dict:
        """
        四层无答案兜底链路（PRD 核心防幻觉）：
          L1 精准定向检索（原始问题 + HyDE 增强）
          L2 宽泛检索（放宽阈值、扩大召回、去除章节限制）
          L3 追问重检索（LLM 补全缺失参数后重写检索问句）
          L4 全网权威官网检索（仅返回官方链接，不做解读）
        :return: {"level": 1~4, "items": [...], "follow_question": str, "summary": {...}}
        """
        req_tag = request_id or "-"
        summary = {}
        # 国家分区：由意图识别的 entities.country 决定（空=全库）
        country = norm_country((entities or {}).get("country", ""))

        # ---------------- L1：精准检索 + HyDE ----------------
        t1 = time.time()
        eb_payload = query
        if hyde_doc:
            eb_payload = f"{query}\n\n候选规则线索：{hyde_doc}"
        l1_items = await self.search(query=eb_payload, request_id=req_tag, country=country)
        l1_elapsed = round(time.time() - t1, 3)
        l1_hit = len(l1_items) > 0
        logger.info(f"[{req_tag}] 兜底一级(精准+HyDE) 完成 | hit={l1_hit} | count={len(l1_items)} | elapsed={l1_elapsed}s")
        summary["L1"] = {"hit": l1_hit, "count": len(l1_items), "elapsed": l1_elapsed}
        if l1_hit:
            return {"level": 1, "items": l1_items, "follow_question": "", "summary": summary}

        # ---------------- L2：宽泛检索 ----------------
        t2 = time.time()
        l2_threshold = settings.SIMILARITY_THRESHOLD * settings.LOOSE_THRESHOLD_RATIO
        l2_top_k = settings.RETRIEVE_TOP_K * settings.EXPANDED_TOP_K_MULTIPLIER
        l2_items = await self.search(query=query, top_k=l2_top_k, threshold=l2_threshold, request_id=req_tag, country=country)
        l2_elapsed = round(time.time() - t2, 3)
        l2_hit = len(l2_items) > 0
        logger.info(
            f"[{req_tag}] 兜底二级(宽泛检索) 完成 | hit={l2_hit} | count={len(l2_items)} | "
            f"threshold={l2_threshold} | elapsed={l2_elapsed}s"
        )
        summary["L2"] = {"hit": l2_hit, "count": len(l2_items), "elapsed": l2_elapsed}
        if l2_hit:
            return {"level": 2, "items": l2_items, "follow_question": "", "summary": summary}

        # ---------------- L3：追问补参重检索 ----------------
        follow_question = ""
        if settings.FOLLOW_UP_ENABLE:
            follow_question = await self._build_followup(query, entities, request_id)
        t3 = time.time()
        rewrite = query
        if follow_question:
            # 用追问中补全的实体信息改写检索问句
            rewrite = f"{query} {follow_question}"
        l3_items = await self.search(
            query=rewrite,
            top_k=l2_top_k,
            threshold=l2_threshold,
            request_id=req_tag,
            country=country,
        )
        l3_elapsed = round(time.time() - t3, 3)
        l3_hit = len(l3_items) > 0
        logger.info(
            f"[{req_tag}] 兜底三级(追问重检索) 完成 | hit={l3_hit} | count={len(l3_items)} | "
            f"follow={bool(follow_question)} | elapsed={l3_elapsed}s"
        )
        summary["L3"] = {"hit": l3_hit, "count": len(l3_items), "elapsed": l3_elapsed}
        if l3_hit:
            return {"level": 3, "items": l3_items, "follow_question": follow_question, "summary": summary}

        # ---------------- L4：全网权威官网检索 ----------------
        l4_items = await self._official_sources(entities, request_id)
        l4_hit = len(l4_items) > 0
        logger.info(f"[{req_tag}] 兜底四级(全网官网检索) 完成 | hit={l4_hit} | count={len(l4_items)}")
        summary["L4"] = {"hit": l4_hit, "count": len(l4_items)}
        return {"level": 4, "items": l4_items, "follow_question": follow_question, "summary": summary}

    async def _build_followup(self, query: str, entities: dict, request_id: str) -> str:
        """三级兜底：LLM 生成补充追问（指出缺失关键业务参数）"""
        from core.llm_client import llm_client

        prompt = (
            "你是跨境清关顾问。下列用户问题缺失了关键业务参数（商品名称、HS编码、贸易条款、合规场景等之一）。\n"
            "请生成一句不超过30字的追问，向用户索要最关键的缺失参数，用于后续检索。直接输出问句，不要其他内容。\n\n"
            f"用户问题：{query}\n已抽取实体：{json.dumps(entities, ensure_ascii=False)}"
        )
        try:
            import asyncio as _aio

            ret = await _aio.to_thread(
                llm_client.chat,
                [{"role": "system", "content": "你是跨境清关顾问，负责生成追问。"}, {"role": "user", "content": prompt}],
                0.2,
                64,
                no_think=True,
            )
            ret = (ret or "").strip().strip('"：“”')
            logger.info(f"[{request_id}] 三级追问问句生成 | question={ret}")
            return ret
        except Exception as e:
            logger.error(f"[{request_id}] 追问生成失败 | error={e}", exc_info=True)
            return ""

    async def _official_sources(self, entities: dict, request_id: str) -> list[RetrievalItem]:
        """四级兜底：按国家返回官方源链接（注册表命中则给链接，未注册国家不硬塞他国源）+ 知识库文档来源"""
        from config.constants import OFFICIAL_SOURCES_BY_COUNTRY

        country = norm_country((entities or {}).get("country", ""))
        sources = OFFICIAL_SOURCES_BY_COUNTRY.get(country or "德国", {})
        items: list[RetrievalItem] = []
        for key, src in sources.items():
            items.append(
                RetrievalItem(
                    doc_title=src["name"],
                    source_url=src["url"],
                    category=src["category"],
                    content_raw=f"权威官方来源：{src['name']}（{src['category']}），官方链接 {src['url']}",
                    source="official",
                    fused_score=0.1,
                )
            )
        # 附带知识库文档来源链接（有国家分区时只带本国+未标注文档；未注册国家仅带未标注文档）
        if self._bm25_rows:
            seen = {i.doc_uuid for i in items}
            for r in self._bm25_rows:
                if not (r.doc_uuid and r.doc_uuid not in seen and r.source_url):
                    continue
                if country and r.country and r.country != country:
                    continue
                seen.add(r.doc_uuid)
                items.append(
                    RetrievalItem(
                        doc_uuid=r.doc_uuid,
                        doc_title=r.doc_title,
                        source_url=r.source_url,
                        category=r.category,
                        content_raw=f"内部知识库文档：{r.doc_title}，来源 {r.source_url}",
                        source="official",
                        fused_score=0.1,
                    )
                )
        logger.info(f"[{request_id}] 全网官网来源整理 | country={country or '-'} | count={len(items)}")
        return items

    def invalidate_snapshot(self):
        """失效知识库快照缓存（新文档入库后调用，使其立即可检索）"""
        self._kb_loaded_at = None
        logger.info("知识库快照缓存已失效，下次检索将重新加载")