# -*- coding: utf-8 -*-
"""对比：rerank 开/关 对 4.9 系召回的影响（德语查询）"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config.settings as settings
from service.chat_service.retriever import HybridRetriever

Q = "Welche Schritte sind für die elektronische Zollanmeldung über ATLAS in Deutschland erforderlich?"

async def run_with(enable: bool, tag: str):
    settings.RERANK_ENABLE = enable
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    items = await r.search(Q, top_k=8, request_id=tag)
    print("\n=== %s (rerank=%s) ===" % (tag, enable), flush=True)
    for i, it in enumerate(items):
        mark = " <== 4.9系" if "/4.9" in it.chapter_path else ""
        print("[%d] fused=%.3f | src=%s | ch=%s %s%s" % (i, it.fused_score, it.source,
              it.chapter_id, it.chapter_title[:52], mark), flush=True)

async def main():
    await run_with(True, "A")
    await run_with(False, "B")

asyncio.run(main())