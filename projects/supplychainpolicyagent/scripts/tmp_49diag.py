# -*- coding: utf-8 -*-
"""根因定位：4.9 章节为何检索不到。分别诊断章节级粗筛与段落级 BM25"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever, tokenize

QUERIES = [
    "Welche Schritte sind für die elektronische Zollanmeldung über ATLAS in Deutschland erforderlich?",
    "elektronisches Zollanmeldungsverfahren ATLAS Deutschland Schritte",
    "Ausfuhrverfahren Zollanmeldung ATLAS Ablauf Schritte",
    "Wie läuft die elektronische Zollanmeldung über ATLAS ab? Schritte im Verfahren",
]

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    print("章节总数:", len(r._chapter_rows), flush=True)
    # 找到 4.9 系章节
    c49 = [c for c in r._chapter_rows if "/4.9" in c[2]]
    print("4.9 系章节数:", len(c49), flush=True)
    for q in QUERIES:
        toks = tokenize(q)
        cb_scores = await asyncio.to_thread(r._chapter_bm25.get_scores, toks)
        top = sorted(range(len(r._chapter_rows)), key=lambda i: cb_scores[i], reverse=True)[:10]
        c49_hit = [i for i in range(len(r._chapter_rows)) if "/4.9" in r._chapter_rows[i][2] and cb_scores[i] > 0]
        print("\n=== query=%s ===" % q[:50], flush=True)
        print("  tokenize:", toks[:18], flush=True)
        print("  4.9 系有正分章节:", len(c49_hit), "| 最高4.9分=", 
              max((cb_scores[i] for i in c49_hit), default=0), flush=True)
        print("  top10 章节:", flush=True)
        for i in top[:10]:
            print("    %.2f %-10s %s%s" % (cb_scores[i], r._chapter_rows[i][0], r._chapter_rows[i][1][:55],
                  " <== 4.9系" if "/4.9" in r._chapter_rows[i][2] else ""), flush=True)

        # 段落级 BM25 直接打分（不经过章节过滤）
        p_scores = await asyncio.to_thread(r._bm25.get_scores, toks)
        top_p = sorted(range(len(p_scores)), key=lambda i: p_scores[i], reverse=True)[:8]
        print("  段落级 top8:", flush=True)
        for i in top_p[:8]:
            if p_scores[i] <= 0:
                continue
            it = r._bm25_rows[i]
            print("    %.2f | ch=%s %s%s" % (p_scores[i], it.chapter_id, it.chapter_title[:48],
                  " <== 4.9系" if "/4.9" in it.chapter_path else ""), flush=True)

asyncio.run(main())