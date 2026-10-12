# -*- coding: utf-8 -*-
"""验证：embedding cosine 对 fused 池全量重排，能否让 4.6/4.9 系进入 top5"""
import sys
from pathlib import Path
import asyncio
import os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
from sentence_transformers import SentenceTransformer
from service.chat_service.retriever import HybridRetriever

Q = "Welche Schritte sind für die elektronische Zollanmeldung über ATLAS in Deutschland erforderlich?"

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    bm25_items = await r._bm25_search(Q, 16, None)
    vec_items = await r._vector_search(Q, 12, None)
    fused = r._fuse(bm25_items, vec_items, 0.65)
    print("fused 池:", len(fused), flush=True)

    model = SentenceTransformer(r._embed_model_name if hasattr(r, "_embed_model_name") else "paraphrase-multilingual-MiniLM-L12-v2")
    qv = model.encode([Q], normalize_embeddings=True)[0]
    docs = [(it.content_raw or it.content_summary or "")[:500] for it in fused]
    dvs = model.encode(docs, normalize_embeddings=True)
    sims = dvs @ qv
    order = sorted(range(len(fused)), key=lambda i: sims[i], reverse=True)
    print("\n--- embedding-cosine 重排 (top10) ---", flush=True)
    for j, i in enumerate(order[:10]):
        it = fused[i]
        print("[%d] cos=%.3f (fused=%.3f bm25=%.2f vec=%.3f) | ch=%s %s" % (
            j, sims[i], it.fused_score, it.bm25_score, it.vector_score, it.chapter_id, it.chapter_title[:52]), flush=True)

model = None
asyncio.run(main())