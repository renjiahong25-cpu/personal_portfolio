# -*- coding: utf-8 -*-
"""定位 4.9.1.1/4.9.1.4 段落 BM25 实际得分"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever, tokenize

Q2 = "Welche Schritte sind in ATLAS für die elektronische Zollanmeldung in Deutschland zu befolgen, von der Datenerfassung über die Übermittlung bis zur Bestätigung durch die Zollverwaltung?"
Q4 = "Welche Freigabe, Zahlungspflichten, Nachkontrollen und nachträglichen Pflichten ergeben sich nach der ATLAS-Zollanmeldung in Deutschland?"

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    toks = tokenize(Q2)
    print("sub2 tokens:", toks, flush=True)
    scores = await asyncio.to_thread(r._bm25.get_scores, toks)
    top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:25]
    print("\nsub2 段落级 BM25 top25:", flush=True)
    for i in top:
        it = r._bm25_rows[i]
        mark = " <== 4.9.1系" if "/4.9.1" in it.chapter_path else (" <== 4.9系" if "/4.9" in it.chapter_path else "")
        print("  %.2f | ch=%s %s%s" % (scores[i], it.chapter_id, it.chapter_path[:55], mark), flush=True)
    # 4.9.1.1 / 4.9.1.4 具体段落得分
    print("\n4.9.1.1/4.9.1.4 段落得分（sub2 query）:", flush=True)
    cands = [(i, r._bm25_rows[i]) for i in range(len(r._bm25_rows)) if "/4.9.1" in r._bm25_rows[i].chapter_path]
    cands.sort(key=lambda x: scores[x[0]], reverse=True)
    for i, it in cands[:12]:
        print("  %.2f | ch=%s %s | %s" % (scores[i], it.chapter_id, it.chapter_path[:45], (it.content_raw or "")[:60].replace("\n", " ")), flush=True)
    print("\nsub2 query 下 4.9.1 系最高分段落排名:", flush=True)
    for rank, i in enumerate(top, 1):
        if "/4.9.1" in r._bm25_rows[i].chapter_path:
            print(f"  rank={rank} {r._bm25_rows[i].chapter_path[:60]}")
            break
    else:
        print("  不在 top25 内")

asyncio.run(main())