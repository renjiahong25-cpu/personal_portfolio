# -*- coding: utf-8 -*-
"""深度诊断：fused池(全量) vs rerank后；强制章节=4.9系验证召回能力"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.models.base import SessionLocal, DocChapter
from service.chat_service.retriever import HybridRetriever

Q = "Welche Schritte sind für die elektronische Zollanmeldung über ATLAS in Deutschland erforderlich?"
DOC_UUID = "085898499f1e44feb11015599da4c511"

s = SessionLocal()
chs49 = s.query(DocChapter).filter(DocChapter.doc_uuid == DOC_UUID, DocChapter.chapter_path.like("4.9%")).all()
print("4.9 系章节 id:", [(c.id, c.chapter_path) for c in chs49], flush=True)
ch4 = s.query(DocChapter).filter(DocChapter.doc_uuid == DOC_UUID, DocChapter.chapter_path.like("4.6%")).all()
print("4.6 系章节 id:", [(c.id, c.chapter_path) for c in ch4], flush=True)
s.close()

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    print("\n=== A. 检索(query红fused池全量) ===", flush=True)
    cb_toks = __import__("service.chat_service.retriever", fromlist=["tokenize"]).tokenize(Q)
    print("tokenize:", cb_toks[:20], flush=True)
    coarse = None
    cb_scores = await asyncio.to_thread(r._chapter_bm25.get_scores, cb_toks)
    top_idx = sorted(range(len(r._chapter_rows)), key=lambda i: cb_scores[i], reverse=True)[:12]
    positive = {r._chapter_rows[i][0] for i in top_idx if cb_scores[i] > 0}
    forced = set()
    core = [s_ for s_ in ("ausfuhr", "anmeldung", "einfuhr", "verfahren", "zoll", "abfertigung") if s_ in cb_toks]
    if core:
        for cid, ct, cp in r._chapter_rows:
            if any(s_ in (ct + " " + cp).lower() for s_ in core):
                forced.add(cid)
    coarse = (positive | forced) or None
    print("章节粗筛 positive=%d forced=%d → %s" % (len(positive), len(forced), sorted(coarse) if coarse else None), flush=True)

    bm25_items = await r._bm25_search(Q, 16, coarse)
    vec_items = await r._vector_search(Q, 12, coarse)
    print("BM25 命中 %d | 向量命中 %d" % (len(bm25_items), len(vec_items)), flush=True)
    fused = r._fuse(bm25_items, vec_items, 0.65)
    print("\n--- fused 池 (rerank前, 按 fused_score) ---", flush=True)
    for i, it in enumerate(fused):
        mark = " <== 4.9系" if it.chapter_id in {c.id for c in chs49} else ""
        print("[%d] fused=%.3f bm25=%.2f vec=%.3f | ch=%s %s%s" % (
            i, it.fused_score, it.bm25_score, it.vector_score, it.chapter_id, it.chapter_title[:50], mark), flush=True)

    print("\n--- rerank 后 (pipeline 实际返回) ---", flush=True)
    final = await r._rerank(Q, fused, 8)
    for i, it in enumerate(final):
        mark = " <== 4.9系" if it.chapter_id in {c.id for c in chs49} else ""
        print("[%d] rerank=%.3f | ch=%s %s%s" % (i, it.fused_score, it.chapter_id, it.chapter_title[:50], mark), flush=True)

asyncio.run(main())