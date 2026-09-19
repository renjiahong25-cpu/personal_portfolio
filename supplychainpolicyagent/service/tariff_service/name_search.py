# -*- coding: utf-8 -*-
"""商品名模糊搜索：泛化词 → HS 编码候选（A→B 速查 / 报价候选确认用）。

三级策略：
  1. 精确：q 为 6~10 位纯数字 → 官方税则存在性校验后单候选返回
  2. 快路径：关键词倒排索引（world_mfn 5 国 39,703 条税则描述 + US 真实行）
     中文泛化词经中英同义词典(data/tariff/name_synonyms.json)展开为英文关键词后命中
  3. LLM 兜底：快路径 0 命中/低置信 → Qwen 产出 ≤5 个 HS 候选，
     每个候选必须在官方税则(world_mfn 5 国 / US 真实行)中真实存在才保留（防编造），
     品名以官方税则描述为准。

候选按 HS6 归并（各国税则 HS6 编码同构，同一商品跨国聚合）；
dest 国税率复用 world_tariff.import_duty（world 5 国）/ DutyRate 基础 MFN（其余）。
"""
import json
import math
import re
import threading
import time

from config import settings
from config.logging_config import get_logger
from service.tariff_service import world_tariff
from service.tariff_service.hs_classifier import (
    _LATIN_RE, _CJK_RE, _STOP, _stem_plural,
)

logger = get_logger("name_search")


def _tok(text: str) -> list:
    """索引/查询分词：Latin 词 + 中文二字 bigram（不用单字——单字太宽松，
    '面包'会因'面/包'误中'绿茶(小包装)/鞋面'等无关行）。"""
    toks = []
    t = (text or "").lower()
    for m in _LATIN_RE.finditer(t):
        w = m.group()
        if w not in _STOP and len(w) > 1:
            toks.append(_stem_plural(w))
    for seg in _CJK_RE.findall(t):
        for i in range(len(seg) - 1):
            toks.append(seg[i:i + 2])
    return toks

_SYNONYMS_FILE = settings.TARIFF_DATA_DIR / "name_synonyms.json"
_TTL_SEC = 600
_LOW_CONF = 0.5
_META = "__meta__"

try:
    from core.llm_client import llm_client
except Exception:  # pragma: no cover
    llm_client = None

try:
    from db.models.base import SessionLocal, HsCode
except Exception:  # pragma: no cover
    SessionLocal = HsCode = None

_LLM_SYS = (
    "你是 HS（商品名称及编码协调制度）商品分类专家。给定商品名（可能是泛化词/俗称），"
    "给出最可能的 HS 编码候选。严格要求：\n"
    "- 只输出纯 JSON，不要任何解释或代码块围栏，格式：\n"
    '  {"candidates": [{"hs": "6位或8位编码", "name_cn": "中文品名", "name_en": "英文品名"}]}\n'
    "- 不超过 5 个候选，按可能性从高到低\n"
    "- 编码必须是真实存在的 HS2022 编码，禁止编造"
)


def _load_synonyms() -> dict:
    try:
        return json.loads(_SYNONYMS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"同义词典加载失败 | {e}")
        return {}


def _query_tokens(q: str):
    """查询分词：Latin 词 + 中文 bigram + 中文段经同义词典展开为英文关键词。
    词典键为 ≥2 字词，双向子串匹配（'鞋' in '鞋类' 命中；'面包' 不命中 '箱包'）。"""
    toks = _tok(q)
    syn = _load_synonyms()
    if not syn:
        return toks, False
    hit = False
    for m in re.finditer(r"[\u4e00-\u9fff]+", q):
        seg = m.group()
        for word, kws in syn.items():
            if (word in seg) or (seg in word):
                toks.extend(kws)
                hit = True
                break
    return toks, hit


class NameIndex:
    """world_mfn 描述 + US 真实行的倒排索引（进程内，TTL/mtime 刷新）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._posts = {}
        self._docs = {}
        self._total = 0
        self._ts = 0.0
        self._mtime = None

    def _ensure(self):
        snap = world_tariff._snapshot()
        mtime = None
        try:
            p = settings.TARIFF_DATA_DIR / "world_mfn.json"
            if p.exists():
                mtime = p.stat().st_mtime
        except Exception:
            pass
        with self._lock:
            stale = (self._total == 0 or time.time() - self._ts > _TTL_SEC
                     or (mtime is not None and mtime != self._mtime))
        if stale:
            self._rebuild(snap, mtime)
        return self

    def _rebuild(self, snap, mtime):
        posts, docs, total = {}, {}, 0

        def add_doc(doc_id, country, code, text):
            nonlocal total
            text = (text or "").strip()
            if not text:
                return
            toks = _tok(text)
            if not toks:
                return
            total += 1
            docs[doc_id] = {"country": country, "code": code, "desc": text, "nt": len(toks)}
            local = {}
            for t in toks:
                local[t] = local.get(t, 0) + 1
            for t, tf in local.items():
                posts.setdefault(t, {})[doc_id] = tf

        for c, rows in snap.items():
            if c == _META:
                continue
            for code, row in (rows or {}).items():
                add_doc(f"{c}:{code}", c, code, row.get("desc") or "")

        if HsCode is not None:
            try:
                with SessionLocal() as db:
                    rows = db.query(HsCode).filter(
                        HsCode.country == "US", HsCode.status == 1,
                        HsCode.description_en.notlike("%SYNTHETIC%")).all()
                for r in rows:
                    add_doc(f"US:{r.hs_code}", "US", r.hs_code,
                            f"{r.description_cn or ''} {r.description_en or ''}")
            except Exception as e:
                logger.warning(f"US HS 行加载失败 | {e}")

        with self._lock:
            self._posts, self._docs, self._total = posts, docs, total
            self._ts, self._mtime = time.time(), mtime
        logger.info(f"商品名索引构建 | docs={total} | tokens={len(posts)}")

    def search(self, tokens, top_k: int = 40) -> list:
        with self._lock:
            posts, docs, total = self._posts, self._docs, self._total
        if not total:
            return []
        weights = {}
        for tok in tokens:
            df = len(posts.get(tok, {}))
            if df:
                weights[tok] = max(0.3, math.log((total + 2) / (df + 1)))
        if not weights:
            return []
        scores = {}
        for tok, w in weights.items():
            for doc_id in posts.get(tok, {}):
                scores[doc_id] = scores.get(doc_id, 0.0) + w
        out = []
        for doc_id, raw in scores.items():
            doc = docs[doc_id]
            out.append({"id": doc_id, "country": doc["country"], "code": doc["code"],
                        "desc": doc["desc"], "score": raw / math.sqrt(doc["nt"])})
        out.sort(key=lambda x: -x["score"])
        return out[:top_k]


_index = NameIndex()


def _aggregate(hits: list, limit: int = 8) -> list:
    """行级命中按 HS6 归并（跨国同品聚合），保留最高分行描述。"""
    groups = {}
    for h in hits:
        key = h["code"][:6]
        g = groups.get(key)
        if g is None:
            groups[key] = {"hs": h["code"], "hs6": key, "name_en": h["desc"],
                           "score": h["score"], "countries": [h["country"]]}
        else:
            if h["score"] > g["score"]:
                g["hs"], g["score"], g["name_en"] = h["code"], h["score"], h["desc"]
            if h["country"] not in g["countries"]:
                g["countries"].append(h["country"])
    out = sorted(groups.values(), key=lambda x: -x["score"])
    for g in out:
        g.pop("countries", None)
    return out[:limit]


def _base_mfn_rate(code: str, dest: str) -> dict:
    """DutyRate 基础 MFN（US/EU/CN 等非 world 快照国），与 /ab 端点同逻辑。"""
    from service.tariff_service.tariff_service import tariff_service
    try:
        duties = tariff_service.duties(code, dest, direction="import").get("duties", [])
    except Exception:
        duties = []
    for t in ("import_mfn", "mfn", "import_general", "general"):
        for x in duties:
            if x.get("duty_type") == t:
                note = "基础 MFN/普通进口税率"
                if dest == "US":
                    note += "（美线另含 301/对等附加，见报价）"
                return {"rate": x.get("rate_value"), "note": note, "source": "DutyRate"}
    if duties:
        x = duties[0]
        return {"rate": x.get("rate_value"), "note": "基础进口税率", "source": "DutyRate"}
    return {"rate": None, "note": "该目的国暂无基础税率数据", "source": ""}


def _rate_for(code: str, dest: str) -> dict:
    if dest in world_tariff.supported_countries():
        r = world_tariff.import_duty(dest, code)
        return {"rate": r.get("rate"), "note": r.get("note") or "", "source": "world_mfn"}
    return _base_mfn_rate(code, dest)


def _official_desc(code: str) -> str:
    """官方税则描述：world_mfn 精确行(10/8/6位) → US 真实行；找不到返回 ''。"""
    snap = world_tariff._snapshot()
    for c, rows in snap.items():
        if c == _META:
            continue
        for L in (10, 8, 6):
            if len(code) >= L:
                sub = code[:L]
                if sub in rows:
                    return rows[sub].get("desc") or ""
                if L == 6:
                    for k, v in rows.items():
                        if k.startswith(sub):
                            return v.get("desc") or ""
    if HsCode is not None:
        try:
            with SessionLocal() as db:
                r = db.query(HsCode).filter(
                    HsCode.country == "US", HsCode.status == 1,
                    HsCode.hs_code == code).first()
                if r is None and len(code) > 6:
                    r = db.query(HsCode).filter(
                        HsCode.country == "US", HsCode.status == 1,
                        HsCode.hs_code.like(code + "%")).first()
                if r:
                    return (r.description_cn or "") + (" | " if r.description_cn and r.description_en else "") + (r.description_en or "")
        except Exception as e:
            logger.warning(f"US 描述查询失败 | {e}")
    return ""


def _candidate_for(code: str, dest: str, name_cn: str = "", name_en_hint: str = ""):
    """存在性校验 + 官方描述 + dest 国税率；任一官方税则无此码 → None（防 LLM 编造）。"""
    code = re.sub(r"\D", "", code or "")
    if len(code) < 6:
        return None
    desc = _official_desc(code)
    if not desc:
        return None
    rate = _rate_for(code, dest)
    return {"hs": code, "name_cn": name_cn, "name_en": desc or name_en_hint,
            "rate": rate["rate"], "rate_note": rate["note"], "source": rate["source"],
            "score": None}


def _llm_candidates(q: str, dest: str, limit: int) -> list:
    if llm_client is None:
        return []
    messages = [
        {"role": "system", "content": _LLM_SYS},
        {"role": "user", "content": f"商品名：{q}\n出口国：CN，目的国：{dest}\n输出 JSON。"},
    ]
    try:
        resp = llm_client.adjudicate(messages, temperature=0.0, max_tokens=2048, timeout=120.0)
    except Exception as e:
        logger.warning(f"模糊搜索 LLM 调用失败 | q={q[:30]} | {e}")
        return []
    text = (resp or "").strip()
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception:
        logger.warning(f"模糊搜索 LLM 输出不可解析 | q={q[:30]} | head={text[:80]}")
        return []
    out = []
    for c in (data.get("candidates") or [])[:limit]:
        cand = _candidate_for(c.get("hs"), dest,
                              name_cn=str(c.get("name_cn") or ""),
                              name_en_hint=str(c.get("name_en") or ""))
        if cand:
            out.append(cand)
    return out


def smart_search(q: str, dest: str = "VN", limit: int = 8) -> dict:
    """商品名模糊搜索入口。

    返回 {query, dest, via: exact|keyword|dict|llm, precise(候选恰为 1), candidates[]}。
    candidates: [{hs, name_cn, name_en, score, rate, rate_note, source}]。
    """
    q = (q or "").strip()
    dest = (dest or "VN").upper()
    limit = max(1, min(int(limit or 8), 20))
    if not q:
        return {"query": q, "dest": dest, "via": "", "precise": False,
                "candidates": [], "note": "查询词为空"}
    code = re.sub(r"\D", "", q)
    if code and len(code) >= 6:
        cand = _candidate_for(code, dest)
        cands = [cand] if cand else []
        return {"query": q, "dest": dest, "via": "exact", "precise": len(cands) == 1,
                "candidates": cands,
                "note": "" if cand else "该编码在官方税则中不存在"}
    toks, used_dict = _query_tokens(q)
    _index._ensure()
    hits = _index.search(toks, top_k=40)
    cands = []
    for g in _aggregate(hits, limit):
        r = _rate_for(g["hs"], dest)
        cands.append({"hs": g["hs"], "name_cn": "", "name_en": g["name_en"],
                      "score": round(g["score"], 4), "rate": r["rate"],
                      "rate_note": r["note"], "source": r["source"]})
    via = "dict" if used_dict else "keyword"
    if (not cands or cands[0]["score"] < _LOW_CONF):
        llm_cands = _llm_candidates(q, dest, limit)
        if llm_cands:
            cands, via = llm_cands, "llm"
        elif not cands:
            cands = []
    return {"query": q, "dest": dest, "via": via, "precise": len(cands) == 1,
            "candidates": cands}


name_search_service = smart_search
