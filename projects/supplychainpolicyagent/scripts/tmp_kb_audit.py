# -*- coding: utf-8 -*-
"""知识库存在性审计：翻译查询 → 检索 → 输出 Top 段全文，判断库内是否有答案"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.query_translator import QueryTranslator
from service.chat_service.retriever import HybridRetriever

Q = "德国ATLAS电子报关流程需要哪些步骤？"
ENT = {"country": "德国", "product": "", "hs_code": "", "trade_term": "", "category": ""}

async def main():
    tr = QueryTranslator()
    translated = await tr.translate(Q, ENT, "audit")
    print("=== 1. 查询翻译 ===")
    print("translated:", repr(translated))

    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()
    query = translated or Q
    items = await r.search(query, top_k=10, request_id="audit")
    print("\n=== 2. 检索命中 %d 段 (query=%s) ===" % (len(items), query[:60]))
    for i, it in enumerate(items):
        print("\n----- [%d] fused=%.4f | bm25=%.4f | vec=%.4f | paragraph_id=%d | chapter_id=%s | token=%d -----" % (
            i, it.fused_score, it.bm25_score, it.vector_score, it.paragraph_id, it.chapter_id, it.token_len))
        print("章节:", it.chapter_title)
        print("--- content 前 500 ---")
        print((it.content_raw or it.content_summary or "")[:500])

asyncio.run(main())