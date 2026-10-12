# -*- coding: utf-8 -*-
"""验证 pipeline 全链路：翻译query → r.search → 最终返回"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever

QUERIES = [
    "Welche Schritte sind für die elektronische Zollanmeldung über ATLAS in Deutschland erforderlich?",
    "elektronisches Zollanmeldungsverfahren ATLAS Deutschland Schritte",
]

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    for q in QUERIES:
        print("\n=== search: %s ===" % q[:60], flush=True)
        items = await r.search(q, top_k=10, request_id="verify")
        for i, it in enumerate(items):
            print("[%d] fused=%.3f | ch=%s %s%s" % (i, it.fused_score, it.chapter_id,
                  it.chapter_title[:55], " <== 4.9系" if "/4.9" in it.chapter_path else ""), flush=True)

asyncio.run(main())