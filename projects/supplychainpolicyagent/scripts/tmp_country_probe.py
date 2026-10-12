# -*- coding: utf-8 -*-
"""验证国家分区：德国问题→只召回德国文档；中国分区→空回退全库"""
import sys
from pathlib import Path
import asyncio
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.chat_service.retriever import HybridRetriever, norm_country

Q = "德国ATLAS电子报关流程需要哪些步骤？"

def countries_of(items):
    return sorted({(i.country or "(空)") for i in items})

async def main():
    r = HybridRetriever()
    await r._ensure_kb()
    await r._ensure_milvus()

    print("norm_country:", norm_country("德国"), norm_country("Germany"), norm_country("DE"), norm_country("中国"), norm_country(""), norm_country("日本"))

    full = await r.search(Q, top_k=12, request_id="cprobe")
    print(f"全库:     n={len(full)} | docs={countries_of(full)}")

    de = await r.search(Q, top_k=12, request_id="cprobe", country="德国")
    print(f"德国分区: n={len(de)} | docs={countries_of(de)}")

    cn = await r.search(Q, top_k=12, request_id="cprobe", country="中国")
    print(f"中国分区: n={len(cn)} | docs={countries_of(cn)}  (空→应回退全库)")

    ms = await r.multi_search(["ATLAS Zollanmeldung Schritte"], request_id="cprobe", original_query=Q, country="德国")
    print(f"multi_search(德国): n={len(ms)} | docs={countries_of(ms)}")

asyncio.run(main())
